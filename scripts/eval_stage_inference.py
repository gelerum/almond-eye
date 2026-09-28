"""Accuracy of stage inference (backend/app/domain/stage_inference.py) on the 100 labelled frames.

1. ground truth: the manifest's equipment lists (image-level presence) — the ceiling of the methodology;
2. detectors: cached ensemble output runtime/eval/raw.json (scripts/eval_detectors.py), if present,
   fused with the current fusion config.

    uv run --no-sync python scripts/eval_stage_inference.py [--json out.json] [--errors]
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.domain.stage_inference import UNKNOWN, detections_summary, infer_stages  # noqa: E402
from backend.app.paths import MANIFESTS_DIR, ROOT  # noqa: E402

RAW = ROOT / "runtime/eval/raw.json"


def score(images: list[dict], features: dict[str, tuple]) -> dict:
    top1 = top2 = 0
    per_stage = defaultdict(lambda: [0, 0])
    confusion = Counter()
    errors = []
    for image in images:
        equipment, materials, coverage, counts = features[image["file"]]
        ranked = [r for r in infer_stages(equipment, materials, coverage, counts) if r["stage_key"] != UNKNOWN]
        keys = [r["stage_key"] for r in ranked]
        predicted = keys[0] if keys and ranked[0]["score"] >= 0.3 else UNKNOWN
        truth = image["observed_stage"]
        top1 += predicted == truth
        top2 += truth in keys[:2]
        per_stage[truth][0] += predicted == truth
        per_stage[truth][1] += 1
        confusion[(truth, predicted)] += 1
        if predicted != truth:
            errors.append({"file": image["file"], "truth": truth, "predicted": predicted,
                           "equipment": sorted(equipment), "top": [(r["stage_key"], r["score"]) for r in ranked[:3]]})
    n = len(images)
    return {"n": n, "top1": round(top1 / n, 3), "top2": round(top2 / n, 3),
            "per_stage": {k: f"{v[0]}/{v[1]}" for k, v in sorted(per_stage.items(), key=lambda kv: -kv[1][1])},
            "confusion": {f"{t}→{p}": c for (t, p), c in confusion.most_common() if t != p}, "errors": errors}


def detector_features(images: list[dict]) -> dict | None:
    if not RAW.exists():
        return None
    from backend.app.services.detectors.base import MATERIAL_PREFIX, RawBox
    from backend.app.services.detectors.ensemble import fuse
    from backend.app.services.detectors.pipeline import fusion_config, load_config
    config = load_config()
    fusion = fusion_config(config)
    raw = json.loads(RAW.read_text(encoding="utf-8"))["raw"]
    features = {}
    for image in images:
        entry = raw.get(image["file"])
        if entry is None:
            continue
        boxes = [RawBox(b["source"], b["label"], b["canonical"], b["confidence"], tuple(b["bbox"]))
                 for b in entry["boxes"] if b.get("canonical")]
        fused = fuse(boxes, fusion)
        equipment = [{"equipment": b.canonical, "confidence": b.confidence} for b in fused
                     if not b.canonical.startswith(MATERIAL_PREFIX)]
        materials = [{"material": b.canonical[len(MATERIAL_PREFIX):], "confidence": b.confidence} for b in fused
                     if b.canonical.startswith(MATERIAL_PREFIX)]
        best, counts = detections_summary(equipment)
        mbest, _ = detections_summary(materials)
        coverage = {k[len(MATERIAL_PREFIX):]: v for k, v in (entry.get("coverage") or {}).items()
                    if k.startswith(MATERIAL_PREFIX) and v >= config.get("coverage_min", 0.02)}
        features[image["file"]] = (best, mbest, coverage, counts)
    return features


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, help="сохранить отчёт в JSON")
    parser.add_argument("--errors", action="store_true", help="напечатать ошибки по кадрам")
    args = parser.parse_args()
    images = json.loads((MANIFESTS_DIR / "dgp_images.json").read_text(encoding="utf-8"))["images"]
    report = {"ground_truth": score(images, {i["file"]: (i["equipment"], None, None, None) for i in images})}
    detected = detector_features(images)
    if detected:
        report["detectors"] = score([i for i in images if i["file"] in detected], detected)
    for mode, result in report.items():
        print(f"\n== {mode}: n={result['n']} top-1={result['top1']:.2f} top-2={result['top2']:.2f}")
        print("   по этапам (верно/всего):", ", ".join(f"{k} {v}" for k, v in result["per_stage"].items()))
        print("   частые ошибки:", ", ".join(f"{k} ×{v}" for k, v in list(result["confusion"].items())[:8]))
        if args.errors:
            for e in result["errors"]:
                print("   ", e["file"], e["truth"], "→", e["predicted"], e["equipment"], e["top"])
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
