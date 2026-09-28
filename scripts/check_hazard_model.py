"""Real CPU inference through upload -> analysis -> persisted evidence, using a temporary DB."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/manifests/hazard-smoke.json')
    args = parser.parse_args()
    os.environ['DETECTOR_PROFILE'] = 'construction-hazard'
    os.environ.pop('DETECTOR_CLASS_MAP', None)
    os.environ['DETECTOR_VALIDATED_CLASSES'] = '[]'
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    import torch
    import ultralytics

    def post(client, url, **kwargs):
        response = client.post(url, **kwargs)
        if response.is_error:
            raise RuntimeError(f"{response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    with tempfile.TemporaryDirectory(prefix='hazard-smoke-') as directory:
        with TestClient(create_app(f'sqlite:///{Path(directory)/"test.db"}', Path(directory))) as client:
            health = client.get('/api/health').json()
            assert health['detector'] == 'ready', health
            project = post(client, '/api/projects', json={'name':'Model smoke check'})
            base = f'/api/projects/{project["id"]}'
            cam = post(client, base+'/cameras', json={'name':'Test camera'})
            zone = post(client, base+'/zones', json={'name':'Full frame', 'camera_id':cam['id'],
                'polygon':[[0,0],[1,0],[1,1],[0,1]], 'coverage_confirmed':True})
            preview = post(client, base+'/imports/preview', files={'file':('schedule.csv',b'id,name\n1,Excavation')},
                           data={'options':json.dumps({'header_row':1,'object_columns':[]})})
            imported = post(client, base+'/imports/confirm', json={'preview_id':preview['id'],'accept_warnings':True})
            calc = post(client, base+f'/imports/{imported["id"]}/calculate', json={
                'anchor':'2026-09-21','anchor_source':'demonstration','works':[{
                    'stage_id':'1','zone_id':zone['id'],'volume':100,'output_per_shift':100,
                    'source_ids':['pkti-62-04-2007'],'assumptions':['Smoke test only'],
                    'rule':{'version':'smoke-v1','required':[['excavator'],['dump_truck']]}}]})
            schedule = post(client, base+'/schedules/confirm', json={'preview_id':calc['id']})
            results = []
            for path in args.images:
                picture = post(client, base+'/images', files={'file':(path.name,path.read_bytes())}, data={
                    'camera_id':cam['id'],'captured_at':'2026-09-21T10:00:00+03:00','time_source':'demonstration'})
                started = time.perf_counter()
                result = post(client, base+'/evaluations/analyze', json={
                    'image_id':picture['id'],'schedule_id':schedule['id'],'zone_id':zone['id']})
                elapsed = time.perf_counter()-started
                saved = client.get(base+f'/evaluations/{result["id"]}').json()
                assert saved == result
                assert result['mode'] == 'model'
                assert result['status'] == 'insufficient_data' and not result['alerts']
                assert not result['observation']['detections']
                assert result['scene']['advisory'] is True
                assert result['scene']['plan_context']['status'] == 'active'
                raw = result['observation']['model_detections']
                results.append({'image':path.name,'sha256':picture['sha256'],'seconds':round(elapsed,3),
                    'status':result['status'],'reason':result['reason'],
                    'detections':raw, 'scene':result['scene'],
                    'classes_at_conf_0_25':dict(Counter(d['label'] for d in raw if d['confidence']>=.25))})
            assert any(r['classes_at_conf_0_25'] for r in results), 'No confident detections on supplied images'
            report = {'check':'real CPU inference and API persistence; not an accuracy evaluation',
                'ultralytics':ultralytics.__version__,'torch':torch.__version__, 'health':health,'results':results}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({r['image']:r['classes_at_conf_0_25'] for r in results},ensure_ascii=False))
            print(args.output)


if __name__ == '__main__':
    main()
