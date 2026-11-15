"""Reuse the verified Release seed while keeping each API test DB isolated."""

import os
import shutil

import pytest

from backend.catalog import SEED_DIR

os.environ.setdefault(
    "SEATVIZ_WARM", "0"
)  # 테스트마다 뷰어 통계를 미리 계산하지 않는다
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
