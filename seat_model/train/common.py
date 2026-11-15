"""공통: 설정, DB 접속(.env, 접속 정보는 출력하지 않음), 허용 목록 로더, 누설 가드.

특징 생성 코드는 `FeatureReader`만 쓴다. 허용 목록에 없는 컬렉션·필드는 읽을 수 없다.
평가 코드는 `TruthReader`로 참값(latent)을 따로 읽는다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SEAT_MODEL_DIR = REPO / "seat_model"


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else SEAT_MODEL_DIR / "config" / "train.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    for k, v in cfg["paths"].items():
        cfg["paths"][k] = str(REPO / v)
    return cfg


def _env() -> dict:
    d = {}
    f = REPO / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip("\"'")
    d.update({k: v for k, v in os.environ.items() if k.startswith("MONGO_")})
    return d


def _mongo_db(name: str):
    from pymongo import MongoClient

    e = _env()
    client = MongoClient(
        host=e["MONGO_HOST"],
        port=int(e["MONGO_PORT"]),
        username=e.get("MONGO_USER"),
        password=e.get("MONGO_PASSWORD"),
        authSource=e.get("MONGO_AUTH_DB", "admin"),
        serverSelectionTimeoutMS=10000,
    )
    return client[name]


# ---------------------------------------------------------------- 누설 가드
# 정답·참값·사후 정보. 특징 단계에서 이 이름이 필드 경로에 나오면 예외.
FORBIDDEN_TOKENS = (
    "latent",
    "is_correct_vs_latent",
    "chosen_true_utility",
    "chosen_preference_fit",
    "satisfaction",
    "choice_origin",
    "archetype",
    "reason",
    "rationality",
    "rec_trust",
    "noise_aversion",
    "utility_weights",
    "straightliner",
    "feedback",
    "likert",
    "u_true",
)
FORBIDDEN_COLLECTIONS = (
    "synth_events",
)  # ratingSubmitted(만족도), useConfirmed(사후) 포함

FEATURE_ALLOWLIST = {
    "synth_users": [
        "user_id",
        "home_library_id",
        "preference_profile.preferences",
        "requirements",
    ]
    + [
        f"kakaotalk_profile.preferences.{f}.{k}"
        for f in (
            "restroom",
            "lighting",
            "air_conditioning",
            "desk",
            "laptop_usage",
            "outlet",
            "view",
        )
        for k in ("preference", "confidence", "inference_type")
    ],
    "synth_seat_env": [
        "scenario_id",
        "seat_id",
        "room_id",
        "library_id",
        "features",
        "brightness_by_slot",
        "raw.noise_index",
        "raw.desk_group",
        "raw.desk_type",
    ],
    "synth_scenarios": [
        "scenario_id",
        "room_id",
        "library_id",
        "conditions.season",
        "conditions.weather",
        "conditions.base_temperature_c",
        "conditions.laptop_allowed",
    ],
    "synth_sessions": [
        "session_id",
        "user_id",
        "library_id",
        "scenario_id",
        "room_id",
        "time_slot",
        "occurred_at",
        "occupied_seat_ids",
        "exposed",
        "chosen_seat_id",
    ],
}
# 분할 층화에만 쓰는 필드(특징 아님)
SPLIT_ALLOWLIST = {"synth_users": ["user_id", "archetype"]}
# 평가 전용 참값
TRUTH_ALLOWLIST = {"synth_users": ["user_id", "archetype", "latent"]}


class LeakageError(RuntimeError):
    pass


def check_fields(fields, allow_forbidden=False):
    for f in fields:
        low = f.lower()
        if not allow_forbidden and any(
            t in low.split(".") or t == low for t in FORBIDDEN_TOKENS
        ):
            raise LeakageError(f"특징 단계에서 금지 필드 접근: {f}")


class _Reader:
    allowlist: dict = {}
    allow_forbidden = False

    def __init__(self, db_name="library"):
        self._db = _mongo_db(db_name)

    def find(self, coll, filt=None, fields=None):
        if coll in FORBIDDEN_COLLECTIONS or coll not in self.allowlist:
            raise LeakageError(f"{type(self).__name__}: 허용되지 않은 컬렉션 {coll}")
        allowed = self.allowlist[coll]
        fields = fields or allowed
        bad = [f for f in fields if f not in allowed]
        if bad:
            raise LeakageError(f"{type(self).__name__}: 허용 목록 밖 필드 {bad}")
        check_fields(fields, self.allow_forbidden)
        proj = {f: 1 for f in fields}
        proj["_id"] = 0
        return self._db[coll].find(filt or {}, proj)


class FeatureReader(_Reader):
    allowlist = FEATURE_ALLOWLIST


class SplitReader(_Reader):
    allowlist = SPLIT_ALLOWLIST
    allow_forbidden = True  # archetype은 층화에만 쓰고 특징 테이블에 넣지 않는다


class TruthReader(_Reader):
    """평가 전용. 특징 생성 모듈(data.py)에서 import하면 테스트가 실패한다."""

    allowlist = TRUTH_ALLOWLIST
    allow_forbidden = True


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=float), encoding="utf-8"
    )
