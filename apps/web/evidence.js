// Rendering is pure: saved observations and zone snapshots, never current settings.
export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

export const equipmentNames = {
  dump_truck:'Самосвал', excavator:'Экскаватор', roller:'Каток',
  loader_crane:'Кран-манипулятор', concrete_mixer:'Бетоносмеситель',
  bulldozer:'Бульдозер', truck:'Грузовик', mobile_crane:'Автокран'
};
export const statusNames = {
  warning:'Предупреждение', insufficient_data:'Недостаточно данных',
  matches_observations:'Соответствует наблюдениям'
};
export const modelNames = {
  Hardhat:'Каска', Mask:'Маска', 'NO-Hardhat':'Без каски', 'NO-Mask':'Без маски',
  'NO-Safety Vest':'Без сигнального жилета', Person:'Человек', 'Safety Cone':'Конус',
  'Safety Vest':'Сигнальный жилет', machinery:'Техника (общий класс)',
  'utility pole':'Опора', vehicle:'Транспорт (общий класс)'
};
const detectionName = d => d.label ? (modelNames[d.label] || d.label) : (equipmentNames[d.equipment] || d.equipment);

export function renderScene(scene) {
  if (!scene) return '';
  const counts = values => Object.entries(values || {}).map(([label,count]) =>
    `<li>${escapeHtml(modelNames[label] || label)}: ${escapeHtml(count)}</li>`).join('');
  return `<section class="scene-summary"><h3>Обзор сцены · экспериментальные наблюдения</h3>
    ${scene.status === 'quality_limited' ? '<p class="notice">Качество кадра ограничено. Сигналы для проверки не формируются.</p>' : ''}
    <p>Порог обзора: ${(scene.confidence_threshold*100).toFixed(0)}%. Число рамок по классам, не уникальных людей.</p>
    <div class="grid"><div><b>Весь кадр</b><ul>${counts(scene.frame_counts) || '<li>Нет уверенных детекций; это не доказательство отсутствия объектов.</li>'}</ul></div>
    <div><b>В выбранной зоне</b><ul>${scene.zone_counts === null ? '<li>Зона не выбрана</li>' : counts(scene.zone_counts) || '<li>Нет уверенных детекций внутри зоны</li>'}</ul></div></div>
    <p>Слабых рамок: ${escapeHtml(scene.weak_detections)} · на границе зоны: ${escapeHtml(scene.boundary_detections)}</p>
    <h3>Требует просмотра человеком</h3>
    ${scene.review_candidates.length ? `<ul>${scene.review_candidates.map(c => `<li>${escapeHtml(c.message)}${c.confidence !== undefined ? ` · ${(c.confidence*100).toFixed(1)}% · ${escapeHtml(({inside:'в зоне',outside:'вне зоны',boundary:'на границе',unassigned:'весь кадр'})[c.zone_location])}` : ''}</li>`).join('')}</ul>` : '<p>Кандидатов нет. Соблюдение требований этим не подтверждено.</p>'}
    <h3>Контекст загруженного графика</h3>
    <p>${escapeHtml(scene.plan_context.reason)}</p>
    ${scene.plan_context.status === 'outside_plan' ? '<p>На дату снимка в выбранной зоне нет активных работ в этой версии графика.</p>' : ''}
    <ul>${scene.plan_context.active_works.map(w => `<li>${escapeHtml(w.name)} · ${escapeHtml(w.start_date)} — ${escapeHtml(w.end_date)}</li>`).join('')}</ul>
    <details><summary>Границы интерпретации</summary><ul>${scene.limitations.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ul></details></section>`;
}

export function renderEvidence(evaluation, sourceUrl) {
  const raw = evaluation.observation?.model_detections || [];
  const detections = raw.length ? raw : (evaluation.observation?.detections || []);
  const polygon = evaluation.zone_snapshot?.polygon || [];
  const points = polygon.map(([x,y]) => `${x*1000},${y*1000}`).join(' ');
  const boxes = detections.map(d => {
    const [x1,y1,x2,y2] = d.bbox;
    const label = `${detectionName(d)} · ${(d.confidence*100).toFixed(1)}%`;
    return `<g><title>${escapeHtml(label)}</title><rect x="${x1*1000}" y="${y1*1000}" width="${(x2-x1)*1000}" height="${(y2-y1)*1000}"/><circle cx="${(x1+x2)*500}" cy="${y2*1000}" r="4"/></g>`;
  }).join('');
  const time = evaluation.captured_at || 'Время съёмки неизвестно';
  const demo = evaluation.time_source === 'demonstration' || evaluation.schedule_snapshot?.works?.some(w => w.provenance === 'demonstration');
  return `<div class="evidence">
    ${evaluation.mode === 'fixture' ? '<p class="notice">ТЕСТОВЫЕ ДЕТЕКЦИИ: рамки заданы вручную, это не результат модели.</p>' : ''}
    ${demo ? '<p class="notice">Использованы демонстрационные сроки или время снимка.</p>' : ''}
    <p>${escapeHtml(time)} · ${escapeHtml(evaluation.zone_snapshot?.name || 'Весь кадр — зона не выбрана')}</p>
    ${renderScene(evaluation.scene)}
    <div class="evidence-frame"><img src="${escapeHtml(sourceUrl)}" alt="Снимок-доказательство" loading="lazy">
      <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="Зона и рамки обнаруженных объектов" role="img">
        <polygon class="zone-outline" points="${points}"/>${boxes}
      </svg>
    </div>
    <p class="muted">Жёлтый контур — сохранённая зона. Зелёные рамки — все детекции, включая слабые и вне зоны. Точка внизу рамки определяет принадлежность зоне.</p>
    ${raw.length ? '<p class="notice">Исходные классы модели. Общие категории техники и транспорта не подтверждают конкретные типы техники в графике. Детекции СИЗ требуют проверки человеком.</p>' : ''}
    ${detections.length ? `<ul>${detections.map(d=>`<li>${escapeHtml(detectionName(d))}: ${(d.confidence*100).toFixed(1)}%</li>`).join('')}</ul>` : '<p>Рамки не получены. Причину и условия проверки смотрите в результате.</p>'}
    <a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">Открыть исходный снимок</a>
    <p class="muted">Модель: ${escapeHtml(evaluation.model_version)}<br>Версия графика: ${escapeHtml(evaluation.schedule_id || 'не выбрана')}</p>
  </div>`;
}
