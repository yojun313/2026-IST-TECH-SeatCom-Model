"""누설·시간 순서 테스트.  seat_model/.venv/bin/python -m pytest seat_model/tests -q"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from seat_model.train import common as C
from seat_model.train.data import FEATURE_COLS, UserPrefs
from seat_model.train.online import BayesUser
from seat_model.train.userstate import SEAT_KEYS

EXP = Path(C.load_config()["paths"]["exports"])
POST_CHOICE = (
    "use_confirmed",
    "stay",
    "early_leave",
    "moved",
    "satisfaction",
    "rating",
    "origin",
    "true_utility",
    "preference_fit",
    "u_true",
)


def test_feature_names_clean():
    for c in FEATURE_COLS:
        low = c.lower()
        assert not any(t in low for t in C.FORBIDDEN_TOKENS if t not in ("reason",)), c
        assert not any(t in low for t in POST_CHOICE), c


def test_reader_blocks_truth_and_feedback():
    class Fake(C.FeatureReader):
        def __init__(self):  # DB 접속 없이 가드만 검사
            pass

    r = Fake()
    with pytest.raises(C.LeakageError):
        r.find("synth_events")  # ratingSubmitted(만족도) 포함
    with pytest.raises(C.LeakageError):
        r.find("synth_users", fields=["latent"])
    with pytest.raises(C.LeakageError):
        r.find("synth_sessions", fields=["satisfaction"])
    with pytest.raises(C.LeakageError):
        r.find("synth_sessions", fields=["chosen_true_utility"])
    with pytest.raises(C.LeakageError):
        r.find(
            "synth_users",
            fields=["kakaotalk_profile.preferences.view.is_correct_vs_latent"],
        )
    with pytest.raises(C.LeakageError):
        C.check_fields(["latent.utility_weights"])


def test_feature_code_never_reads_truth():
    src = (Path(C.__file__).parent / "data.py").read_text(encoding="utf-8")
    assert "TruthReader" not in src and "eval_only" not in src and "u_true" not in src
    for f in ("userstate.py", "online.py", "inference.py"):
        s = (Path(C.__file__).parent / f).read_text(encoding="utf-8")
        assert "TruthReader" not in s and "eval_only" not in s


def test_prefs_ignore_latent():
    doc = {
        "preference_profile": {
            "preferences": [{"featureId": "airflow", "target": 0.05, "importance": 0.6}]
        },
        "requirements": [],
        "kakaotalk_profile": {"preferences": {"desk": {"inference_type": "direct"}}},
    }
    a = UserPrefs(doc)
    b = UserPrefs(
        {
            **doc,
            "latent": {"utility_weights": {"airflow": {"target": 1, "weight": 1}}},
            "satisfaction": 1,
        }
    )
    assert (
        np.array_equal(a.imp, b.imp)
        and np.array_equal(a.tgt, b.tgt)
        and np.array_equal(a.itype, b.itype)
    )


@pytest.mark.skipif(not (EXP / "candidates.parquet").exists(), reason="데이터 없음")
def test_table_columns_and_history_order():
    import pyarrow.parquet as pq

    cols = pq.read_schema(EXP / "candidates.parquet").names
    assert set(cols) == {"session_id", "seat_id", "y_chosen", *FEATURE_COLS}
    meta = pd.read_parquet(EXP / "sessions.parquet")
    users = meta.user_id.drop_duplicates().sample(40, random_state=0)
    sub = meta[meta.user_id.isin(users)]
    m0 = C.load_config()["data"]["inf_shrinkage"]
    need = (
        ["session_id", "y_chosen", "hist_n"]
        + [f"seatc_{k}" for k in SEAT_KEYS]
        + [f"inf_r_{k}" for k in SEAT_KEYS]
    )
    cand = pd.read_parquet(
        EXP / "candidates.parquet",
        columns=need,
        filters=[("session_id", "in", list(sub.session_id))],
    )
    first = cand.groupby("session_id", sort=False).first()
    chosen = cand[cand.y_chosen == 1].set_index("session_id")
    for u, g in sub.sort_values(["occurred_at", "session_id"]).groupby("user_id"):
        h = first.loc[g.session_id, "hist_n"].values
        assert (np.diff(h) >= 1).all(), (
            "이력 횟수는 시간 순서로 증가해야 한다(미래 참조 금지)"
        )
        acc = np.zeros(len(SEAT_KEYS))
        for sid, n in zip(g.session_id, h):
            r = first.loc[sid, [f"inf_r_{k}" for k in SEAT_KEYS]].values.astype(float)
            assert np.allclose(r, acc / (n + m0), atol=1e-4), (
                "취향 추정은 그 요청 이전 선택만으로 계산"
            )
            acc += chosen.loc[sid, [f"seatc_{k}" for k in SEAT_KEYS]].values.astype(
                float
            )


def test_online_score_before_label():
    rng = np.random.default_rng(0)
    g, Z = rng.normal(size=8), rng.normal(size=(8, 5))
    a, b = BayesUser(5, 1.0), BayesUser(5, 1.0)
    sa, sb = a.scores(g, Z), b.scores(g, Z)
    a.update(g, Z, 0)
    b.update(g, Z, 7)
    assert np.allclose(sa, sb)  # 점수는 선택(라벨) 이전 상태로만 계산
    assert not np.allclose(a.mu, b.mu)  # 선택 후 바로 갱신된다
    assert a.scores(g, Z)[0] - sa[0] > 0  # 고른 좌석 점수가 올라간다
