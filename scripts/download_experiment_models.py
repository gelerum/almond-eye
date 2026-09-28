"""Download public, revision-pinned pilot models; no inference or app changes."""
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'runtime/experiments/materials-20260928/models'


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    specs = [
        ('Zaafan/sitesense-weights', 'sitesense', {'yolo26l_construction_v1.pt', 'README.md'}),
        ('IDEA-Research/grounding-dino-tiny', 'grounding-dino',
         {'config.json', 'model.safetensors', 'preprocessor_config.json', 'tokenizer.json',
          'tokenizer_config.json', 'special_tokens_map.json', 'added_tokens.json', 'vocab.txt', 'README.md'}),
    ]
    for repo, folder, names in specs:
        target = DEST / folder
        target.mkdir(exist_ok=True)
        manifest_path = target / 'download-manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        else:
            provenance = ROOT / 'data/experiments/materials-20260928/provenance.json'
            if provenance.exists():
                manifest = json.loads(provenance.read_text())['models'][folder]
            else:
                info = fetch_json(f'https://huggingface.co/api/models/{repo}')
                manifest = {'repo': repo, 'revision': info['sha'], 'files': {}}
            manifest_path.write_text(json.dumps(manifest, indent=2))
        revision = manifest['revision']
        for name in sorted(names):
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
            manifest_path.write_text(json.dumps(manifest, indent=2))
            print(f'Saved {name}: {path.stat().st_size} bytes', flush=True)


if __name__ == '__main__':
    main()
