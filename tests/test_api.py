import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app.main import create_app
from backend.app.services.detector import DetectionBatch
from backend.app.domain.contracts import Detection, Equipment


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path}/test.db',tmp_path/'runtime')) as c:
        yield c


def post(c,path,data):
    response=c.post(path,json=data)
    assert response.status_code in (200,201),response.text
    return response.json()


class FakeDetector:
    def detect(self, image_path):
        return DetectionBatch(model_version='fake-model-v1', supported_classes=list(Equipment),
                              detections=[Detection(equipment='excavator',confidence=.9,bbox=(.2,.2,.4,.5))])


def test_end_to_end_versions_and_tenant_boundaries(client):
    c=client
    p=post(c,'/api/projects',{'name':'Тест'})
    base=f"/api/projects/{p['id']}"
    cam=post(c,base+'/cameras',{'name':'Камера'})
    z=post(c,base+'/zones',{'name':'Котлован','camera_id':cam['id'],'polygon':[[0,0],[1,0],[1,1],[0,1]],'coverage_confirmed':True})
    file=next(Path('.').glob('*.xlsx'))
    preview=c.post(base+'/imports/preview',files={'file':(file.name,file.read_bytes())},data={'options':'{"recover_date_numbers":true}'}).json()
    assert c.post(base+'/imports/confirm',json={'preview_id':preview['id']}).status_code==422
    imported=post(c,base+'/imports/confirm',{'preview_id':preview['id'],'accept_warnings':True})
    assert post(c,base+'/imports/confirm',{'preview_id':preview['id'],'accept_warnings':True})['id']==imported['id']
    stage=next(s for s in imported['stages'] if s['source_row']==72)
    params={'anchor':'2026-09-21','anchor_source':'demonstration','works':[{'stage_id':stage['external_id'],'zone_id':z['id'],
        'volume':1200,'output_per_shift':200,'source_ids':['pkti-62-04-2007'],'assumptions':['Демонстрация'],
        'rule':{'version':'v1','required':[['excavator'],['dump_truck']]}}]}
    calc=post(c,base+f"/imports/{imported['id']}/calculate",params)
    schedule=post(c,base+'/schedules/confirm',{'preview_id':calc['id']})
    picture=io.BytesIO();Image.new('RGB',(640,480),'white').save(picture,format='PNG')
    img=c.post(base+'/images',files={'file':('test.png',picture.getvalue())},data={'camera_id':cam['id'],'captured_at':'2026-09-21T10:00:00+03:00','time_source':'demonstration'}).json()
    c.app.state.detector=FakeDetector()
    analyzed=post(c,base+'/evaluations/analyze',{'image_id':img['id'],'schedule_id':schedule['id'],'zone_id':z['id']})
    assert analyzed['mode']=='model' and analyzed['model_version']=='fake-model-v1'
    body={'image_id':img['id'],'schedule_id':schedule['id'],'zone_id':z['id'],'observation':{
        'model_version':'fixture-v1','mode':'fixture','quality_ok':True,'supported_classes':['excavator','dump_truck'],
        'detections':[{'equipment':'excavator','confidence':.9,'bbox':[.2,.2,.4,.5]}]}}
    first=post(c,base+'/evaluations/fixture',body)
    assert first['alerts'][0]['type']=='possible_absence'
    assert post(c,base+'/evaluations/fixture',body)['id']==first['id']
    params['works'][0]['rule']['version']='v2'
    params['works'][0]['rule']['required']=[['excavator']]
    next_calc=post(c,base+f"/imports/{imported['id']}/calculate",params)
    next_schedule=post(c,base+'/schedules/confirm',{'preview_id':next_calc['id']})
    body['schedule_id']=next_schedule['id']
    second=post(c,base+'/evaluations/fixture',body)
    assert second['status']=='matches_observations' and second['id']!=first['id']
    assert c.get(base+f"/evaluations/{first['id']}").json()['alerts'][0]['type']=='possible_absence'
    assert c.get(base+f"/images/{img['id']}/file").content==picture.getvalue()
    other=post(c,'/api/projects',{'name':'Другая'})
    assert c.get(f"/api/projects/{other['id']}/images/{img['id']}/file").status_code==404
    health=c.get('/api/health').json()
    assert health['detector']=='ready' and health['worker']=='synchronous'


def test_bad_import_does_not_commit(client):
    p=post(client,'/api/projects',{'name':'Test'})
    base=f"/api/projects/{p['id']}"
    pre=client.post(base+'/imports/preview',files={'file':('a.csv',b'id,name\na,A\na,B')},data={'options':'{"header_row":1}'}).json()
    assert client.post(base+'/imports/confirm',json={'preview_id':pre['id'],'accept_warnings':True}).status_code==422
    assert client.get(base+'/imports').json()==[]


def test_bad_files_and_naive_time(client):
    p=post(client,'/api/projects',{'name':'Test'})
    base=f"/api/projects/{p['id']}"
    assert client.post(base+'/images/unknown/stage-prediction',json={'image_id':'unknown'}).status_code==503
    cam=post(client,base+'/cameras',{'name':'Cam'})
    assert client.post(base+'/images',files={'file':('bad.png',b'not image')},data={'camera_id':cam['id']}).status_code==422
    assert client.post(base+'/images',files={'file':('bad.png',b'not image')},data={'camera_id':cam['id'],'captured_at':'2026-09-21T10:00:00','time_source':'user'}).status_code==422
    assert client.post(base+'/imports/preview',files={'file':('bad.xlsx',b'not zip')}).status_code==422


def test_batch_images_keeps_valid_files_when_one_fails(client):
    p=post(client,'/api/projects',{'name':'Batch'})
    base=f"/api/projects/{p['id']}"
    cam=post(client,base+'/cameras',{'name':'Cam'})
    good=io.BytesIO();Image.new('RGB',(640,480),'white').save(good,format='PNG')
    response=client.post(base+'/images/batch',files=[
        ('files',('good.png',good.getvalue(),'image/png')),
        ('files',('bad.png',b'not image','image/png')),
    ],data={'camera_id':cam['id'],'manifest':'{"good.png":{"captured_at":"2026-09-21T10:00:00+03:00","time_source":"user"}}'})
    assert response.status_code==201,response.text
    body=response.json()
    assert body['total']==2 and len(body['accepted'])==1 and len(body['errors'])==1
    assert body['accepted'][0]['time_source']=='user'
    assert 'декодируется' in body['errors'][0]['error']
    assert len(client.get(base+'/images').json())==1


def test_batch_duplicate_names_cannot_assign_ambiguous_times(client):
    p=post(client,'/api/projects',{'name':'Batch'})
    base=f"/api/projects/{p['id']}"
    cam=post(client,base+'/cameras',{'name':'Cam'})
    response=client.post(base+'/images/batch',files=[('files',('same.png',b'a')),('files',('same.png',b'b'))],
                         data={'camera_id':cam['id']})
    assert response.status_code==422
    assert client.get(base+'/images').json()==[]
