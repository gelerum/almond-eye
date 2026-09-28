"""Optional real-image detector adapter.

The adapter is deliberately separate from evaluation so model output cannot be
confused with fixture observations.
"""
import json
import os
import hashlib
from threading import Lock
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from backend.app.domain.contracts import Detection, Equipment, ModelDetection

HAZARD_LABELS = {"Hardhat", "Mask", "NO-Hardhat", "NO-Mask", "NO-Safety Vest",
                 "Person", "Safety Cone", "Safety Vest", "machinery", "utility pole", "vehicle"}
HAZARD_MODEL = Path(__file__).resolve().parents[3] / "runtime/models/construction-hazard/yolo11n.pt"


class DetectorUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectionBatch:
    model_version: str
    supported_classes: list[Equipment]
    detections: list[Detection]
    detection_confidence_floor: float = 0
    model_detections: list[ModelDetection] = field(default_factory=list)


class UltralyticsDetector:
    def __init__(self, model_path: str, class_map: dict[str, Equipment],
                 validated_classes: list[Equipment] | None = None, profile: str = "generic"):
        path = Path(model_path)
        if not path.is_file():
            raise DetectorUnavailable("DETECTOR_MODEL должен указывать на существующий локальный файл весов")
        try:
            os.environ.setdefault("YOLO_CONFIG_DIR", str(HAZARD_MODEL.parents[2]))
            from ultralytics import YOLO
        except ImportError as exc:
            raise DetectorUnavailable("Установите optional dependency ultralytics для реального детектора") from exc
        except Exception as exc:
            raise DetectorUnavailable(f"Не удалось инициализировать Ultralytics: {exc}") from exc
        self.model_path = str(path.resolve())
        self.lock = Lock()
        try:
            self.model = YOLO(self.model_path)
            if self.model.task != "detect":
                raise ValueError("Нужна модель обнаружения объектов (detect)")
            names = self.model.names
            names = set(names.values() if isinstance(names, dict) else names)
            self.model_classes = sorted(names)
            self.profile = profile
            if profile == "construction-hazard" and names not in (HAZARD_LABELS, HAZARD_LABELS - {"utility pole"}):
                raise ValueError("Набор классов не соответствует Construction Hazard Detection")
            self.class_map = {name: Equipment(value) for name,value in class_map.items() if name in names}
            if not self.class_map and profile != "construction-hazard":
                raise ValueError("В весах нет классов из DETECTOR_CLASS_MAP")
            self.available_classes = sorted(set(self.class_map.values()))
            self.supported_classes = sorted(set(validated_classes or []))
            if not set(self.supported_classes) <= set(self.available_classes):
                raise ValueError("Проверенные классы отсутствуют в весах или mapping")
            with path.open("rb") as stream:
                weights_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        except Exception as exc:
            raise DetectorUnavailable(f"Не удалось загрузить детектор: {exc}") from exc
        self.inference_config = {"device":"cpu", "conf":0.01, "iou":0.7, "imgsz":640, "max_det":300}
        if profile == "construction-hazard":
            self.inference_config["conf"] = 0.25
        identity = {"weights_sha256":weights_hash, "class_map":self.class_map,
                    "validated_classes":self.supported_classes, "inference":self.inference_config,
                    "profile":profile, "adapter_version":2}
        self.model_version = "ultralytics:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def detect(self, image_path: Path) -> DetectionBatch:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                # Content-addressed storage has no extension. PIL also preserves
                # the raw pixel coordinate system used by the saved zone.
                with self.lock:
                    results = self.model.predict(source=image.convert("RGB"), verbose=False, **self.inference_config)
            if len(results) != 1 or results[0].boxes is None:
                raise ValueError("Детектор не вернул рамки для одного снимка")
            if len(results[0].boxes) >= self.inference_config["max_det"]:
                raise ValueError("Достигнут лимит детекций; полнота результата не подтверждена")
            detections = []
            model_detections = []
            for box in results[0].boxes:
                class_id = int(box.cls[0].item())
                model_name = str(results[0].names[class_id])
                equipment = self.class_map.get(model_name)
                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
                model_detections.append(ModelDetection(label=model_name, confidence=float(box.conf[0].item()),
                                                      bbox=(x1 / width, y1 / height, x2 / width, y2 / height)))
                if equipment is None:
                    continue
                detections.append(Detection(equipment=equipment, confidence=float(box.conf[0].item()),
                                            bbox=(x1 / width, y1 / height, x2 / width, y2 / height)))
        except Exception as exc:
            raise DetectorUnavailable(f"Ошибка распознавания: {exc}") from exc
        return DetectionBatch(model_version=self.model_version,
                              supported_classes=self.supported_classes,
                              detections=detections, model_detections=model_detections,
                              detection_confidence_floor=self.inference_config["conf"])


def detector_from_environment():
    profile = os.getenv("DETECTOR_PROFILE", "generic")
    if profile not in {"generic", "construction-hazard"}:
        raise DetectorUnavailable("Неизвестный DETECTOR_PROFILE")
    model_path = os.getenv("DETECTOR_MODEL")
    if not model_path and profile == "construction-hazard":
        model_path = str(HAZARD_MODEL)
    if not model_path:
        return None
    raw_mapping = os.getenv("DETECTOR_CLASS_MAP", "")
    if profile == "construction-hazard" and raw_mapping:
        raise DetectorUnavailable("Construction Hazard Detection не поддерживает mapping на типы техники")
    if raw_mapping:
        try:
            configured = json.loads(raw_mapping)
            if not isinstance(configured, dict) or not configured:
                raise ValueError("Ожидался непустой объект")
            class_map = {name: Equipment(value) for name, value in configured.items()}
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise DetectorUnavailable("DETECTOR_CLASS_MAP должен быть JSON mapping имя-модели → класс") from exc
    else:
        class_map = {} if profile == "construction-hazard" else {equipment.value: equipment for equipment in Equipment}
    try:
        validated = json.loads(os.getenv("DETECTOR_VALIDATED_CLASSES", "[]"))
        if not isinstance(validated, list):
            raise ValueError("Ожидался список")
        validated = [Equipment(value) for value in validated]
    except (ValueError, TypeError) as exc:
        raise DetectorUnavailable("DETECTOR_VALIDATED_CLASSES должен быть JSON-списком проверенных классов") from exc
    return UltralyticsDetector(model_path, class_map, validated, profile=profile)
