"""Reproducible local audit. Never changes the original customer files."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.domain.contracts import ImportOptions
from backend.app.services.import_schedule import preview_import
from backend.app.paths import DGP_IMAGES, WORK_CATALOG_XLSX


def main():
    out = ROOT/"data/manifests"
    out.mkdir(parents=True,exist_ok=True)
    excel = WORK_CATALOG_XLSX
    preview = preview_import(excel.read_bytes(),excel.name,ImportOptions(recover_date_numbers=True))
    (out/"work-catalog.json").write_text(preview.model_dump_json(indent=2),encoding="utf-8")
    records = []
    for path in sorted(DGP_IMAGES.glob("*.png"),key=lambda p:int(p.stem.split('_')[-1])):
        record = {"file":path.relative_to(ROOT).as_posix(),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                  "bytes":path.stat().st_size,"camera_id":None,"captured_at":None,"scene_group":None,
                  "split":"unassigned","annotation_status":"unlabelled","license_status":"organizer_materials_not_verified_for_redistribution"}
        try:
            with Image.open(path) as im:
                im.load()
                thumb = ImageOps.grayscale(im).resize((9,8))
                pixels = [thumb.getpixel((x,y)) for y in range(8) for x in range(9)]
                bits = [pixels[y*9+x]>pixels[y*9+x+1] for y in range(8) for x in range(8)]
                record.update(width=im.width,height=im.height,format=im.format,
                              dhash=f"{sum(int(bit)<<i for i,bit in enumerate(bits)):016x}",
                              exif_datetime=im.getexif().get(36867),decode_status="ok")
        except (OSError,ValueError) as exc:
            record.update(decode_status="error",error=str(exc))
        records.append(record)
    duplicates = [h for h,n in Counter(r['sha256'] for r in records).items() if n>1]
    similar=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            if 'dhash' in a and 'dhash' in b:
                distance=(int(a['dhash'],16)^int(b['dhash'],16)).bit_count()
                if distance<=6:
                    similar.append({"a":a['file'],"b":b['file'],"distance":distance,"status":"manual_review_required"})
    report={"images":records,"summary":{"count":len(records),"bytes":sum(r['bytes'] for r in records),
            "decode_errors":sum(r['decode_status']!='ok' for r in records),"duplicate_hashes":duplicates,
            "exif_datetime_count":sum(bool(r.get('exif_datetime')) for r in records),
            "similar_pair_candidates":len(similar),"catalog_rows":len(preview.stages),
            "catalog_work_rows":sum(s.kind=='work' for s in preview.stages),
            "catalog_issues":dict(Counter(i.code for i in preview.issues))},"similar_pairs":similar}
    (out/"input-audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
