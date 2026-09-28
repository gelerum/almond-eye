import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {statusText, statusMeta, delayText, dayNumber, dayIso, formatDate, makeScale, ganttDomain, monthTicks,
  ganttLayout, renderGanttLabels, renderGanttPlot, scoreLevel, latestObservationDate, tzOffset, capturedAtFor,
  renderVerdict, renderStagesTable, renderAlerts, imageUrlMap, labelsFrom, analysisBoxes, renderViewer, upToDate,
  UNMATCHED} from '../../apps/web/status.js';
import {renderBoxOverlay} from '../../apps/web/evidence.js';

const mock = JSON.parse(readFileSync(new URL('../../apps/web/mock-status.json', import.meta.url)));
const status = mock.status;

test('status → label and tone mapping', () => {
  assert.equal(statusText('behind', 34), 'Отстаём на 34 дн.');
  assert.equal(statusText('ahead', -6), 'Опережаем на 6 дн.');
  assert.equal(statusText('at_risk', 0), 'Риск');
  assert.equal(statusText('on_track'), 'В графике');
  assert.equal(statusText('no_data'), 'Нет данных');
  assert.equal(statusMeta('behind').tone, 'behind');
  assert.equal(statusMeta('ahead').tone, 'ahead');
  assert.equal(statusMeta('whatever').tone, 'none');
  assert.equal(delayText('behind', 25), '+25 дн.');
  assert.equal(delayText('ahead', 6), '−6 дн.');
  assert.equal(delayText('on_track', 0), '—');
});

test('calendar-day helpers ignore time and zone suffixes', () => {
  assert.equal(dayIso(dayNumber('2024-06-25T23:30:00+03:00')), '2024-06-25');
  assert.equal(dayNumber('2024-03-01') - dayNumber('2024-02-28'), 2);
  assert.equal(dayNumber('garbage'), null);
  assert.equal(formatDate('2024-06-25'), '25.06.2024');
  assert.equal(formatDate('2024-06-25', true), '25.06');
});

test('date → x scale is linear and invertible', () => {
  const x = makeScale(100, 200, 10, 1010);
  assert.equal(x(100), 10);
  assert.equal(x(150), 510);
  assert.equal(x(200), 1010);
  assert.equal(x.invert(510), 150);
});

test('domain covers whole months of plan, observations and selected date', () => {
  const [d0, d1] = ganttDomain(status);
  assert.equal(dayIso(d0), '2024-03-01');
  assert.equal(dayIso(d1), '2025-01-01');
  const ticks = monthTicks(d0, d1);
  assert.equal(ticks.length, 10);
  assert.equal(ticks[0].label, 'мар 2024');
  assert.equal(ticks[1].label, 'апр');
  const jan = monthTicks(dayNumber('2024-12-01'), dayNumber('2025-02-01'));
  assert.deepEqual(jan.map(t => t.label), ['дек 2024', 'янв 2025']);
});

test('gantt layout: one row per stage, dots on the top-stage row, today line at date', () => {
  const layout = ganttLayout(status, {plotWidth: 1000, rowHeight: 30, axisHeight: 28});
  const unmatched = status.observations.some(o => !status.stages.some(s => s.stage_key === o.top_stage));
  assert.equal(layout.rows.length, status.stages.length + (unmatched ? 1 : 0));
  assert.equal(layout.width, 1000);
  const ex = layout.rows.findIndex(r => r.key === 'excavation');
  const row = layout.rows[ex];
  assert.ok(row.plan.x > 0 && row.plan.w > 0);
  // plan bar: 1 Apr → 31 May inclusive = 61 days
  const dayW = (layout.width - 16) / (layout.d1 - layout.d0);
  assert.ok(Math.abs(row.plan.w - 61 * dayW) < 0.01);
  const exDots = layout.dots.filter(d => d.obs.top_stage === 'excavation');
  assert.ok(exDots.length > 0 && exDots.every(d => d.y === row.cy));
  assert.ok(layout.today > row.plan.x + row.plan.w, 'selected date is after planned end');
  assert.equal(layout.dots.length, status.observations.length);
  if (unmatched) assert.equal(layout.rows.at(-1).key, UNMATCHED);
  // same-day observations on one row do not overlap exactly
  const pairs = layout.dots.filter(d => d.obs.captured_at === '2024-06-25');
  assert.equal(new Set(pairs.map(d => `${d.x},${d.y}`)).size, pairs.length);
});

test('narrow plot width forces horizontal scroll instead of squashing', () => {
  const layout = ganttLayout(status, {plotWidth: 200, minDayWidth: 3});
  assert.ok(layout.width > 200);
});

test('score levels', () => {
  assert.equal(scoreLevel(0.82), 'hi');
  assert.equal(scoreLevel(0.5), 'mid');
  assert.equal(scoreLevel(0.1), 'lo');
  assert.equal(scoreLevel(undefined), 'lo');
});

test('gantt SVG has clickable dots, escaped labels and delay badges', () => {
  const evil = {...status, stages: [{...status.stages[2], name: '<script>x</script>'}], observations: status.observations.slice(0, 3)};
  const layout = ganttLayout(evil, {plotWidth: 600});
  const labels = renderGanttLabels(layout, {labelWidth: 250});
  const plot = renderGanttPlot(layout, {stageNames: {excavation: 'Котлован'}});
  assert.doesNotMatch(labels + plot, /<script>/);
  assert.match(labels, /&lt;script/);
  assert.match(labels, /\+25 дн\./);
  assert.match(plot, /data-image-id="img-001"/);
  assert.match(plot, /class="g-today"/);
  assert.match(plot, /class="g-plan tone-behind"/);
});

test('latest observation date and mock date filter', () => {
  assert.equal(latestObservationDate(status), '2024-06-25');
  assert.equal(latestObservationDate({observations: []}), null);
  const earlier = upToDate(status, '2024-04-30');
  assert.ok(earlier.observations.every(o => o.captured_at <= '2024-04-30'));
  assert.equal(earlier.date, '2024-04-30');
});

test('batch captured_at carries the site timezone offset', () => {
  assert.equal(tzOffset('2024-06-25', 'Europe/Moscow'), '+03:00');
  assert.equal(tzOffset('2024-06-25', 'UTC'), '+00:00');
  assert.equal(tzOffset('2024-01-15', 'America/New_York'), '-05:00');
  assert.equal(tzOffset('2024-07-15', 'America/New_York'), '-04:00');
  assert.equal(capturedAtFor('2024-06-25', 'Europe/Moscow'), '2024-06-25T12:00:00+03:00');
});

test('verdict, table and alerts render Russian labels and escape data', () => {
  const v = renderVerdict(status);
  assert.match(v, /Отстаём на 25 дн\./);
  assert.match(v, /tone-behind/);
  assert.match(renderVerdict({verdict: {status: 'no_data'}, stages: [], observations: []}), /Нет данных/);
  const t = renderStagesTable(status);
  assert.match(t, /Разработка котлована/);
  assert.match(t, /01\.04\.2024 – 31\.05\.2024/);
  assert.match(t, /Отклонение/);
  const url = imageUrlMap(status, 'mock-b');
  assert.equal(url('img-001'), '/assets/mock-photo.svg');
  assert.equal(url('other'), '/api/projects/mock-b/images/other/file');
  const a = renderAlerts({...status, alerts: [{severity: 'high', kind: 'behind', stage_key: 'excavation', message: '<b>x</b>', image_ids: ['img-001']}]}, url);
  assert.match(a, /Отставание/);
  assert.match(a, /&lt;b&gt;x/);
  assert.match(a, /data-image-id="img-001"/);
});

test('equipment labels merge API list with local materials; viewer draws both kinds of boxes', () => {
  const labels = labelsFrom([{id: 'excavator', name: 'Экскаватор (API)'}, {id: 'material:rebar', name: 'Арматура (API)'}]);
  assert.equal(labels.excavator, 'Экскаватор (API)');
  assert.equal(labels.rebar, 'Арматура (API)');
  assert.equal(labels.formwork, 'Опалубка');
  const analysis = {detections: [{equipment: 'excavator', confidence: .91, bbox: [.1, .2, .4, .8]}],
    materials: [{material: 'rebar', confidence: .6, bbox: [.5, .5, .9, .9]}, {material: 'concrete', confidence: .4}],
    stages: [{stage_key: 'foundation', score: .3}, {stage_key: 'excavation', score: .82}], top_stage: 'excavation'};
  const boxes = analysisBoxes(analysis, labels);
  assert.equal(boxes.length, 2);
  const html = renderViewer({observation: {captured_at: '2024-06-25'}, analysis, imageUrl: '/x.png', labels,
    stageNames: {excavation: 'Разработка котлована'}});
  assert.match(html, /class="box-equipment" x="100\.0" y="200\.0"/);
  assert.match(html, /class="box-material"/);
  assert.match(html, /Экскаватор \(API\) · 91%/);
  assert.match(html, /25\.06\.2024/);
  assert.ok(html.indexOf('Разработка котлована') < html.indexOf('foundation'), 'stages ranked by score');
});

test('box overlay escapes labels', () => {
  const html = renderBoxOverlay([{bbox: [0, 0, .5, .5], label: '<img onerror=x>', confidence: .5}]);
  assert.doesNotMatch(html, /<img onerror/);
  assert.match(html, /left:0\.00%;top:0\.00%/);
});
