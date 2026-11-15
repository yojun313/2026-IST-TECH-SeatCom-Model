"""좌석 좌표가 있는 도서관의 실좌석 배치 위에 합성 환경·사용자·선택 이력을 생성한다.

모든 출력 레코드는 is_synthetic=true, verification="synthetic"이며 실측값이 아니다.
실데이터(dataset.json)는 읽기만 한다. 출력: libraries/synthetic/*.jsonl

    python3 libraries/scripts/generate_synthetic.py [--scenarios 30] [--users 3000] [--sessions 60000] [--seed 20261114] [--workers N]

멀티프로세스: 열람실 환경은 열람실 단위, 세션은 CHUNK건 단위로 병렬 생성한다. 각 단위의 난수는
"seed:종류:번호" 문자열 시드로 따로 만들므로 --workers 값과 관계없이 결과가 같다(같은 seed면 재현).

좌표계: 원본 지도 픽셀(top-left, x→right, y→down). 미터 환산은 합성 축척(synthetic_meters_per_px)이며
좌석 버튼 한 변 ≈ 0.75m 라는 가정에서 나온 값이다(실측 아님).
"""

import argparse, json, math, multiprocessing as mp, os, random, statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # 진행률 표시(선택). 없으면 그냥 실행된다: python3 -m pip install --user tqdm
    from tqdm import tqdm
except ImportError:

    def tqdm(it=None, **kw):
        return it


LIB = Path(__file__).resolve().parents[1]  # data/libraries
ROOT = LIB / "real"  # 좌석 좌표가 있는 활성 도서관
OUT = LIB / "synthetic"
GEN_VERSION = "synth-1.0"
FEATURE_REGISTRY = "features-1"
KST = timezone(timedelta(hours=9))
SEAT_SIDE_M = 0.75
TIME_SLOTS = {"morning": 10, "noon": 13, "evening": 19}
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

DESK_TYPES = {  # type: (width_cm range, height_cm range, partition prob)
    "individual_carrel": ((85, 110), (72, 76), 0.95),
    "long_shared_table": ((65, 90), (71, 75), 0.15),
    "four_person_table": ((90, 120), (72, 75), 0.30),
    "wide_individual_desk": ((120, 160), (72, 76), 0.60),
    "window_counter": ((70, 95), (88, 105), 0.05),
    "standing_desk": ((80, 105), (100, 112), 0.0),
}


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def r3(v):
    return None if v is None else round(v, 3)


# ---------------------------------------------------------------- geometry helpers
def seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else clamp(((px - ax) * dx + (py - ay) * dy) / L2)
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def wall_point(bounds, wall, t):
    x0, y0, x1, y1 = bounds
    return {
        "top": (x0 + t * (x1 - x0), y0),
        "bottom": (x0 + t * (x1 - x0), y1),
        "left": (x0, y0 + t * (y1 - y0)),
        "right": (x1, y0 + t * (y1 - y0)),
    }[wall]


def wall_len(bounds, wall):
    x0, y0, x1, y1 = bounds
    return (x1 - x0) if wall in ("top", "bottom") else (y1 - y0)


def nearest_wall(bounds, p):
    x0, y0, x1, y1 = bounds
    d = {"left": p[0] - x0, "right": x1 - p[0], "top": p[1] - y0, "bottom": y1 - p[1]}
    return min(d, key=d.get)


INWARD = {"top": (0, 1), "bottom": (0, -1), "left": (1, 0), "right": (-1, 0)}


def desk_groups(seats, med):
    """맞닿은 좌석 버튼을 같은 책상 묶음으로 본다(union-find)."""
    n = len(seats)
    par = list(range(n))

    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    gap = 0.3 * med
    for i in range(n):
        a = seats[i]["bbox"]
        for j in range(i + 1, n):
            b = seats[j]["bbox"]
            if (
                a["x"] - gap <= b["x"] + b["width"]
                and b["x"] - gap <= a["x"] + a["width"]
                and a["y"] - gap <= b["y"] + b["height"]
                and b["y"] - gap <= a["y"] + a["height"]
            ):
                par[f(i)] = f(j)
    groups = {}
    for i in range(n):
        groups.setdefault(f(i), []).append(i)
    return list(groups.values())


# ---------------------------------------------------------------- real data loading
def load_units():
    reg = json.load(open(LIB / "registry" / "ID_REGISTRY.json", encoding="utf-8"))
    units, libs = [], []
    for r in reg:
        p = ROOT / r["library_id"] / "dataset.json"
        if not p.exists():
            continue  # backup/ 으로 옮긴 좌석 좌표 없는 도서관
        ds = json.load(open(p, encoding="utf-8"))
        if not ds["seats"]:
            continue
        libs.append(r["library_id"])
        maps = {m["map_id"]: m for m in ds["maps"]}
        rooms = {x["room_id"]: x for x in ds["rooms"]}
        rf = {}
        for x in ds.get("room_features", []):
            rf.setdefault(x["room_id"], {})[x["name"]] = x["value"]
        by = {}
        for s in ds["seats"]:
            by.setdefault((s["map_id"], s["room_id"]), []).append(s)
        for (mid, rid), ss in by.items():
            m = maps[mid]
            fac = [f for f in ds["facilities"] if f["map_id"] == mid]
            units.append(
                {
                    "library_id": r["library_id"],
                    "library_name": r["name"],
                    "map": m,
                    "room": rooms.get(rid, {"room_id": rid, "name": None}),
                    "seats": ss,
                    "real_facilities": fac,
                    "real_room_features": rf.get(rid, {}),
                }
            )
    return libs, units


# ---------------------------------------------------------------- environment synthesis
def build_unit_static(u):
    ss = u["seats"]
    med = statistics.median(min(s["bbox"]["width"], s["bbox"]["height"]) for s in ss)
    mpp = SEAT_SIDE_M / med
    xs0 = min(s["bbox"]["x"] for s in ss)
    ys0 = min(s["bbox"]["y"] for s in ss)
    xs1 = max(s["bbox"]["x"] + s["bbox"]["width"] for s in ss)
    ys1 = max(s["bbox"]["y"] + s["bbox"]["height"] for s in ss)
    mg = 1.5 / mpp
    W, H = u["map"]["width"], u["map"]["height"]
    bounds = (max(0, xs0 - mg), max(0, ys0 - mg), min(W, xs1 + mg), min(H, ys1 + mg))
    centers = [
        (
            s["bbox"]["x"] + s["bbox"]["width"] / 2,
            s["bbox"]["y"] + s["bbox"]["height"] / 2,
        )
        for s in ss
    ]
    groups = desk_groups(ss, med)
    seat_group = {}
    for gi, g in enumerate(groups):
        for i in g:
            seat_group[i] = gi
    u.update(
        mpp=mpp,
        bounds=bounds,
        centers=centers,
        groups=groups,
        seat_group=seat_group,
        med=med,
    )
    real_ent = [
        f
        for f in u["real_facilities"]
        if f["type"] == "entrance" and f.get("access_point")
    ]
    real_toi = [
        f
        for f in u["real_facilities"]
        if f["type"] == "toilet" and f.get("access_point")
    ]
    u["real_entrance"] = tuple(real_ent[0]["access_point"]) if real_ent else None
    u["real_toilets"] = [tuple(f["access_point"]) for f in real_toi]
    u["unit_id"] = f"{u['map']['map_id']}|{u['room']['room_id']}"


def synth_scenario(u, k, rng):
    lid = u["library_id"]
    mpp = u["mpp"]
    b = u["bounds"]
    sid = f"{lid}:synth:{u['map']['map_id'].split(':')[-1]}:{u['room']['room_id'].split(':')[-1]}:s{k:03d}"
    px = lambda m: m / mpp  # metres → px
    walls = ["top", "bottom", "left", "right"]
    objs = []

    # 출입구: 같은 지도에 실제 후보가 있으면 그것을 사용
    if u["real_entrance"]:
        ent = u["real_entrance"]
        ent_src = "real_model_candidate"
    else:
        ew = rng.choice(walls)
        ent = wall_point(b, ew, rng.uniform(0.1, 0.9))
        ent_src = "synthetic"
    ent_wall = nearest_wall(b, ent)
    objs.append(
        {
            "type": "entrance",
            "point": [r3(ent[0]), r3(ent[1])],
            "wall": ent_wall,
            "origin": ent_src,
        }
    )

    # 창문: 출입구 벽이 아닌 1~3개 벽, 벽 길이의 40~90%
    win_walls = rng.sample(
        [w for w in walls if w != ent_wall], rng.choice([1, 1, 2, 2, 3])
    )
    windows = []
    for w in win_walls:
        cover = rng.uniform(0.4, 0.9)
        start = rng.uniform(0, 1 - cover)
        n_pane = max(1, int(wall_len(b, w) * cover * mpp / rng.uniform(1.5, 3.0)))
        view = rng.uniform(0.2, 1.0)  # 합성 전망 점수(벽 단위)
        for i in range(n_pane):
            t0 = start + cover * i / n_pane
            t1 = start + cover * (i + 0.85) / n_pane
            a, c = wall_point(b, w, t0), wall_point(b, w, t1)
            windows.append(
                {"a": a, "b": c, "wall": w, "view": view, "blind": rng.random() < 0.2}
            )
            objs.append(
                {
                    "type": "window",
                    "line": [[r3(a[0]), r3(a[1])], [r3(c[0]), r3(c[1])]],
                    "wall": w,
                    "view_score": r3(view),
                    "blind_closed": windows[-1]["blind"],
                    "origin": "synthetic",
                }
            )

    # 화장실: 실제 후보가 같은 지도에 있으면 사용, 아니면 출입구 바깥 3~25m
    if u["real_toilets"]:
        toilets = u["real_toilets"]
        t_src = "real_model_candidate"
    else:
        toilets = []
        for _ in range(rng.choice([1, 1, 2])):
            d = px(rng.uniform(3, 25))
            ang = rng.uniform(0, 2 * math.pi)
            toilets.append((ent[0] + d * math.cos(ang), ent[1] + d * math.sin(ang)))
        t_src = "synthetic"
    for t in toilets:
        objs.append({"type": "toilet", "point": [r3(t[0]), r3(t[1])], "origin": t_src})

    # 냉방기
    acs = []
    if rng.random() < 0.6:
        sp = rng.uniform(6, 9)
        nx = max(1, int((b[2] - b[0]) * mpp / sp))
        ny = max(1, int((b[3] - b[1]) * mpp / sp))
        for i in range(nx):
            for j in range(ny):
                x = b[0] + (i + 0.5) * (b[2] - b[0]) / nx + px(rng.gauss(0, 0.5))
                y = b[1] + (j + 0.5) * (b[3] - b[1]) / ny + px(rng.gauss(0, 0.5))
                acs.append(
                    {
                        "kind": "ceiling_cassette",
                        "p": (x, y),
                        "dir": None,
                        "strength": rng.uniform(0.5, 1.0),
                        "throw_m": rng.uniform(3, 5),
                    }
                )
    else:
        for w in rng.sample(walls, rng.choice([1, 2])):
            n = max(1, int(wall_len(b, w) * mpp / rng.uniform(5, 8)))
            for i in range(n):
                acs.append(
                    {
                        "kind": "wall_mounted",
                        "p": wall_point(b, w, (i + 0.5) / n),
                        "dir": INWARD[w],
                        "strength": rng.uniform(0.6, 1.0),
                        "throw_m": rng.uniform(5, 8),
                    }
                )
    if rng.random() < 0.3:
        acs.append(
            {
                "kind": "standing",
                "p": (rng.uniform(b[0], b[2]), rng.uniform(b[1], b[3])),
                "dir": None,
                "strength": rng.uniform(0.4, 0.8),
                "throw_m": rng.uniform(2.5, 4),
            }
        )
    for a in acs:
        objs.append(
            {
                "type": "air_conditioner",
                "kind": a["kind"],
                "point": [r3(a["p"][0]), r3(a["p"][1])],
                "direction": a["dir"],
                "strength": r3(a["strength"]),
                "throw_m": r3(a["throw_m"]),
                "origin": "synthetic",
            }
        )

    # 조명(천장 격자), 5~12%는 어둡거나 고장
    lights = []
    sp = rng.uniform(2.4, 3.6)
    base = rng.uniform(450, 750)
    nx = max(1, int((b[2] - b[0]) * mpp / sp))
    ny = max(1, int((b[3] - b[1]) * mpp / sp))
    bad = rng.uniform(0.05, 0.12)
    for i in range(nx):
        for j in range(ny):
            x = b[0] + (i + 0.5) * (b[2] - b[0]) / nx
            y = b[1] + (j + 0.5) * (b[3] - b[1]) / ny
            f = rng.uniform(0, 0.4) if rng.random() < bad else rng.uniform(0.85, 1.1)
            lights.append({"p": (x, y), "lux": base * f})
            objs.append(
                {
                    "type": "ceiling_light",
                    "point": [r3(x), r3(y)],
                    "lux_at_desk_below": r3(base * f),
                    "origin": "synthetic",
                }
            )
    light_sp_m = sp

    # 콘센트: 벽 콘센트 2~4m 간격 + 일부 책상 묶음에 책상 콘센트
    outlets = []
    o_sp = rng.uniform(2, 4)
    for w in walls:
        n = max(1, int(wall_len(b, w) * mpp / o_sp))
        for i in range(n):
            if rng.random() < 0.85:
                outlets.append(("wall", wall_point(b, w, (i + 0.5) / n), None))
    p_desk = rng.uniform(0.2, 0.85)
    desk_outlet_groups = set(
        gi for gi in range(len(u["groups"])) if rng.random() < p_desk
    )
    for gi in desk_outlet_groups:
        g = u["groups"][gi]
        cx = sum(u["centers"][i][0] for i in g) / len(g)
        cy = sum(u["centers"][i][1] for i in g) / len(g)
        outlets.append(("desk", (cx, cy), gi))
    for kind, p, gi in outlets:
        objs.append(
            {
                "type": "outlet",
                "kind": kind,
                "point": [r3(p[0]), r3(p[1])],
                "desk_group": gi,
                "origin": "synthetic",
            }
        )

    # 책상: 묶음 크기와 위치로 타입 확률 조정
    desk = {}
    for gi, g in enumerate(u["groups"]):
        n = len(g)
        cx = sum(u["centers"][i][0] for i in g) / n
        cy = sum(u["centers"][i][1] for i in g) / n
        near_win = (
            windows
            and min(seg_dist((cx, cy), w["a"], w["b"]) for w in windows) * mpp < 1.8
        )
        if n == 1:
            opts = ["individual_carrel", "wide_individual_desk", "standing_desk"] + (
                ["window_counter"] * 2 if near_win else []
            )
        elif n <= 4:
            opts = [
                "four_person_table",
                "four_person_table",
                "individual_carrel",
                "wide_individual_desk",
            ]
        else:
            opts = ["long_shared_table", "long_shared_table", "individual_carrel"] + (
                ["window_counter"] if near_win else []
            )
        t = rng.choice(opts)
        (w0, w1), (h0, h1), pp = DESK_TYPES[t]
        desk[gi] = {
            "desk_type": t,
            "width_cm": round(rng.uniform(w0, w1)),
            "height_cm": round(rng.uniform(h0, h1)),
            "partition": rng.random() < pp,
            "desk_outlet": gi in desk_outlet_groups,
        }

    # 규정(실제 room_features가 있으면 우선)
    rrf = u["real_room_features"]
    laptop = (
        rrf.get("laptopAllowed")
        if rrf.get("laptopAllowed") is not None
        else (rng.random() < 0.6)
    )
    keyboard = (
        rrf.get("keyboardAllowed")
        if rrf.get("keyboardAllowed") is not None
        else (laptop and rng.random() < 0.7)
    )
    season = rng.choice(["summer", "summer", "winter", "spring_fall"])
    weather = rng.choice(["clear", "clear", "cloudy", "rain"])
    base_temp = {"summer": 27.5, "winter": 21.0, "spring_fall": 24.0}[season]
    day_f = {"clear": 1.0, "cloudy": 0.55, "rain": 0.3}[weather]
    hour_f = {"morning": 0.7, "noon": 1.0, "evening": 0.05}
    win_lux0 = rng.uniform(900, 1800)

    def lux_at(p, slot):
        # 격자 조명 커널 합 ≈ 2.6 (a=0.5*간격) → 실내 중앙에서 ≈ 기준 조도
        art = (
            sum(
                L["lux"]
                * (
                    1
                    + (
                        (math.hypot(p[0] - L["p"][0], p[1] - L["p"][1]) * mpp)
                        / (0.5 * light_sp_m)
                    )
                    ** 2
                )
                ** -1.5
                for L in lights
            )
            / 2.6
        )
        day = 0.0
        for w in windows:
            if w["blind"]:
                continue
            d = seg_dist(p, w["a"], w["b"]) * mpp
            day = max(day, win_lux0 * day_f * hour_f[slot] * math.exp(-d / 2.5))
        return art + day

    def airflow_at(p):
        tot = 0.0
        for a in acs:
            dx, dy = (p[0] - a["p"][0]) * mpp, (p[1] - a["p"][1]) * mpp
            d = math.hypot(dx, dy)
            if a["dir"]:
                cos = (dx * a["dir"][0] + dy * a["dir"][1]) / (d + 1e-9)
                tot += a["strength"] * max(0.0, cos) ** 2 * math.exp(-d / a["throw_m"])
            else:
                tot += a["strength"] * math.exp(-((d / a["throw_m"]) ** 2))
        return clamp(tot)

    # 밝기 격자(1m) — 정오 기준
    gstep = 1.0 / mpp
    gx = max(1, int((b[2] - b[0]) / gstep))
    gy = max(1, int((b[3] - b[1]) / gstep))
    if gx * gy > 4000:
        s = math.sqrt(gx * gy / 4000)
        gx = max(1, int(gx / s))
        gy = max(1, int(gy / s))
    grid = [
        [
            round(
                lux_at(
                    (
                        b[0] + (i + 0.5) * (b[2] - b[0]) / gx,
                        b[1] + (j + 0.5) * (b[3] - b[1]) / gy,
                    ),
                    "noon",
                )
            )
            for i in range(gx)
        ]
        for j in range(gy)
    ]

    seats_out = []
    for i, s in enumerate(u["seats"]):
        p = u["centers"][i]
        gi = u["seat_group"][i]
        dk = desk[gi]
        d_win = min((seg_dist(p, w["a"], w["b"]) for w in windows), default=None)
        d_win = None if d_win is None else d_win * mpp
        best_view = 0.0
        for w in windows:
            dm = seg_dist(p, w["a"], w["b"]) * mpp
            best_view = max(best_view, w["view"] * math.exp(-dm / 4))
        d_ent = (abs(p[0] - ent[0]) + abs(p[1] - ent[1])) * mpp
        d_toi = (
            min(
                (
                    abs(p[0] - ent[0])
                    + abs(p[1] - ent[1])
                    + math.hypot(t[0] - ent[0], t[1] - ent[1]) * 1.15
                )
                for t in toilets
            )
            * mpp
        )
        d_out = (
            min(math.hypot(p[0] - o[1][0], p[1] - o[1][1]) for o in outlets) * mpp
            if outlets
            else None
        )
        outlet_ok = dk["desk_outlet"] or (d_out is not None and d_out <= 1.2)
        d_ac = (
            min(math.hypot(p[0] - a["p"][0], p[1] - a["p"][1]) for a in acs) * mpp
            if acs
            else None
        )
        air = airflow_at(p)
        temp = (
            base_temp
            - (3.5 * air if season == "summer" else 1.5 * air)
            + rng.gauss(0, 0.3)
        )
        lux = {sl: round(lux_at(p, sl)) for sl in TIME_SLOTS}
        noise = clamp(0.25 + 0.35 * (1 - clamp(d_ent / 15)) + rng.gauss(0, 0.08))
        feats = {
            "laptopAllowed": int(bool(laptop)),
            "keyboardAllowed": int(bool(keyboard)),
            "partition": int(dk["partition"]),
            "outletAvailable": int(outlet_ok),
            "windowProximity": r3(max(0, 1 - d_win / 10)) if d_win is not None else 0.0,
            "toiletProximity": r3(max(0, 1 - d_toi / 100)),
            "entranceProximity": r3(max(0, 1 - d_ent / 50)),
            "brightness": r3(clamp((lux["noon"] - 100) / 900)),
            "airflow": r3(air),
            "deskHeight": r3(clamp((dk["height_cm"] - 60) / 40)),
            "deskWidth": r3(clamp((dk["width_cm"] - 60) / 120)),
            "viewQuality": r3(best_view),
        }
        seats_out.append(
            {
                "_id": f"{sid}|{s['seat_id']}",
                "scenario_id": sid,
                "library_id": lid,
                "room_id": u["room"]["room_id"],
                "map_id": u["map"]["map_id"],
                "seat_id": s["seat_id"],
                "seat_label": s.get("seat_label"),
                "center_px": [r3(p[0]), r3(p[1])],
                "raw": {
                    "dist_window_m": r3(d_win),
                    "dist_toilet_walk_m": r3(d_toi),
                    "dist_entrance_walk_m": r3(d_ent),
                    "dist_outlet_m": r3(d_out),
                    "dist_ac_m": r3(d_ac),
                    "airflow_index": r3(air),
                    "temperature_c": round(temp, 1),
                    "lux": lux,
                    "noise_index": r3(noise),
                    "desk_group": gi,
                    **dk,
                },
                "features": feats,
                "brightness_by_slot": {
                    sl: r3(clamp((v - 100) / 900)) for sl, v in lux.items()
                },
                "feature_registry_version": FEATURE_REGISTRY,
                "is_synthetic": True,
                "verification": "synthetic",
                "generator_version": GEN_VERSION,
            }
        )

    scen = {
        "_id": sid,
        "scenario_id": sid,
        "scenario_index": k,
        "library_id": lid,
        "library_name": u["library_name"],
        "room_id": u["room"]["room_id"],
        "room_name": u["room"].get("name"),
        "map_id": u["map"]["map_id"],
        "map_size_px": [u["map"]["width"], u["map"]["height"]],
        "synthetic_meters_per_px": r3(mpp),
        "room_bounds_px": [r3(v) for v in b],
        "seat_count": len(u["seats"]),
        "conditions": {
            "season": season,
            "weather": weather,
            "base_temperature_c": base_temp,
            "laptop_allowed": bool(laptop),
            "keyboard_allowed": bool(keyboard),
            "laptop_rule_origin": "real_room_feature"
            if rrf.get("laptopAllowed") is not None
            else "synthetic",
        },
        "objects": objs,
        "desk_groups": [
            {
                "desk_group": gi,
                "seat_ids": [u["seats"][i]["seat_id"] for i in g],
                **desk[gi],
            }
            for gi, g in enumerate(u["groups"])
        ],
        "brightness_grid": {
            "slot": "noon",
            "unit": "lux",
            "cols": gx,
            "rows": gy,
            "origin_px": [r3(b[0]), r3(b[1])],
            "cell_px": [r3((b[2] - b[0]) / gx), r3((b[3] - b[1]) / gy)],
            "values": grid,
        },
        "counts": {
            t: sum(1 for o in objs if o["type"] == t)
            for t in (
                "window",
                "toilet",
                "air_conditioner",
                "ceiling_light",
                "outlet",
                "entrance",
            )
        },
        "is_synthetic": True,
        "verification": "synthetic",
        "generator_version": GEN_VERSION,
    }
    return scen, seats_out


# ---------------------------------------------------------------- users (카카오톡 분석 스키마 형식)
PREF_VALUES = {
    "restroom": ["near", "no_preference"],
    "lighting": ["bright", "dim"],
    "air_conditioning": ["cool", "warm"],
    "desk": ["large", "compact"],
    "laptop_usage": ["yes", "no"],
    "outlet": ["required", "not_required"],
    "view": ["window", "no_preference"],
}
PREF_PRIOR = {
    "restroom": [0.35, 0.65],
    "lighting": [0.7, 0.3],
    "air_conditioning": [0.5, 0.5],
    "desk": [0.55, 0.45],
    "laptop_usage": [0.6, 0.4],
    "outlet": [0.6, 0.4],
    "view": [0.45, 0.55],
}
ARCHETYPES = {  # 행동 원형별 사전 치우침
    "laptop_worker": {"laptop_usage": "yes", "outlet": "required", "desk": "large"},
    "exam_focus": {"lighting": "bright", "view": "no_preference", "desk": "compact"},
    "comfort_seeker": {"view": "window", "air_conditioning": "warm"},
    "heat_sensitive": {"air_conditioning": "cool"},
    "frequent_break": {"restroom": "near"},
    "casual_reader": {
        "laptop_usage": "no",
        "outlet": "not_required",
        "lighting": "dim",
    },
}


def pref_to_features(feat, val):
    """카카오톡 선호값 → features-1 (featureId, target, base_importance)."""
    m = {
        ("restroom", "near"): [("toiletProximity", 1.0, 0.7)],
        ("lighting", "bright"): [("brightness", 0.85, 0.7)],
        ("lighting", "dim"): [("brightness", 0.3, 0.6)],
        ("air_conditioning", "cool"): [("airflow", 0.7, 0.6)],
        ("air_conditioning", "warm"): [("airflow", 0.05, 0.7)],
        ("desk", "large"): [("deskWidth", 1.0, 0.6)],
        ("desk", "compact"): [("deskWidth", 0.3, 0.3), ("partition", 1, 0.5)],
        ("laptop_usage", "yes"): [("laptopAllowed", 1, 1.0)],
        ("outlet", "required"): [("outletAvailable", 1, 0.9)],
        ("view", "window"): [("windowProximity", 1.0, 0.6), ("viewQuality", 1.0, 0.4)],
    }
    return m.get((feat, val), [])


def make_user(i, rng, libs):
    uid = f"synth-user:{i:05d}"
    arch = rng.choice(list(ARCHETYPES))
    latent = {}
    for f, vals in PREF_VALUES.items():
        if f in ARCHETYPES[arch] and rng.random() < 0.8:
            latent[f] = ARCHETYPES[arch][f]
        else:
            latent[f] = rng.choices(vals, PREF_PRIOR[f])[0]
    if latent["laptop_usage"] == "yes" and rng.random() < 0.8:
        latent["outlet"] = "required"
    # 진짜(잠재) 효용 가중치: 표현된 선호 + 숨은 특성(입구 근접·소음 회피·칸막이)
    true_w = {}
    for f, v in latent.items():
        for fid, t, imp in pref_to_features(f, v):
            true_w[fid] = {
                "target": t,
                "weight": round(clamp(imp * rng.uniform(0.6, 1.4)), 3),
            }
    if rng.random() < 0.3:
        true_w["entranceProximity"] = {
            "target": 1.0,
            "weight": round(rng.uniform(0.2, 0.6), 3),
        }
    if "partition" not in true_w and rng.random() < 0.35:
        true_w["partition"] = {"target": 1, "weight": round(rng.uniform(0.2, 0.7), 3)}
    noise_aversion = round(rng.uniform(0, 1), 3)
    # 관측 프로필: 카카오톡 분석기가 냈을 법한 결과(누락·오추론 포함)
    text_richness = rng.uniform(0.2, 1.0)
    prefs = {}
    for f, v in latent.items():
        r = rng.random()
        if r > 0.35 + 0.6 * text_richness:
            prefs[f] = {
                "preference": "unknown",
                "confidence": 0.0,
                "inference_type": "unknown",
                "reason": "합성: 대화에 근거가 부족한 경우를 모사",
            }
            continue
        direct = rng.random() < 0.35
        wrong = rng.random() < (0.05 if direct else 0.15)
        obs = [x for x in PREF_VALUES[f] if x != v][0] if wrong else v
        conf = rng.uniform(0.7, 0.95) if direct else rng.uniform(0.35, 0.7)
        prefs[f] = {
            "preference": obs,
            "confidence": round(conf, 2),
            "inference_type": "direct" if direct else "contextual",
            "reason": "합성: 직접 언급" if direct else "합성: 맥락 단서",
            "is_correct_vs_latent": not wrong,
        }
    profile = []
    for f, p in prefs.items():
        for fid, t, imp in pref_to_features(f, p["preference"]):
            profile.append(
                {
                    "featureId": fid,
                    "target": t,
                    "importance": round(clamp(imp * (0.5 + 0.5 * p["confidence"])), 3),
                }
            )
    requirements = (
        [{"featureId": "laptopAllowed", "operator": "eq", "value": 1}]
        if prefs["laptop_usage"]["preference"] == "yes"
        and prefs["laptop_usage"]["confidence"] >= 0.6
        else []
    )
    home = rng.choice(libs)
    return {
        "_id": uid,
        "user_id": uid,
        "archetype": arch,
        "home_library_id": home,
        "secondary_library_ids": rng.sample(
            [l for l in libs if l != home], k=rng.choice([0, 0, 1, 2])
        ),
        "visit_rate_per_week": round(rng.uniform(0.5, 5), 2),
        "kakaotalk_profile": {
            "participant_id": uid,
            "preferences": prefs,
            "evidence": [],
            "behavioral_summary": f"합성 사용자({arch})",
            "seat_recommendation": None,
            "follow_up_question": "",
        },
        "preference_profile": {
            "revision": 1,
            "featureRegistryVersion": FEATURE_REGISTRY,
            "preferences": profile,
        },
        "requirements": requirements,
        "latent": {
            "preferences": latent,
            "utility_weights": true_w,
            "noise_aversion": noise_aversion,
            "rationality": round(rng.uniform(2, 8), 2),
            "rec_trust": round(rng.uniform(0.4, 0.9), 2),
        },
        "is_synthetic": True,
        "verification": "synthetic",
        "generator_version": GEN_VERSION,
    }


# ---------------------------------------------------------------- sessions / events
def true_utility(user, seat, slot):
    L = user["latent"]
    f = dict(seat["features"])
    f["brightness"] = seat["brightness_by_slot"][slot]
    if L["preferences"]["laptop_usage"] == "yes" and f["laptopAllowed"] == 0:
        return None
    u = 0.0
    for fid, tw in L["utility_weights"].items():
        u += tw["weight"] * (1 - abs(tw["target"] - f[fid]))
    u -= 0.5 * L["noise_aversion"] * seat["raw"]["noise_index"]
    return u


SAT_NOISE = 0.03  # 만족도 관측 노이즈(표준편차). 0이면 취향 적합도와 완전히 같다.


def preference_fit(user, seat, slot):
    """잠재 취향 적합도 0~1: true_utility와 같은 항목·가중치를 가중치 합으로 나눈 값.
    소음 회피는 (1-noise_index) 항목으로, 가중치 0.5*noise_aversion을 준다. 노트북 금지 좌석(노트북 사용자)은 None."""
    L = user["latent"]
    f = dict(seat["features"])
    f["brightness"] = seat["brightness_by_slot"][slot]
    if L["preferences"]["laptop_usage"] == "yes" and f["laptopAllowed"] == 0:
        return None
    a = 0.5 * L["noise_aversion"]
    num = a * (1 - seat["raw"]["noise_index"])
    den = a
    for fid, tw in L["utility_weights"].items():
        num += tw["weight"] * (1 - abs(tw["target"] - f[fid]))
        den += tw["weight"]
    return num / den if den > 0 else 0.5


def engine_score(user, seat, slot):
    """명세 6.2 featureScore (관측 프로필 기반 베이스라인 추천)."""
    prefs = user["preference_profile"]["preferences"]
    W = sum(p["importance"] for p in prefs)
    if W == 0:
        return None
    f = dict(seat["features"])
    f["brightness"] = seat["brightness_by_slot"][slot]
    return (
        sum(p["importance"] * (1 - abs(p["target"] - f[p["featureId"]])) for p in prefs)
        / W
    )


def softmax_choice(rng, items, utils, beta):
    m = max(utils)
    ws = [math.exp(beta * (x - m)) for x in utils]
    return rng.choices(items, ws)[0]


def gen_sessions(ks, users, scen_by_lib, seats_by_scen, rng, start):
    sessions, events = [], []
    for k in ks:
        user = rng.choice(users)
        lid = (
            user["home_library_id"]
            if (not user["secondary_library_ids"] or rng.random() < 0.75)
            else rng.choice(user["secondary_library_ids"])
        )
        scen = rng.choice(scen_by_lib[lid])
        seats = seats_by_scen[scen["scenario_id"]]
        slot = rng.choices(list(TIME_SLOTS), [0.35, 0.4, 0.25])[0]
        ts = start + timedelta(
            days=int(rng.uniform(0, 60)), hours=TIME_SLOTS[slot] + rng.uniform(-1, 1)
        )
        occ_rate = clamp(
            rng.betavariate(3, 3) * (1.2 if slot == "noon" else 0.9), 0.05, 0.95
        )
        # 인기 좌석(창가·콘센트·밝음)이 먼저 차도록 점유 확률을 치우침
        avail = []
        for s in seats:
            pop = (
                0.3 * s["features"]["windowProximity"]
                + 0.3 * s["features"]["outletAvailable"]
                + 0.2 * s["brightness_by_slot"][slot]
            )
            if rng.random() > clamp(occ_rate * (0.7 + pop)):
                avail.append(s)
        if not avail:
            continue
        sess_id = f"synth-session:{k:06d}"
        reqs = user["requirements"]
        elig = [
            s
            for s in avail
            if all(s["features"][r["featureId"]] == r["value"] for r in reqs)
        ]
        scored = [(engine_score(user, s, slot), s) for s in elig]
        if scored and scored[0][0] is not None:
            scored.sort(key=lambda x: (-x[0], x[1]["seat_id"]))
            basis = "preferences"
        else:
            scored = [(None, s) for s in sorted(elig, key=lambda s: s["seat_id"])]
            basis = "unpersonalized"
        topk = scored[:5]
        exposed = [
            {"seat_id": s["seat_id"], "rank": i + 1, "engine_score": r3(sc)}
            for i, (sc, s) in enumerate(topk)
        ]
        # 사용자 선택: 추천 신뢰에 따라 추천 내 또는 전체 가용 좌석 중 MNL
        cand_util = [(s, true_utility(user, s, slot)) for s in avail]
        cand_util = [(s, x) for s, x in cand_util if x is not None]
        L = user["latent"]
        chosen, origin = None, None
        if cand_util:
            exp_ids = {e["seat_id"] for e in exposed}
            if exposed and rng.random() < L["rec_trust"]:
                pool = [
                    (
                        s,
                        x
                        + 0.08
                        * (
                            6
                            - next(
                                e["rank"]
                                for e in exposed
                                if e["seat_id"] == s["seat_id"]
                            )
                        ),
                    )
                    for s, x in cand_util
                    if s["seat_id"] in exp_ids
                ]
                origin = "recommendation"
            else:
                pool = cand_util
                origin = None
            if pool:
                chosen = softmax_choice(
                    rng, [p[0] for p in pool], [p[1] for p in pool], L["rationality"]
                )
                if origin is None:
                    origin = (
                        "recommendation"
                        if chosen["seat_id"] in exp_ids
                        else "outsideRecommendations"
                    )
        others = [
            s["seat_id"]
            for s in avail
            if not chosen or s["seat_id"] != chosen["seat_id"]
        ]
        negs = rng.sample(others, min(20, len(others)))
        satisfaction = fit = None
        if (
            chosen
        ):  # 선택 좌석이 잠재 취향에 맞을수록 높다(절대값, 다른 빈자리와 비교하지 않음)
            fit = preference_fit(user, chosen, slot)
            satisfaction = r3(clamp(fit + rng.gauss(0, SAT_NOISE), 0, 1))
        sess = {
            "_id": sess_id,
            "session_id": sess_id,
            "user_id": user["user_id"],
            "library_id": lid,
            "scenario_id": scen["scenario_id"],
            "room_id": scen["room_id"],
            "time_slot": slot,
            "occurred_at": ts.isoformat(),
            "occupancy_rate": r3(1 - len(avail) / len(seats)),
            "available_seat_ids_count": len(avail),
            "eligible_count": len(elig),
            "ranking_basis": basis,
            "occupied_seat_ids": sorted(
                set(s["seat_id"] for s in seats) - set(s["seat_id"] for s in avail)
            ),
            "exposed": exposed,
            "chosen_seat_id": chosen["seat_id"] if chosen else None,
            "choice_origin": origin,
            "chosen_true_utility": r3(true_utility(user, chosen, slot))
            if chosen
            else None,
            "negative_seat_ids": negs,
            "chosen_preference_fit": r3(fit),
            "satisfaction": satisfaction,
            "is_synthetic": True,
            "verification": "synthetic",
            "generator_version": GEN_VERSION,
        }
        sessions.append(sess)
        ev = lambda typ, dt, **kw: events.append(
            {
                "_id": f"{sess_id}:{typ}",
                "event_id": f"{sess_id}:{typ}",
                "session_id": sess_id,
                "user_id": user["user_id"],
                "type": typ,
                "occurred_at": (ts + timedelta(seconds=dt)).isoformat(),
                "is_synthetic": True,
                **kw,
            }
        )
        if exposed:
            ev(
                "recommendationExposed",
                0,
                visibleTargetIds=[e["seat_id"] for e in exposed],
            )
        if chosen:
            ev(
                "candidateSelected",
                rng.uniform(5, 60),
                targetId=chosen["seat_id"],
                origin=origin,
            )
            if rng.random() < 0.7:
                ev("useConfirmed", rng.uniform(120, 600), targetId=chosen["seat_id"])
            if rng.random() < 0.4:
                ev(
                    "ratingSubmitted",
                    rng.uniform(1800, 10800),
                    targetId=chosen["seat_id"],
                    satisfaction=satisfaction,
                )
        if exposed and rng.random() < 0.05:
            ev(
                "candidateRejected",
                rng.uniform(5, 40),
                targetId=exposed[0]["seat_id"],
                reason="synthetic_dislike",
                excludeFromFuture=False,
            )
    return sessions, events


def write_jsonl(name, rows):
    with open(OUT / f"{name}.jsonl", "w", encoding="utf-8") as f:
        for r in tqdm(
            rows, desc=f"저장 {name}", unit="행", leave=False, dynamic_ncols=True
        ):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 병렬 작업 단위
CHUNK = 1000  # 세션 작업 단위(건). 바꾸면 같은 seed라도 결과가 달라진다.
_G = {}  # fork 로 워커에 물려줄 읽기 전용 공유 데이터


def _unit_job(args):
    i, u, seed, n_scen = args
    rng = random.Random(f"{seed}:unit:{i}")
    build_unit_static(u)
    scen, seats = [], []
    for k in range(n_scen):
        sc, ss = synth_scenario(u, k, rng)
        scen.append(sc)
        seats.extend(ss)
    return scen, seats


def _session_job(c):
    """c번째 세션 묶음 → (세션 JSON 줄들, 이벤트 JSON 줄들, 시도 건수). 직렬화도 워커에서 한다."""
    g = _G
    k0 = c * CHUNK
    ks = range(k0, min(k0 + CHUNK, g["n"]))
    rng = random.Random(f"{g['seed']}:sessions:{c}")
    sess, evs = gen_sessions(
        ks, g["users"], g["scen_by_lib"], g["seats_by_scen"], rng, g["start"]
    )
    dump = lambda r: json.dumps(r, ensure_ascii=False) + "\n"
    return [dump(r) for r in sess], [dump(r) for r in evs], len(ks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=30)
    ap.add_argument("--users", type=int, default=3000)
    ap.add_argument("--sessions", type=int, default=60000)
    ap.add_argument("--seed", type=int, default=20261114)
    ap.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 1,
        help="프로세스 수(기본: CPU 코어 수)",
    )
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    ctx = mp.get_context("fork")
    libs, units = load_units()
    scen_rows, seat_rows = [], []
    with ctx.Pool(min(a.workers, len(units))) as pool:
        jobs = [(i, u, a.seed, a.scenarios) for i, u in enumerate(units)]
        for sc, ss in tqdm(
            pool.imap(_unit_job, jobs),
            total=len(jobs),
            desc="열람실 환경",
            unit="실",
            dynamic_ncols=True,
        ):
            scen_rows.extend(sc)
            seat_rows.extend(ss)
    urng = random.Random(f"{a.seed}:users")
    users = [
        make_user(i, urng, libs)
        for i in tqdm(range(a.users), desc="사용자", unit="명", dynamic_ncols=True)
    ]
    scen_by_lib, seats_by_scen = {}, {}
    for sc in scen_rows:
        scen_by_lib.setdefault(sc["library_id"], []).append(sc)
    for s in seat_rows:
        seats_by_scen.setdefault(s["scenario_id"], []).append(s)
    write_jsonl("synth_scenarios", scen_rows)
    write_jsonl("synth_seat_env", seat_rows)
    write_jsonl("synth_users", users)
    _G.update(
        n=a.sessions,
        seed=a.seed,
        users=users,
        scen_by_lib=scen_by_lib,
        seats_by_scen=seats_by_scen,
        start=datetime(2026, 9, 1, tzinfo=KST),
    )
    n_sess = n_ev = 0
    with (
        ctx.Pool(a.workers) as pool,
        open(OUT / "synth_sessions.jsonl", "w", encoding="utf-8") as fs,
        open(OUT / "synth_events.jsonl", "w", encoding="utf-8") as fe,
        tqdm(
            total=a.sessions,
            desc=f"세션({a.workers}프로세스)",
            unit="건",
            dynamic_ncols=True,
        ) as bar,
    ):
        for sl, el, tried in pool.imap(
            _session_job, range(math.ceil(a.sessions / CHUNK))
        ):  # 순서 유지 → 파일 결정적
            fs.writelines(sl)
            fe.writelines(el)
            n_sess += len(sl)
            n_ev += len(el)
            bar.update(tried)
    manifest = {
        "generator_version": GEN_VERSION,
        "seed": a.seed,
        "params": {k: v for k, v in vars(a).items() if k != "workers"},
        "session_chunk": CHUNK,
        "feature_registry_version": FEATURE_REGISTRY,
        "libraries": libs,
        "room_units": len(units),
        "counts": {
            "synth_scenarios": len(scen_rows),
            "synth_seat_env": len(seat_rows),
            "synth_users": len(users),
            "synth_sessions": n_sess,
            "synth_events": n_ev,
        },
        "assumptions": {
            "seat_button_side_m": SEAT_SIDE_M,
            "time_slots_hour": TIME_SLOTS,
            "note": "모든 환경 객체·측정값·사용자·이벤트는 합성. 실좌석 좌표/ID와 같은 지도에 있는 실제 출입구·화장실 후보만 실데이터에서 가져옴(origin 필드).",
        },
        "generated_at": datetime.now(KST).isoformat(),
    }
    json.dump(
        manifest,
        open(OUT / "manifest.json", "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
