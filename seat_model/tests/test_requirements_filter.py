"""회귀 테스트: 필수 조건(노트북 규정)은 하드 필터다. DB 없이 작은 스냅숏으로 확인한다.

예전 동작(final = s + 1000·req_ok)은 조건 만족 좌석이 k개보다 적으면 금지 좌석을 섞어 반환했다.
"""

from pathlib import Path

import pytest

from seat_model.train.common import load_config
from seat_model.train.run import MODEL_VERSION

MDIR = Path(load_config()["paths"]["models"]) / MODEL_VERSION
pytestmark = pytest.mark.skipif(
    not (MDIR / "manifest.json").exists(), reason="모델 없음"
)

KEYS = [
    "laptopAllowed",
    "keyboardAllowed",
    "partition",
    "outletAvailable",
    "windowProximity",
    "toiletProximity",
    "entranceProximity",
    "brightness",
    "airflow",
    "deskHeight",
    "deskWidth",
    "viewQuality",
]


def _seat(i, laptop):
    f = {k: 0.5 for k in KEYS}
    f.update(
        laptopAllowed=laptop,
        keyboardAllowed=laptop,
        partition=i % 2,
        outletAvailable=(i + 1) % 2,
        windowProximity=i / 10,
        brightness=0.6,
    )
    return {
        "seat_id": f"t:seat:{i:02d}",
        "features": f,
        "brightness_by_slot": {"morning": 0.6, "noon": 0.7, "evening": 0.5},
        "noise_index": 0.3,
        "desk_type": "individual_carrel",
        "desk_group": i,
    }


def _snap(laptop_flags):
    return {
        "room_id": "t:room",
        "time_slot": "noon",
        "requested_at": "2026-09-10T13:00:00+09:00",
        "conditions": {
            "season": "spring_fall",
            "weather": "clear",
            "base_temperature_c": 23.0,
        },
        "seats": [_seat(i, x) for i, x in enumerate(laptop_flags)],
        "occupied_seat_ids": [],
    }


USER = {
    "user_id": "t:user",
    "requirements": [{"featureId": "laptopAllowed", "operator": "eq", "value": 1}],
    "preference_profile": {
        "preferences": [
            {"featureId": "outletAvailable", "target": 1, "importance": 0.9}
        ]
    },
}


@pytest.fixture(scope="module")
def rec():
    from seat_model.train.inference import SeatRecommender

    return SeatRecommender.load(MDIR, state_file="__none__.json")


def test_fewer_eligible_than_k_returns_only_eligible(rec):
    out = rec.recommend(USER, _snap([1, 0, 0, 1, 0, 0]), k=5)
    ids = [r["seat_id"] for r in out["recommendations"]]
    assert ids and set(ids) <= {"t:seat:00", "t:seat:03"} and len(ids) == 2
    assert all(r["meets_requirements"] for r in out["recommendations"])
    assert out["excluded_by_requirements"] == 4


def test_no_eligible_seat_returns_empty_with_reason(rec):
    out = rec.recommend(USER, _snap([0, 0, 0]), k=5)
    assert out["recommendations"] == [] and out["reason"] == "no_eligible_seat"


def test_user_without_requirements_unchanged(rec):
    out = rec.recommend({"user_id": "t:free"}, _snap([1, 0, 0, 1]), k=4)
    assert len(out["recommendations"]) == 4 and out["excluded_by_requirements"] == 0
