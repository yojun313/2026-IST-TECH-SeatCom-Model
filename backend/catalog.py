"""Versioned library snapshot and application records share DATABASE_PATH.

MongoDB is needed only by the one-off export tool, never by this module.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "sqlite_seed"
INDEX_FIELDS = ("library_id", "room_id", "map_id", "scenario_id", "session_id", "user_id")
SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_documents (
 collection TEXT NOT NULL, ordinal INTEGER NOT NULL,
 document TEXT NOT NULL CHECK(json_valid(document)),
 PRIMARY KEY(collection, ordinal)
) WITHOUT ROWID;
"""


def connect(path):
    db = sqlite3.connect(str(path), timeout=300, uri=True)
    db.execute("PRAGMA busy_timeout=300000")
    return db


def initialize(path: Path, seed_dir: Path = SEED_DIR):
    """Install a verified Release snapshot in the same DB as user records.

    Download/extraction happen before the write lock. Only catalog tables are
    replaced, in one transaction; rollback preserves the previous catalog.
    """
    from .seed_download import read_manifest, snapshot_file

    manifest = read_manifest(seed_dir)
    version = manifest["version"]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.executescript(SCHEMA)
        if db.execute("SELECT value FROM catalog_metadata WHERE key='version'").fetchone() == (version,):
            return
        source = snapshot_file(manifest, seed_dir)
        if source.resolve() == path.resolve():
            raise ValueError("Seed and runtime database must be different files")
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT value FROM catalog_metadata WHERE key='version'").fetchone() == (version,):
            db.rollback()
            return
        db.execute("ATTACH DATABASE ? AS seed", (source.resolve().as_uri() + "?mode=ro",))
        db.execute("DELETE FROM catalog_documents")
        for field in INDEX_FIELDS:
            db.execute(f"DROP INDEX IF EXISTS main.catalog_{field}")
        db.execute("INSERT INTO catalog_documents SELECT collection, ordinal, document FROM seed.catalog_documents")
        counts = dict(db.execute("SELECT collection, count(*) FROM catalog_documents GROUP BY collection"))
        if counts != manifest["counts"]:
            raise ValueError("Catalog collection counts do not match Release manifest")
        for field in INDEX_FIELDS:
            db.execute(f"CREATE INDEX main.catalog_{field} ON catalog_documents(collection, json_extract(document, '$.{field}'))")
        db.execute("INSERT OR REPLACE INTO catalog_metadata VALUES ('version', ?)", (version,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def json_path(field):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", field):
        raise ValueError(f"Invalid catalog field: {field}")
    return "$." + field


def project(document, projection):
    if not projection:
        return document
    fields = [k for k, enabled in projection.items() if enabled and k != "_id"]
    if not fields:
        return {k: v for k, v in document.items() if projection.get(k, 1)}
    result = {}
    for field in fields:
        parts = field.split(".")
        value = document
        for key in parts:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            parent = result
            for key in parts[:-1]:
                parent = parent.setdefault(key, {})
            parent[parts[-1]] = value
    return result


class Collection:
    """Small read-only query surface used by Room and the training allowlists.

    Supports equality filters and nested inclusive projections, not arbitrary
    MongoDB operators. Values are always bound SQL parameters.
    """
    def __init__(self, path, name):
        self.path, self.name = path, name

    def find(self, filt=None, projection=None):
        conditions, values = ["collection=?"], [self.name]
        for field, value in (filt or {}).items():
            path = json_path(field)
            if isinstance(value, (dict, list)):
                raise ValueError("Only scalar equality filters are supported")
            conditions.append(f"json_extract(document, '{path}') IS ?")
            values.append(value)
        db = connect(self.path)
        try:
            rows = db.execute("SELECT document FROM catalog_documents WHERE " + " AND ".join(conditions) + " ORDER BY ordinal", values)
            for (document,) in rows:
                yield project(json.loads(document), projection)
        finally:
            db.close()

    def find_one(self, filt=None, projection=None):
        rows = self.find(filt, projection)
        try:
            return next(rows, None)
        finally:
            rows.close()

    def distinct(self, field):
        path = json_path(field)
        db = connect(self.path)
        try:
            return [r[0] for r in db.execute(
                f"SELECT DISTINCT json_extract(document, '{path}') FROM catalog_documents WHERE collection=? AND json_type(document, '{path}') IS NOT NULL",
                (self.name,))]
        finally:
            db.close()


class Catalog:
    def __init__(self, path, *, seed_dir=SEED_DIR):
        self.path = Path(path)
        initialize(self.path, seed_dir)

    def __getitem__(self, name):
        return Collection(self.path, name)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return self[name]


if __name__ == "__main__":
    from .config import get_settings
    path = get_settings().database_path
    initialize(path)
    print(f"SQLite catalog ready: {path}")
