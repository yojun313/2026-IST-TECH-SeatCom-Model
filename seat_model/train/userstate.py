"""사용자별 온라인 상태: 착석 기록이 쌓일 때마다 바로 갱신되는 이력·취향 추정 특징.

학습 특징(data.py)과 서비스 추론(inference.py)이 같은 코드를 쓴다. 그래서 학습 때 본 특징과
서비스 때 보는 특징이 같은 방식으로 계산된다.

- hist_*: 직전 좌석·책상묶음과 같은지, 과거 선택 횟수
- inf_*: "드러난 선호(revealed preference)". 선택 좌석 특징이 그때 빈자리 평균보다 얼마나 높았는지를
  누적한 값. 취향 입력(카카오톡 프로필)이 없어도 착석 기록만으로 취향 방향을 추정한다.
      r_k = Σ_t (x_chosen,k − mean_avail,k) / (n + m0)        (m0: 수축 강도, 기록이 적으면 0 쪽으로)
  좌석 점수용 상호작용: inf_x_k = r_k · (x_seat,k − mean_avail,k)
"""

from __future__ import annotations

import numpy as np

SEAT_KEYS = [
    "laptopAllowed",
    "keyboardAllowed",
    "partition",
    "outletAvailable",
    "windowProximity",
    "toiletProximity",
    "entranceProximity",
    "brightness",
    "airflow",
    "deskHeight",
    "deskWidth",
    "viewQuality",
    "noise_index",
]
K = len(SEAT_KEYS)


class UserState:
    __slots__ = (
        "n",
        "contrast_sum",
        "contrast_sq",
        "last_seat",
        "last_group",
        "seat_counts",
        "group_counts",
    )

    def __init__(self):
        self.n = 0
        self.contrast_sum = np.zeros(K)
        self.contrast_sq = np.zeros(K)
        self.last_seat = None
        self.last_group = None
        self.seat_counts: dict = {}
        self.group_counts: dict = {}

    # ---- 특징 (현재 요청 이전 상태로 계산)
    def revealed(self, m0: float) -> np.ndarray:
        return self.contrast_sum / (self.n + m0)

    def features(self, seat_ids, group_keys, Xc, m0):
        """seat_ids·group_keys: 후보 좌석 목록, Xc: (n, K) 빈자리 평균을 뺀 좌석 특징."""
        n = len(seat_ids)
        r = self.revealed(m0)
        hist = np.zeros((n, 5), dtype=np.float32)
        hist[:, 0] = self.n
        hist[:, 1] = [s == self.last_seat for s in seat_ids]
        hist[:, 2] = [self.seat_counts.get(s, 0) for s in seat_ids]
        hist[:, 3] = [g == self.last_group for g in group_keys]
        hist[:, 4] = [self.group_counts.get(g, 0) for g in group_keys]
        inf_x = (Xc * r).astype(np.float32)
        return hist, np.broadcast_to(r.astype(np.float32), (n, K)), inf_x

    # ---- 갱신 (선택이 관측된 뒤)
    def update(self, seat_id, group_key, xc_chosen):
        self.n += 1
        self.contrast_sum += xc_chosen
        self.contrast_sq += xc_chosen**2
        self.last_seat, self.last_group = seat_id, group_key
        self.seat_counts[seat_id] = self.seat_counts.get(seat_id, 0) + 1
        self.group_counts[group_key] = self.group_counts.get(group_key, 0) + 1

    # ---- 직렬화
    def to_dict(self):
        enc = lambda g: (
            None if g is None else f"{g[0]}|{g[1]}"
        )  # (room_id, desk_group) → 문자열
        return {
            "n": self.n,
            "contrast_sum": self.contrast_sum.tolist(),
            "contrast_sq": self.contrast_sq.tolist(),
            "last_seat": self.last_seat,
            "last_group": enc(self.last_group),
            "seat_counts": self.seat_counts,
            "group_counts": {enc(k): v for k, v in self.group_counts.items()},
        }

    @classmethod
    def from_dict(cls, d):
        s = cls()
        s.n = d["n"]
        s.contrast_sum = np.array(d["contrast_sum"])
        s.contrast_sq = np.array(d["contrast_sq"])

        def dec(x):
            if x is None:
                return None
            r, g = x.rsplit("|", 1)
            return (r, int(g))

        s.last_seat, s.last_group = d["last_seat"], dec(d["last_group"])
        s.seat_counts = dict(d["seat_counts"])
        s.group_counts = {dec(k): v for k, v in d["group_counts"].items()}
        return s


HIST_COLS = [
    "hist_n",
    "hist_last_seat",
    "hist_seat_cnt",
    "hist_last_group",
    "hist_group_cnt",
]
INF_R_COLS = [f"inf_r_{k}" for k in SEAT_KEYS]
INF_X_COLS = [f"inf_x_{k}" for k in SEAT_KEYS]
