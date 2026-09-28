// Almond Eye UI: objects list, «new object» wizard, object status page. Pure rendering lives in status.js.
import {escapeHtml} from './evidence.js';
import {renderVerdict, renderStagesTable, renderAlerts, renderViewer, ganttLayout, renderGanttLabels,
  renderGanttPlot, latestObservationDate, imageUrlMap, labelsFrom, capturedAtFor, upToDate, formatDate,
  formatRange, statusMeta, statusText} from './status.js';

const $ = id => document.getElementById(id);
const MOCK = new URLSearchParams(location.search).get('mock') === '1';
// Old anchors of the engineering console still land there.
const ADVANCED_ANCHORS = ['setup', 'import', 'schedule', 'images', 'history', 'view-setup'];
// Stages that exist in plans but cannot be judged from outside photos (stage_signatures.yaml: observable: false).
const NOT_VISIBLE = new Set(['interior', 'commissioning']);
const BATCH = 100;  // server limit per /images/batch request

const state = {projects:[], summaries:{}, pid:null, status:null, labels:labelsFrom(null), dateTouched:false,
  mock:null, requestSeq:0};

// ---------- helpers ----------
function note(text, error = false) {
  const el = $('message'); if (!el) return;
  el.textContent = text; el.className = error ? 'error' : '';
  clearTimeout(note.timer);
  if (text && !error) note.timer = setTimeout(() => { if (el.textContent === text) note(''); }, 6000);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!response.ok) {
    const detail = body?.detail ?? body;
    throw Error(typeof detail === 'string' ? detail || `Ошибка сервера (${response.status})` : JSON.stringify(detail));
  }
  return body;
}
const postJson = (path, data) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
const projectBase = pid => `/api/projects/${encodeURIComponent(pid)}`;
const projectById = pid => state.projects.find(p => p.id === pid);
const today = () => new Date().toISOString().slice(0, 10);
const plural = (n, one, few, many) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many}`;

async function loadMock() {
  if (!state.mock) state.mock = await api('/assets/mock-status.json');
  return state.mock;
}

function guardUpload() {
  if (MOCK) throw Error('В демо-режиме загрузка на сервер отключена.');
}

// ---------- routing ----------
function route() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [page, id] = hash.split('/');
  let view = 'objects';
  if (page === 'new') view = 'new';
  else if (page === 'object' && id) view = 'status';
  else if (page === 'advanced' || ADVANCED_ANCHORS.includes(page)) view = 'setup';
  for (const v of ['objects', 'new', 'status', 'setup']) $(`view-${v}`).hidden = v !== view;
  for (const a of document.querySelectorAll('[data-nav]')) a.setAttribute('aria-current', String(a.dataset.nav === view));
  window.scrollTo(0, 0);
  if (view === 'objects') showObjects().catch(e => note(e.message, true));
  if (view === 'new') startWizard();
  if (view === 'status') openObject(decodeURIComponent(id)).catch(e => note(e.message, true));
  if (view === 'setup' && page !== 'advanced' && page !== 'view-setup') document.getElementById(page)?.scrollIntoView();
  renderNav();
}

// ---------- projects & summaries ----------
async function loadProjects() {
  state.projects = (MOCK ? (await loadMock()).projects : await api('/api/projects')) || [];
  renderNav();
}

async function fetchStatus(pid, {date = null, planId = null} = {}) {
  if (MOCK) {
    const mock = await loadMock();
    if (pid !== mock.status.project_id) throw Error('Загрузите календарный план объекта');
    return date ? upToDate(mock.status, date) : mock.status;
  }
  const q = new URLSearchParams();
  if (date) q.set('date', date);
  if (planId) q.set('plan_id', planId);
  return api(`${projectBase(pid)}/status${q.toString() ? `?${q}` : ''}`);
}

// Status on the latest photo date: what a manager wants to see first.
async function latestStatus(pid) {
  let status = await fetchStatus(pid);
  const latest = latestObservationDate(status);
  if (latest && latest !== status.date) status = await fetchStatus(pid, {date: latest});
  return status;
}

async function summarize(pid) {
  try {
    const status = await latestStatus(pid);
    state.summaries[pid] = {status};
  } catch (e) {
    state.summaries[pid] = {error: /план/i.test(e.message) ? 'no_plan' : e.message};
  }
  return state.summaries[pid];
}

function renderNav() {
  const box = $('nav-projects'); if (!box) return;
  const current = location.hash.startsWith('#/object/') ? decodeURIComponent(location.hash.split('/')[2] || '') : null;
  box.innerHTML = state.projects.map(p => {
    const s = state.summaries[p.id]?.status;
    const tone = s ? statusMeta(s.verdict?.status).tone : 'none';
    return `<a href="#/object/${encodeURIComponent(p.id)}" aria-current="${p.id === current}"><i class="dot tone-${tone}"></i>${escapeHtml(p.name)}</a>`;
  }).join('');
}

// ---------- objects list ----------
async function showObjects() {
  const grid = $('objects-grid');
  grid.innerHTML = '<p class="muted">Загрузка…</p>';
  await loadProjects();
  if (!state.projects.length) {
    grid.innerHTML = `<div class="empty big"><h2>Объектов пока нет</h2><p>Создайте объект, загрузите календарный план и снимки с камер — система покажет, успевает ли стройка.</p><a class="button" href="#/new">+ Новый объект</a></div>`;
    return;
  }
  grid.innerHTML = state.projects.map(p => card(p, null)).join('');
  await Promise.all(state.projects.map(async p => {
    const summary = await summarize(p.id);
    const el = grid.querySelector(`[data-pid="${CSS.escape(p.id)}"]`);
    if (el) el.outerHTML = card(p, summary);
  }));
  renderNav();
}

function card(p, summary) {
  const href = `#/object/${encodeURIComponent(p.id)}`;
  if (!summary) return `<a class="obj-card loading" data-pid="${escapeHtml(p.id)}" href="${href}"><h3>${escapeHtml(p.name)}</h3><p class="muted">Считаем статус…</p></a>`;
  if (summary.error) {
    const text = summary.error === 'no_plan' ? 'План ещё не загружен' : `Статус недоступен: ${summary.error}`;
    return `<a class="obj-card tone-none" data-pid="${escapeHtml(p.id)}" href="${href}"><div class="obj-thumb empty-thumb">◉</div><div class="obj-body"><h3>${escapeHtml(p.name)}</h3><span class="pill tone-none">${escapeHtml(text)}</span></div></a>`;
  }
  const s = summary.status, m = statusMeta(s.verdict?.status);
  const obs = s.observations || [];
  const last = obs.reduce((a, o) => (!a || o.captured_at > a.captured_at ? o : a), null);
  const thumb = last?.image_url ? `<img src="${escapeHtml(last.image_url)}" alt="" loading="lazy">` : '◉';
  const facts = [obs.length ? plural(obs.length, 'снимок', 'снимка', 'снимков') : 'снимков нет',
    last ? `последний ${formatDate(last.captured_at)}` : null, s.alerts?.length ? plural(s.alerts.length, 'алерт', 'алерта', 'алертов') : null].filter(Boolean);
  return `<a class="obj-card tone-${m.tone}" data-pid="${escapeHtml(p.id)}" href="${href}">
<div class="obj-thumb${last ? '' : ' empty-thumb'}">${thumb}</div>
<div class="obj-body"><h3>${escapeHtml(p.name)}</h3>
<span class="pill tone-${m.tone}"><span aria-hidden="true">${m.icon}</span> ${escapeHtml(statusText(s.verdict?.status, s.verdict?.delay_days))}</span>
<p class="obj-summary">${escapeHtml(s.verdict?.summary || '')}</p><p class="muted small">${escapeHtml(facts.join(' · '))}</p></div></a>`;
}

// ---------- uploaders (used by the wizard and by modals) ----------
function dropzone({id, accept, multiple, title, hint}) {
  return `<label class="dropzone" for="${id}"><input type="file" id="${id}" accept="${accept}" ${multiple ? 'multiple' : ''}>
<span class="dz-icon" aria-hidden="true">⤒</span><strong>${escapeHtml(title)}</strong><span class="muted small">${escapeHtml(hint)}</span></label>`;
}

function wireDropzone(root, onFiles) {
  const zone = root.querySelector('.dropzone'), input = zone.querySelector('input');
  input.addEventListener('change', () => input.files.length && onFiles([...input.files]));
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('over'); if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]); });
}

function planUploader(root, pid, onSaved) {
  root.innerHTML = `<p class="muted">Файл Excel в формате «График производства работ»: строки — этапы, столбцы — месяцы или даты начала и окончания. Подойдёт и CSV.</p>
${dropzone({id:`plan-file-${Math.random().toString(36).slice(2)}`, accept:'.xlsx,.csv', title:'Перетащите план сюда или нажмите, чтобы выбрать', hint:'XLSX или CSV, до 10 МБ'})}
<p class="small"><a href="/assets/example-plan.xlsx" download="Пример календарного плана.xlsx">Скачать пример плана</a></p>
<div class="plan-result"></div>`;
  const result = root.querySelector('.plan-result');
  wireDropzone(root, async ([file]) => {
    result.innerHTML = '<p class="muted">Читаем план…</p>';
    try {
      guardUpload();
      const body = new FormData(); body.set('file', file);
      const preview = await api(`${projectBase(pid)}/plans/xlsx/preview`, {method:'POST', body});
      renderPlanPreview(result, preview, file.name, async () => {
        await postJson(`${projectBase(pid)}/plans/xlsx/confirm`, {preview_id: preview.id, accept_warnings: true});
        onSaved(preview);
      });
    } catch (e) { result.innerHTML = `<p class="text-bad">Не удалось прочитать план: ${escapeHtml(e.message)}</p>`; }
  });
}

function renderPlanPreview(root, preview, filename, save) {
  const stages = preview.stages || [], issues = preview.issues || [];
  const errors = issues.filter(i => i.severity === 'error'), warnings = issues.filter(i => i.severity !== 'error');
  const visible = stages.filter(s => s.stage_key && !NOT_VISIBLE.has(s.stage_key)).length;
  root.innerHTML = `<div class="plan-summary ${errors.length ? 'bad' : 'good'}">
<strong>${escapeHtml(filename)}</strong>: ${plural(stages.length, 'этап', 'этапа', 'этапов')}${preview.start ? `, ${escapeHtml(formatRange(preview.start, preview.end))}` : ''}.
${visible ? `По снимкам система отслеживает ${plural(visible, 'этап', 'этапа', 'этапов')}.` : 'Ни один этап не распознаётся по снимкам — проверьте названия.'}</div>
${errors.length ? `<ul class="issues bad">${errors.map(i => `<li>${i.row ? `Строка ${escapeHtml(i.row)}: ` : ''}${escapeHtml(i.message)}</li>`).join('')}</ul>` : ''}
${warnings.length ? `<details class="issues-box"><summary>Замечания: ${warnings.length}</summary><ul class="issues">${warnings.map(i => `<li>${i.row ? `Строка ${escapeHtml(i.row)}: ` : ''}${escapeHtml(i.message)}</li>`).join('')}</ul></details>` : ''}
<div class="table-wrap mini"><table><thead><tr><th>Этап</th><th>Сроки</th><th>По снимкам</th></tr></thead><tbody>${stages.map(s =>
  `<tr><td>${escapeHtml(s.name)}</td><td class="nowrap">${escapeHtml(formatRange(s.start, s.end))}</td><td>${s.stage_key && !NOT_VISIBLE.has(s.stage_key) ? '<span class="text-good">✓ отслеживается</span>' : '<span class="muted">не видно на фото</span>'}</td></tr>`).join('')}</tbody></table></div>
${errors.length ? '<p class="text-bad">Исправьте ошибки в файле и загрузите его снова.</p>' : '<button type="button" class="save-plan">Сохранить план</button>'}`;
  const button = root.querySelector('.save-plan');
  button?.addEventListener('click', async () => {
    button.disabled = true; button.textContent = 'Сохраняем…';
    try { await save(); } catch (e) { note(e.message, true); button.disabled = false; button.textContent = 'Сохранить план'; }
  });
}

function photoUploader(root, pid, onDone, {skip = null} = {}) {
  let files = [];
  const inputId = `photos-${Math.random().toString(36).slice(2)}`;
  root.innerHTML = `<p class="muted">Снимки с камер площадки. Дату съёмки укажите одну на всю пачку — по ней снимки ложатся на график.</p>
${dropzone({id:inputId, accept:'image/png,image/jpeg', multiple:true, title:'Перетащите снимки сюда или нажмите, чтобы выбрать', hint:'PNG или JPEG, до 10 МБ каждый'})}
<div class="photo-list"></div>
<div class="photo-actions"><label>Дата съёмки<input type="date" class="photo-date" value="${today()}" max="${today()}"></label>
<button type="button" class="upload-go" disabled>Загрузить и проанализировать</button>${skip ? '<button type="button" class="ghost skip">Пропустить</button>' : ''}</div>
<div class="progress-box" hidden><progress value="0" max="1"></progress><p class="small progress-text" role="status" aria-live="polite"></p></div>`;
  const list = root.querySelector('.photo-list'), go = root.querySelector('.upload-go');
  const render = () => {
    const shown = files.slice(0, 12);
    list.innerHTML = files.length ? `<div class="thumbs">${shown.map(f => `<img src="${URL.createObjectURL(f)}" alt="${escapeHtml(f.name)}" title="${escapeHtml(f.name)}">`).join('')}${files.length > shown.length ? `<span class="more">+${files.length - shown.length}</span>` : ''}</div>` : '';
    go.disabled = !files.length;
    go.textContent = files.length ? `Загрузить и проанализировать ${plural(files.length, 'снимок', 'снимка', 'снимков')}` : 'Загрузить и проанализировать';
  };
  wireDropzone(root, picked => { files = picked.filter(f => /image\/(png|jpeg)/.test(f.type)); render(); });
  root.querySelector('.skip')?.addEventListener('click', skip);
  go.addEventListener('click', async () => {
    const date = root.querySelector('.photo-date').value;
    if (!date) { note('Укажите дату съёмки', true); return; }
    go.disabled = true;
    const box = root.querySelector('.progress-box'), bar = box.querySelector('progress'), text = box.querySelector('.progress-text');
    const show = (done, total, message) => { box.hidden = false; bar.max = Math.max(1, total); bar.value = Math.min(done, bar.max); text.textContent = message; };
    try {
      guardUpload();
      const base = projectBase(pid);
      let cameras = await api(`${base}/cameras`);
      const camera = cameras?.[0]?.id || (await postJson(`${base}/cameras`, {name:'Камера 1'})).id;
      const capturedAt = capturedAtFor(date, projectById(pid)?.timezone || 'Europe/Moscow');
      let accepted = 0; const failed = [];
      for (let i = 0; i < files.length; i += BATCH) {
        const chunk = files.slice(i, i + BATCH), body = new FormData();
        body.set('camera_id', camera);
        body.set('manifest', JSON.stringify(Object.fromEntries(chunk.map(f => [f.name, {captured_at:capturedAt, time_source:'user'}]))));
        chunk.forEach(f => body.append('files', f));
        show(i, files.length, `Загружаем снимки: ${i} из ${files.length}`);
        const r = await api(`${base}/images/batch`, {method:'POST', body});
        accepted += r.accepted?.length || 0; failed.push(...(r.errors || []));
      }
      let job = await api(`${base}/analyze-all`, {method:'POST'});
      while (!['done', 'failed'].includes(job.status)) {
        show(job.done || 0, job.total || 1, `Ищем технику и определяем этапы: ${job.done || 0} из ${job.total}`);
        await new Promise(r => setTimeout(r, 1000));
        job = await api(`${base}/jobs/${encodeURIComponent(job.id)}`);
      }
      if (job.status === 'failed') throw Error(job.error || 'анализ прерван');
      const problems = failed.length + (job.errors?.length || 0);
      show(1, 1, `Готово: загружено ${accepted}, проанализировано ${job.done ?? job.total}${problems ? `, не удалось: ${problems}` : ''}`);
      onDone({accepted, problems});
    } catch (e) { text.textContent = ''; note(`Не получилось: ${e.message}`, true); go.disabled = false; }
  });
}

// ---------- wizard ----------
function wizardStep(n) {
  for (const li of document.querySelectorAll('#wizard-steps li')) {
    const s = Number(li.dataset.step);
    li.className = s < n ? 'done' : s === n ? 'current' : '';
  }
  for (const sec of document.querySelectorAll('.wizard-step')) sec.hidden = Number(sec.dataset.step) !== n;
}

function startWizard() {
  const form = $('wizard-name');
  form.reset(); wizardStep(1);
  setTimeout(() => form.name.focus(), 50);
  form.onsubmit = async e => {
    e.preventDefault();
    const button = form.querySelector('button'); button.disabled = true;
    try {
      guardUpload();
      const project = await postJson('/api/projects', {name: form.name.value.trim(), timezone: 'Europe/Moscow'});
      await loadProjects();
      wizardStep(2);
      planUploader($('wizard-plan'), project.id, () => {
        wizardStep(3);
        photoUploader($('wizard-photos'), project.id, () => { location.hash = `#/object/${encodeURIComponent(project.id)}`; },
          {skip: () => { location.hash = `#/object/${encodeURIComponent(project.id)}`; }});
      });
    } catch (err) { note(err.message, true); } finally { button.disabled = false; }
  };
}

// ---------- modal ----------
function openModal(title, fill) {
  const dialog = $('modal');
  $('modal-title').textContent = title;
  fill($('modal-body'));
  if (!dialog.open) dialog.showModal();
}
const closeModal = () => $('modal').close();

// ---------- object page ----------
async function openObject(pid) {
  if (!state.projects.length) await loadProjects();
  const changed = state.pid !== pid;
  state.pid = pid;
  if (changed) { state.dateTouched = false; state.status = null; }
  const p = projectById(pid);
  $('st-title').textContent = p?.name || 'Объект';
  await loadPlans();
  await loadStatus();
}

async function loadPlans() {
  const sel = $('st-plan');
  let plans = [];
  if (!MOCK) { try { plans = await api(`${projectBase(state.pid)}/plans`) || []; } catch { plans = []; } }
  sel.innerHTML = '<option value="">Актуальный</option>' + plans.map(p =>
    `<option value="${escapeHtml(p.id)}">${escapeHtml(p.filename || p.id.slice(0, 8))}${p.created_at ? ` · ${escapeHtml(formatDate(p.created_at))}` : ''}</option>`).join('');
  sel.closest('label').hidden = plans.length < 2;
  state.planCount = plans.length;
}

async function loadStatus() {
  const seq = ++state.requestSeq, pid = state.pid;
  $('st-dashboard').setAttribute('aria-busy', 'true');
  try {
    const opts = {planId: $('st-plan').value || null};
    const status = state.dateTouched ? await fetchStatus(pid, {...opts, date: $('st-date').value}) : await latestStatus(pid);
    if (seq !== state.requestSeq) return;
    state.status = status;
    state.summaries[pid] = {status};
    $('st-date').value = status?.date || '';
    renderAll();
  } catch (e) {
    if (seq !== state.requestSeq) return;
    state.status = null;
    renderEmpty(/план/i.test(e.message) ? 'no_plan' : e.message);
  } finally {
    if (seq === state.requestSeq) $('st-dashboard').removeAttribute('aria-busy');
    renderNav();
  }
}

function renderEmpty(reason) {
  $('st-subtitle').textContent = '';
  const noPlan = reason === 'no_plan';
  $('st-verdict').innerHTML = `<div class="empty big"><h2>${noPlan ? 'Загрузите календарный план' : 'Статус недоступен'}</h2>
<p>${noPlan ? 'Без плана не с чем сравнивать снимки. После загрузки плана добавьте снимки с камер.' : escapeHtml(reason)}</p>
${noPlan ? '<button type="button" id="empty-plan">Загрузить план</button>' : ''}</div>`;
  $('empty-plan')?.addEventListener('click', openPlanModal);
  for (const id of ['st-alerts', 'st-stages', 'st-gantt']) $(id).innerHTML = '';
  $('st-alert-count').textContent = '';
  document.querySelector('.st-alerts').hidden = true;
}

const stageNames = () => Object.fromEntries((state.status?.stages || []).map(s => [s.stage_key, s.name]));

function renderAll() {
  const s = state.status;
  const obs = s.observations?.length || 0;
  $('st-subtitle').textContent = [`${plural(s.stages?.length || 0, 'этап', 'этапа', 'этапов')} в плане`,
    obs ? plural(obs, 'снимок', 'снимка', 'снимков') : 'снимков пока нет'].join(' · ');
  $('st-verdict').innerHTML = renderVerdict(s) + stageTiles(s) + (obs ? '' : `<div class="empty"><p>Снимков пока нет — добавьте их, чтобы сравнить факт с планом.</p><button type="button" id="empty-photos">+ Добавить снимки</button></div>`);
  $('empty-photos')?.addEventListener('click', openPhotosModal);
  const urlFor = imageUrlMap(s, state.pid);
  $('st-stages').innerHTML = renderStagesTable(s);
  const alerts = s.alerts?.length || 0;
  document.querySelector('.st-alerts').hidden = false;
  $('st-alerts').innerHTML = alerts ? renderAlerts(s, urlFor) : '<p class="muted">Отклонений не найдено.</p>';
  $('st-alert-count').textContent = alerts ? String(alerts) : '';
  renderGantt();
}

// Stage counts under the verdict: the «how bad is it» overview before the details.
function stageTiles(s) {
  const count = (...statuses) => (s.stages || []).filter(x => statuses.includes(x.status)).length;
  const tiles = [['behind', 'Отстают', count('behind')], ['risk', 'Под риском', count('at_risk')],
    ['ok', 'Идут по графику', count('on_track', 'ahead')], ['none', 'Завершены', count('done')],
    ['none', 'Ещё не начаты', count('not_started')]];
  return `<div class="tiles">${tiles.map(([tone, label, n]) =>
    `<div class="tile tone-${tone}${n ? '' : ' zero'}"><strong>${n}</strong><span>${label}</span></div>`).join('')}</div>`;
}

function renderGantt() {
  const box = $('st-gantt'), s = state.status;
  if (!box || $('view-status').hidden || !s) return;
  if (!s.stages?.length && !s.observations?.length) { box.innerHTML = ''; return; }
  const width = box.clientWidth || 800;
  const labelWidth = width < 640 ? 150 : 260;
  const layout = ganttLayout(s, {plotWidth: width - labelWidth - 2, rowHeight: 30});
  const scroller = box.querySelector('.gantt-scroll'), keep = scroller ? scroller.scrollLeft : null;
  box.innerHTML = `${renderGanttLabels(layout, {labelWidth})}<div class="gantt-scroll" tabindex="0" aria-label="Диаграмма, прокручивается по горизонтали">${renderGanttPlot(layout, {stageNames: stageNames()})}</div>`;
  const sc = box.querySelector('.gantt-scroll');
  if (keep !== null) sc.scrollLeft = keep;
  else if (layout.today !== null && layout.width > sc.clientWidth) sc.scrollLeft = Math.max(0, layout.today - sc.clientWidth * 0.7);
}

async function afterUpload() {
  closeModal();
  state.dateTouched = false;
  await loadPlans();
  await loadStatus();
}

function openPhotosModal() {
  openModal('Добавить снимки', body => photoUploader(body, state.pid, async ({accepted}) => {
    await afterUpload(); note(`Снимки добавлены (${accepted}), статус пересчитан.`);
  }));
}

function openPlanModal() {
  openModal('Календарный план', body => planUploader(body, state.pid, async () => {
    await afterUpload(); note('План сохранён, статус пересчитан.');
  }));
}

// ---------- evidence viewer ----------
async function openViewer(imageId) {
  const s = state.status, dialog = $('st-viewer');
  const observation = s?.observations?.find(o => o.image_id === imageId) || {image_id:imageId};
  const imageUrl = imageUrlMap(s, state.pid)(imageId);
  $('st-viewer-title').textContent = `Снимок от ${formatDate(observation.captured_at)}`;
  $('st-viewer-body').innerHTML = '<p class="muted">Загрузка…</p>';
  if (!dialog.open) dialog.showModal();
  let analysis = null, error = null;
  try {
    analysis = MOCK ? (await loadMock()).analyses[imageId] : await api(`${projectBase(state.pid)}/images/${encodeURIComponent(imageId)}/analysis`);
    if (!analysis) error = 'снимок ещё не проанализирован';
  } catch (e) { error = e.message; }
  $('st-viewer-body').innerHTML = renderViewer({observation, analysis, imageUrl, labels:state.labels, stageNames:stageNames(), error});
}

function onEvidenceClick(event) {
  const target = event.target.closest('[data-image-id]');
  if (target) { event.preventDefault(); openViewer(target.dataset.imageId); }
}

// ---------- init ----------
async function init() {
  if (MOCK) $('st-mock').hidden = false;
  window.addEventListener('hashchange', route);
  $('st-date').addEventListener('change', () => { state.dateTouched = !!$('st-date').value; loadStatus(); });
  $('st-plan').addEventListener('change', () => loadStatus());
  $('st-controls').addEventListener('submit', e => e.preventDefault());
  $('st-add-photos').addEventListener('click', openPhotosModal);
  $('st-replace-plan').addEventListener('click', openPlanModal);
  for (const id of ['st-gantt', 'st-alerts']) $(id).addEventListener('click', onEvidenceClick);
  $('st-gantt').addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.dataset?.imageId) onEvidenceClick(e); });
  $('st-viewer-close').addEventListener('click', () => $('st-viewer').close());
  for (const d of ['st-viewer', 'modal']) $(d).addEventListener('click', e => { if (e.target === $(d)) $(d).close(); });
  $('modal').querySelector('[data-close]').addEventListener('click', closeModal);
  let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(renderGantt, 120); });
  if (!MOCK) api('/api/equipment').then(r => { state.labels = labelsFrom(r); }).catch(() => {});
  await loadProjects().catch(e => note(e.message, true));
  route();
  // Nav dots need verdicts even when the user lands directly on an object page.
  if (!location.hash.startsWith('#/objects') && location.hash) Promise.all(state.projects.map(p => state.summaries[p.id] || summarize(p.id))).then(renderNav);
}

init().catch(e => note(e.message, true));
