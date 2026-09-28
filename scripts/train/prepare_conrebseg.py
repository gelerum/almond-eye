"""Convert ConRebSeg (Schmidt & Nalpantidis 2025, CC BY 4.0) to YOLO detection format.

Source: https://doi.org/10.11583/DTU.26213762 — `samples.json` (FiftyOne export with instance
annotations) and `ConRebSeg.zip` (self-collected sequences `langebro`, `vester_sogade`, ~22 GB).
The YouTube part of the dataset is not downloaded (see the authors' README about local law).

Only `ExposedBars` (exposed rebar) is kept: it is the cue we need for reinforcement stages;
trucks/people are already covered by other models. FiftyOne boxes are [x, y, w, h] normalised.
Split comes from the sample tags (train / val / test; test is merged into val here).
Sequences are video frames, so only every `--stride`-th frame of a sequence is kept.

Output: runtime/datasets/conrebseg/yolo/{images,labels}/{train,val} + data.yaml
"""
import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'runtime/datasets/conrebseg'
CLASSES = {'ExposedBars': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--stride', type=int, default=3)
    args = parser.parse_args()
    out = args.data / 'yolo'
    samples = json.loads((args.data / 'samples.json').read_text())['samples']
    archive = zipfile.ZipFile(args.data / 'ConRebSeg.zip')
    members = {Path(name).as_posix(): name for name in archive.namelist() if not name.endswith('/')}
    by_suffix = {'/'.join(Path(k).parts[-3:]): v for k, v in members.items()}
    counts = {'train': 0, 'val': 0, 'missing': 0, 'boxes': 0}
    seen: dict[str, int] = {}
    for sample in sorted(samples, key=lambda s: s['filepath']):
        path = sample['filepath']
        if '/youtube/' in path:
            continue
        sequence = str(Path(path).parent)
        seen[sequence] = seen.get(sequence, -1) + 1
        if seen[sequence] % args.stride:
            continue
        member = members.get(path) or by_suffix.get('/'.join(Path(path).parts[-3:]))
        if member is None:
            counts['missing'] += 1
            continue
        split = 'train' if 'train' in sample.get('tags', []) else 'val'
        stem = '_'.join(Path(path).parts[-3:]).rsplit('.', 1)[0]
        image_dir, label_dir = out / 'images' / split, out / 'labels' / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for det in (sample.get('gt_annotations') or {}).get('detections', []):
            if det['label'] not in CLASSES:
                continue
            x, y, w, h = det['bounding_box']
            lines.append(f"{CLASSES[det['label']]} {x + w / 2:.6f} {y + h / 2:.6f} {w:.6f} {h:.6f}")
        target = image_dir / f'{stem}{Path(path).suffix}'
        if not target.exists():
            target.write_bytes(archive.read(member))
        (label_dir / f'{stem}.txt').write_text('\n'.join(lines) + ('\n' if lines else ''))
        counts[split] += 1
        counts['boxes'] += len(lines)
    (out / 'data.yaml').write_text(f'path: {out}\ntrain: images/train\nval: images/val\nnames:\n  0: exposed_bars\n')
    print(counts)


if __name__ == '__main__':
    main()
