"""One-off, read-only MongoDB library -> catalog-only SQLite export.

uv run --group migration python -m data.libraries.scripts.export_sqlite_seed
"""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from backend.catalog import INDEX_FIELDS, SCHEMA
from seat_model.train.common import _mongo_db

COLLECTIONS = {
    "build_reports",
    "facilities",
    "libraries",
    "maps",
    "meta",
    "room_features",
    "rooms",
    "seats",
    "sources",
    "synth_events",
    "synth_scenarios",
    "synth_seat_env",
    "synth_sessions",
    "synth_users",
    "walkway_nodes",
}


def export(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial")
    if temporary.exists():
        raise FileExistsError(temporary)
    mongo = _mongo_db("library")
    names = set(mongo.list_collection_names())
    if names != COLLECTIONS:
        raise RuntimeError("Collection set changed; review the export allowlist first")
    manifest = {}
    db = sqlite3.connect(temporary)
    try:
        db.executescript(SCHEMA)
        for name in sorted(names):
            sha = hashlib.sha256()
            count, batch = 0, []
            for doc in mongo[name].find({}, {"_id": 0}).sort("_id", 1):
                body = json.dumps(
                    doc,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                sha.update((body + "\n").encode())
                batch.append((name, count, body))
                count += 1
                if len(batch) == 1000:
                    db.executemany(
                        "INSERT INTO catalog_documents VALUES (?,?,?)", batch
                    )
                    batch.clear()
            db.executemany("INSERT INTO catalog_documents VALUES (?,?,?)", batch)
            if count != mongo[name].count_documents({}):
                raise RuntimeError(f"Source collection changed during export: {name}")
            manifest[name] = {"count": count, "sha256": sha.hexdigest()}
            print(name, count, flush=True)
        for field in INDEX_FIELDS:
            db.execute(
                f"CREATE INDEX catalog_{field} ON catalog_documents(collection, json_extract(document, '$.{field}'))"
            )
        version = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest()
        db.execute("INSERT INTO catalog_metadata VALUES ('version', ?)", (version,))
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("SQLite integrity check failed")
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        db.close()
        temporary.replace(output)
    print("Catalog-only SQLite exported:", output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("deploy/artifacts/library.sqlite3")
    )
    export(parser.parse_args().output)


if __name__ == "__main__":
    main()
