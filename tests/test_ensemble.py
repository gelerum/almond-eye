import pytest

from backend.app.domain.contracts import Equipment, Material
from backend.app.services.detectors.base import AdapterResult, RawBox, normalised_box
from backend.app.services.detectors.ensemble import FusionConfig, fuse, iou
from backend.app.services.detectors.grounding import GroundingDinoAdapter
from backend.app.services.detectors.pipeline import DetectionPipeline


def box(source, canonical, conf, bbox=(0.1, 0.1, 0.4, 0.5), label=None):
    return RawBox(source, label or canonical, canonical, conf, bbox)


def test_iou_and_normalisation():
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1
    assert iou((0, 0, .5, .5), (.5, .5, 1, 1)) == 0
    assert normalised_box(-5, 10, 50, 5, 100, 20) == (0, .25, .5, .5)
    assert normalised_box(10, 10, 10.5, 30, 100, 100) is None


def test_agreement_raises_confidence_and_keeps_sources():
    config = FusionConfig(default_min_confidence=0.1)
    [fused] = fuse([box('a', 'excavator', .6), box('b', 'excavator', .5, (0.12, 0.1, 0.42, 0.5))], config)
    assert fused.confidence == pytest.approx(1 - .4 * .5)
    assert fused.sources == ('a', 'b')


def test_one_box_per_source_in_a_cluster():
    config = FusionConfig(default_min_confidence=0.1)
    [fused] = fuse([box('a', 'excavator', .6), box('a', 'excavator', .5)], config)
    assert fused.confidence == pytest.approx(.6)


def test_separate_objects_stay_separate():
    config = FusionConfig(default_min_confidence=0.1)
    fused = fuse([box('a', 'excavator', .6), box('a', 'excavator', .7, (.6, .6, .9, .9))], config)
    assert len(fused) == 2


def test_conflicting_classes_keep_the_more_confident():
    config = FusionConfig(default_min_confidence=0.1, conflict_groups=[['mobile_crane', 'drilling_rig']])
    fused = fuse([box('a', 'mobile_crane', .5), box('b', 'drilling_rig', .8)], config)
    assert [f.canonical for f in fused] == ['drilling_rig']


def test_conflict_priority_beats_confidence():
    config = FusionConfig(default_min_confidence=0.1, conflict_groups=[['mobile_crane', 'drilling_rig']],
                          conflict_priority=['drilling_rig'])
    fused = fuse([box('a', 'mobile_crane', .9), box('b', 'drilling_rig', .3)], config)
    assert [f.canonical for f in fused] == ['drilling_rig']


def test_per_class_threshold_and_source_weight():
    config = FusionConfig(default_min_confidence=0.5, min_confidence={'truck': 0.2}, source_weights={'weak': 0.5})
    labels = {f.canonical for f in fuse([box('weak', 'excavator', .8), box('a', 'truck', .3, (.6, .6, .9, .9))], config)}
    assert labels == {'truck'}


def test_class_specific_source_weight_overrides_source_weight():
    config = FusionConfig(default_min_confidence=0.5, source_weights={'g': 0.85, 'g:drilling_rig': 2.0})
    fused = fuse([box('g', 'drilling_rig', .3), box('g', 'material:rebar', .5, (.6, .6, .9, .9))], config)
    assert [(f.canonical, round(f.confidence, 2)) for f in fused] == [('drilling_rig', 0.6)]


def test_dino_span_mapping_rejects_ambiguous_spans():
    adapter = GroundingDinoAdapter.__new__(GroundingDinoAdapter)
    group = {'dump truck': 'dump_truck', 'flatbed truck': 'truck', 'excavator': 'excavator', 'truck crane': 'mobile_crane'}
    assert adapter._canonical('excavator', group) == ('excavator', 'excavator')
    assert adapter._canonical('yellow dump truck', group) == ('dump truck', 'dump_truck')
    assert adapter._canonical('truck', group) is None
    assert adapter._canonical('excav', group) == ('excavator', 'excavator')


class StaticAdapter:
    name, version = 'static', 'static-v1'

    def __init__(self, result):
        self.result = result

    def run(self, image):
        return self.result


def test_pipeline_splits_equipment_materials_and_raw(tmp_path):
    from PIL import Image
    image = tmp_path / 'frame'
    Image.new('RGB', (64, 48)).save(image, format='PNG')
    result = AdapterResult(
        boxes=[box('static', 'excavator', .9), box('static', 'material:rebar', .8, (.5, .5, .9, .9)),
               box('static', None, .9, label='Person')],
        coverage={'material:concrete': 0.4, 'material:steel': 0.01})
    config = {'adapters': {}, 'fusion': {'default_min_confidence': 0.3}, 'coverage_min': 0.03,
              'validated_classes': ['excavator']}
    batch = DetectionPipeline(config, device='cpu', adapters={'static': StaticAdapter(result)}).detect(image)
    assert [d.equipment for d in batch.detections] == [Equipment.excavator]
    assert [m.material for m in batch.materials] == [Material.rebar]
    assert batch.material_coverage == {Material.concrete: 0.4}
    assert {m.label for m in batch.model_detections} == {'excavator', 'material:rebar', 'Person'}
    assert batch.supported_classes == [Equipment.excavator]
