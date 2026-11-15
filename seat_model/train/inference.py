"""9단계: 추론 모듈. 요청 스냅샷 → 상위 k개 좌석, 점수, 추천 이유 3개. 선택이 관측되면 observe()로 바로 학습에 반영.

    from seat_model.train.inference import SeatRecommender
    rec = SeatRecommender.load("seat_model/models/v1-20261115")
    out = rec.recommend(user, snapshot, k=5)       # user: {"user_id", "preference_profile"?, "requirements"?} — 취향 미입력 가능
    rec.observe(user["user_id"], snapshot, chosen_seat_id)   # 착석 기록 → 사용자 상태 즉시 갱신
    rec.inferred_profile(user["user_id"])          # 착석 기록으로 파악한 취향(서비스 preference_profile 형식)
    rec.save_state()                               # 상태 저장(models/<버전>/online_state.json)

snapshot 형식
  {"room_id", "time_slot": "morning|noon|evening", "requested_at": ISO 문자열,
   "conditions": {"season", "weather", "base_temperature_c"},
   "seats": [{"seat_id", "features": {12개}, "brightness_by_slot": {...}, "noise_index", "desk_type", "desk_group"}],
   "occupied_seat_ids": [...]}
필수 조건(노트북 규정 등)은 하드 필터: 조건 밖 좌석은 결과에 나오지 않는다(recommend 설명 참고).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .common import parse_ts
from .data import DESK_TYPES, FEATURE_COLS, SLOTS, Scenario, UserPrefs, candidate_block
from .models.clogit import CLogit
from .online import FEATURE_KO, SEATC_COLS, OnlineState, infer_profile
from .userstate import K, SEAT_KEYS

HIGH_LOW = {
    "laptopAllowed": ("노트북 가능", "노트북 불가"),
    "keyboardAllowed": ("키보드 가능", "키보드 불가"),
    "partition": ("칸막이", "개방형"),
    "outletAvailable": ("콘센트 있음", "콘센트 없음"),
    "windowProximity": ("창가", "안쪽 자리"),
    "toiletProximity": ("화장실 가까움", "화장실과 떨어짐"),
    "entranceProximity": ("출입구 가까움", "출입구와 떨어짐"),
    "brightness": ("밝음", "은은한 조명"),
    "airflow": ("바람 셈", "바람 약함"),
    "deskHeight": ("높은 책상", "낮은 책상"),
    "deskWidth": ("넓은 책상", "좁은 책상"),
    "viewQuality": ("전망 좋음", "전망 적음"),
    "noise_index": ("활기찬 구역", "조용함"),
}


def _feature_of(col):
    for k in SEAT_KEYS:
        if col.endswith("_" + k):
            return k
    if col.startswith("rt_"):
        return "_rt"
    if col.startswith("hist_"):
        return "_hist"
    return None


def scenario_from_snapshot(snap):
    seats = sorted(snap["seats"], key=lambda s: s["seat_id"])
    sc = Scenario()
    sc.seat_ids = [s["seat_id"] for s in seats]
    sc.index = {x: i for i, x in enumerate(sc.seat_ids)}
    sc.F = np.array(
        [
            [
                s["features"][k] if k != "noise_index" else s["noise_index"]
                for k in SEAT_KEYS
            ]
            for s in seats
        ],
        float,
    )
    sc.bright = np.array([[s["brightness_by_slot"][sl] for sl in SLOTS] for s in seats])
    sc.desk = np.array([DESK_TYPES.index(s["desk_type"]) for s in seats])
    sc.group = np.array([int(s["desk_group"]) for s in seats])
    _, inv, cnt = np.unique(sc.group, return_inverse=True, return_counts=True)
    sc.group_size = cnt[inv]
    sc.cond = snap["conditions"]
    sc.room_id = snap["room_id"]
    return sc


class SeatRecommender:
    def __init__(
        self, model: CLogit, z_sd, online: OnlineState, cfg_online, model_dir=None
    ):
        self.model, self.z_sd, self.online, self.cfg_online, self.model_dir = (
            model,
            np.asarray(z_sd),
            online,
            cfg_online,
            model_dir,
        )

    @classmethod
    def load(cls, model_dir, state_file="online_state.json"):
        d = Path(model_dir)
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        model = CLogit.from_state(json.loads((d / man["model_file"]).read_text()))
        sp = d / state_file
        online = (
            OnlineState.from_dict(json.loads(sp.read_text()))
            if sp.exists()
            else OnlineState(K, man["online"]["prior_var"])
        )
        return cls(model, man["z_sd"], online, man["online"], d)

    def save_state(self, path=None):
        p = Path(path or (self.model_dir / "online_state.json"))
        p.write_text(json.dumps(self.online.to_dict()), encoding="utf-8")
        return p

    def _block(self, user, snap):
        sc = scenario_from_snapshot(snap)
        occ = set(snap.get("occupied_seat_ids") or [])
        avail = np.array([i for i, x in enumerate(sc.seat_ids) if x not in occ])
        st, bu = self.online.get(user["user_id"])
        prefs = UserPrefs(
            {
                "preference_profile": user.get("preference_profile"),
                "requirements": user.get("requirements"),
                "kakaotalk_profile": user.get("kakaotalk_profile"),
            }
        )
        M, seat_ids, gkeys, Xc = candidate_block(
            sc,
            avail,
            snap["time_slot"],
            parse_ts(snap["requested_at"]),
            prefs,
            st,
            {},
            self.cfg_online.get("inf_shrinkage", 3.0),
        )
        return M, seat_ids, gkeys, Xc, st, bu

    def recommend(self, user, snap, k=5, bias=None):
        """bias: 사용자가 직접 정한 취향 중 모델 프로필 특징이 없는 항목(칸막이·소음·출입구 등)의 고정 가산(표준화 단위).
        학습되는 μ_u와 달리 착석 기록으로 바뀌지 않는다.

        필수 조건(노트북 규정 등)은 하드 필터다. 점수는 학습 때와 같이 빈자리 전체로 계산하고(ctx_n_avail·중심화 특징 유지),
        조건을 만족하지 않는 좌석은 결과에서 뺀다. 만족 좌석이 k개보다 적으면 그 수만큼만 반환하고,
        0개면 빈 목록과 reason="no_eligible_seat"를 반환한다. 동점은 seat_id 오름차순(안정 정렬)."""
        M, seat_ids, _, Xc, st, bu = self._block(user, snap)
        cols = self.model.cols
        Xm = M[:, [FEATURE_COLS.index(c) for c in cols]]
        g = self.model.score_matrix(Xm)
        Z = Xc / self.z_sd
        mu = bu.mu if bias is None else bu.mu + np.asarray(bias)
        s = g + Z @ mu
        req_ok = M[:, FEATURE_COLS.index("ctx_req_ok")]
        elig = np.flatnonzero(req_ok > 0)
        order = elig[np.argsort(-s[elig], kind="stable")][:k]
        base = {
            "user_id": user["user_id"],
            "history_sessions": st.n,
            "excluded_by_requirements": int(len(seat_ids) - len(elig)),
        }
        if len(elig) == 0:
            return {**base, "recommendations": [], "reason": "no_eligible_seat"}
        # 이유: (특징 기여 − 빈자리 평균 기여)를 좌석 특징 단위로 합산
        Xs = self.model._std(Xm.astype(np.float64))
        for c in ("exp_flag", "exp_rank", "exp_inv_rank"):
            if c in cols:
                Xs[:, cols.index(c)] = self.model._std(np.zeros((1, len(cols))))[
                    0, cols.index(c)
                ]
        contrib = (Xs - Xs.mean(0)) * self.model.beta
        out = []
        for i in order:
            agg = {}
            for j, c in enumerate(cols):
                f = _feature_of(c)
                if f:
                    agg[f] = agg.get(f, 0) + contrib[i, j]
            for j, kf in enumerate(SEAT_KEYS):
                agg[kf] = agg.get(kf, 0) + Z[i, j] * mu[j]
            reasons, items = [], []
            for f, v in sorted(agg.items(), key=lambda x: -x[1])[:3]:
                if v <= 0:
                    break
                if f == "_rt":
                    text, fid, code = "주변 자리 여유", None, "QUIET_NEIGHBORHOOD"
                elif f == "_hist":
                    text, fid, code = "자주 앉던 자리", None, "HISTORY_HABIT"
                else:
                    high = Xc[i, SEAT_KEYS.index(f)] > 0
                    text, fid, code = (
                        HIGH_LOW[f][0 if high else 1],
                        f,
                        f"FEATURE_{'HIGH' if high else 'LOW'}",
                    )
                reasons.append(text)
                items.append(
                    {
                        "code": code,
                        "featureId": fid,
                        "text": text,
                        "contribution": float(v),
                    }
                )
            out.append(
                {
                    "seat_id": seat_ids[i],
                    "score": float(s[i]),
                    "meets_requirements": bool(req_ok[i]),
                    "reasons": reasons,
                    "reason_items": items,
                    "reason_text": " · ".join(reasons),
                }
            )
        return {**base, "recommendations": out}

    def observe(self, user_id, snap, chosen_seat_id, user=None, exposed_rank=None):
        """착석 기록 1건을 바로 반영한다(전역 모델 재학습 없이 사용자 상태만 갱신).
        exposed_rank: 그 요청에서 실제로 보여준 추천 {seat_id: 순위}. 노출 효과를 빼고 개인 취향을 갱신한다."""
        user = user or {"user_id": user_id}
        M, seat_ids, gkeys, Xc, st, bu = self._block(user, snap)
        if chosen_seat_id not in seat_ids:
            raise ValueError("선택 좌석이 그 시각 빈자리 목록에 없다")
        ci = seat_ids.index(chosen_seat_id)
        er = np.array([(exposed_rank or {}).get(s, 0) for s in seat_ids], float)
        M[:, FEATURE_COLS.index("exp_flag")] = er > 0
        M[:, FEATURE_COLS.index("exp_rank")] = er
        M[:, FEATURE_COLS.index("exp_inv_rank")] = np.where(er > 0, (6 - er) / 5, 0)
        Xm = M[:, [FEATURE_COLS.index(c) for c in self.model.cols]]
        g = self.model.score_matrix(Xm, neutral_exp=False)
        bu.update(g, Xc / self.z_sd, ci)
        st.update(chosen_seat_id, gkeys[ci], Xc[ci])
        return {"user_id": user_id, "history_sessions": st.n}

    def inferred_profile(self, user_id):
        st, bu = self.online.get(user_id)
        return infer_profile(
            bu,
            self.z_sd,
            self.cfg_online["profile_min_sessions"],
            self.cfg_online["profile_min_z"],
        )


def snapshot_from_db(reader, session_id):
    """테스트·백엔드 연동 예시: DB 세션 1건 → snapshot(선택 결과는 넣지 않는다)."""
    s = next(iter(reader.find("synth_sessions", {"session_id": session_id})))
    sc = next(iter(reader.find("synth_scenarios", {"scenario_id": s["scenario_id"]})))
    seats = [
        {
            "seat_id": d["seat_id"],
            "features": d["features"],
            "brightness_by_slot": d["brightness_by_slot"],
            "noise_index": d["raw"]["noise_index"],
            "desk_type": d["raw"]["desk_type"],
            "desk_group": d["raw"]["desk_group"],
        }
        for d in reader.find("synth_seat_env", {"scenario_id": s["scenario_id"]})
    ]
    snap = {
        "room_id": s["room_id"],
        "time_slot": s["time_slot"],
        "requested_at": s["occurred_at"],
        "conditions": sc["conditions"],
        "seats": seats,
        "occupied_seat_ids": s["occupied_seat_ids"],
    }
    exposed = {e["seat_id"]: e["rank"] for e in (s.get("exposed") or [])}
    return snap, s["user_id"], s.get("chosen_seat_id"), exposed
