"""Optional image-level construction-stage classifier.

Stage predictions are advisory and are never used to select the active schedule
stage automatically. The schedule remains the source of truth for evaluation.
"""
import os
import hashlib
from threading import Lock
from dataclasses import dataclass
from pathlib import Path
from PIL import Image
from backend.app.services.detector import torch_device


class StageModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StagePrediction:
    model_version: str
    label: str
    confidence: float


class UltralyticsStageClassifier:
    def __init__(self, model_path: str):
        path = Path(model_path)
        if not path.is_file():
            raise StageModelUnavailable("STAGE_MODEL должен указывать на существующий локальный файл весов")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise StageModelUnavailable("Установите optional dependency ultralytics для классификатора этапов") from exc
        try:
            self.model = YOLO(str(path.resolve()))
            if self.model.task != "classify":
                raise ValueError("Нужна модель классификации этапов")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream,"sha256").hexdigest()
        except Exception as exc:
            raise StageModelUnavailable(f"Не удалось загрузить классификатор этапов: {exc}") from exc
        self.lock = Lock()
        self.model_version = f"ultralytics-classify:{digest}"

    def predict(self, image_path: Path) -> StagePrediction:
        try:
            with Image.open(image_path) as image, self.lock:
                result = self.model.predict(source=image.convert("RGB"), device=torch_device(), verbose=False)[0]
            if result.probs is None:
                raise ValueError("Модель не вернула классификацию этапа")
            class_id = int(result.probs.top1)
            label = str(result.names[class_id])
            return StagePrediction(model_version=self.model_version, label=label,
                                   confidence=float(result.probs.top1conf.item()))
        except Exception as exc:
            raise StageModelUnavailable(f"Ошибка классификации этапа: {exc}") from exc


def stage_classifier_from_environment():
    model_path = os.getenv("STAGE_MODEL")
    return UltralyticsStageClassifier(model_path) if model_path else None
