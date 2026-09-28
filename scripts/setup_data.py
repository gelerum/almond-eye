"""Put the organiser's materials into data/raw/dgp and verify them.

The hackathon materials are not redistributed with the repository. Get them from the organiser
(archive «7.ДГП_датасеты»: folder or zip «Строительная_техника» with 100 PNG screenshots and
«Сводный перечень строительных работ_ЛТЦ.xlsx»), then run:

  uv run scripts/setup_data.py --source ~/Downloads/7.ДГП_датасеты          # folder
  uv run scripts/setup_data.py --source ~/Downloads/Строительная_техника.zip  # zip
  uv run scripts/setup_data.py --source a.zip --source b.xlsx                 # several sources

Every image is checked against the SHA-256 recorded in data/manifests/input-audit.json, so the
labels in data/manifests/dgp_images.json are guaranteed to refer to the same pixels.
"""
import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.paths import DGP_DIR, DGP_IMAGES, MANIFESTS_DIR, WORK_CATALOG_XLSX  # noqa: E402

LINKS = 'Ссылки на открыте датасеты.txt'


def expected_hashes() -> dict[str, str]:
    audit = json.loads((MANIFESTS_DIR / 'input-audit.json').read_text(encoding='utf-8'))
    return {Path(item['file']).name: item['sha256'] for item in audit['images']}


def collect(source: Path, expected: set[str]) -> int:
    """Copy wanted files from a folder, zip, or single file; returns the number copied."""
    copied = 0

    def place(name: str, read):
        nonlocal copied
        base = Path(name).name
        if base in expected:
            target = DGP_IMAGES / base
        elif base == WORK_CATALOG_XLSX.name or base == LINKS:
            target = DGP_DIR / base
        elif base.lower().endswith('.zip'):
            # A zip inside the organiser's folder (Строительная_техника.zip)
            nested = DGP_DIR / '.nested.zip'
            nested.write_bytes(read())
            copied += collect(nested, expected)
            nested.unlink()
            return
        else:
            return
        if not target.exists():
            target.write_bytes(read())
            copied += 1

    if source.is_dir():
        for path in sorted(source.rglob('*')):
            if path.is_file():
                place(path.name, path.read_bytes)
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    # Zips made on Windows may store Cyrillic names in cp866.
                    name = info.filename
                    if not info.flag_bits & 0x800:
                        try:
                            name = name.encode('cp437').decode('cp866')
                        except UnicodeError:
                            pass
                    place(name, lambda info=info: archive.read(info))
    elif source.is_file():
        place(source.name, source.read_bytes)
    else:
        raise SystemExit(f'Не найдено: {source}')
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, action='append', default=[],
                        help='папка, zip или отдельный файл с материалами организатора')
    args = parser.parse_args()
    hashes = expected_hashes()
    DGP_IMAGES.mkdir(parents=True, exist_ok=True)
    for source in args.source:
        print(f'{source}: скопировано {collect(source.expanduser(), set(hashes))}')

    missing, corrupted = [], []
    for name, digest in sorted(hashes.items()):
        path = DGP_IMAGES / name
        if not path.exists():
            missing.append(name)
            continue
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                corrupted.append(name)
    print(f'Снимки: {len(hashes) - len(missing) - len(corrupted)}/{len(hashes)} совпадают с эталонными SHA-256')
    print(f'Перечень работ: {"есть" if WORK_CATALOG_XLSX.exists() else "НЕТ"} ({WORK_CATALOG_XLSX.relative_to(ROOT)})')
    if missing:
        print(f'Нет файлов ({len(missing)}): {", ".join(missing[:10])}{" …" if len(missing) > 10 else ""}')
    if corrupted:
        print(f'Отличаются от эталона ({len(corrupted)}): {", ".join(corrupted[:10])}')
        for name in corrupted:
            shutil.move(DGP_IMAGES / name, DGP_IMAGES / f'{name}.mismatch')
    raise SystemExit(1 if missing or corrupted or not WORK_CATALOG_XLSX.exists() else 0)


if __name__ == '__main__':
    main()
