"""추론·연속 반영 통합 테스트(모델 저장 후).  seat_model/.venv/bin/python -m pytest seat_model/tests -q -s"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from seat_model.train.common import FeatureReader, load_config
from seat_model.train.run import MODEL_VERSION

CFG = load_config()
MDIR = Path(CFG["paths"]["models"]) / MODEL_VERSION
pytestmark = pytest.mark.skipif(
    not (MDIR / "manifest.json").exists(), reason="모델 없음"
)


def _rank(rec, user, snap, seat):
    r = [x["seat_id"] for x in rec.recommend(user, snap, k=10_000)["recommendations"]]
    return r.index(seat) + 1


def test_recommend_observe_profile(tmp_path):
    from seat_model.train.inference import SeatRecommender, snapshot_from_db
    from seat_model.train.online import OnlineState

    rec = SeatRecommender.load(MDIR)
    rd = FeatureReader()
    meta = pd.read_parquet(Path(CFG["paths"]["exports"]) / "sessions.parquet")
    sid = meta[
        (meta.split == "test") & (meta.n_avail >= 20) & ~meta.chosen_exposed
    ].session_id.iloc[0]
    snap, uid, chosen, exposed = snapshot_from_db(rd, sid)
    user = next(iter(rd.find("synth_users", {"user_id": uid})))

    out = rec.recommend(user, snap, k=5)
    n_free = len(snap["seats"]) - len(snap["occupied_seat_ids"])
    assert len(out["recommendations"]) == min(
        5, n_free - out["excluded_by_requirements"]
    )  # 필수 조건은 하드 필터
    assert all(r["meets_requirements"] for r in out["recommendations"])

    # 취향 미입력 새 사용자: user_id만으로 추천된다(인구 평균 + 상황)
    new = {"user_id": "new-user:test"}
    out0 = rec.recommend(new, snap, k=5)
    assert out0["history_sessions"] == 0 and len(out0["recommendations"]) == 5
    r_before = _rank(rec, new, snap, chosen)

    # 착석할 때마다 바로 반영(재학습 없음)
    mu0 = rec.online.bayes["new-user:test"].mu.copy()
    for _ in range(4):
        rec.observe("new-user:test", snap, chosen, exposed_rank=exposed)
    st, bu = rec.online.get("new-user:test")
    assert st.n == 4 and bu.n == 4 and not np.allclose(mu0, bu.mu)
    r_after = _rank(rec, new, snap, chosen)
    assert r_after < r_before, (
        "같은 좌석을 고를수록 그 좌석(과 비슷한 좌석)이 위로 올라와야 한다"
    )

    prof = rec.inferred_profile("new-user:test")
    assert prof["n_sessions"] == 4 and isinstance(prof["preferences"], list)

    # 상태 저장·복원 후 같은 추천
    p = rec.save_state(tmp_path / "state.json")
    rec2 = SeatRecommender.load(MDIR)
    rec2.online = OnlineState.from_dict(json.loads(p.read_text()))
    a = [r["seat_id"] for r in rec.recommend(new, snap, k=5)["recommendations"]]
    b = [r["seat_id"] for r in rec2.recommend(new, snap, k=5)["recommendations"]]
    assert a == b

    print(
        "\n[취향 입력 사용자] 상위 3:",
        [(r["seat_id"], r["reason_text"]) for r in out["recommendations"][:3]],
    )
    print(
        f"[새 사용자] 고른 좌석 순위: 착석 0회 {r_before}위 → 4회 후 {r_after}위 (빈자리 {len(snap['seats']) - len(snap['occupied_seat_ids'])}개)"
    )
    print(
        "[새 사용자] 추정 취향:",
        [
            (q["featureId"], q["target"], q["importance"], q["confidence"])
            for q in prof["preferences"][:4]
        ],
    )
