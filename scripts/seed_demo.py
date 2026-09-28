"""Demo sites from the organiser's photos: one project per dated site of data/manifests/dgp_images.json.

For each site: create the project, import data/plans/<site>.xlsx (scripts/make_demo_plans.py), upload the site's
photos with captured_at = manifest date (12:00 Europe/Moscow, time_source "user"), run analyze-all, wait and
print the verdict on the latest photo date (and, with --timeline, on every capture date).

    uv run --no-sync python scripts/seed_demo.py                         # через запущенный API (localhost:7860)
    uv run --no-sync python scripts/seed_demo.py --in-process            # внутри процесса, детекторы из окружения
    uv run --no-sync python scripts/seed_demo.py --ground-truth --timeline --database sqlite:///runtime/gt.db

--ground-truth (always in-process) replaces the detectors with the manifest's equipment lists: the ceiling of
the methodology and a fast check of plans and rules without a GPU.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.domain.contracts import Detection, Equipment  # noqa: E402
from backend.app.paths import MANIFESTS_DIR, PLANS_DIR, dataset_image  # noqa: E402
from backend.app.services.detector import DetectionBatch  # noqa: E402

SITES = ["site-a", "site-b", "site-c", "site-d", "site-e"]


class GroundTruthDetector:
    """Image-level presence from the manual labels; one full-frame «box» per class (no localisation)."""
    model_version = "ground-truth-manifest-v1"
    supported_classes = list(Equipment)
    available_classes = list(Equipment)
    profile = "ground-truth"

    def __init__(self, images: list[dict]):
        self.by_sha = {}
        for image in images:
            path = dataset_image(image["file"])
            if path.exists():
                self.by_sha[hashlib.sha256(path.read_bytes()).hexdigest()] = image["equipment"]

    def detect(self, image_path: Path) -> DetectionBatch:
        classes = self.by_sha.get(Path(image_path).name, [])
        return DetectionBatch(model_version=self.model_version, supported_classes=self.supported_classes,
                              detections=[Detection(equipment=c, confidence=1.0, bbox=(0, 0, 1, 1), sources=["manifest"])
                                          for c in classes])


def check(response):
    if response.status_code >= 400:
        raise SystemExit(f"{response.request.method} {response.request.url}: {response.status_code} {response.text}")
    return response.json()


def seed_site(client, site: dict, images: list[dict], timeline: bool, timeout: float) -> dict:
    project = check(client.post("/api/projects", json={"name": site["name"], "timezone": "Europe/Moscow"}))
    base = f"/api/projects/{project['id']}"
    plan_file = PLANS_DIR / f"{site['id']}.xlsx"
    preview = check(client.post(base + "/plans/xlsx/preview", files={"file": (plan_file.name, plan_file.read_bytes())}))
    errors = [i for i in preview["issues"] if i["severity"] == "error"]
    if errors:
        raise SystemExit(f"{plan_file}: {errors}")
    check(client.post(base + "/plans/xlsx/confirm", json={"preview_id": preview["id"], "accept_warnings": True}))
    camera = check(client.post(base + "/cameras", json={"name": "Камера 1"}))
    dated = [i for i in images if i["captured_at"] and dataset_image(i["file"]).exists()]
    manifest = {i["file"]: {"captured_at": f"{i['captured_at']}T12:00:00+03:00", "time_source": "user"} for i in dated}
    result = check(client.post(base + "/images/batch", data={"camera_id": camera["id"], "manifest": json.dumps(manifest)},
                               files=[("files", (i["file"], dataset_image(i["file"]).read_bytes(), "image/png"))
                                      for i in dated]))
    if result["errors"]:
        print("  ошибки загрузки:", result["errors"])
    job = check(client.post(base + "/analyze-all"))
    started = time.monotonic()
    while job["status"] == "running":
        if time.monotonic() - started > timeout:
            raise SystemExit(f"{site['id']}: анализ не завершился за {timeout} с")
        time.sleep(1)
        job = check(client.get(f"{base}/jobs/{job['id']}"))
    if job["status"] != "done" or job.get("errors"):
        print("  задача анализа:", job["status"], job.get("errors") or job.get("error"))
    status = check(client.get(base + "/status"))
    report = {"site": site["id"], "project_id": project["id"], "images": len(dated), "verdict": status["verdict"],
              "alerts": [(a["severity"], a["kind"], a["message"]) for a in status["alerts"]]}
    if timeline:
        report["timeline"] = {d: check(client.get(base + "/status", params={"date": d}))["verdict"]
                              for d in sorted({i["captured_at"] for i in dated})}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", nargs="?", default="http://localhost:7860")
    parser.add_argument("--in-process", action="store_true", help="TestClient вместо HTTP (детектор из окружения)")
    parser.add_argument("--ground-truth", action="store_true", help="разметка вместо детекторов (in-process)")
    parser.add_argument("--database", help="DATABASE_URL для in-process режима (по умолчанию как у приложения)")
    parser.add_argument("--sites", nargs="*", default=SITES)
    parser.add_argument("--timeline", action="store_true", help="вердикт на каждую дату съёмки")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--json", type=Path, help="сохранить отчёт")
    args = parser.parse_args()
    data = json.loads((MANIFESTS_DIR / "dgp_images.json").read_text(encoding="utf-8"))
    sites = {s["id"]: s for s in data["sites"]}
    if args.ground_truth or args.in_process:
        from fastapi.testclient import TestClient
        from backend.app.main import create_app
        detector = GroundTruthDetector(data["images"]) if args.ground_truth else None
        client = TestClient(create_app(args.database, detector=detector))
    else:
        import httpx
        client = httpx.Client(base_url=args.base_url, timeout=600)
    reports = []
    with client:
        for site_id in args.sites:
            report = seed_site(client, sites[site_id], [i for i in data["images"] if i["site_id"] == site_id],
                               args.timeline, args.timeout)
            reports.append(report)
            print(f"\n{site_id} ({report['images']} снимков): {report['verdict']['summary']}")
            for severity, kind, message in report["alerts"]:
                print(f"  [{severity}] {kind}: {message}")
            for day, verdict in report.get("timeline", {}).items():
                print(f"  {day}: {verdict['status']:9} {verdict['summary']}")
    if args.json:
        args.json.write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
