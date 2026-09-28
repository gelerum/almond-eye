"""Plan vs fact on a date: per-stage status, alerts with photo evidence and an overall verdict.

Pure functions. Rules (docs/PRESENTATION.md §3, docs/STAGE_METHOD.md) for the as-of date D, using only
frames captured on or before D; a frame is evidence of a stage when its stage score ≥ threshold:

* planned end passed, stage still seen on the latest frames after the end → behind, N = last seen − end;
* planned start passed, stage not seen, recent frames show only earlier stages → behind, N = D − start;
* stage seen before its planned start → ahead, N = start − first seen (reported as negative delay);
* stage going on, its recent frames lack every pace-critical machine → at_risk («экскаватор без самосвалов»);
* equipment on recent frames needed by no active stage → alert «техника не по этапу»;
* no frames within the window (45 days: the organiser's frames are monthly) → no_data, never a guess.

A stage that finished late (not seen any more, later stages seen) is `done` with its historical delay:
the verdict reflects the current state, earlier slippage shows up again in the successors' dates.
"""
from collections.abc import Iterable, Mapping
from datetime import date, datetime

from backend.app.domain.stage_inference import GENITIVE, UNKNOWN, load_signatures, short

ACTIVE = ("behind", "at_risk", "ahead", "on_track")
MAX_EVIDENCE = 12


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def days(n: int) -> str:
    n = abs(int(n))
    word = "дней" if 11 <= n % 100 <= 14 else {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(n % 10, "дней")
    return f"{n} {word}"


def ddmm(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _frames(observations: Iterable[Mapping], as_of: date, threshold: float) -> list[dict]:
    frames = []
    for o in observations:
        if not o.get("captured_at"):
            continue
        day = as_date(o["captured_at"])
        if day > as_of:
            continue
        evidence = {s["stage_key"] for s in o.get("stages") or []
                    if s["stage_key"] != UNKNOWN and s["score"] >= threshold}
        frames.append({"image_id": o["image_id"], "date": day, "evidence": evidence,
                       "equipment": {k: v for k, v in (o.get("equipment") or {}).items() if v}})
    return sorted(frames, key=lambda f: (f["date"], f["image_id"]))


def _episodes(frames: list[dict], key: str) -> tuple[list[dict], list[dict]]:
    """Evidence frames of a stage without isolated hits.

    An episode is a run of consecutive capture dates on which the stage is seen. A one-date episode that the next
    capture does not confirm is treated as noise (e.g. a lone excavator during site preparation looks exactly like
    excavation). The latest episode is always kept: it has not had a chance to be confirmed yet."""
    dates = sorted({f["date"] for f in frames})
    seen = {f["date"] for f in frames if key in f["evidence"]}
    noise_dates = {d for i, d in enumerate(dates) if d in seen and i + 1 < len(dates)
                   and dates[i + 1] not in seen and (i == 0 or dates[i - 1] not in seen)}
    evidence = [f for f in frames if key in f["evidence"]]
    return ([f for f in evidence if f["date"] not in noise_dates], [f for f in evidence if f["date"] in noise_dates])


def _ids(frames: Iterable[dict]) -> list[str]:
    return list(dict.fromkeys(f["image_id"] for f in frames))[-MAX_EVIDENCE:]


def assess(plan: list[Mapping], observations: Iterable[Mapping], as_of, *, signatures: dict | None = None,
           threshold: float = 0.5, window_days: int = 45, tolerance_days: int = 7,
           supported_classes: Iterable[str] | None = None) -> dict:
    """plan: [{stage_key, name, start, end}]; observations: [{image_id, captured_at, stages:[{stage_key, score}],
    equipment:{class: count}}]. supported_classes: classes whose absence may raise an alert (None = all)."""
    sig = signatures or load_signatures()
    stages_sig = sig["stages"]
    D = as_date(as_of)
    frames = _frames(observations, D, threshold)
    recent = [f for f in frames if (D - f["date"]).days <= window_days]
    latest = frames[-1]["date"] if frames else None
    supported = None if supported_classes is None else {str(c) for c in supported_classes}

    def order(key):
        return stages_sig.get(key, {}).get("order", 99)

    def observable(key):
        return key in stages_sig and stages_sig[key].get("observable", True)

    rows, alerts = [], []
    for item in plan:
        key, name = item.get("stage_key"), item["name"]
        ps, pe = as_date(item["start"]), as_date(item["end"])
        state = "not_started" if D < ps else "in_progress" if D <= pe else "should_be_done"
        ev, noise = _episodes(frames, key)
        row = {"stage_key": key, "name": name, "planned_start": ps.isoformat(), "planned_end": pe.isoformat(),
               "planned_state": state, "observed_first": ev[0]["date"].isoformat() if ev else None,
               "observed_last": ev[-1]["date"].isoformat() if ev else None, "observations": len(ev),
               "status": "no_data", "delay_days": None, "explanation": "", "image_ids": _ids(ev)}
        rows.append(row)
        if noise:
            row["ignored_image_ids"] = _ids(noise)
        if not observable(key):
            row["status"] = "not_started" if state == "not_started" else "no_data"
            row["explanation"] = ("Этап не имеет внешнего почерка техники и по снимкам площадки не оценивается"
                                  if key else "Строка плана не сопоставлена ни с одним этапом методики")
            continue
        first, last = (ev[0]["date"], ev[-1]["date"]) if ev else (None, None)
        ongoing = bool(ev) and last == latest and key in {s for f in recent for s in f["evidence"]}
        later = [f for f in frames if any(order(s) > order(key) for s in f["evidence"])]
        earlier_recent = [f for f in recent if f["evidence"] and all(order(s) < order(key) for s in f["evidence"])]
        later_recent = [f for f in recent if any(order(s) > order(key) for s in f["evidence"])]

        def behind_not_started(n, basis):
            row.update(status="behind", delay_days=n, image_ids=_ids(earlier_recent),
                       explanation=f"{basis}: по плану с {ddmm(ps)}, на снимках до {ddmm(D)} этап не виден — "
                                   f"видны только предыдущие этапы. Старт задерживается на {days(n)}")

        marker = stages_sig[key].get("completed_by") or []
        standing = [f for f in frames if any(f["equipment"].get(e) for e in marker)]
        if marker and standing and not ongoing:
            row.update(status="done", delay_days=0, image_ids=_ids(standing[:3]),
                       explanation=f"{short(marker[0]).capitalize()} уже стоит на снимках с {ddmm(standing[0]['date'])}: "
                                   f"этап выполнен")
        elif state != "should_be_done" and ev and (ps - first).days > tolerance_days and (ongoing or state == "not_started"):
            n = (ps - first).days
            early = [f for f in ev if f["date"] < ps]
            row.update(status="ahead", delay_days=-n, image_ids=_ids(early),
                       explanation=f"Виден с {ddmm(first)}, по плану начало {ddmm(ps)}: опережение на {days(n)}")
        elif state == "not_started":
            row.update(status="not_started", delay_days=0,
                       explanation=f"По плану начало {ddmm(ps)}" + (", этап уже виден в пределах допуска" if ev else ""))
        elif state == "in_progress":
            if ev and ongoing:
                row.update(status="on_track", delay_days=0,
                           explanation=f"Идёт по плану (до {ddmm(pe)}): подтверждён снимками, последний {ddmm(last)}")
            elif not recent:
                row["explanation"] = f"Нет снимков за последние {days(window_days)}"
            elif ev and later_recent:
                row.update(status="done", delay_days=0, explanation=f"Не виден после {ddmm(last)}, идут следующие этапы: "
                                                                   f"вероятно завершён раньше плана ({ddmm(pe)})")
            elif ev:
                row["explanation"] = f"Виден до {ddmm(last)}, на свежих снимках не подтверждён"
            elif (D - ps).days <= tolerance_days:
                row.update(status="not_started", delay_days=0,
                           explanation=f"Плановый старт {ddmm(ps)}, на снимках этап пока не виден (в пределах допуска)")
            elif later_recent:
                row.update(status="on_track", delay_days=0,
                           explanation="Этап на снимках не выделяется, но уже видны последующие этапы")
            elif earlier_recent:
                behind_not_started((D - ps).days, "Не начат")
            else:
                row["explanation"] = "На свежих снимках нет распознанных признаков этапа"
        else:  # should_be_done
            if ev:
                late = (last - pe).days
                if late > tolerance_days and ongoing:
                    row.update(status="behind", delay_days=late, image_ids=_ids(f for f in ev if f["date"] > pe),
                               explanation=f"По плану завершение {ddmm(pe)}, но этап всё ещё виден {ddmm(last)}: "
                                           f"отставание {days(late)}")
                elif ongoing:
                    row.update(status="on_track", delay_days=0,
                               explanation=f"Ещё идёт: плановое окончание {ddmm(pe)}, виден {ddmm(last)} (в пределах допуска "
                                           f"{days(tolerance_days)})")
                elif late > tolerance_days:
                    row.update(status="done", delay_days=late,
                               explanation=f"Завершён с опозданием: последний раз виден {ddmm(last)}, план до {ddmm(pe)} "
                                           f"(+{days(late)})")
                else:
                    row.update(status="done", delay_days=0,
                               explanation=f"Выполнен в срок: последний раз виден {ddmm(last)}, план до {ddmm(pe)}")
            elif any(f["date"] >= ps for f in later):
                row.update(status="done", delay_days=0,
                           explanation="Сам этап на снимках не зафиксирован, но последующие этапы уже идут")
            elif earlier_recent:
                behind_not_started((D - ps).days, "Не выполнен")
            elif not recent:
                row["explanation"] = f"Нет снимков за последние {days(window_days)}"
            else:
                row["explanation"] = "Этап на снимках не зафиксирован; последующих этапов тоже не видно"

        # Темп: идущий этап на своих последних кадрах без техники, от которой зависит скорость.
        pace = stages_sig[key].get("pace_critical") or []
        if pace and ongoing and row["status"] in ("on_track", "behind", "ahead"):
            if supported is not None and not set(pace) <= supported:
                row["explanation"] += (". Темп не оценивается: распознавание техники «" + "», «".join(short(p) for p in pace)
                                       + "» пока ненадёжно")
            else:
                last_frames = [f for f in ev if f["date"] == last]
                if not any(f["equipment"].get(p) for f in last_frames for p in pace):
                    seen = [m for g in stages_sig[key]["required"] for m in g
                            if any(f["equipment"].get(m) for f in last_frames)]
                    lead = short(seen[0]) if seen else "техника этапа"
                    message = (f"{name}: на снимках от {ddmm(last)} {lead} без "
                               f"{' и '.join(GENITIVE.get(p, short(p, 2)) for p in pace)} — риск снижения темпа")
                    alerts.append({"severity": "medium", "kind": "pace_risk", "stage_key": key, "message": message,
                                   "image_ids": _ids(last_frames)})
                    if row["status"] == "on_track":
                        row["status"] = "at_risk"
                    row["explanation"] += f". Риск снижения темпа: нет {' / '.join(GENITIVE.get(p, p) for p in pace)}"

        if row["status"] == "behind":
            alerts.append({"severity": "high" if row["delay_days"] > 30 else "medium", "kind": "behind",
                           "stage_key": key, "message": f"{name}: {row['explanation']}", "image_ids": row["image_ids"]})
        elif row["status"] == "ahead":
            alerts.append({"severity": "low", "kind": "ahead", "stage_key": key,
                           "message": f"{name}: {row['explanation']}", "image_ids": row["image_ids"]})
        elif row["status"] == "no_data" and state != "not_started" and recent:
            alerts.append({"severity": "low", "kind": "no_data", "stage_key": key,
                           "message": f"{name}: {row['explanation']}", "image_ids": []})

    alerts += _unexpected(plan, recent, rows, stages_sig, sig.get("always_allowed", []))
    if not recent:
        alerts.append({"severity": "medium", "kind": "no_data", "stage_key": None, "image_ids": [],
                       "message": f"Нет снимков за последние {days(window_days)}" +
                                  (f" (последний {ddmm(latest)})" if latest else "") + ": статус не оценивается"})
    rank = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: rank[a["severity"]])
    return {"date": D.isoformat(), "verdict": verdict(rows, recent, latest, window_days, alerts),
            "stages": rows, "alerts": alerts}


def _features(stage: dict) -> set[str]:
    return ({f for g in stage.get("required", []) for f in g} | {f for f, w in stage.get("supporting", {}).items() if w > 0}
            | set(stage.get("allowed", [])) | set(stage.get("pace_critical", [])))


def _unexpected(plan, recent, rows, stages_sig, always_allowed) -> list[dict]:
    """Equipment on recent frames that no stage active on that date needs (idea of evaluation.evaluate)."""
    found: dict[str, list[dict]] = {}
    planned = {row["stage_key"] for row in rows}
    for f in recent:
        active = [r for r in plan if as_date(r["start"]) <= f["date"] <= as_date(r["end"])]
        allowed = set(always_allowed)
        for key in {r.get("stage_key") for r in active} | (f["evidence"] & planned):
            if key in stages_sig:
                allowed |= _features(stages_sig[key])
        for equipment in f["equipment"]:
            if equipment not in allowed:
                found.setdefault(equipment, []).append(f)
    return [{"severity": "low", "kind": "unexpected_equipment", "stage_key": None,
             "message": f"Техника не по этапу: {short(e)} на снимках от {', '.join(sorted({ddmm(f['date']) for f in fs}))} — "
                        f"ни один текущий этап плана её не требует. Проверьте назначение техники",
             "image_ids": _ids(fs)} for e, fs in sorted(found.items())]


def verdict(rows, recent, latest, window_days, alerts) -> dict:
    if not recent:
        return {"status": "no_data", "delay_days": None,
                "summary": f"Нет данных: нет снимков за последние {days(window_days)}" +
                           (f" (последний {ddmm(latest)})" if latest else "")}
    by = {s: [r for r in rows if r["status"] == s] for s in ACTIVE}
    if by["behind"]:
        worst = max(by["behind"], key=lambda r: r["delay_days"])
        n = worst["delay_days"]
        if worst["observed_last"] and worst["planned_state"] == "should_be_done" and worst["observations"]:
            reason = (f"этап «{worst['name']}» не завершён в срок (последнее наблюдение "
                      f"{as_date(worst['observed_last']).strftime('%d.%m')}, план до {as_date(worst['planned_end']).strftime('%d.%m')})")
        else:
            reason = (f"этап «{worst['name']}» не начат (план с {as_date(worst['planned_start']).strftime('%d.%m')}, "
                      f"на снимках только предыдущие этапы)")
        return {"status": "behind", "delay_days": n, "summary": f"Отстаём на {days(n)}: {reason}"}
    if by["at_risk"]:
        pace = next((a for a in alerts if a["kind"] == "pace_risk"), None)
        return {"status": "at_risk", "delay_days": 0,
                "summary": "Риск отставания: " + (pace["message"] if pace else by["at_risk"][0]["explanation"])}
    if by["ahead"]:
        best = min(by["ahead"], key=lambda r: r["delay_days"])
        n = -best["delay_days"]
        return {"status": "ahead", "delay_days": -n,
                "summary": f"Опережаем на {days(n)}: этап «{best['name']}» виден с "
                           f"{as_date(best['observed_first']).strftime('%d.%m')}, по плану с "
                           f"{as_date(best['planned_start']).strftime('%d.%m')}"}
    if by["on_track"]:
        names = ", ".join(f"«{r['name']}»" for r in by["on_track"])
        return {"status": "on_track", "delay_days": 0,
                "summary": f"Идём по графику: {names} — подтверждено снимками (последний {ddmm(latest)})"}
    return {"status": "no_data", "delay_days": None,
            "summary": "Нет данных: на свежих снимках не распознаны этапы текущего плана"}
