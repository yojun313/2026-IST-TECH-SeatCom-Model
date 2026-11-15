"""데이터셋 뷰어(/)가 백엔드에 붙어 SQLite 카탈로그를 읽기 전용으로 제공하는지."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app


@pytest.fixture
def client(sqlite_database, tmp_path):
    s = replace(
        get_settings(),
        database_path=sqlite_database,
        attachment_dir=tmp_path / "att",
        auth_jwt_algorithm="HS256",
        auth_jwt_secret="t" * 40,
    )
    with TestClient(create_app(settings=s)) as c:
        yield c


def test_viewer_at_root_does_not_shadow_backend(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="/static/app.css"' in page.text and 'window.SV_BASE = ""' in page.text
    assert client.get("/static/common.js").status_code == 200
    assert client.get("/healthz").json() == {"ok": True}
    # 백엔드 라우트가 먼저 매칭된다
    assert client.get("/health").json()["service"] == "SeatCom"
    assert client.get("/api/v1/persona/questions").status_code in (200, 401)
    assert "/api/v1/auth/login" in client.get("/openapi.json").json()["paths"]


def test_viewer_api_reads_catalog(client):
    ov = client.get("/api/overview").json()
    assert ov["counts"]["seats"] > 0 and ov["libraries"]
    users = client.get("/api/users", params={"limit": 2}).json()
    uid = users["items"][0]["_id"]
    assert client.get(f"/api/users/{uid}").json()["user"]["user_id"] == uid
    sid = client.get("/api/sessions", params={"limit": 1}).json()["items"][0]["_id"]
    assert client.get(f"/api/sessions/{sid}").json()["session"]["session_id"] == sid


def test_viewer_rejects_unsafe_queries(client):
    bad = [
        {"filter": json.dumps({"$where": "1"})},
        {"filter": json.dumps({"$or": [{"a": 1}]})},
        {"filter": json.dumps({"a') OR 1=1 --": 1})},
        {"filter": "{}", "sort": "x'); DROP TABLE auth_users; --"},
    ]
    for params in bad:
        assert client.get("/api/collections/seats", params=params).status_code == 400
    assert client.get("/api/collections/auth_users").status_code == 404
    assert client.get("/api/users", params={"skip": -1}).status_code == 422
    assert client.get("/api/stats", params={"library": "nope"}).status_code == 404
