"""real/{library_id}/dataset.json (좌석 좌표가 있는 활성 도서관) -> _unified/{table}.jsonl + coverage.csv (library_id 부여, 좌석 정규화 좌표 계산)."""
import csv, json
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB / "real"
OUT = LIB / "unified"
TABLES = ["libraries", "rooms", "maps", "seats", "facilities", "room_features", "seat_features",
          "walkway_nodes", "walkway_edges", "map_transforms", "sources"]


def main():
    reg = json.load(open(LIB / "registry" / "ID_REGISTRY.json", encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    rows = {t: [] for t in TABLES}
    cov = []
    for r in reg:
        lid = r["library_id"]
        if not (ROOT / lid / "dataset.json").exists():
            continue  # backup/ 으로 이동된 도서관
        ds = json.load(open(ROOT / lid / "dataset.json", encoding="utf-8"))
        maps = {m["map_id"]: m for m in ds.get("maps", [])}
        for t in TABLES:
            for rec in ds.get(t, []):
                rec = dict(rec)
                rec["library_id"] = lid
                if t == "seats":
                    m = maps.get(rec.get("map_id"))
                    b = rec.get("bbox")
                    if m and b and m.get("width") and m.get("height"):
                        rec["norm_bbox"] = [round(b["x"] / m["width"], 6), round(b["y"] / m["height"], 6),
                                            round(b["width"] / m["width"], 6), round(b["height"] / m["height"], 6)]
                rows[t].append(rec)
        br = json.load(open(ROOT / lid / "build_report.json", encoding="utf-8"))
        seats = ds.get("seats", [])
        cov.append({
            "library_id": lid, "name": r["name"], "status": br.get("status"),
            "rooms": len(ds.get("rooms", [])), "seats": len(seats),
            "seats_with_label": sum(1 for s in seats if s.get("seat_label")),
            "seats_with_official_id": sum(1 for s in seats if s.get("external_seat_id")),
            "facilities": len(ds.get("facilities", [])), "has_latlng": bool(ds["libraries"][0].get("latitude")),
            "room_features": len(ds.get("room_features", [])),
            "human_checked_seats": sum(1 for s in seats if s.get("verification") in ("human_checked", "field_verified")),
        })
    for t in TABLES:
        with open(OUT / f"{t}.jsonl", "w", encoding="utf-8") as f:
            for rec in rows[t]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(OUT / "coverage.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cov[0])); w.writeheader(); w.writerows(cov)
    print({t: len(rows[t]) for t in TABLES})


if __name__ == "__main__":
    main()
