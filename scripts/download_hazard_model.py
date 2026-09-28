"""Download the author's pinned YOLO11n weights and verify before installation."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

REVISION = "212ee245136b4e330f84b409f67f3d35eff59f42"
SHA256 = "f55600c106ba7952c64d2aec70dc673240b422bdbd989d395d301b0d5426b02b"
BASE = f"https://huggingface.co/yihong1120/Construction-Hazard-Detection/resolve/{REVISION}"
DEST = Path(__file__).resolve().parents[1] / "runtime/models/construction-hazard"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / "yolo11n.pt"
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == SHA256:
        print(f"Verified: {target}")
        return
    with urlopen(f"{BASE}/models/yolo11/pt/yolo11n.pt", timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("Weight checksum mismatch; model was not installed")
    with urlopen(f"{BASE}/LICENSE", timeout=60) as response:
        (DEST / "LICENSE").write_bytes(response.read())
    temporary = target.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(target)
    print(f"Installed and verified: {target}")


if __name__ == "__main__":
    main()
