"""Reuse the verified Release seed while keeping each API test DB isolated."""
import shutil

import pytest

from backend.catalog import SEED_DIR
from backend.seed_download import read_manifest, snapshot_file


@pytest.fixture(scope="session")
def catalog_snapshot():
    return snapshot_file(read_manifest(SEED_DIR), SEED_DIR)


@pytest.fixture
def sqlite_database(tmp_path, catalog_snapshot):
    # Seed is a closed immutable catalog-only file; no user state is shared.
    path = tmp_path / "db.sqlite3"
    shutil.copyfile(catalog_snapshot, path)
    return path
