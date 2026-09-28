"""Well-known repository paths shared by the app, scripts and tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DGP_DIR = ROOT / "data/raw/dgp"
DGP_IMAGES = DGP_DIR / "images"
WORK_CATALOG_XLSX = DGP_DIR / "Сводный перечень строительных работ_ЛТЦ.xlsx"
PLANS_DIR = ROOT / "data/plans"
MANIFESTS_DIR = ROOT / "data/manifests"
MODELS_DIR = ROOT / "runtime/models"


def dataset_image(name: str) -> Path:
    """Resolve an image reference from older manifests ("Строительная_техника/X.png") or a bare file name."""
    return DGP_IMAGES / Path(name).name
