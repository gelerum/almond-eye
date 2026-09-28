"""Explicit, versioned calendar supplied by the user, independent of estimates."""
import csv
import hashlib
import io
import json
import re
from datetime import date

from pydantic import Field, model_validator

from backend.app.domain.contracts import Contract
from backend.app.services.import_schedule import MAX_UPLOAD

COLUMNS = ['stage_id', 'work_name', 'zone_id', 'start_date', 'end_date', 'rule_profile']
PROFILES = {
    'none': None,
    'excavation': {'version': 'excavation-import-v1', 'required': [['excavator'], ['dump_truck']],
                   'allowed': [], 'confidence': 0.5},
}


class TimelineOptions(Contract):
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    holidays: list[date] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def calendar(self):
        if any(d not in range(7) for d in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError('Рабочие дни: уникальные числа 0–6')
        return self


def template(stages, zone_id):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(COLUMNS)
    for stage in stages:
        if stage['kind'] == 'work':
            # Names are display-only; avoid formula execution when opening in Excel.
            name = stage['name']
            if name.lstrip().startswith(('=', '+', '-', '@')):
                name = "'" + name
            writer.writerow([stage['external_id'], name, zone_id, '', '', 'none'])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def preview_timeline(content, filename, imported, zones, options, timezone):
    if not filename.lower().endswith('.csv') or not content or len(content) > MAX_UPLOAD:
        raise ValueError('Загрузите CSV UTF-8 до 10 МБ по шаблону сроков')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('Сохраните CSV в кодировке UTF-8') from exc
    if not text.strip():
        raise ValueError('CSV не содержит заголовка и сроков')
    delimiter = ';' if ';' in text.splitlines()[0] else ','
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
    if reader.fieldnames != COLUMNS:
        raise ValueError('Неверный заголовок. Используйте столбцы шаблона: ' + ', '.join(COLUMNS))
    known = {s['external_id']: s for s in imported['stages'] if s['kind'] == 'work'}
    issues, works, seen = [], [], set()
    holidays = set(options.holidays)
    for row in reader:
        number = reader.line_num
        if number > 1001:
            raise ValueError('В графике допускается до 1000 строк')
        if not any(row.values()):
            continue
        try:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('Число столбцов не соответствует шаблону')
            row = {k: v.strip() for k, v in row.items()}
            sid, zid = row['stage_id'], row['zone_id']
            if sid not in known:
                raise ValueError('stage_id не найден среди работ выбранной версии перечня')
            if zid not in zones:
                raise ValueError('zone_id не найден на этой площадке')
            if (sid, zid) in seen:
                raise ValueError('Повтор пары работа + зона; для каждой пары нужна одна строка')
            seen.add((sid, zid))
            if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', row[k]) for k in ('start_date', 'end_date')):
                raise ValueError('Обе даты обязательны в формате ГГГГ-ММ-ДД')
            start, end = date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])
            if end < start or (end-start).days > 36500:
                raise ValueError('Окончание должно быть не раньше начала; интервал не более 36 500 дней')
            profile = row['rule_profile']
            if profile not in PROFILES:
                raise ValueError('rule_profile: none либо excavation (экскаватор и самосвал)')
            weeks, tail = divmod((end-start).days+1, 7)
            duration = weeks * len(options.weekdays) + sum(
                (start.weekday()+n) % 7 in options.weekdays for n in range(tail))
            duration -= sum(start <= day <= end and day.weekday() in options.weekdays for day in holidays)
            if not duration:
                raise ValueError('Интервал не содержит рабочих дней выбранного календаря')
            stage = known[sid]
            works.append({'stage_id': sid, 'zone_id': zid, 'name': stage['name'],
                          'source_row': stage['source_row'], 'timeline_row': number,
                          'start_date': start.isoformat(), 'end_date': end.isoformat(),
                          'duration_days': duration, 'provenance': 'uploaded', 'source_ids': [],
                          'rule': PROFILES[profile], 'rule_profile': profile,
                          'raw_values': row})
        except ValueError as exc:
            issues.append({'severity': 'error', 'row': number, 'message': str(exc)})
    if not works:
        issues.append({'severity': 'error', 'row': None, 'message': 'Нет корректных строк сроков'})
    omitted = len(set(known) - {w['stage_id'] for w in works})
    if omitted:
        issues.append({'severity': 'warning', 'row': None,
                       'message': f'Без сроков осталось работ: {omitted}. Они не участвуют в этой версии графика.'})
    if any(w['rule'] is None for w in works):
        issues.append({'severity': 'warning', 'row': None,
                       'message': 'Для работ с none доступен календарный контекст, проверка состава техники не настроена.'})
    payload = {'method_version': 'uploaded-calendar-v1', 'import_id': imported['id'],
               'file_sha256': hashlib.sha256(content).hexdigest(), 'timezone': timezone,
               'parameters': options.model_dump(mode='json'), 'calendar_bound': True,
               'works': works, 'issues': issues,
               'resource_constraint': 'Сроки загружены пользователем. Конечная дата включительна. Версия самостоятельная, без слияния с предыдущей.'}
    payload['fingerprint'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload
