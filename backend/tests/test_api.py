import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app


class FakeAnalyzer:
    def __init__(self):
        self.calls = []
        self.fail = False

    def analyze_by_GPT_OSS_API(self, source, name):
        # 원문은 파일 경로가 아니라 문자열(메모리)로 와야 한다
        assert isinstance(source, str) and not Path(source[:200]).exists()
        self.calls.append((name, source))
        if self.fail:
            raise RuntimeError("boom 비밀대화")
        return {
            "participant_id": name,
            "behavioral_summary": "테스트 분석",
            "preferences": {},
            "evidence": [],
            "seat_recommendation": "조용한 자리",
            "follow_up_question": "",
        }


@pytest.fixture
def api(tmp_path):
    settings = replace(
        get_settings(),
        database_path=tmp_path / "db.sqlite3",
        attachment_dir=tmp_path / "files",
        auth_jwt_algorithm="HS256",
        auth_jwt_secret="test-only-high-entropy-secret-at-least-32-bytes-long",
        auth_issuer="test-issuer",
        auth_audience="test-audience",
        max_txt_bytes=4096,
        lab_uploads_per_hour=5,
    )
    analyzer = FakeAnalyzer()
    app = create_app(settings=settings, analyzer=analyzer)
    c = TestClient(app)

    def auth(uid):
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        t = jwt.encode(
            {
                "sub": uid,
                "iss": "test-issuer",
                "aud": "test-audience",
                "iat": now,
                "exp": now + timedelta(hours=1),
            },
            "test-only-high-entropy-secret-at-least-32-bytes-long",
            algorithm="HS256",
        )
        return {"Authorization": "Bearer " + t}

    return c, app, auth, None, analyzer


def persona_ready(api, uid="a"):
    c, app, auth, _, _ = api
    h = auth(uid)
    for k, v in [
        ("outlet", "required"),
        ("hvac_proximity", "near"),
        ("noise_sensitivity", "high"),
        ("partition", "yes"),
    ]:
        r = c.put(f"/api/v1/persona/answers/{k}", json={"answer": v}, headers=h)
        assert r.status_code == 200, r.text
    assert (
        c.post(
            "/api/v1/persona/analyze",
            json={"analysis_type": "library_preference"},
            headers=h,
        ).status_code
        == 200
    )


def create_session(api, uid="a"):
    c, app, auth, _, _ = api
    r = c.post(
        "/api/v1/lab/sessions",
        json={"target_name": "홍길동", "consent": True},
        headers=auth(uid),
    )
    assert r.status_code == 201, r.text
    return r.json()


CHAT = (
    "테스트방 카카오톡 대화\n저장한 날짜 : 2026년 11월 15일 오후 1:00\n\n"
    "2026년 11월 15일 오후 1:01, 홍길동 : 노트북 켜고 콘센트 자리 찾는 중\n"
    "2026년 11월 15일 오후 1:02, 친구 : 창가 자리 비었어\n"
).encode()
TXT = {"Content-Type": "text/plain; charset=utf-8"}


def upload(api, s, uid="a", data=CHAT, headers=TXT):
    c, app, auth, _, _ = api
    return c.post(
        f"/api/v1/lab/sessions/{s['upload_id']}/file",
        content=data,
        headers={**auth(uid), **headers},
    )


def no_raw_on_disk(tmp_root, needle="콘센트 자리"):
    """DB·파일 어디에도 대화 원문이 없는지(SQLite WAL 포함)."""
    for f in Path(tmp_root).rglob("*"):
        if f.is_file():
            b = f.read_bytes()
            assert needle.encode() not in b and needle.encode("cp949") not in b, f


def test_questions_and_persona(api):
    c, app, auth, _, _ = api
    assert c.get("/api/v1/persona/questions").status_code == 401
    q = c.get("/api/v1/persona/questions", headers=auth("a"))
    assert q.status_code == 200 and len(q.json()["questions"]) == 4
    assert [x["id"] for x in q.json()["questions"]] == [
        "outlet",
        "hvac_proximity",
        "noise_sensitivity",
        "partition",
    ]
    bad = c.put(
        "/api/v1/persona/answers/outlet", json={"answer": "invalid"}, headers=auth("a")
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_ANSWER"
    inc = c.post(
        "/api/v1/persona/analyze",
        json={"analysis_type": "library_preference"},
        headers=auth("a"),
    )
    assert inc.status_code == 422 and len(inc.json()["error"]["missing_questions"]) == 4
    persona_ready(api)
    result = c.get("/api/v1/persona/result", headers=auth("a"))
    assert result.status_code == 200
    assert result.json()["seat_preferences"]["partition"] == "yes"
    assert c.get("/api/v1/persona/result", headers=auth("b")).status_code == 404
    # Updating a single answer invalidates previous saved result.
    c.put("/api/v1/persona/answers/partition", json={"answer": "no"}, headers=auth("a"))
    assert c.get("/api/v1/persona/result", headers=auth("a")).status_code == 404
    assert (
        c.get("/api/v1/persona/answers", headers=auth("a")).json()["answered_count"]
        == 4
    )


@pytest.mark.parametrize("english_export", [False, True])
def test_lab_upload_memory_only(api, tmp_path, english_export):
    c, app, auth, _, analyzer = api
    assert (
        c.post(
            "/api/v1/lab/sessions",
            headers=auth("a"),
            json={"target_name": "홍길동", "consent": True},
        ).status_code
        == 404
    )
    persona_ready(api)
    assert (
        c.post(
            "/api/v1/lab/sessions",
            headers=auth("a"),
            json={"target_name": "홍길동", "consent": False},
        ).status_code
        == 400
    )
    s = create_session(api)
    assert s["status"] == "waiting_upload" and "recipient_email" not in s
    # 다른 사용자: 세션이 있는지도 모른다(404), 업로드도 못 한다
    assert (
        c.get(
            f"/api/v1/lab/sessions/{s['upload_id']}/status", headers=auth("b")
        ).status_code
        == 404
    )
    assert upload(api, s, uid="b").status_code == 404
    data = CHAT
    if english_export:
        data = (
            "Talk_2026.11.15 05:34-1.txt\n"
            "Date Saved : Nov 15, 2026 at 1:04\u202fPM\n\n"
            "Thursday, June 4, 2026\n"
            "Jun 4, 2026 at 3:55\u202fPM: 친구 invited 홍길동 .\n"
            "Jun 4, 2026 at 3:56\u202fPM, 홍길동 : 노트북 켜고 콘센트 자리 찾는 중\n"
            "Jun 4, 2026 at 3:57\u202fPM, 친구 : 창가 자리 비었어\n"
        ).encode("utf-8-sig")
    r = upload(api, s, data=data)
    assert r.status_code == 202, r.text
    assert app.state.service.wait_idle()
    status = c.get(
        f"/api/v1/lab/sessions/{s['upload_id']}/status", headers=auth("a")
    ).json()
    assert status["status"] == "completed" and status["file_received"] is True
    aid = status["analysis"]["analysis_id"]
    result = c.get(f"/api/v1/lab/analyses/{aid}", headers=auth("a"))
    assert (
        result.status_code == 200
        and result.json()["analysis"]["participant_id"] == "홍길동"
    )
    assert len(analyzer.calls) == 1 and "콘센트 자리" in analyzer.calls[0][1]
    assert not app.state.service._jobs, "분석 후 메모리에서 원문 제거"
    assert upload(api, s).status_code == 409, "같은 세션에 두 번 업로드 불가"
    assert c.get(f"/api/v1/lab/analyses/{aid}", headers=auth("b")).status_code == 404
    no_raw_on_disk(tmp_path)
    assert (
        c.delete(
            f"/api/v1/lab/sessions/{s['upload_id']}", headers=auth("a")
        ).status_code
        == 200
    )
    assert c.get(f"/api/v1/lab/analyses/{aid}", headers=auth("a")).status_code == 404


def test_lab_upload_validation(api, tmp_path, caplog):
    c, app, auth, _, analyzer = api
    persona_ready(api)
    s = create_session(api)
    # multipart·JSON 은 거절(임시파일·본문 로그 위험)
    assert (
        upload(
            api, s, headers={"Content-Type": "multipart/form-data; boundary=x"}
        ).status_code
        == 415
    )
    assert (
        upload(api, s, headers={"Content-Type": "application/json"}).status_code == 415
    )
    assert upload(api, s, data=b"x" * 5000).status_code == 413
    assert upload(api, s, data=b"a\x00b").status_code == 422
    assert (
        upload(api, s, data="그냥 메모 파일".encode()).json()["error"]["code"]
        == "INVALID_KAKAO_TXT"
    )
    assert (
        upload(api, s, data=CHAT.replace("홍길동".encode(), "김철수".encode())).json()[
            "error"
        ]["code"]
        == "TARGET_NOT_FOUND"
    )
    # cp949 로 내보낸 파일도 받는다
    assert upload(api, s, data=CHAT.decode().encode("cp949")).status_code == 202
    assert app.state.service.wait_idle()
    # 분석 실패: 상태는 failed, 로그에 예외 메시지(대화 조각일 수 있음)가 남지 않는다
    analyzer.fail = True
    s2 = create_session(api)
    with caplog.at_level("DEBUG"):
        assert upload(api, s2).status_code == 202
        assert app.state.service.wait_idle()
    st = c.get(
        f"/api/v1/lab/sessions/{s2['upload_id']}/status", headers=auth("a")
    ).json()
    assert st["status"] == "failed" and st["error"]["code"] == "AI_ANALYSIS_FAILED"
    assert "비밀대화" not in caplog.text and "콘센트" not in caplog.text
    # 시간당 업로드 횟수 제한(설정 5회: 지금까지 2회 성공)
    codes = []
    for _ in range(4):
        codes.append(upload(api, create_session(api)).status_code)
        app.state.service.wait_idle()
    assert codes == [202, 202, 202, 429]
    no_raw_on_disk(tmp_path)


def test_lab_restart_and_legacy_purge(api, tmp_path):
    c, app, auth, _, analyzer = api
    persona_ready(api)
    svc = app.state.service
    legacy = svc.settings.attachment_dir
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "file_old.txt").write_text("예전 메일 첨부 원문")
    s = create_session(api)
    with svc.store.db() as db:  # 재시작 직전에 분석 중이던 세션
        db.execute(
            "UPDATE lab_sessions SET status='analyzing',filename='KakaoTalk_친구.txt' WHERE upload_id=?",
            (s["upload_id"],),
        )
    svc.purge_legacy_raw()
    assert not legacy.exists()
    st = c.get(
        f"/api/v1/lab/sessions/{s['upload_id']}/status", headers=auth("a")
    ).json()
    assert st["status"] == "failed" and st["error"]["code"] == "UPLOAD_LOST"
    with svc.store.db() as db:
        assert db.execute("SELECT filename FROM lab_sessions").fetchone()[0] is None


def test_lab_guest_blocked(api):
    c, app, auth, _, _ = api
    persona_ready(api, uid="guest-abc")
    assert (
        c.post(
            "/api/v1/lab/sessions",
            headers=auth("guest-abc"),
            json={"target_name": "홍길동", "consent": True},
        ).status_code
        == 403
    )


def test_input_and_token_guard(api):
    c, app, auth, identity, analyzer = api
    assert (
        c.get(
            "/api/v1/persona/questions", headers={"Authorization": "Bearer faked"}
        ).status_code
        == 401
    )
    assert (
        c.put(
            "/api/v1/persona/answers/bad-id", json={"answer": "yes"}, headers=auth("a")
        ).status_code
        == 404
    )
    assert (
        c.put(
            "/api/v1/persona/answers/partition",
            json={"answer": "yes", "hidden": "other"},
            headers=auth("a"),
        ).status_code
        == 422
    )
    assert c.post("/internal/v1/lab/attachments", json={}).status_code == 404, (
        "메일 수집용 내부 API 제거"
    )
    assert c.post("/internal/v1/lab/email-received", json={}).status_code == 404
