"""Read-only CSV/XLSX preview. No persistence occurs until explicit confirmation."""
import csv
import hashlib
import io
import re
import zipfile
from datetime import date, datetime

from openpyxl import load_workbook
from pydantic import ValidationError

from backend.app.domain.contracts import ImportOptions, Issue, Preview, Stage

MAX_UPLOAD = 10 * 1024 * 1024
MAX_ROWS = 10_000


def raw(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def identifier(value):
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().rstrip(".")


def check_hierarchy(stages):
    issues = []
    by_id = {}
    for stage in stages:
        if stage.external_id in by_id:
            issues.append(Issue(severity="error", code="duplicate_id", row=stage.source_row,
                                message=f"Повтор номера {stage.external_id}"))
        by_id[stage.external_id] = stage
    done = set()
    for stage in stages:
        chain, current = set(), stage
        while current and current.external_id not in done:
            if current.external_id in chain:
                issues.append(Issue(severity="error", code="cycle", row=stage.source_row,
                                    message="Цикл в иерархии"))
                break
            chain.add(current.external_id)
            if current.parent_id and current.parent_id not in by_id:
                issues.append(Issue(severity="error", code="missing_parent", row=current.source_row,
                                    message=f"Не найден родитель {current.parent_id}"))
                break
            current = by_id.get(current.parent_id)
        done.update(chain)
    return issues


def preview_import(content: bytes, filename: str, options: ImportOptions) -> Preview:
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("Файл пуст или превышает 10 МБ")
    digest = hashlib.sha256(content).hexdigest()
    if filename.lower().endswith(".xlsx"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 50*1024*1024:
                raise ValueError("Распакованный XLSX превышает 50 МБ")
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        try:
            sheets = book.sheetnames
            sheet = options.sheet or sheets[0]
            if sheet not in sheets:
                raise ValueError("Лист не найден")
            ws = book[sheet]
            if ws.max_row > MAX_ROWS or ws.max_column > 100:
                raise ValueError("Лимит: 10 000 строк, 100 столбцов")
            rows = list(ws.values)
        finally:
            book.close()
    elif filename.lower().endswith(".csv"):
        text = content.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        sheets, sheet = ["CSV"], "CSV"
        if len(rows) > MAX_ROWS or any(len(row)>100 for row in rows):
            raise ValueError("Лимит: 10 000 строк, 100 столбцов")
    else:
        raise ValueError("Поддерживаются только CSV UTF-8 и XLSX")
    if options.header_row >= len(rows):
        raise ValueError("После строки заголовков нет данных")
    headers = rows[options.header_row-1]
    def cell(row, col):
        return row[col-1] if col and col <= len(row) else None
    selected = [options.id_column, options.name_column, options.parent_column,
                options.start_column, options.end_column]
    if any(c and c > len(headers) for c in selected):
        raise ValueError("Выбранный столбец за пределами таблицы")
    if options.id_column == options.name_column:
        raise ValueError("Номер и название должны быть в разных столбцах")
    stages, issues = [], []
    last_numbered = None
    for row_num, row in enumerate(rows[options.header_row:], options.header_row+1):
        name = cell(row, options.name_column)
        if name is None or not str(name).strip():
            if any(v not in (None, "") for v in row):
                issues.append(Issue(severity="error", code="missing_name", row=row_num,
                                    message="В заполненной строке отсутствует название"))
            continue
        number = cell(row, options.id_column)
        status = "explicit"
        if isinstance(number, (date, datetime)):
            candidate = f"{number.day}.{number.month}"
            issues.append(Issue(severity="warning" if options.recover_date_numbers else "error",
                                code="date_in_number", row=row_num,
                                message=f"Номер раздела {candidate}: Excel хранит ячейку в формате даты. Нормализуем номер; календарных сроков эта ячейка не задаёт."))
            number_id = candidate if options.recover_date_numbers else f"unresolved:{row_num}"
            status = "proposed"
        else:
            number_id = identifier(number)
        parent = identifier(cell(row, options.parent_column))
        if not number_id:
            number_id = f"row:{digest[:16]}:{sheet}:{row_num}"
            if not options.parent_column:
                parent = last_numbered
                status = "proposed"
                issues.append(Issue(severity="warning", code="inferred_parent", row=row_num,
                                    message=f"Строка без номера; предлагаемый родитель {parent or 'не задан'}. Нужна проверка."))
        else:
            if not options.parent_column and re.fullmatch(r"\d+(\.\d+)+", number_id):
                parent = number_id.rsplit(".", 1)[0]
            last_numbered = number_id
        def parse_date(col):
            value = cell(row, col)
            if value in (None, ""):
                return None
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value).strip())
        try:
            if any(isinstance(value, str) and value.startswith("=") for value in row):
                raise ValueError("Формулы не принимаются: загрузите значения")
            stages.append(Stage(external_id=number_id, parent_id=parent, source_row=row_num,
                                name=str(name).strip(), sequence_index=len(stages), raw_number=str(raw(number)) if number is not None else None,
                                raw_values=[raw(v) for v in row], hierarchy_status=status,
                                object_types=[str(cell(headers, c)) for c in options.object_columns
                                              if cell(headers,c) and str(cell(row,c)).strip() in ("˅", "✓", "1", "+", "Да", "да")],
                                start_date=parse_date(options.start_column), end_date=parse_date(options.end_column)))
        except (ValueError, ValidationError) as exc:
            issues.append(Issue(severity="error", code="invalid_row", row=row_num, message=str(exc)))
    issues.extend(check_hierarchy(stages))
    parents = {s.parent_id for s in stages if s.parent_id}
    for stage in stages:
        stage.kind = "summary" if stage.external_id in parents else "work"
    if not stages:
        issues.append(Issue(severity="error", code="empty", message="Не найдены работы"))
    return Preview(file_sha256=digest, sheet=sheet, sheets=sheets, stages=stages, issues=issues)
