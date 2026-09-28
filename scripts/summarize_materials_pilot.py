"""Compute image-level pilot agreement; never interpret as object-level mAP."""
import hashlib
import html
import json
import sys
import statistics
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.paths import dataset_image  # noqa: E402
DATA = ROOT / 'data/experiments/materials-20260928'
WORK = ROOT / 'runtime/experiments/materials-20260928'
EQUIPMENT = ['excavator','dump_truck','roller','tower_crane','concrete_mixer']
MATERIALS = ['rebar','brick_wall','steel_pipe','formwork']
ALIASES = {'sitesense': {'excavator':'excavator','dump_truck':'dump_truck',
                        'roller_compactor':'roller','tower_crane':'tower_crane','cement_mixer':'concrete_mixer'},
           'grounding-dino': {'excavator':'excavator','dump truck':'dump_truck','road roller':'roller',
                             'tower crane':'tower_crane','concrete mixer truck':'concrete_mixer',
                             'rebar':'rebar','brick wall':'brick_wall','steel pipe':'steel_pipe','formwork':'formwork'}}
ALIASES['grounding-materials'] = {key:value for key,value in ALIASES['grounding-dino'].items() if value in MATERIALS}


def score_pairs(pairs):
    counts = dict(tp=0, fp=0, fn=0, tn=0, excluded=0)
    for truth, prediction in pairs:
        if truth is None:
            counts['excluded'] += 1
        else:
            counts['tp' if truth and prediction else 'fn' if truth else 'fp' if prediction else 'tn'] += 1
    counts['precision'] = counts['tp'] / (counts['tp'] + counts['fp']) if counts['tp'] + counts['fp'] else None
    counts['recall'] = counts['tp'] / (counts['tp'] + counts['fn']) if counts['tp'] + counts['fn'] else None
    return counts


def main():
    reference_bytes = (DATA / 'reference.json').read_bytes()
    reference = json.loads(reference_bytes)
    ref_hash = hashlib.sha256(reference_bytes).hexdigest()
    correction_path = DATA / 'review-corrections.json'
    corrections = json.loads(correction_path.read_text(encoding='utf-8')) if correction_path.exists() else {'corrections':[]}
    corrected = {(r['image'],r['class']):r['after'] for r in corrections['corrections']}
    summary = {'metric':'image-level presence agreement with AI visual reference; not mAP; not expert ground truth',
               'reference_sha256':ref_hash, 'post_inference_corrections':corrections, 'models':{}}
    results = {}
    for name in ['hazard','sitesense','grounding-dino','grounding-materials']:
        path = DATA / f'{name}.json'
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding='utf-8'))
        assert result['reference_sha256'] == ref_hash
        rows = {r['image']:r for r in result['results']}
        results[name] = rows
        times = [r['seconds'] for r in result['results']]
        record = {'frames':len(rows),'complete':result['complete'],
                  'median_seconds':statistics.median(times) if times else None,
                  'total_inference_seconds':sum(times),
                  'peak_process_rss_gib':result['peak_process_rss_gib'],
                  'per_class':{},'corrected_per_class':{},'disagreements':[]}
        if name == 'hazard':
            record['broad_equipment_presence_frames'] = sum(any(d['label'] in ('machinery','vehicle') for d in r['detections']) for r in rows.values())
            record['note'] = 'All 13 selected frames have equipment. No broad-equipment negatives; specificity cannot be estimated. Specific equipment/material classes unsupported.'
        else:
            available = MATERIALS if name == 'grounding-materials' else EQUIPMENT + (MATERIALS if name == 'grounding-dino' else [])
            if name == 'grounding-materials':
                record['study_note'] = 'Exploratory material-only prompt after primary run, same images; not independent test'
            unmapped = set()
            predicted = {}
            for key,row in rows.items():
                labels = {d['label'].strip().lower() for d in row['detections']}
                predicted[key] = {ALIASES[name][label] for label in labels if label in ALIASES[name]}
                unmapped |= labels - set(ALIASES[name])
            record['unmapped_labels'] = sorted(unmapped)
            for cls in available:
                pairs=[]
                reviewed_pairs=[]
                for row in reference['images']:
                    key = row['image']
                    if key not in rows:
                        continue
                    truth = row['presence'][cls]
                    pred = cls in predicted[key]
                    pairs.append((truth,pred))
                    reviewed_pairs.append((corrected.get((key,cls),truth),pred))
                    if truth is not None and truth != pred:
                        record['disagreements'].append({'image':key,'class':cls,'reference':truth,'prediction':pred})
                record['per_class'][cls] = score_pairs(pairs)
                record['corrected_per_class'][cls] = score_pairs(reviewed_pairs)
        summary['models'][name] = record
    (DATA / 'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    head='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Пилот моделей: 13 кадров</title><style>body{font:16px system-ui;background:#f1f5f9;color:#172033;margin:32px auto;padding:0 20px;max-width:1500px}h1{font-size:30px}section{background:white;border-radius:12px;padding:20px;margin:24px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}img{width:100%;height:auto}figure{margin:0}figcaption{padding:8px;font-weight:bold}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #cbd5e1;text-align:left}.note{color:#475569}details{margin:10px 0}@media(max-width:750px){.grid{grid-template-columns:1fr}}</style><h1>Эксперимент: техника и материалы</h1><p>28.09.2026 · CPU, два потока · 13 выбранных кадров · без обучения</p><p><strong>Разведочная проверка.</strong> Метки подготовлены Codex до inference, не подтверждены экспертом. Считается наличие класса в кадре, а не точность рамок или выполнение работ. Похожие сцены и малое число примеров ограничивают выводы.</p>'''
    parts=[head,'<section><h2>Ресурсы и скорость</h2><table><tr><th>Модель</th><th>Кадров</th><th>Медиана, с</th><th>Пиковая RSS процесса, ГиБ</th></tr>']
    for name,rec in summary['models'].items():
        parts.append(f"<tr><td>{html.escape(name)}</td><td>{rec['frames']}</td><td>{rec['median_seconds']:.2f}</td><td>{rec['peak_process_rss_gib']:.2f}</td></tr>")
    parts.append('</table><p class="note">Без времени загрузки весов; первый холодный inference включён. RSS семплирована каждые 100 мс, не отражает всю память системы.</p></section>')
    review_path = DATA / 'localization-review.json'
    if review_path.exists():
        review = json.loads(review_path.read_text(encoding='utf-8'))
        parts.append('<section><h2>Ошибки рамок: почему совпадения класса недостаточно</h2><p>Это выборочная визуальная проверка Codex после inference. Даже TP в таблицах ниже может соответствовать рамке на неверном объекте.</p><ul>')
        for case in review['cases']:
            parts.append('<li>'+html.escape(f"{case['model']} / {case['image']}: {case['finding']}")+'</li>')
        parts.append('</ul></section>')
    for name,rec in summary['models'].items():
        if not rec['per_class']:
            continue
        parts.append(f'<section><h2>{html.escape(name)}: совпадение с предварительной разметкой</h2><table>')
        if name == 'grounding-materials':
            parts.append('<caption>Дополнительный эксперимент с коротким запросом после просмотра первых результатов; это подбор на той же выборке.</caption>')
        parts.append('<tr><th>Класс</th><th>TP</th><th>FP</th><th>FN</th><th>TN</th><th>Неясно</th><th>Precision</th><th>Recall</th></tr>')
        for cls,c in rec['per_class'].items():
            values=[str(c[k]) for k in ['tp','fp','fn','tn','excluded']]+['—' if c[k] is None else f'{c[k]:.0%}' for k in ['precision','recall']]
            parts.append('<tr><td>'+html.escape(cls)+'</td>'+''.join(f'<td>{v}</td>' for v in values)+'</tr>')
        parts.append('</table><p class="note">TP/FP относятся к парам «кадр–класс». Число объектов и IoU здесь не оценены. Ошибки самой предварительной разметки возможны. На кадре 6 после увеличения подтверждён самосвал, пропущенный в исходной разметке; исправленные метрики сохранены отдельно в summary.json. Такой пересмотр после inference не является независимой проверкой.</p></section>')
    for row in reference['images']:
        stem=Path(row['image']).stem
        number=stem.split('_')[-1]
        with Image.open(dataset_image(row['image'])) as source:
            original = source.convert('RGB')
        WORK.mkdir(parents=True, exist_ok=True)
        ImageOps.contain(original, (1000,750)).save(WORK / f'review-{number}.jpg')
        parts.append(f'<section><h2>{html.escape(stem)}</h2><p>{html.escape(row["note"])}</p><details><summary>Предварительные метки</summary><pre>{html.escape(json.dumps(row["presence"],indent=2))}</pre></details><div class="grid"><figure><figcaption>Исходный кадр</figcaption><img loading="lazy" src="review-{number}.jpg" alt="Исходный кадр {number}"></figure>')
        for name,rows in results.items():
            if row['image'] not in rows:
                continue
            target = WORK / 'overlays' / name / f'{stem}.jpg'
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                canvas = ImageOps.contain(original, (1200,900))
                draw = ImageDraw.Draw(canvas)
                sx,sy = canvas.width/original.width,canvas.height/original.height
                for detection in rows[row['image']]['detections']:
                    x1,y1,x2,y2 = detection['bbox_xyxy']
                    box = (x1*sx,y1*sy,x2*sx,y2*sy)
                    draw.rectangle(box,outline='#ff3030',width=2)
                    draw.text((box[0],max(0,box[1]-12)),f"{detection['label']} {detection['confidence']:.2f}",fill='#ff3030',stroke_width=1,stroke_fill='white')
                canvas.save(target)
            parts.append(f'<figure><figcaption>{html.escape(name)}</figcaption><img loading="lazy" src="overlays/{name}/{stem}.jpg" alt="Рамки {html.escape(name)} {number}"></figure>')
        parts.append('</div></section>')
    (WORK / 'index.html').write_text(''.join(parts)+'</html>',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
