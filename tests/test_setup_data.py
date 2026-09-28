import hashlib
import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location('setup_data', ROOT / 'scripts/setup_data.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_from_nested_zip(tmp_path, monkeypatch):
    setup = load_script()
    dgp = tmp_path / 'dgp'
    monkeypatch.setattr(setup, 'DGP_DIR', dgp)
    monkeypatch.setattr(setup, 'DGP_IMAGES', dgp / 'images')
    monkeypatch.setattr(setup, 'WORK_CATALOG_XLSX', dgp / 'Сводный перечень строительных работ_ЛТЦ.xlsx')
    (dgp / 'images').mkdir(parents=True)
    inner = tmp_path / 'Строительная_техника.zip'
    with zipfile.ZipFile(inner, 'w') as archive:
        archive.writestr('Строительная_техника/Screenshot_1.png', b'one')
        archive.writestr('Строительная_техника/other.png', b'skip')
    folder = tmp_path / 'organiser'
    folder.mkdir()
    (folder / 'Строительная_техника.zip').write_bytes(inner.read_bytes())
    (folder / 'Сводный перечень строительных работ_ЛТЦ.xlsx').write_bytes(b'xlsx')

    assert setup.collect(folder, {'Screenshot_1.png'}) == 2
    assert (dgp / 'images/Screenshot_1.png').read_bytes() == b'one'
    assert not (dgp / 'images/other.png').exists()
    assert (dgp / 'Сводный перечень строительных работ_ЛТЦ.xlsx').exists()
    # Idempotent: nothing is copied twice.
    assert setup.collect(folder, {'Screenshot_1.png'}) == 0


def test_reference_hashes_cover_the_labelled_frames():
    import json
    setup = load_script()
    hashes = setup.expected_hashes()
    labels = json.loads((ROOT / 'data/manifests/dgp_images.json').read_text(encoding='utf-8'))
    assert {item['file'] for item in labels['images']} == set(hashes)
    assert all(len(h) == 64 and int(h, 16) >= 0 for h in hashes.values())
    assert hashlib.sha256(b'').hexdigest() not in hashes.values()
