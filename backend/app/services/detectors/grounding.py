"""Zero-shot Grounding DINO adapter (transformers).

Used for classes no available closed-set model covers: drilling rigs, crawler cranes, loader
cranes, concrete pumps and site materials. Prompts are grouped: DINO degrades when one text query
lists too many phrases, so each group is a separate forward pass.
"""
import hashlib
import json
from pathlib import Path
from threading import Lock

import torch
from PIL import Image

from backend.app.services.detectors.base import AdapterResult, RawBox, normalised_box


class GroundingDinoAdapter:
    def __init__(self, name: str, model_dir: Path, prompt_groups: list[dict[str, str | None]], *,
                 device: str = "cpu", box_threshold: float = 0.3, text_threshold: float = 0.25):
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.name = name
        self.device = f"cuda:{device}" if str(device).isdigit() else device
        self.processor = AutoProcessor.from_pretrained(model_dir)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(model_dir)
        self.model.to(self.device).eval()
        # fp16 autocast on GPU: ~2x faster; a plain .half() breaks DINO's mixed text/image layers.
        self.autocast = self.device.startswith("cuda")
        self.groups = prompt_groups
        self.box_threshold, self.text_threshold = box_threshold, text_threshold
        self.lock = Lock()
        config = json.dumps({"groups": prompt_groups, "box": box_threshold, "text": text_threshold}, sort_keys=True)
        weights = Path(model_dir) / "model.safetensors"
        digest = hashlib.sha256(weights.read_bytes()[:1 << 20]).hexdigest()[:12] if weights.exists() else "hub"
        self.version = f"gdino:{name}:{digest}:{hashlib.sha256(config.encode()).hexdigest()[:8]}"

    def _canonical(self, phrase: str, group: dict[str, str | None]) -> tuple[str, str | None] | None:
        """DINO returns the matched *text span*; map it back to the longest prompt it belongs to."""
        phrase = phrase.strip().lower()
        if phrase in group:
            return phrase, group[phrase]
        # A span containing a full prompt ("yellow drilling rig") is unambiguous: take the longest.
        contained = [p for p in group if p in phrase]
        if contained:
            prompt = max(contained, key=len)
            return prompt, group[prompt]
        # A partial span ("truck") is kept only if every prompt it may come from means the same class.
        candidates = [p for p in group if phrase and phrase in p]
        if candidates and len({group[p] for p in candidates}) == 1:
            return max(candidates, key=len), group[candidates[0]]
        return None

    @torch.inference_mode()
    def run(self, image: Image.Image) -> AdapterResult:
        width, height = image.size
        out = AdapterResult()
        for group in self.groups:
            text = ". ".join(group) + "."
            with self.lock:
                inputs = self.processor(images=image, text=text, return_tensors="pt").to(self.device)
                with torch.autocast("cuda", dtype=torch.float16, enabled=self.autocast):
                    outputs = self.model(**inputs)
                result = self.processor.post_process_grounded_object_detection(
                    outputs, inputs["input_ids"], threshold=self.box_threshold,
                    text_threshold=self.text_threshold, target_sizes=[(height, width)])[0]
            labels = result.get("text_labels", result.get("labels"))
            for box, score, phrase in zip(result["boxes"].float().tolist(), result["scores"].float().tolist(), labels):
                match = self._canonical(str(phrase), group)
                bbox = normalised_box(*box, width, height)
                if match is None or bbox is None:
                    continue
                # Near-full-frame boxes are DINO's typical "whole scene" false positives.
                if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 0.9:
                    continue
                out.boxes.append(RawBox(self.name, match[0], match[1], float(score), bbox))
        return out
