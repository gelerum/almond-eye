from datetime import date

from backend.app.domain.progress import assess, days
from backend.app.domain.stage_inference import infer_stages

PLAN = [
    {"stage_key": "piling", "name": "Шпунтовое ограждение и сваи", "start": "2024-02-01", "end": "2024-04-30"},
    {"stage_key": "excavation", "name": "Разработка котлована", "start": "2024-04-15", "end": "2024-05-31"},
    {"stage_key": "foundation", "name": "Фундаменты и ростверки", "start": "2024-06-01", "end": "2024-08-31"},
    {"stage_key": "commissioning", "name": "Ввод объекта", "start": "2025-06-01", "end": "2025-06-30"},
]


def obs(image_id, day, *equipment):
    counts = {e: equipment.count(e) for e in equipment}
    return {"image_id": image_id, "captured_at": day, "equipment": counts,
            "stages": [{"stage_key": r["stage_key"], "score": r["score"]} for r in infer_stages(counts)]}


def row(result, key):
    return next(r for r in result["stages"] if r["stage_key"] == key)


def test_days_declension():
    assert [days(n) for n in (1, 2, 5, 11, 21, 34)] == ["1 день", "2 дня", "5 дней", "11 дней", "21 день", "34 дня"]


def test_on_track_when_plan_matches_photos():
    result = assess(PLAN, [obs("p1", "2024-02-20", "drilling_rig"), obs("p2", "2024-03-20", "drilling_rig", "concrete_mixer")],
                    "2024-03-20")
    assert row(result, "piling")["status"] == "on_track"
    assert row(result, "excavation")["status"] == "not_started"
    assert row(result, "commissioning")["planned_state"] == "not_started"
    assert result["verdict"]["status"] == "on_track" and "«Шпунтовое ограждение и сваи»" in result["verdict"]["summary"]


def test_behind_when_stage_seen_after_planned_end_with_evidence():
    observations = [obs("p1", "2024-04-20", "drilling_rig"), obs("e1", "2024-05-27", "excavator", "dump_truck"),
                    obs("e2", "2024-06-25", "excavator"), obs("e3", "2024-06-25", "excavator")]
    result = assess(PLAN, observations, "2024-06-25")
    excavation = row(result, "excavation")
    assert excavation["status"] == "behind" and excavation["delay_days"] == 25
    assert excavation["observed_first"] == "2024-05-27" and excavation["observations"] == 3
    # Фундамент по плану идёт с 01.06, а видим только котлован: старт задерживается на D − start.
    foundation = row(result, "foundation")
    assert foundation["status"] == "behind" and foundation["delay_days"] == 24 and foundation["image_ids"] == ["e1", "e2", "e3"]
    assert row(result, "piling")["status"] == "done"
    verdict = result["verdict"]
    assert verdict["status"] == "behind" and verdict["delay_days"] == 25
    assert verdict["summary"].startswith("Отстаём на 25 дней: этап «Разработка котлована» не завершён в срок")
    kinds = {a["kind"]: a for a in result["alerts"]}
    assert kinds["behind"]["image_ids"] and kinds["behind"]["severity"] in ("high", "medium")
    pace = kinds["pace_risk"]
    assert pace["image_ids"] == ["e2", "e3"] and "экскаватор без самосвалов" in pace["message"]
    assert result["alerts"][0]["severity"] == "high" or all(a["severity"] != "high" for a in result["alerts"])


def test_pace_risk_marks_active_stage_at_risk():
    result = assess(PLAN, [obs("e1", "2024-05-10", "excavator")], "2024-05-10")
    assert row(result, "excavation")["status"] == "at_risk"
    assert result["verdict"]["status"] == "at_risk" and "риск снижения темпа" in result["verdict"]["summary"]
    ok = assess(PLAN, [obs("e1", "2024-05-10", "excavator", "dump_truck")], "2024-05-10")
    assert row(ok, "excavation")["status"] == "on_track" and not [a for a in ok["alerts"] if a["kind"] == "pace_risk"]


def test_pace_alert_skipped_for_unvalidated_classes():
    result = assess(PLAN, [obs("e1", "2024-05-10", "excavator")], "2024-05-10", supported_classes=["excavator"])
    assert row(result, "excavation")["status"] == "on_track"
    assert "Темп не оценивается" in row(result, "excavation")["explanation"]


def test_ahead_when_seen_before_planned_start():
    result = assess(PLAN, [obs("p1", "2024-03-01", "drilling_rig"), obs("e1", "2024-03-25", "excavator", "dump_truck")],
                    "2024-03-25")
    excavation = row(result, "excavation")
    assert excavation["status"] == "ahead" and excavation["delay_days"] == -21 and excavation["image_ids"] == ["e1"]
    assert result["verdict"]["status"] == "ahead" and result["verdict"]["delay_days"] == -21


def test_small_deviation_within_tolerance_is_not_ahead():
    result = assess(PLAN, [obs("e1", "2024-04-10", "excavator", "dump_truck")], "2024-04-10")
    assert row(result, "excavation")["status"] == "not_started"


def test_finished_late_is_done_and_not_in_verdict():
    observations = [obs("p0", "2024-04-20", "drilling_rig"), obs("p1", "2024-05-20", "drilling_rig"),
                    obs("e1", "2024-05-25", "excavator", "dump_truck")]
    result = assess(PLAN, observations, "2024-05-25")
    piling = row(result, "piling")
    assert piling["status"] == "done" and piling["delay_days"] == 20
    assert result["verdict"]["status"] == "on_track"


def test_behind_when_planned_stage_not_started():
    result = assess(PLAN, [obs("p1", "2024-05-10", "drilling_rig")], "2024-05-10")
    excavation = row(result, "excavation")
    assert excavation["status"] == "behind" and excavation["delay_days"] == 25
    assert "не начат" in result["verdict"]["summary"]


def test_no_data_without_recent_frames():
    result = assess(PLAN, [obs("p1", "2024-02-10", "drilling_rig")], "2024-05-01")
    assert result["verdict"]["status"] == "no_data" and result["verdict"]["delay_days"] is None
    assert any(a["kind"] == "no_data" and a["stage_key"] is None for a in result["alerts"])
    empty = assess(PLAN, [], date(2024, 5, 1))
    assert empty["verdict"]["status"] == "no_data" and "Нет данных" in empty["verdict"]["summary"]


def test_future_frames_are_ignored():
    observations = [obs("p1", "2024-03-01", "drilling_rig"), obs("e1", "2024-07-01", "excavator")]
    result = assess(PLAN, observations, "2024-03-01")
    assert row(result, "excavation")["observations"] == 0


def test_low_score_is_not_evidence():
    weak = {"image_id": "w", "captured_at": "2024-05-10", "equipment": {"excavator": 1},
            "stages": [{"stage_key": "excavation", "score": 0.3}]}
    assert row(assess(PLAN, [weak], "2024-05-10"), "excavation")["observations"] == 0


def test_unexpected_equipment_alert_with_evidence():
    result = assess(PLAN, [obs("x", "2024-03-10", "drilling_rig", "roller")], "2024-03-10")
    alert = next(a for a in result["alerts"] if a["kind"] == "unexpected_equipment")
    assert alert["image_ids"] == ["x"] and "каток" in alert["message"]
    assert not [a for a in assess(PLAN, [obs("x", "2024-03-10", "drilling_rig", "truck")], "2024-03-10")["alerts"]
                if a["kind"] == "unexpected_equipment"]


def test_unobservable_and_unmapped_rows():
    plan = PLAN + [{"stage_key": None, "name": "Прочее", "start": "2024-01-01", "end": "2024-02-01"}]
    result = assess(plan, [obs("p1", "2024-03-01", "drilling_rig")], "2025-06-10")
    assert row(result, "commissioning")["status"] == "no_data"
    assert "не сопоставлена" in row(result, None)["explanation"]


def test_isolated_single_date_hit_is_ignored():
    """A lone excavator during site preparation looks like excavation; one unconfirmed date is noise."""
    observations = [obs("x", "2024-01-24", "excavator"), obs("p1", "2024-02-21", "drilling_rig"),
                    obs("p2", "2024-03-25", "drilling_rig")]
    excavation = row(assess(PLAN, observations, "2024-03-25"), "excavation")
    assert excavation["status"] == "not_started" and excavation["ignored_image_ids"] == ["x"]
