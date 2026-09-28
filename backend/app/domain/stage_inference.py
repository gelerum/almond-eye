"""Stage of works from the combination of equipment and materials in a frame.

Pure functions over the reviewable methodology in backend/app/rules/stage_signatures.yaml.
The score is explained in Russian so a site engineer can check every conclusion.
"""
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

import yaml

from backend.app.domain.contracts import LABELS, MATERIAL_LABELS, Equipment, Material

SIGNATURES = Path(__file__).resolve().parents[1] / "rules/stage_signatures.yaml"
UNKNOWN = "unknown"
FEATURE_LABELS = {**{e.value: l for e, l in LABELS.items()}, **{m.value: l for m, l in MATERIAL_LABELS.items()}}
# Короткие имена для объяснений («вижу экскаватор и 2 самосвала»).
SHORT = {"dump_truck": ("самосвал", "самосвалы"), "excavator": ("экскаватор", "экскаваторы"),
         "roller": ("каток", "катки"), "loader_crane": ("кран-манипулятор", "краны-манипуляторы"),
         "concrete_mixer": ("бетоносмеситель", "бетоносмесители"), "bulldozer": ("бульдозер", "бульдозеры"),
         "truck": ("грузовик", "грузовики"), "mobile_crane": ("автокран", "автокраны"),
         "tower_crane": ("башенный кран", "башенные краны"), "crawler_crane": ("гусеничный кран", "гусеничные краны"),
         "drilling_rig": ("буровая установка", "буровые установки"), "wheel_loader": ("погрузчик", "погрузчики"),
         "concrete_pump": ("бетононасос", "бетононасосы"), "rebar": ("арматура", "арматура"),
         "formwork": ("опалубка", "опалубка"), "scaffolding": ("леса", "леса"), "pipes": ("трубы", "трубы"),
         "precast_slabs": ("ж/б плиты", "ж/б плиты"), "brickwork": ("кладка", "кладка"),
         "concrete": ("бетон", "бетон"), "steel": ("металлоконструкции", "металлоконструкции"),
         "metal_deck": ("профнастил", "профнастил")}
FEATURES = {e.value for e in Equipment} | {m.value for m in Material}


def load_signatures(path: Path | str | None = None) -> dict:
    return _load(str(path or SIGNATURES))


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for key, stage in data["stages"].items():
        stage.setdefault("observable", True)
        for field in ("supporting", "contradicting"):
            stage[field] = dict(stage.get(field) or {})
        for field in ("required", "allowed", "pace_critical", "completed_by", "plan_keywords"):
            stage[field] = list(stage.get(field) or [])
        names = ([f for g in stage["required"] for f in g] + list(stage["supporting"]) +
                 list(stage["contradicting"]) + stage["allowed"] + stage["pace_critical"] + stage["completed_by"])
        unknown = set(names) - FEATURES
        if unknown:
            raise ValueError(f"{key}: неизвестные признаки {sorted(unknown)}")
        if stage["observable"] and not stage["required"]:
            raise ValueError(f"{key}: у наблюдаемого этапа нужны required-группы")
    return data


def short(feature: str, count: int = 1) -> str:
    one, many = SHORT.get(feature, (FEATURE_LABELS.get(feature, feature).lower(),) * 2)
    return f"{many} ({count})" if count > 1 else one


# Родительный падеж множественного числа для «нет …» / «без …».
GENITIVE = {"dump_truck": "самосвалов", "concrete_mixer": "бетоносмесителей", "concrete_pump": "бетононасосов",
            "excavator": "экскаваторов", "drilling_rig": "буровых установок", "tower_crane": "башенного крана",
            "mobile_crane": "автокрана", "crawler_crane": "гусеничного крана", "wheel_loader": "погрузчиков",
            "bulldozer": "бульдозеров", "roller": "катков", "loader_crane": "крана-манипулятора",
            "truck": "грузовиков", "rebar": "арматуры", "formwork": "опалубки", "scaffolding": "лесов",
            "pipes": "труб", "precast_slabs": "ж/б плит", "brickwork": "кладки", "concrete": "бетона",
            "steel": "металлоконструкций", "metal_deck": "профнастила"}


def genitive(feature: str) -> str:
    return GENITIVE.get(feature, short(feature))


def presence(equipment: Mapping[str, float] | Iterable[str] | None = None,
             materials: Mapping[str, float] | Iterable[str] | None = None,
             material_coverage: Mapping[str, float] | None = None, coverage_full: float = 0.10) -> dict[str, float]:
    """Feature -> presence 0..1. Mappings carry the best confidence; plain iterables mean «виден наверняка»."""
    def as_map(values):
        if values is None:
            return {}
        if isinstance(values, Mapping):
            return {str(k): float(v) for k, v in values.items()}
        return {str(v): 1.0 for v in values}
    result = as_map(equipment)
    for name, value in as_map(materials).items():
        result[name] = max(result.get(name, 0.0), value)
    for name, share in (material_coverage or {}).items():
        name = str(name)
        result[name] = max(result.get(name, 0.0), min(1.0, float(share) / coverage_full))
    return {k: min(1.0, max(0.0, v)) for k, v in result.items() if v > 0}


def detections_summary(detections: Iterable) -> tuple[dict[str, float], dict[str, int]]:
    """Detection-like objects/dicts -> (best confidence per class, box count per class)."""
    best, counts = {}, {}
    for d in detections:
        get = d.get if isinstance(d, Mapping) else (lambda k, d=d: getattr(d, k, None))
        name = str(get("equipment") or get("material"))
        best[name] = max(best.get(name, 0.0), float(get("confidence")))
        counts[name] = counts.get(name, 0) + 1
    return best, counts


def score_stage(key: str, stage: dict, present: dict[str, float], required_weight: float) -> dict:
    groups = stage["required"]
    matched, missing = [], []
    closure = 0.0
    for group in groups:
        best = max(group, key=lambda f: present.get(f, 0.0))
        if present.get(best, 0.0) > 0:
            matched.append(best)
            closure += present[best]
        else:
            missing.append(list(group))
    supporting = {f: w for f, w in stage["supporting"].items() if present.get(f, 0.0) > 0}
    contradicting = {f: w for f, w in stage["contradicting"].items() if present.get(f, 0.0) > 0}
    raw = (required_weight * closure / len(groups) + sum(w * present[f] for f, w in supporting.items())
           - sum(w * present[f] for f, w in contradicting.items()))
    return {"stage_key": key, "name": stage["name"], "score": round(min(1.0, max(0.0, raw)), 3),
            "full_signature": not missing, "matched_required": matched, "missing_required": missing,
            "supporting_seen": sorted(supporting, key=lambda f: -supporting[f]),
            "contradicting_seen": sorted(contradicting, key=lambda f: -contradicting[f])}


def explain(result: dict, stage: dict, present: dict[str, float], counts: Mapping[str, int]) -> str:
    seen = list(dict.fromkeys(result["matched_required"] + result["supporting_seen"]))
    parts = []
    if seen:
        items = [short(f, counts.get(f, 1)) for f in seen]
        parts.append("вижу " + (", ".join(items[:-1]) + " и " + items[-1] if len(items) > 1 else items[0]))
    for group in result["missing_required"]:
        parts.append("нет " + " / ".join(genitive(f) for f in group))
    if result["contradicting_seen"]:
        parts.append("но видна " + ", ".join(short(f, counts.get(f, 1)) for f in result["contradicting_seen"]))
    else:
        # Самый сильный отсутствующий «противник» отличает этап от соседнего: «нет буровых → котлован».
        absent = [f for f in sorted(stage["contradicting"], key=lambda f: -stage["contradicting"][f])
                  if f in {e.value for e in Equipment} and f not in present]
        if absent and seen:
            parts.append("нет " + genitive(absent[0]))
    return "; ".join(parts) + f" → {stage['name'].lower()}, уверенность {result['score']:.2f}"


def infer_stages(equipment: Mapping[str, float] | Iterable[str] | None = None,
                 materials: Mapping[str, float] | Iterable[str] | None = None,
                 material_coverage: Mapping[str, float] | None = None,
                 counts: Mapping[str, int] | None = None, signatures: dict | None = None,
                 limit: int | None = None) -> list[dict]:
    """Ranked stages for one frame. The first item is `unknown` when no stage reaches min_score."""
    sig = signatures or load_signatures()
    present = presence(equipment, materials, material_coverage, sig.get("material_coverage_full", 0.10))
    counts = {k: v for k, v in (counts or {}).items()}
    ranked = []
    for key, stage in sig["stages"].items():
        if not stage["observable"]:
            continue
        result = score_stage(key, stage, present, sig.get("required_weight", 0.6))
        if result["score"] <= 0:
            continue
        result["explanation"] = explain(result, stage, present, counts)
        ranked.append(result)
    order = {k: s["order"] for k, s in sig["stages"].items()}
    # Ничья: сначала полный почерк, потом более ранний этап (в сомнении не приписываем прогресс).
    ranked.sort(key=lambda r: (-r["score"], not r["full_signature"], order[r["stage_key"]]))
    if not ranked or ranked[0]["score"] < sig.get("min_score", 0.3):
        seen = ", ".join(short(f, counts.get(f, 1)) for f in present) or "техника этапов не обнаружена"
        ranked.insert(0, {"stage_key": UNKNOWN, "name": "Этап не распознан",
                          "score": 0.0,
                          "full_signature": False, "matched_required": [], "missing_required": [],
                          "supporting_seen": [], "contradicting_seen": [],
                          "explanation": f"{seen[:1].upper() + seen[1:]}: сочетание не соответствует почерку ни одного этапа"})
    return ranked[:limit] if limit else ranked


def top_stage(ranked: list[dict]) -> tuple[str, float]:
    if not ranked:
        return UNKNOWN, 0.0
    return ranked[0]["stage_key"], ranked[0]["score"]


def match_plan_row(name: str, signatures: dict | None = None) -> str | None:
    """Stage key for a plan / work-catalogue row by the longest matching keyword."""
    text = " ".join(str(name).lower().replace("ё", "е").split())
    best, length = None, 0
    for key, stage in (signatures or load_signatures())["stages"].items():
        for word in stage["plan_keywords"]:
            word = word.lower().replace("ё", "е")
            if word in text and len(word) > length:
                best, length = key, len(word)
    return best
