"""실데이터(27관: real/ + backup/)와 합성 데이터(synthetic/)를 MongoDB `library` DB에 적재한다.

    python3 libraries/scripts/load_mongo.py [--db library] [--env ../.env]

- 접속 정보는 저장소 루트 .env(MONGO_HOST/PORT/USER/PASSWORD/AUTH_DB)에서 읽는다.
- mongoimport(MongoDB Database Tools)를 사용하며 비밀번호는 권한 600 임시 설정파일로만 전달한다.
- 각 컬렉션은 --drop 후 다시 넣는다(재실행 가능). 인덱스는 mongosh로 생성한다.
"""
import argparse, json, os, subprocess, tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
REPO = LIB.parents[1]
TABLES = ["libraries", "rooms", "maps", "seats", "facilities", "room_features", "seat_features",
          "walkway_nodes", "walkway_edges", "map_transforms", "sources"]
ID_FIELD = {"libraries": "library_id", "rooms": "room_id", "maps": "map_id", "seats": "seat_id",
            "facilities": "facility_id", "walkway_nodes": "node_id", "sources": "source_id"}
SYNTH = ["synth_scenarios", "synth_seat_env", "synth_users", "synth_sessions", "synth_events"]
INDEXES = {
    "libraries": [{"dataset_status": 1}],
    "rooms": [{"library_id": 1}], "maps": [{"library_id": 1}],
    "seats": [{"library_id": 1, "room_id": 1}, {"map_id": 1}],
    "facilities": [{"library_id": 1, "type": 1}], "room_features": [{"room_id": 1, "name": 1}],
    "sources": [{"library_id": 1}], "build_reports": [{"library_id": 1}],
    "synth_scenarios": [{"library_id": 1, "room_id": 1}],
    "synth_seat_env": [{"scenario_id": 1}, {"seat_id": 1}, {"library_id": 1}],
    "synth_users": [{"home_library_id": 1}, {"archetype": 1}],
    "synth_sessions": [{"user_id": 1, "occurred_at": 1}, {"scenario_id": 1}, {"library_id": 1}],
    "synth_events": [{"session_id": 1}, {"user_id": 1, "type": 1}],
}


def read_env(p):
    env = {}
    for line in Path(p).read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def real_rows():
    reg = json.load(open(LIB / "registry" / "ID_REGISTRY.json", encoding="utf-8"))
    rows = {t: [] for t in TABLES + ["build_reports"]}
    for r in reg:
        lid = r["library_id"]
        for status, base in (("active", LIB / "real"), ("backup", LIB / "backup")):
            if (base / lid / "dataset.json").exists():
                break
        else:
            raise FileNotFoundError(lid)
        ds = json.load(open(base / lid / "dataset.json", encoding="utf-8"))
        maps = {m["map_id"]: m for m in ds.get("maps", [])}
        for t in TABLES:
            for rec in ds.get(t, []):
                rec = dict(rec); rec["library_id"] = lid; rec["dataset_status"] = status; rec["is_synthetic"] = False
                if t in ID_FIELD:
                    rec["_id"] = rec[ID_FIELD[t]]
                if t == "seats":
                    m = maps.get(rec.get("map_id")); b = rec.get("bbox")
                    if m and b and m.get("width") and m.get("height"):
                        rec["norm_bbox"] = [round(b["x"] / m["width"], 6), round(b["y"] / m["height"], 6),
                                            round(b["width"] / m["width"], 6), round(b["height"] / m["height"], 6)]
                rows[t].append(rec)
        br = json.load(open(base / lid / "build_report.json", encoding="utf-8"))
        br.update(_id=lid, library_id=lid, dataset_status=status)
        rows["build_reports"].append(br)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="library")
    ap.add_argument("--env", default=str(REPO / ".env"))
    a = ap.parse_args()
    e = read_env(a.env)
    uri = f'mongodb://{e["MONGO_HOST"]}:{e.get("MONGO_PORT", "27017")}/?authSource={e.get("MONGO_AUTH_DB", "admin")}'
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cfg.yaml"
        cfg.write_text(f'password: "{e["MONGO_PASSWORD"]}"\n'); os.chmod(cfg, 0o600)

        def imp(coll, path):
            subprocess.run(["mongoimport", "--uri", uri, "--username", e["MONGO_USER"], "--config", str(cfg),
                            "--db", a.db, "--collection", coll, "--drop", "--file", str(path), "--quiet",
                            "--numInsertionWorkers", str(min(8, os.cpu_count() or 1))], check=True)
            print(f"  {coll:16s} <- {path.name}")

        rows = real_rows()
        meta = [{"_id": "registry", "items": json.load(open(LIB / "registry" / "ID_REGISTRY.json", encoding="utf-8"))},
                {"_id": "synthetic_manifest", **json.load(open(LIB / "synthetic" / "manifest.json", encoding="utf-8"))}]
        rows["meta"] = meta
        for t, rs in rows.items():
            p = Path(td) / f"{t}.jsonl"
            with open(p, "w", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            imp(t, p)
        for t in SYNTH:
            imp(t, LIB / "synthetic" / f"{t}.jsonl")
        js = "const d=db.getSiblingDB(%s);" % json.dumps(a.db)
        for c, ixs in INDEXES.items():
            for ix in ixs:
                js += f"d.{c}.createIndex({json.dumps(ix)});"
        js += "printjson(Object.fromEntries(d.getCollectionNames().sort().map(c=>[c,d[c].countDocuments()])));"
        jsf = Path(td) / "ix.js"; jsf.write_text(js)
        subprocess.run(["mongosh", "--quiet", uri, "--username", e["MONGO_USER"], "--password", e["MONGO_PASSWORD"], jsf.as_posix()],
                       check=True)


if __name__ == "__main__":
    main()
