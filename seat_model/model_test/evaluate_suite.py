"""오프라인 트랙 평가: 테스트셋마다 user_test 적재 → data.py 특징 → 7개 모델 점수 → 지표·신뢰구간·구간별 성능.

    seat_model/.venv/bin/python -m seat_model.model_test.evaluate_suite --validate     # 채점 파이프라인 검증(학습 분할 A 재현)
    seat_model/.venv/bin/python -m seat_model.model_test.evaluate_suite                # 전체 세트

- 모델 점수는 학습 때(run.py)와 같은 방식: 노출 특징 0(중립), 필수 조건 규칙(조건 만족 좌석 먼저).
- data.py의 분할은 덮어쓴다: 테스트 사용자 전원 test, 홀드아웃 열람실은 train_v1/splits.json의 4개.
- 참값(u_true)은 세트별 eval_only/truth.parquet에서 평가 단계에만 붙인다.
출력: seat_model/exports/test_v1/eval/<세트>/(candidates·sessions·results/*.parquet), eval/offline_summary.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from seat_model.train import data as D
from seat_model.train import evaluate as E
from seat_model.train.common import load_config as load_train_config
from seat_model.train.models.clogit import CLogit
from seat_model.train.models.gbdt import neutralize
from seat_model.train.run import MODEL_VERSION

from .load_user_test import load_set
from .pinned import load_config

MODELS = [
    "clogit_b_no_exp",
    "clogit_a_full",
    "lgbm_b_no_exp",
    "service_rule",
    "popularity",
    "entrance_nearest",
    "random",
]
SERVICE = "clogit_b_no_exp"
METRICS = ["ndcg@5", "oracle_hit@1", "exp_utility_pct", "hit@1", "hit@3", "mrr"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rule(frame, s):
    return s + 1000.0 * frame.cand["ctx_req_ok"].to_numpy(np.float64)


class Scorers:
    """저장된 모델과 기준선. 테스트 결과로 바뀌는 것은 없다(학습 산출물·모델 파일 읽기만)."""

    def __init__(self, tcfg):
        import lightgbm as lgb

        mdir = Path(tcfg["paths"]["models"]) / MODEL_VERSION
        self.clogit = {
            v: CLogit.from_state(json.loads((mdir / f"{v}.json").read_text()))
            for v in ("clogit_b_no_exp", "clogit_a_full")
        }
        self.lgbm = lgb.Booster(model_file=str(mdir / "lgbm_b_no_exp.txt"))
        self.lgbm_cols = self.lgbm.feature_name()
        tr = Path(tcfg["paths"]["exports"])  # 학습 산출물: 학습 분할 좌석 선택률
        meta = pd.read_parquet(
            tr / "sessions.parquet", columns=["session_id", "split", "room_holdout"]
        )
        cand = pd.read_parquet(
            tr / "candidates.parquet", columns=["session_id", "seat_id", "y_chosen"]
        )
        keep = set(meta.session_id[(meta.split == "train") & ~meta.room_holdout])
        c = cand[cand.session_id.isin(keep)]
        g = c.groupby("seat_id").y_chosen.agg(["sum", "count"])
        self.prior = float(c.y_chosen.mean())
        self.pop = ((g["sum"] + 5 * self.prior) / (g["count"] + 5)).to_dict()
        self.seed = tcfg["seed"]

    def scores(self, f):
        out = {}
        for v, m in self.clogit.items():
            out[v] = m.score(f)
        X = neutralize(f.X(self.lgbm_cols), self.lgbm_cols)
        out["lgbm_b_no_exp"] = self.lgbm.predict(X)
        out["service_rule"] = f.cand["match_score"].to_numpy()
        out["popularity"] = f.cand.seat_id.map(self.pop).fillna(self.prior).to_numpy()
        out["entrance_nearest"] = f.cand["seat_entranceProximity"].to_numpy()
        out["random"] = np.random.default_rng(self.seed).random(len(f.y))
        return {k: rule(f, v) for k, v in out.items()}


def attach_truth(f, truth_file):
    t = pd.read_parquet(truth_file)
    pos = pd.Series(
        np.arange(len(t)),
        index=pd.MultiIndex.from_arrays(
            [t.session_id.astype(str).values, t.seat_id.values]
        ),
    )
    k = pd.MultiIndex.from_arrays([f.cand.session_id.values, f.cand.seat_id.values])
    idx = pos.reindex(k).values
    if np.isnan(idx).any():
        raise RuntimeError(f"참값이 없는 후보 {int(np.isnan(idx).sum())}행")
    f.u_true = t.u_true.values[idx.astype(int)].astype(np.float64)


def evaluate_frame(f, scorers, grade_cuts):
    sc = scorers.scores(f)
    return {m: E.per_session(f, sc[m], grade_cuts=grade_cuts) for m in MODELS}


def summarize(per, users, B):
    boot = E.bootstrap(per, users, ref="service_rule", metrics=METRICS, B=B)
    return {m: {k: boot[m][k] for k in METRICS if k in boot[m]} for m in per}


# ---------------------------------------------------------------- 검증: 학습 분할 A 재현
def validate(tcfg):
    s = Scorers(tcfg)
    fr, _ = E.load(tcfg, splits=("A",))
    E.attach_truth(tcfg, fr)
    per = evaluate_frame(fr["A"], s, tcfg["eval"]["grade_cuts"])
    rep = Path(tcfg["paths"]["reports"])
    ref = {}
    for fn in ("metrics_clogit.json", "metrics_gbdt.json", "metrics_baselines.json"):
        ref.update(json.loads((rep / fn).read_text()))
    out = {}
    for m in MODELS:
        got = {k: float(per[m][k].mean()) for k in ("hit@1", "mrr", "ndcg@5")}
        want = {k: ref[m]["A"][k] for k in got}
        out[m] = {
            "got": got,
            "report": want,
            "max_abs_diff": max(abs(got[k] - want[k]) for k in got),
        }
    return out


# ---------------------------------------------------------------- 테스트셋 평가
def build_features(name, tcfg, holdout, out_dir):
    load_set(name)
    orig = D.make_splits
    D.make_splits = lambda cfg, arch, rooms_lib, rng: (
        {u: "test" for u in arch},
        sorted(holdout),
    )
    try:
        cfg = json.loads(json.dumps(tcfg))
        cfg["db"] = "user_test"
        rep = D.build(cfg, out=out_dir)
    finally:
        D.make_splits = orig
    return rep


def slices(per, meta, users_arch):
    """base 세트 구간별 성능(주 지표 중심)."""
    m = meta.copy()
    m["hist"] = np.where(m.hist_n > 0, "이력 있음", "신규(이력 0)")
    m["info"] = pd.cut(
        m.n_unknown,
        [0, 3, 5, 8],
        right=False,
        labels=["unknown 0–2", "unknown 3–4", "unknown 5+"],
    ).astype(str)
    m["room"] = np.where(m.room_holdout, "홀드아웃 열람실", "학습 열람실")
    q = m.occ.quantile([1 / 3, 2 / 3]).values
    m["crowd"] = np.where(
        m.occ < q[0], "혼잡 하", np.where(m.occ < q[1], "혼잡 중", "혼잡 상")
    )
    m["archetype"] = m.user_id.map(users_arch)
    rows = []
    for dim in ("hist", "info", "room", "crowd", "archetype"):
        for lv, idx in m.groupby(dim).groups.items():
            sel = m.index.isin(idx)
            r = {
                "dim": dim,
                "level": lv,
                "n_requests": int(sel.sum()),
                "n_users": int(m.user_id[sel].nunique()),
            }
            for mod, ps in per.items():
                for k in ("ndcg@5", "oracle_hit@1", "hit@3"):
                    r[f"{mod}|{k}"] = float(ps[k].values[sel].mean())
            rows.append(r)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--sets", nargs="*")
    a = ap.parse_args()
    tcfg = load_train_config()
    if a.validate:
        print(json.dumps(validate(tcfg), ensure_ascii=False, indent=2))
        return
    cfg = load_config()
    root = Path(cfg["paths"]["exports"]) / "eval"
    holdout = json.loads(
        (Path(cfg["paths"]["train_exports"]) / "splits.json").read_text()
    )["room_holdout"]
    users_arch = {}
    with open(
        Path(cfg["paths"]["exports"]) / "users" / "synth_users.jsonl", encoding="utf-8"
    ) as fh:
        for line in fh:  # 평가 단계 전용(구간 나누기)
            d = json.loads(line)
            users_arch[d["user_id"]] = d["archetype"]
    scorers = Scorers(tcfg)
    B = cfg["eval"]["bootstrap"]
    summary_path = root / "offline_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    for name in a.sets or list(cfg["sets"]):
        t0 = time.time()
        out = root / name
        rep = build_features(name, tcfg, holdout, out)
        meta = pd.read_parquet(out / "sessions.parquet")
        cand = pd.read_parquet(out / "candidates.parquet")
        f = E.Frame(cand, meta)
        attach_truth(
            f,
            Path(cfg["paths"]["exports"])
            / "sets"
            / name
            / "eval_only"
            / "truth.parquet",
        )
        per = evaluate_frame(f, scorers, tcfg["eval"]["grade_cuts"])
        (out / "results").mkdir(exist_ok=True)
        for mod, ps in per.items():
            ps.assign(user_id=meta.user_id.values).to_parquet(
                out / "results" / f"{mod}.parquet", index=False
            )
        summary[name] = {
            "data": {
                k: rep[k]
                for k in (
                    "sessions_total",
                    "sessions_used",
                    "rows",
                    "excluded",
                    "chosen_violates_requirements",
                )
            },
            "users": int(meta.user_id.nunique()),
            "metrics": summarize(per, meta.user_id.values, B),
        }
        if name == "base":
            meta["occ"] = cand.ctx_occ.values[f.starts]
            sl = slices(per, meta, users_arch)
            sl.to_parquet(root / "base_slices.parquet", index=False)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        mm = summary[name]["metrics"]
        log(
            name,
            f"{time.time() - t0:.0f}s",
            {
                m: round(mm[m]["ndcg@5"]["mean"], 4)
                for m in (SERVICE, "lgbm_b_no_exp", "service_rule")
            },
        )
    load_set("base")  # 끝나면 base로 되돌린다


if __name__ == "__main__":
    main()
