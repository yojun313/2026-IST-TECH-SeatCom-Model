"""1단계: 학습 데이터 파이프라인 (v1, synth-1.0).

출력: seat_model/exports/train_v1/
  candidates.parquet  요청 × 빈자리 전체 1행(long format). 라벨 y_chosen만 포함
  sessions.parquet    요청 단위 메타(분할, 열람실 홀드아웃, 시각, 빈자리 수). 특징 아님
  splits.json         사용자 70/15/15(archetype 층화), 열람실 홀드아웃 4개, 시간 분할 기준
  data_report.json    규모·제외 건수

- 후보 = 그 시각 열람실 빈자리 전체(시나리오 좌석 − occupied_seat_ids). v1 negative_seat_ids(최대 20개)는 쓰지 않는다.
- 요청은 occurred_at 순서로 처리한다. 이력(hist_)·취향 추정(inf_)은 그 요청 **이전** 상태로 계산한 뒤 갱신한다.
- 참값·만족도·사후 정보는 FeatureReader 허용 목록 밖이라 읽을 수 없다.

    seat_model/.venv/bin/python -m seat_model.train.data
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from .common import FeatureReader, SplitReader, load_config, parse_ts, write_json
from .userstate import HIST_COLS, INF_R_COLS, INF_X_COLS, K, SEAT_KEYS, UserState

SLOTS = ["morning", "noon", "evening"]
DESK_TYPES = ["individual_carrel", "long_shared_table", "four_person_table", "wide_individual_desk",
              "window_counter", "standing_desk"]
WEATHERS = ["clear", "cloudy", "rain"]
SEASONS = ["spring_fall", "summer", "winter"]
# 관측 프로필(preference_profile)이 다루는 특징
PREF_FIDS = ["toiletProximity", "brightness", "airflow", "deskWidth", "laptopAllowed", "outletAvailable",
             "windowProximity", "viewQuality"]
KAKAO = ["restroom", "lighting", "air_conditioning", "desk", "laptop_usage", "outlet", "view"]
ITYPE = {"direct": 1.0, "contextual": 0.5, "unknown": 0.0}

SEAT_COLS = [f"seat_{k}" for k in SEAT_KEYS]
SEATC_COLS = [f"seatc_{k}" for k in SEAT_KEYS]
DESK_COLS = [f"seat_desk_{d}" for d in DESK_TYPES]
PREF_COLS = [f"pref_imp_{f}" for f in PREF_FIDS] + [f"pref_tgt_{f}" for f in PREF_FIDS] + \
            ["pref_n_known"] + [f"pref_itype_{f}" for f in KAKAO]
MATCH_COLS = [f"match_{f}" for f in PREF_FIDS] + ["match_score"]
CTX_COLS = [f"ctx_slot_{s}" for s in SLOTS] + ["ctx_weekend", "ctx_dow", "ctx_occ", "ctx_n_avail"] + \
           [f"ctx_weather_{w}" for w in WEATHERS] + [f"ctx_season_{s}" for s in SEASONS] + ["ctx_temp", "ctx_req_ok"]
RT_COLS = ["rt_group_size", "rt_group_occ", "rt_group_empty_ratio"]
EXP_COLS = ["exp_flag", "exp_rank", "exp_inv_rank"]
FEATURE_COLS = (SEAT_COLS + SEATC_COLS + DESK_COLS + PREF_COLS + MATCH_COLS + CTX_COLS + RT_COLS
                + HIST_COLS + INF_R_COLS + INF_X_COLS + ["inf_score"] + EXP_COLS)
GROUPS = ["seat", "seatc", "pref", "match", "ctx", "rt", "hist", "inf", "exp"]


def group_cols(groups, cols=FEATURE_COLS):
    """접두어 그룹으로 특징 열 선택. seat_desk_*는 seat 그룹."""
    out = []
    for c in cols:
        g = c.split("_", 1)[0]
        if g in groups:
            out.append(c)
    return out


# ---------------------------------------------------------------- 정적 테이블
class Scenario:
    __slots__ = ("seat_ids", "index", "F", "bright", "desk", "group", "group_size", "req_vals", "cond", "room_id")


def load_scenarios(rd: FeatureReader):
    cond = {d["scenario_id"]: d for d in rd.find("synth_scenarios")}
    rows = defaultdict(list)
    for d in rd.find("synth_seat_env"):
        rows[d["scenario_id"]].append(d)
    scen = {}
    for sid, lst in rows.items():
        lst.sort(key=lambda d: d["seat_id"])
        s = Scenario()
        s.seat_ids = [d["seat_id"] for d in lst]
        s.index = {x: i for i, x in enumerate(s.seat_ids)}
        n = len(lst)
        F = np.zeros((n, K))
        for i, d in enumerate(lst):
            f = d["features"]
            F[i] = [f[k] if k != "noise_index" else d["raw"]["noise_index"] for k in SEAT_KEYS]
        s.F = F
        s.bright = np.array([[d["brightness_by_slot"][sl] for sl in SLOTS] for d in lst])
        s.desk = np.array([DESK_TYPES.index(d["raw"]["desk_type"]) for d in lst])
        s.group = np.array([d["raw"]["desk_group"] for d in lst])
        _, inv, cnt = np.unique(s.group, return_inverse=True, return_counts=True)
        s.group_size = cnt[inv]
        s.req_vals = {k: F[:, j] for j, k in enumerate(SEAT_KEYS)}
        c = cond[sid]["conditions"]
        s.cond = c
        s.room_id = lst[0]["room_id"]
        scen[sid] = s
    return scen


class UserPrefs:
    """관측 프로필(카카오톡 → preference_profile) 벡터. 취향 미입력 사용자는 모두 0."""
    __slots__ = ("imp", "tgt", "itype", "reqs", "home")

    def __init__(self, doc=None, masked=False):
        self.imp = np.zeros(len(PREF_FIDS))
        self.tgt = np.zeros(len(PREF_FIDS))
        self.itype = np.zeros(len(KAKAO))
        self.reqs = []
        self.home = None
        if doc is None:
            return
        self.home = doc.get("home_library_id")
        self.reqs = [(r["featureId"], r["value"]) for r in doc.get("requirements") or []]
        if masked:
            return
        for p in (doc.get("preference_profile") or {}).get("preferences") or []:
            if p["featureId"] in PREF_FIDS:
                j = PREF_FIDS.index(p["featureId"])
                self.imp[j], self.tgt[j] = p["importance"], p["target"]
        kp = ((doc.get("kakaotalk_profile") or {}).get("preferences")) or {}
        for j, f in enumerate(KAKAO):
            self.itype[j] = ITYPE.get(((kp.get(f) or {}).get("inference_type")) or "unknown", 0.0)


def candidate_block(sc: Scenario, avail_idx, slot, ts, prefs: UserPrefs, state: UserState, exposed_rank, m0):
    """한 요청의 후보 특징 행렬(n × FEATURE_COLS). 서비스 추론도 이 함수를 쓴다."""
    si = SLOTS.index(slot)
    X = sc.F[avail_idx].copy()
    X[:, SEAT_KEYS.index("brightness")] = sc.bright[avail_idx, si]
    n = len(avail_idx)
    mu = X.mean(0)
    Xc = X - mu
    desk = np.zeros((n, len(DESK_TYPES)))
    desk[np.arange(n), sc.desk[avail_idx]] = 1
    # 관측 프로필
    pref = np.concatenate([prefs.imp, prefs.tgt, [np.count_nonzero(prefs.imp)], prefs.itype])
    cols_p = [SEAT_KEYS.index(f) for f in PREF_FIDS]
    match = prefs.imp * (1 - np.abs(prefs.tgt - X[:, cols_p]))
    W = prefs.imp.sum()
    mscore = match.sum(1) / W if W > 0 else np.zeros(n)
    # 필수 조건
    req_ok = np.ones(n)
    for fid, val in prefs.reqs:
        req_ok *= (X[:, SEAT_KEYS.index(fid)] == val)
    # 상황
    c = sc.cond
    dow = ts.weekday()
    occ = 1 - n / len(sc.seat_ids)
    ctx = np.zeros(len(CTX_COLS))
    ctx[si] = 1
    ctx[3], ctx[4], ctx[5], ctx[6] = dow >= 5, dow, occ, n
    ctx[7 + WEATHERS.index(c["weather"])] = 1
    ctx[10 + SEASONS.index(c["season"])] = 1
    ctx[13] = c["base_temperature_c"]
    # 실시간: 같은 책상 묶음 점유
    avail_mask = np.zeros(len(sc.seat_ids), bool)
    avail_mask[avail_idx] = True
    g = sc.group[avail_idx]
    gsize = sc.group_size[avail_idx]
    empty_in_group = np.bincount(sc.group[avail_mask], minlength=sc.group.max() + 1)[g]
    rt = np.stack([gsize, gsize - empty_in_group, (empty_in_group - 1) / np.maximum(gsize - 1, 1)], 1)
    # 이력·취향 추정 (요청 이전 상태)
    seat_ids = [sc.seat_ids[i] for i in avail_idx]
    gkeys = [(sc.room_id, int(x)) for x in g]
    hist, inf_r, inf_x = state.features(seat_ids, gkeys, Xc, m0)
    # 노출
    er = np.array([exposed_rank.get(s, 0) for s in seat_ids], float)
    exp = np.stack([er > 0, er, np.where(er > 0, (6 - er) / 5, 0)], 1)
    blocks = [X, Xc, desk, np.broadcast_to(pref, (n, len(pref))), match, mscore[:, None],
              np.broadcast_to(ctx, (n, len(ctx))), rt, hist, inf_r, inf_x, inf_x.sum(1, keepdims=True), exp]
    M = np.concatenate(blocks, 1).astype(np.float32)
    M[:, FEATURE_COLS.index("ctx_req_ok")] = req_ok
    return M, seat_ids, gkeys, Xc


# ---------------------------------------------------------------- 분할
def make_splits(cfg, users_arch, rooms_lib, rng):
    by_arch = defaultdict(list)
    for u, a in sorted(users_arch.items()):
        by_arch[a].append(u)
    split = {}
    r = cfg["split"]["user_ratios"]
    for a, lst in sorted(by_arch.items()):
        lst = list(rng.permutation(lst))
        n1, n2 = round(len(lst) * r[0]), round(len(lst) * (r[0] + r[1]))
        for i, u in enumerate(lst):
            split[u] = "train" if i < n1 else ("val" if i < n2 else "test")
    libs = sorted(set(rooms_lib.values()))
    pick_libs = list(rng.choice(libs, cfg["split"]["room_holdout_count"], replace=False))
    holdout = []
    for lib in sorted(pick_libs):
        rooms = sorted(r for r, l in rooms_lib.items() if l == lib)
        holdout.append(str(rng.choice(rooms)))
    return split, sorted(holdout)


# ---------------------------------------------------------------- 메인
def build(cfg, max_sessions=0, out=None):
    t0 = time.time()
    rd = FeatureReader(cfg["db"])
    out = Path(out or cfg["paths"]["exports"])
    out.mkdir(parents=True, exist_ok=True)
    m0 = cfg["data"]["inf_shrinkage"]
    rng = np.random.default_rng(cfg["seed"])

    scen = load_scenarios(rd)
    users = {d["user_id"]: d for d in rd.find("synth_users")}
    arch = {d["user_id"]: d["archetype"] for d in SplitReader(cfg["db"]).find("synth_users")}
    rooms_lib = {}
    for d in rd.find("synth_scenarios", fields=["room_id", "library_id"]):
        rooms_lib[d["room_id"]] = d["library_id"]
    split, holdout = make_splits(cfg, arch, rooms_lib, rng)
    tsplit = cfg["split"]["time_split_date"]

    sess = list(rd.find("synth_sessions"))
    for s in sess:
        s["_ts"] = parse_ts(s["occurred_at"])
    sess.sort(key=lambda s: (s["_ts"], s["session_id"]))
    if max_sessions:
        keep = set(rng.choice(len(sess), max_sessions, replace=False).tolist())
        sess = [s for i, s in enumerate(sess) if i in keep]
    ids = [s["session_id"] for s in sess]
    assert len(ids) == len(set(ids)), "중복 session_id"

    prefs = {u: UserPrefs(d) for u, d in users.items()}
    states = defaultdict(UserState)
    excl = defaultdict(int)
    mats, meta_rows, keys = [], [], []
    for s in sess:
        sc = scen[s["scenario_id"]]
        occ = set(s["occupied_seat_ids"] or [])
        avail_idx = np.array([i for i, x in enumerate(sc.seat_ids) if x not in occ])
        chosen = s.get("chosen_seat_id")
        if chosen is None:
            excl["no_choice"] += 1
            continue
        if len(avail_idx) < cfg["data"]["min_available"]:
            excl["avail_le_1"] += 1
            # 상태는 갱신(서비스에서도 착석 기록은 남는다)
        u = s["user_id"]
        er = {e["seat_id"]: e["rank"] for e in (s.get("exposed") or [])}
        M, seat_ids, gkeys, Xc = candidate_block(sc, avail_idx, s["time_slot"], s["_ts"], prefs[u], states[u], er, m0)
        if chosen not in seat_ids:
            excl["chosen_not_available"] += 1
            continue
        ci = seat_ids.index(chosen)
        if len(avail_idx) >= cfg["data"]["min_available"]:
            y = np.zeros(len(seat_ids), np.int8)
            y[ci] = 1
            mats.append(M)
            keys.append(pd.DataFrame({"session_id": s["session_id"], "seat_id": seat_ids, "y_chosen": y}))
            meta_rows.append({
                "session_id": s["session_id"], "user_id": u, "room_id": s["room_id"], "library_id": s["library_id"],
                "scenario_id": s["scenario_id"], "occurred_at": s["occurred_at"], "time_slot": s["time_slot"],
                "split": split[u], "room_holdout": s["room_id"] in holdout,
                "before_time_split": s["occurred_at"][:10] < tsplit,
                "n_avail": len(seat_ids), "n_eligible": int(M[:, FEATURE_COLS.index("ctx_req_ok")].sum()),
                "hist_n": states[u].n, "has_profile": bool(prefs[u].imp.sum() > 0),
                "n_unknown": int(7 - np.count_nonzero(prefs[u].itype)), "laptop_req": bool(prefs[u].reqs),
                "exposed_any": bool(er), "chosen_exposed": chosen in er,
            })
        states[u].update(chosen, gkeys[ci], Xc[ci])

    X = np.concatenate(mats)
    df = pd.concat(keys, ignore_index=True)
    feat = pd.DataFrame(X, columns=FEATURE_COLS)
    cand = pd.concat([df, feat], axis=1)
    meta = pd.DataFrame(meta_rows)
    cand.to_parquet(out / "candidates.parquet", index=False, compression="zstd")
    meta.to_parquet(out / "sessions.parquet", index=False)
    write_json(out / "splits.json", {"user_split": split, "room_holdout": holdout, "time_split_date": tsplit,
                                     "seed": cfg["seed"], "stratified_by": "archetype"})
    rep = {
        "sessions_total": len(sess), "sessions_used": len(meta), "rows": len(cand), "features": len(FEATURE_COLS),
        "excluded": dict(excl),
        "users": {k: int(v) for k, v in pd.Series(split).value_counts().items()},
        "room_holdout": holdout,
        "sessions_by_split": meta.groupby(["split", "room_holdout"]).size().rename("n").reset_index().to_dict("records"),
        "n_avail_quantiles": {str(q): float(meta.n_avail.quantile(q)) for q in (0, .1, .25, .5, .75, .9, .99, 1)},
        "chosen_in_exposed_rate": float(meta.chosen_exposed.mean()),
        "exposed_any_rate": float(meta.exposed_any.mean()),
        "chosen_violates_requirements": int(cand.loc[cand.y_chosen == 1, "ctx_req_ok"].eq(0).sum()),
        "seconds": round(time.time() - t0, 1),
    }
    write_json(out / "data_report.json", rep)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--max-sessions", type=int, default=0)
    ap.add_argument("--out")
    a = ap.parse_args()
    rep = build(load_config(a.config), a.max_sessions, a.out)
    print({k: rep[k] for k in ("sessions_total", "sessions_used", "rows", "excluded", "seconds")})


if __name__ == "__main__":
    main()
