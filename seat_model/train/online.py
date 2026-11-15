"""11~12단계(신규): 착석 기록으로 취향을 스스로 파악하고, 선택이 쌓일 때마다 바로 반영하는 온라인 학습.

두 층으로 반영한다.
1) 사용자 층(즉시, 재학습 없음)
   - UserState(hist_·inf_ 특징)를 선택 1건마다 갱신한다(userstate.py).
   - 베이지안 개인 계수 μ_u: 점수 V = g(전역 모델) + μ_u · z(빈자리 평균을 뺀 좌석 특징, 표준화).
     사전 μ ~ N(0, τ²I). 선택이 관측되면 그 요청의 softmax 우도로 뉴턴 1스텝(가정 밀도 필터/라플라스):
        p = softmax(g + Zμ),  P ← P + Zᵀ(diag p − ppᵀ)Z,  μ ← μ + P⁻¹ Zᵀ(y − p)
     취향 입력이 없어도 μ_u가 착석 기록만으로 취향 방향과 강도를 학습한다. P⁻¹은 불확실성이다.
   - infer_profile(): μ_u, P⁻¹ → 서비스 preference_profile 형식(featureId·target·importance)과 확신도.
2) 전역 층(주기적): 새로 쌓인 요청으로 조건부 로짓을 웜스타트 재학습한다(weekly_retrain).
"""

from __future__ import annotations

import numpy as np

from .userstate import SEAT_KEYS, UserState

SEATC_COLS = [f"seatc_{k}" for k in SEAT_KEYS]
FEATURE_KO = {
    "laptopAllowed": "노트북 가능",
    "keyboardAllowed": "키보드 가능",
    "partition": "칸막이",
    "outletAvailable": "콘센트",
    "windowProximity": "창가",
    "toiletProximity": "화장실 가까움",
    "entranceProximity": "출입구 가까움",
    "brightness": "밝음",
    "airflow": "바람(냉방) 셈",
    "deskHeight": "높은 책상",
    "deskWidth": "넓은 책상",
    "viewQuality": "전망",
    "noise_index": "소음",
}


class BayesUser:
    __slots__ = ("mu", "P", "n")

    def __init__(self, k, prior_var):
        self.mu = np.zeros(k)
        self.P = np.eye(k) / prior_var
        self.n = 0

    def scores(self, g, Z):
        return g + Z @ self.mu

    def update(self, g, Z, ci, steps=1):
        y = np.zeros(len(g))
        y[ci] = 1
        for _ in range(steps):
            v = g + Z @ self.mu
            p = np.exp(v - v.max())
            p /= p.sum()
            Zp = Z.T @ p
            H = (Z * p[:, None]).T @ Z - np.outer(Zp, Zp)
            self.P = self.P + H if _ == 0 else self.P
            self.mu = self.mu + np.linalg.solve(self.P, Z.T @ (y - p))
        self.n += 1

    def cov(self):
        return np.linalg.inv(self.P)


def infer_profile(bu: BayesUser, z_sd, min_sessions=3, min_z=1.0):
    """μ_u → 서비스 프로필. z_sd: 표준화에 쓴 좌석 특징 표준편차(원 단위 기울기로 환산)."""
    sd = np.sqrt(np.diag(bu.cov()))
    zstat = bu.mu / sd
    items = []
    for j, k in enumerate(SEAT_KEYS):
        items.append(
            {
                "featureId": k,
                "label_ko": FEATURE_KO[k],
                "direction": "high" if bu.mu[j] > 0 else "low",
                "strength": float(bu.mu[j]),
                "z": float(zstat[j]),
                "slope_raw": float(bu.mu[j] / z_sd[j]),
            }
        )
    known = [i for i in items if abs(i["z"]) >= min_z] if bu.n >= min_sessions else []
    mx = max([abs(i["strength"]) for i in known], default=1.0)
    prefs = []
    for i in known:
        if i["featureId"] == "noise_index":
            continue  # 서비스 특성 레지스트리에 없음. 설명에는 쓴다
        prefs.append(
            {
                "featureId": i["featureId"],
                "target": 1.0 if i["direction"] == "high" else 0.0,
                "importance": round(min(1.0, abs(i["strength"]) / mx), 3),
                "source": "seat_history",
                "confidence": round(float(1 - np.exp(-abs(i["z"]) / 2)), 3),
            }
        )
    return {
        "n_sessions": bu.n,
        "preferences": sorted(prefs, key=lambda p: -p["importance"]),
        "all": items,
    }


def prequential(
    frame, g, z_sd, cfg, users, g_update=None, snapshots=(0, 1, 3, 5, 10, 20, 40)
):
    """요청을 시간 순서로 처리: (1) 지금 상태로 점수 → 기록 (2) 실제 선택으로 갱신.
    g: 추천 점수용 전역 점수(노출 중립). g_update: 갱신 우도용 전역 점수(실제로 보였던 노출 포함).
    실제 선택은 노출의 영향을 받았으므로, 갱신 때 노출 효과를 빼지 않으면 μ_u가 로깅 정책의 취향을 배운다.
    반환: 갱신 전 점수(평가용), 사용자별 BayesUser, 착석 횟수별 μ 스냅샷."""
    g_update = g if g_update is None else g_update
    k = len(SEAT_KEYS)
    tau2 = cfg["online"]["prior_var"]
    steps = cfg["online"]["max_newton_steps"]
    Zall = frame.X(SEATC_COLS).astype(np.float64) / z_sd
    out = np.empty(len(g))
    state = {}
    snaps = {}
    for i, (s0, n) in enumerate(zip(frame.starts, frame.sizes)):
        u = users[i]
        bu = state.get(u)
        if bu is None:
            bu = state[u] = BayesUser(k, tau2)
        sl = slice(s0, s0 + n)
        gi, Zi = g[sl], Zall[sl]
        out[sl] = bu.scores(gi, Zi)
        if bu.n in snapshots:
            snaps.setdefault(bu.n, {})[u] = bu.mu.copy()
        ci = int(np.flatnonzero(frame.y[sl])[0])
        bu.update(g_update[sl], Zi, ci, steps)
    final = {u: b.mu.copy() for u, b in state.items()}
    return out, state, snaps, final


def truth_direction(truth_users, users):
    """참 취향의 방향 × 중요도(평가 전용). 목표가 0.5보다 크면 +w, 작으면 −w. 소음은 −noise_aversion."""
    t = truth_users.set_index("user_id").loc[users]
    D = np.zeros((len(users), len(SEAT_KEYS)))
    for j, k in enumerate(SEAT_KEYS):
        if k == "noise_index":
            D[:, j] = -0.5 * t["noise_aversion"].values
        elif f"w_{k}" in t:
            D[:, j] = t[f"w_{k}"].values * np.sign(t[f"t_{k}"].values - 0.5)
    return D


def recovery(snaps, final, truth_users):
    """착석 횟수별로 추정 μ와 참 취향의 상관(특징별 스피어만, 사용자 횡단)."""
    from scipy.stats import spearmanr

    res = {}
    for n, d in sorted(
        list(snaps.items()) + [("final", final)],
        key=lambda x: (isinstance(x[0], str), x[0]),
    ):
        users = sorted(d)
        if len(users) < 30:
            continue
        M = np.array([d[u] for u in users])
        D = truth_direction(truth_users, users)
        row = {}
        for j, k in enumerate(SEAT_KEYS):
            if np.std(D[:, j]) == 0 or np.std(M[:, j]) == 0:
                continue
            row[k] = float(spearmanr(M[:, j], D[:, j]).statistic)
        vals = [v for v in row.values() if np.isfinite(v)]
        row["_users"] = len(users)
        row["_mean_rho"] = float(np.mean(vals)) if vals else None
        res[str(n)] = row
    return res


def weekly_retrain(frames_all, meta_all, make_model, cfg, eval_users_mask, log=print):
    """전역 층 연속 학습 실험. 주 단위로 (지난주까지 학습 사용자 요청으로 웜스타트 재학습) → (이번 주 테스트 사용자 평가).
    정적 모델(첫 2주로 1회 학습)과 비교한다."""
    import pandas as pd
    from .evaluate import per_session

    days = pd.to_datetime(meta_all.occurred_at.str[:10])
    d0 = days.min()
    week = ((days - d0).dt.days // cfg["online"]["retrain_every_days"]).values
    tr_mask = (meta_all.split.values == "train") & ~meta_all.room_holdout.values
    rows = []
    static, model, beta = None, None, None
    for w in range(2, int(week.max()) + 1):
        fit_frame = frames_all.subset(tr_mask & (week < w))
        model = make_model()
        model.fit(fit_frame, warm_start=beta)
        beta = model.beta
        if static is None:
            static = model
        ev = frames_all.subset(eval_users_mask & (week == w))
        if len(ev.starts) == 0:
            continue
        for name, m in (("static_first_2w", static), ("weekly_warm_start", model)):
            ps = per_session(ev, m.score(ev), truth=False)
            rows.append(
                {
                    "week": w,
                    "model": name,
                    "n": len(ev.starts),
                    "train_sessions": len(fit_frame.starts),
                    "hit@1": ps["hit@1"].mean(),
                    "hit@5": ps["hit@5"].mean(),
                    "mrr": ps["mrr"].mean(),
                    "nit": model.opt["nit"],
                }
            )
        log(
            f"week {w}: train={len(fit_frame.starts)} eval={len(ev.starts)} nit={model.opt['nit']} "
            f"static={rows[-2]['mrr']:.4f} weekly={rows[-1]['mrr']:.4f}"
        )
    return rows


class OnlineState:
    """서비스용 상태 저장소: 사용자별 UserState + BayesUser. 선택 1건마다 observe()로 갱신."""

    def __init__(self, k=len(SEAT_KEYS), prior_var=1.0):
        self.k, self.prior_var = k, prior_var
        self.hist: dict[str, UserState] = {}
        self.bayes: dict[str, BayesUser] = {}

    def get(self, u):
        if u not in self.hist:
            self.hist[u] = UserState()
            self.bayes[u] = BayesUser(self.k, self.prior_var)
        return self.hist[u], self.bayes[u]

    def to_dict(self):
        return {
            "k": self.k,
            "prior_var": self.prior_var,
            "users": {
                u: {
                    "hist": self.hist[u].to_dict(),
                    "mu": self.bayes[u].mu.tolist(),
                    "P": self.bayes[u].P.tolist(),
                    "n": self.bayes[u].n,
                }
                for u in self.hist
            },
        }

    @classmethod
    def from_dict(cls, d):
        s = cls(d["k"], d["prior_var"])
        for u, v in d["users"].items():
            s.hist[u] = UserState.from_dict(v["hist"])
            b = BayesUser(s.k, s.prior_var)
            b.mu, b.P, b.n = np.array(v["mu"]), np.array(v["P"]), v["n"]
            s.bayes[u] = b
        return s
