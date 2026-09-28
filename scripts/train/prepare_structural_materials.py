"""Convert the VT Structural Material Semantic Segmentation Dataset (CC0) to YOLO-seg format.

Source: https://doi.org/10.7294/16624648 (Bianchi & Hebdon, 3817 bridge-inspection images,
LabelMe polygons: Concrete, Steel, Metal_Deck). Download "Material Detection.zip" and unpack it
into runtime/datasets/structural-material/ first.

Output: runtime/datasets/structural-material/yolo/{images,labels}/{train,val} + data.yaml
The dataset's own Train/Test split is kept (Test -> val).
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'runtime/datasets/structural-material/Material Detection/original'
CLASSES = ['concrete', 'steel', 'metal_deck']
LABELS = {'Concrete': 0, 'Steel': 1, 'Metal_Deck': 2}


def convert(json_path: Path, image_dir: Path, image_out: Path, label_out: Path) -> bool:
    data = json.loads(json_path.read_text())
    image = image_dir / Path(data['imagePath']).name
    if not image.exists():
        image = image_dir / f'{json_path.stem}.jpeg'
    if not image.exists():
        return False
    width, height = data['imageWidth'], data['imageHeight']
    lines = []
    for shape in data['shapes']:
        if shape.get('shape_type', 'polygon') != 'polygon' or len(shape['points']) < 3:
            continue
        coords = []
        for x, y in shape['points']:
            coords += [min(max(x / width, 0), 1), min(max(y / height, 0), 1)]
        lines.append(f"{LABELS[shape['label']]} " + ' '.join(f'{v:.6f}' for v in coords))
    shutil.copy2(image, image_out / f'{json_path.stem}.jpg')
    (label_out / f'{json_path.stem}.txt').write_text('\n'.join(lines) + ('\n' if lines else ''))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--out', type=Path, default=SOURCE.parents[1] / 'yolo')
    args = parser.parse_args()
    for split, name in (('Train', 'train'), ('Test', 'val')):
        image_out, label_out = args.out / 'images' / name, args.out / 'labels' / name
        image_out.mkdir(parents=True, exist_ok=True)
        label_out.mkdir(parents=True, exist_ok=True)
        converted = sum(convert(path, args.source / split / 'images', image_out, label_out)
                        for path in sorted((args.source / split / 'json').glob('*.json')))
        print(f'{split}: {converted} images')
    (args.out / 'data.yaml').write_text(
        f'path: {args.out}\ntrain: images/train\nval: images/val\nnames:\n'
        + ''.join(f'  {i}: {name}\n' for i, name in enumerate(CLASSES)))


if __name__ == '__main__':
    main()
