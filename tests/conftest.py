import pytest


@pytest.fixture(autouse=True)
def isolated_model_env(monkeypatch):
    """Tests must not pick up the detector/GPU settings of the dev container (compose.yaml)."""
    for name in ('DETECTOR_PROFILE', 'DETECTOR_MODEL', 'DETECTOR_CLASS_MAP', 'DETECTOR_VALIDATED_CLASSES',
                 'STAGE_MODEL', 'TORCH_DEVICE', 'DETECTOR_PIPELINE'):
        monkeypatch.delenv(name, raising=False)
