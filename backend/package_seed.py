"""Build catalog-only GitHub Release assets; refuses runtime/user databases."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import re
import shutil
import sqlite3

from .seed_download import digest


def package(source, output, repository, tag):
    source, output = Path(source).resolve(), Path(output)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
        raise ValueError("Invalid release tag")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {"catalog_documents", "catalog_metadata"}:
            raise ValueError("Refusing to publish a database containing non-catalog/user tables")
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity check failed")
        version = db.execute("SELECT value FROM catalog_metadata WHERE key='version'").fetchone()[0]
        counts = dict(db.execute("SELECT collection,count(*) FROM catalog_documents GROUP BY collection"))
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"seatcom-library-{tag}.sqlite3.gz"
    if asset.exists():
        raise FileExistsError(f"Release asset already exists: {asset}")
    partial = asset.with_suffix(".partial")
    try:
        with source.open("rb") as src, partial.open("wb") as dest:
            with gzip.GzipFile(filename="", fileobj=dest, mode="wb", mtime=0) as zipped:
                shutil.copyfileobj(src, zipped, length=1024 * 1024)
        partial.replace(asset)
    finally:
        partial.unlink(missing_ok=True)
    manifest = {
        "format": "sqlite-gzip-v1", "version": version,
        "repository": repository, "tag": tag, "asset": asset.name,
        "url": f"https://github.com/{repository}/releases/download/{tag}/{asset.name}",
        "bytes": asset.stat().st_size, "sha256": digest(asset),
        "sqlite_bytes": source.stat().st_size, "sqlite_sha256": digest(source),
        "counts": counts,
    }
    (output / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(f"{manifest['sha256']}  {asset.name}\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("deploy/artifacts"))
    parser.add_argument("--repository", default="yojun313/2026-IST-TECH-SeatCom-Model")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    manifest = package(args.source, args.output, args.repository, args.tag)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
