"""Scene inventory and review candidates; never infer compliance from missing boxes."""
from collections import Counter
from datetime import date
from zoneinfo import ZoneInfo

from backend.app.domain.evaluation import point_location

PPE = {'NO-Hardhat': 'Возможное отсутствие каски',
       'NO-Safety Vest': 'Возможное отсутствие сигнального жилета',
       'NO-Mask': 'Модель отметила отсутствие маски; необходимость определяется условиями работ'}


def summarize_scene(observation, zone=None, schedule=None, timezone='Europe/Moscow'):
    threshold = max(0.5, observation.detection_confidence_floor)
    counts, zone_counts = Counter(), Counter()
    candidates = []
    weak = boundary = 0
    for index, detection in enumerate(observation.model_detections):
        if detection.confidence < threshold:
            weak += 1
            continue
        counts[detection.label] += 1
        x1, _, x2, y2 = detection.bbox
        location = point_location(((x1+x2)/2, y2), zone['polygon']) if zone else 'unassigned'
        if location == 'inside':
            zone_counts[detection.label] += 1
        elif location == 'boundary':
            boundary += 1
        if detection.label in PPE and observation.quality_ok:
            candidates.append({'type': 'ppe_review', 'label': detection.label,
                               'message': PPE[detection.label], 'detection_index': index,
                               'confidence': detection.confidence, 'bbox': list(detection.bbox),
                               'zone_location': location, 'status': 'needs_review'})
    context = {'status': 'unavailable', 'active_works': [], 'reason': 'Для сравнения нужны график, зона и время снимка'}
    if schedule and zone and observation.captured_at and schedule['calendar_bound']:
        day = observation.captured_at.astimezone(ZoneInfo(timezone)).date()
        calendar = schedule['parameters']
        working = day.weekday() in calendar['weekdays'] and day.isoformat() not in calendar['holidays']
        active = [w for w in schedule['works'] if working and w['zone_id'] == zone['id'] and
                  date.fromisoformat(w['start_date']) <= day <= date.fromisoformat(w['end_date'])]
        context = {'status': 'active' if active else 'outside_plan',
                   'date': day.isoformat(), 'active_works': [
                       {'stage_id': w['stage_id'], 'name': w['name'], 'start_date': w['start_date'],
                        'end_date': w['end_date']} for w in active],
                   'reason': 'Наличие объектов не подтверждает выполнение работ или соблюдение сроков'}
        activity = {label: zone_counts[label] for label in ('Person', 'machinery', 'vehicle') if zone_counts[label]}
        context['observed_activity'] = activity
        if activity and not active and observation.quality_ok and zone['coverage_confirmed']:
            candidates.append({'type': 'activity_outside_plan', 'status': 'needs_review',
                               'message': 'В зоне видны люди, техника или транспорт вне загруженных сроков. Проверьте полноту графика и назначение объектов.'})
    return {'version': 'scene-v1', 'advisory': True,
            'status': 'observations' if observation.quality_ok else 'quality_limited',
            'confidence_threshold': threshold, 'frame_counts': dict(counts),
            'zone_counts': dict(zone_counts) if zone else None,
            'weak_detections': weak, 'boundary_detections': boundary,
            'review_candidates': candidates, 'plan_context': context,
            'limitations': [
                'Это количество рамок по классам, а не число уникальных людей или комплектов СИЗ. Классы нельзя суммировать как людей.',
                'Отсутствие детекции не доказывает отсутствие объекта. Качество модели на целевой выборке не измерено.',
                'СИЗ не сопоставлены конкретным людям. Сигналы требуют проверки; отсутствие каски не выводится из отсутствия рамки каски.',
                'По одному кадру не определяются движение, расстояния в метрах, объём и процент выполнения работ.',
            ]}
