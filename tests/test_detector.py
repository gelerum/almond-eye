"""Adapter contract tests; no weights download or fabricated accuracy claims."""
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.domain.contracts import Equipment
from backend.app.services.detector import DetectorUnavailable, UltralyticsDetector, detector_from_environment
from backend.app.services.stage_recognition import StageModelUnavailable, UltralyticsStageClassifier


class Scalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class Coordinates:
    def tolist(self):
        return [20, 10, 80, 50]


class ModelDouble:
    task = "detect"
    names = {0:"excavator", 1:"truck"}

    def __init__(self, _path):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        assert isinstance(kwargs['source'],Image.Image)
        box = SimpleNamespace(cls=[Scalar(0)], conf=[Scalar(.2)], xyxy=[Coordinates()])
        return [SimpleNamespace(names=self.names, boxes=[box])]


@pytest.fixture
def weights(tmp_path,monkeypatch):
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=ModelDouble))
    path=tmp_path/'model.pt';path.write_bytes(b'unit-test-weights')
    return path


def test_available_classes_are_not_assumed_validated(weights,tmp_path):
    adapter=UltralyticsDetector(str(weights),{e.value:e for e in Equipment})
    assert adapter.available_classes==[Equipment.excavator,Equipment.truck]
    assert adapter.supported_classes==[]
    image=tmp_path/'content-hash-without-extension'
    Image.new('RGB',(100,100),'white').save(image,format='PNG')
    batch=adapter.detect(image)
    assert batch.supported_classes==[]
    assert batch.detections[0].bbox==(.2,.1,.8,.5)
    assert batch.detections[0].confidence==.2
    assert adapter.model.calls[0]['conf']==.01


def test_missing_class_cannot_be_declared_supported(weights):
    with pytest.raises(DetectorUnavailable,match='Проверенные классы'):
        UltralyticsDetector(str(weights),{e.value:e for e in Equipment},[Equipment.dump_truck])


def test_model_identity_tracks_weights_mapping_and_validation(weights):
    mapping={'excavator':Equipment.excavator}
    a=UltralyticsDetector(str(weights),mapping)
    same=UltralyticsDetector(str(weights),mapping)
    validated=UltralyticsDetector(str(weights),mapping,[Equipment.excavator])
    remapped=UltralyticsDetector(str(weights),{'excavator':Equipment.bulldozer})
    weights.write_bytes(b'new-weights-same-filename')
    changed=UltralyticsDetector(str(weights),mapping)
    assert a.model_version==same.model_version
    assert len({a.model_version,validated.model_version,remapped.model_version,changed.model_version})==4


@pytest.mark.parametrize('raw',['[]','null','"excavator"','{}'])
def test_invalid_mapping_is_configuration_error(weights,monkeypatch,raw):
    monkeypatch.setenv('DETECTOR_MODEL',str(weights))
    monkeypatch.setenv('DETECTOR_CLASS_MAP',raw)
    with pytest.raises(DetectorUnavailable,match='JSON mapping'):
        detector_from_environment()


def test_unmapped_weights_and_missing_path(weights):
    with pytest.raises(DetectorUnavailable,match='нет классов'):
        UltralyticsDetector(str(weights),{'roller':Equipment.roller})
    with pytest.raises(DetectorUnavailable,match='локальный файл'):
        UltralyticsDetector(str(weights.parent/'missing.pt'),{})


def test_broken_inference_never_returns_empty_success(weights,tmp_path):
    adapter=UltralyticsDetector(str(weights),{'excavator':Equipment.excavator})
    path=tmp_path/'image';Image.new('RGB',(100,100)).save(path,format='PNG')
    adapter.model.predict=lambda **kwargs: []
    with pytest.raises(DetectorUnavailable,match='Ошибка распознавания'):
        adapter.detect(path)


def test_classification_uses_decoded_image_and_checks_task(weights,tmp_path,monkeypatch):
    with pytest.raises(StageModelUnavailable,match='классификации'):
        UltralyticsStageClassifier(str(weights))
    class ClassifierDouble(ModelDouble):
        task='classify'
        names={0:'excavation'}
        def predict(self,**kwargs):
            assert isinstance(kwargs['source'],Image.Image)
            return [SimpleNamespace(names=self.names,probs=SimpleNamespace(top1=0,top1conf=Scalar(.8)))]
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=ClassifierDouble))
    model=UltralyticsStageClassifier(str(weights))
    path=tmp_path/'image';Image.new('RGB',(100,100)).save(path,format='PNG')
    assert model.predict(path).label=='excavation'


def test_api_survives_invalid_detector_settings(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    monkeypatch.setenv('DETECTOR_MODEL',str(tmp_path/'missing.pt'))
    monkeypatch.setenv('STAGE_MODEL',str(tmp_path/'missing-cls.pt'))
    with TestClient(create_app(f'sqlite:///{tmp_path}/db',tmp_path/'runtime')) as client:
        health=client.get('/api/health').json()
        assert health['api']=='ready'
        assert health['detector']=='not_configured'
        assert health['stage_classifier']=='not_configured'
        assert health['detector_error']
        assert not any(item['supported'] for item in client.get('/api/equipment').json())


def test_hazard_profile_preserves_raw_classes_without_equipment_mapping(weights, tmp_path, monkeypatch):
    from backend.app.services.detector import HAZARD_LABELS
    class HazardDouble(ModelDouble):
        names = dict(enumerate(sorted(HAZARD_LABELS)))
    monkeypatch.setitem(sys.modules, 'ultralytics', SimpleNamespace(YOLO=HazardDouble))
    monkeypatch.setenv('DETECTOR_PROFILE', 'construction-hazard')
    monkeypatch.setenv('DETECTOR_MODEL', str(weights))
    monkeypatch.delenv('DETECTOR_CLASS_MAP', raising=False)
    monkeypatch.delenv('DETECTOR_VALIDATED_CLASSES', raising=False)
    adapter = detector_from_environment()
    path = tmp_path / 'image'
    Image.new('RGB', (100, 100)).save(path, format='PNG')
    batch = adapter.detect(path)
    assert not batch.detections and not batch.supported_classes
    assert batch.model_detections[0].label == 'Hardhat'
    assert batch.model_detections[0].bbox == (.2, .1, .8, .5)
    assert batch.detection_confidence_floor == .25
    monkeypatch.setenv('DETECTOR_CLASS_MAP', '{"machinery":"excavator"}')
    with pytest.raises(DetectorUnavailable, match='mapping'):
        detector_from_environment()


def test_hazard_profile_rejects_wrong_weights(weights):
    with pytest.raises(DetectorUnavailable, match='Набор классов'):
        UltralyticsDetector(str(weights), {}, profile='construction-hazard')
