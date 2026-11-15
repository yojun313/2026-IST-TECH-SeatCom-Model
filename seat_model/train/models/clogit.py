"""4단계: 조건부 로짓(요청별 softmax 우도 + L2). numpy + scipy L-BFGS로 직접 구현.

V_si = x_si · β.  요청 안에서 상수인 열(pref_imp, ctx 등)은 softmax에서 사라지므로 쓰지 않는다.
관측 선호 효과는 match_(= importance · (1 − |target − x|))로, 착석 기록 취향은 inf_x_(= r_k · (x − mean))로 들어간다.
  β(seat_k)  = 전체 평균 선호(β_pop)
  β(match_k) = 관측 선호를 얼마나 믿는지(β_pers)
  β(inf_x_k) = 착석 기록으로 추정한 개인 취향을 얼마나 믿는지
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from ..data import DESK_COLS, EXP_COLS, MATCH_COLS, RT_COLS, SEAT_COLS
from ..userstate import INF_X_COLS

BASE = SEAT_COLS + DESK_COLS[1:] + RT_COLS + ["ctx_req_ok"]
HIST = ["hist_last_seat", "hist_seat_cnt", "hist_last_group", "hist_group_cnt"]
VARIANTS = {
    "a_full": BASE + MATCH_COLS[:-1] + HIST + INF_X_COLS + EXP_COLS,
    "b_no_exp": BASE + MATCH_COLS[:-1] + HIST + INF_X_COLS,
    "g_no_profile": BASE
    + HIST
    + INF_X_COLS
    + EXP_COLS,  # 취향 미입력: 관측 프로필 없이 착석 기록만
    "h_profile_only": BASE
    + MATCH_COLS[:-1]
    + EXP_COLS,  # 착석 기록 미사용(관측 프로필만)
    "i_population": BASE + EXP_COLS,  # 개인화 없음
}


class CLogit:
    def __init__(self, cols, l2=1.0, maxiter=300):
        self.cols, self.l2, self.maxiter = list(cols), l2, maxiter
        self.beta = None

    # 표준화는 학습 분할 통계로만
    def _std(self, X):
        return (X - self.mu) / self.sd

    def _nll(self, b, X, y, starts, seg):
        v = X @ b
        mx = np.maximum.reduceat(v, starts)
        e = np.exp(v - mx[seg])
        z = np.add.reduceat(e, starts)
        p = e / z[seg]
        nll = -(v[y] - mx - np.log(z)).sum() + 0.5 * self.l2 * b @ b
        g = X.T @ p - X[y].sum(0) + self.l2 * b
        return nll, g

    def fit(self, frame, warm_start=None):
        X = frame.X(self.cols).astype(np.float64)
        if warm_start is None or not hasattr(self, "mu"):
            self.mu = X.mean(0)
            self.sd = X.std(0) + 1e-6
        X = self._std(X)
        y = frame.y == 1
        b0 = warm_start if warm_start is not None else np.zeros(X.shape[1])
        r = minimize(
            self._nll,
            b0,
            args=(X, y, frame.starts, frame.seg),
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": self.maxiter},
        )
        self.beta = r.x
        self.opt = {
            "success": bool(r.success),
            "nit": int(r.nit),
            "nll": float(r.fun),
            "nll_per_session": float(r.fun / len(frame.starts)),
        }
        self._se(X, frame)
        return self

    def _se(self, X, frame):
        v = X @ self.beta
        mx = np.maximum.reduceat(v, frame.starts)
        e = np.exp(v - mx[frame.seg])
        p = e / np.add.reduceat(e, frame.starts)[frame.seg]
        A = (X * p[:, None]).T @ X
        G = np.add.reduceat(X * p[:, None], frame.starts)
        H = A - G.T @ G + self.l2 * np.eye(X.shape[1])
        try:
            self.se = np.sqrt(np.clip(np.diag(np.linalg.inv(H)), 0, None))
        except np.linalg.LinAlgError:
            self.se = np.full(X.shape[1], np.nan)

    def score(self, frame, neutral_exp=True):
        """neutral_exp: 노출 특징을 0으로 두고 점수(새 정책은 기존 노출이 없다고 보고 순위를 낸다)."""
        X = frame.X(self.cols).astype(np.float64)
        if neutral_exp:
            for c in EXP_COLS:
                if c in self.cols:
                    X[:, self.cols.index(c)] = 0
        return self._std(X) @ self.beta

    def score_matrix(self, X, neutral_exp=True):
        X = X.astype(np.float64).copy()
        if neutral_exp:
            for c in EXP_COLS:
                if c in self.cols:
                    X[:, self.cols.index(c)] = 0
        return self._std(X) @ self.beta

    def coef_table(self):
        return [
            {
                "feature": c,
                "beta_std": float(b),
                "se": float(s),
                "z": float(b / s) if s > 0 else None,
                "beta_raw": float(b / sd),
            }
            for c, b, s, sd in zip(self.cols, self.beta, self.se, self.sd)
        ]

    def state(self):
        return {
            "cols": self.cols,
            "beta": self.beta.tolist(),
            "mu": self.mu.tolist(),
            "sd": self.sd.tolist(),
            "l2": self.l2,
            "opt": self.opt,
        }

    @classmethod
    def from_state(cls, st):
        m = cls(st["cols"], st["l2"])
        m.beta, m.mu, m.sd = (
            np.array(st["beta"]),
            np.array(st["mu"]),
            np.array(st["sd"]),
        )
        return m
