"""Detector ensemble configured by backend/app/config/detectors.yaml.

Drop-in replacement for UltralyticsDetector: `detect(path) -> DetectionBatch`.
Adapters whose weights are missing and that are marked `optional` are skipped and reported in
`status()`, so the app still starts before the fine-tuned models are trained.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import yaml
from PIL import Image

from backend.app.domain.contracts import (Detection, Equipment, Material, MaterialDetection,
                                          ModelDetection)
from backend.app.paths import ROOT
from backend.app.services.detector import DetectionBatch, DetectorUnavailable, torch_device
from backend.app.services.detectors.base import MATERIAL_PREFIX, Adapter, RawBox
from backend.app.services.detectors.ensemble import FusionConfig, fuse

CONFIG = ROOT / "backend/app/config/detectors.yaml"


def _path(value: str) -> Path:
    path = Path(os.path.expandvars(value))
    return path if path.is_absolute() else ROOT / path


def build_adapter(name: str, spec: dict, device: str) -> Adapter:
    kind = spec["type"]
    if kind == "yolo":
        from backend.app.services.detectors.yolo import YoloAdapter
        return YoloAdapter(name, _path(spec["weights"]), spec.get("classes", {}), device=device,
                           conf=spec.get("conf", 0.25), iou=spec.get("iou", 0.6), imgsz=spec.get("imgsz", 640),
                           keep_unmapped=spec.get("keep_unmapped", False))
    if kind == "grounding_dino":
        from backend.app.services.detectors.grounding import GroundingDinoAdapter
        return GroundingDinoAdapter(name, _path(spec["model"]), spec["groups"], device=device,
                                    box_threshold=spec.get("box_threshold", 0.3),
                                    text_threshold=spec.get("text_threshold", 0.25))
    raise ValueError(f"Неизвестный тип адаптера {kind}")


def _weights_present(spec: dict) -> bool:
    return _path(spec.get("weights") or spec.get("model")).exists()


class DetectionPipeline:
    def __init__(self, config: dict, device: str | None = None, adapters: dict[str, Adapter] | None = None):
        self.config = config
        self.device = device or torch_device()
        self.skipped: dict[str, str] = {}
        if adapters is None:
            adapters = {}
            for name, spec in config["adapters"].items():
                if spec.get("enabled", True) is False:
                    self.skipped[name] = "disabled"
                    continue
                if not _weights_present(spec):
                    if spec.get("optional"):
                        self.skipped[name] = "weights missing"
                        continue
                    raise DetectorUnavailable(f"{name}: нет весов {spec.get('weights') or spec.get('model')}")
                try:
                    adapters[name] = build_adapter(name, spec, self.device)
                except Exception as exc:
                    if not spec.get("optional"):
                        raise DetectorUnavailable(f"{name}: {exc}") from exc
                    self.skipped[name] = f"load error: {exc}"
        if not adapters:
            raise DetectorUnavailable("Ни один детектор не загружен")
        self.verifier = None
        verify = config.get("verify")
        if verify and verify.get("enabled", True):
            try:
                from backend.app.services.detectors.clip_verify import ClipVerifier
                local = _path(verify["model"])
                model = str(local) if local.exists() else verify["model"]  # local dir or Hub id
                self.verifier = ClipVerifier(model, verify["descriptions"], device=self.device,
                                             sources=tuple(verify.get("sources", ["gdino"])),
                                             labels=tuple(verify.get("labels") or ()) or None,
                                             min_probability=verify.get("min_probability", 0.3),
                                             relabel=verify.get("relabel", False))
            except Exception as exc:
                raise DetectorUnavailable(f"CLIP-верификатор: {exc}") from exc
        self.adapters = adapters
        fusion = config.get("fusion", {})
        self.fusion = fusion_config(config)
        self.coverage_min = config.get("coverage_min", 0.02)
        self.supported_classes = sorted(Equipment(c) for c in config.get("validated_classes", []))
        identity = {"adapters": {n: a.version for n, a in adapters.items()}, "fusion": fusion,
                    "verifier": self.verifier.version if self.verifier else None,
                    "validated": self.supported_classes}
        self.model_version = "ensemble:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        self.available_classes = sorted({Equipment(label) for spec in config["adapters"].values()
                                         for label in _canonical_labels(spec) if label in Equipment.__members__})

    def status(self) -> dict:
        return {"model_version": self.model_version, "adapters": {n: a.version for n, a in self.adapters.items()},
                "verifier": self.verifier.version if self.verifier else None,
                "skipped": self.skipped, "device": self.device}

    def run_adapters(self, image: Image.Image) -> tuple[list[RawBox], dict[str, float], dict[str, float]]:
        boxes, coverage, timings = [], {}, {}
        for name, adapter in self.adapters.items():
            start = time.perf_counter()
            result = adapter.run(image)
            timings[name] = round(time.perf_counter() - start, 3)
            boxes += result.boxes
            for label, share in result.coverage.items():
                coverage[label] = max(coverage.get(label, 0.0), share)
        if self.verifier:
            start = time.perf_counter()
            boxes = self.verifier.verify(image, boxes)
            timings["clip_verify"] = round(time.perf_counter() - start, 3)
        return boxes, coverage, timings

    def detect(self, image_path: Path) -> DetectionBatch:
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGB")
            boxes, coverage, timings = self.run_adapters(image)
        except Exception as exc:
            raise DetectorUnavailable(f"Ошибка распознавания: {exc}") from exc
        self.last_timings = timings
        return self.to_batch(boxes, coverage)

    def to_batch(self, boxes: list[RawBox], coverage: dict[str, float]) -> DetectionBatch:
        detections, materials = [], []
        for box in fuse(boxes, self.fusion):
            if box.canonical.startswith(MATERIAL_PREFIX):
                materials.append(MaterialDetection(material=Material(box.canonical[len(MATERIAL_PREFIX):]),
                                                   confidence=round(box.confidence, 4), bbox=box.bbox,
                                                   sources=list(box.sources)))
            else:
                detections.append(Detection(equipment=Equipment(box.canonical), confidence=round(box.confidence, 4),
                                            bbox=box.bbox, sources=list(box.sources)))
        raw = [ModelDetection(label=b.label, confidence=round(b.confidence, 4), bbox=b.bbox, source=b.source)
               for b in boxes]
        material_coverage = {Material(label[len(MATERIAL_PREFIX):]): round(share, 4)
                             for label, share in coverage.items() if share >= self.coverage_min}
        floor = min([self.fusion.default_min_confidence, *self.fusion.min_confidence.values()])
        return DetectionBatch(model_version=self.model_version, supported_classes=self.supported_classes,
                              detections=detections, model_detections=raw, materials=materials,
                              material_coverage=material_coverage, detection_confidence_floor=floor)


def fusion_config(config: dict) -> FusionConfig:
    fusion = config.get("fusion", {})
    return FusionConfig(iou=fusion.get("iou", 0.5), conflict_iou=fusion.get("conflict_iou", 0.6),
                        source_weights=fusion.get("source_weights", {}),
                        conflict_groups=fusion.get("conflict_groups", []),
                        conflict_priority=fusion.get("conflict_priority", []),
                        min_confidence=dict(fusion.get("min_confidence") or {}),
                        default_min_confidence=fusion.get("default_min_confidence", 0.35))


def _canonical_labels(spec: dict) -> set[str]:
    if spec["type"] == "grounding_dino":
        return {c for group in spec["groups"] for c in group.values() if c}
    return {c for c in spec.get("classes", {}).values() if c}


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(Path(os.getenv("DETECTOR_CONFIG", path)).read_text(encoding="utf-8"))


def pipeline_from_config(path: Path | None = None) -> DetectionPipeline:
    return DetectionPipeline(load_config(path or CONFIG))
