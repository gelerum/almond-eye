"""Common types for detector adapters.

Every adapter turns one image into `RawBox`es with a *canonical* label: an `Equipment` value,
`material:<Material>` or `None` (a model class we keep only for the scene overview, e.g. Person).
"""
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image

MATERIAL_PREFIX = "material:"


@dataclass(frozen=True)
class RawBox:
    source: str              # adapter name, e.g. "sitesense"
    label: str               # class name as the model reports it
    canonical: str | None    # Equipment value, "material:<name>" or None
    confidence: float
    bbox: tuple[float, float, float, float]  # normalised x1, y1, x2, y2


@dataclass
class AdapterResult:
    boxes: list[RawBox] = field(default_factory=list)
    # canonical material -> share of the frame covered (segmentation adapters only)
    coverage: dict[str, float] = field(default_factory=dict)


class Adapter(Protocol):
    name: str
    version: str

    def run(self, image: Image.Image) -> AdapterResult: ...


def normalised_box(x1: float, y1: float, x2: float, y2: float, width: int, height: int):
    """Clip to the frame and normalise; returns None for degenerate boxes."""
    x1, x2 = sorted((max(0.0, min(x1, width)), max(0.0, min(x2, width))))
    y1, y2 = sorted((max(0.0, min(y1, height)), max(0.0, min(y2, height))))
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return (x1 / width, y1 / height, x2 / width, y2 / height)
