from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.app.paths import WORK_CATALOG_XLSX

import pytest

from backend.app.domain.contracts import CalculationRequest, Equipment, ImportOptions, Observation, Stage, Zone
from backend.app.domain.evaluation import evaluate
from backend.app.services.import_schedule import preview_import
from backend.app.services.scheduling import calculate


def stage(key="a"):
    return Stage(external_id=key,source_row=72,name="Выемка грунта",sequence_index=0)


def request(**overrides):
    data={"anchor":"2026-09-25","anchor_source":"demonstration","works":[{
        "stage_id":"a","zone_id":"z","volume":1200,"output_per_shift":200,
        "source_ids":["source"],"assumptions":["demo"],"rule":{
            "version":"v1","required":[["excavator"],["dump_truck"]]}}]}
    data.update(overrides)
    return CalculationRequest.model_validate(data)


def scheduled(req=None):
    return calculate(req or request(),[stage(),stage("b")],{"source"})


def observation(classes=("excavator","dump_truck"),**kwargs):
    return Observation.model_validate({"captured_at":"2026-09-25T10:00:00+03:00","time_source":"demonstration",
        "quality_ok":True,"supported_classes":list(Equipment),"model_version":"fixture-v1","mode":"fixture",
        "detections":[{"equipment":c,"confidence":0.9,"bbox":[0.2,0.2,0.4,0.6]} for c in classes],**kwargs})


def result(obs=None,plan=None,zone=None):
    return evaluate(obs or observation(),plan or scheduled(),zone or {
        "id":"z","coverage_confirmed":True,"polygon":[[0,0],[1,0],[1,1],[0,1]]},"Europe/Moscow")


def test_customer_excel_restores_numbers_without_inventing_dates():
    file=WORK_CATALOG_XLSX
    p=preview_import(file.read_bytes(),file.name,ImportOptions(recover_date_numbers=True))
    assert len(p.stages)==377
    assert sum(i.code=='date_in_number' for i in p.issues)==19
    assert not [i for i in p.issues if i.severity=='error']
    rows={s.source_row:s for s in p.stages}
    assert rows[5].external_id=='10.1'
    assert rows[47].parent_id=='12.3'
    assert rows[72].name=='Выемка грунта котлована'
    assert rows[72].hierarchy_status=='proposed'
    assert all(s.start_date is None and s.end_date is None for s in p.stages)


@pytest.mark.parametrize('data,code',[
    ('id,name,parent\na,A,\na,B,','duplicate_id'),
    ('id,name,parent\na,A,b\nb,B,a','cycle'),
    ('id,name,parent\na,A,missing','missing_parent'),
])
def test_invalid_hierarchy(data,code):
    p=preview_import(data.encode(),'input.csv',ImportOptions(header_row=1,parent_column=3,object_columns=[]))
    assert code in [i.code for i in p.issues]


def test_dates_and_no_partial_row_acceptance():
    p=preview_import(b'id,name,start,end\na,A,2026-02-03,2026-02-01','a.csv',
         ImportOptions(header_row=1,start_column=3,end_column=4,object_columns=[]))
    assert any(i.severity=='error' for i in p.issues)


def test_calendar_and_relative_schedule():
    w=scheduled()['works'][0]
    assert w['duration_days']==6
    assert w['start_date']=='2026-09-25' and w['end_date']=='2026-10-02'
    assert not scheduled(request(anchor=None,anchor_source='unknown'))['calendar_bound']
    holiday=scheduled(request(holidays=['2026-09-28']))['works'][0]
    assert holiday['end_date']=='2026-10-05'


def test_dependency_parallel_and_cycle():
    data=request().model_dump(mode='json')
    data['works'].append({**deepcopy(data['works'][0]),'stage_id':'b','predecessors':['a']})
    sequential=scheduled(CalculationRequest.model_validate(data))
    assert sequential['works'][1]['start_date']=='2026-10-05'
    data['works'][1]['predecessors']=[]
    parallel=scheduled(CalculationRequest.model_validate(data))
    assert parallel['works'][0]['start_date']==parallel['works'][1]['start_date']
    data['works'][0]['predecessors']=['b'];data['works'][1]['predecessors']=['a']
    with pytest.raises(ValueError,match='Цикл'):
        scheduled(CalculationRequest.model_validate(data))


def test_unit_mismatch():
    data=request().model_dump();data['works'][0]['output_unit']='t'
    with pytest.raises(ValueError,match='Единицы'):
        scheduled(CalculationRequest.model_validate(data))


def test_good_absent_unexpected():
    assert result()['status']=='matches_observations'
    missing=result(observation(['excavator']))
    assert missing['alerts'][0]['missing']==[['dump_truck']]
    extra=result(observation(['excavator','dump_truck','roller']))
    assert extra['alerts'][0]['type']=='unexpected_equipment'


def test_parallel_allowed_union():
    data=request().model_dump();data['works'].append({**deepcopy(data['works'][0]),'stage_id':'b',
          'rule':{'version':'v2','required':[['roller']]}})
    assert result(observation(['excavator','dump_truck','roller']),scheduled(CalculationRequest.model_validate(data)))['status']=='matches_observations'


@pytest.mark.parametrize('kwargs',[
    {'quality_ok':False}, {'captured_at':None,'time_source':'unknown'},
    {'supported_classes':['excavator']}, {'captured_at':'2026-09-26T10:00:00+03:00'},
    {'captured_at':'2026-10-05T10:00:00+03:00'},
])
def test_insufficient_data(kwargs):
    answer=result(observation(**kwargs))
    assert answer['status']=='insufficient_data' and not answer['alerts']


def test_zone_boundary_and_other_zone():
    zone={'id':'z','coverage_confirmed':True,'polygon':[[0.6,0],[1,0],[1,1],[0.6,1]]}
    assert result(zone=zone)['alerts'][0]['type']=='possible_absence'
    zone['polygon']=[[0.3,0],[1,0],[1,1],[0.3,1]]
    assert result(zone=zone)['status']=='insufficient_data'


def test_alternatives_and_date_timezone():
    data=request().model_dump();data['works'][0]['rule']['required']=[['mobile_crane','loader_crane']]
    plan=scheduled(CalculationRequest.model_validate(data))
    assert result(observation(['loader_crane']),plan)['status']=='matches_observations'
    assert result(observation(captured_at='2026-09-24T21:00:00Z'))['status']=='matches_observations'
    assert result(observation(captured_at='2026-09-24T20:59:59Z'))['status']=='insufficient_data'
    assert result(observation(captured_at='2026-10-02T20:59:59Z'))['status']=='matches_observations'


def test_low_confidence_and_unconfigured_rule():
    obs=observation().model_dump();obs['detections'][0]['confidence']=0.1
    assert result(Observation.model_validate(obs))['status']=='insufficient_data'
    plan=scheduled();plan['works'][0]['rule']=None
    assert result(plan=plan)['status']=='insufficient_data'


def test_polygon_rejects_intersections():
    with pytest.raises(ValueError):
        Zone(name='bad',camera_id='c',polygon=[(0,0),(1,1),(0,1),(1,0)])


def test_filtered_model_output_cannot_support_lower_rule_threshold():
    answer=result(observation(detection_confidence_floor=.6))
    assert answer['status']=='insufficient_data' and answer['alerts']==[]
