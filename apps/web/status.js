// Pure helpers for the «Статус объекта» tab: no DOM access, importable from node tests.
import {escapeHtml, equipmentNames, materialNames, renderBoxOverlay} from './evidence.js';

export const STATUS_META = {
  behind:   {label:'Отстаём',     tone:'behind',  icon:'▼'},
  at_risk:  {label:'Риск',        tone:'risk',    icon:'!'},
  on_track: {label:'В графике',   tone:'ok',      icon:'✓'},
  ahead:    {label:'Опережаем',   tone:'ahead',   icon:'▲'},
  no_data:  {label:'Нет данных',  tone:'none',    icon:'—'},
  done:     {label:'Завершён',    tone:'ok',      icon:'✓'},
  not_started: {label:'Не начат', tone:'none',    icon:'○'},
};
export const statusMeta = status => STATUS_META[status] || {label: status ? String(status) : 'Нет данных', tone:'none', icon:'—'};

export const SEVERITY_META = {
  critical:{label:'Критично', tone:'behind'}, high:{label:'Высокая', tone:'behind'},
  medium:{label:'Средняя', tone:'risk'}, warning:{label:'Средняя', tone:'risk'},
  low:{label:'Низкая', tone:'ahead'}, info:{label:'Инфо', tone:'none'},
};
export const severityMeta = s => SEVERITY_META[s] || {label: s || 'Инфо', tone:'none'};
export const ALERT_KINDS = {
  behind:'Отставание', at_risk:'Риск темпа', ahead:'Опережение', no_data:'Нет данных',
  unexpected_equipment:'Техника не по этапу', missing_equipment:'Нет обязательной техники',
};

export const PLANNED_STATE = {
  should_be_done:'по плану завершён', in_progress:'по плану идёт', should_be_active:'по плану идёт',
  not_started:'по плану не начат', planned:'по плану не начат', future:'по плану не начат',
};

// «Отстаём на 34 дн.» — the delay is part of the verdict text, so status never relies on color alone.
export function statusText(status, delayDays) {
  const n = Math.abs(Number(delayDays) || 0);
  if (status === 'behind') return n ? `Отстаём на ${n} дн.` : 'Отстаём';
  if (status === 'ahead') return n ? `Опережаем на ${n} дн.` : 'Опережаем';
  return statusMeta(status).label;
}

export function delayText(status, delayDays) {
  const n = Math.abs(Number(delayDays) || 0);
  if (!n) return '—';
  if (status === 'ahead') return `−${n} дн.`;
  return `+${n} дн.`;
}

// ---- dates: calendar days as integers (UTC), so no local-timezone drift ----
export function dayNumber(iso) {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!m) return null;
  return Date.UTC(+m[1], +m[2]-1, +m[3]) / 864e5;
}
export const dayIso = n => new Date(n * 864e5).toISOString().slice(0, 10);
export function formatDate(iso, short = false) {
  const n = dayNumber(iso);
  if (n === null) return '—';
  const [y, m, d] = dayIso(n).split('-');
  return short ? `${d}.${m}` : `${d}.${m}.${y}`;
}
export function formatRange(a, b) {
  if (!a && !b) return '—';
  if (a === b || !b) return formatDate(a);
  return `${formatDate(a)} – ${formatDate(b)}`;
}
const MONTHS = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];

// Offset such as "+03:00" of an IANA zone on a given calendar date (at noon, away from DST switches).
export function tzOffset(dateIso, timeZone) {
  const n = dayNumber(dateIso);
  if (n === null) throw Error('Некорректная дата');
  const instant = n * 864e5 + 12 * 36e5;
  let minutes = 0;
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone, hourCycle:'h23',
      year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'})
      .formatToParts(new Date(instant)).map(p => [p.type, p.value]));
    const local = Date.UTC(+parts.year, +parts.month-1, +parts.day, +parts.hour % 24, +parts.minute);
    minutes = Math.round((local - instant) / 6e4);
  } catch { minutes = 0; }
  const sign = minutes < 0 ? '-' : '+', abs = Math.abs(minutes);
  return `${sign}${String(Math.floor(abs/60)).padStart(2,'0')}:${String(abs%60).padStart(2,'0')}`;
}
// captured_at for a batch whose date was set at upload: noon on that day in the site's zone.
export const capturedAtFor = (dateIso, timeZone, time = '12:00') =>
  `${dayIso(dayNumber(dateIso))}T${time}:00${tzOffset(dateIso, timeZone)}`;

export function latestObservationDate(status) {
  const days = (status?.observations || []).map(o => dayNumber(o.captured_at)).filter(n => n !== null);
  return days.length ? dayIso(Math.max(...days)) : null;
}

export function upToDate(status, dateIso) {
  // Mock mode only: emulate the server's "observations ≤ D" filter.
  const limit = dayNumber(dateIso);
  if (!status || limit === null) return status;
  return {...status, date: dateIso,
    observations: (status.observations || []).filter(o => dayNumber(o.captured_at) <= limit)};
}

// ---- scales & gantt layout ----
export function makeScale(d0, d1, x0, x1) {
  const span = Math.max(1, d1 - d0);
  const f = day => x0 + (day - d0) * (x1 - x0) / span;
  f.invert = x => d0 + (x - x0) * span / (x1 - x0);
  return f;
}

const monthStart = n => { const d = new Date(n * 864e5); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 864e5; };
const nextMonth = n => { const d = new Date(n * 864e5); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) / 864e5; };

// Whole months covering the plan, observations and the selected date.
export function ganttDomain(status) {
  const days = [];
  for (const s of status?.stages || [])
    for (const k of ['planned_start','planned_end','observed_first','observed_last']) days.push(dayNumber(s[k]));
  for (const o of status?.observations || []) days.push(dayNumber(o.captured_at));
  days.push(dayNumber(status?.date));
  const valid = days.filter(n => n !== null);
  if (!valid.length) { const t = Math.floor(Date.now() / 864e5); return [monthStart(t), nextMonth(t)]; }
  return [monthStart(Math.min(...valid)), nextMonth(Math.max(...valid))];
}

export function monthTicks(d0, d1) {
  const ticks = [];
  for (let n = monthStart(d0); n < d1; n = nextMonth(n)) {
    if (n < d0) continue;
    const d = new Date(n * 864e5);
    ticks.push({day:n, month:d.getUTCMonth(), year:d.getUTCFullYear(),
      label: MONTHS[d.getUTCMonth()] + (d.getUTCMonth() === 0 || !ticks.length ? ` ${d.getUTCFullYear()}` : '')});
  }
  return ticks;
}

export function scoreLevel(score) {
  const s = Number(score) || 0;
  return s >= 0.7 ? 'hi' : s >= 0.4 ? 'mid' : 'lo';
}

export const UNMATCHED = '__unmatched__';

export function ganttLayout(status, {plotWidth = 800, rowHeight = 30, axisHeight = 28, minDayWidth = 2.4, pad = 8} = {}) {
  const [d0, d1] = ganttDomain(status);
  const days = d1 - d0;
  const width = Math.max(plotWidth, Math.ceil(days * minDayWidth) + pad * 2);
  const x = makeScale(d0, d1, pad, width - pad);
  const dayW = (width - pad * 2) / Math.max(1, days);
  const rows = (status?.stages || []).map(s => ({key:s.stage_key, name:s.name || s.stage_key, status:s.status,
    delay:s.delay_days, stage:s}));
  const index = new Map(rows.map((r, i) => [r.key, i]));
  const obs = status?.observations || [];
  if (obs.some(o => !index.has(o.top_stage))) {
    index.set(UNMATCHED, rows.length);
    rows.push({key:UNMATCHED, name:'Этап не определён', status:'no_data', delay:null, stage:null});
  }
  rows.forEach((r, i) => {
    r.y = axisHeight + i * rowHeight; r.cy = r.y + rowHeight / 2;
    const s = r.stage, ps = dayNumber(s?.planned_start), pe = dayNumber(s?.planned_end);
    r.plan = ps !== null && pe !== null ? {x:x(ps), w:Math.max(2, x(pe + 1) - x(ps))} : null;
    const fs = dayNumber(s?.observed_first), fe = dayNumber(s?.observed_last);
    r.fact = fs !== null && fe !== null ? {x:x(fs), w:Math.max(2, x(fe + 1) - x(fs))} : null;
  });
  const seen = new Map();
  const dots = obs.map(o => {
    const day = dayNumber(o.captured_at);
    if (day === null) return null;
    const ri = index.has(o.top_stage) ? index.get(o.top_stage) : index.get(UNMATCHED);
    const key = `${ri}:${day}`, k = seen.get(key) || 0; seen.set(key, k + 1);
    const shift = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 5;
    return {x:x(day) + dayW / 2 + shift, y:rows[ri].cy, level:scoreLevel(o.top_score), obs:o, row:ri};
  }).filter(Boolean);
  const sel = dayNumber(status?.date);
  return {d0, d1, width, height: axisHeight + rows.length * rowHeight + 4, axisHeight, rowHeight, rows, dots,
    today: sel === null ? null : x(sel) + dayW / 2,
    ticks: monthTicks(d0, d1).map(t => ({...t, x:x(t.day)}))};
}

const truncate = (s, n) => (s.length > n ? s.slice(0, Math.max(1, n - 1)) + '…' : s);

export function renderGanttLabels(layout, {labelWidth = 220} = {}) {
  const maxChars = Math.floor((labelWidth - 70) / 6.6);
  const rows = layout.rows.map((r, i) => {
    const m = statusMeta(r.status), badge = delayText(r.status, r.delay);
    return `<g class="g-row tone-${m.tone}">${i % 2 ? '' : `<rect class="g-band" x="0" y="${r.y}" width="${labelWidth}" height="${layout.rowHeight}"/>`}
<title>${escapeHtml(`${r.name} · ${statusText(r.status, r.delay)}`)}</title>
<text class="g-label" x="8" y="${r.cy + 4}">${escapeHtml(truncate(r.name, maxChars))}</text>
${badge !== '—' ? `<rect class="g-badge" x="${labelWidth - 58}" y="${r.cy - 9}" width="52" height="18" rx="9"/><text class="g-badge-text" x="${labelWidth - 32}" y="${r.cy + 4}" text-anchor="middle">${badge}</text>`
  : `<text class="g-icon" x="${labelWidth - 14}" y="${r.cy + 4}" text-anchor="middle">${m.icon}</text>`}</g>`;
  }).join('');
  return `<svg class="gantt-labels" width="${labelWidth}" height="${layout.height}" viewBox="0 0 ${labelWidth} ${layout.height}" role="presentation">
<text class="g-axis-title" x="8" y="18">Этап</text>${rows}</svg>`;
}

export function renderGanttPlot(layout, {stageNames = {}} = {}) {
  const {width, height, axisHeight, rowHeight} = layout;
  const bands = layout.rows.map((r, i) => i % 2 ? '' : `<rect class="g-band" x="0" y="${r.y}" width="${width}" height="${rowHeight}"/>`).join('');
  const grid = layout.ticks.map(t => `<line class="g-grid" x1="${t.x.toFixed(1)}" x2="${t.x.toFixed(1)}" y1="${axisHeight - 6}" y2="${height}"/>
<text class="g-tick" x="${(t.x + 4).toFixed(1)}" y="18">${escapeHtml(t.label)}</text>`).join('');
  const bars = layout.rows.map(r => {
    const m = statusMeta(r.status), out = [];
    if (r.plan) out.push(`<rect class="g-plan tone-${m.tone}" x="${r.plan.x.toFixed(1)}" y="${r.cy - 7}" width="${r.plan.w.toFixed(1)}" height="14" rx="4"><title>${escapeHtml(`План: ${r.name}, ${formatRange(r.stage.planned_start, r.stage.planned_end)}`)}</title></rect>`);
    if (r.fact) out.push(`<rect class="g-fact" x="${r.fact.x.toFixed(1)}" y="${r.cy - 1.5}" width="${r.fact.w.toFixed(1)}" height="3" rx="1.5"><title>${escapeHtml(`Факт: ${formatRange(r.stage.observed_first, r.stage.observed_last)}`)}</title></rect>`);
    return out.join('');
  }).join('');
  const today = layout.today === null ? '' : `<line class="g-today" x1="${layout.today.toFixed(1)}" x2="${layout.today.toFixed(1)}" y1="${axisHeight - 4}" y2="${height}"/>`;
  const dots = layout.dots.map(d => {
    const o = d.obs, name = stageNames[o.top_stage] || o.top_stage || 'этап не определён';
    const label = `${formatDate(o.captured_at)} · ${name} · ${Math.round((o.top_score || 0) * 100)}%`;
    return `<circle class="g-dot lvl-${d.level}" cx="${d.x.toFixed(1)}" cy="${d.y}" r="5.5" tabindex="0" role="button" data-image-id="${escapeHtml(o.image_id)}" aria-label="${escapeHtml('Открыть снимок: ' + label)}"><title>${escapeHtml(label)}</title></circle>`;
  }).join('');
  return `<svg class="gantt-plot" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Диаграмма Ганта: план и наблюдения">
${bands}${grid}${bars}${today}${dots}</svg>`;
}

export function renderVerdict(status) {
  const v = status?.verdict || {status:'no_data'};
  const m = statusMeta(v.status);
  const stages = status?.stages || [];
  const count = t => stages.filter(s => s.status === t).length;
  const facts = [
    `на ${formatDate(status?.date)}`,
    `этапов: ${stages.length}`,
    count('behind') && `отстают: ${count('behind')}`,
    count('at_risk') && `под риском: ${count('at_risk')}`,
    `снимков: ${(status?.observations || []).length}`,
  ].filter(Boolean).join(' · ');
  return `<div class="verdict tone-${m.tone}" role="status">
  <div class="verdict-main"><span class="verdict-icon" aria-hidden="true">${m.icon}</span><strong>${escapeHtml(statusText(v.status, v.delay_days))}</strong></div>
  <div class="verdict-text"><p>${escapeHtml(v.summary || (v.status === 'no_data' ? 'Недостаточно снимков или нет плана для сравнения.' : ''))}</p><small>${escapeHtml(facts)}</small></div>
</div>`;
}

export function statusPill(status, delay) {
  const m = statusMeta(status);
  return `<span class="pill tone-${m.tone}"><span aria-hidden="true">${m.icon}</span> ${escapeHtml(m.label)}</span>`;
}

export function renderStagesTable(status) {
  const stages = status?.stages || [];
  if (!stages.length) return '<p class="empty">В плане нет этапов. Загрузите календарный план XLSX.</p>';
  return `<div class="table-wrap status-table"><table><thead><tr><th>Этап</th><th>План</th><th>Факт</th><th class="num">Снимков</th><th>Статус</th><th class="num">Отклонение</th><th>Пояснение</th></tr></thead><tbody>
${stages.map(s => `<tr><td><b>${escapeHtml(s.name || s.stage_key)}</b>${s.planned_state ? `<br><small class="muted">${escapeHtml(PLANNED_STATE[s.planned_state] || s.planned_state)}</small>` : ''}</td>
<td class="nowrap">${escapeHtml(formatRange(s.planned_start, s.planned_end))}</td>
<td class="nowrap">${escapeHtml(formatRange(s.observed_first, s.observed_last))}</td>
<td class="num">${escapeHtml(s.observations ?? 0)}</td>
<td>${statusPill(s.status)}</td>
<td class="num nowrap">${escapeHtml(delayText(s.status, s.delay_days))}</td>
<td class="explain">${escapeHtml(s.explanation || '')}</td></tr>`).join('')}
</tbody></table></div>`;
}

export function imageUrlMap(status, pid) {
  const map = {};
  for (const o of status?.observations || []) map[o.image_id] = o.image_url;
  return id => map[id] || `/api/projects/${encodeURIComponent(pid ?? status?.project_id ?? '')}/images/${encodeURIComponent(id)}/file`;
}

export function renderAlerts(status, urlFor, {maxThumbs = 6} = {}) {
  const alerts = status?.alerts || [];
  if (!alerts.length) return '<p class="empty">Алертов нет. Это не подтверждает отсутствие отклонений, если снимков мало.</p>';
  const stageName = Object.fromEntries((status.stages || []).map(s => [s.stage_key, s.name]));
  return `<ul class="alerts">${alerts.map(a => {
    const sev = severityMeta(a.severity), ids = a.image_ids || [];
    const thumbs = ids.slice(0, maxThumbs).map(id => `<button type="button" class="thumb" data-image-id="${escapeHtml(id)}" aria-label="Открыть снимок-доказательство"><img src="${escapeHtml(urlFor(id))}" alt="" loading="lazy"></button>`).join('');
    return `<li class="alert tone-${sev.tone}"><div class="alert-head"><span class="pill tone-${sev.tone}">${escapeHtml(sev.label)}</span>
<span class="alert-kind">${escapeHtml(ALERT_KINDS[a.kind] || a.kind || '')}</span>${a.stage_key ? `<span class="muted">· ${escapeHtml(stageName[a.stage_key] || a.stage_key)}</span>` : ''}</div>
<p>${escapeHtml(a.message)}</p>${ids.length ? `<div class="thumbs">${thumbs}${ids.length > maxThumbs ? `<span class="more">+${ids.length - maxThumbs}</span>` : ''}</div>` : ''}</li>`;
  }).join('')}</ul>`;
}

// labels: {equipment_or_material_id: 'Русское название'}
export function labelsFrom(equipmentResponse) {
  const labels = {...equipmentNames, ...materialNames};
  const add = (id, name) => { if (id && name) labels[String(id).replace(/^material:/, '')] = name; };
  const walk = v => {
    if (Array.isArray(v)) v.forEach(x => add(x.id ?? x.key ?? x.value, x.name ?? x.label));
    else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) {
      if (typeof x === 'string') add(k, x); else walk(x);
    }
  };
  walk(equipmentResponse);
  return labels;
}

export function analysisBoxes(analysis, labels = {}) {
  const name = id => labels[String(id).replace(/^material:/, '')] || id;
  return [
    ...(analysis?.detections || []).map(d => ({bbox:d.bbox, confidence:d.confidence, kind:'equipment', label:name(d.equipment ?? d.label)})),
    ...(analysis?.materials || []).filter(m => m.bbox).map(m => ({bbox:m.bbox, confidence:m.confidence, kind:'material', label:name(m.material)})),
  ];
}

export function renderViewer({observation, analysis, imageUrl, labels = {}, stageNames = {}, error = null}) {
  const boxes = analysisBoxes(analysis, labels);
  const ranked = (analysis?.stages?.length ? analysis.stages : observation?.stages || [])
    .slice().sort((a, b) => (b.score || 0) - (a.score || 0));
  const top = analysis?.top_stage?.stage_key ?? analysis?.top_stage ?? observation?.top_stage;
  const name = k => stageNames[k] || k;
  const equipment = boxes.length ? boxes.map(b => `<li><span class="swatch swatch-${b.kind}"></span>${escapeHtml(b.label)} · ${Math.round((b.confidence || 0) * 100)}%</li>`).join('')
    : Object.entries(observation?.equipment || {}).map(([k, n]) => `<li>${escapeHtml(labels[k] || k)}: ${escapeHtml(n)}</li>`).join('');
  return `<div class="viewer-grid">
<figure class="viewer-photo"><div class="evidence-frame boxed"><img src="${escapeHtml(imageUrl)}" alt="Снимок-доказательство">${renderBoxOverlay(boxes)}</div>
<figcaption>${escapeHtml(formatDate(observation?.captured_at || analysis?.captured_at))} · <span class="swatch swatch-equipment"></span>техника <span class="swatch swatch-material"></span>материалы · <a href="${escapeHtml(imageUrl)}" target="_blank" rel="noreferrer">исходный снимок</a></figcaption></figure>
<div class="viewer-side">
${error ? `<p class="notice">Разметка недоступна: ${escapeHtml(error)}</p>` : ''}
<h3>Определённые этапы</h3>
${ranked.length ? `<ol class="ranked">${ranked.map(s => `<li class="${s.stage_key === top ? 'top' : ''}"><span>${escapeHtml(name(s.stage_key))}</span><span class="bar"><i style="width:${Math.round(Math.min(1, s.score || 0) * 100)}%"></i></span><b>${(s.score || 0).toFixed(2)}</b>${s.explanation ? `<small>${escapeHtml(s.explanation)}</small>` : ''}</li>`).join('')}</ol>` : '<p class="muted">Этап по снимку не определён.</p>'}
<h3>Объекты в кадре</h3>
${equipment ? `<ul class="objects">${equipment}</ul>` : '<p class="muted">Уверенных детекций нет. Это не доказательство отсутствия техники.</p>'}
</div></div>`;
}
