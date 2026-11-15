"""1단계: 유저 테스트 데이터셋 생성 (v1, synth-1.0 고정 생성기).

    seat_model/.venv/bin/python -m seat_model.model_test.generate [--workers N]

선행 조건: reproduce.py 통과(exports/test_v1/reproducibility.json의 passed = true, env/ 존재).

출력: seat_model/exports/test_v1/
  env/                         학습과 같은 열람실 환경(시드 20261114) — reproduce.py가 만든다
  users/synth_users.jsonl      테스트 사용자 2,000명(test-user:*). latent.test_truth = 새 정답 로직 파라미터(평가 전용)
  base/synth_sessions.jsonl    고정 생성기 그대로 만든 요청·노출·선택(가정이 맞는 세계, λ = 0)
  base/synth_events.jsonl      (참고용, 모델·적재에 쓰지 않음)
  sets/<세트>/choices.parquet  세트별 선택(요청 맥락·노출은 base와 같고 선택만 다시 시뮬레이션)
  sets/<세트>/eval_only/truth.parquet   후보(빈자리)별 참 효용 u_true — 평가 단계에서만 읽는다
  manifest.json, dataset_report.json

- 모든 세트는 같은 사용자·같은 요청 맥락(시각·열람실·점유·노출)을 공유하므로 세트 간 비교가 짝지어진다.
- 세트별 선택 난수는 "test_seed:choice:<세트>:<session_id>"라 요청 하나만 따로 재현할 수 있다.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import truth as T
from .pinned import check_pinned, load_config, load_generator


def read_jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def write_jsonl(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def rename_user(u, prefix):
    new = u["user_id"].replace("synth-user:", f"{prefix}user:", 1)
    u["_id"] = u["user_id"] = new
    u["kakaotalk_profile"]["participant_id"] = new
    return u


def ranks(x):
    r = np.empty(len(x))
    r[np.argsort(x, kind="stable")] = np.arange(len(x))
    return r


def spearman(a, b):
    if len(a) < 3:
        return np.nan
    ra, rb = ranks(a), ranks(b)
    sa, sb = ra.std(), rb.std()
    return (
        float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))
        if sa > 0 and sb > 0
        else np.nan
    )


# ---------------------------------------------------------------- 세트별 선택 시뮬레이션 (fork 워커)
_W = {}


def _run_set(name):
    W = _W
    spec, cfg_t, seed = W["sets"][name], W["cfg_truth"], W["seed"]
    lam, modules = float(spec["lambda"]), list(spec["modules"])
    trust_mult = T.trust_multiplier(spec, cfg_t)
    users, scen, sessions = W["users"], W["scen"], W["sessions"]
    last = {}
    ch_rows, tr_sid, tr_seat, tr_u = [], [], [], []
    for s in sessions:  # 시각 순서(B3 습관이 직전 선택을 쓴다)
        user = users[s["user_id"]]
        sa = scen[s["scenario_id"]]
        occ_set = set(s["occupied_seat_ids"])
        idx = np.array([i for i, x in enumerate(sa.seat_ids) if x not in occ_set])
        ub = T.u_base(user, sa, idx, s["time_slot"])
        u = T.u_truth(
            user, sa, idx, s["time_slot"], s["occupancy_rate"], lam, modules, cfg_t
        )
        if name == "base":
            chosen, origin, proc = s["chosen_seat_id"], s["choice_origin"], "pinned"
        else:
            rng = random.Random(f"{seed}:choice:{name}:{s['session_id']}")
            chosen, origin, proc = T.choose(
                rng,
                user,
                s,
                sa,
                idx,
                u,
                lam,
                modules,
                last.get(s["user_id"]),
                cfg_t,
                trust_mult,
            )
        ids = [sa.seat_ids[i] for i in idx]
        ok = ~np.isinf(u)
        oracle = ids[int(np.argmax(u))] if ok.any() else None
        base_oracle = ids[int(np.argmax(ub))] if ok.any() else None
        if chosen is not None:
            j = ids.index(chosen)
            last[s["user_id"]] = (chosen, s["room_id"], sa.group[idx[j]])
            better = int((u[ok] > u[j]).sum())
            ch_rank = better + 1
            ch_pct = 1 - better / max(ok.sum() - 1, 1)
            cu = float(u[j])
        else:
            ch_rank = ch_pct = cu = None
        rho = spearman(u[ok], ub[ok]) if name != "base" else 1.0
        ch_rows.append(
            {
                "session_id": s["session_id"],
                "chosen_seat_id": chosen,
                "choice_origin": origin,
                "process": proc,
                "chosen_u_true": cu,
                "chosen_truth_rank": ch_rank,
                "chosen_truth_pct": ch_pct,
                "oracle_seat_id": oracle,
                "base_oracle_seat_id": base_oracle,
                "n_avail": len(ids),
                "rho_truth_vs_base": rho,
                "replay_seed": f"{seed}:choice:{name}:{s['session_id']}"
                if name != "base"
                else None,
            }
        )
        tr_sid.extend([s["session_id"]] * len(ids))
        tr_seat.extend(ids)
        tr_u.append(u.astype(np.float32))
    out = Path(W["out"]) / "sets" / name
    (out / "eval_only").mkdir(parents=True, exist_ok=True)
    ch = pd.DataFrame(ch_rows)
    ch.to_parquet(out / "choices.parquet", index=False)
    pd.DataFrame(
        {
            "session_id": pd.Categorical(tr_sid),
            "seat_id": tr_seat,
            "u_true": np.concatenate(tr_u),
        }
    ).to_parquet(out / "eval_only" / "truth.parquet", index=False, compression="zstd")
    summ = _set_summary(ch, sessions)
    summ["rec_trust_multiplier"] = trust_mult
    return name, summ


def _set_summary(ch, sessions):
    exp = {
        s["session_id"]: {e["seat_id"] for e in s["exposed"] or []} for s in sessions
    }
    base_choice = {s["session_id"]: s["chosen_seat_id"] for s in sessions}
    c = ch[ch.chosen_seat_id.notna()]
    return {
        "sessions_with_choice": int(len(c)),
        "changed_vs_base": float(
            np.mean(
                [base_choice[a] != b for a, b in zip(c.session_id, c.chosen_seat_id)]
            )
        ),
        "chosen_in_exposed": float(
            np.mean([b in exp[a] for a, b in zip(c.session_id, c.chosen_seat_id)])
        ),
        "chosen_is_oracle": float((c.chosen_seat_id == c.oracle_seat_id).mean()),
        # 라벨 반응도: 선택이 새 정답 1위를 따르는 비율 − 기존 정답(base) 1위를 따르는 비율
        "chosen_is_base_oracle": float(
            (c.chosen_seat_id == c.base_oracle_seat_id).mean()
        ),
        "label_response": float(
            (c.chosen_seat_id == c.oracle_seat_id).mean()
            - (c.chosen_seat_id == c.base_oracle_seat_id).mean()
        ),
        "truth_top1_agree_with_base": float(
            (ch.oracle_seat_id == ch.base_oracle_seat_id)[
                ch.oracle_seat_id.notna()
            ].mean()
        ),
        "chosen_truth_pct_mean": float(c.chosen_truth_pct.mean()),
        "new_process_share": float((c.process == "new").mean()),
        "habit_share": float((c.choice_origin == "habit").mean()),
        "rho_truth_vs_base_mean": float(ch.rho_truth_vs_base.mean()),
        "origin": {
            k: round(v / len(c), 4) for k, v in Counter(c.choice_origin).items()
        },
    }


# ---------------------------------------------------------------- 점검
def _profile_key(u):
    kp = u["kakaotalk_profile"]["preferences"]
    return tuple((k, kp[k]["preference"]) for k in sorted(kp)) + (
        ("req", bool(u["requirements"])),
    )


def checks(users, sessions, train_users, train_meta):
    test_ids = [u["user_id"] for u in users]
    train_ids = {u["user_id"] for u in train_users} | set(train_meta.user_id)
    sids = [s["session_id"] for s in sessions]
    tk = {_profile_key(u) for u in train_users}
    lat_k = {tuple(sorted(u["latent"]["preferences"].items())) for u in train_users}
    return {
        "user_ids_unique": len(set(test_ids)) == len(test_ids),
        "user_ids_prefix_ok": all(x.startswith("test-user:") for x in test_ids),
        "user_ids_overlap_train": len(set(test_ids) & train_ids),
        "session_ids_unique": len(set(sids)) == len(sids),
        "session_ids_prefix_ok": all(x.startswith("test-session:") for x in sids),
        "observed_profile_combo_in_train_share": float(
            np.mean([_profile_key(u) in tk for u in users])
        ),
        "latent_pref_combo_in_train_share": float(
            np.mean(
                [
                    tuple(sorted(u["latent"]["preferences"].items())) in lat_k
                    for u in users
                ]
            )
        ),
    }


def distribution(users, sessions, train_meta, train_users):
    """학습 산출물(sessions.parquet)과 같은 정의로 분포를 비교한다: 선택 있음 & 빈자리 2개 이상."""
    U = {u["user_id"]: u for u in users}
    rows = []
    for s in sessions:
        n = s["available_seat_ids_count"]
        if s["chosen_seat_id"] is None or n < 2:
            continue
        u = U[s["user_id"]]
        kp = u["kakaotalk_profile"]["preferences"]
        rows.append(
            {
                "n_avail": n,
                "time_slot": s["time_slot"],
                "exposed_any": bool(s["exposed"]),
                "chosen_exposed": s["chosen_seat_id"]
                in {e["seat_id"] for e in s["exposed"] or []},
                "laptop_req": bool(u["requirements"]),
                "n_unknown": sum(v["preference"] == "unknown" for v in kp.values()),
                "user_id": s["user_id"],
            }
        )
    te = pd.DataFrame(rows)
    tr = train_meta

    def stats(d):
        return {
            "sessions": int(len(d)),
            "sessions_per_user": round(len(d) / d.user_id.nunique(), 2),
            "n_avail_q": {
                str(q): float(d.n_avail.quantile(q)) for q in (0.1, 0.5, 0.9)
            },
            "slot_share": {
                k: round(v, 4)
                for k, v in d.time_slot.value_counts(normalize=True)
                .sort_index()
                .items()
            },
            "exposed_any": round(float(d.exposed_any.mean()), 4),
            "chosen_exposed": round(float(d.chosen_exposed.mean()), 4),
            "laptop_req": round(float(d.laptop_req.mean()), 4),
            "n_unknown_mean": round(float(d.n_unknown.mean()), 3),
        }

    arch = lambda us: {
        k: round(v / len(us), 4)
        for k, v in sorted(Counter(u["archetype"] for u in us).items())
    }
    return {
        "test": stats(te),
        "train": stats(tr),
        "archetype_test": arch(users),
        "archetype_train": arch(train_users),
    }


# ---------------------------------------------------------------- 메인
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()
    cfg = load_config()
    gc, tc = cfg["generator"], cfg["test"]
    out = Path(cfg["paths"]["exports"])
    rep = json.loads((out / "reproducibility.json").read_text())
    if not rep["passed"]:
        raise SystemExit("재현성 검사를 통과하지 않았다(reproduce.py 먼저)")
    blob = check_pinned(gc["pinned_commit"])
    g = load_generator(out / "base")
    seed, prefix = tc["seed"], tc["id_prefix"]
    if seed == gc["train_seed"]:
        raise SystemExit("테스트 시드가 학습 시드와 같다")

    # 환경: 학습과 같은 시나리오·좌석(재현성 검사에서 학습 산출물과 일치 확인)
    scen_rows = read_jsonl(out / "env" / "synth_scenarios.jsonl")
    seat_rows = read_jsonl(out / "env" / "synth_seat_env.jsonl")
    libs, _ = g.load_units()
    scen_by_lib, seats_by_scen = {}, {}
    for sc in scen_rows:
        scen_by_lib.setdefault(sc["library_id"], []).append(sc)
    for s in seat_rows:
        seats_by_scen.setdefault(s["scenario_id"], []).append(s)

    # 사용자: 고정 생성기 make_user를 테스트 시드로 → ID만 test- 로 바꾸고, 새 정답 파라미터를 latent에 추가
    urng = random.Random(f"{seed}:users")
    users = [
        rename_user(g.make_user(i, urng, libs), prefix) for i in range(tc["users"])
    ]
    for u in users:
        u["latent"]["test_truth"] = T.draw_user_ext(u, seed, cfg["truth"])
        u["test_dataset"] = "test_v1"

    # 요청·노출·선택(base): 고정 생성기 세션 로직을 그대로 쓴다
    g._G.update(
        n=tc["sessions"],
        seed=seed,
        users=users,
        scen_by_lib=scen_by_lib,
        seats_by_scen=seats_by_scen,
        start=datetime(2026, 9, 1, tzinfo=g.KST),
    )
    ctx = mp.get_context("fork")
    sess_lines, ev_lines = [], []
    with ctx.Pool(a.workers) as pool:
        for sl, el, _ in pool.imap(
            g._session_job, range(-(-tc["sessions"] // g.CHUNK))
        ):
            sess_lines.extend(sl)
            ev_lines.extend(el)
    fix = lambda line: line.replace('"synth-session:', f'"{prefix}session:')
    sessions = [json.loads(fix(x)) for x in sess_lines]
    for s in sessions:
        s["test_dataset"] = "test_v1"
    write_jsonl(out / "users" / "synth_users.jsonl", users)
    write_jsonl(out / "base" / "synth_sessions.jsonl", sessions)
    with open(out / "base" / "synth_events.jsonl", "w", encoding="utf-8") as f:
        f.writelines(fix(x) for x in ev_lines)

    # 정답 함수 검증: 벡터화한 U_base == 고정 생성기 true_utility
    sample_rng = random.Random(0)
    U = {u["user_id"]: u for u in users}
    scen = {k: T.ScenArrays(v) for k, v in seats_by_scen.items()}
    max_err = 0.0
    for s in sample_rng.sample(sessions, 300):
        sa, user = scen[s["scenario_id"]], U[s["user_id"]]
        idx = np.arange(len(sa.seat_ids))
        ub = T.u_base(user, sa, idx, s["time_slot"])
        for i in sample_rng.sample(range(len(idx)), min(10, len(idx))):
            ref = g.true_utility(
                user, seats_by_scen[s["scenario_id"]][i], s["time_slot"]
            )
            max_err = max(
                max_err, 0.0 if (ref is None and np.isinf(ub[i])) else abs(ub[i] - ref)
            )
    if max_err > 1e-9:
        raise SystemExit(
            f"U_base가 고정 생성기 true_utility와 다르다(최대 오차 {max_err})"
        )

    # 세트별 선택 다시 시뮬레이션(요청 맥락은 base와 같다)
    order = sorted(sessions, key=lambda s: (s["occurred_at"], s["session_id"]))
    _W.update(
        sets=cfg["sets"],
        cfg_truth=cfg["truth"],
        seed=seed,
        users=U,
        scen=scen,
        sessions=order,
        out=str(out),
    )
    with ctx.Pool(min(a.workers, len(cfg["sets"]))) as pool:
        set_summary = dict(pool.map(_run_set, list(cfg["sets"])))

    train_users = read_jsonl(out / "env" / "train_users.jsonl")
    train_meta = pd.read_parquet(
        Path(cfg["paths"]["train_exports"]) / "sessions.parquet",
        columns=[
            "user_id",
            "n_avail",
            "time_slot",
            "exposed_any",
            "chosen_exposed",
            "laptop_req",
            "n_unknown",
        ],
    )
    report = {
        "checks": checks(users, sessions, train_users, train_meta),
        "u_base_max_abs_err_vs_pinned": max_err,
        "distribution_base_vs_train": distribution(
            users, sessions, train_meta, train_users
        ),
        "sets": set_summary,
    }
    (out / "dataset_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    manifest = {
        "dataset": "test_v1",
        "generator_version": g.GEN_VERSION,
        "pinned_blob": blob,
        "pinned_commit": gc["pinned_commit"],
        "env_seed": gc["train_seed"],
        "test_seed": seed,
        "users": len(users),
        "sessions_generated": len(sessions),
        "sessions_target": tc["sessions"],
        "events": len(ev_lines),
        "sets": cfg["sets"],
        "truth": cfg["truth"],
        "db": cfg["db"],
        "generated_at": datetime.now(g.KST).isoformat(),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
