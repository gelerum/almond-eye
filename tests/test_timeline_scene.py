import csv
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.domain.contracts import ModelDetection, Observation
from backend.app.domain.scene import summarize_scene
from backend.app.main import create_app
from backend.app.services.detector import DetectionBatch
from backend.app.services.timeline_import import COLUMNS, TimelineOptions, preview_timeline


def csv_bytes(rows):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return ('\ufeff'+stream.getvalue()).encode('utf-8')


def test_timeline_validation_and_calendar():
    imported = {'id':'catalog', 'stages':[{'external_id':'A', 'name':'Работа', 'kind':'work', 'source_row':2}]}
    options = TimelineOptions(weekdays=[0,1,2,3,4], holidays=['2026-09-22'])
    def preview(rows):
        return preview_timeline(csv_bytes(rows), 'a.csv', imported, {'zone'}, options, 'Europe/Moscow')
    valid = ['A','Название не является идентификатором','zone','2026-09-21','2026-09-28','none']
    result = preview([valid])
    assert result['works'][0]['duration_days'] == 5
    assert result['works'][0]['name'] == 'Работа'
    assert result['works'][0]['provenance'] == 'uploaded'
    for index, bad in [(0,'unknown'),(2,'other-zone'),(3,'21.09.2026'),(3,''),(4,'2026-09-20'),(5,'automatic')]:
        row = valid.copy(); row[index] = bad
        assert any(i['severity']=='error' and i['row']==2 for i in preview([row])['issues'])
    assert any('Повтор' in i['message'] for i in preview([valid,valid])['issues'])
    assert any('рабочих дней' in i['message'] for i in preview([['A','','zone','2026-09-26','2026-09-27','none']])['issues'])
    with pytest.raises(ValueError):
        preview_timeline(b'\xef\xbb\xbf', 'a.csv', imported, {'zone'}, options, 'Europe/Moscow')


def test_scene_counts_are_not_people_or_confirmed_violations():
    obs = Observation(mode='model', model_version='test', quality_ok=True, model_detections=[
        ModelDetection(label='Person', confidence=.9,bbox=(.1,.1,.2,.3)),
        ModelDetection(label='NO-Hardhat', confidence=.8,bbox=(.1,.1,.2,.2)),
        ModelDetection(label='NO-Safety Vest', confidence=.3,bbox=(.1,.1,.2,.3)),
        ModelDetection(label='machinery', confidence=.9,bbox=(.6,.1,.9,.7)),
    ])
    zone = {'polygon':[[0,0],[.5,0],[.5,1],[0,1]],'id':'z','coverage_confirmed':True}
    scene = summarize_scene(obs, zone)
    assert scene['frame_counts'] == {'Person':1,'NO-Hardhat':1,'machinery':1}
    assert scene['zone_counts'] == {'Person':1,'NO-Hardhat':1}
    assert scene['weak_detections'] == 1
    assert len(scene['review_candidates']) == 1
    assert scene['review_candidates'][0]['detection_index'] == 1
    assert scene['review_candidates'][0]['status'] == 'needs_review'
    assert summarize_scene(obs.model_copy(update={'quality_ok':False}), zone)['review_candidates'] == []
    assert summarize_scene(Observation(mode='model',model_version='empty',quality_ok=True))['review_candidates'] == []


def test_scene_calendar_timezone_and_no_outside_plan_claim_with_unknown_time():
    obs = Observation(mode='model', model_version='test', quality_ok=True,
        captured_at=datetime.fromisoformat('2026-09-20T22:00:00+00:00'),time_source='user',
        model_detections=[ModelDetection(label='Person',confidence=.9,bbox=(.1,.1,.2,.3))])
    zone = {'polygon':[[0,0],[1,0],[1,1],[0,1]],'id':'z','coverage_confirmed':True}
    schedule = {'calendar_bound':True,'parameters':{'weekdays':[0,1,2,3,4],'holidays':[]},
        'works':[{'stage_id':'a','name':'Работа','zone_id':'z','start_date':'2026-09-21','end_date':'2026-09-21'}]}
    assert summarize_scene(obs,zone,schedule)['plan_context']['status'] == 'active'
    schedule['parameters']['holidays'] = ['2026-09-21']
    result = summarize_scene(obs,zone,schedule)
    assert result['review_candidates'][0]['type'] == 'activity_outside_plan'
    assert not summarize_scene(obs,{**zone,'coverage_confirmed':False},schedule)['review_candidates']
    assert summarize_scene(obs.model_copy(update={'captured_at':None}),zone,schedule)['plan_context']['status'] == 'unavailable'


class SceneDetector:
    def detect(self, path):
        return DetectionBatch(model_version='test', supported_classes=[], detections=[],
            model_detections=[ModelDetection(label='Person',confidence=.9,bbox=(.1,.1,.2,.3))])


def test_separate_timeline_and_scene_end_to_end(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path}/test.db',tmp_path,SceneDetector())) as c:
        def post(path, data):
            response = c.post(path,json=data)
            assert response.status_code in (200,201), response.text
            return response.json()
        pid = post('/api/projects',{'name':'Сроки'})['id']
        base = f'/api/projects/{pid}'
        camera = post(base+'/cameras',{'name':'Камера'})
        zone = post(base+'/zones',{'name':'Зона','camera_id':camera['id'],
            'polygon':[[0,0],[1,0],[1,1],[0,1]],'coverage_confirmed':True})
        preview = c.post(base+'/imports/preview',files={'file':('catalog.csv',b'id,name\nA,Work\nB,Other')},
            data={'options':'{"header_row":1,"object_columns":[]}'}).json()
        imported = post(base+'/imports/confirm',{'preview_id':preview['id'],'accept_warnings':True})
        root = base+f"/imports/{imported['id']}"
        template = c.get(root+'/timeline-template',params={'zone_id':zone['id']})
        assert template.status_code == 200
        assert list(csv.reader(io.StringIO(template.content.decode('utf-8-sig')),delimiter=';'))[0] == COLUMNS
        rows = [['A','Work',zone['id'],'2026-09-21','2026-09-25','none']]
        def timeline(rows):
            response = c.post(root+'/timeline-preview',files={'file':('timeline.csv',csv_bytes(rows))})
            assert response.status_code == 200, response.text
            return response.json()
        pre = timeline(rows)
        assert c.post(base+'/schedules/confirm',json={'preview_id':pre['id']}).status_code == 422
        schedule = post(base+'/schedules/confirm',{'preview_id':pre['id'],'accept_warnings':True})
        assert post(base+'/schedules/confirm',{'preview_id':timeline(rows)['id'],'accept_warnings':True})['id'] == schedule['id']
        bad = timeline(rows+rows)
        assert c.post(base+'/schedules/confirm',json={'preview_id':bad['id'],'accept_warnings':True}).status_code == 422
        malformed = (';'.join(COLUMNS)+'\n"unterminated').encode()
        assert c.post(root+'/timeline-preview',files={'file':('bad.csv',malformed)}).status_code == 422
        picture=io.BytesIO(); Image.new('RGB',(640,480),'white').save(picture,format='PNG')
        image = c.post(base+'/images',files={'file':('image.png',picture.getvalue())},data={
            'camera_id':camera['id'],'captured_at':'2026-09-25T23:59:00+03:00','time_source':'user'}).json()
        overview = post(base+'/evaluations/analyze',{'image_id':image['id']})
        assert overview['scene']['frame_counts'] == {'Person':1}
        assert overview['schedule_snapshot'] is None
        evaluated = post(base+'/evaluations/analyze',{'image_id':image['id'],'zone_id':zone['id'],'schedule_id':schedule['id']})
        assert evaluated['scene']['plan_context']['status'] == 'active'
        assert evaluated['status'] == 'insufficient_data'  # No equipment rule fabricated.
        rows[0][4] = '2026-09-24'
        revised = post(base+'/schedules/confirm',{'preview_id':timeline(rows)['id'],'accept_warnings':True})
        assert revised['id'] != schedule['id']
        later = post(base+'/evaluations/analyze',{'image_id':image['id'],'zone_id':zone['id'],'schedule_id':revised['id']})
        assert later['scene']['review_candidates'][0]['type'] == 'activity_outside_plan'
        assert c.get(base+f"/evaluations/{evaluated['id']}").json() == evaluated
        other = post('/api/projects',{'name':'Другая'})['id']
        assert c.post(f'/api/projects/{other}/schedules/confirm',json={'preview_id':pre['id'],'accept_warnings':True}).status_code == 404
        assert c.post(f"/api/projects/{other}/imports/{imported['id']}/timeline-preview",files={'file':('timeline.csv',csv_bytes(rows))}).status_code == 404
        other_cam = post(base+'/cameras',{'name':'Другая камера'})
        other_zone = post(base+'/zones',{'name':'Другая зона','camera_id':other_cam['id'],'polygon':[[0,0],[1,0],[1,1],[0,1]]})
        assert c.post(base+'/evaluations/analyze',json={'image_id':image['id'],'zone_id':other_zone['id']}).status_code == 422
        rows[0][5] = 'excavation'
        rows[0][4] = '2026-09-25'
        equipment_schedule = post(base+'/schedules/confirm',{'preview_id':timeline(rows)['id'],'accept_warnings':True})
        checked = post(base+'/evaluations/fixture',{'image_id':image['id'],'zone_id':zone['id'],
            'schedule_id':equipment_schedule['id'],'observation':{'mode':'fixture','model_version':'fixture',
            'quality_ok':True,'supported_classes':['excavator','dump_truck'],'detections':[
                {'equipment':'excavator','confidence':.9,'bbox':[.1,.1,.2,.3]},
                {'equipment':'dump_truck','confidence':.9,'bbox':[.3,.1,.4,.3]}]}})
        assert checked['status'] == 'matches_observations'
        assert checked['evaluations'][0]['provenance'] == 'uploaded'
