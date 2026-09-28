"""Ultralytics adapter for detection and instance-segmentation weights."""
import hashlib
import os
from pathlib import Path
from threading import Lock

import numpy as np
from PIL import Image

from backend.app.services.detectors.base import AdapterResult, RawBox, normalised_box


class YoloAdapter:
    def __init__(self, name: str, weights: Path, class_map: dict[str, str | None], *, device: str = "cpu",
                 conf: float = 0.25, iou: float = 0.6, imgsz: int = 640, keep_unmapped: bool = False):
        os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(weights).resolve().parents[2]))
        from ultralytics import YOLO
        self.name = name
        self.model = YOLO(str(weights))
        if self.model.task not in {"detect", "segment"}:
            raise ValueError(f"{name}: нужна модель detect или segment, получено {self.model.task}")
        names = self.model.names
        self.model_classes = set(names.values() if isinstance(names, dict) else names)
        unknown = set(class_map) - self.model_classes
        if unknown:
            raise ValueError(f"{name}: в весах нет классов {sorted(unknown)}")
        self.class_map = class_map
        self.keep_unmapped = keep_unmapped
        self.settings = {"device": device, "conf": conf, "iou": iou, "imgsz": imgsz, "max_det": 300}
        self.lock = Lock()
        with open(weights, "rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        self.version = f"yolo:{name}:{digest[:12]}:conf{conf}:img{imgsz}"

    def run(self, image: Image.Image) -> AdapterResult:
        width, height = image.size
        with self.lock:
            result = self.model.predict(source=image, verbose=False, **self.settings)[0]
        out = AdapterResult()
        if result.boxes is None:
            return out
        masks = result.masks.data.cpu().numpy() if result.masks is not None else None
        union: dict[str, np.ndarray] = {}
        for i, box in enumerate(result.boxes):
            label = str(result.names[int(box.cls[0].item())])
            if label not in self.class_map and not self.keep_unmapped:
                continue
            canonical = self.class_map.get(label)
            bbox = normalised_box(*box.xyxy[0].tolist(), width, height)
            if bbox is None:
                continue
            out.boxes.append(RawBox(self.name, label, canonical, float(box.conf[0].item()), bbox))
            if masks is not None and canonical:
                # Masks are at letterboxed model resolution, so padding slightly lowers the share;
                # good enough for a coarse "how much of the frame is concrete/steel" cue.
                mask = masks[i] > 0.5
                union[canonical] = mask if canonical not in union else (union[canonical] | mask)
        out.coverage = {label: float(mask.mean()) for label, mask in union.items()}
        return out
