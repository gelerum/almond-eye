"""Draft grouping of the organiser photos into construction sites.

DINOv2 embeddings (GPU if available) -> average-linkage agglomerative clustering on cosine
distance -> draft manifest + one contact sheet per cluster for manual review.
The draft is a starting point only: data/manifests/dgp_images.json is curated by hand.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.paths import DGP_IMAGES  # noqa: E402


def embed(paths, model_id, device):
    from transformers import AutoImageProcessor, AutoModel
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id).to(device).eval()
    vectors = []
    with torch.inference_mode():
        for path in paths:
            with Image.open(path) as im:
                inputs = processor(images=im.convert('RGB'), return_tensors='pt').to(device)
            out = model(**inputs).last_hidden_state
            # CLS token + mean of patch tokens: CLS captures the scene, patches the texture/layout.
            vector = torch.cat([out[:, 0], out[:, 1:].mean(1)], dim=1)[0]
            vectors.append(torch.nn.functional.normalize(vector, dim=0).cpu().numpy())
    return np.stack(vectors)


def agglomerate(vectors, threshold):
    """Average linkage on cosine distance; stops when the closest clusters are farther than threshold."""
    clusters = [[i] for i in range(len(vectors))]
    distance = 1 - vectors @ vectors.T
    while len(clusters) > 1:
        best, pair = None, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = distance[np.ix_(clusters[a], clusters[b])].mean()
                if best is None or d < best:
                    best, pair = d, (a, b)
        if best > threshold:
            break
        a, b = pair
        clusters[a] += clusters.pop(b)
    return sorted(clusters, key=len, reverse=True)


def contact_sheet(paths, target):
    tiles = []
    for path in paths:
        with Image.open(path) as im:
            tile = ImageOps.contain(im.convert('RGB'), (320, 200))
        canvas = Image.new('RGB', (320, 220), 'white')
        canvas.paste(tile, (0, 0))
        ImageDraw.Draw(canvas).text((4, 204), path.stem, fill='black')
        tiles.append(canvas)
    cols = min(5, len(tiles))
    sheet = Image.new('RGB', (cols * 320, -(-len(tiles) // cols) * 220), 'white')
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % cols) * 320, (i // cols) * 220))
    sheet.save(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='facebook/dinov2-base')
    parser.add_argument('--threshold', type=float, default=0.45)
    parser.add_argument('--out', type=Path, default=ROOT / 'runtime/site-clusters')
    args = parser.parse_args()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    paths = sorted(DGP_IMAGES.glob('*.png'), key=lambda p: int(p.stem.split('_')[-1]))
    vectors = embed(paths, args.model, device)
    clusters = agglomerate(vectors, args.threshold)
    args.out.mkdir(parents=True, exist_ok=True)
    draft = []
    for n, members in enumerate(clusters, 1):
        site = f'cluster-{n:02d}'
        contact_sheet([paths[i] for i in members], args.out / f'{site}.jpg')
        draft += [{'file': paths[i].name, 'site_id': site} for i in members]
    (args.out / 'draft.json').write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding='utf-8')
    np.save(args.out / 'embeddings.npy', vectors)
    print(f'{len(clusters)} clusters, sizes {[len(c) for c in clusters]} -> {args.out}')


if __name__ == '__main__':
    main()
