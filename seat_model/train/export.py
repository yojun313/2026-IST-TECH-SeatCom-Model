"""최종 모델 선택(검증 분할 y_chosen MRR만) + 서비스용 저장 + 전 기간 착석 기록으로 온라인 상태 구축.

    seat_model/.venv/bin/python -m seat_model.train.export

저장: seat_model/models/<버전>/manifest.json, <모델>.json, online_state.json, train.yaml
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np

from .common import FeatureReader, SEAT_MODEL_DIR, load_config, parse_ts, write_json
from .data import FEATURE_COLS, UserPrefs, candidate_block, load_scenarios
from .models.clogit import CLogit
from .online import OnlineState
from .run import MODEL_VERSION
from .userstate import K


def select(cfg):
    rep = Path(cfg["paths"]["reports"])
    J = lambda n: json.loads((rep / n).read_text()) if (rep / n).exists() else {}
    cl, gb, on = (
        J("metrics_clogit.json"),
        J("metrics_gbdt.json"),
        J("metrics_online.json"),
    )
    cands = []
    for m, v in cl.items():
        if "val" in v:
            cands.append(
                {
                    "name": m,
                    "family": "clogit",
                    "base": m[len("clogit_") :],
                    "online": False,
                    "val_mrr": v["val"]["mrr"],
                    "A_mrr": v.get("A", {}).get("mrr"),
                    "A_ndcg": v.get("A", {}).get("ndcg@5"),
                }
            )
    for b, s in (on.get("selected") or {}).items():
        a = on["prequential_A"].get(f"online_{b}", {})
        cands.append(
            {
                "name": f"online_{b}",
                "family": "clogit",
                "base": b,
                "online": True,
                "val_mrr": s["val_mrr"],
                "mode": s["mode"],
                "prior_var": s["prior_var"],
                "A_mrr": a.get("mrr"),
                "A_ndcg": a.get("ndcg@5"),
            }
        )
    for m, v in gb.items():
        if "val" in v:
            cands.append(
                {
                    "name": m,
                    "family": m.split("_")[0],
                    "online": False,
                    "val_mrr": v["val"]["mrr"],
                    "A_mrr": v.get("A", {}).get("mrr"),
                    "A_ndcg": v.get("A", {}).get("ndcg@5"),
                }
            )
    cands.sort(key=lambda c: -c["val_mrr"])
    best = cands[0]
    servable = next(
        c for c in cands if c["family"] == "clogit"
    )  # 추론 모듈·온라인 갱신은 로짓 계열
    truth_best = max(
        (c for c in cands if c["A_ndcg"] is not None), key=lambda c: c["A_ndcg"]
    )
    return cands, best, servable, truth_best


def build_online_state(cfg, model: CLogit, mode, prior_var, z_sd):
    """DB 착석 기록 전체를 시간 순서로 재생해 서비스 시작 시점(마지막 기록 이후)의 사용자 상태를 만든다."""
    rd = FeatureReader(cfg["db"])
    scen = load_scenarios(rd)
    users = {d["user_id"]: d for d in rd.find("synth_users")}
    prefs = {u: UserPrefs(d) for u, d in users.items()}
    sess = list(rd.find("synth_sessions"))
    sess.sort(key=lambda s: (s["occurred_at"], s["session_id"]))
    st = OnlineState(K, prior_var)
    idx = [FEATURE_COLS.index(c) for c in model.cols]
    m0 = cfg["data"]["inf_shrinkage"]
    n = 0
    for s in sess:
        ch = s.get("chosen_seat_id")
        if ch is None:
            continue
        sc = scen[s["scenario_id"]]
        occ = set(s["occupied_seat_ids"] or [])
        avail = np.array([i for i, x in enumerate(sc.seat_ids) if x not in occ])
        u = s["user_id"]
        hs, bu = st.get(u)
        er = {e["seat_id"]: e["rank"] for e in (s.get("exposed") or [])}
        M, seat_ids, gkeys, Xc = candidate_block(
            sc, avail, s["time_slot"], parse_ts(s["occurred_at"]), prefs[u], hs, er, m0
        )
        if ch not in seat_ids:
            continue
        ci = seat_ids.index(ch)
        if len(seat_ids) >= 2:
            g = model.score_matrix(M[:, idx], neutral_exp=(mode != "logged_exposure"))
            bu.update(g, Xc / z_sd, ci)
        hs.update(ch, gkeys[ci], Xc[ci])
        n += 1
    return st, n, sess[-1]["occurred_at"]


def main():
    cfg = load_config()
    rep = Path(cfg["paths"]["reports"])
    mdir = Path(cfg["paths"]["models"]) / MODEL_VERSION
    cands, best, serv, truth_best = select(cfg)
    on = json.loads((rep / "metrics_online.json").read_text())
    z_sd = np.array(on["z_sd"])
    base = serv["base"]
    mode = serv.get("mode", "neutral")
    prior_var = serv.get("prior_var", cfg["online"]["prior_var"])
    # 서빙 모델은 온라인 층을 항상 켠다(검증으로 고른 τ²). 정적 로짓이 선택됐으면 같은 전역 모델의 온라인 설정을 쓴다
    if not serv["online"] and base in on.get("selected", {}):
        mode, prior_var = (
            on["selected"][base]["mode"],
            on["selected"][base]["prior_var"],
        )
    model = CLogit.from_state(json.loads((mdir / f"clogit_{base}.json").read_text()))
    st, n, last = build_online_state(cfg, model, mode, prior_var, z_sd)
    (mdir / "online_state.json").write_text(json.dumps(st.to_dict()), encoding="utf-8")
    shutil.copy(SEAT_MODEL_DIR / "config" / "train.yaml", mdir / "train.yaml")
    man = {
        "model_version": MODEL_VERSION,
        "data_version": "v1",
        "generator_version": cfg["generator_version"],
        "model_type": "clogit+online",
        "model_file": f"clogit_{base}.json",
        "base_variant": base,
        "feature_cols": model.cols,
        "all_feature_cols": FEATURE_COLS,
        "z_sd": z_sd.tolist(),
        "online": {
            "mode": mode,
            "prior_var": prior_var,
            "inf_shrinkage": cfg["data"]["inf_shrinkage"],
            "profile_min_sessions": cfg["online"]["profile_min_sessions"],
            "profile_min_z": cfg["online"]["profile_min_z"],
        },
        "online_state": {
            "file": "online_state.json",
            "users": len(st.hist),
            "sessions_replayed": n,
            "last_record_at": last,
        },
        "selection_rule": "검증 분할 y_chosen MRR 최대(model_train.md 공통 원칙). truth 지표는 보고만",
        "selected_by_rule": best,
        "served": serv,
        "best_by_truth_ndcg_A": truth_best,
        "rule_filter": "requirements(노트북 규정 등)를 모델 밖에서 먼저 적용",
        "excluded_from_training": [
            "satisfaction",
            "ratingSubmitted",
            "synth_events",
            "choice_origin",
            "latent",
            "chosen_true_utility",
            "chosen_preference_fit",
            "is_correct_vs_latent",
        ],
    }
    write_json(mdir / "manifest.json", man)
    lines = [
        f"- 규칙(검증 `y_chosen` MRR 최대)으로 고른 모델: **{best['name']}** (검증 MRR {best['val_mrr']:.4f})",
        f"- 서비스 저장 모델: **로짓 {base} + 온라인 개인 계수**(갱신 {mode}, τ² {prior_var}). 추론 모듈과 착석 즉시 갱신을 지원한다.",
        f"- 참 효용 NDCG@5(A)가 가장 높은 모델: {truth_best['name']} ({truth_best['A_ndcg']:.4f}) — 보고용이며 선택에 쓰지 않았다.",
        f"- 온라인 상태: 사용자 {len(st.hist):,}명, 착석 기록 {n:,}건 재생(마지막 기록 {last}).",
        "",
        "| 후보 | 검증 MRR | A MRR | A NDCG@5 |",
        "|---|---:|---:|---:|",
    ]
    for c in cands[:14]:
        lines.append(
            f"| {c['name']} | {c['val_mrr']:.4f} | {c['A_mrr'] or 0:.4f} | {(c['A_ndcg'] or 0):.4f} |"
        )
    write_json(
        rep / "model_selection.json",
        {
            "candidates": cands,
            "selected": best,
            "served": serv,
            "truth_best": truth_best,
            "text": "\n".join(lines),
        },
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
