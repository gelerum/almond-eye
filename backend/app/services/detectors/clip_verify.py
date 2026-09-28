"""CLIP re-classification of zero-shot proposals.

Grounding DINO reliably finds "a tall boom on tracks" but cannot tell a drilling rig from a crawler
or truck crane, and prompted alone it finds *something* in almost every frame. CLIP sees only the
crop and picks among close machine types; a proposal survives only if CLIP agrees with a class we
accept, and its confidence becomes DINO score x CLIP probability.
"""
from threading import Lock

import torch
from PIL import Image

from backend.app.services.detectors.base import RawBox


class ClipVerifier:
    def __init__(self, model_id: str, descriptions: dict[str, str], *, device: str = "cpu",
                 sources: tuple[str, ...] = ("gdino",), labels: tuple[str, ...] | None = None,
                 min_probability: float = 0.3, relabel: bool = False, padding: float = 0.1):
        from transformers import CLIPModel, CLIPProcessor
        self.device = f"cuda:{device}" if str(device).isdigit() else device
        self.model = CLIPModel.from_pretrained(model_id).to(self.device).eval()
        self.processor = CLIPProcessor.from_pretrained(model_id)
        self.names = list(descriptions)
        with torch.inference_mode():
            text = self.processor(text=list(descriptions.values()), return_tensors="pt", padding=True).to(self.device)
            features = self.model.get_text_features(**text)
        self.text = features / features.norm(dim=-1, keepdim=True)
        self.sources, self.labels = set(sources), set(labels) if labels else None
        self.min_probability, self.relabel, self.padding = min_probability, relabel, padding
        self.lock = Lock()
        self.version = f"clip:{model_id}:p{min_probability}:{'relabel' if relabel else 'keep'}"

    def applies(self, box: RawBox) -> bool:
        return box.source in self.sources and bool(box.canonical) and (self.labels is None or box.canonical in self.labels)

    @torch.inference_mode()
    def probabilities(self, image: Image.Image, boxes: list[RawBox]) -> list[dict[str, float]]:
        if not boxes:
            return []
        width, height = image.size
        crops = []
        for box in boxes:
            x1, y1, x2, y2 = box.bbox
            pad_x, pad_y = (x2 - x1) * self.padding, (y2 - y1) * self.padding
            crops.append(image.crop((max(0.0, x1 - pad_x) * width, max(0.0, y1 - pad_y) * height,
                                     min(1.0, x2 + pad_x) * width, min(1.0, y2 + pad_y) * height)))
        with self.lock:
            inputs = self.processor(images=crops, return_tensors="pt").to(self.device)
            features = self.model.get_image_features(**inputs)
        features = features / features.norm(dim=-1, keepdim=True)
        probs = (100 * features @ self.text.T).softmax(-1).tolist()
        return [dict(zip(self.names, row)) for row in probs]

    def verify(self, image: Image.Image, boxes: list[RawBox]) -> list[RawBox]:
        targets = [b for b in boxes if self.applies(b)]
        verdicts = dict(zip(map(id, targets), self.probabilities(image, targets)))
        out = []
        for box in boxes:
            probs = verdicts.get(id(box))
            if probs is None:
                out.append(box)
                continue
            winner = max(probs, key=probs.get)
            if winner == box.canonical and probs[winner] >= self.min_probability:
                out.append(RawBox(box.source, box.label, box.canonical, box.confidence * probs[winner], box.bbox))
            elif self.relabel and winner in (self.labels or ()) and probs[winner] >= self.min_probability:
                out.append(RawBox(box.source, f"{box.label}→{winner}", winner, box.confidence * probs[winner], box.bbox))
        return out
