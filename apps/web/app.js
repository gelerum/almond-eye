import {renderEvidence, statusNames, equipmentNames, modelNames} from './evidence.js';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let project, imported=[], preview, calculation, timelinePreview;
const base = () => `/api/projects/${project}`;
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';}
async function api(path,options={}){const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));return result;}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function select(id,items,label){const old=$(id).value;$(id).innerHTML=items.length?items.map(x=>`<option value="${escape(x.id)}">${escape(label(x))}</option>`).join(''):'<option value="">Нет данных</option>';if(items.some(x=>x.id===old))$(id).value=old;}
function table(rows,cols){return `<div class="table-wrap"><table><thead><tr>${cols.map(c=>`<th>${escape(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr class="${r.kind==='summary'?'summary':''}">${cols.map(c=>`<td>${escape(c[1](r))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
function stagesTable(rows){return table(rows,[['Строка',r=>r.source_row],['Номер',r=>r.external_id.startsWith('row:')?'Без номера':r.external_id],['Родитель',r=>r.parent_id],['Работа',r=>r.name],['Тип',r=>r.kind==='summary'?'Раздел':'Работа'],['Иерархия',r=>r.hierarchy_status==='proposed'?'Предложена':'По номеру']]);}
function scheduleTable(rows){return table(rows,[['Работа',r=>r.name],['Зона',r=>zoneItems.find(z=>z.id===r.zone_id)?.name || r.zone_id],['Рабочих дней',r=>r.duration_days],['Начало',r=>r.start_date||`Раб. день ${r.start_offset+1}`],['Окончание',r=>r.end_date||`Раб. день ${r.end_offset+1}`],['Происхождение',r=>({uploaded:'Загружено пользователем',demonstration:'Демонстрационное',calculated:'Расчётное'})[r.provenance]],['Правило',r=>r.rule?.version || 'Не настроено']]);}
function bind(id,fn){$(id).addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button[type="submit"],button:not([type])');if(button.disabled)return;button.disabled=true;try{await fn(new FormData(event.currentTarget));}catch(e){message(e.message,true);}finally{button.disabled=false;}});}
function action(id,fn){$(id).onclick=async()=>{const button=$(id);button.disabled=true;try{await fn();}catch(e){message(e.message,true);}finally{button.disabled=false;}};}
async function projects(){const list=await api('/api/projects');select('project-select',list,p=>p.name);project=$('project-select').value;await refresh();}
let imageItems = [], zoneItems = [], evaluationItems = [], detectorReady = false;
let analysisBusy = false;
async function loadDetectorStatus() {
  const health = await api('/api/health');
  detectorReady = health.detector === 'ready';
  const available = (health.model_classes || health.available_classes || []).map(c => modelNames[c] || equipmentNames[c] || c).join(', ');
  const validated = (health.supported_classes || []).map(c => equipmentNames[c] || c).join(', ');
  $('detector-status').textContent = detectorReady
    ? `Детектор подключён. Классы в весах: ${available || 'не указаны'}. Проверенные классы: ${validated || 'нет — вывод о соответствии недоступен'}.`
    : `Распознавание недоступно: ${health.detector_error || 'веса модели не настроены'}. Снимки можно загружать и просматривать.`;
  updateAnalysisSelection();
}
function updateAnalysisSelection() {
  const image = imageItems.find(i => i.id === $('analysis-image').value);
  select('analysis-zone', [{id:'',name:'Весь кадр, без зоны'}, ...zoneItems.filter(z => z.camera_id === image?.camera_id)], z => z.name);
  $('run-analysis').disabled = analysisBusy || !detectorReady || !image;
}
function renderEvaluations() {
  const rows = evaluationItems.filter(e => (!$('result-status').value || e.status === $('result-status').value)
    && (!$('result-mode').value || e.mode === $('result-mode').value)
    && (!$('result-scene').value || e.scene?.review_candidates?.length > 0));
  $('evaluations').innerHTML = rows.length ? rows.map(e => `<details>
    <summary>${e.mode === 'fixture' ? 'ТЕСТОВЫЕ ДЕТЕКЦИИ · ' : ''}${e.scene ? `Обзор кадра · сигналов: ${e.scene.review_candidates.length} · ` : ''}График: ${escape(statusNames[e.status] || e.status)} · ${escape(e.captured_at || 'время неизвестно')}</summary>
    <p>${escape(e.reason)}</p>${e.alerts.map(a => `<p>${escape(a.message)}</p>`).join('')}
    ${renderEvidence(e, `${base()}/images/${e.image_id}/file`)}
    <details><summary>Условия проверки этапов</summary><pre>${escape(JSON.stringify(e.evaluations,null,2))}</pre></details>
    </details>`).join('') : '<p class="empty">Нет результатов по выбранным условиям. Это не означает отсутствие отклонений.</p>';
}
async function refresh() {
  if (!project) return;
  const pid = project;
  const [cameras,zones,imports,schedules,images,evaluations] = await Promise.all(
    ['cameras','zones','imports','schedules','images','evaluations'].map(p => api(`/api/projects/${pid}/${p}`)));
  if (project !== pid) return;
  imported = imports; imageItems = images; zoneItems = zones; evaluationItems = evaluations;
  select('zone-camera',cameras,c=>c.name); select('image-camera',cameras,c=>c.name);
  select('schedule-zone',zones,z=>z.name);
  $('cameras').textContent=cameras.map(c=>c.name).join(', ');
  $('zones').textContent=zones.map(z=>z.name).join(', ');
  select('import-select',imports,i=>`${i.sheet} · ${i.stages.length} строк · ${i.id.slice(0,8)}`);
  select('timeline-import',imports,i=>`${i.sheet} · ${i.stages.length} строк · ${i.id.slice(0,8)}`);
  select('timeline-zone',zones,z=>z.name);
  fillStages();
  $('schedules').innerHTML=schedules.map(s=>`<details><summary>Версия ${escape(s.id.slice(0,8))} · работ: ${s.works.length}</summary>${scheduleTable(s.works)}<p class="muted">${escape(s.resource_constraint)}</p></details>`).join('');
  $('image-list').innerHTML=images.length?images.map(i=>{
    const previous=evaluations.find(e=>e.image_id===i.id && e.mode==='model');
    return `<article class="image-card"><a href="${base()}/images/${i.id}/file" target="_blank" rel="noreferrer"><img src="${base()}/images/${i.id}/file" alt="${escape(i.filename)}"></a><p><b>${escape(i.filename)}</b><br>${escape(i.captured_at||'Время неизвестно')}<br>${i.time_source==='demonstration'?'Демонстрационное время · ':''}${i.quality_heuristic_ok?'Базовая проверка качества пройдена':'Качество требует проверки'}<br>${previous?escape(statusNames[previous.status]):'В последних результатах нет анализа этого снимка'}</p></article>`;
  }).join(''):'<p class="empty">Снимки ещё не загружены</p>';
  select('analysis-image', images, i=>`${i.filename} · ${i.captured_at || 'время неизвестно'}`);
  select('analysis-schedule', [{id:'',works:[]}, ...schedules], s=>s.id ? `${s.id.slice(0,8)} · работ: ${s.works.length}` : 'Без графика — обзор кадра');
  updateAnalysisSelection(); renderEvaluations();
}
$('analysis-image').onchange = updateAnalysisSelection;
$('analysis-schedule').onchange = updateAnalysisSelection;
$('analysis-zone').onchange = updateAnalysisSelection;
$('result-status').onchange = renderEvaluations;
$('result-mode').onchange = renderEvaluations;
$('result-scene').onchange = renderEvaluations;
$('analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (analysisBusy) return;
  const pid = project;
  const data = {image_id:$('analysis-image').value, schedule_id:$('analysis-schedule').value || null, zone_id:$('analysis-zone').value || null};
  analysisBusy = true; updateAnalysisSelection();
  $('analysis-state').textContent = 'Идёт распознавание. Результат появится в истории проверок.';
  try {
    const result = await post(`/api/projects/${pid}/evaluations/analyze`, data);
    if (project === pid) {
      await refresh();
      $('analysis-state').textContent = `Обзор кадра сохранён · сигналов для проверки: ${result.scene?.review_candidates.length || 0}. График: ${statusNames[result.status] || result.status}.`;
      message('Анализ сохранён. Откройте результат в истории для просмотра рамок и объяснения.');
    }
  } catch (error) {
    if (project === pid) {
      $('analysis-state').textContent = `Анализ не выполнен: ${error.message}`;
      message(error.message,true);
    }
  } finally {
    analysisBusy = false; updateAnalysisSelection();
  }
});
function fillStages(){const item=imported.find(i=>i.id===$('import-select').value);select('stage-select',(item?.stages||[]).filter(s=>s.kind==='work').map(s=>({...s,id:s.external_id})),s=>`Стр. ${s.source_row} · ${s.name}`);const pit=item?.stages.find(s=>s.source_row===72);if(pit)$('stage-select').value=pit.external_id;}
let timelineRevision = 0;
function clearTimeline() {
  timelineRevision += 1;
  timelinePreview = null;
  $('timeline-confirm').hidden = true;
  $('timeline-preview').innerHTML = '';
  $('timeline-accept').checked = false;
}
$('timeline-form').addEventListener('change', clearTimeline);
$('project-select').addEventListener('change', clearTimeline);
action('download-timeline', async () => {
  const iid = $('timeline-import').value, zid = $('timeline-zone').value;
  if (!iid || !zid) throw Error('Сначала сохраните перечень работ и зону');
  const response = await fetch(`${base()}/imports/${encodeURIComponent(iid)}/timeline-template?zone_id=${encodeURIComponent(zid)}`);
  if (!response.ok) throw Error('Не удалось получить шаблон');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url; link.download = 'timeline.csv'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
bind('timeline-form', async f => {
  clearTimeline();
  const revision = timelineRevision;
  const pid = project, iid = $('timeline-import').value;
  if (!iid) throw Error('Выберите сохранённый перечень работ');
  const body = new FormData(); body.set('file', f.get('file'));
  body.set('options', JSON.stringify({weekdays:f.get('calendar') === '5' ? [0,1,2,3,4] : [0,1,2,3,4,5,6],
    holidays:String(f.get('holidays') || '').split(',').map(x=>x.trim()).filter(Boolean)}));
  const result = await api(`/api/projects/${pid}/imports/${iid}/timeline-preview`, {method:'POST',body});
  if (project !== pid || $('timeline-import').value !== iid || timelineRevision !== revision) return;
  timelinePreview = result;
  $('timeline-preview').innerHTML = `<p>Работ со сроками: ${result.works.length} · часовой пояс: ${escape(result.timezone)}</p><div class="issues">${result.issues.map(i=>`<p>${i.severity === 'error' ? 'Ошибка' : 'Проверить'} · строка ${i.row || '—'}: ${escape(i.message)}</p>`).join('')}</div>${scheduleTable(result.works)}`;
  $('timeline-confirm').hidden = result.issues.some(i=>i.severity === 'error');
  const errors = result.issues.filter(i=>i.severity === 'error').length;
  message(errors ? `Ошибок в сроках: ${errors}. Исправьте файл и загрузите снова.` : 'Предпросмотр сроков готов. Проверьте даты, зоны и правила.', errors > 0);
});
action('save-timeline', async () => {
  if (!timelinePreview || !$('timeline-accept').checked) throw Error('Подтвердите проверку предпросмотра сроков');
  await post(`${base()}/schedules/confirm`, {preview_id:timelinePreview.id, accept_warnings:true});
  clearTimeline(); await refresh(); message('Загруженный график сохранён отдельной версией');
});
$('import-select').onchange=fillStages;
$('project-select').onchange=async()=>{project=$('project-select').value;preview=calculation=null;$('analysis-state').textContent='';$('upload-errors').textContent='';for(const id of ['import-preview','calculation-preview'])$(id).innerHTML='';$('import-confirm').hidden=$('save-schedule').hidden=true;try{await refresh();}catch(e){message(e.message,true);}};
bind('project-form',async f=>{const p=await post('/api/projects',{name:f.get('name'),timezone:f.get('timezone')});await projects();$('project-select').value=p.id;project=p.id;await refresh();message('Площадка создана');});
bind('camera-form',async f=>{await post(`${base()}/cameras`,{name:f.get('name')});await refresh();message('Камера добавлена');});
bind('zone-form',async f=>{await post(`${base()}/zones`,{name:f.get('name'),camera_id:$('zone-camera').value,polygon:JSON.parse(f.get('polygon')),coverage_confirmed:f.has('coverage')});await refresh();message('Зона сохранена');});
bind('import-form',async f=>{preview=null;$('import-confirm').hidden=true;$('accept-warnings').checked=false;const options={sheet:f.get('sheet')||null,recover_date_numbers:f.has('recover')};for(const key of ['header_row','id_column','name_column','parent_column','start_column','end_column'])options[key]=f.get(key)?Number(f.get(key)):null;const body=new FormData();body.set('file',f.get('file'));body.set('options',JSON.stringify(options));preview=await api(`${base()}/imports/preview`,{method:'POST',body});$('import-preview').innerHTML=`<p><b>${preview.stages.length} строк</b> · лист ${escape(preview.sheet)} · ${preview.issues.filter(i=>i.severity==='error').length} ошибок</p><div class="issues">${preview.issues.map(i=>`<div>${i.severity==='error'?'Ошибка':'Проверить'} · строка ${i.row??'—'}: ${escape(i.message)}</div>`).join('')}</div>${stagesTable(preview.stages)}`;$('import-confirm').hidden=preview.issues.some(i=>i.severity==='error');message('Предпросмотр готов; проверьте иерархию до сохранения');});
action('save-import',async()=>{await post(`${base()}/imports/confirm`,{preview_id:preview.id,accept_warnings:$('accept-warnings').checked});await refresh();$('import-confirm').hidden=true;message('Версия перечня сохранена');});
bind('schedule-form',async f=>{calculation=null;$('save-schedule').hidden=true;const anchor=f.get('anchor')||null;calculation=await post(`${base()}/imports/${$('import-select').value}/calculate`,{anchor,anchor_source:anchor?'demonstration':'unknown',weekdays:f.get('calendar')==='5'?[0,1,2,3,4]:[0,1,2,3,4,5,6],works:[{stage_id:$('stage-select').value,zone_id:$('schedule-zone').value,volume:Number(f.get('volume')),output_per_shift:Number(f.get('output')),machines:Number(f.get('machines')),shifts_per_day:Number(f.get('shifts')),source_ids:['pkti-62-04-2007'],assumptions:['Демонстрационные объём, выработка и ресурсы. Производительность не взята из технологической карты.','Технология выемки и вывоза грунта выбрана пользователем для демонстрации.'],provenance:'demonstration',rule:{version:'excavation-demo-v1',required:[['excavator'],['dump_truck']],allowed:[],confidence:0.5}}]});$('calculation-preview').innerHTML=scheduleTable(calculation.works);$('save-schedule').hidden=false;message('Расчёт готов к проверке');});
action('save-schedule',async()=>{await post(`${base()}/schedules/confirm`,{preview_id:calculation.id});await refresh();$('save-schedule').hidden=true;message('Версия графика сохранена');});
bind('image-form',async f=>{$('upload-errors').textContent='';f.set('camera_id',$('image-camera').value);const files=f.getAll('file');if(files.length>1){const batch=new FormData();batch.set('camera_id',$('image-camera').value);batch.set('manifest',JSON.stringify(Object.fromEntries(files.map(file=>[file.name,{captured_at:f.get('captured_at'),time_source:f.get('time_source')}]))));files.forEach(file=>batch.append('files',file));const result=await api(`${base()}/images/batch`,{method:'POST',body:batch});$('upload-errors').innerHTML=result.errors.map(e=>`<p>${escape(e.filename)}: ${escape(e.error)}</p>`).join('');await refresh();message(`Сохранено ${result.accepted.length} из ${result.total}; ошибок: ${result.errors.length}. Анализ запускается отдельно после загрузки.`,result.errors.length>0);return;}await api(`${base()}/images`,{method:'POST',body:f});await refresh();message('Снимок сохранён. Анализ запускается отдельно после загрузки.');});
async function init(){await loadDetectorStatus();const sources=await api('/api/sources');$('sources').innerHTML=sources.map(s=>`<details><summary>${escape(s.title)} · ${escape(s.edition)}</summary><p>${escape(s.facts)}</p><p>${escape(s.mapping.reason)}</p><p>${escape(s.limitations)}</p><a href="${escape(s.url)}" target="_blank" rel="noreferrer">Открыть источник</a></details>`).join('');await projects();}
// ?mock=1 works without a backend: only the status tab (status-view.js) runs, from mock-status.json.
if(new URLSearchParams(location.search).get('mock')==='1')$('detector-status').textContent='Демо-режим: настройка площадки недоступна без сервера.';
else init().catch(e=>message(e.message,true));
