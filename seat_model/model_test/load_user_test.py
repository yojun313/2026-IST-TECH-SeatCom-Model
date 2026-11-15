"""유저 테스트 데이터셋을 MongoDB `user_test` DB에 적재한다. 한 번에 테스트셋 하나만 올린다.

    seat_model/.venv/bin/python -m seat_model.model_test.load_user_test --set base
    seat_model/.venv/bin/python -m seat_model.model_test.load_user_test --set real_0.50

- DB 이름이 `user_test`가 아니면 실행하지 않는다(운영 DB `library`는 건드리지 않는다).
- 컬렉션 이름은 학습과 같다(synth_scenarios, synth_seat_env, synth_users, synth_sessions).
  그래서 seat_model.train의 FeatureReader("user_test")·TruthReader("user_test")를 수정 없이 쓴다.
- 세트를 바꾸면 synth_sessions를 base 요청 맥락 + 그 세트의 선택으로 다시 넣는다.
  base가 아닌 세트의 satisfaction·chosen_preference_fit은 다시 계산하지 않으므로 null이다(특징·평가에 쓰지 않는 필드).
- synth_events는 적재하지 않는다(특징 단계 금지 컬렉션이며 평가에도 쓰지 않는다).
- 적재된 세트는 meta 컬렉션 {_id: "testset"}에 기록한다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from seat_model.train.common import _mongo_db

from .pinned import load_config

DB_NAME = "user_test"
INDEXES = {
    "synth_scenarios": [{"library_id": 1, "room_id": 1}, {"scenario_id": 1}],
    "synth_seat_env": [{"scenario_id": 1}, {"seat_id": 1}, {"library_id": 1}],
    "synth_users": [{"user_id": 1}, {"home_library_id": 1}],
    "synth_sessions": [
        {"session_id": 1},
        {"user_id": 1, "occurred_at": 1},
        {"scenario_id": 1},
        {"library_id": 1},
    ],
}


def read_jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def put(db, coll, docs, batch=5000):
    db[coll].drop()
    for i in range(0, len(docs), batch):
        db[coll].insert_many(docs[i : i + batch], ordered=False)
    for keys in INDEXES.get(coll, []):
        db[coll].create_index(list(keys.items()))
    return db[coll].estimated_document_count()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--set", default="base", help="적재할 테스트셋 이름(model_test.yaml sets)"
    )
    a = ap.parse_args()
    print(json.dumps(load_set(a.set), ensure_ascii=False))


def load_set(name):
    """세트 하나를 user_test에 적재한다. 반환: 적재 요약."""
    cfg = load_config()
    if cfg["db"] != DB_NAME:
        raise SystemExit(f"model_test.yaml의 db가 {DB_NAME}가 아니다: {cfg['db']}")
    if name not in cfg["sets"]:
        raise SystemExit(f"알 수 없는 세트: {name}")

    out = Path(cfg["paths"]["exports"])
    man = json.loads((out / "manifest.json").read_text())
    db = _mongo_db(DB_NAME)
    if db.name != DB_NAME:
        raise SystemExit("접속 DB 이름 확인 실패")

    counts = {}
    meta = db.meta.find_one({"_id": "synthetic_manifest"}) or {}
    if (
        meta.get("generated_at") != man["generated_at"]
    ):  # 환경·사용자는 데이터셋이 바뀔 때만 다시 넣는다
        counts["synth_scenarios"] = put(
            db, "synth_scenarios", read_jsonl(out / "env" / "synth_scenarios.jsonl")
        )
        counts["synth_seat_env"] = put(
            db, "synth_seat_env", read_jsonl(out / "env" / "synth_seat_env.jsonl")
        )
        counts["synth_users"] = put(
            db, "synth_users", read_jsonl(out / "users" / "synth_users.jsonl")
        )
        db.meta.replace_one(
            {"_id": "synthetic_manifest"},
            {"_id": "synthetic_manifest", **man},
            upsert=True,
        )

    sessions = read_jsonl(out / "base" / "synth_sessions.jsonl")
    if name != "base":
        ch = pd.read_parquet(
            out / "sets" / name / "choices.parquet",
            columns=["session_id", "chosen_seat_id", "choice_origin", "chosen_u_true"],
        ).set_index("session_id")
        for s in sessions:
            r = ch.loc[s["session_id"]]
            chosen = r.chosen_seat_id if isinstance(r.chosen_seat_id, str) else None
            s["chosen_seat_id"], s["choice_origin"] = (
                chosen,
                (r.choice_origin if chosen else None),
            )
            s["chosen_true_utility"] = (
                round(float(r.chosen_u_true), 3) if chosen else None
            )
            s["satisfaction"] = s["chosen_preference_fit"] = None
            s["testset"] = name
    else:
        for s in sessions:
            s["testset"] = "base"
    counts["synth_sessions"] = put(db, "synth_sessions", sessions)
    spec = cfg["sets"][name]
    db.meta.replace_one(
        {"_id": "testset"},
        {
            "_id": "testset",
            "name": name,
            "lambda": spec["lambda"],
            "modules": spec["modules"],
            "dataset_generated_at": man["generated_at"],
            "loaded_at": datetime.now(timezone.utc).isoformat(),
        },
        upsert=True,
    )
    return {"db": DB_NAME, "set": name, "counts": counts}


if __name__ == "__main__":
    main()
