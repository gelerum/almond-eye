import hashlib
import io

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app.domain.contracts import Detection, Equipment
from backend.app.main import create_app
from backend.app.services.detector import DetectionBatch
from backend.app.services.gantt_import import render_gantt_xlsx

PLAN = [{"name": "Устройство свай", "start": "2024-02-01", "end": "2024-04-30", "stage_key": "piling"},
        {"name": "Разработка котлована", "start": "2024-04-01", "end": "2024-05-22"},
        {"name": "Устройство фундаментов и ростверков", "start": "2024-06-01", "end": "2024-08-15"},
        {"name": "Ввод объекта", "start": "2025-06-01", "end": "2025-06-30"}]


class FakeDetector:
    model_version = 'fake-stage-v1'
    supported_classes = list(Equipment)

    def __init__(self):
        self.by_sha = {}

    def detect(self, image_path):
        classes = self.by_sha[image_path.name]
        return DetectionBatch(model_version=self.model_version, supported_classes=self.supported_classes,
                              detections=[Detection(equipment=c, confidence=.9, bbox=(.1 + .1*i, .2, .2 + .1*i, .5),
                                                    sources=['fake']) for i, c in enumerate(classes)])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path}/test.db', tmp_path/'runtime', detector=FakeDetector())) as c:
        yield c


def png(colour):
    stream = io.BytesIO()
    Image.new('RGB', (640, 480), colour).save(stream, format='PNG')
    return stream.getvalue()


def ok(response):
    assert response.status_code in (200, 201, 202), response.text
    return response.json()


def test_plan_upload_analysis_and_status_verdict(client):
    c = client
    base = f"/api/projects/{ok(c.post('/api/projects', json={'name': 'Площадка B'}))['id']}"
    preview = ok(c.post(base+'/plans/xlsx/preview',
                        files={'file': ('plan.xlsx', render_gantt_xlsx('Площадка B', PLAN, hide_key=False))}))
    assert [s['stage_key'] for s in preview['stages']] == ['piling', 'excavation', 'foundation', 'commissioning']
    assert c.get(base+'/status').status_code == 404  # плана ещё нет
    plan = ok(c.post(base+'/plans/xlsx/confirm', json={'preview_id': preview['id'], 'accept_warnings': True}))
    assert ok(c.get(base+'/plans'))[0]['id'] == plan['id']
    camera = ok(c.post(base+'/cameras', json={'name': 'Камера 1'}))
    shots = [('2024-04-24', 'white', ['drilling_rig', 'concrete_mixer']),
             ('2024-05-27', 'gray', ['excavator', 'dump_truck']),
             ('2024-06-25', 'black', ['excavator']), ('2024-06-25', 'navy', ['excavator'])]
    images = []
    for i, (day, colour, classes) in enumerate(shots):
        content = png(colour)
        c.app.state.detector.by_sha[hashlib.sha256(content).hexdigest()] = classes
        images.append(ok(c.post(base+'/images', files={'file': (f'{i}.png', content)},
                                data={'camera_id': camera['id'], 'captured_at': f'{day}T12:00:00+03:00',
                                      'time_source': 'user'})))
    one = ok(c.post(base+f"/images/{images[0]['id']}/analyze-stage"))
    assert one['top_stage'] == 'piling' and one['detections'][0]['bbox'] == [.1, .2, .2, .5]
    job = ok(c.post(base+'/analyze-all'))
    assert job['total'] == 3  # первый снимок уже проанализирован этой моделью
    job = ok(c.get(base+f"/jobs/{job['id']}"))
    assert job['status'] == 'done' and job['done'] == 3 and not job['errors']
    assert ok(c.post(base+'/analyze-all'))['total'] == 0
    assert ok(c.get(base+f"/images/{images[2]['id']}/analysis"))['top_stage'] == 'excavation'

    status = ok(c.get(base+'/status'))
    assert status['date'] == '2024-06-25' and status['plan_id'] == plan['id']
    assert status['verdict']['status'] == 'behind' and status['verdict']['delay_days'] == 34
    assert 'Отстаём на 34 дня' in status['verdict']['summary']
    stages = {s['stage_key']: s for s in status['stages']}
    assert stages['excavation']['status'] == 'behind' and stages['excavation']['observed_last'] == '2024-06-25'
    assert stages['foundation']['status'] == 'behind' and stages['foundation']['delay_days'] == 24
    kinds = {a['kind'] for a in status['alerts']}
    assert {'behind', 'pace_risk'} <= kinds
    pace = next(a for a in status['alerts'] if a['kind'] == 'pace_risk')
    assert set(pace['image_ids']) == {images[2]['id'], images[3]['id']}
    obs = status['observations'][0]
    assert set(obs) >= {'image_id', 'captured_at', 'top_stage', 'top_score', 'stages', 'equipment', 'materials',
                        'image_url', 'analysis_id'}

    earlier = ok(c.get(base+'/status', params={'date': '2024-05-27'}))
    # 27.05: котлован идёт, плановое окончание прошло 5 дней назад — в пределах допуска 7 дней
    assert earlier['verdict']['status'] == 'on_track' and len(earlier['observations']) == 2
    late = ok(c.get(base+'/status', params={'date': '2024-09-30'}))
    assert late['verdict']['status'] == 'no_data'


def test_health_and_catalogue_list_ensemble_and_materials(client):
    health = ok(client.get('/api/health'))
    assert health['stage_signatures'] and health['ensemble'] is None
    catalogue = ok(client.get('/api/equipment'))
    assert {'material', 'equipment'} == {e['kind'] for e in catalogue}
    assert any(e['id'] == 'rebar' and e['name'] == 'Арматура' for e in catalogue)


def test_analysis_requires_detector(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path}/t.db', tmp_path/'rt')) as c:
        base = f"/api/projects/{ok(c.post('/api/projects', json={'name': 'X'}))['id']}"
        assert c.post(base+'/analyze-all').status_code == 503
