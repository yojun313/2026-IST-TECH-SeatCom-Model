"""좌석 추천 API 통합 테스트(실제 모델 + MongoDB 읽기). 모델·DB가 없으면 건너뛴다.
deploy/.venv/bin/python -m pytest backend/tests -q   (저장소 루트에서)
"""

from dataclasses import replace
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app

ROOM = "yongin-giheung:room:1"


@pytest.fixture()
def client(tmp_path):
    s = replace(
        get_settings(),
        database_path=tmp_path / "db.sqlite3",
        attachment_dir=tmp_path / "att",
        allow_guest_tokens=True,
    )
    if not (Path(s.model_dir) / "manifest.json").exists():
        pytest.skip("모델 없음")
    app = create_app(settings=s)
    with TestClient(app) as c:
        if not c.get("/api/v1/system/model").json()["loaded"]:
            pytest.skip("모델 로드 실패")
        yield c


def H(tok="mock-token-0b5d1c9e-1111-4222-8333-944455556666"):
    return {"Authorization": f"Bearer {tok}"}


def test_guest_token_rules(client):
    assert client.get("/api/v1/persona/questions", headers=H()).status_code == 200
    assert client.get(
        "/api/v1/persona/questions", headers=H("mock-token-x")
    ).status_code in (401, 503)  # 너무 짧음
    assert client.get("/api/v1/persona/questions").status_code == 401


def test_full_flow_learns_from_selection(client):
    for q, a in (
        ("outlet", "required"),
        ("hvac_proximity", "not_near"),
        ("noise_sensitivity", "high"),
        ("partition", "yes"),
    ):
        assert (
            client.put(
                f"/api/v1/persona/answers/{q}", json={"answer": a}, headers=H()
            ).status_code
            == 200
        )
    assert (
        client.post(
            "/api/v1/persona/analyze",
            json={"analysis_type": "library_preference"},
            headers=H(),
        ).status_code
        == 200
    )

    m = client.get(f"/api/v1/rooms/{ROOM}/map", headers=H()).json()
    assert m["seats"] and all(0 <= s["x"] <= 1 and 0 <= s["y"] <= 1 for s in m["seats"])
    av = client.get(f"/api/v1/rooms/{ROOM}/availability", headers=H()).json()
    occ = {s["seatId"] for s in av["seatStates"] if s["state"] == "occupied"}

    r = client.post(
        "/api/v1/recommendations/seats",
        json={
            "roomId": ROOM,
            "options": {"topK": 5, "availabilityPolicy": "allowUnknown"},
        },
        headers=H(),
    )
    assert r.status_code == 200, r.text
    rec = r.json()
    res = rec["result"]["results"]
    assert rec["result"]["outcome"] == "ranked" and 1 <= len(res) <= 5
    assert not ({x["seatId"] for x in res} & occ), "사용 중 좌석은 추천하지 않는다"
    assert all(x["mapAnchor"] and x["reasons"] for x in res)
    assert res[0]["targetId"] in rec["displayNames"]
    rid = rec["recommendationId"]

    vis = [x["targetId"] for x in res[:2]]
    e = client.post(
        f"/api/v1/recommendations/{rid}/events",
        json={
            "eventId": "e1",
            "occurredAt": "2026-11-15T04:00:00Z",
            "type": "recommendationExposed",
            "visibleTargetIds": vis,
        },
        headers=H(),
    )
    assert e.status_code == 200 and e.json()["effect"] == "storedOnly"
    e = client.post(
        f"/api/v1/recommendations/{rid}/events",
        json={
            "eventId": "e2",
            "occurredAt": "2026-11-15T04:00:01Z",
            "type": "candidateSelected",
            "targetId": vis[1],
            "origin": "recommendation",
        },
        headers=H(),
    )
    assert e.status_code == 200 and e.json()["effect"] == "learned", e.text
    dup = client.post(
        f"/api/v1/recommendations/{rid}/events",
        json={
            "eventId": "e2",
            "type": "candidateSelected",
            "targetId": vis[1],
            "origin": "recommendation",
        },
        headers=H(),
    )
    assert dup.json().get("duplicate") is True, "같은 eventId는 한 번만 학습"

    ins = client.get("/api/v1/users/me/preferences/insights", headers=H()).json()
    assert ins["historySessions"] == 1 and "persona" in ins["profileSources"]

    # 고른 좌석은 이제 사용 중으로 보이고 추천에서 빠진다
    av2 = client.get(f"/api/v1/rooms/{ROOM}/availability", headers=H()).json()
    assert {s["seatId"]: s["state"] for s in av2["seatStates"]}[vis[1]] == "occupied"

    # 거절(다시 추천하지 않기)
    rec2 = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=H()
    ).json()
    t = rec2["result"]["results"][0]["targetId"]
    e = client.post(
        f"/api/v1/recommendations/{rec2['recommendationId']}/events",
        json={
            "eventId": "e3",
            "type": "candidateRejected",
            "targetId": t,
            "reason": "시끄러움",
            "excludeFromFuture": True,
        },
        headers=H(),
    )
    assert (
        e.json()["effect"] == "preferencesUpdated"
    )  # '시끄러움' → 조용한 쪽으로 취향 보정 + 제외
    rec3 = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=H()
    ).json()
    assert t not in [x["targetId"] for x in rec3["result"]["results"]]

    # 다른 사용자는 남의 추천에 이벤트를 남길 수 없다
    other = H("mock-token-ffffffff-2222-4333-8444-955566667777")
    assert (
        client.post(
            f"/api/v1/recommendations/{rec3['recommendationId']}/events",
            json={
                "eventId": "e4",
                "type": "recommendationExposed",
                "visibleTargetIds": [],
            },
            headers=other,
        ).status_code
        == 404
    )


def test_new_user_without_answers_gets_recommendation(client):
    tok = H("mock-token-aaaaaaaa-3333-4444-8555-966677778888")
    r = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
    ).json()
    assert r["result"]["results"] and r["result"]["warnings"]
    assert client.get("/api/v1/rooms/unknown-room/map", headers=tok).status_code == 404


def test_libraries_rooms_and_room_recommendation(client):
    libs = client.get("/api/v1/libraries", headers=H()).json()["items"]
    assert len(libs) == 11 and all(
        l["roomCount"] >= 1 and l["totalSeats"] > 0 for l in libs
    )
    gh = next(l for l in libs if l["libraryId"] == "yongin-giheung")
    rooms = client.get("/api/v1/libraries/yongin-giheung/rooms", headers=H()).json()[
        "items"
    ]
    assert rooms[0]["roomId"] == ROOM and rooms[0]["hours"]["today"]
    assert client.get("/api/v1/libraries/nowhere/rooms", headers=H()).status_code == 404
    rr = client.post(
        "/api/v1/recommendations/rooms", json={"options": {"topK": 3}}, headers=H()
    ).json()
    assert (
        rr["outcome"] == "ranked"
        and len(rr["results"]) == 3
        and rr["results"][0]["bestSeats"]
    )
    one = client.post(
        "/api/v1/recommendations/rooms",
        json={"libraryIds": ["yongin-giheung"]},
        headers=H(),
    ).json()
    assert all(x["libraryId"] == "yongin-giheung" for x in one["results"])
    assert gh["region"] == "경기 용인"


def test_preferences_history_and_use_flow(client):
    tok = H("mock-token-bbbbbbbb-3333-4444-8555-966677778888")
    p = client.get("/api/v1/users/me/preferences", headers=tok).json()
    assert p["revision"] == 0 and len(p["catalog"]) == 10
    body = {
        "expectedRevision": 0,
        "laptopRequired": True,
        "items": [
            {"featureId": "windowProximity", "choice": "near", "importance": 3},
            {"featureId": "partition", "choice": "yes", "importance": 3},
        ],
    }
    p2 = client.put("/api/v1/users/me/preferences", json=body, headers=tok).json()
    assert (
        p2["revision"] == 1
        and "explicit" in p2["effective"]["sources"]
        and p2["effective"]["laptopRequired"]
    )
    assert (
        client.put("/api/v1/users/me/preferences", json=body, headers=tok).status_code
        == 409
    )  # 오래된 revision
    bad = {
        **body,
        "expectedRevision": 1,
        "items": [{"featureId": "nope", "choice": "x", "importance": 2}],
    }
    assert (
        client.put("/api/v1/users/me/preferences", json=bad, headers=tok).status_code
        == 422
    )

    rec = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
    ).json()
    top = rec["result"]["results"]
    assert top and all(
        "칸막이" in [r["text"] for r in x["reasons"]] or x["reasons"] for x in top
    )
    # 지도에서 직접 고른(추천 밖) 좌석
    m = client.get(f"/api/v1/rooms/{ROOM}/map", headers=tok).json()
    av = {
        s["seatId"]: s["state"]
        for s in client.get(f"/api/v1/rooms/{ROOM}/availability", headers=tok).json()[
            "seatStates"
        ]
    }
    outside = next(
        s["seatId"]
        for s in m["seats"]
        if av[s["seatId"]] == "available"
        and s["seatId"] not in [x["targetId"] for x in top]
    )
    rid = rec["recommendationId"]
    e = client.post(
        f"/api/v1/recommendations/{rid}/events",
        json={
            "eventId": "h1",
            "type": "candidateSelected",
            "targetId": outside,
            "origin": "outsideRecommendations",
        },
        headers=tok,
    ).json()
    assert e["effect"] == "learned"
    h = client.get("/api/v1/users/me/history", headers=tok).json()
    assert (
        h["current"]["seatId"] == outside
        and h["current"]["origin"] == "outsideRecommendations"
    )
    assert (
        client.post(
            f"/api/v1/recommendations/{rid}/events",
            json={"eventId": "h2", "type": "useConfirmed", "targetId": outside},
            headers=tok,
        ).json()["effect"]
        == "historyUpdated"
    )
    assert (
        client.post(
            f"/api/v1/recommendations/{rid}/events",
            json={
                "eventId": "h3",
                "type": "ratingSubmitted",
                "targetId": outside,
                "rating": 4,
            },
            headers=tok,
        ).json()["effect"]
        == "storedOnly"
    )
    assert (
        client.post(
            f"/api/v1/recommendations/{rid}/events",
            json={"eventId": "h4", "type": "useEnded", "targetId": outside},
            headers=tok,
        ).json()["effect"]
        == "historyUpdated"
    )
    h = client.get("/api/v1/users/me/history", headers=tok).json()
    assert (
        h["current"] is None
        and h["items"][0]["status"] == "ended"
        and h["items"][0]["rating"] == 4
    )
    av2 = {
        s["seatId"]: s["state"]
        for s in client.get(f"/api/v1/rooms/{ROOM}/availability", headers=tok).json()[
            "seatStates"
        ]
    }
    assert av2[outside] == "available", "이용을 끝낸 좌석은 다시 빈자리"
    ins = client.get("/api/v1/users/me/preferences/insights", headers=tok).json()
    assert (
        ins["historySessions"] == 1
        and ins["items"]
        and ins["items"][0]["evidence"]["observations"] == 1
    )
    assert (
        client.post(
            f"/api/v1/recommendations/{rid}/events",
            json={"eventId": "h5", "type": "useEnded", "targetId": top[0]["targetId"]},
            headers=tok,
        ).status_code
        == 409
    )


def test_floor_and_seat_explanation(client):
    tok = H("mock-token-cccccccc-3333-4444-8555-966677778888")
    for q, a in (
        ("outlet", "required"),
        ("hvac_proximity", "not_near"),
        ("noise_sensitivity", "high"),
        ("partition", "yes"),
    ):
        client.put(f"/api/v1/persona/answers/{q}", json={"answer": a}, headers=tok)
    f = client.get(f"/api/v1/rooms/{ROOM}/floor", headers=tok).json()
    assert (
        f["seats"]
        and f["deskGroups"]
        and f["airConditioners"]
        and f["windows"]
        and f["entrances"]
    )
    rec = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
    ).json()
    sid = rec["result"]["results"][0]["targetId"]
    ex = client.get(
        f"/api/v1/recommendations/{rec['recommendationId']}/seats/{sid}", headers=tok
    ).json()
    assert (
        ex["rank"] == 1
        and ex["available"]
        and ex["reasons"]
        and len(ex["facts"]) >= 10
        and ex["matches"]
    )
    import re

    texts = (
        [ex["summary"]]
        + [r["text"] for r in ex["reasons"]]
        + [x["value"] for x in ex["facts"]]
        + [x["detail"] for x in ex["facts"]]
    )
    bad = [
        t for t in texts if re.search(r"\d", t) and "4인" not in t and "1인" not in t
    ]
    assert not bad, f"설명 문장에는 숫자를 넣지 않는다: {bad}"
    assert (
        client.get(
            f"/api/v1/recommendations/{rec['recommendationId']}/seats/nope", headers=tok
        ).status_code
        == 404
    )
    print(
        "\n"
        + json.dumps(
            {k: ex[k] for k in ("summary", "reasons", "cautions", "matches", "nearby")},
            ensure_ascii=False,
            indent=1,
        )
    )


def _pct(client, rec, feature):
    """추천 상위 3석의 특징값이 그때 빈자리 중 몇 번째 백분위인지(평균)"""
    import numpy as np

    reco = client.app.state.reco
    r = reco.room(rec["result"]["results"][0]["roomId"])
    with reco.store.db() as db:
        occ = set(
            json.loads(
                db.execute(
                    "SELECT occupied_json FROM reco_recommendations WHERE recommendation_id=?",
                    (rec["recommendationId"],),
                ).fetchone()[0]
            )
        )
    free = [s for s in r.feat if s not in occ]
    val = lambda s: (
        r.feat[s][feature]["noon"] if feature == "brightness" else r.feat[s][feature]
    )
    vals = np.array([val(s) for s in free])
    return (
        float(
            np.mean(
                [
                    (vals < val(x["targetId"])).mean()
                    for x in rec["result"]["results"][:3]
                ]
            )
        ),
        free,
        vals,
    )


def test_repeated_choices_shift_recommendations(client):
    """에어컨 바람이 센 자리만 반복해서 고르면(인구 평균과 반대) 추천도 바람 센 자리로 옮겨 간다."""
    tok = H("mock-token-dddddddd-3333-4444-8555-966677778888")
    rec = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
    ).json()
    before, free, vals = _pct(client, rec, "airflow")
    for t in range(3):
        if t:
            rec = client.post(
                "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
            ).json()
            _, free, vals = _pct(client, rec, "airflow")
        pick = free[int(vals.argmax())]
        client.post(
            f"/api/v1/recommendations/{rec['recommendationId']}/events",
            json={
                "eventId": f"rc{t}",
                "type": "candidateSelected",
                "targetId": pick,
                "origin": "outsideRecommendations",
            },
            headers=tok,
        )
    after, _, _ = _pct(
        client,
        client.post(
            "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
        ).json(),
        "airflow",
    )
    # 처음 값은 10분마다 바뀌는 모의 빈자리에 따라 달라진다: 충분히 오르거나(+0.3) 이미 거의 끝까지(0.9 이상) 가면 된다
    assert after > before and after >= min(before + 0.3, 0.9), (before, after)


def test_reject_reason_corrects_preference(client):
    """'입구에서 너무 멀어요'로 거절하면 다음 추천은 출입구 쪽으로 간다(착석 기록이 없어도)."""
    tok = H("mock-token-eeeeeeee-3333-4444-8555-966677778888")
    for q, a in (
        ("outlet", "not_required"),
        ("hvac_proximity", "no_preference"),
        ("noise_sensitivity", "none"),
        ("partition", "no_preference"),
    ):
        client.put(f"/api/v1/persona/answers/{q}", json={"answer": a}, headers=tok)
    rec = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
    ).json()
    before, _, _ = _pct(client, rec, "entranceProximity")
    for i, x in enumerate(rec["result"]["results"][:2]):
        e = client.post(
            f"/api/v1/recommendations/{rec['recommendationId']}/events",
            json={
                "eventId": f"rj{i}",
                "type": "candidateRejected",
                "targetId": x["targetId"],
                "reason": "입구에서 너무 멀어요",
                "excludeFromFuture": True,
            },
            headers=tok,
        ).json()
        assert e["effect"] == (
            "preferencesUpdated" if i == 0 else "excluded"
        )  # 같은 추천에서는 한 번만 보정
    rec2 = client.post(
        "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
    ).json()
    after, _, _ = _pct(client, rec2, "entranceProximity")
    assert after >= before, (before, after)
    for i in range(2):  # 다시 두 번 더 거절하면 확실히 출입구 쪽
        r = client.post(
            "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
        ).json()
        client.post(
            f"/api/v1/recommendations/{r['recommendationId']}/events",
            json={
                "eventId": f"rk{i}",
                "type": "candidateRejected",
                "targetId": r["result"]["results"][0]["targetId"],
                "reason": "입구에서 너무 멀어요",
                "excludeFromFuture": True,
            },
            headers=tok,
        )
    final, _, _ = _pct(
        client,
        client.post(
            "/api/v1/recommendations/seats", json={"roomId": ROOM}, headers=tok
        ).json(),
        "entranceProximity",
    )
    assert final > before + 0.2, (before, after, final)
