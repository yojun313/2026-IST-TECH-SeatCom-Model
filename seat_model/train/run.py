"""단계 실행기. 결과: exports/train_v1/results/*.parquet(요청별 지표), reports/metrics_*.json, models/<버전>/

    seat_model/.venv/bin/python -m seat_model.train.run --stage baselines
    seat_model/.venv/bin/python -m seat_model.train.run --stage clogit
    seat_model/.venv/bin/python -m seat_model.train.run --stage gbdt
    seat_model/.venv/bin/python -m seat_model.train.run --stage online
    seat_model/.venv/bin/python -m seat_model.train.run --stage clusters

모든 점수는 마지막에 필수 조건(노트북 규정) 규칙 필터를 똑같이 적용한다: 조건을 만족하는 좌석을 먼저 놓는다.
노출 특징은 점수 계산 때 0(미노출)으로 둔다. 새 추천 정책은 기존 로깅 정책의 노출을 알 수 없기 때문이다.
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import evaluate as E
from .common import load_config, write_json
from .data import FEATURE_COLS, MATCH_COLS, PREF_FIDS, SEAT_COLS
from .userstate import SEAT_KEYS

EVAL_SPLITS = ("val", "A", "B")
MODEL_VERSION = "v1-20261115"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rule(frame, s):
    return s + 1000.0 * frame.cand["ctx_req_ok"].to_numpy(np.float64)


class Ctx:
    def __init__(self, cfg, splits=E.SPLITS):
        self.cfg = cfg
        if not E.truth_path(cfg).exists():
            log("참값(평가 전용) 계산")
            E.build_truth(cfg)
        self.fr, self.full = E.load(cfg, splits=splits)
        self.truth_users = E.attach_truth(cfg, self.fr)
        self.res = Path(cfg["paths"]["exports"]) / "results"
        self.res.mkdir(exist_ok=True)
        self.rep = Path(cfg["paths"]["reports"])
        self.mdir = Path(cfg["paths"]["models"]) / MODEL_VERSION
        self.mdir.mkdir(parents=True, exist_ok=True)

    def evaluate(self, name, score_fn, splits=EVAL_SPLITS):
        out = {}
        for sp in splits:
            f = self.fr[sp]
            ps = E.per_session(f, rule(f, score_fn(f)), grade_cuts=self.cfg["eval"]["grade_cuts"])
            ps.to_parquet(self.res / f"{name}__{sp}.parquet", index=False)
            out[sp] = E.summarize(ps)
        log(name, {sp: round(v["mrr"], 4) for sp, v in out.items()})
        return out


def stage_baselines(c: Ctx):
    m = {}
    rng = np.random.default_rng(c.cfg["seed"])
    m["random"] = c.evaluate("random", lambda f: rng.random(len(f.y)))
    m["entrance_nearest"] = c.evaluate("entrance_nearest", lambda f: f.cand["seat_entranceProximity"].to_numpy())
    tr = c.fr["train"].cand
    g = tr.groupby("seat_id").y_chosen.agg(["sum", "count"])
    prior = tr.y_chosen.mean()
    pop = ((g["sum"] + 5 * prior) / (g["count"] + 5)).to_dict()
    m["popularity"] = c.evaluate("popularity", lambda f: f.cand.seat_id.map(pop).fillna(prior).to_numpy())
    m["service_rule"] = c.evaluate("service_rule", lambda f: f.cand["match_score"].to_numpy())

    def equal_w(f):
        imp = f.cand[[f"pref_imp_{k}" for k in PREF_FIDS]].to_numpy()
        mt = f.cand[MATCH_COLS[:-1]].to_numpy()
        return np.where(imp > 0, mt / np.maximum(imp, 1e-9), 0).sum(1)
    m["equal_weight_rule"] = c.evaluate("equal_weight_rule", equal_w)
    m["truth_upper_bound"] = c.evaluate("truth_upper_bound", lambda f: np.where(np.isfinite(f.u_true), f.u_true, -1e6))
    write_json(c.rep / "metrics_baselines.json", m)


def stage_clogit(c: Ctx):
    from .models.clogit import VARIANTS, CLogit
    cc = c.cfg["clogit"]
    m, coefs = {}, {}
    for v, cols in VARIANTS.items():
        t = time.time()
        mdl = CLogit(cols, cc["l2"], cc["maxiter"]).fit(c.fr["train"])
        log(f"clogit {v}: {mdl.opt} {time.time() - t:.0f}s")
        m[f"clogit_{v}"] = c.evaluate(f"clogit_{v}", mdl.score)
        coefs[v] = mdl.coef_table()
        (c.mdir / f"clogit_{v}.json").write_text(json.dumps(mdl.state()), encoding="utf-8")
        if v == "a_full":
            m["clogit_a_full_with_logged_exposure"] = c.evaluate("clogit_a_full_with_logged_exposure",
                                                                 lambda f: mdl.score(f, neutral_exp=False), ("A",))
    # (C) 시간 분할: 9월 요청으로만 다시 학습 → 10월 평가
    for v in ("a_full", "g_no_profile"):
        mdl = CLogit(VARIANTS[v], cc["l2"], cc["maxiter"]).fit(c.fr["train_C"])
        m[f"clogit_{v}"]["C"] = c.evaluate(f"clogit_{v}", mdl.score, ("C",))["C"]
    write_json(c.rep / "metrics_clogit.json", m)
    write_json(c.rep / "clogit_coefficients.json", coefs)


def stage_gbdt(c: Ctx, n_trials=None):
    from .models import gbdt as G
    g = c.cfg["gbdt"]
    n_trials = n_trials or g["optuna_trials"]
    mrr = lambda f, s: float(E.per_session(f, rule(f, s), truth=False)["mrr"].mean())
    cols = G.variant_cols("a_full")
    best, trials = G.tune_lgb(c.fr["train"], c.fr["val"], cols, c.cfg, mrr, n_trials, log)
    write_json(c.rep / "gbdt_optuna.json", {"best_params": best, "trials": trials})
    m, shap = {}, {}
    for v in G.VARIANTS:
        t = time.time()
        vc = G.variant_cols(v)
        mdl = G.LGBRanker(vc, best, g["max_rounds"], g["early_stopping"], seed=c.cfg["seed"]).fit(c.fr["train"], c.fr["val"])
        log(f"lgbm {v}: iter={mdl.best_iter} {time.time() - t:.0f}s")
        m[f"lgbm_{v}"] = c.evaluate(f"lgbm_{v}", mdl.score)
        mdl.booster.save_model(str(c.mdir / f"lgbm_{v}.txt"), num_iteration=mdl.best_iter)
        if v == "a_full":
            shap[v] = mdl.shap(c.fr["val"])
            mC = G.LGBRanker(vc, best, g["max_rounds"], g["early_stopping"], seed=c.cfg["seed"]).fit(c.fr["train_C"], c.fr["val"])
            m[f"lgbm_{v}"]["C"] = c.evaluate(f"lgbm_{v}", mC.score, ("C",))["C"]
    t = time.time()
    x = G.XGBRanker(cols, device=g["xgb_device"], rounds=g["max_rounds"], es=g["early_stopping"], seed=c.cfg["seed"]).fit(c.fr["train"], c.fr["val"])
    log(f"xgb a_full: device={x.used_device} iter={x.best_iter} {time.time() - t:.0f}s")
    m["xgb_a_full"] = c.evaluate("xgb_a_full", x.score)
    m["xgb_a_full"]["_device"] = x.used_device
    write_json(c.rep / "metrics_gbdt.json", m)
    write_json(c.rep / "gbdt_shap.json", shap)


def stage_online(c: Ctx):
    """착석 기록 기반 취향 파악 + 연속 반영(사용자 층 베이지안, 전역 층 주간 재학습)."""
    from .models.clogit import VARIANTS, CLogit
    from . import online as O
    cfg = c.cfg
    cc = cfg["clogit"]
    # 사용자 층: 테스트 사용자의 모든 요청을 시간 순서로 처리하고, 지표는 A(홀드아웃 아닌 열람실)에서만 낸다
    meta = c.full.meta
    tmask = meta.split.values == "test"
    ft = c.full.subset(tmask)
    E.attach_truth(cfg, {"t": ft})
    users = ft.meta.user_id.values
    inA = ~ft.meta.room_holdout.values
    z_sd = c.fr["train"].X(O.SEATC_COLS).std(0).astype(np.float64) + 1e-6
    out, curves, recov, profiles = {}, [], {}, {}
    bases = {"h_profile_only": "관측 프로필만 + 온라인 개인 계수", "i_population": "취향 미입력(개인화 없음) + 온라인 개인 계수",
             "a_full": "전체(이력 특징 포함) + 온라인 개인 계수", "g_no_profile": "취향 미입력 + 이력 특징 + 온라인 개인 계수",
             "b_no_exp": "노출 특징 없이 학습 + 온라인 개인 계수"}
    # 검증 사용자로 갱신 방식(neutral/logged)과 사전분산 τ²를 고른다(y_chosen MRR). 테스트는 고른 설정으로 1회
    fv = c.full.subset((meta.split.values == "val"))
    inV = ~fv.meta.room_holdout.values
    grid, chosen_cfg = [], {}
    for v in bases:
        mdl = CLogit.from_state(json.loads((c.mdir / f"clogit_{v}.json").read_text()))
        gv, gv_log = mdl.score(fv), mdl.score(fv, neutral_exp=False)
        best = None
        for mode in ("neutral", "logged_exposure"):
            for tau in cfg["online"]["prior_var_grid"]:
                cv = dict(cfg, online=dict(cfg["online"], prior_var=tau))
                s_v, *_ = O.prequential(fv, gv, z_sd, cv, fv.meta.user_id.values,
                                        g_update=gv_log if mode == "logged_exposure" else None)
                mrr = float(E.per_session(fv, rule(fv, s_v), truth=False)["mrr"].values[inV].mean())
                grid.append({"base": v, "mode": mode, "prior_var": tau, "val_mrr": mrr})
                if best is None or mrr > best[0]:
                    best = (mrr, mode, tau)
        chosen_cfg[v] = {"val_mrr": best[0], "mode": best[1], "prior_var": best[2],
                         "val_mrr_static": float(E.per_session(fv, rule(fv, gv), truth=False)["mrr"].values[inV].mean())}
        log(f"online select {v}: {chosen_cfg[v]}")
    for v in bases:
        st = json.loads((c.mdir / f"clogit_{v}.json").read_text())
        mdl = CLogit.from_state(st)
        g = mdl.score(ft)
        sel = chosen_cfg[v]
        cv = dict(cfg, online=dict(cfg["online"], prior_var=sel["prior_var"]))
        g_upd = mdl.score(ft, neutral_exp=False) if sel["mode"] == "logged_exposure" else None
        s_on, state, snaps, final = O.prequential(ft, g, z_sd, cv, users, g_update=g_upd)
        for name, s in ((f"clogit_{v}", g), (f"online_{v}", s_on)):
            ps = E.per_session(ft, rule(ft, s), grade_cuts=cfg["eval"]["grade_cuts"])
            ps = ps[inA].reset_index(drop=True)
            ps.to_parquet(c.res / f"{name}__A_prequential.parquet", index=False)
            out[name] = E.summarize(ps)
            mA = ft.meta[inA].reset_index(drop=True)
            bt = E.bucket_table(ps, mA, "hist_n", bins=[0, 1, 3, 6, 11, 21, 41, 1000],
                                labels=["0", "1-2", "3-5", "6-10", "11-20", "21-40", "41+"])
            for b, r in bt.iterrows():
                curves.append({"model": name, "hist_n": str(b), **{k: float(x) for k, x in r.items()}})
        log(f"online {v}: static={out[f'clogit_{v}']['mrr']:.4f} online={out[f'online_{v}']['mrr']:.4f} "
            f"ndcg {out[f'clogit_{v}']['ndcg@5']:.4f}->{out[f'online_{v}']['ndcg@5']:.4f}")
        recov[v] = O.recovery(snaps, final, c.truth_users)
        if v == "i_population":
            ex = sorted(state)[:5]
            profiles = {u: O.infer_profile(state[u], z_sd, cfg["online"]["profile_min_sessions"], cfg["online"]["profile_min_z"]) for u in ex}
            with open(c.mdir / "online_state_test_users.pkl", "wb") as fh:
                pickle.dump({u: (b.mu, b.P, b.n) for u, b in state.items()}, fh)
    # 관측 프로필 자체의 참 취향 상관(비교 기준): pref_imp·sign(target−0.5)
    D0 = {}
    pu = ft.cand.groupby(ft.seg)[[f"pref_imp_{k}" for k in PREF_FIDS] + [f"pref_tgt_{k}" for k in PREF_FIDS]].first()
    pu["user_id"] = users
    pu = pu.groupby("user_id").first()
    from scipy.stats import spearmanr
    D = O.truth_direction(c.truth_users, list(pu.index))
    for k in PREF_FIDS:
        j = SEAT_KEYS.index(k)
        est = pu[f"pref_imp_{k}"].values * np.sign(pu[f"pref_tgt_{k}"].values - 0.5)
        D0[k] = float(spearmanr(est, D[:, j]).statistic)
    # 전역 층: 주간 웜스타트 재학습 vs 정적 모델
    log("주간 재학습 실험")
    weekly = O.weekly_retrain(c.full, meta, lambda: CLogit(VARIANTS["a_full"], cc["l2"], 100), cfg,
                              (meta.split.values == "test") & ~meta.room_holdout.values, log)
    write_json(c.rep / "metrics_online.json", {"prequential_A": out, "curve_by_history": curves,
                                               "recovery_spearman": recov, "observed_profile_spearman": D0,
                                               "weekly_retrain": weekly, "example_inferred_profiles": profiles,
                                               "val_grid": grid, "selected": chosen_cfg,
                                               "z_sd": z_sd.tolist()})


def stage_clusters(c: Ctx):
    """8단계-2: 좌석 유형 군집(설명용)."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    f = c.fr["train"].cand
    keys = ["partition", "outletAvailable", "windowProximity", "brightness", "airflow", "deskWidth", "noise_index",
            "toiletProximity", "entranceProximity"]
    X = f.drop_duplicates("seat_id")[[f"seat_{k}" for k in keys]].to_numpy()
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    best = None
    for k in range(4, 9):
        km = KMeans(k, n_init=10, random_state=c.cfg["seed"]).fit(Z)
        s = silhouette_score(Z, km.labels_, sample_size=min(5000, len(Z)), random_state=0)
        if best is None or s > best[0]:
            best = (s, k, km)
    s, k, km = best
    names = {"partition": ("칸막이", ""), "outletAvailable": ("콘센트", ""), "windowProximity": ("창가", "안쪽"),
             "brightness": ("밝음", "어두움"), "airflow": ("바람 셈", "바람 약함"), "deskWidth": ("넓은 책상", "좁은 책상"),
             "noise_index": ("시끄러움", "조용함"), "toiletProximity": ("화장실 가까움", "화장실 멂"),
             "entranceProximity": ("출입구 가까움", "출입구 멂")}
    out = []
    for i, ctr in enumerate(km.cluster_centers_):
        top = np.argsort(-np.abs(ctr))[:3]
        label = " · ".join(names[keys[j]][0 if ctr[j] > 0 else 1] or f"{names[keys[j]][0]} 없음" for j in top)
        out.append({"cluster": i, "label": label, "size": int((km.labels_ == i).sum()),
                    "center_raw": dict(zip(keys, (ctr * sd + mu).round(3).tolist()))})
    write_json(c.rep / "seat_clusters.json", {"k": k, "silhouette": float(s), "clusters": out})
    with open(c.mdir / "seat_clusters.pkl", "wb") as fh:
        pickle.dump({"keys": keys, "mu": mu, "sd": sd, "km": km, "labels": out}, fh)
    log("clusters", k, round(s, 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["baselines", "clogit", "gbdt", "online", "clusters"])
    ap.add_argument("--trials", type=int)
    ap.add_argument("--config")
    a = ap.parse_args()
    cfg = load_config(a.config)
    c = Ctx(cfg)
    {"baselines": stage_baselines, "clogit": stage_clogit, "online": stage_online, "clusters": stage_clusters,
     "gbdt": lambda c: stage_gbdt(c, a.trials)}[a.stage](c)


if __name__ == "__main__":
    main()
