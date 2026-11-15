"""서비스 트랙: SeatRecommender(clogit 서비스 모델 + 사용자별 온라인 갱신)를 inference.py로 직접 호출한다.

    seat_model/.venv/bin/python -m seat_model.model_test.service_suite [--workers N]

- 대상: base 세트(가정이 맞는 세계). 요청 맥락은 exports/test_v1 파일에서 만든다(user_test 적재 상태와 무관).
- 사용자 입력은 FeatureReader 허용 목록과 같은 필드만 넘긴다(latent 등은 넘기지 않는다).
- 순차 모드: 사용자별 시간순으로 추천 → observe. 콜드 모드: 같은 요청을 상태 없이 추천.
- 상태는 메모리에서만 쓴다. save_state는 막고, 실행 전후 모델 폴더 해시를 비교한다.
출력: exports/test_v1/eval/service/(per_session_*.parquet, service_summary.json)
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import multiprocessing as mp
import os
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from seat_model.train.common import FEATURE_ALLOWLIST
from seat_model.train.common import load_config as load_train_config
from seat_model.train.inference import SeatRecommender
from seat_model.train.run import MODEL_VERSION

from .pinned import load_config

GRADE_CUTS = (0.05, 0.20, 0.50)


def md5_dir(d):
    return {
        p.name: hashlib.md5(p.read_bytes()).hexdigest()
        for p in sorted(Path(d).iterdir())
        if p.is_file()
    }


def allow_user(doc):
    """FeatureReader 허용 필드만 남긴 사용자 입력."""
    out = {
        "user_id": doc["user_id"],
        "home_library_id": doc.get("home_library_id"),
        "requirements": doc.get("requirements") or [],
    }
    out["preference_profile"] = {
        "preferences": (doc.get("preference_profile") or {}).get("preferences") or []
    }
    kp = ((doc.get("kakaotalk_profile") or {}).get("preferences")) or {}
    allowed = {
        f.split(".")[2]
        for f in FEATURE_ALLOWLIST["synth_users"]
        if f.startswith("kakaotalk_profile.preferences.")
    }
    out["kakaotalk_profile"] = {
        "preferences": {
            k: {x: v.get(x) for x in ("preference", "confidence", "inference_type")}
            for k, v in kp.items()
            if k in allowed
        }
    }
    return out


def seat_doc(s):
    return {
        "seat_id": s["seat_id"],
        "features": s["features"],
        "brightness_by_slot": s["brightness_by_slot"],
        "noise_index": s["raw"]["noise_index"],
        "desk_type": s["raw"]["desk_type"],
        "desk_group": s["raw"]["desk_group"],
    }


def snapshot(sess, scen_seats, scen_cond):
    return {
        "room_id": sess["room_id"],
        "time_slot": sess["time_slot"],
        "requested_at": sess["occurred_at"],
        "conditions": {
            k: scen_cond[sess["scenario_id"]][k]
            for k in ("season", "weather", "base_temperature_c")
        },
        "seats": scen_seats[sess["scenario_id"]],
        "occupied_seat_ids": sess["occupied_seat_ids"],
    }


def metrics_one(order, u_map, chosen, ndcg_k=5):
    """order: 추천 순서(조건 만족 좌석) + 나머지 좌석. u_map: 빈자리별 u_true."""
    ids = list(u_map)
    u = np.array([u_map[x] for x in ids])
    ut = np.where(np.isfinite(u), u, -1e9)
    t_order = np.argsort(-ut, kind="stable")
    trank = {ids[j]: r + 1 for r, j in enumerate(t_order)}
    n = len(ids)
    cuts = [max(1, math.ceil(c * n)) for c in GRADE_CUTS]
    grade = lambda r: (
        3 if r <= cuts[0] else 2 if r <= cuts[1] else 1 if r <= cuts[2] else 0
    )
    rank = {x: i + 1 for i, x in enumerate(order)}
    dcg = sum(
        (2 ** grade(trank[x]) - 1) / math.log2(rank[x] + 1) for x in order[:ndcg_k]
    )
    idcg = sum(
        (2 ** grade(r) - 1) / math.log2(r + 1) for r in range(1, min(ndcg_k, n) + 1)
    )
    rc = rank.get(chosen, n + 1)
    nvalid = int(np.isfinite(u).sum())
    top = order[0]
    pct = (
        (1 - (trank[top] - 1) / max(nvalid - 1, 1)) if np.isfinite(u_map[top]) else 0.0
    )
    return {
        "hit@1": float(rc <= 1),
        "hit@3": float(rc <= 3),
        "hit@5": float(rc <= 5),
        "mrr": 1 / rc,
        "ndcg@5": dcg / idcg if idcg > 0 else 0.0,
        "oracle_hit@1": float(trank[top] == 1),
        "exp_utility_pct": pct,
    }


# ---------------------------------------------------------------- 워커(사용자 묶음 단위, 사용자끼리 상태가 독립)
_G = {}


def _worker(user_ids):
    G = _G
    rec_seq = SeatRecommender.load(G["mdir"], state_file="__none__.json")
    rec_cold = SeatRecommender.load(G["mdir"], state_file="__none__.json")
    for r in (rec_seq, rec_cold):
        r.save_state = _blocked
    rows, errors, m1 = [], [], {"checked": 0, "same": 0}
    viol = {
        "laptop_req_requests": 0,
        "top5_forbidden": 0,
        "any_forbidden": 0,
        "no_eligible": 0,
        "chosen_ineligible": 0,
    }
    for uid in user_ids:
        user = G["users"][uid]
        lap = any(r["featureId"] == "laptopAllowed" for r in user["requirements"])
        for s in G["by_user"][uid]:
            snap = snapshot(s, G["scen_seats"], G["scen_cond"])
            chosen = s["chosen_seat_id"]
            free = [
                x["seat_id"]
                for x in snap["seats"]
                if x["seat_id"] not in set(snap["occupied_seat_ids"])
            ]
            evaluate = chosen is not None and len(free) >= 2
            try:
                if evaluate:
                    row = {
                        "session_id": s["session_id"],
                        "user_id": uid,
                        "n_avail": len(free),
                    }
                    for mode, rec in (("seq", rec_seq), ("cold", rec_cold)):
                        out = rec.recommend(user, snap, k=10_000)
                        order = [x["seat_id"] for x in out["recommendations"]]
                        rest = sorted(set(free) - set(order))
                        m = metrics_one(
                            order + rest, G["truth"][s["session_id"]], chosen
                        )
                        row.update({f"{mode}|{k}": v for k, v in m.items()})
                        if mode == "seq":
                            row["hist_n"] = out["history_sessions"]
                            if lap:
                                feat = {
                                    x["seat_id"]: x["features"]["laptopAllowed"]
                                    for x in snap["seats"]
                                }
                                viol["laptop_req_requests"] += 1
                                viol["top5_forbidden"] += sum(
                                    feat[x] == 0 for x in order[:5]
                                )
                                viol["any_forbidden"] += sum(
                                    feat[x] == 0 for x in order
                                )
                                viol["no_eligible"] += (
                                    out.get("reason") == "no_eligible_seat"
                                )
                                viol["chosen_ineligible"] += feat[chosen] == 0
                            if len(rows) % 40 == 0:  # M1: 같은 상태로 2회 → 같은 출력
                                again = rec.recommend(user, snap, k=10_000)
                                m1["checked"] += 1
                                m1["same"] += [
                                    (x["seat_id"], x["score"])
                                    for x in again["recommendations"]
                                ] == [
                                    (x["seat_id"], x["score"])
                                    for x in out["recommendations"]
                                ]
                    rows.append(row)
                if chosen is not None:
                    er = {e["seat_id"]: e["rank"] for e in s["exposed"] or []}
                    rec_seq.observe(uid, snap, chosen, user=user, exposed_rank=er)
            except Exception as e:  # 정상 데이터 예외는 세고 계속한다
                errors.append(
                    {
                        "session_id": s["session_id"],
                        "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc(limit=3),
                    }
                )
    return rows, errors, m1, viol


def _blocked(*a, **k):
    raise RuntimeError("테스트에서는 save_state를 쓰지 않는다")


# ---------------------------------------------------------------- 지연 시간
def latency(mdir, sessions, users, scen_seats, scen_cond, n=3000, seed=0):
    rec = SeatRecommender.load(mdir, state_file="__none__.json")
    rec.save_state = _blocked
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(sessions), min(n, len(sessions)), replace=False)
    rows = []
    for w in range(50):  # 워밍업
        s = sessions[pick[w]]
        rec.recommend(users[s["user_id"]], snapshot(s, scen_seats, scen_cond), k=5)
    for i in pick:
        s = sessions[i]
        snap = snapshot(s, scen_seats, scen_cond)
        t = time.perf_counter()
        rec.recommend(users[s["user_id"]], snap, k=5)
        rows.append(
            {
                "n_avail": len(snap["seats"]) - len(snap["occupied_seat_ids"]),
                "ms": (time.perf_counter() - t) * 1000,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 견고성 R1~R8
def robustness(mdir, sessions, users, scen_seats, scen_cond):
    rec = SeatRecommender.load(mdir, state_file="__none__.json")
    rec.save_state = _blocked
    s = next(
        x
        for x in sessions
        if 20 <= len(scen_seats[x["scenario_id"]]) - len(x["occupied_seat_ids"]) <= 60
    )
    user = users[s["user_id"]]
    base = snapshot(s, scen_seats, scen_cond)
    free_ids = [
        x["seat_id"]
        for x in base["seats"]
        if x["seat_id"] not in set(base["occupied_seat_ids"])
    ]

    def run(code, desc, expected, snap, usr=user, k=5, check=None):
        t = time.perf_counter()
        try:
            out = rec.recommend(usr, snap, k=k)
            ms = (time.perf_counter() - t) * 1000
            recs = out["recommendations"]
            nan = sum(not np.isfinite(r["score"]) for r in recs)
            behavior = (
                f"정상 반환: 추천 {len(recs)}개"
                + (f", 점수 NaN/inf {nan}개" if nan else "")
                + (f", reason={out['reason']}" if out.get("reason") else "")
            )
            ok = False if check == "raise" else (check(out) if check else nan == 0)
        except Exception as e:
            ms = (time.perf_counter() - t) * 1000
            behavior = f"예외 {type(e).__name__}: {str(e)[:120]}"
            ok = check == "raise"
        return {
            "code": code,
            "input": desc,
            "expected": expected,
            "behavior": behavior,
            "matches_expected": bool(ok),
            "ms": round(ms, 2),
        }

    out = []
    snap = copy.deepcopy(base)
    snap["occupied_seat_ids"] = [x["seat_id"] for x in snap["seats"]]
    out.append(
        run(
            "R1",
            "후보 0개(전 좌석 점유)",
            "빈 결과 + 사유",
            snap,
            check=lambda o: o["recommendations"] == [] and bool(o.get("reason")),
        )
    )
    snap = copy.deepcopy(base)
    snap["occupied_seat_ids"] = [
        x for x in [y["seat_id"] for y in snap["seats"]] if x != free_ids[0]
    ]
    out.append(
        run(
            "R2",
            "후보 1개",
            "그 좌석 반환",
            snap,
            usr={"user_id": "r2-free"},
            check=lambda o: (
                [r["seat_id"] for r in o["recommendations"]] == [free_ids[0]]
            ),
        )
    )
    big = copy.deepcopy(base)
    src = [x for x in big["seats"]]
    big["seats"] = [
        dict(
            copy.deepcopy(src[i % len(src)]),
            seat_id=f"r3:seat:{i:04d}",
            desk_group=10_000 + i,
        )
        for i in range(500)
    ]
    big["occupied_seat_ids"] = []
    out.append(
        run(
            "R3",
            "후보 500개(학습 최대 176개, 분포 밖)",
            "정상 동작, 지연 시간 기록",
            big,
            usr={"user_id": "r3-free"},
        )
    )
    # 밝기는 features.brightness가 아니라 시간대별 brightness_by_slot을 쓴다
    for key in ("brightness_by_slot", "airflow", "outletAvailable", "noise_index"):
        snap = copy.deepcopy(base)
        tgt = next(x for x in snap["seats"] if x["seat_id"] == free_ids[0])
        if key == "noise_index":
            tgt["noise_index"] = float("nan")
        elif key == "brightness_by_slot":
            tgt["brightness_by_slot"][snap["time_slot"]] = float("nan")
        else:
            tgt["features"][key] = float("nan")
        out.append(
            run(
                "R4",
                f"특징 결측(NaN): {key} 1석",
                "오류 없이 처리, 결측 처리 방식 기록",
                snap,
            )
        )
    snap = copy.deepcopy(base)
    tgt = next(x for x in snap["seats"] if x["seat_id"] == free_ids[0])
    tgt["features"]["airflow"] = None
    out.append(
        run(
            "R4",
            "특징 결측(null): airflow 1석",
            "오류 없이 처리, 결측 처리 방식 기록",
            snap,
        )
    )
    for v in (-1.0, 2.0, float("inf")):
        snap = copy.deepcopy(base)
        tgt = next(x for x in snap["seats"] if x["seat_id"] == free_ids[0])
        tgt["features"]["deskWidth"] = v
        out.append(run("R5", f"범위 밖 값: deskWidth = {v}", "거부 또는 클리핑", snap))
    snap = copy.deepcopy(base)
    snap["seats"] = snap["seats"] + [
        copy.deepcopy(next(x for x in snap["seats"] if x["seat_id"] == free_ids[0]))
    ]
    out.append(run("R6", "중복 좌석 ID", "명시적 오류", snap, check="raise"))
    snap = copy.deepcopy(base)
    next(x for x in snap["seats"] if x["seat_id"] == free_ids[0])["desk_type"] = (
        "bean_bag"
    )
    out.append(run("R7", "처음 보는 desk_type(bean_bag)", "오류 없이 처리", snap))
    snap = copy.deepcopy(base)
    snap["conditions"]["weather"] = "snow"
    out.append(run("R7", "처음 보는 날씨(snow)", "오류 없이 처리", snap))
    for desc, usr in (
        (
            "preference_profile 없음",
            {"user_id": user["user_id"], "requirements": user["requirements"]},
        ),
        (
            "preferences 항목에 importance 없음",
            {
                **user,
                "preference_profile": {
                    "preferences": [
                        {k: v for k, v in p.items() if k != "importance"}
                        for p in user["preference_profile"]["preferences"]
                    ]
                    or [{"featureId": "brightness", "target": 0.8}]
                },
            },
        ),
        (
            "kakaotalk_profile·requirements 없음",
            {
                "user_id": user["user_id"],
                "preference_profile": user["preference_profile"],
            },
        ),
    ):
        out.append(
            run(
                "R8",
                f"관측 프로필 필드 누락: {desc}",
                "기본값 경로 동작",
                copy.deepcopy(base),
                usr=usr,
            )
        )
    return out


# ---------------------------------------------------------------- 메인
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=min(30, os.cpu_count() or 1))
    a = ap.parse_args()
    cfg, tcfg = load_config(), load_train_config()
    E = Path(cfg["paths"]["exports"])
    out = E / "eval" / "service"
    out.mkdir(parents=True, exist_ok=True)
    mdir = Path(tcfg["paths"]["models"]) / MODEL_VERSION
    h_before = md5_dir(mdir)

    seats = {}
    with open(E / "env" / "synth_seat_env.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            seats.setdefault(d["scenario_id"], []).append(seat_doc(d))
    with open(E / "env" / "synth_scenarios.jsonl", encoding="utf-8") as f:
        cond = {d["scenario_id"]: d["conditions"] for d in map(json.loads, f)}
    with open(E / "users" / "synth_users.jsonl", encoding="utf-8") as f:
        users = {d["user_id"]: allow_user(d) for d in map(json.loads, f)}
    with open(E / "base" / "synth_sessions.jsonl", encoding="utf-8") as f:
        sessions = sorted(
            map(json.loads, f), key=lambda s: (s["occurred_at"], s["session_id"])
        )
    by_user = {}
    for s in sessions:
        by_user.setdefault(s["user_id"], []).append(s)
    t = pd.read_parquet(E / "sets" / "base" / "eval_only" / "truth.parquet")
    t["session_id"] = t.session_id.astype(str)
    truth = {
        sid: dict(zip(g.seat_id.values, g.u_true.values.astype(float)))
        for sid, g in t.groupby("session_id", sort=False)
    }
    del t

    _G.update(
        mdir=mdir,
        users=users,
        by_user=by_user,
        scen_seats=seats,
        scen_cond=cond,
        truth=truth,
    )
    uids = sorted(by_user)
    chunks = [uids[i :: a.workers] for i in range(a.workers)]
    t0 = time.time()
    with mp.get_context("fork").Pool(a.workers) as pool:
        res = pool.map(_worker, chunks)
    rows = [r for x in res for r in x[0]]
    errors = [e for x in res for e in x[1]]
    m1 = {k: sum(x[2][k] for x in res) for k in ("checked", "same")}
    viol = {k: sum(x[3][k] for x in res) for k in res[0][3]}
    per = pd.DataFrame(rows)
    per.to_parquet(out / "per_session_base.parquet", index=False)
    seq_secs = time.time() - t0

    lat = latency(mdir, sessions, users, seats, cond)
    lat.to_parquet(out / "latency.parquet", index=False)
    rob = robustness(mdir, sessions, users, seats, cond)
    h_after = md5_dir(mdir)

    keys = ["hit@1", "hit@3", "mrr", "ndcg@5", "oracle_hit@1", "exp_utility_pct"]
    summ = {
        "requests_evaluated": int(len(per)),
        "users": int(per.user_id.nunique()),
        "seconds": round(seq_secs, 1),
        "seq": {k: float(per[f"seq|{k}"].mean()) for k in keys},
        "cold": {k: float(per[f"cold|{k}"].mean()) for k in keys},
        "seq_by_history": {
            b: {k: float(g[f"seq|{k}"].mean()) for k in ("hit@3", "ndcg@5")}
            | {"n": int(len(g))}
            for b, g in per.groupby(
                pd.cut(
                    per.hist_n,
                    [0, 1, 5, 20, 10_000],
                    right=False,
                    labels=["0", "1–4", "5–19", "20+"],
                ),
                observed=True,
            )
        },
        "exceptions_on_normal_data": len(errors),
        "exception_samples": errors[:5],
        "M1_determinism": {**m1, "rate": m1["same"] / max(m1["checked"], 1)},
        "M9_hard_constraint": viol,
        "latency_ms": {
            "p50": float(lat.ms.quantile(0.5)),
            "p95": float(lat.ms.quantile(0.95)),
            "p99": float(lat.ms.quantile(0.99)),
            "p95_by_avail": {
                str(b): float(g.ms.quantile(0.95))
                for b, g in lat.groupby(
                    pd.cut(lat.n_avail, [0, 25, 50, 100, 200]), observed=True
                )
            },
            "n": int(len(lat)),
        },
        "robustness": rob,
        "model_dir_unchanged": h_before == h_after,
    }
    (out / "service_summary.json").write_text(
        json.dumps(summ, ensure_ascii=False, indent=1, default=float)
    )
    print(
        json.dumps(
            {
                k: summ[k]
                for k in (
                    "requests_evaluated",
                    "seq",
                    "cold",
                    "exceptions_on_normal_data",
                    "M1_determinism",
                    "M9_hard_constraint",
                    "model_dir_unchanged",
                )
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print(json.dumps(summ["latency_ms"], ensure_ascii=False))
    for r in rob:
        print(
            r["code"],
            r["input"],
            "→",
            r["behavior"],
            "| 기대 일치" if r["matches_expected"] else "| 기대와 다름",
        )


if __name__ == "__main__":
    main()
