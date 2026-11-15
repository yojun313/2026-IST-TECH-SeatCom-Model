"""2단계: 평가 모듈. 모든 모델을 같은 분할·지표로 평가한다.

분할(sessions.parquet 기준)
  train   학습 사용자, 홀드아웃 아닌 열람실 (모델 학습)
  val     검증 사용자, 홀드아웃 아닌 열람실 (튜닝·모델 선택: y_chosen 지표만)
  A       테스트 사용자, 홀드아웃 아닌 열람실 (최종 평가 1회)
  B       학습 사용자, 홀드아웃 열람실 (처음 보는 열람실)
  C       학습 사용자, 시간 분할 이후(10월). 모델은 9월 요청만으로 다시 학습한다

참값(U_true)은 TruthReader로 latent를 읽어 v1 생성기와 같은 식으로 계산한다:
  U = Σ_k w_k (1 − |target_k − x_k|) − 0.5 · noise_aversion · noise_index,  노트북 사용자 × 노트북 금지 좌석 = 선택 불가
  exports/train_v1/eval_only/ 에만 저장하고, 특징 테이블과 합치지 않는다.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .common import TruthReader, load_config
from .data import FEATURE_COLS

SPLITS = ("train", "val", "A", "B", "C", "train_C")


def split_mask(meta: pd.DataFrame, name: str) -> np.ndarray:
    ho = meta.room_holdout.values
    sp = meta.split.values
    before = meta.before_time_split.values
    return {
        "train": (sp == "train") & ~ho,
        "val": (sp == "val") & ~ho,
        "A": (sp == "test") & ~ho,
        "B": (sp == "train") & ho,
        "train_C": (sp == "train") & ~ho & before,
        "C": (sp == "train") & ~ho & ~before,
    }[name]


class Frame:
    """한 분할의 후보 행과 요청 경계. rows는 session 순서로 연속이다."""

    def __init__(self, cand: pd.DataFrame, meta: pd.DataFrame):
        self.meta = meta.reset_index(drop=True)
        self.cand = cand.reset_index(drop=True)
        sid = self.cand.session_id.values
        change = np.r_[True, sid[1:] != sid[:-1]]
        self.starts = np.flatnonzero(change)
        self.sizes = np.diff(np.r_[self.starts, len(sid)])
        self.seg = np.repeat(np.arange(len(self.starts)), self.sizes)
        assert (
            self.cand.session_id.values[self.starts] == self.meta.session_id.values
        ).all()
        self.y = self.cand.y_chosen.values.astype(np.int8)

    def X(self, cols):
        return self.cand[cols].to_numpy(np.float32)

    def subset(self, mask):
        keep = np.repeat(mask, self.sizes)
        return Frame(self.cand[keep], self.meta[mask])


def load(cfg, cols=None, splits=SPLITS):
    exp = Path(cfg["paths"]["exports"])
    meta = pd.read_parquet(exp / "sessions.parquet")
    cols = list(
        dict.fromkeys(
            ["session_id", "seat_id", "y_chosen"] + list(cols or FEATURE_COLS)
        )
    )
    cand = pd.read_parquet(exp / "candidates.parquet", columns=cols)
    full = Frame(cand, meta)
    return {s: full.subset(split_mask(meta, s)) for s in splits}, full


# ---------------------------------------------------------------- 참값 (평가 전용)
def truth_path(cfg):
    return Path(cfg["paths"]["exports"]) / "eval_only" / "truth.parquet"


def build_truth(cfg):
    """후보 행 순서와 같은 U_true, 사용자별 archetype·참 중요도. 평가 전용 파일."""
    exp = Path(cfg["paths"]["exports"])
    seat_cols = [
        c
        for c in FEATURE_COLS
        if c.startswith("seat_") and not c.startswith("seat_desk_")
    ]
    cand = pd.read_parquet(
        exp / "candidates.parquet", columns=["session_id", "seat_id"] + seat_cols
    )
    meta = pd.read_parquet(exp / "sessions.parquet", columns=["session_id", "user_id"])
    lat = {d["user_id"]: d for d in TruthReader(cfg["db"]).find("synth_users")}
    users = sorted(lat)
    uidx = {u: i for i, u in enumerate(users)}
    keys = [c[5:] for c in seat_cols]
    Wt = np.zeros((len(users), len(keys)))
    Tt = np.zeros((len(users), len(keys)))
    na = np.zeros(len(users))
    lap = np.zeros(len(users), bool)
    for u, d in lat.items():
        L = d["latent"]
        i = uidx[u]
        for fid, tw in L["utility_weights"].items():
            j = keys.index(fid)
            Wt[i, j], Tt[i, j] = tw["weight"], tw["target"]
        na[i] = L["noise_aversion"]
        lap[i] = L["preferences"]["laptop_usage"] == "yes"
    sess_user = dict(zip(meta.session_id, meta.user_id))
    ui = np.array([uidx[sess_user[s]] for s in cand.session_id.values])
    X = cand[seat_cols].to_numpy(np.float64)
    u = (Wt[ui] * (1 - np.abs(Tt[ui] - X))).sum(1) - 0.5 * na[ui] * X[
        :, keys.index("noise_index")
    ]
    banned = lap[ui] & (X[:, keys.index("laptopAllowed")] == 0)
    u[banned] = -np.inf
    out = pd.DataFrame(
        {
            "session_id": cand.session_id.values,
            "seat_id": cand.seat_id.values,
            "u_true": u,
        }
    )
    p = truth_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(p, index=False)
    prof = pd.DataFrame(
        {"user_id": users, "archetype": [lat[u]["archetype"] for u in users]}
    )
    for j, k in enumerate(keys):
        prof[f"w_{k}"] = Wt[:, j]
        prof[f"t_{k}"] = Tt[:, j]
    prof["noise_aversion"] = na
    prof.to_parquet(p.parent / "truth_users.parquet", index=False)
    return out


def attach_truth(cfg, frames: dict):
    """평가 단계에서만 호출. 각 Frame에 u_true 배열을 붙인다."""
    t = pd.read_parquet(truth_path(cfg))
    key = pd.MultiIndex.from_arrays([t.session_id.values, t.seat_id.values])
    pos = pd.Series(np.arange(len(t)), index=key)
    for f in frames.values():
        k = pd.MultiIndex.from_arrays([f.cand.session_id.values, f.cand.seat_id.values])
        f.u_true = t.u_true.values[pos.reindex(k).values]
    return pd.read_parquet(truth_path(cfg).parent / "truth_users.parquet")


# ---------------------------------------------------------------- 지표
def _rank_within(f: Frame, score):
    """요청 안 순위(1부터). 동점은 시드 고정 무작위로 깬다."""
    rng = np.random.default_rng(0)
    s = score.astype(np.float64) + rng.uniform(0, 1e-9, len(score))
    order = np.lexsort((-s, f.seg))
    rank = np.empty(len(s), np.int64)
    rank[order] = np.arange(len(s)) - np.repeat(f.starts, f.sizes)
    return rank + 1


def per_session(
    f: Frame, score, ks=(1, 3, 5), ndcg_k=5, grade_cuts=(0.05, 0.20, 0.50), truth=True
):
    rank = _rank_within(f, score)
    ch = f.y == 1
    r_chosen = rank[ch]
    seg_ch = f.seg[ch]
    out = pd.DataFrame({"session_id": f.meta.session_id.values})
    rc = np.zeros(len(f.starts))
    rc[seg_ch] = r_chosen
    for k in ks:
        out[f"hit@{k}"] = (rc <= k).astype(float)
    out["mrr"] = 1 / rc
    # 로그 우도(점수를 요청별 softmax로 본 값; 확률 모델일 때 의미 있음)
    s = score.astype(np.float64)
    mx = np.maximum.reduceat(s, f.starts)
    lse = np.log(np.add.reduceat(np.exp(s - mx[f.seg]), f.starts)) + mx
    ll = np.zeros(len(f.starts))
    ll[seg_ch] = s[ch] - lse[seg_ch]
    out["loglik"] = ll
    if truth and hasattr(f, "u_true"):
        u = f.u_true
        ut = np.where(np.isfinite(u), u, -1e9)
        trank = _rank_within(f, ut)
        n = f.sizes[f.seg]
        cuts = [np.maximum(1, np.ceil(c * n)) for c in grade_cuts]
        grade = np.where(
            trank <= cuts[0],
            3,
            np.where(trank <= cuts[1], 2, np.where(trank <= cuts[2], 1, 0)),
        )
        gain = 2.0**grade - 1
        disc = np.where(rank <= ndcg_k, 1 / np.log2(rank + 1), 0)
        idisc = np.where(trank <= ndcg_k, 1 / np.log2(trank + 1), 0)
        dcg = np.add.reduceat(gain * disc, f.starts)
        idcg = np.add.reduceat(gain * idisc, f.starts)
        out[f"ndcg@{ndcg_k}"] = np.where(idcg > 0, dcg / np.maximum(idcg, 1e-12), 0)
        top1 = rank == 1
        out["oracle_hit@1"] = np.add.reduceat(
            (top1 & (trank == 1)).astype(float), f.starts
        )
        # 상위 1개 좌석의 참 효용 백분위(선택 가능 좌석 중). 1 = 가장 좋은 좌석
        nvalid = np.add.reduceat(np.isfinite(u).astype(float), f.starts)
        pct = np.where(
            np.isfinite(u), 1 - (trank - 1) / np.maximum(nvalid[f.seg] - 1, 1), 0
        )
        out["exp_utility_pct"] = np.add.reduceat(np.where(top1, pct, 0), f.starts)
    return out


METRICS = [
    "hit@1",
    "hit@3",
    "hit@5",
    "mrr",
    "loglik",
    "ndcg@5",
    "oracle_hit@1",
    "exp_utility_pct",
]


def summarize(ps: pd.DataFrame):
    return {m: float(ps[m].mean()) for m in METRICS if m in ps}


def bootstrap(
    per_model: dict,
    users: np.ndarray,
    ref: str,
    metrics=("hit@1", "mrr", "ndcg@5"),
    B=1000,
    seed=0,
):
    """사용자 단위 재표본 95% CI. per_model: {name: per_session DataFrame(같은 요청 순서)}."""
    uid, inv = np.unique(users, return_inverse=True)
    U = len(uid)
    rng = np.random.default_rng(seed)
    W = rng.multinomial(U, np.full(U, 1 / U), size=B).astype(np.float64)
    cnt = np.bincount(inv, minlength=U).astype(float)
    den = W @ cnt
    res = {}
    for m in metrics:
        if m not in per_model[ref]:
            continue
        base = W @ np.bincount(inv, per_model[ref][m].values, minlength=U) / den
        for name, ps in per_model.items():
            if m not in ps:
                continue
            v = W @ np.bincount(inv, ps[m].values, minlength=U) / den
            d = v - base
            res.setdefault(name, {})[m] = {
                "mean": float(ps[m].mean()),
                "ci": [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))],
                "diff_vs_ref": float(ps[m].mean() - per_model[ref][m].mean()),
                "diff_ci": [
                    float(np.percentile(d, 2.5)),
                    float(np.percentile(d, 97.5)),
                ],
            }
    return res


def bucket_table(
    ps: pd.DataFrame,
    meta: pd.DataFrame,
    col: str,
    bins=None,
    labels=None,
    metrics=("hit@1", "hit@5", "mrr", "ndcg@5"),
):
    x = meta[col]
    if bins is not None:
        x = pd.cut(x, bins=bins, labels=labels, right=False)
    g = ps.assign(_b=x.values).groupby("_b", observed=True)
    t = g[[m for m in metrics if m in ps]].mean()
    t["n"] = g.size()
    return t
