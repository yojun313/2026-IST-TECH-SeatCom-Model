"""도서관·열람실·좌석 추천 서비스: 학습한 모델(seat_model/models/<버전>)을 백엔드에 통합한다.

대상: 좌석도·환경 데이터가 있는 11개 도서관(열람실 24개). 위치와 관계없이 사용자가 도서관을 고른다.

엔드포인트(main.py에서 연결)
  GET  /api/v1/libraries                          도서관 목록(지역·열람실 수·잔여석·오늘 운영시간)
  GET  /api/v1/libraries/{libraryId}/rooms        열람실 목록(층·좌석 수·노트북 규정·잔여석·운영시간)
  GET  /api/v1/rooms/{roomId}/map                 좌석도(0~1 정규화 좌표) + 좌석별 특징 + 시설 레이어
  GET  /api/v1/rooms/{roomId}/availability        좌석 현황
  POST /api/v1/recommendations/rooms              내 취향에 맞는 열람실 추천(선택한 도서관 안 또는 전체)
  POST /api/v1/recommendations/seats              내 취향 좌석 추천
  POST /api/v1/recommendations/{id}/events        노출·선택(추천 안/밖)·거절·이용 확정·이용 종료·평가
  GET  /api/v1/users/me/preferences               내 취향(직접 설정 + 출처별 요약)
  PUT  /api/v1/users/me/preferences               직접 취향 설정(revision 충돌 검사)
  GET  /api/v1/users/me/preferences/insights      착석 기록으로 파악한 내 취향과 근거
  GET  /api/v1/users/me/history                   이용 중인 좌석과 이용 기록

취향 합치기(뒤가 우선)
  설문 4문항 → 카카오톡 실험실 분석(to_profile) → 직접 설정
  - 모델의 관측 프로필 특징(8개)은 preference_profile로, 그 밖의 항목(칸막이·조용함·출입구)은
    직접 설정이면 고정 가산(bias), 설문이면 개인 계수 μ_u의 초기값이 된다.
  - 착석할 때마다 μ_u가 갱신된다(온라인 학습). 평가(별점)는 저장만 하고 학습에 쓰지 않는다.
가상 데이터(실데이터가 없는 부분, 응답에 표시)
  - 좌석 현황: 10분 단위 결정적 모의 현황(source: "simulated"). 서비스 사용자가 고른 좌석은 이용 종료 전까지(최대 3시간) 사용 중.
  - 좌석 환경 특징: v1 합성 환경(synth_seat_env)의 계절·맑음 시나리오.
  - 운영시간이 없는 열람실은 09:00–22:00(가상), 층 정보가 없으면 null.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .security import api_error
from .store import Store, now_iso

log = logging.getLogger("seatcom.recommend")
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
KST = ZoneInfo("Asia/Seoul")
REC_TTL_MIN = 10
SELECTION_HOLD_H = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS reco_recommendations (
 recommendation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, room_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, requested_at TEXT NOT NULL, time_slot TEXT NOT NULL,
 occupied_json TEXT NOT NULL, ranked_json TEXT NOT NULL, profile_json TEXT NOT NULL, exposed_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_reco_user ON reco_recommendations(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS reco_events (
 event_id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL, user_id TEXT NOT NULL, type TEXT NOT NULL,
 payload_json TEXT NOT NULL, effect TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reco_selections (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, room_id TEXT NOT NULL, seat_id TEXT NOT NULL,
 recommendation_id TEXT NOT NULL, selected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reco_sel_room ON reco_selections(room_id, selected_at);
CREATE TABLE IF NOT EXISTS reco_exclusions (
 user_id TEXT NOT NULL, room_id TEXT NOT NULL, seat_id TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(user_id, room_id, seat_id)
);
CREATE TABLE IF NOT EXISTS reco_user_state (
 user_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_preferences (
 user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, prefs_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""
# 기존 DB에 없을 수 있는 열(이전 배포 호환)
SELECTION_COLUMNS = {
    "origin": "TEXT",
    "confirmed_at": "TEXT",
    "ended_at": "TEXT",
    "rating": "INTEGER",
    "rating_comment": "TEXT",
}

# 페르소나 답 → 관측 프로필(featureId, target, importance)
PERSONA_PREFS = {
    ("outlet", "required"): [("outletAvailable", 1, 0.9), ("laptopAllowed", 1, 0.6)],
    ("hvac_proximity", "near"): [("airflow", 0.7, 0.6)],
    ("hvac_proximity", "not_near"): [("airflow", 0.05, 0.6)],
}
# 페르소나 답 → 개인 계수 초기값(표준화 단위; 학습된 μ의 90% 분위 ≈ 0.15)
PERSONA_PRIOR = {
    ("noise_sensitivity", "high"): ("noise_index", -0.15),
    ("noise_sensitivity", "low"): ("noise_index", -0.08),
    ("partition", "yes"): ("partition", 0.15),
    ("partition", "no"): ("partition", -0.08),
}

# 직접 설정 항목. kind=profile: 모델 관측 프로필 특징(target), kind=bias: 고정 가산(부호)
PREF_CATALOG = [
    {
        "featureId": "outletAvailable",
        "label": "콘센트",
        "kind": "profile",
        "choices": [
            {"value": "need", "label": "있는 자리", "target": 1},
            {"value": "any", "label": "상관없음"},
        ],
    },
    {
        "featureId": "windowProximity",
        "label": "창가",
        "kind": "profile",
        "choices": [
            {"value": "near", "label": "창가 쪽", "target": 1.0},
            {"value": "any", "label": "상관없음"},
            {"value": "far", "label": "안쪽", "target": 0.0},
        ],
    },
    {
        "featureId": "brightness",
        "label": "밝기",
        "kind": "profile",
        "choices": [
            {"value": "bright", "label": "밝은 자리", "target": 0.85},
            {"value": "any", "label": "상관없음"},
            {"value": "dim", "label": "은은한 자리", "target": 0.3},
        ],
    },
    {
        "featureId": "airflow",
        "label": "에어컨 바람",
        "kind": "profile",
        "choices": [
            {"value": "strong", "label": "시원한 자리", "target": 0.7},
            {"value": "any", "label": "상관없음"},
            {"value": "weak", "label": "바람 약한 자리", "target": 0.05},
        ],
    },
    {
        "featureId": "deskWidth",
        "label": "책상",
        "kind": "profile",
        "choices": [
            {"value": "wide", "label": "넓은 책상", "target": 1.0},
            {"value": "any", "label": "상관없음"},
            {"value": "compact", "label": "작아도 괜찮음", "target": 0.3},
        ],
    },
    {
        "featureId": "toiletProximity",
        "label": "화장실",
        "kind": "profile",
        "choices": [
            {"value": "near", "label": "가까운 자리", "target": 1.0},
            {"value": "any", "label": "상관없음"},
        ],
    },
    {
        "featureId": "viewQuality",
        "label": "전망",
        "kind": "profile",
        "choices": [
            {"value": "good", "label": "전망 좋은 자리", "target": 1.0},
            {"value": "any", "label": "상관없음"},
        ],
    },
    {
        "featureId": "partition",
        "label": "칸막이",
        "kind": "bias",
        "choices": [
            {"value": "yes", "label": "칸막이 있는 자리", "sign": 1},
            {"value": "any", "label": "상관없음"},
            {"value": "no", "label": "트인 자리", "sign": -1},
        ],
    },
    {
        "featureId": "noise_index",
        "label": "조용함",
        "kind": "bias",
        "choices": [
            {"value": "quiet", "label": "조용한 자리", "sign": -1},
            {"value": "any", "label": "상관없음"},
        ],
    },
    {
        "featureId": "entranceProximity",
        "label": "출입구",
        "kind": "bias",
        "choices": [
            {"value": "near", "label": "출입구 가까이", "sign": 1},
            {"value": "any", "label": "상관없음"},
            {"value": "far", "label": "출입구에서 멀리", "sign": -1},
        ],
    },
]
CATALOG = {c["featureId"]: c for c in PREF_CATALOG}
PROFILE_IMPORTANCE = {1: 0.3, 2: 0.6, 3: 0.9}
BIAS_SIZE = {1: 0.08, 2: 0.15, 3: 0.25}
REJECT_REASONS = {
    "tooNoisy",
    "tooBright",
    "tooDark",
    "tooCold",
    "tooHot",
    "noOutlet",
    "tooCrowded",
    "tooFar",
    "other",
}
# 거절 이유 → (특징, 방향). 명세 코드와 화면 문구 둘 다 받는다
REJECT_SIGNAL = [
    (("tooCold", "추울"), "airflow", -1),
    (("tooHot", "더울"), "airflow", 1),
    (("tooNoisy", "시끄러"), "noise_index", -1),
    (("tooCrowded", "붐빌", "북적"), "noise_index", -1),
    (("noOutlet", "콘센트가 없"), "outletAvailable", 1),
    (("창가가 아니",), "windowProximity", 1),
    (("tooFar", "입구에서 너무 멀"), "entranceProximity", 1),
    (("tooBright", "너무 밝"), "brightness", -1),
    (("tooDark", "어두"), "brightness", 1),
    (("칸막이가 없",), "partition", 1),
    (("책상이 좁",), "deskWidth", 1),
]
REJECT_STEP = (
    0.10  # 거절 이유 1개당 개인 계수 이동(표준화 단위). 학습된 계수의 90% 분위 ≈ 0.15
)
REJECT_CAP = 0.5  # 이유로 옮길 수 있는 최대 크기
FORGET = 0.97  # 선택마다 과거 확신(정밀도)을 3%씩 줄여 최근 선택에 더 무게를 둔다(취향 변화 추적)
HIST_DECAY = 0.85  # 이력 특징(드러난 취향 누적·자리별 습관)도 선택마다 감쇠: 4번 전 선택의 무게 ≈ 절반

# 인사이트에 보여줄 좌석 특징(binary = 0/1 특징)
INSIGHT_FEATURES = [
    ("outletAvailable", "콘센트 있는 자리", True),
    ("partition", "칸막이 자리", True),
    ("windowProximity", "창가에 가까운 자리", False),
    ("brightness", "밝은 자리", False),
    ("airflow", "에어컨 바람이 센 자리", False),
    ("deskWidth", "넓은 책상", False),
    ("noise_index", "시끄러운 자리", False),
    ("toiletProximity", "화장실 가까운 자리", False),
    ("entranceProximity", "출입구 가까운 자리", False),
    ("viewQuality", "전망 좋은 자리", False),
]
REGION = {"yongin": "경기 용인", "seongnam": "경기 성남", "nl": "서울"}
DESK_KO = {
    "individual_carrel": "1인 칸막이 책상",
    "long_shared_table": "긴 공용 테이블",
    "four_person_table": "4인 테이블",
    "wide_individual_desk": "넓은 1인 책상",
    "window_counter": "창가 바 테이블",
    "standing_desk": "스탠딩 책상",
}


def _season(month):
    return (
        "summer"
        if month in (6, 7, 8)
        else "winter"
        if month in (12, 1, 2)
        else "spring_fall"
    )


def _slot(hour):
    return "morning" if hour < 12 else "noon" if hour < 17 else "evening"


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _level(v, words, cuts=(0.66, 0.33)):
    """0~1 값을 말로: 높음/중간/낮음"""
    return words[0] if v >= cuts[0] else words[1] if v >= cuts[1] else words[2]


def _desk_size_word(width_cm):
    w = width_cm or 90
    return "넓은 책상" if w >= 120 else "보통 크기 책상" if w >= 85 else "아담한 책상"


def _hours_today(opening_hours, now: datetime):
    """오늘 운영시간. weekly[].day는 0=월요일로 본다(토·일 단축 운영 열람실 기준). 자료가 없으면 가상 09:00–22:00."""
    virtual = not opening_hours or not (opening_hours.get("weekly"))
    weekly = (opening_hours or {}).get("weekly") or [
        {"day": d, "opens": "09:00", "closes": "22:00"} for d in range(7)
    ]
    day = next((d for d in weekly if d.get("day") == now.weekday()), None)
    if not day or not day.get("opens"):
        return {
            "today": None,
            "openNow": False,
            "closedToday": True,
            "virtual": virtual,
        }
    hm = now.strftime("%H:%M")
    return {
        "today": f"{day['opens']}–{day['closes']}",
        "openNow": day["opens"] <= hm < day["closes"],
        "closedToday": False,
        "virtual": virtual,
    }


class Room:
    """열람실 정적 정보(좌석도·환경 특징·규정). SQLite에서 읽기만 한다."""

    def __init__(self, db, room_id, month):
        scen = list(
            db.synth_scenarios.find(
                {"room_id": room_id},
                {
                    "scenario_id": 1,
                    "scenario_index": 1,
                    "conditions": 1,
                    "map_id": 1,
                    "library_id": 1,
                    "room_name": 1,
                    "library_name": 1,
                    "objects": 1,
                    "desk_groups": 1,
                    "room_bounds_px": 1,
                    "synthetic_meters_per_px": 1,
                },
            )
        )
        if not scen:
            raise KeyError(room_id)
        season = _season(month)
        key = lambda s: (
            s["conditions"]["season"] != season,
            s["conditions"]["weather"] != "clear",
            int(s["scenario_index"]),
        )
        sc = sorted(scen, key=key)[0]
        self.room_id, self.scenario_id, self.map_id = (
            room_id,
            sc["scenario_id"],
            sc["map_id"],
        )
        self.library_id = sc["library_id"]
        self.conditions = {
            k: sc["conditions"][k] for k in ("season", "weather", "base_temperature_c")
        }
        self.laptop_allowed = bool(sc["conditions"].get("laptop_allowed"))
        self.laptop_rule_origin = sc["conditions"].get("laptop_rule_origin")
        lib = (
            db.libraries.find_one(
                {"library_id": self.library_id}, {"name": 1, "address": 1}
            )
            or {}
        )
        room = (
            db.rooms.find_one(
                {"room_id": room_id},
                {"name": 1, "floor_id": 1, "capacity": 1, "opening_hours": 1},
            )
            or {}
        )
        self.library_name = lib.get("name") or sc.get("library_name") or self.library_id
        self.address = lib.get("address")
        self.room_name = room.get("name") or sc.get("room_name") or room_id
        self.floor = room.get("floor_id")
        self.opening_hours = room.get("opening_hours")
        m = (
            db.maps.find_one(
                {"map_id": self.map_id}, {"width": 1, "height": 1, "verification": 1}
            )
            or {}
        )
        self.width, self.height = (
            float(m.get("width") or 1000),
            float(m.get("height") or 700),
        )
        env = {
            d["seat_id"]: d
            for d in db.synth_seat_env.find({"scenario_id": self.scenario_id})
        }
        seats = {
            d["seat_id"]: d
            for d in db.seats.find(
                {"room_id": room_id, "dataset_status": "active"},
                {
                    "seat_id": 1,
                    "seat_label": 1,
                    "bbox": 1,
                    "norm_bbox": 1,
                    "verification": 1,
                },
            )
        }
        self.seats = []  # inference snapshot 형식
        self.map_seats = []  # 프론트 좌석도 형식
        self.label = {}
        self.feat = {}  # 좌석별 표시용 특징
        self.raw = {}  # 좌석별 합성 원값(거리 m, lux, 책상 cm 등) — 설명 문장에만 쓰고 숫자는 내보내지 않는다
        self.bbox_px = {}
        self.objects = sc.get("objects") or []
        self.desk_groups = sc.get("desk_groups") or []
        self.bounds = sc.get("room_bounds_px") or [0, 0, self.width, self.height]
        self.mpp = float(sc.get("synthetic_meters_per_px") or 0.025)
        for sid in sorted(set(env) & set(seats)):
            e, s = env[sid], seats[sid]
            self.seats.append(
                {
                    "seat_id": sid,
                    "features": e["features"],
                    "brightness_by_slot": e["brightness_by_slot"],
                    "noise_index": e["raw"]["noise_index"],
                    "desk_type": e["raw"]["desk_type"],
                    "desk_group": e["raw"]["desk_group"],
                }
            )
            nb = s.get("norm_bbox")
            if nb:
                box = {"x": nb[0], "y": nb[1], "width": nb[2], "height": nb[3]}
            else:
                b = s["bbox"]
                box = {
                    "x": b["x"] / self.width,
                    "y": b["y"] / self.height,
                    "width": b["width"] / self.width,
                    "height": b["height"] / self.height,
                }
            f = e["features"]
            self.raw[sid] = e["raw"]
            self.bbox_px[sid] = s["bbox"]
            self.feat[sid] = {
                "outletAvailable": f["outletAvailable"],
                "partition": f["partition"],
                "windowProximity": f["windowProximity"],
                "brightness": e["brightness_by_slot"],
                "airflow": f["airflow"],
                "deskWidth": f["deskWidth"],
                "noise_index": e["raw"]["noise_index"],
                "toiletProximity": f["toiletProximity"],
                "entranceProximity": f["entranceProximity"],
                "viewQuality": f["viewQuality"],
                "laptopAllowed": f["laptopAllowed"],
            }
            tags = []
            if f["outletAvailable"]:
                tags.append("콘센트")
            if f["partition"]:
                tags.append("칸막이")
            if f["windowProximity"] >= 0.6:
                tags.append("창가")
            if e["raw"]["noise_index"] <= 0.25:
                tags.append("조용함")
            if f["deskWidth"] >= 0.6:
                tags.append("넓은 책상")
            raw_label = s.get("seat_label") or sid.rsplit(":", 1)[-1]
            self.label[sid] = (
                str(int(raw_label)) if raw_label.isdigit() else raw_label
            )  # 0139 → 139
            self.map_seats.append(
                {
                    "seatId": sid,
                    "label": self.label[sid],
                    **box,
                    "verification": "verified"
                    if s.get("verification") == "verified"
                    else "unverified",
                    "deskType": DESK_KO.get(
                        e["raw"]["desk_type"], e["raw"]["desk_type"]
                    ),
                    "tags": tags,
                }
            )
        self.box = {
            m["seatId"]: {k: m[k] for k in ("x", "y", "width", "height")}
            for m in self.map_seats
        }
        self.pop = {
            d["seat_id"]: 0.3 * d["features"]["windowProximity"]
            + 0.3 * d["features"]["outletAvailable"]
            + 0.2 * d["brightness_by_slot"]["noon"]
            for d in self.seats
        }
        self.facilities = self._facilities(db, sc.get("objects") or [])
        self.real_facilities = [
            {
                "type": f.get("type"),
                "geometry": f.get("geometry"),
                "label": (f.get("attributes") or {}).get("label"),
            }
            for f in sorted(db.facilities.find(
                {"map_id": self.map_id, "dataset_status": "active"}
            ), key=lambda item: item["facility_id"])
        ]
        self._floor_cache = None

    def floor_plan(self):
        """평면도(지도 픽셀 좌표): 책상 묶음·좌석·창문·냉방기·조명·콘센트·출입구·화장실. 환경 객체는 합성이다."""
        if self._floor_cache is not None:
            return self._floor_cache
        o = lambda t: [x for x in self.objects if x.get("type") == t]
        M = 1 / self.mpp  # 1 m = M px
        groups = []
        for g in self.desk_groups:
            boxes = [self.bbox_px[sid] for sid in g["seat_ids"] if sid in self.bbox_px]
            if not boxes:
                continue
            x0, y0 = min(b["x"] for b in boxes), min(b["y"] for b in boxes)
            x1, y1 = (
                max(b["x"] + b["width"] for b in boxes),
                max(b["y"] + b["height"] for b in boxes),
            )
            groups.append(
                {
                    "id": g["desk_group"],
                    "deskType": g["desk_type"],
                    "deskTypeLabel": DESK_KO.get(g["desk_type"], g["desk_type"]),
                    "box": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
                    "seatIds": [s for s in g["seat_ids"] if s in self.bbox_px],
                    "partition": bool(g.get("partition")),
                    "deskOutlet": bool(g.get("desk_outlet")),
                    "size": _desk_size_word(g.get("width_cm")),
                }
            )
        x0, y0, x1, y1 = self.bounds

        def edge(pt):  # 지도 밖 객체(화장실 등)는 가장 가까운 벽 위치에 화살표로 표시
            x, y = pt
            inside = 0 <= x <= self.width and 0 <= y <= self.height
            return {
                "point": [min(max(x, x0), x1), min(max(y, y0), y1)],
                "outside": not inside,
                "direction": [
                    (-1 if x < x0 else 1 if x > x1 else 0),
                    (-1 if y < y0 else 1 if y > y1 else 0),
                ],
            }

        self._floor_cache = {
            "roomId": self.room_id,
            "mapId": self.map_id,
            "width": self.width,
            "height": self.height,
            "roomBounds": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
            "pxPerMeter": M,
            "seats": [
                {
                    "seatId": sid,
                    "label": self.label[sid],
                    "box": self.bbox_px[sid],
                    "deskGroup": self.raw[sid]["desk_group"],
                    "partition": bool(self.raw[sid]["partition"]),
                    "deskOutlet": bool(self.raw[sid]["desk_outlet"]),
                }
                for sid in self.bbox_px
            ],
            "deskGroups": groups,
            "windows": [
                {
                    "line": w["line"],
                    "wall": w.get("wall"),
                    "blindClosed": bool(w.get("blind_closed")),
                    "view": _level(
                        w.get("view_score", 0),
                        (
                            "전망이 트여 있어요",
                            "전망이 보통이에요",
                            "전망이 가려져 있어요",
                        ),
                    ),
                }
                for w in o("window")
            ],
            "airConditioners": [
                {
                    "point": a["point"],
                    "kind": a.get("kind"),
                    "direction": a.get("direction"),
                    "reachPx": a.get("throw_m", 4) * M,
                    "strength": float(a.get("strength", 0.6)),
                    "strengthWord": "바람이 센 편"
                    if a.get("strength", 0) >= 0.7
                    else "바람이 보통"
                    if a.get("strength", 0) >= 0.4
                    else "바람이 약한 편",
                }
                for a in o("air_conditioner")
            ],
            "lights": [
                {"point": l["point"], "dim": l.get("lux_at_desk_below", 500) < 300}
                for l in o("ceiling_light")
            ],
            "outlets": [
                {"point": c["point"], "kind": c.get("kind", "wall")}
                for c in o("outlet")
            ],
            "entrances": [
                {
                    **edge(e["point"]),
                    "wall": e.get("wall"),
                    "real": e.get("origin") != "synthetic",
                }
                for e in o("entrance")
            ],
            "toilets": [
                {**edge(t["point"]), "real": t.get("origin") != "synthetic"}
                for t in o("toilet")
            ],
            "realFacilities": self.real_facilities,
            "conditions": self.conditions,
            "virtual": True,
        }
        return self._floor_cache

    def _facilities(self, db, objects):
        out = []
        W, H = self.width, self.height

        def bbox(xs, ys, min_w=0.012, min_h=0.012):
            """픽셀 좌표 → 0~1 상자. 점·선은 최소 크기를 주고 가운데 정렬한다."""
            x0, x1, y0, y1 = min(xs) / W, max(xs) / W, min(ys) / H, max(ys) / H
            w, h = max(x1 - x0, min_w), max(y1 - y0, min_h)
            x = _clamp((x0 + x1) / 2 - w / 2, 0, 1 - w)
            y = _clamp((y0 + y1) / 2 - h / 2, 0, 1 - h)
            return {"x": x, "y": y, "width": w, "height": h}

        for f in sorted(db.facilities.find(
            {"map_id": self.map_id, "dataset_status": "active"}
        ), key=lambda item: item["facility_id"]):
            g = f.get("geometry") or {}
            c = g.get("coordinates")
            t = g.get("type")
            if t == "Point":
                pts = [c]
            elif t == "Polygon" and c and isinstance(c[0][0], (list, tuple)):
                pts = c[0]  # GeoJSON 형식 [[[x, y], ...]]
            else:
                pts = c  # LineString 또는 [[x, y], ...] 형식 Polygon
            try:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            except (TypeError, IndexError):
                continue
            out.append(
                {
                    "facilityId": f["facility_id"],
                    "type": f.get("type", "other"),
                    "geometry": bbox(xs, ys),
                    "verification": "unverified",
                }
            )
        # 좌석 특징을 만든 합성 환경 객체(창문·출입구·화장실·콘센트·냉방기)도 함께 보여준다
        for i, o in enumerate(objects):
            t = o.get("type")
            if t not in ("window", "entrance", "toilet", "outlet", "air_conditioner"):
                continue
            if "line" in o:
                (ax, ay), (bx, by) = o["line"]
                xs, ys = [ax, bx], [ay, by]
            elif "point" in o:
                xs, ys = [o["point"][0]], [o["point"][1]]
            else:
                continue
            size = 0.008 if t == "outlet" else 0.014
            out.append(
                {
                    "facilityId": f"{self.scenario_id}:obj:{i}",
                    "type": t,
                    "geometry": bbox(xs, ys, size, size),
                    "verification": "unverified",
                }
            )
        return out

    def seat_name(self, sid):
        return f"{self.room_name} {self.label.get(sid, sid)}번"

    def group_of(self, sid):
        """같은 책상 묶음의 좌석들"""
        g = self.raw[sid]["desk_group"]
        return [s for s, r in self.raw.items() if r["desk_group"] == g]

    def summary(self, occupied, now):
        free = len(self.seats) - len(occupied)
        return {
            "roomId": self.room_id,
            "libraryId": self.library_id,
            "name": self.room_name,
            "floor": self.floor,
            "totalSeats": len(self.seats),
            "remainingSeats": free,
            "laptopAllowed": self.laptop_allowed,
            "laptopRuleSource": "official"
            if self.laptop_rule_origin == "real_usage_rules"
            else "virtual",
            "hours": _hours_today(self.opening_hours, now),
            "seatRecommendation": True,
        }


class RecommendationService:
    def __init__(self, settings, store: Store | None = None):
        self.settings = settings
        self.store = store or Store(settings.database_path)
        with self.store.db() as db:
            db.executescript(SCHEMA)
            cols = {r["name"] for r in db.execute("PRAGMA table_info(reco_selections)")}
            for c, t in SELECTION_COLUMNS.items():
                if c not in cols:
                    db.execute(f"ALTER TABLE reco_selections ADD COLUMN {c} {t}")
        self.lock = threading.RLock()
        self.rooms: dict[str, Room] = {}
        self.rec = None
        self.error = None
        self.model_dir = Path(settings.model_dir)
        self._catalog = None
        self._room_ids = None
        self._touched: set[str] = (
            set()
        )  # 거절 이유로 개인 계수를 보정한 사용자(설문 초기값으로 되돌리지 않음)

    # ---------------------------------------------------------------- 모델·데이터 로드
    def load(self):
        self._db()  # Seed failures must fail startup, not silently serve empty rooms.
        try:
            from seat_model.train.inference import SeatRecommender
            from seat_model.train.online import OnlineState

            self.rec = SeatRecommender.load(
                self.model_dir, state_file="__none__.json"
            )  # 실제 사용자 상태는 DB에서
            self.rec.online = OnlineState(
                self.rec.online.k, self.rec.cfg_online["prior_var"]
            )
            self.manifest = json.loads(
                (self.model_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.error = None
            log.info("seat model loaded: %s", self.manifest.get("model_version"))
        except Exception as e:  # 모델이 없어도 나머지 API는 동작
            log.exception("seat model load failed")
            self.rec, self.error = None, f"{type(e).__name__}: {e}"

    def preload_rooms(self):
        """열람실 정보를 미리 읽어 첫 요청 지연을 없앤다(백그라운드)."""

        def run():
            try:
                for rid in self.room_ids():
                    self.room(rid)
                log.info("rooms preloaded: %d", len(self.rooms))
            except Exception:
                log.exception("room preload failed")

        threading.Thread(target=run, daemon=True, name="room-preload").start()

    def _db(self):
        if self._catalog is None:
            from .catalog import Catalog

            self._catalog = Catalog(self.settings.database_path)
        return self._catalog

    def room_ids(self):
        if self._room_ids is None:
            self._room_ids = sorted(self._db().synth_scenarios.distinct("room_id"))
        return self._room_ids

    def room(self, room_id) -> Room:
        month = datetime.now(KST).month
        r = self.rooms.get(room_id)
        if r is None or r.conditions["season"] != _season(
            month
        ):  # 계절이 바뀌면 시나리오를 다시 고른다
            if room_id not in self.room_ids():
                api_error(
                    404, "ROOM_NOT_FOUND", "좌석 추천을 지원하지 않는 열람실입니다."
                )
            with self.lock:
                r = Room(self._db(), room_id, month)
                self.rooms[room_id] = r
        return r

    def require_model(self):
        if self.rec is None:
            api_error(503, "MODEL_UNAVAILABLE", "추천 모델을 불러오지 못했습니다.")

    def status(self):
        return {
            "loaded": self.rec is not None,
            "error": self.error,
            "model_version": getattr(self, "manifest", {}).get("model_version"),
            "base_variant": getattr(self, "manifest", {}).get("base_variant"),
            "rooms_cached": len(self.rooms),
        }

    # ---------------------------------------------------------------- 현황(모의)
    def occupied(self, room: Room, now: datetime):
        import numpy as np

        window = int(now.timestamp() // 600)
        seed = int(
            hashlib.sha1(f"{room.room_id}:{window}".encode()).hexdigest()[:12], 16
        )
        rng = np.random.default_rng(seed)
        h = now.astimezone(KST).hour
        base = rng.beta(3, 3) * (1.2 if 12 <= h < 17 else 0.9 if 9 <= h < 22 else 0.4)
        rate = _clamp(base, 0.05, 0.9)
        occ = {
            s["seat_id"]
            for s in room.seats
            if rng.random() < _clamp(rate * (0.7 + room.pop[s["seat_id"]]))
        }
        return occ | self._held(room.room_id, now)

    def _held(self, room_id, now):
        """서비스 사용자가 고르고 아직 이용 종료하지 않은 좌석(최대 3시간)."""
        since = (
            (now - timedelta(hours=SELECTION_HOLD_H))
            .astimezone(timezone.utc)
            .isoformat()
        )
        with self.store.db() as db:
            return {
                r["seat_id"]
                for r in db.execute(
                    "SELECT seat_id FROM reco_selections WHERE room_id=? AND selected_at>=? AND ended_at IS NULL",
                    (room_id, since),
                )
            }

    # ---------------------------------------------------------------- 도서관·열람실
    def list_libraries(self):
        now = datetime.now(KST)
        by_lib: dict[str, list[Room]] = {}
        for rid in self.room_ids():
            r = self.room(rid)
            by_lib.setdefault(r.library_id, []).append(r)
        items = []
        for lid, rooms in by_lib.items():
            free = total = 0
            open_any, hours = False, []
            for r in rooms:
                occ = self.occupied(r, now)
                total += len(r.seats)
                free += len(r.seats) - len(occ)
                h = _hours_today(r.opening_hours, now)
                open_any = open_any or h["openNow"]
                if h["today"]:
                    hours.append(h["today"])
            items.append(
                {
                    "libraryId": lid,
                    "name": rooms[0].library_name,
                    "address": rooms[0].address,
                    "region": REGION.get(lid.split("-")[0], "기타"),
                    "roomCount": len(rooms),
                    "totalSeats": total,
                    "remainingSeats": free,
                    "openNow": open_any,
                    "todayHours": sorted(set(hours))[0] if hours else None,
                    "laptopRooms": sum(r.laptop_allowed for r in rooms),
                    "capabilities": {
                        "seatRecommendation": True,
                        "seatMap": True,
                        "roomRecommendation": True,
                        "seatAvailability": "simulated",
                    },
                }
            )
        items.sort(key=lambda x: (x["region"] != "경기 용인", x["region"], x["name"]))
        return {"items": items, "availabilitySource": "simulated"}

    def library_rooms(self, library_id):
        now = datetime.now(KST)
        rooms = [
            self.room(rid)
            for rid in self.room_ids()
            if self.room(rid).library_id == library_id
        ]
        if not rooms:
            api_error(404, "LIBRARY_NOT_FOUND", "지원하지 않는 도서관입니다.")
        return {
            "libraryId": library_id,
            "name": rooms[0].library_name,
            "address": rooms[0].address,
            "items": [r.summary(self.occupied(r, now), now) for r in rooms],
            "availabilitySource": "simulated",
        }

    def room_map(self, room_id):
        r = self.room(room_id)
        return {
            "roomId": r.room_id,
            "libraryId": r.library_id,
            "mapId": r.map_id,
            "mapVersion": r.scenario_id,
            "width": r.width,
            "height": r.height,
            "backgroundUrl": None,
            "verification": "unverified",
            "seats": r.map_seats,
            "facilities": r.facilities,
            "displayName": f"{r.library_name} {r.room_name}",
            "libraryName": r.library_name,
            "roomName": r.room_name,
            "laptopAllowed": r.laptop_allowed,
        }

    def floor_plan(self, room_id):
        r = self.room(room_id)
        return {
            **r.floor_plan(),
            "libraryName": r.library_name,
            "roomName": r.room_name,
            "laptopAllowed": r.laptop_allowed,
            "hours": _hours_today(r.opening_hours, datetime.now(KST)),
        }

    def explain_seat(self, user_id, recommendation_id, seat_id):
        self.require_model()
        from .explain import explain

        with self.store.db() as db:
            rec = self.store.row(
                db,
                "SELECT * FROM reco_recommendations WHERE recommendation_id=?",
                (recommendation_id,),
            )
        if not rec or rec["user_id"] != user_id:
            api_error(404, "NOT_FOUND", "추천 결과를 찾을 수 없습니다.")
        return explain(self, user_id, rec, seat_id)

    def availability(self, room_id):
        r = self.room(room_id)
        now = datetime.now(KST)
        occ = self.occupied(r, now)
        states = [
            {
                "seatId": s["seatId"],
                "state": "occupied" if s["seatId"] in occ else "available",
            }
            for s in r.map_seats
        ]
        return {
            "roomId": room_id,
            "source": "simulated",
            "roomAggregate": {
                "totalSeats": len(states),
                "remainingSeats": sum(s["state"] == "available" for s in states),
                "observedAt": now.replace(
                    second=0, microsecond=0, minute=now.minute // 10 * 10
                ).isoformat(),
            },
            "seatStates": states,
        }

    # ---------------------------------------------------------------- 사용자 취향
    def _explicit(self, user_id):
        with self.store.db() as db:
            row = self.store.row(
                db,
                "SELECT revision, prefs_json, updated_at FROM user_preferences WHERE user_id=?",
                (user_id,),
            )
        if not row:
            return {
                "revision": 0,
                "items": [],
                "laptopRequired": None,
                "updatedAt": None,
            }
        d = json.loads(row["prefs_json"])
        return {
            "revision": row["revision"],
            "items": d.get("items", []),
            "laptopRequired": d.get("laptopRequired"),
            "updatedAt": row["updated_at"],
        }

    def profile(self, user_id):
        """설문 → 카카오톡 분석 → 직접 설정 순으로 합친 추천용 프로필."""
        import numpy as np
        from seat_model.train.userstate import SEAT_KEYS

        prefs, reqs, prior, sources = {}, [], {}, []
        with self.store.db() as db:
            ans = {
                r["question_id"]: r["answer"]
                for r in db.execute(
                    "SELECT question_id,answer FROM persona_answers WHERE user_id=?",
                    (user_id,),
                )
            }
            lab = self.store.row(
                db,
                "SELECT result_json FROM lab_results WHERE user_id=? ORDER BY completed_at DESC LIMIT 1",
                (user_id,),
            )
        for (q, v), items in PERSONA_PREFS.items():
            if ans.get(q) == v:
                for fid, t, imp in items:
                    prefs[fid] = {
                        "featureId": fid,
                        "target": t,
                        "importance": imp,
                        "source": "persona",
                    }
        for (q, v), (fid, m) in PERSONA_PRIOR.items():
            if ans.get(q) == v:
                prior[fid] = m
        if ans:
            sources.append("persona")
        kakao = None
        if lab:
            try:
                from recommender.preference_adapter import to_profile

                analysis = json.loads(lab["result_json"])
                p = to_profile(analysis)
                for x in p["preferences"]:
                    if (
                        x["featureId"] not in prefs
                        or x["importance"] > prefs[x["featureId"]]["importance"]
                    ):
                        prefs[x["featureId"]] = {
                            **{k: x[k] for k in ("featureId", "target", "importance")},
                            "source": "kakao_lab",
                        }
                reqs = list(p["requirements"])
                kakao = {"preferences": analysis.get("preferences") or {}}
                sources.append("kakao_lab")
            except Exception:
                log.exception("lab profile conversion failed")
        ex = self._explicit(user_id)
        bias = np.zeros(len(SEAT_KEYS))
        for it in ex["items"]:
            c = CATALOG.get(it["featureId"])
            ch = next(
                (
                    x
                    for x in (c or {}).get("choices", [])
                    if x["value"] == it.get("choice")
                ),
                None,
            )
            if not c or not ch:
                continue
            imp = int(it.get("importance") or 2)
            if c["kind"] == "profile":
                if ch["value"] == "any":
                    prefs.pop(c["featureId"], None)
                else:
                    prefs[c["featureId"]] = {
                        "featureId": c["featureId"],
                        "target": ch["target"],
                        "importance": PROFILE_IMPORTANCE[imp],
                        "source": "explicit",
                    }
            else:
                prior.pop(
                    c["featureId"], None
                )  # 직접 정했으면 설문 초기값 대신 고정 가산
                if ch["value"] != "any":
                    bias[SEAT_KEYS.index(c["featureId"])] = ch["sign"] * BIAS_SIZE[imp]
        if ex["laptopRequired"] is not None:
            reqs = [r for r in reqs if r["featureId"] != "laptopAllowed"]
            if ex["laptopRequired"]:
                reqs.append(
                    {"featureId": "laptopAllowed", "operator": "eq", "value": 1}
                )
        if ex["items"] or ex["laptopRequired"] is not None:
            sources.append("explicit")
        user = {
            "user_id": user_id,
            "requirements": reqs,
            "preference_profile": {
                "preferences": [
                    {k: p[k] for k in ("featureId", "target", "importance")}
                    for p in sorted(prefs.values(), key=lambda x: x["featureId"])
                ]
            },
        }
        if kakao:
            user["kakaotalk_profile"] = kakao
        return {
            "user": user,
            "prior": prior,
            "bias": bias,
            "sources": sources,
            "prefs": prefs,
            "explicit": ex,
        }

    def get_preferences(self, user_id):
        p = self.profile(user_id)
        ex = p["explicit"]
        laptop = any(
            r["featureId"] == "laptopAllowed" for r in p["user"]["requirements"]
        )
        return {
            "revision": ex["revision"],
            "updatedAt": ex["updatedAt"],
            "items": ex["items"],
            "laptopRequired": ex["laptopRequired"],
            "catalog": [
                {
                    "featureId": c["featureId"],
                    "label": c["label"],
                    "choices": [
                        {"value": x["value"], "label": x["label"]} for x in c["choices"]
                    ],
                }
                for c in PREF_CATALOG
            ],
            "effective": {
                "sources": p["sources"],
                "laptopRequired": laptop,
                "preferences": [
                    {
                        "featureId": v["featureId"],
                        "label": CATALOG.get(v["featureId"], {}).get(
                            "label", v["featureId"]
                        ),
                        "target": v["target"],
                        "importance": v["importance"],
                        "source": v["source"],
                    }
                    for v in p["prefs"].values()
                    if v["featureId"] in CATALOG
                ],
            },
        }

    def put_preferences(self, user_id, expected_revision, items, laptop_required):
        clean = []
        for it in items:
            c = CATALOG.get(it.get("featureId"))
            if not c or it.get("choice") not in {x["value"] for x in c["choices"]}:
                api_error(
                    422,
                    "INVALID_PREFERENCE",
                    f"알 수 없는 취향 항목입니다: {it.get('featureId')}",
                )
            imp = int(it.get("importance") or 2)
            if imp not in (1, 2, 3):
                api_error(422, "INVALID_PREFERENCE", "중요도는 1~3입니다.")
            clean.append(
                {"featureId": c["featureId"], "choice": it["choice"], "importance": imp}
            )
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.store.row(
                db, "SELECT revision FROM user_preferences WHERE user_id=?", (user_id,)
            )
            cur = row["revision"] if row else 0
            if expected_revision != cur:
                db.rollback()
                api_error(
                    409,
                    "REVISION_CONFLICT",
                    "다른 곳에서 취향이 바뀌었어요. 새로고침한 뒤 다시 저장해 주세요.",
                    currentRevision=cur,
                )
            doc = json.dumps(
                {"items": clean, "laptopRequired": laptop_required}, ensure_ascii=False
            )
            db.execute(
                "INSERT INTO user_preferences VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision, "
                "prefs_json=excluded.prefs_json, updated_at=excluded.updated_at",
                (user_id, cur + 1, doc, now_iso()),
            )
            db.commit()
        return self.get_preferences(user_id)

    # ---------------------------------------------------------------- 사용자 온라인 상태(DB)
    def _user_state(self, user_id, prior):
        import numpy as np
        from seat_model.train.online import BayesUser
        from seat_model.train.userstate import SEAT_KEYS, UserState

        on = self.rec.online
        if user_id not in on.hist:
            with self.store.db() as db:
                row = self.store.row(
                    db,
                    "SELECT state_json FROM reco_user_state WHERE user_id=?",
                    (user_id,),
                )
            if row:
                d = json.loads(row["state_json"])
                on.hist[user_id] = UserState.from_dict(d["hist"])
                b = BayesUser(on.k, on.prior_var)
                b.mu, b.P, b.n = np.array(d["mu"]), np.array(d["P"]), d["n"]
                on.bayes[user_id] = b
                if d.get("touched"):
                    self._touched.add(user_id)
        st, bu = on.get(user_id)
        if (
            bu.n == 0 and user_id not in self._touched
        ):  # 착석·보정 기록 전에는 설문 답이 초기 취향
            bu.mu = np.zeros(on.k)
            for fid, m in prior.items():
                bu.mu[SEAT_KEYS.index(fid)] = m
        return st, bu

    def forget_user(self, user_id):
        """탈퇴: 메모리에 남은 개인 학습 상태를 지운다(DB 기록은 AuthService가 지움)."""
        with self.lock:
            self._touched.discard(user_id)
            if self.rec is not None:
                self.rec.online.hist.pop(user_id, None)
                self.rec.online.bayes.pop(user_id, None)

    def _save_state(self, user_id):
        on = self.rec.online
        st, bu = on.hist[user_id], on.bayes[user_id]
        d = {
            "hist": st.to_dict(),
            "mu": bu.mu.tolist(),
            "P": bu.P.tolist(),
            "n": bu.n,
            "touched": user_id in self._touched,
        }
        with self.store.db() as db:
            db.execute(
                "INSERT INTO reco_user_state(user_id,state_json,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "state_json=excluded.state_json, updated_at=excluded.updated_at",
                (user_id, json.dumps(d), now_iso()),
            )

    def _snapshot(self, room: Room, occupied, requested_at: datetime, slot):
        return {
            "room_id": room.room_id,
            "time_slot": slot,
            "requested_at": requested_at.isoformat(),
            "conditions": room.conditions,
            "seats": room.seats,
            "occupied_seat_ids": sorted(occupied),
        }

    def _rank(self, user_id, room: Room, occ, now, p):
        """열람실 하나의 빈자리 전체 순위. 반환: (순위 목록, 이력 수)."""
        free = [s for s in room.seats if s["seat_id"] not in occ]
        if not free:
            return [], 0
        with self.lock:
            st, _ = self._user_state(user_id, p["prior"])
            if len(free) == 1:
                return [
                    {
                        "seat_id": free[0]["seat_id"],
                        "score": 0.0,
                        "meets_requirements": True,
                        "reason_items": [],
                    }
                ], st.n
            out = self.rec.recommend(
                p["user"],
                self._snapshot(room, occ, now, _slot(now.hour)),
                k=len(free),
                bias=p["bias"],
            )
        return out["recommendations"], out["history_sessions"]

    def _result_item(self, room: Room, x, rank):
        sid = x["seat_id"]
        reasons = [
            {"code": it["code"], "featureId": it["featureId"], "text": it["text"]}
            for it in x.get("reason_items", [])
        ]
        return {
            "rank": rank,
            "targetType": "seat",
            "targetId": sid,
            "libraryId": room.library_id,
            "roomId": room.room_id,
            "seatId": sid,
            "mapAnchor": {**room.box[sid], "mapId": room.map_id}
            if sid in room.box
            else None,
            "availability": {"effectiveState": "available"},
            "reasons": reasons,
            "warnings": [] if x["meets_requirements"] else ["필수 조건 밖 좌석"],
            "previewOnly": False,
            "score": round(float(x["score"]), 4),
        }

    # ---------------------------------------------------------------- 추천
    def recommend(self, user_id, room_id, top_k=5):
        self.require_model()
        r = self.room(room_id)
        now = datetime.now(KST)
        slot = _slot(now.hour)
        occ = self.occupied(r, now)
        p = self.profile(user_id)
        with self.store.db() as db:
            excl = {
                x["seat_id"]
                for x in db.execute(
                    "SELECT seat_id FROM reco_exclusions WHERE user_id=? AND room_id=?",
                    (user_id, room_id),
                )
            }
        rid = "rec_" + uuid.uuid4().hex
        created = datetime.now(timezone.utc)
        expires = created + timedelta(minutes=REC_TTL_MIN)
        warnings = []
        ranked_all, n_hist = self._rank(user_id, r, occ, now, p)
        ranked = [x for x in ranked_all if x["seat_id"] not in excl]
        if not ranked_all:
            outcome = "noCandidates"
        else:
            outcome = "ranked" if ranked else "noEligibleCandidates"
            if any(not x["meets_requirements"] for x in ranked[:top_k]):
                warnings.append(
                    "필수 조건(노트북 규정)에 맞는 빈자리가 부족해 일부 조건 밖 좌석을 포함했어요."
                )
            if not p["sources"]:
                warnings.append(
                    "취향 정보가 없어 이용 기록과 전체 이용자 경향으로 추천했어요."
                )
        hours = _hours_today(r.opening_hours, now)
        if not hours["openNow"]:
            warnings.append(
                "지금은 운영 시간이 아니에요. 운영 시간 기준이 아닌 미리보기 추천이에요."
            )
        results = [self._result_item(r, x, i + 1) for i, x in enumerate(ranked[:top_k])]
        with self.store.db() as db:
            db.execute(
                "INSERT INTO reco_recommendations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    user_id,
                    room_id,
                    r.scenario_id,
                    created.isoformat(),
                    expires.isoformat(),
                    now.isoformat(),
                    slot,
                    json.dumps(sorted(occ)),
                    json.dumps([x["seat_id"] for x in ranked_all]),
                    json.dumps(p["user"], ensure_ascii=False),
                    None,
                ),
            )
        names = {
            r.room_id: f"{r.library_name} {r.room_name}",
            r.library_id: r.library_name,
        }
        names.update({x["targetId"]: r.seat_name(x["targetId"]) for x in results})
        return {
            "recommendationId": rid,
            "createdAt": created.isoformat(),
            "expiresAt": expires.isoformat(),
            "profileRevision": p["explicit"]["revision"],
            "result": {
                "executionMode": "live",
                "outcome": outcome,
                "results": results,
                "warnings": warnings,
            },
            "displayNames": names,
            "room": r.summary(occ, now),
            "model": {
                "version": self.manifest.get("model_version"),
                "historySessions": n_hist,
                "profileSources": p["sources"],
                "availabilitySource": "simulated",
            },
        }

    def recommend_rooms(self, user_id, library_ids=None, top_k=3):
        """취향에 맞는 열람실: 열람실마다 빈자리 순위를 내고 상위 3석 점수 평균으로 비교한다."""
        self.require_model()
        now = datetime.now(KST)
        p = self.profile(user_id)
        laptop = any(
            rq["featureId"] == "laptopAllowed" for rq in p["user"]["requirements"]
        )
        cands = []
        for rid in self.room_ids():
            r = self.room(rid)
            if library_ids and r.library_id not in library_ids:
                continue
            occ = self.occupied(r, now)
            ranked, _ = self._rank(user_id, r, occ, now, p)
            ok = [x for x in ranked if x["meets_requirements"]]
            if not ok or (laptop and not r.laptop_allowed):
                continue
            top = ok[:3]
            score = sum(x["score"] for x in top) / len(top)
            counts = {}
            for x in top:
                for it in x.get("reason_items", []):
                    counts[it["text"]] = counts.get(it["text"], 0) + 1
            reasons = [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
            free = len(r.seats) - len(occ)
            if free / max(len(r.seats), 1) >= 0.35:
                reasons.append(f"빈자리 여유({free}석)")
            cands.append(
                {
                    "targetType": "room",
                    "targetId": r.room_id,
                    "roomId": r.room_id,
                    "libraryId": r.library_id,
                    "libraryName": r.library_name,
                    "roomName": r.room_name,
                    "score": round(score, 4),
                    "reasons": [
                        {"code": "ROOM_FIT", "featureId": None, "text": t}
                        for t in reasons[:4]
                    ],
                    "room": r.summary(occ, now),
                    "bestSeats": [
                        {"seatId": x["seat_id"], "name": r.seat_name(x["seat_id"])}
                        for x in top
                    ],
                }
            )
        cands.sort(key=lambda c: -c["score"])
        for i, c in enumerate(cands):
            c["rank"] = i + 1
        return {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "outcome": "ranked" if cands else "noCandidates",
            "results": cands[:top_k],
            "profileSources": p["sources"],
            "availabilitySource": "simulated",
            "warnings": []
            if p["sources"]
            else ["취향 정보가 없어 전체 이용자 경향으로 골랐어요."],
        }

    # ---------------------------------------------------------------- 이벤트
    def _active_selection(self, db, user_id, now=None):
        now = now or datetime.now(KST)
        since = (
            (now - timedelta(hours=SELECTION_HOLD_H))
            .astimezone(timezone.utc)
            .isoformat()
        )
        return self.store.row(
            db,
            "SELECT * FROM reco_selections WHERE user_id=? AND selected_at>=? AND ended_at IS NULL ORDER BY selected_at DESC LIMIT 1",
            (user_id, since),
        )

    def record_event(self, user_id, recommendation_id, body: dict):
        self.require_model()
        etype = body.get("type")
        event_id = str(body.get("eventId") or uuid.uuid4())
        with self.store.db() as db:
            prev = self.store.row(
                db,
                "SELECT effect,user_id FROM reco_events WHERE event_id=?",
                (event_id,),
            )
            rec = self.store.row(
                db,
                "SELECT * FROM reco_recommendations WHERE recommendation_id=?",
                (recommendation_id,),
            )
        if prev:  # 멱등: 같은 eventId는 한 번만 반영
            if prev["user_id"] != user_id:
                api_error(409, "EVENT_ID_CONFLICT", "이미 사용된 이벤트 ID입니다.")
            return {
                "eventId": event_id,
                "accepted": True,
                "effect": prev["effect"],
                "duplicate": True,
            }
        if not rec or rec["user_id"] != user_id:
            api_error(404, "NOT_FOUND", "추천 결과를 찾을 수 없습니다.")
        ranked = json.loads(rec["ranked_json"])
        tid = body.get("targetId")
        effect = "storedOnly"
        if etype == "recommendationExposed":
            vis = [x for x in (body.get("visibleTargetIds") or []) if x in ranked]
            with self.store.db() as db:
                db.execute(
                    "UPDATE reco_recommendations SET exposed_json=? WHERE recommendation_id=?",
                    (json.dumps(vis), recommendation_id),
                )
        elif etype == "candidateRejected":
            if tid not in ranked:
                api_error(422, "VALIDATION_ERROR", "추천 후보가 아닌 좌석입니다.")
            if body.get("excludeFromFuture"):
                with self.store.db() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO reco_exclusions VALUES(?,?,?,?)",
                        (user_id, rec["room_id"], tid, now_iso()),
                    )
                effect = "excluded"
            if self._apply_reject_reason(
                user_id, recommendation_id, body.get("reason") or ""
            ):
                effect = "preferencesUpdated"
        elif etype == "candidateSelected":
            effect = self._select(
                user_id,
                rec,
                ranked,
                tid,
                body.get("origin") or "recommendation",
                recommendation_id,
            )
        elif etype in ("useConfirmed", "useEnded", "ratingSubmitted"):
            with self.store.db() as db:
                sel = self.store.row(
                    db,
                    "SELECT * FROM reco_selections WHERE user_id=? AND recommendation_id=? AND seat_id=? ORDER BY id DESC LIMIT 1",
                    (user_id, recommendation_id, tid),
                )
                if not sel:
                    api_error(409, "NOT_SELECTED", "먼저 이 좌석을 선택해야 합니다.")
                if etype == "useConfirmed":
                    db.execute(
                        "UPDATE reco_selections SET confirmed_at=COALESCE(confirmed_at, ?) WHERE id=?",
                        (now_iso(), sel["id"]),
                    )
                    effect = "historyUpdated"
                elif etype == "useEnded":
                    db.execute(
                        "UPDATE reco_selections SET ended_at=COALESCE(ended_at, ?) WHERE id=?",
                        (now_iso(), sel["id"]),
                    )
                    effect = "historyUpdated"
                else:
                    rating = body.get("rating")
                    if not isinstance(rating, int) or not 1 <= rating <= 5:
                        api_error(422, "VALIDATION_ERROR", "평가는 1~5점입니다.")
                    db.execute(
                        "UPDATE reco_selections SET rating=?, rating_comment=? WHERE id=?",
                        (rating, (body.get("comment") or "")[:300], sel["id"]),
                    )
                    effect = "storedOnly"  # 만족도는 학습에 쓰지 않는다
        else:
            api_error(422, "VALIDATION_ERROR", "지원하지 않는 이벤트 종류입니다.")
        with self.store.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO reco_events VALUES(?,?,?,?,?,?,?)",
                (
                    event_id,
                    recommendation_id,
                    user_id,
                    etype,
                    json.dumps(body, ensure_ascii=False),
                    effect,
                    now_iso(),
                ),
            )
        return {"eventId": event_id, "accepted": True, "effect": effect}

    def _apply_reject_reason(self, user_id, recommendation_id, reason):
        """'너무 추울 것 같아요' 같은 거절 이유를 개인 계수 μ_u에 바로 반영한다. 같은 추천에서는 한 번만."""
        from seat_model.train.userstate import SEAT_KEYS

        hits = [
            (f, sgn) for keys, f, sgn in REJECT_SIGNAL if any(k in reason for k in keys)
        ]
        if not hits:
            return []
        with self.store.db() as db:
            done = db.execute(
                "SELECT 1 FROM reco_events WHERE recommendation_id=? AND type='candidateRejected' AND effect='preferencesUpdated'",
                (recommendation_id,),
            ).fetchone()
        if done:
            return []
        p = self.profile(user_id)
        with self.lock:
            _, bu = self._user_state(user_id, p["prior"])
            self._touched.add(user_id)
            for f, sgn in hits:
                j = SEAT_KEYS.index(f)
                bu.mu[j] = max(
                    -REJECT_CAP, min(REJECT_CAP, bu.mu[j] + sgn * REJECT_STEP)
                )
            self._save_state(user_id)
        return hits

    def _select(self, user_id, rec, ranked, tid, origin, recommendation_id):
        """좌석 선택 → 개인 모델 즉시 갱신. 추천 밖 좌석도 그 추천 당시 빈자리면 받는다."""
        if tid not in ranked:
            api_error(422, "VALIDATION_ERROR", "추천 당시 빈자리가 아닌 좌석입니다.")
        if datetime.fromisoformat(rec["expires_at"]) < datetime.now(timezone.utc):
            api_error(
                410,
                "RECOMMENDATION_EXPIRED",
                "추천이 만료되었습니다. 다시 추천받아 주세요.",
            )
        with self.store.db() as db:
            taken = self.store.row(
                db,
                "SELECT user_id FROM reco_selections WHERE room_id=? AND seat_id=? AND selected_at>=? AND user_id<>? AND ended_at IS NULL",
                (rec["room_id"], tid, rec["created_at"], user_id),
            )
        if taken:
            api_error(
                409,
                "AVAILABILITY_CHANGED",
                "그 사이 다른 사용자가 고른 좌석입니다. 다시 추천받아 주세요.",
            )
        r = self.room(rec["room_id"])
        occ = json.loads(rec["occupied_json"])
        snap = self._snapshot(
            r, occ, datetime.fromisoformat(rec["requested_at"]), rec["time_slot"]
        )
        vis = json.loads(rec["exposed_json"] or "[]") or ranked[:2]
        exposed_rank = {s: i + 1 for i, s in enumerate(vis)}
        user = json.loads(rec["profile_json"])
        p = self.profile(user_id)
        with self.lock:
            st, bu = self._user_state(user_id, p["prior"])
            if bu.n > 0:
                bu.P = bu.P * FORGET  # 최근 선택에 더 무게(취향이 바뀌면 따라간다)
            if st.n > 0:  # 습관·드러난 취향도 오래된 것부터 옅어진다
                st.contrast_sum = st.contrast_sum * HIST_DECAY
                st.seat_counts = {k: v * HIST_DECAY for k, v in st.seat_counts.items()}
                st.group_counts = {
                    k: v * HIST_DECAY for k, v in st.group_counts.items()
                }
            if len(r.seats) - len(occ) >= 2:
                self.rec.observe(
                    user_id, snap, tid, user=user, exposed_rank=exposed_rank
                )
            else:
                st, _ = self.rec.online.get(user_id)
                st.n += 1
            self._save_state(user_id)
        with self.store.db() as db:
            # 한 사람은 한 자리만: 이용 중이던 자리는 자동으로 이용 종료
            db.execute(
                "UPDATE reco_selections SET ended_at=? WHERE user_id=? AND ended_at IS NULL",
                (now_iso(), user_id),
            )
            db.execute(
                "INSERT INTO reco_selections(user_id,room_id,seat_id,recommendation_id,selected_at,origin) VALUES(?,?,?,?,?,?)",
                (user_id, rec["room_id"], tid, recommendation_id, now_iso(), origin),
            )
        return "learned"

    # ---------------------------------------------------------------- 기록·인사이트
    def history(self, user_id, limit=50):
        now = datetime.now(KST)
        since = (
            (now - timedelta(hours=SELECTION_HOLD_H))
            .astimezone(timezone.utc)
            .isoformat()
        )
        with self.store.db() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM reco_selections WHERE user_id=? ORDER BY selected_at DESC LIMIT ?",
                    (user_id, limit),
                )
            ]
            total = db.execute(
                "SELECT COUNT(*) FROM reco_selections WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        items = []
        for s in rows:
            try:
                r = self.room(s["room_id"])
                seat_name, room_name, lib_name, lib_id = (
                    r.seat_name(s["seat_id"]),
                    r.room_name,
                    r.library_name,
                    r.library_id,
                )
            except Exception:
                seat_name, room_name, lib_name, lib_id = (
                    s["seat_id"],
                    s["room_id"],
                    "",
                    "",
                )
            active = s["ended_at"] is None and s["selected_at"] >= since
            items.append(
                {
                    "selectionId": s["id"],
                    "recommendationId": s["recommendation_id"],
                    "seatId": s["seat_id"],
                    "seatName": seat_name,
                    "roomId": s["room_id"],
                    "roomName": room_name,
                    "libraryId": lib_id,
                    "libraryName": lib_name,
                    "origin": s.get("origin") or "recommendation",
                    "selectedAt": s["selected_at"],
                    "confirmedAt": s.get("confirmed_at"),
                    "endedAt": s.get("ended_at"),
                    "rating": s.get("rating"),
                    "status": "active"
                    if active
                    else ("ended" if s["ended_at"] else "expired"),
                    "holdUntil": (
                        datetime.fromisoformat(s["selected_at"])
                        + timedelta(hours=SELECTION_HOLD_H)
                    ).isoformat()
                    if active
                    else None,
                }
            )
        return {
            "current": next((i for i in items if i["status"] == "active"), None),
            "items": items,
            "total": total,
        }

    def insights(self, user_id):
        """착석 기록으로 파악한 취향: 개인 계수 μ_u의 방향·확신도 + 실제로 고른 자리와 그때 빈자리의 비교."""
        self.require_model()
        import numpy as np
        from seat_model.train.userstate import SEAT_KEYS

        p = self.profile(user_id)
        with self.lock:
            st, bu = self._user_state(user_id, p["prior"])
            prof = self.rec.inferred_profile(user_id)
            n = st.n
        z = {i["featureId"]: i for i in prof["all"]}
        # 근거: 선택마다 (고른 좌석 값, 그때 빈자리 평균)
        with self.store.db() as db:
            rows = list(
                db.execute(
                    "SELECT s.seat_id, s.room_id, r.occupied_json, r.time_slot FROM reco_selections s "
                    "JOIN reco_recommendations r ON r.recommendation_id=s.recommendation_id WHERE s.user_id=? "
                    "ORDER BY s.selected_at DESC LIMIT 100",
                    (user_id,),
                )
            )
        ev = {
            f: {"n": 0, "higher": 0, "chosen": 0.0, "avail": 0.0}
            for f, _, _ in INSIGHT_FEATURES
        }
        for row in rows:
            try:
                r = self.room(row["room_id"])
            except Exception:
                continue
            occ = set(json.loads(row["occupied_json"]))
            free = [sid for sid in r.feat if sid not in occ] or list(r.feat)
            for f, _, _ in INSIGHT_FEATURES:
                val = lambda sid: (
                    r.feat[sid][f][row["time_slot"]]
                    if f == "brightness"
                    else r.feat[sid][f]
                )
                c, a = val(row["seat_id"]), float(np.mean([val(s) for s in free]))
                e = ev[f]
                e["n"] += 1
                e["chosen"] += c
                e["avail"] += a
                e["higher"] += c > a + 1e-9
        items = []
        for f, label, binary in INSIGHT_FEATURES:
            e = ev[f]
            info = z.get(f, {})
            zs = float(info.get("z", 0.0))
            if e["n"] == 0:
                continue
            chosen, avail = e["chosen"] / e["n"], e["avail"] / e["n"]
            if binary:
                text = f"최근 {e['n']}번 중 {label}를 {round(chosen * e['n'])}번 골랐어요 (그때 빈자리 중 {avail:.0%})"
            elif f == "noise_index":  # 소음은 '조용한 자리'로 읽는다
                text = f"최근 {e['n']}번 중 {e['n'] - e['higher']}번, 그때 빈자리 평균보다 조용한 자리를 골랐어요"
            else:
                text = f"최근 {e['n']}번 중 {e['higher']}번, 그때 빈자리 평균보다 {label}를 골랐어요"
            status = (
                "insufficientData"
                if e["n"] < 3
                else ("learned" if abs(zs) >= 1.0 else "neutral")
            )
            direction = "high" if zs > 0 else "low"
            if f == "noise_index":  # 소음은 반대로 읽는다
                summary = "조용한 자리를 선호" if zs < 0 else "시끌벅적해도 괜찮음"
            else:
                summary = f"{label} 선호" if zs > 0 else f"{label}를 피함"
            items.append(
                {
                    "featureId": f,
                    "label": label,
                    "direction": direction,
                    "z": round(zs, 2),
                    "confidence": round(float(1 - np.exp(-abs(zs) / 2)), 3),
                    "status": status,
                    "summary": summary
                    if status == "learned"
                    else (
                        "아직 파악 중"
                        if status == "insufficientData"
                        else "뚜렷한 경향 없음"
                    ),
                    "evidence": {
                        "observations": e["n"],
                        "chosenRate": round(chosen, 3),
                        "availableRate": round(avail, 3),
                        "higherCount": e["higher"],
                    },
                    "text": text,
                }
            )
        items.sort(key=lambda x: (x["status"] != "learned", -abs(x["z"])))
        return {
            "historySessions": n,
            "modelVersion": self.manifest.get("model_version"),
            "items": items,
            "inferredPreferences": prof["preferences"],
            "profileSources": p["sources"],
            "note": "착석 3회 이상부터, 확신이 충분한 항목만 학습된 취향으로 보여줘요. 직접 설정한 취향은 학습으로 바뀌지 않아요.",
        }
