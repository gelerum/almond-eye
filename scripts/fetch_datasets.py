"""Download the open datasets used for fine-tuning (resumable, MD5-checked).

  uv run scripts/fetch_datasets.py structural-material   # 1.9 GB, CC0
  uv run scripts/fetch_datasets.py conrebseg             # 22.6 GB, CC BY 4.0
  uv run scripts/fetch_datasets.py                       # both

Files land in runtime/datasets/<name>/. Interrupted downloads continue from where they stopped
(HTTP Range on the .part file). Checksums are the ones figshare publishes for each file.
"""
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'runtime/datasets'

DATASETS = {
    # Bianchi & Hebdon, VT, https://doi.org/10.7294/16624648
    'structural-material': {
        'files': {
            'material.zip': ('https://ndownloader.figshare.com/files/30914683', 'b37c2c6628c3b6e00f1b6cc9cd008169'),
            'README.rtf': ('https://ndownloader.figshare.com/files/30930376', '53941a4145c7f956c8a2bf485a926e78'),
        },
        'unzip': 'material.zip',
    },
    # Schmidt & Nalpantidis, DTU, https://doi.org/10.11583/DTU.26213762 (self-collected part only)
    'conrebseg': {
        'files': {
            'metadata.json': ('https://ndownloader.figshare.com/files/47627011', '8024f05766f52549abfbf6e869679ce3'),
            'samples.json': ('https://ndownloader.figshare.com/files/47627014', '3a7ca34b89077fd6104a4fa8a5aba0b5'),
            'ConRebSeg.zip': ('https://ndownloader.figshare.com/files/47836036', 'c1517d9e41fc59cc7be2835b3e2f4642'),
        },
    },
}


def md5(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'md5').hexdigest()


def download(url: str, target: Path, expected_md5: str):
    if target.exists() and md5(target) == expected_md5:
        print(f'  {target.name}: уже скачан')
        return
    part = target.with_name(target.name + '.part')
    offset = part.stat().st_size if part.exists() else 0
    request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
    with urllib.request.urlopen(request, timeout=120) as response:
        if offset and response.status != 206:  # server ignored Range: start over
            offset = 0
        total = offset + int(response.headers.get('Content-Length', 0))
        with part.open('ab' if offset else 'wb') as stream:
            done = offset
            while chunk := response.read(8 << 20):
                stream.write(chunk)
                done += len(chunk)
                print(f'\r  {target.name}: {done >> 20} / {total >> 20} МБ', end='', flush=True)
    print()
    if md5(part) != expected_md5:
        raise SystemExit(f'{target.name}: MD5 не совпал; удалите {part} и повторите')
    part.replace(target)


def main(names):
    for name in names or DATASETS:
        spec = DATASETS[name]
        folder = DEST / name
        folder.mkdir(parents=True, exist_ok=True)
        print(name)
        for file, (url, checksum) in spec['files'].items():
            download(url, folder / file, checksum)
        if spec.get('unzip'):
            marker = folder / '.unpacked'
            if not marker.exists():
                with zipfile.ZipFile(folder / spec['unzip']) as archive:
                    archive.extractall(folder)
                marker.touch()
            print(f'  распакован в {folder}')


if __name__ == '__main__':
    main(sys.argv[1:])
