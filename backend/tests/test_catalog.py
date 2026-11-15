import gzip
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest

from backend.catalog import Catalog, INDEX_FIELDS, SCHEMA, initialize
from backend.package_seed import package
from backend import seed_download


def seed(root, version='v1', documents=None):
    root.mkdir(exist_ok=True)
    documents = documents or [
        {'room_id': 'r1', 'library_id': 'l1', 'conditions': {'season': 'summer', 'temperature': 28}},
        {'room_id': 'r2', 'library_id': 'l1', 'conditions': {'season': 'winter'}},
    ]
    source = root / f'{version}.sqlite3'
    with sqlite3.connect(source) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO catalog_documents VALUES (?,?,?)',
                       [('rooms', i, json.dumps(doc)) for i, doc in enumerate(documents)])
        db.execute("INSERT INTO catalog_metadata VALUES ('version', ?)", (hashlib.sha256(version.encode()).hexdigest(),))
        for field in INDEX_FIELDS:
            db.execute(f"CREATE INDEX catalog_{field} ON catalog_documents(collection, json_extract(document, '$.{field}'))")
    package(source, root, 'example/seatcom', version)
    return root


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('SEATCOM_DATA_CACHE', str(tmp_path / 'cache'))
    monkeypatch.delenv('SEATCOM_SEED_PATH', raising=False)


def test_catalog_queries_and_projection(tmp_path):
    catalog = Catalog(tmp_path / 'app.sqlite3', seed_dir=seed(tmp_path / 'seed'))
    assert catalog.rooms.distinct('library_id') == ['l1']
    assert catalog.rooms.find_one({'room_id': 'missing'}) is None
    assert catalog.rooms.find_one({'room_id': 'r1'}, {'conditions.season': 1, 'room_id': 1, '_id': 0}) == {
        'room_id': 'r1', 'conditions': {'season': 'summer'}}
    assert list(catalog.rooms.find({'conditions.season': 'winter'}))[0]['room_id'] == 'r2'
    assert list(catalog.rooms.find({'room_id': "r1' OR 1=1 --"})) == []
    with pytest.raises(ValueError):
        list(catalog.rooms.find({'room_id': {'$ne': 'r1'}}))
    with pytest.raises(ValueError):
        catalog.rooms.distinct("bad' SQL")


def test_restart_and_update_preserve_user_records(tmp_path):
    path, source = tmp_path / 'app.sqlite3', seed(tmp_path / 'seed')
    initialize(path, source)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE auth_users(user_id TEXT)')
        db.execute("INSERT INTO auth_users VALUES ('existing-user')")
    initialize(path, source)
    seed(source, 'v2', [{'room_id': 'r3'}])
    initialize(path, source)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM auth_users').fetchall() == [('existing-user',)]
        assert db.execute('SELECT count(*) FROM catalog_documents').fetchone() == (1,)
        assert db.execute("SELECT value FROM catalog_metadata WHERE key='version'").fetchone() == (hashlib.sha256(b'v2').hexdigest(),)


@pytest.mark.parametrize('failure', ['checksum', 'count', 'sqlite_checksum'])
def test_failed_update_is_atomic(tmp_path, failure):
    path, source = tmp_path / 'app.sqlite3', seed(tmp_path / 'seed')
    initialize(path, source)
    seed(source, 'v2', [{'room_id': 'replacement'}])
    manifest = json.loads((source / 'release.json').read_text())
    if failure == 'checksum':
        manifest['sha256'] = '0' * 64
    elif failure == 'count':
        manifest['counts']['rooms'] = 10
    else:
        manifest['sqlite_sha256'] = '0' * 64
    (source / 'release.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        initialize(path, source)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM catalog_documents').fetchone() == (2,)
        assert db.execute("SELECT value FROM catalog_metadata WHERE key='version'").fetchone() == (hashlib.sha256(b'v1').hexdigest(),)


def test_concurrent_first_start(tmp_path):
    source, path = seed(tmp_path / 'seed'), tmp_path / 'app.sqlite3'
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: initialize(path, source), range(2)))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM catalog_documents').fetchone() == (2,)
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_download_once_then_restart_without_network(tmp_path, monkeypatch):
    source, path = seed(tmp_path / 'seed'), tmp_path / 'app.sqlite3'
    manifest = seed_download.read_manifest(source)
    asset = source / manifest['asset']
    content = asset.read_bytes()
    asset.unlink()
    calls = []
    def download(request, timeout):
        calls.append(request.full_url)
        return BytesIO(content)
    monkeypatch.setattr(seed_download, 'urlopen', download)
    initialize(path, source)
    assert calls == [manifest['url']]
    monkeypatch.setattr(seed_download, 'urlopen', lambda *a, **k: pytest.fail('No download on restart'))
    initialize(path, source)
    initialize(tmp_path / 'second.sqlite3', source)  # reuse verified extracted cache


@pytest.mark.parametrize('compressed', [False, True])
def test_offline_override(tmp_path, monkeypatch, compressed):
    source = seed(tmp_path / 'seed')
    manifest = seed_download.read_manifest(source)
    local = source / (manifest['asset'] if compressed else 'v1.sqlite3')
    monkeypatch.setenv('SEATCOM_SEED_PATH', str(local))
    monkeypatch.setattr(seed_download, 'urlopen', lambda *a, **k: pytest.fail('Offline must not download'))
    initialize(tmp_path / 'app.sqlite3', source)


def test_reject_truncated_download(tmp_path, monkeypatch):
    source = seed(tmp_path / 'seed')
    manifest = seed_download.read_manifest(source)
    asset = source / manifest['asset']
    content = asset.read_bytes()[:-8]
    asset.unlink()
    monkeypatch.setattr(seed_download, 'urlopen', lambda *a, **k: BytesIO(content))
    with pytest.raises(ValueError, match='checksum'):
        initialize(tmp_path / 'app.sqlite3', source)
    assert not list((tmp_path / 'cache').rglob('*.download'))


def test_refuse_packaging_user_data(tmp_path):
    source = seed(tmp_path / 'seed') / 'v1.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE auth_users(user_id TEXT)')
    with pytest.raises(ValueError, match='user tables'):
        package(source, tmp_path / 'release', 'example/seatcom', 'v2')
