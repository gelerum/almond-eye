import assert from 'node:assert/strict';
import {test} from 'node:test';
import {renderEvidence, renderScene} from '../../apps/web/evidence.js';

test('scene review is separate from schedule compliance and safely renders untrusted text',()=>{
  const html = renderScene({status:'observations', confidence_threshold:.5,
    frame_counts:{Person:2,'<img>':1}, zone_counts:null, weak_detections:3,boundary_detections:0,
    review_candidates:[{message:'<script>candidate</script>',confidence:.8,zone_location:'unassigned'}],
    plan_context:{reason:'Нет графика',status:'unavailable',active_works:[]},limitations:['Не уникальные люди']});
  assert.match(html,/Человек: 2/);
  assert.match(html,/Зона не выбрана/);
  assert.match(html,/Требует просмотра человеком/);
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<img>|<script>|Соответствует наблюдениям/);
});

test('renders hazard classes without promoting generic machinery to excavator',()=>{
  const html=renderEvidence({mode:'model',observation:{detections:[],model_detections:[
    {label:'machinery',confidence:.8,bbox:[.1,.2,.4,.8]},
    {label:'NO-Hardhat',confidence:.7,bbox:[.2,.3,.4,.6]},
    {label:'<script>',confidence:.1,bbox:[.2,.3,.4,.6]}
  ]}},'/original.png');
  assert.match(html,/Техника \(общий класс\): 80.0%/);
  assert.match(html,/Без каски: 70.0%/);
  assert.doesNotMatch(html,/Экскаватор|<script>/);
  assert.match(html,/&lt;script&gt;/);
});

test('renders immutable zone and normalized boxes over source image',()=>{
  const html=renderEvidence({mode:'model',model_version:'sha256:v1',schedule_id:'schedule-1',
    zone_snapshot:{name:'Зона А',polygon:[[0,0],[1,0],[1,1]]},
    observation:{detections:[{equipment:'excavator',confidence:.9,bbox:[.1,.2,.4,.8]}]}},'/original.png');
  assert.match(html,/points="0,0 1000,0 1000,1000"/);
  assert.match(html,/x="100" y="200"/);
  assert.match(html,/Экскаватор: 90.0%/);
  assert.match(html,/sha256:v1/);
  assert.match(html,/image|img/);
});

test('fixture and demonstration cannot appear as unmarked evidence',()=>{
  const html=renderEvidence({mode:'fixture',time_source:'demonstration',observation:{detections:[]}},'/original.png');
  assert.match(html,/ТЕСТОВЫЕ ДЕТЕКЦИИ/);
  assert.match(html,/демонстрационные/);
  assert.match(html,/Рамки не получены/);
});

test('escapes names and versions from saved data',()=>{
  const html=renderEvidence({model_version:'<script>alert(1)</script>',zone_snapshot:{name:'<img onerror=x>',polygon:[]}},'/original.png');
  assert.doesNotMatch(html,/<script>/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/&lt;img onerror=x&gt;/);
});
