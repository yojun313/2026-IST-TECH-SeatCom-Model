"""0단계 재현성 검사: 고정 생성기를 학습 설정(시드·인자) 그대로 다시 돌려 학습 산출물과 비교한다.

    seat_model/.venv/bin/python -m seat_model.model_test.reproduce --work <임시 폴더>

- 비교 기준은 seat_model/exports/train_v1/ (sessions.parquet, candidates.parquet, data_report.json).
  data/libraries/synthetic 과 운영 DB는 이미 synth-1.1 데이터라 기준으로 쓸 수 없다.
- 통과하면 학습 환경(synth_scenarios, synth_seat_env)과 학습 사용자 파일을 exports/test_v1/env/ 에 복사한다.
  테스트 데이터는 이 환경 위에서 만든다(학습과 같은 열람실 환경).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

from .pinned import check_pinned, load_config, load_generator


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="재생성 출력 폴더(임시)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument(
        "--skip-gen", action="store_true", help="이미 생성된 --work 폴더를 비교만 한다"
    )
    a = ap.parse_args()
    cfg = load_config()
    gc = cfg["generator"]
    blob = check_pinned(gc["pinned_commit"])
    work = Path(a.work)
    if not a.skip_gen:
        g = load_generator(work)
        sys.argv = [
            "gen_v1_pinned",
            "--scenarios",
            str(gc["scenarios"]),
            "--users",
            str(gc["train_users"]),
            "--sessions",
            str(gc["train_sessions"]),
            "--seed",
            str(gc["train_seed"]),
            "--workers",
            str(a.workers),
        ]
        g.main()

    tr = Path(cfg["paths"]["train_exports"])
    rep_train = json.loads((tr / "data_report.json").read_text())
    meta = pd.read_parquet(
        tr / "sessions.parquet",
        columns=["session_id", "user_id", "scenario_id", "occurred_at", "n_avail"],
    )
    cand = pd.read_parquet(
        tr / "candidates.parquet", columns=["session_id", "seat_id", "y_chosen"]
    )
    chosen_train = cand[cand.y_chosen == 1].set_index("session_id").seat_id

    gen = {}
    with open(work / "synth_sessions.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            gen[d["session_id"]] = (
                d["user_id"],
                d["scenario_id"],
                d["occurred_at"],
                d["available_seat_ids_count"],
                d["chosen_seat_id"],
            )
    mism = {
        "missing": 0,
        "user": 0,
        "scenario": 0,
        "time": 0,
        "n_avail": 0,
        "chosen": 0,
    }
    for r in meta.itertuples(index=False):
        x = gen.get(r.session_id)
        if x is None:
            mism["missing"] += 1
            continue
        mism["user"] += x[0] != r.user_id
        mism["scenario"] += x[1] != r.scenario_id
        mism["time"] += x[2] != r.occurred_at
        mism["n_avail"] += x[3] != r.n_avail
        mism["chosen"] += x[4] != chosen_train.get(r.session_id)
    man = json.loads((work / "manifest.json").read_text())
    ok = man["counts"]["synth_sessions"] == rep_train["sessions_total"] and not any(
        mism.values()
    )
    report = {
        "pinned_blob": blob,
        "generator_version": man["generator_version"],
        "params": man["params"],
        "sessions_regenerated": man["counts"]["synth_sessions"],
        "sessions_train": rep_train["sessions_total"],
        "compared_sessions": len(meta),
        "mismatch": mism,
        "passed": bool(ok),
        "sha256": {
            n: sha256(work / f"{n}.jsonl")
            for n in ("synth_scenarios", "synth_seat_env", "synth_users")
        },
    }
    out = Path(cfg["paths"]["exports"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "reproducibility.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        sys.exit("재현성 검사 실패: 테스트 데이터를 만들지 않는다")
    env = out / "env"
    env.mkdir(exist_ok=True)
    for n in ("synth_scenarios", "synth_seat_env", "synth_users"):
        shutil.copyfile(
            work / f"{n}.jsonl",
            env / (f"{n}.jsonl" if n != "synth_users" else "train_users.jsonl"),
        )
    shutil.copyfile(work / "manifest.json", env / "train_manifest.json")


if __name__ == "__main__":
    main()
