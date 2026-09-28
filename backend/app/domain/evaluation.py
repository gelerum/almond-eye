"""Conservative single-frame evaluation. Unknown data must never become an absence alert."""
from datetime import date
from zoneinfo import ZoneInfo

from backend.app.domain.contracts import LABELS, Observation, Rule


def point_location(point, polygon, epsilon=1e-6):
    x, y = point
    inside = False
    for (ax,ay),(bx,by) in zip(polygon, polygon[1:]+polygon[:1]):
        cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross) <= epsilon and min(ax,bx)-epsilon <= x <= max(ax,bx)+epsilon and min(ay,by)-epsilon <= y <= max(ay,by)+epsilon:
            return "boundary"
        if (ay>y) != (by>y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return "inside" if inside else "outside"


def evaluate(observation: Observation, schedule: dict, zone: dict, timezone: str):
    common = {"mode": observation.mode, "model_version": observation.model_version,
              "schedule_fingerprint": schedule["fingerprint"], "zone_id": zone["id"],
              "time_source": observation.time_source, "captured_at": observation.captured_at.isoformat() if observation.captured_at else None}
    def insufficient(reason):
        return {**common, "status": "insufficient_data", "reason": reason, "alerts": [], "evaluations": []}
    if not observation.captured_at:
        return insufficient("Неизвестно время съёмки")
    if not schedule["calendar_bound"]:
        return insufficient("График не привязан к календарю")
    if not observation.quality_ok or not zone["coverage_confirmed"]:
        return insufficient("Не подтверждены качество снимка или покрытие зоны")
    day = observation.captured_at.astimezone(ZoneInfo(timezone)).date()
    calendar = schedule["parameters"]
    if day.weekday() not in calendar["weekdays"] or day.isoformat() in calendar["holidays"]:
        return insufficient("Вне заданных рабочих дней")
    active = [w for w in schedule["works"] if w["zone_id"]==zone["id"] and date.fromisoformat(w["start_date"]) <= day <= date.fromisoformat(w["end_date"])]
    if not active:
        return insufficient("В зоне нет активного этапа")
    if any(not w["rule"] for w in active):
        return insufficient("Для активного этапа не настроено правило")
    rules = [Rule.model_validate(w["rule"]) for w in active]
    if any(rule.confidence < observation.detection_confidence_floor for rule in rules):
        return insufficient("Порог правила ниже порога выдачи детектора; часть наблюдений могла быть отфильтрована")
    required = {c for rule in rules for group in rule.required for c in group}
    # Even an OR group is conservatively unknown if one alternative is unsupported.
    if not required <= set(observation.supported_classes):
        return insufficient("Детектор не проверен для всех обязательных классов")
    in_zone = []
    for detection in observation.detections:
        x1,y1,x2,y2 = detection.bbox
        location = point_location(((x1+x2)/2,y2), zone["polygon"])
        if location == "boundary":
            return insufficient("Объект на границе зоны; требуется проверка")
        if location == "inside":
            if detection.equipment not in observation.supported_classes:
                return insufficient("В зоне обнаружен класс с непроверенной поддержкой")
            in_zone.append(detection)
    alerts, evaluations = [], []
    allowed = {c for rule in rules for c in rule.allowed} | required
    for work, rule in zip(active, rules):
        observed = {d.equipment for d in in_zone if d.confidence >= rule.confidence}
        missing = [group for group in rule.required if not set(group)&observed]
        # Low confidence candidates prevent asserting absence.
        if any(any(d.equipment in group and d.confidence < rule.confidence for d in in_zone) for group in missing):
            return insufficient("Низкая уверенность в обязательной технике")
        evaluation = {"stage_id":work["stage_id"], "name":work["name"], "source_row":work["source_row"],
                      "source_ids":work["source_ids"], "provenance":work["provenance"],
                      "rule_version":rule.version, "required":rule.required,
                      "observed":sorted(observed), "missing":missing,
                      "status":"possible_absence" if missing else "matches_observations"}
        evaluations.append(evaluation)
        if missing:
            alerts.append({**evaluation, "type":"possible_absence", "message":"На снимке не обнаружено: "+
                           "; ".join(" или ".join(LABELS[c] for c in group) for group in missing)+
                           ". Техника может быть вне обзора или в рейсе."})
    threshold = max(rule.confidence for rule in rules)
    unexpected = {d.equipment for d in in_zone if d.confidence >= threshold and d.equipment not in allowed}
    if unexpected:
        alerts.append({"type":"unexpected_equipment", "classes":sorted(unexpected),
                       "message":"Техника вне перечня всех активных этапов: "+", ".join(LABELS[c] for c in sorted(unexpected))+". Проверьте назначение техники."})
    return {**common, "status":"warning" if alerts else "matches_observations", "alerts":alerts,
            "evaluations":evaluations, "reason":"Проверка одного снимка; присутствие техники не доказывает выполнение объёма работ"}
