"""회원가입(이메일 인증)·로그인. SMTP는 가짜로 바꿔 실제 메일을 보내지 않는다."""

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend import auth as A
from backend.config import get_settings
from backend.main import create_app


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture()
def c(tmp_path, monkeypatch, sqlite_database):
    monkeypatch.setattr(A.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("MAIL_SENDER", "sender@example.com")
    monkeypatch.setenv("MAIL_PASSWORD", "app-password")
    monkeypatch.setattr(A, "CODE_RESEND_S", 0)
    FakeSMTP.sent.clear()
    s = replace(
        get_settings(),
        database_path=sqlite_database,
        attachment_dir=tmp_path / "att",
        auth_jwt_algorithm="HS256",
        auth_jwt_secret="t" * 40,
        auth_issuer="seatcom",
        auth_audience="seatcom-web",
        allow_guest_tokens=False,
    )
    with TestClient(create_app(settings=s)) as client:
        yield client


def last_code():
    body = FakeSMTP.sent[-1].get_body(("plain",)).get_content()
    return re.search(r"(\d{6})", body).group(1)


def test_signup_login_flow(c):
    assert (
        c.post(
            "/api/v1/auth/email-codes", json={"email": "User@Example.com"}
        ).status_code
        == 200
    )
    assert FakeSMTP.sent[-1]["To"] == "user@example.com"
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    r = c.post(
        "/api/v1/auth/signup",
        json={
            "email": "user@example.com",
            "code": wrong,
            "nickname": "열람왕",
            "password": "secret12",
        },
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CODE"
    r = c.post(
        "/api/v1/auth/signup",
        json={
            "email": "user@example.com",
            "code": code,
            "nickname": "열람왕",
            "password": "secret12",
        },
    )
    assert r.status_code == 201, r.text
    # 같은 코드는 다시 못 쓴다, 같은 이메일은 다시 가입 못 한다
    assert (
        c.post("/api/v1/auth/email-codes", json={"email": "user@example.com"}).json()[
            "error"
        ]["code"]
        == "EMAIL_TAKEN"
    )
    r = c.post("/api/v1/auth/login", json={"nickname": "열람왕", "password": "nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    tok = c.post(
        "/api/v1/auth/login", json={"nickname": "열람왕", "password": "secret12"}
    ).json()["accessToken"]
    h = {"Authorization": f"Bearer {tok}"}
    me = c.get("/api/v1/auth/me", headers=h).json()
    assert me["email"] == "user@example.com" and me["nickname"] == "열람왕"
    assert c.get("/api/v1/persona/questions", headers=h).status_code == 200
    # 게스트 토큰은 꺼져 있다
    assert (
        c.get(
            "/api/v1/persona/questions",
            headers={"Authorization": "Bearer mock-token-abcdefgh-1234"},
        ).status_code
        == 401
    )


def test_duplicate_nickname_and_lockout(c):
    for email in ("a@example.com", "b@example.com"):
        c.post("/api/v1/auth/email-codes", json={"email": email})
        r = c.post(
            "/api/v1/auth/signup",
            json={
                "email": email,
                "code": last_code(),
                "nickname": "같은이름",
                "password": "secret12",
            },
        )
    assert r.status_code == 409 and r.json()["error"]["code"] == "NICKNAME_TAKEN"
    c.post("/api/v1/auth/email-codes", json={"email": "c@example.com"})
    good = last_code()
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        c.post(
            "/api/v1/auth/signup",
            json={
                "email": "c@example.com",
                "code": bad,
                "nickname": "c",
                "password": "secret12",
            },
        )
    r = c.post(
        "/api/v1/auth/signup",
        json={
            "email": "c@example.com",
            "code": good,
            "nickname": "c",
            "password": "secret12",
        },
    )
    assert r.json()["error"]["code"] == "CODE_LOCKED", "5번 틀리면 맞는 코드도 막는다"


def _signup(c, email="a@example.com", nick="자리왕", pw="secret12"):
    c.post("/api/v1/auth/email-codes", json={"email": email})
    r = c.post(
        "/api/v1/auth/signup",
        json={"email": email, "code": last_code(), "nickname": nick, "password": pw},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def test_password_reset(c):
    old = _signup(c)
    # 가입 안 한 이메일은 재설정 코드를 못 받는다
    r = c.post(
        "/api/v1/auth/email-codes",
        json={"email": "none@example.com", "purpose": "reset"},
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "EMAIL_NOT_FOUND"
    assert (
        c.post(
            "/api/v1/auth/email-codes",
            json={"email": "A@example.com", "purpose": "reset"},
        ).status_code
        == 200
    )
    assert "비밀번호 재설정" in FakeSMTP.sent[-1]["Subject"]
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    assert (
        c.post(
            "/api/v1/auth/password-reset",
            json={"email": "a@example.com", "code": wrong, "password": "newpass1"},
        ).status_code
        == 400
    )
    r = c.post(
        "/api/v1/auth/password-reset",
        json={"email": "a@example.com", "code": code, "password": "newpass1"},
    )
    assert r.status_code == 200 and r.json()["nickname"] == "자리왕"
    assert (
        c.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {r.json()['accessToken']}"},
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/v1/auth/login", json={"nickname": "자리왕", "password": "secret12"}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/api/v1/auth/login", json={"nickname": "자리왕", "password": "newpass1"}
        ).status_code
        == 200
    )
    # 같은 코드는 다시 못 쓴다
    assert (
        c.post(
            "/api/v1/auth/password-reset",
            json={"email": "a@example.com", "code": code, "password": "again12"},
        ).status_code
        == 400
    )


def test_password_reset_revokes_old_tokens(c, monkeypatch):
    old = _signup(c)
    t1 = A.time.time() + 100  # 토큰을 받은 뒤에 비밀번호를 바꿈
    monkeypatch.setattr(A.time, "time", lambda: t1)
    c.post(
        "/api/v1/auth/email-codes", json={"email": "a@example.com", "purpose": "reset"}
    )
    assert (
        c.post(
            "/api/v1/auth/password-reset",
            json={
                "email": "a@example.com",
                "code": last_code(),
                "password": "newpass1",
            },
        ).status_code
        == 200
    )
    assert c.get("/api/v1/auth/me", headers=old).status_code == 401


def test_delete_account(c):
    h = _signup(c)
    c.put("/api/v1/persona/answers/q1", headers=h, json={"answer": "A"})
    r = c.request("DELETE", "/api/v1/auth/me", headers=h, json={"password": "wrong1"})
    assert r.status_code == 401
    r = c.request("DELETE", "/api/v1/auth/me", headers=h, json={"password": "secret12"})
    assert r.status_code == 200 and r.json()["deleted"]
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401  # 토큰도 끊긴다
    assert (
        c.post(
            "/api/v1/auth/login", json={"nickname": "자리왕", "password": "secret12"}
        ).status_code
        == 401
    )
    db = c.app.state.service.store
    with db.db() as conn:
        for t in ("auth_users", "persona_answers", "reco_user_state"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    # 같은 이메일·닉네임으로 다시 가입할 수 있다
    _signup(c)
