"""JSON API. 모든 응답은 백엔드와 같은 SQLite 카탈로그에서 읽는다(읽기 전용)."""

import bisect
import json
import re
import threading
import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Query

from ..db import clean, get_db, query

router = APIRouter(prefix="/api")

REAL = [
    "libraries",
    "rooms",
    "maps",
    "seats",
    "facilities",
    "room_features",
    "sources",
    "walkway_nodes",
    "build_reports",
    "meta",
]
SYNTH = [
    "synth_scenarios",
    "synth_seat_env",
    "synth_users",
    "synth_sessions",
    "synth_events",
]
COLLECTIONS = REAL + SYNTH
FEATURES = [
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
]
_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def must(doc, what):
    if not doc:
        raise HTTPException(404, f"{what} 없음")
    return doc


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview():
    def build():
        db = get_db()
        counts = {c: db[c].estimated_document_count() for c in COLLECTIONS}
        libs = list(
            db.libraries.find(
                {},
                {
                    "_id": 0,
                    "library_id": 1,
                    "name": 1,
                    "address": 1,
                    "dataset_status": 1,
                },
            )
        )
        seat_n = {r["_id"]: r["n"] for r in db.seats.group("library_id", n=("count",))}
        room_n = {r["_id"]: r["n"] for r in db.rooms.group("library_id", n=("count",))}
        fac_n = {
            r["_id"]: r["n"] for r in db.facilities.group("library_id", n=("count",))
        }
        sc_n = {
            r["_id"]: r["n"]
            for r in db.synth_scenarios.group("library_id", n=("count",))
        }
        ss_n = {
            r["_id"]: r["n"]
            for r in db.synth_sessions.group("library_id", n=("count",))
        }
        us_n = {
            r["_id"]: r["n"]
            for r in db.synth_users.group("home_library_id", n=("count",))
        }
        for l in libs:
            lid = l["library_id"]
            l.update(
                seats=seat_n.get(lid, 0),
                rooms=room_n.get(lid, 0),
                facilities=fac_n.get(lid, 0),
                scenarios=sc_n.get(lid, 0),
                sessions=ss_n.get(lid, 0),
                home_users=us_n.get(lid, 0),
            )
        libs.sort(
            key=lambda l: (
                l["dataset_status"] != "active",
                -l["seats"],
                l["library_id"],
            )
        )
        manifest = db.meta.find_one({"_id": "synthetic_manifest"}) or {}
        return {"counts": counts, "libraries": libs, "manifest": clean(manifest)}

    return cached("overview", 86400, build)


# ------------------------------------------------------------------ libraries (real)
@router.get("/libraries/{lid}")
def library(lid: str):
    db = get_db()
    lib = must(db.libraries.find_one({"library_id": lid}), "도서관")
    q = {"library_id": lid}
    rooms = list(db.rooms.find(q))
    maps = list(db.maps.find(q))
    seats = list(db.seats.find(q, {"_id": 0}))
    seat_by_room = Counter(s["room_id"] for s in seats)
    for r in rooms:
        r["seat_count"] = seat_by_room.get(r["room_id"], 0)
    groups = {}
    for sc in db.synth_scenarios.find(q).sort("scenario_index", 1):
        g = groups.setdefault(
            (sc["map_id"], sc["room_id"]),
            {
                "_id": {"map_id": sc["map_id"], "room_id": sc["room_id"]},
                "room_name": sc.get("room_name"),
                "seat_count": sc.get("seat_count"),
                "scenarios": [],
            },
        )
        g["scenarios"].append(
            {
                "scenario_id": sc.get("scenario_id"),
                "index": sc.get("scenario_index"),
                "season": (sc.get("conditions") or {}).get("season"),
                "weather": (sc.get("conditions") or {}).get("weather"),
                "counts": sc.get("counts"),
            }
        )
    units = sorted(groups.values(), key=lambda u: u["_id"]["room_id"])
    return clean(
        {
            "library": lib,
            "rooms": rooms,
            "maps": maps,
            "seats": seats,
            "facilities": list(db.facilities.find(q)),
            "room_features": list(db.room_features.find(q)),
            "sources": list(db.sources.find(q)),
            "walkway_nodes": list(db.walkway_nodes.find(q)),
            "build_report": db.build_reports.find_one({"library_id": lid}),
            "units": [
                {
                    "map_id": u["_id"]["map_id"],
                    "room_id": u["_id"]["room_id"],
                    "room_name": u["room_name"],
                    "seat_count": u["seat_count"],
                    "scenarios": u["scenarios"],
                }
                for u in units
            ],
        }
    )


# ------------------------------------------------------------------ scenario (synthetic environment)
@router.get("/scenarios/{sid}")
def scenario(sid: str):
    db = get_db()
    sc = must(db.synth_scenarios.find_one({"_id": sid}), "시나리오")
    env = list(db.synth_seat_env.find({"scenario_id": sid}, {"scenario_id": 0}))
    real = {
        s["seat_id"]: s
        for s in db.seats.find(
            {"seat_id": {"$in": [e["seat_id"] for e in env]}},
            {
                "_id": 0,
                "seat_id": 1,
                "bbox": 1,
                "seat_label": 1,
                "external_seat_id": 1,
                "verification": 1,
            },
        )
    }
    for e in env:
        r = real.get(e["seat_id"], {})
        e.update(
            bbox=r.get("bbox"),
            external_seat_id=r.get("external_seat_id"),
            real_verification=r.get("verification"),
        )
    # 이 시나리오의 세션 기반 좌석별 선택·노출 빈도
    chosen = Counter()
    exposed = Counter()
    sats = {}
    for s in db.synth_sessions.find(
        {"scenario_id": sid},
        {"chosen_seat_id": 1, "exposed.seat_id": 1, "satisfaction": 1},
    ):
        if s.get("chosen_seat_id"):
            chosen[s["chosen_seat_id"]] += 1
            if s.get("satisfaction") is not None:
                sats.setdefault(s["chosen_seat_id"], []).append(s["satisfaction"])
        for x in s.get("exposed", []):
            exposed[x["seat_id"]] += 1
    for e in env:
        rs = sats.get(e["seat_id"], [])
        e["usage"] = {
            "chosen": chosen.get(e["seat_id"], 0),
            "exposed": exposed.get(e["seat_id"], 0),
            "avg_satisfaction": round(sum(rs) / len(rs), 3) if rs else None,
        }
    real_fac = list(db.facilities.find({"map_id": sc["map_id"]}))
    siblings = list(
        db.synth_scenarios.find(
            {
                "library_id": sc["library_id"],
                "room_id": sc["room_id"],
                "map_id": sc["map_id"],
            },
            {"_id": 1, "scenario_index": 1},
        ).sort("scenario_index", 1)
    )
    return clean(
        {
            "scenario": sc,
            "seats": env,
            "real_facilities": real_fac,
            "session_count": sum(chosen.values()),
            "siblings": [s["_id"] for s in siblings],
        }
    )


# ------------------------------------------------------------------ users
def sat_bin(v):
    """만족도(0~1) → 0.1 폭 구간 하한 문자열. 1.0은 0.9 구간."""
    return f"{min(int(v * 10), 9) / 10:.1f}"


@router.get("/users")
def users(
    archetype: str | None = None,
    home: str | None = None,
    q: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    db = get_db()
    f = {}
    if archetype:
        f["archetype"] = archetype
    if home:
        f["home_library_id"] = home
    if q:
        f["user_id"] = {"$regex": re.escape(q)}
    total = db.synth_users.count_documents(f)
    rows = list(
        db.synth_users.find(f, {"kakaotalk_profile.evidence": 0})
        .sort("_id", 1)
        .skip(skip)
        .limit(limit)
    )
    sess = {
        r["_id"]: r
        for r in db.synth_sessions.group(
            "user_id",
            {"user_id": {"$in": [r["_id"] for r in rows]}},
            n=("count",),
            avg_satisfaction=("avg", "satisfaction"),
        )
    }
    for r in rows:
        s = sess.get(r["_id"], {})
        r["session_count"] = s.get("n", 0)
        r["avg_satisfaction"] = (
            round(s["avg_satisfaction"], 3)
            if s.get("avg_satisfaction") is not None
            else None
        )
    return clean({"total": total, "items": rows})


@router.get("/users/{uid}")
def user(uid: str):
    db = get_db()
    u = must(db.synth_users.find_one({"_id": uid}), "사용자")
    sessions = list(
        db.synth_sessions.find(
            {"user_id": uid}, {"negative_seat_ids": 0, "occupied_seat_ids": 0}
        ).sort("occurred_at", 1)
    )
    origin = Counter(s.get("choice_origin") or "none" for s in sessions)
    satisfaction = Counter(
        sat_bin(s["satisfaction"])
        for s in sessions
        if s.get("satisfaction") is not None
    )
    events = Counter(
        e["type"] for e in db.synth_events.find({"user_id": uid}, {"type": 1})
    )
    # 사용자가 고른 좌석들의 특성 평균(선호 반영 확인)
    keys = [
        (s["scenario_id"], s["chosen_seat_id"])
        for s in sessions
        if s.get("chosen_seat_id")
    ]
    feat_avg = {}
    if keys:
        envs = db.synth_seat_env.find(
            {"_id": {"$in": [f"{a}|{b}" for a, b in keys]}}, {"features": 1}
        )
        acc = Counter()
        n = 0
        for e in envs:
            n += 1
            for k, v in e["features"].items():
                acc[k] += v
        feat_avg = {k: round(acc[k] / n, 3) for k in FEATURES} if n else {}
    return clean(
        {
            "user": u,
            "sessions": sessions,
            "origin": origin,
            "satisfaction": satisfaction,
            "events": events,
            "chosen_feature_avg": feat_avg,
        }
    )


@router.get("/users/{uid}/timeline")
def user_timeline(uid: str):
    """사용자의 시간순 좌석 선택 이력.

    세션마다 선택 좌석 특성과 같은 세션 가용 비교 좌석(negative_seat_ids, 최대 20개 표본)의 평균 특성을 함께 준다.
    둘의 차이(선택−가용)가 '이력에서 드러나는 취향' 신호다. 방문한 열람실의 실좌석 bbox도 함께 준다(평면 재생용).
    """
    db = get_db()
    u = must(
        db.synth_users.find_one(
            {"_id": uid}, {"latent": 1, "preference_profile": 1, "requirements": 1}
        ),
        "사용자",
    )
    sess = list(
        db.synth_sessions.find(
            {"user_id": uid},
            {
                "exposed.seat_id": 1,
                "exposed.rank": 1,
                "chosen_seat_id": 1,
                "negative_seat_ids": 1,
                "scenario_id": 1,
                "room_id": 1,
                "library_id": 1,
                "time_slot": 1,
                "occurred_at": 1,
                "occupancy_rate": 1,
                "choice_origin": 1,
                "satisfaction": 1,
                "chosen_preference_fit": 1,
                "occupied_seat_ids": 1,
            },
        ).sort("occurred_at", 1)
    )
    keys = set()
    for s in sess:
        for sid in ([s["chosen_seat_id"]] if s.get("chosen_seat_id") else []) + s.get(
            "negative_seat_ids", []
        ):
            keys.add(f"{s['scenario_id']}|{sid}")
    env = {
        e["_id"]: e
        for e in db.synth_seat_env.find(
            {"_id": {"$in": list(keys)}},
            {"features": 1, "brightness_by_slot": 1, "raw.noise_index": 1},
        )
    }

    def fv(e, slot):
        f = dict(e["features"])
        f["brightness"] = e["brightness_by_slot"][slot]
        f["quietness"] = round(1 - e["raw"]["noise_index"], 3)
        return f

    feats = FEATURES + ["quietness"]
    rows, rooms = [], {}
    for s in sess:
        r = {
            k: s.get(k)
            for k in (
                "_id",
                "occurred_at",
                "time_slot",
                "library_id",
                "room_id",
                "scenario_id",
                "chosen_seat_id",
                "choice_origin",
                "satisfaction",
                "chosen_preference_fit",
                "occupancy_rate",
                "occupied_seat_ids",
            )
        }
        r["rank"] = next(
            (
                e["rank"]
                for e in s.get("exposed", [])
                if e["seat_id"] == s.get("chosen_seat_id")
            ),
            None,
        )
        r["exposed_ids"] = [
            e["seat_id"] for e in sorted(s.get("exposed", []), key=lambda e: e["rank"])
        ]
        ch = (
            env.get(f"{s['scenario_id']}|{s['chosen_seat_id']}")
            if s.get("chosen_seat_id")
            else None
        )
        av = [
            env[k]
            for k in (f"{s['scenario_id']}|{x}" for x in s.get("negative_seat_ids", []))
            if k in env
        ]
        r["chosen"] = fv(ch, s["time_slot"]) if ch else None
        r["available_mean"] = (
            {
                f: round(sum(fv(e, s["time_slot"])[f] for e in av) / len(av), 4)
                for f in feats
            }
            if av
            else None
        )
        rows.append(r)
        rooms.setdefault(
            s["room_id"],
            {"room_id": s["room_id"], "library_id": s["library_id"], "visits": 0},
        )["visits"] += 1
    for rm in rooms.values():
        seats = list(
            db.seats.find(
                {"room_id": rm["room_id"], "dataset_status": "active"},
                {"seat_id": 1, "map_id": 1, "bbox": 1, "seat_label": 1},
            )
        )
        m = (
            db.maps.find_one(
                {"map_id": seats[0]["map_id"]}, {"width": 1, "height": 1, "map_id": 1}
            )
            if seats
            else None
        )
        nm = db.rooms.find_one({"room_id": rm["room_id"]}, {"name": 1})
        lib = db.libraries.find_one({"library_id": rm["library_id"]}, {"name": 1})
        rm.update(
            name=f"{(lib or {}).get('name', rm['library_id'])} · {(nm or {}).get('name') or rm['room_id'].split(':')[-1]}",
            map=m,
            seats=seats,
        )
    return clean(
        {
            "user_id": uid,
            "features": feats,
            "latent_weights": u["latent"]["utility_weights"],
            "noise_aversion": u["latent"]["noise_aversion"],
            "profile": u["preference_profile"]["preferences"],
            "sessions": rows,
            "rooms": sorted(rooms.values(), key=lambda r: -r["visits"]),
        }
    )


KO = {
    "laptopAllowed": "노트북 허용",
    "keyboardAllowed": "키보드 허용",
    "partition": "칸막이",
    "outletAvailable": "콘센트",
    "windowProximity": "창문 근접",
    "toiletProximity": "화장실 근접",
    "entranceProximity": "출입구 근접",
    "brightness": "밝기",
    "airflow": "직접 바람",
    "deskHeight": "책상 높이",
    "deskWidth": "책상 폭",
    "viewQuality": "전망",
    "quietness": "조용함",
}
BINARY = {"laptopAllowed", "keyboardAllowed", "partition", "outletAvailable"}


def _seat_parts(user, seat, slot):
    """생성기 잠재 효용을 항목별로 분해. 값 = 가중치 × (1 − |target − 특성|). 소음은 0.5×noise_aversion × (1 − noise).
    생성기 true_utility 와 순위가 같다(상수만 다름). 노트북 사용자의 노트북 금지 좌석은 None."""
    L = user["latent"]
    f = dict(seat["features"])
    f["brightness"] = seat["brightness_by_slot"][slot]
    if L["preferences"]["laptop_usage"] == "yes" and f["laptopAllowed"] == 0:
        return None, f
    f["quietness"] = 1 - seat["raw"]["noise_index"]
    parts = {
        k: w["weight"] * (1 - abs(w["target"] - f[k]))
        for k, w in L["utility_weights"].items()
    }
    parts["quietness"] = 0.5 * L["noise_aversion"] * f["quietness"]
    return parts, f


@router.get("/sessions/{sid}/explain")
def session_explain(sid: str):
    """이 세션에서 이 좌석을 고른 이유(합성 생성기의 정답 취향 기준 해설)."""
    db = get_db()
    s = must(db.synth_sessions.find_one({"_id": sid}), "세션")
    if not s.get("chosen_seat_id"):
        return {
            "session_id": sid,
            "reasons": [
                "이 시점에 이 사람이 앉을 수 있는 빈자리가 없어 선택하지 않았습니다."
            ],
            "items": [],
        }
    u = db.synth_users.find_one(
        {"_id": s["user_id"]}, {"latent": 1, "preference_profile": 1}
    )
    env = {
        e["seat_id"]: e
        for e in db.synth_seat_env.find(
            {"scenario_id": s["scenario_id"]},
            {
                "seat_id": 1,
                "seat_label": 1,
                "features": 1,
                "brightness_by_slot": 1,
                "raw.noise_index": 1,
            },
        )
    }
    occ = set(s.get("occupied_seat_ids", []))
    slot = s["time_slot"]
    L = u["latent"]
    avail = {}
    for k, e in env.items():
        if k in occ:
            continue
        parts, f = _seat_parts(u, e, slot)
        if parts is not None:
            avail[k] = (parts, f)
    ch = s["chosen_seat_id"]
    if ch not in avail:
        return {
            "session_id": sid,
            "reasons": ["선택 좌석의 환경 정보를 찾지 못했습니다."],
            "items": [],
        }
    util = {k: sum(p.values()) for k, (p, _) in avail.items()}
    order = sorted(util, key=lambda k: -util[k])
    rank = order.index(ch) + 1
    best = order[0]
    lab = lambda k: env[k].get("seat_label") or k.split(":")[-1]
    exp = {e["seat_id"]: e["rank"] for e in s.get("exposed", [])}
    prof = {p["featureId"]: p for p in u["preference_profile"]["preferences"]}
    weights = {
        **{
            k: {"target": w["target"], "weight": w["weight"]}
            for k, w in L["utility_weights"].items()
        },
        "quietness": {"target": 1.0, "weight": 0.5 * L["noise_aversion"]},
    }
    n = len(avail)
    cp, cf = avail[ch]
    items = []
    for k, w in weights.items():
        if w["weight"] <= 0:
            continue
        mean_v = sum(f[k] for _, f in avail.values()) / n
        mean_p = sum(p[k] for p, _ in avail.values()) / n
        items.append(
            {
                "feature": k,
                "label": KO[k],
                "target": w["target"],
                "weight": round(w["weight"], 3),
                "chosen_value": round(cf[k], 3),
                "available_mean": round(mean_v, 3),
                "share_matching": round(
                    sum(1 for _, f in avail.values() if abs(f[k] - w["target"]) < 0.25)
                    / n,
                    3,
                ),
                "contribution": round(cp[k] - mean_p, 3),
                "in_profile": k in prof,
                "hidden": k not in prof,
            }
        )
    items.sort(key=lambda x: -x["contribution"])

    def phrase(it):
        k, v, m, t = (
            it["feature"],
            it["chosen_value"],
            it["available_mean"],
            it["target"],
        )
        tag = " · <i>카카오톡 프로필에 없는 숨은 취향</i>" if it["hidden"] else ""
        if k in BINARY:
            want = "있는" if t >= 0.5 else "없는"
            have = "있음" if v >= 0.5 else "없음"
            return f"<b>{it['label']}</b> {have} — 이 사람은 {want} 좌석을 원함(중요도 {it['weight']:.2f}). 빈자리 중 원하는 조건은 {it['share_matching'] * 100:.0f}%{tag}"
        dirw = (
            "높을수록"
            if t >= 0.7
            else "낮을수록"
            if t <= 0.3
            else f"{t:.2f}에 가까울수록"
        )
        cmp_ = "높음" if v > m else "낮음"
        return f"<b>{it['label']}</b> {v:.2f} (빈자리 평균 {m:.2f}보다 {cmp_}) — {dirw} 좋아함(중요도 {it['weight']:.2f}){tag}"

    reasons = []
    if s.get("choice_origin") == "recommendation":
        reasons.append(
            f"추천 <b>{exp.get(ch)}위</b>로 노출된 좌석을 추천 목록에서 골랐습니다. 추천 순위가 높을수록 조금 더 끌립니다(이 사람의 추천 신뢰도 {L['rec_trust']:.2f})."
        )
    else:
        reasons.append(
            f"추천 목록(top-5) 밖에서 직접 골랐습니다. 추천이 이 사람의 실제 취향을 놓친 경우입니다(관측 프로필과 실제 취향의 차이)."
        )
    reasons.append(
        f"이 시점 빈자리 <b>{n}석</b> 중 이 사람의 실제 취향 점수로 <b>{rank}위</b>(상위 {rank / n * 100:.0f}%)인 좌석입니다."
    )
    pos = [it for it in items if it["contribution"] > 0.01][:3]
    neg = [it for it in items if it["contribution"] < -0.01][-2:]
    if pos:
        reasons.append("끌린 점: " + " / ".join(phrase(it) for it in pos))
    if neg:
        reasons.append("감수한 점: " + " / ".join(phrase(it) for it in reversed(neg)))
    if best != ch:
        where = f"추천 {exp[best]}위" if best in exp else "추천 목록 밖"
        reasons.append(
            f"가장 잘 맞는 빈자리는 <b>{lab(best)}</b>({where}, 점수 {util[best]:.2f} vs 선택 {util[ch]:.2f})였지만, 사람은 매번 최적을 고르지 않습니다(합리성 β={L['rationality']}: 점수 차가 작으면 비슷한 좌석 사이에서 흔들림)."
        )
    else:
        reasons.append(
            "이 시점 빈자리 중 이 사람에게 <b>가장 잘 맞는 좌석</b>을 골랐습니다."
        )
    if s.get("satisfaction") is not None:
        reasons.append(
            f"앉은 뒤 만족도 <b>{s['satisfaction']:.2f}</b>(취향 적합도 {s.get('chosen_preference_fit', 0):.2f})."
        )
    return clean(
        {
            "session_id": sid,
            "chosen_seat_id": ch,
            "chosen_label": lab(ch),
            "rank_true": rank,
            "available": n,
            "best_seat_id": best,
            "best_label": lab(best),
            "utility": round(util[ch], 3),
            "best_utility": round(util[best], 3),
            "reasons": reasons,
            "items": items,
            "note": "합성 생성기의 정답(잠재) 취향으로 계산한 해설입니다. 추천 모델은 이 정답을 볼 수 없습니다.",
        }
    )


# ------------------------------------------------------------------ sessions
@router.get("/sessions")
def sessions(
    user: str | None = None,
    library: str | None = None,
    slot: str | None = None,
    origin: str | None = None,
    sat: str | None = None,
    scenario: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    db = get_db()
    f = {}
    if user:
        f["user_id"] = user
    if library:
        f["library_id"] = library
    if slot:
        f["time_slot"] = slot
    if scenario:
        f["scenario_id"] = scenario
    if origin:
        f["choice_origin"] = None if origin == "none" else origin
    if sat:  # 0.1 폭 구간의 하한("0.0"~"0.9"). 0.9 구간은 1.0 포함
        lo = round(float(sat), 1)
        f["satisfaction"] = (
            {"$gte": lo, "$lte": 1.0}
            if lo >= 0.9
            else {"$gte": lo, "$lt": round(lo + 0.1, 1)}
        )
    total = (
        db.synth_sessions.count_documents(f)
        if f
        else db.synth_sessions.estimated_document_count()
    )
    rows = list(
        db.synth_sessions.find(f, {"negative_seat_ids": 0, "occupied_seat_ids": 0})
        .sort("_id", 1)
        .skip(skip)
        .limit(limit)
    )
    return clean({"total": total, "items": rows})


@router.get("/sessions/{sid}")
def session(sid: str):
    db = get_db()
    s = must(db.synth_sessions.find_one({"_id": sid}), "세션")
    ids = (
        [x["seat_id"] for x in s["exposed"]]
        + s.get("negative_seat_ids", [])
        + ([s["chosen_seat_id"]] if s.get("chosen_seat_id") else [])
    )
    env = {
        e["seat_id"]: e
        for e in db.synth_seat_env.find(
            {"_id": {"$in": [f"{s['scenario_id']}|{i}" for i in ids]}},
            {
                "seat_id": 1,
                "seat_label": 1,
                "features": 1,
                "brightness_by_slot": 1,
                "raw": 1,
            },
        )
    }
    return clean(
        {
            "session": s,
            "events": list(
                db.synth_events.find({"session_id": sid}).sort("occurred_at", 1)
            ),
            "user": db.synth_users.find_one({"_id": s["user_id"]}),
            "seat_env": env,
        }
    )


# ------------------------------------------------------------------ stats
_stats_lock = threading.Lock()


def _bin(v, bounds):
    """MongoDB $bucket 과 같은 규칙: bounds[i] <= v < bounds[i+1] → i, 범위 밖은 None."""
    if v is None:
        return None
    i = bisect.bisect_right(bounds, v) - 1
    return i if 0 <= i < len(bounds) - 1 else None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _stats_all():
    """전체(None)와 도서관별 통계를 데이터를 한 번씩만 훑어 한꺼번에 계산한다(카탈로그는 버전 단위로 고정)."""
    FB = [i / 10 for i in range(11)] + [
        1.0001
    ]  # 특성 히스토그램 경계(1.0 은 마지막 구간에 합침)
    SB = [i / 10 for i in range(10)] + [1.0001]  # 만족도 경계
    J = "json_extract(document, '$.{}')".format
    out = {}

    def bucket(lib):
        if lib not in out:
            out[lib] = {
                "hist": {f: [0] * 10 for f in FEATURES},
                "desk": {},
                "lux": {s: [] for s in ("morning", "noon", "evening")},
                "cond": {k: Counter() for k in ("season", "weather", "laptop_allowed")},
                "daily": Counter(),
                "origin": Counter(),
                "sat": Counter(),
                "slot": Counter(),
                "basis": Counter(),
                "arch": Counter(),
                "acc": {},
                "rank": Counter(),
            }
        return out[lib]

    by_lib = {}
    for lib, feats, lm, ln, le, temp, dt, w, h in query(
        f"""SELECT {J("library_id")}, {J("features")}, {J("raw.lux.morning")}, {J("raw.lux.noon")}, {J("raw.lux.evening")},
                   {J("raw.temperature_c")}, {J("raw.desk_type")}, {J("raw.width_cm")}, {J("raw.height_cm")}
            FROM catalog_documents WHERE collection = 'synth_seat_env'"""
    ):
        f = json.loads(feats) if feats else {}
        acc = by_lib.setdefault(lib, {k: [] for k in FEATURES + ["lux_noon", "temp"]})
        for k in FEATURES:
            acc[k].append(f.get(k))
        acc["lux_noon"].append(ln)
        acc["temp"].append(temp)
        for b in (bucket(None), bucket(lib)):
            for k in FEATURES:
                i = _bin(f.get(k), FB)
                if i is not None:
                    b["hist"][k][min(i, 9)] += 1
            d = b["desk"].setdefault(dt, {"n": 0, "w": [], "h": []})
            d["n"] += 1
            d["w"].append(w)
            d["h"].append(h)
            for slot, v in (("morning", lm), ("noon", ln), ("evening", le)):
                b["lux"][slot].append(v)

    for lib, season, weather, laptop in query(
        f"""SELECT {J("library_id")}, {J("conditions.season")}, {J("conditions.weather")}, {J("conditions.laptop_allowed")}
            FROM catalog_documents WHERE collection = 'synth_scenarios'"""
    ):
        for b in (bucket(None), bucket(lib)):
            b["cond"]["season"][str(season)] += 1
            b["cond"]["weather"][str(weather)] += 1
            # SQLite JSON 은 true/false 를 1/0 으로 돌려준다 → MongoDB 때와 같은 "True"/"False" 키로
            b["cond"]["laptop_allowed"][
                str(bool(laptop)) if laptop in (0, 1) else str(laptop)
            ] += 1

    for lib, sat, slot, origin, basis, day, rank in query(
        f"""SELECT {J("library_id")}, {J("satisfaction")}, {J("time_slot")}, {J("choice_origin")}, {J("ranking_basis")},
                   substr({J("occurred_at")}, 1, 10),
                   CASE WHEN {J("chosen_seat_id")} IS NULL THEN NULL ELSE coalesce(
                     (SELECT CAST(e.key AS INTEGER) + 1 FROM json_each(document, '$.exposed') e
                      WHERE json_extract(e.value, '$.seat_id') = {J("chosen_seat_id")} ORDER BY e.key LIMIT 1), 'outside') END
            FROM catalog_documents WHERE collection = 'synth_sessions'"""
    ):
        for b in (bucket(None), bucket(lib)):
            b["daily"][day] += 1
            b["origin"][str(origin)] += 1
            b["slot"][str(slot)] += 1
            b["basis"][str(basis)] += 1
            i = _bin(sat, SB)
            if i is not None:
                b["sat"][f"{SB[i]:.1f}"] += 1
            if rank is not None:
                b["rank"][str(rank)] += 1

    for home, arch, prefs in query(
        f"""SELECT {J("home_library_id")}, {J("archetype")}, {J("kakaotalk_profile.preferences")}
            FROM catalog_documents WHERE collection = 'synth_users'"""
    ):
        prefs = json.loads(prefs) if prefs else {}
        for b in (bucket(None), bucket(home)):
            b["arch"][str(arch)] += 1
            for f, p in prefs.items():
                b["acc"].setdefault(f, Counter())[
                    "unknown"
                    if p["preference"] == "unknown"
                    else ("correct" if p.get("is_correct_vs_latent") else "wrong")
                ] += 1

    event_type = {
        str(t): n
        for t, n in query(
            f"SELECT {J('type')}, count(*) FROM catalog_documents WHERE collection = 'synth_events' GROUP BY 1"
        )
    }
    by_library = [
        {"_id": lib, **{k: _avg(v) for k, v in acc.items()}}
        for lib, acc in sorted(by_lib.items(), key=lambda x: str(x[0]))
    ]
    result = {}
    for lib, b in out.items():
        result[lib] = {
            "feature_hist": b["hist"],
            "by_library": by_library,
            "desk_types": [
                {"_id": k, "n": d["n"], "w": _avg(d["w"]), "h": _avg(d["h"])}
                for k, d in sorted(b["desk"].items(), key=lambda x: str(x[0]))
            ],
            "lux_by_slot": {"_id": None, **{s: _avg(v) for s, v in b["lux"].items()}},
            "conditions": {k: dict(v) for k, v in b["cond"].items()},
            "daily_sessions": [
                {"_id": d, "n": n}
                for d, n in sorted(b["daily"].items(), key=lambda x: str(x[0]))
            ],
            "origin": dict(b["origin"]),
            "satisfaction": dict(sorted(b["sat"].items())),
            "time_slot": dict(b["slot"]),
            "ranking_basis": dict(b["basis"]),
            "archetype": dict(b["arch"]),
            "event_type": event_type,
            "profile_accuracy": {f: dict(c) for f, c in b["acc"].items()},
            "chosen_rank": dict(b["rank"]),
        }
    return result


@router.get("/stats")
def stats(library: str | None = None):
    with _stats_lock:  # 동시에 여러 요청이 와도 한 번만 계산
        allstats = cached("stats", 86400, _stats_all)
    if library and library not in allstats:
        raise HTTPException(404, "도서관 없음")
    return clean(allstats[library or None])


# ------------------------------------------------------------------ raw browser
@router.get("/collections")
def collections():
    db = get_db()
    return [
        {
            "name": c,
            "count": db[c].estimated_document_count(),
            "kind": "synthetic" if c.startswith("synth_") else "real",
        }
        for c in COLLECTIONS
    ]


@router.get("/collections/{name}")
def collection(
    name: str,
    filter: str = "{}",
    sort: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    if name not in COLLECTIONS:
        raise HTTPException(404, "컬렉션 없음")
    try:
        f = json.loads(filter or "{}")
        assert isinstance(f, dict)
    except Exception:
        raise HTTPException(400, "filter는 JSON 객체여야 합니다")
    if "$where" in json.dumps(f) or "$function" in json.dumps(f):
        raise HTTPException(400, "허용되지 않는 연산자")
    db = get_db()
    cur = db[name].find(f)
    if sort:
        cur = cur.sort(sort.lstrip("-"), -1 if sort.startswith("-") else 1)
    try:
        items = list(cur.skip(skip).limit(limit))
        total = db[name].count_documents(f)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"쿼리 오류: {type(e).__name__}")
    return clean({"total": total, "items": items})
