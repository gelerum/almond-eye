"""Image-level evaluation of each detector adapter and of the fused ensemble.

Ground truth: data/manifests/dgp_images.json (presence of each equipment class per frame).
Step 1 (GPU, slow) runs every adapter once and caches raw boxes to runtime/eval/raw.json.
Step 2 (fast) fuses the cached boxes and scores them; `--tune` searches per-class thresholds on
odd-numbered frames and reports on even-numbered frames, so the headline numbers are not
tuned on the frames they are measured on.

  uv run scripts/eval_detectors.py            # run adapters (if cache is stale) + report
  uv run scripts/eval_detectors.py --tune     # also write tuned thresholds to runtime/eval/
"""
import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.paths import DGP_IMAGES, MANIFESTS_DIR  # noqa: E402
from backend.app.services.detectors.base import RawBox  # noqa: E402
from backend.app.services.detectors.ensemble import fuse  # noqa: E402

OUT = ROOT / 'runtime/eval'
THRESHOLDS = [round(0.15 + 0.05 * i, 2) for i in range(15)]


def run_adapters(pipeline, images):
    from PIL import Image
    raw, timings = {}, {}
    for n, item in enumerate(images, 1):
        with Image.open(DGP_IMAGES / item['file']) as source:
            boxes, coverage, t = pipeline.run_adapters(source.convert('RGB'))
        raw[item['file']] = {'boxes': [asdict(b) for b in boxes], 'coverage': coverage}
        timings[item['file']] = t
        print(f'\r{n}/{len(images)}', end='', flush=True)
    print()
    return raw, timings


def predicted(raw_item, config, sources=None):
    boxes = [RawBox(**{**b, 'bbox': tuple(b['bbox'])}) for b in raw_item['boxes']
             if sources is None or b['source'] in sources]
    return {b.canonical for b in fuse(boxes, config) if not b.canonical.startswith('material:')}


def score(images, raw, config, classes, sources=None):
    stats = {c: [0, 0, 0] for c in classes}  # tp, fp, fn
    for item in images:
        truth, pred = set(item['equipment']), predicted(raw[item['file']], config, sources)
        for c in classes:
            stats[c][0] += c in truth and c in pred
            stats[c][1] += c in pred and c not in truth
            stats[c][2] += c in truth and c not in pred
    table = {}
    for c, (tp, fp, fn) in stats.items():
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * r / (p + r) if p and r else 0.0
        table[c] = {'tp': tp, 'fp': fp, 'fn': fn, 'precision': p, 'recall': r, 'f1': f1, 'support': tp + fn}
    return table


def tune(images, raw, config, classes):
    thresholds = {}
    for c in classes:
        best = (-1.0, config.threshold(c))
        for t in THRESHOLDS:
            trial = replace(config, min_confidence={**config.min_confidence, c: t})
            f1 = score(images, raw, trial, [c])[c]['f1']
            if f1 > best[0] + 1e-9:
                best = (f1, t)
        thresholds[c] = best[1]
    return replace(config, min_confidence={**config.min_confidence, **thresholds})


def fmt(v):
    return '—' if v is None else f'{v:.2f}'


def markdown(tables, classes):
    names = list(tables)
    lines = ['| Класс | n | ' + ' | '.join(f'{n} P / R' for n in names) + ' |',
             '|---|---|' + '---|' * len(names)]
    for c in classes:
        support = tables[names[-1]][c]['support']
        cells = [f"{fmt(tables[n][c]['precision'])} / {fmt(tables[n][c]['recall'])}" for n in names]
        lines.append(f'| {c} | {support} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rerun', action='store_true', help='ignore the raw-box cache')
    parser.add_argument('--tune', action='store_true')
    args = parser.parse_args()
    from backend.app.services.detectors.pipeline import DetectionPipeline, fusion_config, load_config
    config = load_config()
    manifest = json.loads((MANIFESTS_DIR / 'dgp_images.json').read_text(encoding='utf-8'))
    images = manifest['images']
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / 'raw.json'
    if args.rerun or not cache.exists():
        pipeline = DetectionPipeline(config)
        raw, timings = run_adapters(pipeline, images)
        cache.write_text(json.dumps({'adapters': pipeline.status(), 'raw': raw, 'timings': timings}))
    cached = json.loads(cache.read_text())
    raw = cached['raw']
    fusion = fusion_config(config)
    classes = sorted({c for item in images for c in item['equipment']})
    sources = sorted({b['source'] for item in raw.values() for b in item['boxes']})

    odd = [i for i in images if int(Path(i['file']).stem.split('_')[1]) % 2]
    even = [i for i in images if not int(Path(i['file']).stem.split('_')[1]) % 2]
    tables = {s: score(even, raw, fusion, classes, {s}) for s in sources if s != 'hazard'}
    tables['ensemble'] = score(even, raw, fusion, classes)
    result = {'split': 'report on even-numbered frames', 'thresholds': fusion.min_confidence, 'tables': tables}
    if args.tune:
        tuned = tune(odd, raw, fusion, classes)
        tables['ensemble (tuned on odd)'] = score(even, raw, tuned, classes)
        result['tuned_thresholds'] = tuned.min_confidence
        (OUT / 'tuned-thresholds.json').write_text(json.dumps(tuned.min_confidence, indent=2))
    timings = cached['timings'].values()
    result['median_seconds'] = {s: sorted(t[s] for t in timings)[len(timings) // 2] for s in next(iter(timings))}
    (OUT / 'report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(markdown(tables, classes))
    print('median seconds per image:', result['median_seconds'])
    if args.tune:
        print('tuned thresholds:', result['tuned_thresholds'])


if __name__ == '__main__':
    main()
