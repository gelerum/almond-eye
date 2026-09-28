"""Fusion of boxes from several adapters into one set of canonical detections.

1. Boxes of the same canonical class that overlap (IoU >= iou) form a cluster; at most one box
   per source counts (the most confident one).
2. Cluster confidence is a noisy-OR of weighted source confidences, 1 - prod(1 - w_s * c_s), so
   two models agreeing on an object beat either of them alone. The box is the confidence-weighted
   mean of the members.
3. Mutually exclusive classes (e.g. mobile crane vs drilling rig on the same machine) are resolved
   by keeping the more confident fused box when they overlap (IoU >= conflict_iou). Classes listed
   in `conflict_priority` win regardless: they are CLIP-verified, while the closed-set model is
   known to call drilling rigs "mobile_crane".
4. Per-class thresholds drop what is left below the operating point.
"""
from dataclasses import dataclass, field

from backend.app.services.detectors.base import RawBox


@dataclass
class FusionConfig:
    iou: float = 0.5
    conflict_iou: float = 0.6
    source_weights: dict[str, float] = field(default_factory=dict)
    conflict_groups: list[list[str]] = field(default_factory=list)
    conflict_priority: list[str] = field(default_factory=list)
    min_confidence: dict[str, float] = field(default_factory=dict)
    default_min_confidence: float = 0.35

    def weight(self, source: str, label: str | None) -> float:
        """`source:label` overrides `source`: e.g. only CLIP-verified DINO classes get recalibrated."""
        return self.source_weights.get(f"{source}:{label}", self.source_weights.get(source, 1.0))

    def threshold(self, label: str) -> float:
        return self.min_confidence.get(label, self.default_min_confidence)


@dataclass(frozen=True)
class FusedBox:
    canonical: str
    confidence: float
    bbox: tuple[float, float, float, float]
    sources: tuple[str, ...]


def iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _merge(members: list[RawBox], config: FusionConfig) -> FusedBox:
    best_per_source: dict[str, RawBox] = {}
    for box in members:
        if box.source not in best_per_source or box.confidence > best_per_source[box.source].confidence:
            best_per_source[box.source] = box
    miss = 1.0
    weighted = [0.0, 0.0, 0.0, 0.0]
    total = 0.0
    for box in best_per_source.values():
        score = min(1.0, config.weight(box.source, box.canonical) * box.confidence)
        miss *= 1 - score
        for i in range(4):
            weighted[i] += box.bbox[i] * score
        total += score
    bbox = tuple(v / total for v in weighted) if total else members[0].bbox
    return FusedBox(members[0].canonical, 1 - miss, bbox, tuple(sorted(best_per_source)))


def fuse(boxes: list[RawBox], config: FusionConfig) -> list[FusedBox]:
    by_label: dict[str, list[RawBox]] = {}
    for box in boxes:
        if box.canonical:
            by_label.setdefault(box.canonical, []).append(box)
    fused: list[FusedBox] = []
    for members in by_label.values():
        clusters: list[list[RawBox]] = []
        for box in sorted(members, key=lambda b: -b.confidence):
            for cluster in clusters:
                if iou(cluster[0].bbox, box.bbox) >= config.iou:
                    cluster.append(box)
                    break
            else:
                clusters.append([box])
        fused += [_merge(cluster, config) for cluster in clusters]

    group_of = {label: i for i, group in enumerate(config.conflict_groups) for label in group}
    kept: list[FusedBox] = []
    priority = {label: i for i, label in enumerate(config.conflict_priority)}
    for box in sorted(fused, key=lambda b: (priority.get(b.canonical, len(priority)), -b.confidence)):
        group = group_of.get(box.canonical)
        if group is not None and any(
                group_of.get(other.canonical) == group and other.canonical != box.canonical
                and iou(other.bbox, box.bbox) >= config.conflict_iou for other in kept):
            continue
        kept.append(box)
    return [box for box in kept if box.confidence >= config.threshold(box.canonical)]
