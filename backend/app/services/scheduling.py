"""Deterministic, finish-to-start scheduling; no resource optimization implied."""
import hashlib
import json
import math
from datetime import date, timedelta

from backend.app.domain.contracts import CalculationRequest, Stage


def calculate(request: CalculationRequest, stages: list[Stage], sources: set[str]) -> dict:
    known = {s.external_id: s for s in stages}
    works = {w.stage_id: w for w in request.works}
    if len(works) != len(request.works):
        raise ValueError("Этап расчёта повторяется")
    for work in works.values():
        if work.stage_id not in known or known[work.stage_id].kind != "work":
            raise ValueError(f"Не найдена исполняемая работа {work.stage_id}")
        if work.volume_unit != work.output_unit:
            raise ValueError("Единицы объёма и выработки не совпадают")
        if not set(work.source_ids) <= sources:
            raise ValueError("Источник отсутствует в реестре")
        if not set(work.predecessors) <= works.keys():
            raise ValueError("Не найден предшественник в расчёте")
    result = {}
    pending = list(works)
    holidays = set(request.holidays)
    def working(day):
        return day.weekday() in request.weekdays and day not in holidays
    def next_workday(day):
        while not working(day):
            day += timedelta(days=1)
        return day
    while pending:
        ready = [key for key in pending if all(p in result for p in works[key].predecessors)]
        if not ready:
            raise ValueError("Цикл зависимостей этапов")
        for key in ready:
            work = works[key]
            days = math.ceil(work.volume/(work.output_per_shift*work.machines*work.shifts_per_day))
            if days > 36500:
                raise ValueError("Длительность превышает 36 500 рабочих дней; проверьте параметры")
            # Relative offsets are working-day indices. Calendar lags require an anchor.
            if work.lag_days and not request.anchor:
                raise ValueError("Календарная выдержка требует даты привязки")
            offset = max((result[p]["end_offset"]+1 for p in work.predecessors), default=0)
            start = end = None
            if request.anchor:
                base = max((date.fromisoformat(result[p]["end_date"])+timedelta(days=1)
                            for p in work.predecessors), default=request.anchor)
                start = next_workday(base+timedelta(days=work.lag_days))
                end = start
                for _ in range(days-1):
                    end = next_workday(end+timedelta(days=1))
            result[key] = {**work.model_dump(mode="json"), "name": known[key].name,
                           "source_row": known[key].source_row, "duration_days": days,
                           "start_offset": offset, "end_offset": offset+days-1,
                           "start_date": start.isoformat() if start else None,
                           "end_date": end.isoformat() if end else None,
                           "provenance": "demonstration" if request.anchor_source=="demonstration" else work.provenance,
                           "formula": "ceil(volume / (output_per_shift * machines * shifts_per_day))"}
            pending.remove(key)
    payload = {"method_version": "capacity-fs-v1", "parameters": request.model_dump(mode="json"),
               "works": list(result.values()), "calendar_bound": request.anchor is not None,
               "resource_constraint": "Ресурсы разных этапов не оптимизированы; независимость задана пользователем"}
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload
