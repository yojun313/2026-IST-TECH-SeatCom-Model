"""정답(참 효용)과 선택 과정 모듈. 생성과 평가가 같은 함수를 쓴다(평가 단계에서만 import).

U_truth(λ) = (1 − λ)·U_base + λ·U_new
  U_base  synth-1.0 true_utility: Σ_k w_k·(1 − |t_k − x_k|) − 0.5·na·noise
  U_new   A1 형태 변경(가우시안 이상점)          Σ_k w_k·exp(−(x_k − t_k)² / (2σ_k²)) − 0.5·na·noise
          + A2 탈락 조건(비보상형)                penalty × [콘센트 필수·콘센트 없음 | 추위 탐·에어컨 근처 | 소음 회피·시끄러움]
          + A4 상호작용                           −c·|좌석온도 − 선호온도|/3 + 저녁×밝기 + 혼잡×소음 + 노트북×넓은 책상
          + C1 숨은 취향                          칸막이·출입구·책상 높이·긴 공용 테이블 기피
선택 과정(B1 고려집합, B3 습관)은 요청마다 확률 λ로 새 과정을 쓴다.
R(추천 신뢰도)은 rec_trust에 1 − λ_R·(1 − scale)을 곱해, 노출 목록(현재 서비스 규칙)보다 참 효용을 따라 고르게 한다.
노트북 사용자 × 노트북 금지 좌석은 모든 세트에서 선택 불가(−inf)로 synth-1.0과 같다.
"""

from __future__ import annotations

import math
import random

import numpy as np

SLOTS = ["morning", "noon", "evening"]
U_MODULES = {"A1", "A2", "A4", "C1"}
B_MODULES = {"B1", "B3"}
HIDDEN = ("partition", "entranceProximity", "deskHeight")


# ---------------------------------------------------------------- 사용자 확장 파라미터(latent.test_truth)
def draw_user_ext(user, seed, cfg_truth):
    """사용자별 새 정답 로직 파라미터. 모듈을 켜고 끄는 것과 무관하게 항상 같은 순서로 전부 뽑는다."""
    c = cfg_truth
    rng = random.Random(f"{seed}:truth_ext:{user['user_id']}")
    L = user["latent"]
    lo, hi = c["A1_ideal_point"]["sigma_range"]
    sigma = {
        fid: round(rng.uniform(lo, hi), 3)
        for fid in sorted(set(L["utility_weights"]) | set(HIDDEN))
    }
    a2 = c["A2_dealbreaker"]
    db_outlet = (
        L["preferences"]["outlet"] == "required"
        and rng.random() < a2["outlet_required_prob"]
    )
    db_cold_ac = (
        L["preferences"]["air_conditioning"] == "warm"
        and rng.random() < a2["cold_ac_prob"]
    )
    db_noise = L["noise_aversion"] >= a2["noise_aversion_min"]
    a4 = c["A4_interaction"]
    ac = L["preferences"]["air_conditioning"]
    pref_temp = a4["pref_temp_c"].get(ac if ac in ("cool", "warm") else "none")
    thermal = round(rng.uniform(*a4["thermal_coef_range"]), 3)
    evening_bright = round(rng.uniform(*a4["evening_bright_range"]), 3)
    c1 = c["C1_hidden"]
    hidden = {}
    for fid in HIDDEN:
        has = rng.random() < c1["attr_prob"]
        w = round(rng.uniform(*c1["weight_range"]), 3)
        if fid == "partition":
            t = 1.0 if rng.random() < c1["partition_target_one_prob"] else 0.0
        elif fid == "entranceProximity":
            t = 1.0 if rng.random() < c1["entrance_target_one_prob"] else 0.0
        else:
            t = c1["desk_height_target"]
        if has:
            hidden[fid] = {"target": t, "weight": w}
    dislike_shared = rng.random() < c1["shared_table_dislike_prob"]
    scan_n = rng.randint(*c["B1_consideration"]["scan_n_range"])
    habit = round(rng.uniform(*c["B3_habit"]["habit_range"]), 3)
    return {
        "sigma": sigma,
        "dealbreaker": {"outlet": db_outlet, "cold_ac": db_cold_ac, "noise": db_noise},
        "pref_temp_c": pref_temp,
        "thermal_coef": thermal,
        "evening_bright": evening_bright,
        "laptop": L["preferences"]["laptop_usage"] == "yes",
        "hidden": hidden,
        "dislike_shared_table": dislike_shared,
        "scan_n": scan_n,
        "habit": habit,
    }


# ---------------------------------------------------------------- 열람실(시나리오) 배열
class ScenArrays:
    """한 시나리오의 좌석 배열. seat_env 레코드 순서를 그대로 쓴다."""

    def __init__(self, seats):
        self.seat_ids = [s["seat_id"] for s in seats]
        self.index = {x: i for i, x in enumerate(self.seat_ids)}
        keys = list(seats[0]["features"])
        self.F = {k: np.array([s["features"][k] for s in seats], float) for k in keys}
        self.bright = {
            sl: np.array([s["brightness_by_slot"][sl] for s in seats], float)
            for sl in SLOTS
        }
        r = [s["raw"] for s in seats]
        self.noise = np.array([x["noise_index"] for x in r], float)
        self.temp = np.array([x["temperature_c"] for x in r], float)
        self.dist_ac = np.array([x["dist_ac_m"] for x in r], float)
        self.dist_ent = np.array([x["dist_entrance_walk_m"] for x in r], float)
        self.group = np.array([x["desk_group"] for x in r])
        self.shared = np.array([x["desk_type"] == "long_shared_table" for x in r])

    def feat(self, fid, slot, idx):
        return self.bright[slot][idx] if fid == "brightness" else self.F[fid][idx]


def u_base(user, sa: ScenArrays, idx, slot):
    """synth-1.0 true_utility와 같은 값(벡터). 노트북 금지는 −inf."""
    L = user["latent"]
    u = np.zeros(len(idx))
    for fid, tw in L["utility_weights"].items():
        u += tw["weight"] * (1 - np.abs(tw["target"] - sa.feat(fid, slot, idx)))
    u -= 0.5 * L["noise_aversion"] * sa.noise[idx]
    return np.where(banned(user, sa, idx), -np.inf, u)


def banned(user, sa, idx):
    return (user["latent"]["preferences"]["laptop_usage"] == "yes") & (
        sa.F["laptopAllowed"][idx] == 0
    )


def u_new(user, sa: ScenArrays, idx, slot, occ, modules, cfg_truth):
    L, X = user["latent"], user["latent"]["test_truth"]
    c = cfg_truth
    u = np.zeros(len(idx))
    for fid, tw in L["utility_weights"].items():  # A1 형태 변경(항상 포함)
        s = X["sigma"][fid]
        u += tw["weight"] * np.exp(
            -((sa.feat(fid, slot, idx) - tw["target"]) ** 2) / (2 * s * s)
        )
    u -= 0.5 * L["noise_aversion"] * sa.noise[idx]
    if "A2" in modules:
        a2, db = c["A2_dealbreaker"], X["dealbreaker"]
        bad = np.zeros(len(idx), bool)
        if db["outlet"]:
            bad |= sa.F["outletAvailable"][idx] == 0
        if db["cold_ac"]:
            bad |= sa.dist_ac[idx] <= a2["cold_ac_dist_m"]
        if db["noise"]:
            bad |= sa.noise[idx] > a2["noise_index_max"]
        u += a2["penalty"] * bad
    if "A4" in modules:
        a4 = c["A4_interaction"]
        u -= X["thermal_coef"] * np.abs(sa.temp[idx] - X["pref_temp_c"]) / 3.0
        if slot == "evening":
            u += X["evening_bright"] * sa.bright[slot][idx]
        u -= (
            a4["crowd_noise_coef"]
            * max(0.0, occ - 0.5)
            * L["noise_aversion"]
            * sa.noise[idx]
        )
        if X["laptop"]:
            u += a4["laptop_desk_coef"] * sa.F["deskWidth"][idx]
    if "C1" in modules:
        for fid, tw in X["hidden"].items():
            s = X["sigma"][fid]
            u += tw["weight"] * np.exp(
                -((sa.feat(fid, slot, idx) - tw["target"]) ** 2) / (2 * s * s)
            )
        if X["dislike_shared_table"]:
            u += c["C1_hidden"]["shared_table_penalty"] * sa.shared[idx]
    return u


def u_truth(user, sa, idx, slot, occ, lam, modules, cfg_truth):
    """평가용 참 효용(벡터). λ = 0 이거나 효용 모듈이 없으면 U_base 그대로."""
    ub = u_base(user, sa, idx, slot)
    if lam == 0 or not (set(modules) & U_MODULES):
        return ub
    un = u_new(user, sa, idx, slot, occ, set(modules), cfg_truth)
    bad = np.isinf(ub)
    return np.where(bad, -np.inf, (1 - lam) * np.where(bad, 0.0, ub) + lam * un)


def trust_multiplier(spec, cfg_truth):
    """R 모듈: 추천 신뢰도 배율 = 1 − λ_R·(1 − scale). λ_R은 세트의 trust_lambda(없으면 lambda)."""
    if "R" not in spec["modules"]:
        return 1.0
    lam_r = float(spec.get("trust_lambda", spec["lambda"]))
    return 1.0 - lam_r * (1.0 - cfg_truth["R_rec_trust"]["scale"])


# ---------------------------------------------------------------- 선택 과정
def _mnl(rng, cand, util, beta):
    m = max(util)
    return rng.choices(cand, [math.exp(beta * (x - m)) for x in util])[0]


def choose(
    rng, user, sess, sa, avail_idx, u, lam, modules, last, cfg_truth, trust_mult=1.0
):
    """한 요청의 선택. 반환: (선택 좌석 id 또는 None, origin, process).
    기존 과정(synth-1.0과 같은 규칙): rec_trust 확률로 노출 좌석(+순위 가산) 중, 아니면 빈자리 전체 중 MNL.
    새 과정(확률 λ, B 모듈이 켜졌을 때): B3 습관 → 노출 신뢰 → B1 고려집합 순으로 적용.
    trust_mult: R 모듈의 추천 신뢰도 배율(두 과정 모두에 적용)."""
    L = user["latent"]
    ok = ~np.isinf(u)
    if not ok.any():
        return None, None, "none"
    ids = [sa.seat_ids[i] for i in avail_idx]
    rank = {e["seat_id"]: e["rank"] for e in sess["exposed"] or []}
    use_new = bool(set(modules) & B_MODULES) and rng.random() < lam
    X = L["test_truth"]
    util = {ids[j]: float(u[j]) for j in range(len(ids)) if ok[j]}
    if use_new and "B3" in modules and last is not None:
        last_seat, last_room, last_group = last
        if last_room == sess["room_id"]:
            if last_seat in util and rng.random() < X["habit"]:
                return last_seat, "habit", "new"
            bonus = cfg_truth["B3_habit"]["same_group_bonus"]
            for j, sid in enumerate(ids):
                if sid in util and sa.group[avail_idx[j]] == last_group:
                    util[sid] += bonus
    exp_ok = [s for s in rank if s in util]
    if exp_ok and rng.random() < L["rec_trust"] * trust_mult:
        pool = [(s, util[s] + 0.08 * (6 - rank[s])) for s in exp_ok]
        origin = "recommendation"
    else:
        if use_new and "B1" in modules:
            order = np.argsort(sa.dist_ent[avail_idx], kind="stable")
            near = [ids[j] for j in order if ids[j] in util][: X["scan_n"]]
            keep = list(dict.fromkeys(exp_ok + near))
            pool = [(s, util[s]) for s in keep]
        else:
            pool = list(util.items())
        origin = None
    chosen = _mnl(rng, [p[0] for p in pool], [p[1] for p in pool], L["rationality"])
    if origin is None:
        origin = "recommendation" if chosen in rank else "outsideRecommendations"
    return chosen, origin, "new" if use_new else "base"
