"""Download public, revision-pinned models into runtime/models/<name>.

Pinned revisions and checksums live in data/sources/models.lock.json; a model missing there is
pinned to the current Hub revision on first download and recorded.
"""
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'runtime/models'
LOCK = ROOT / 'data/sources/models.lock.json'
TOKENIZER = {'config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json',
             'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'README.md'}
SPECS = {
    'sitesense': ('Zaafan/sitesense-weights', {'yolo26l_construction_v1.pt', 'README.md'}),
    'grounding-dino-base': ('IDEA-Research/grounding-dino-base', TOKENIZER),
    'grounding-dino-tiny': ('IDEA-Research/grounding-dino-tiny', TOKENIZER | {'added_tokens.json'}),
    'clip-vit-large-patch14': ('openai/clip-vit-large-patch14',
                               {'config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json',
                                'tokenizer_config.json', 'special_tokens_map.json', 'vocab.json', 'merges.txt'}),
}
# Downloaded by default (what the ensemble in backend/app/config/detectors.yaml needs).
DEFAULT = ['sitesense', 'grounding-dino-base', 'clip-vit-large-patch14']


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def main(names=None):
    DEST.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    for folder in names or DEFAULT:
        repo, files = SPECS[folder]
        target = DEST / folder
        target.mkdir(exist_ok=True)
        if folder not in lock:
            lock[folder] = {'repo': repo, 'revision': fetch_json(f'https://huggingface.co/api/models/{repo}')['sha'],
                            'files': {}}
        manifest = lock[folder]
        revision = manifest['revision']
        for name in sorted(files):
            path = target / name
            if path.exists() and name in manifest['files']:
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() == manifest['files'][name]['sha256']:
                        continue
            url = f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
            print(f'Downloading {repo}/{name}', flush=True)
            temp = path.with_suffix(path.suffix + '.part')
            with urllib.request.urlopen(url, timeout=120) as response, temp.open('wb') as stream:
                size = 0
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    size += len(chunk)
                    if size % (50 * 1024 * 1024) == 0:
                        print(f'  {size // (1024 * 1024)} MiB', flush=True)
            with temp.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            expected = manifest['files'].get(name, {}).get('sha256')
            if expected and digest != expected:
                raise ValueError(f'Checksum mismatch: {name}')
            temp.replace(path)
            manifest['files'][name] = {'sha256': digest, 'bytes': path.stat().st_size, 'url': url}
            LOCK.write_text(json.dumps(lock, indent=2) + '\n')
            print(f'Saved {name}: {path.stat().st_size} bytes', flush=True)


if __name__ == '__main__':
    import sys
    main(sys.argv[1:] or None)
