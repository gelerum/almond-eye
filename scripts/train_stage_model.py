"""Train a project-specific image classifier for construction stages.

Dataset layout follows Ultralytics classification format:
  dataset/{train,val,test}/{stage_label}/*.jpg
Labels must be reviewed against the imported work catalog before use.
"""
import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", help="Dataset root with train/val/test class folders")
    parser.add_argument("--output", default="runtime/models/stages")
    parser.add_argument("--base", default="yolo11n-cls.pt")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default=os.getenv("TORCH_DEVICE", "cpu"), help="cpu, 0 или cuda:0")
    args = parser.parse_args()
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit('Install the detector extra first: uv sync --extra detector') from exc
    model = YOLO(args.base)
    model.train(data=args.dataset, epochs=args.epochs, imgsz=args.imgsz, device=args.device,
                project=args.output, name="stage-classifier")


if __name__ == "__main__":
    main()
