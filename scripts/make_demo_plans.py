"""Demo calendar plans data/plans/<site>.xlsx for the five dated sites of the organiser's photos.

Layout of the organiser's template («календарный план.pdf», «График производства работ»): title, object,
a row of years and a row of months, stage names in the first columns, filled month cells for the planned
interval, plus exact «Начало»/«Окончание» columns and a hidden «Ключ этапа» column.
Stages follow the template and the ЛТЦ work catalogue up to «Ввод объекта».

We have no real schedules for these sites. The dates are chosen so that, computed honestly from the
manifest observations (data/manifests/dgp_images.json), the sites show different outcomes on the date of
their latest photo (the default date of /status):

* site-a — chronic lag. Piling planned until 30.06.2023 is still seen on 14.08.2023 (+45 days on that date);
  on the latest photo (09.08.2024) the frame is still up although the superstructure was planned until
  15.07.2024 → «Отстаём на 25 дней».
* site-b — the slide-8 demo. Excavation planned until 22.05.2024 is still seen on 25.06.2024 (+34 days),
  the excavator works without dump trucks (pace risk), foundations have not started.
* site-c — excavation on schedule, but on 14.02.2024 the excavators have no dump trucks → «Риск отставания».
* site-d — on schedule: piling and excavation finished in time, the tower crane is being installed as planned.
* site-e — ahead: piling seen from 02.01.2026 (plan from 15.01), excavation from 02.03.2026 (plan from 16.03).

    uv run --no-sync python scripts/make_demo_plans.py [--show-key]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.paths import MANIFESTS_DIR, PLANS_DIR  # noqa: E402
from backend.app.services.gantt_import import render_gantt_xlsx  # noqa: E402

# Названия строк — из шаблона организатора и сводного перечня работ ЛТЦ.
PREP = "Подготовительные работы, ограждение площадки"
PILES = "Устройство шпунтового ограждения и свай"
PIT = "Разработка котлована (земляные работы)"
FOUND = "Устройство фундаментов и ростверков"
BASE = "Монтаж цокольного этажа"
CRANE = "Установка башенного крана"
ABOVE = "Монтаж надземной части"
LIFTS = "Монтаж лифтовых шахт"
ROOF = "Устройство кровли"
UNCRANE = "Демонтаж башенного крана"
WINDOWS = "Установка окон"
FACADE = "Фасадные работы"
NETS = "Наружные сети НВК и ТС"
ROADS = "Дорожные работы, благоустройство и озеленение"
OPEN = "Ввод объекта"
KEYS = {PREP: "site_prep", PILES: "piling", PIT: "excavation", FOUND: "foundation", BASE: "underground",
        CRANE: "tower_crane_install", ABOVE: "superstructure", LIFTS: "interior", ROOF: "facade_roof",
        UNCRANE: "tower_crane_dismantle", WINDOWS: "facade_roof", FACADE: "facade_roof", NETS: "external_networks",
        ROADS: "landscaping", OPEN: "commissioning"}

PLANS = {
    "site-a": [  # башенный кран на этой площадке стоит уже во время свай (кадры июня 2023)
        (PREP, "2023-02-01", "2023-03-15"), (PILES, "2023-03-15", "2023-06-30"),
        (CRANE, "2023-05-15", "2023-06-15"), (PIT, "2023-07-01", "2023-09-30"),
        (FOUND, "2023-10-01", "2023-12-15"), (BASE, "2023-12-01", "2024-02-29"),
        (ABOVE, "2024-02-01", "2024-07-15"), (LIFTS, "2024-04-01", "2024-09-30"),
        (UNCRANE, "2024-08-12", "2024-09-15"), (ROOF, "2024-08-15", "2024-10-31"),
        (WINDOWS, "2024-08-15", "2024-11-30"), (FACADE, "2024-09-01", "2025-02-28"),
        (NETS, "2024-10-01", "2025-03-31"), (ROADS, "2025-04-01", "2025-06-30"), (OPEN, "2025-07-01", "2025-08-31")],
    "site-b": [
        (PREP, "2023-12-01", "2024-01-31"), (PILES, "2024-02-01", "2024-04-30"),
        (PIT, "2024-04-01", "2024-05-22"), (FOUND, "2024-06-01", "2024-08-15"),
        (BASE, "2024-08-01", "2024-11-30"), (CRANE, "2024-08-15", "2024-09-15"),
        (ABOVE, "2024-10-01", "2025-09-30"), (LIFTS, "2025-03-01", "2025-10-31"),
        (ROOF, "2025-09-01", "2025-11-30"), (UNCRANE, "2025-10-01", "2025-10-31"),
        (WINDOWS, "2025-06-01", "2025-12-31"), (NETS, "2025-08-01", "2026-02-28"),
        (ROADS, "2026-03-01", "2026-06-30"), (OPEN, "2026-07-01", "2026-09-30")],
    "site-c": [
        (PREP, "2023-08-01", "2023-09-30"), (PILES, "2023-09-15", "2024-01-31"),
        (PIT, "2024-01-15", "2024-04-30"), (FOUND, "2024-04-15", "2024-06-30"),
        (BASE, "2024-06-15", "2024-09-30"), (CRANE, "2024-07-01", "2024-07-31"),
        (ABOVE, "2024-09-01", "2025-06-30"), (LIFTS, "2025-01-15", "2025-08-31"),
        (ROOF, "2025-06-01", "2025-08-31"), (UNCRANE, "2025-07-01", "2025-07-31"),
        (WINDOWS, "2025-04-01", "2025-10-31"), (NETS, "2025-05-01", "2025-10-31"),
        (ROADS, "2025-09-01", "2025-11-30"), (OPEN, "2025-12-01", "2026-01-31")],
    "site-d": [
        (PREP, "2024-07-01", "2024-08-31"), (PILES, "2024-08-15", "2024-10-31"),
        (PIT, "2024-10-15", "2024-12-31"), (FOUND, "2025-01-01", "2025-02-15"),
        (CRANE, "2025-02-01", "2025-03-15"), (BASE, "2025-02-01", "2025-04-30"),
        (ABOVE, "2025-04-01", "2026-03-31"), (LIFTS, "2025-09-01", "2026-05-31"),
        (ROOF, "2026-02-01", "2026-04-30"), (UNCRANE, "2026-04-01", "2026-04-30"),
        (WINDOWS, "2025-12-01", "2026-06-30"), (NETS, "2026-01-01", "2026-06-30"),
        (ROADS, "2026-06-01", "2026-08-31"), (OPEN, "2026-09-01", "2026-10-31")],
    "site-e": [
        (PREP, "2025-11-01", "2026-01-10"), (PILES, "2026-01-15", "2026-02-28"),
        (PIT, "2026-03-16", "2026-06-30"), (FOUND, "2026-06-15", "2026-08-31"),
        (BASE, "2026-08-15", "2026-11-30"), (CRANE, "2026-09-01", "2026-09-30"),
        (ABOVE, "2026-11-01", "2027-10-31"), (LIFTS, "2027-04-01", "2027-11-30"),
        (ROOF, "2027-09-01", "2027-11-30"), (UNCRANE, "2027-11-01", "2027-11-30"),
        (WINDOWS, "2027-06-01", "2027-12-31"), (NETS, "2027-08-01", "2028-02-29"),
        (ROADS, "2028-03-01", "2028-06-30"), (OPEN, "2028-07-01", "2028-09-30")],
}


def plan_rows(site: str) -> list[dict]:
    return [{"name": name, "start": start, "end": end, "stage_key": KEYS[name]} for name, start, end in PLANS[site]]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show-key", action="store_true", help="не скрывать столбец «Ключ этапа»")
    args = parser.parse_args()
    names = {s["id"]: s["name"] for s in json.loads((MANIFESTS_DIR / "dgp_images.json").read_text(encoding="utf-8"))["sites"]}
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    for site in PLANS:
        target = PLANS_DIR / f"{site}.xlsx"
        target.write_bytes(render_gantt_xlsx(names[site], plan_rows(site), hide_key=not args.show_key))
        print(target.relative_to(PLANS_DIR.parents[1]), len(PLANS[site]), "этапов")


if __name__ == "__main__":
    main()
