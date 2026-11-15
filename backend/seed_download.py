"""Pinned GitHub Release download with checksums and an offline override."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.request import Request, urlopen


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read_manifest(seed_dir):
    manifest = json.loads((Path(seed_dir) / "release.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "sqlite-gzip-v1":
        raise ValueError("Unsupported SQLite release format")
    if Path(manifest["asset"]).name != manifest["asset"]:
        raise ValueError("Invalid release asset name")
    if not all(
        len(manifest[k]) == 64 and all(c in "0123456789abcdef" for c in manifest[k])
        for k in ("sha256", "sqlite_sha256", "version")
    ):
        raise ValueError("Invalid release checksum/version")
    return manifest


def validate_database(path, manifest):
    if (
        path.stat().st_size != manifest["sqlite_bytes"]
        or digest(path) != manifest["sqlite_sha256"]
    ):
        raise ValueError("SQLite snapshot checksum mismatch")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if tables != {"catalog_documents", "catalog_metadata"}:
            raise ValueError("Release must contain catalog tables only")
        if db.execute(
            "SELECT value FROM catalog_metadata WHERE key='version'"
        ).fetchone() != (manifest["version"],):
            raise ValueError("SQLite snapshot version mismatch")
        if (
            dict(
                db.execute(
                    "SELECT collection,count(*) FROM catalog_documents GROUP BY collection"
                )
            )
            != manifest["counts"]
        ):
            raise ValueError("SQLite snapshot counts mismatch")


def snapshot_file(manifest, seed_dir):
    override = os.getenv("SEATCOM_SEED_PATH")
    cache_root = Path(
        os.getenv("SEATCOM_DATA_CACHE", str(Path.home() / ".cache" / "seatcom"))
    )
    cache = cache_root / manifest["sqlite_sha256"]
    cache.mkdir(parents=True, exist_ok=True)
    ready = cache / "library.sqlite3"
    if override:
        source = Path(override).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"SEATCOM_SEED_PATH does not exist: {source}")
        if source.suffix != ".gz":
            validate_database(source, manifest)
            return source
    else:
        source = Path(seed_dir) / manifest["asset"]
        if ready.is_file():
            validate_database(ready, manifest)
            return ready
    if not source.is_file():
        source = cache / manifest["asset"]
        if not source.is_file() or digest(source) != manifest["sha256"]:
            if not manifest["url"].startswith("https://github.com/"):
                raise ValueError("Seed URL must be a pinned HTTPS GitHub Release asset")
            with tempfile.NamedTemporaryFile(
                dir=cache, suffix=".download", delete=False
            ) as f:
                temporary = Path(f.name)
                try:
                    with urlopen(
                        Request(
                            manifest["url"],
                            headers={"User-Agent": "SeatCom-SQLite-bootstrap"},
                        ),
                        timeout=60,
                    ) as response:
                        total = 0
                        while block := response.read(1024 * 1024):
                            total += len(block)
                            if total > manifest["bytes"]:
                                raise ValueError(
                                    "Release download exceeds expected size"
                                )
                            f.write(block)
                    f.close()
                    if (
                        total != manifest["bytes"]
                        or digest(temporary) != manifest["sha256"]
                    ):
                        raise ValueError("Release download checksum mismatch")
                    os.replace(temporary, source)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
    if (
        source.stat().st_size != manifest["bytes"]
        or digest(source) != manifest["sha256"]
    ):
        raise ValueError("Compressed SQLite snapshot checksum mismatch")
    with tempfile.NamedTemporaryFile(dir=cache, suffix=".sqlite3", delete=False) as f:
        temporary = Path(f.name)
        try:
            total = 0
            with gzip.open(source, "rb") as zipped:
                while block := zipped.read(1024 * 1024):
                    total += len(block)
                    if total > manifest["sqlite_bytes"]:
                        raise ValueError("Expanded snapshot exceeds expected size")
                    f.write(block)
            f.close()
            validate_database(temporary, manifest)
            os.replace(temporary, ready)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return ready
