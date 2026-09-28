"""Fine-tune an Ultralytics model on a prepared dataset and publish the best weights.

Examples (RTX 3060 12 GB):
  uv run scripts/train/train_yolo.py --data runtime/datasets/structural-material/yolo/data.yaml \
      --model yolo11s-seg.pt --name structural-materials --imgsz 640 --batch 16 --epochs 60
  uv run scripts/train/train_yolo.py --data runtime/datasets/conrebseg/yolo/data.yaml \
      --model yolo11s.pt --name conrebseg-rebar --imgsz 960 --batch 8 --epochs 40

Best weights are copied to runtime/models/<name>/best.pt with a train-manifest.json next to them
(dataset, hyper-parameters, final metrics) so the app can report what a model was trained on.
"""
import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--model', default='yolo11s.pt')
    parser.add_argument('--name', required=True)
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--device', default=os.getenv('TORCH_DEVICE', '0'))
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--patience', type=int, default=15)
    parser.add_argument('--resume', action='store_true',
                        help='continue an interrupted run from runtime/train/<name>/weights/last.pt')
    args = parser.parse_args()

    os.environ.setdefault('YOLO_CONFIG_DIR', str(ROOT / 'runtime'))
    from ultralytics import YOLO
    project = ROOT / 'runtime/train'
    run = project / args.name
    if args.resume:
        # Ultralytics restores epochs, optimizer and data from the checkpoint itself; take the real
        # hyper-parameters from it too, so the manifest does not record this script's defaults.
        import torch
        saved = torch.load(run / 'weights/last.pt', map_location='cpu', weights_only=False).get('train_args', {})
        # After a first resume `model` points at our own last.pt; pass --model to name the base then.
        if saved.get('model') and Path(saved['model']).name not in ('last.pt', 'best.pt'):
            args.model = Path(saved['model']).name
        args.epochs, args.imgsz, args.batch = (saved.get(k, getattr(args, k)) for k in ('epochs', 'imgsz', 'batch'))
        YOLO(str(run / 'weights/last.pt')).train(resume=True)
    else:
        YOLO(args.model).train(data=str(args.data.resolve()), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                               device=args.device, workers=args.workers, patience=args.patience, project=str(project),
                               name=args.name, exist_ok=True, plots=True, cos_lr=True, amp=True)
    best = run / 'weights/best.pt'
    target = ROOT / 'runtime/models' / args.name
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, target / 'best.pt')
    metrics = YOLO(str(best)).val(data=str(args.data.resolve()), imgsz=args.imgsz, device=args.device,
                                   project=str(project), name=f'{args.name}-val', exist_ok=True)
    with (target / 'best.pt').open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = {'name': args.name, 'base_model': args.model, 'data': str(args.data), 'epochs': args.epochs,
                'imgsz': args.imgsz, 'batch': args.batch, 'weights_sha256': digest,
                'metrics': {k: float(v) for k, v in metrics.results_dict.items()},
                'trained_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    (target / 'train-manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(json.dumps(manifest['metrics'], indent=2))


if __name__ == '__main__':
    main()
