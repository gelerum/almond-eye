import json

import pytest

from backend.app.domain.stage_inference import (UNKNOWN, detections_summary, infer_stages, load_signatures,
                                                match_plan_row, presence)
from backend.app.paths import MANIFESTS_DIR


def top(*equipment, **kwargs):
    return infer_stages(list(equipment), **kwargs)[0]


def test_signatures_are_consistent():
    sig = load_signatures()
    orders = [s["order"] for s in sig["stages"].values()]
    assert len(orders) == len(set(orders))
    for key in ("site_prep", "piling", "excavation", "foundation", "underground", "tower_crane_install",
                "superstructure", "facade_roof", "external_networks", "landscaping", "tower_crane_dismantle"):
        assert sig["stages"][key]["required"] and sig["stages"][key]["plan_keywords"]
    assert sig["stages"]["excavation"]["pace_critical"] == ["dump_truck"]


@pytest.mark.parametrize("equipment,stage", [
    (("excavator", "dump_truck"), "excavation"),
    (("excavator",), "excavation"),
    (("drilling_rig", "concrete_mixer"), "piling"),
    (("drilling_rig", "excavator"), "piling"),
    (("tower_crane",), "superstructure"),
    (("mobile_crane", "tower_crane"), "tower_crane_install"),
    (("concrete_pump",), "foundation"),
    (("tower_crane", "concrete_mixer", "excavator"), "underground"),
    (("roller", "dump_truck"), "landscaping"),
])
def test_combination_gives_stage(equipment, stage):
    assert top(*equipment)["stage_key"] == stage


def test_excavation_with_trucks_scores_higher_and_explains():
    full = top("excavator", "dump_truck", counts={"dump_truck": 2})
    alone = top("excavator")
    assert full["score"] > alone["score"] >= 0.5
    assert "экскаватор и самосвалы (2)" in full["explanation"] and "нет буровых установок" in full["explanation"]
    assert full["matched_required"] == ["excavator"] and full["missing_required"] == []
    assert "разработка котлована" in full["explanation"]


def test_materials_and_coverage_shift_stage():
    plain = {r["stage_key"]: r["score"] for r in infer_stages(["tower_crane"])}
    rich = {r["stage_key"]: r["score"] for r in infer_stages(["tower_crane"], material_coverage={"scaffolding": 0.2})}
    assert rich["superstructure"] > plain["superstructure"]
    assert presence(None, {"rebar": 0.4}, {"rebar": 0.05}, 0.1)["rebar"] == 0.5


def test_unknown_when_nothing_matches():
    assert top()["stage_key"] == UNKNOWN
    assert top("truck")["stage_key"] == UNKNOWN or top("truck")["score"] >= 0.3
    assert infer_stages(["drilling_rig"], signatures=None)[0]["stage_key"] == "piling"


def test_dismantle_needs_building_context():
    keys = [r["stage_key"] for r in infer_stages(["mobile_crane", "tower_crane"]) if r["score"] >= 0.5]
    assert "tower_crane_dismantle" not in keys
    ranked = infer_stages(["mobile_crane", "tower_crane"], ["scaffolding", "brickwork"])
    assert any(r["stage_key"] == "tower_crane_dismantle" and r["full_signature"] for r in ranked)


def test_confidence_lowers_score():
    sure = top("excavator")["score"]
    unsure = infer_stages({"excavator": 0.5})[0]["score"]
    assert unsure < sure


def test_detections_summary():
    best, counts = detections_summary([{"equipment": "dump_truck", "confidence": 0.4},
                                       {"equipment": "dump_truck", "confidence": 0.8}])
    assert best == {"dump_truck": 0.8} and counts == {"dump_truck": 2}


@pytest.mark.parametrize("name,key", [
    ("Устройство фундаментов и ростверков", "foundation"), ("Монтаж цокольного этажа", "underground"),
    ("Установка башенного крана", "tower_crane_install"), ("Демонтаж башенного крана", "tower_crane_dismantle"),
    ("Монтаж надземной части", "superstructure"), ("Устройство кровли", "facade_roof"),
    ("Установка окон", "facade_roof"), ("Наружные сети НВК и ТС", "external_networks"),
    ("Дорожные работы благоустройство и озеленение", "landscaping"), ("Ввод объекта", "commissioning"),
    ("Устройство котлована", "excavation"), ("Устройство шпунтового ограждения котлована", "piling"),
    ("Монтаж лифтовых шахт", "interior"), ("Что-то непонятное", None),
])
def test_plan_keywords(name, key):
    assert match_plan_row(name) == key


def test_ground_truth_accuracy_floor():
    """Guards the methodology against regressions: the ceiling on hand labels stays ≥ 0.7 top-1."""
    images = json.loads((MANIFESTS_DIR / "dgp_images.json").read_text(encoding="utf-8"))["images"]
    hits = sum(infer_stages(i["equipment"])[0]["stage_key"] == i["observed_stage"] for i in images)
    assert hits / len(images) >= 0.7
