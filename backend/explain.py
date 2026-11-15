"""좌석 자세히 보기: 이 자리의 특징과 '왜 추천했는지'를 숫자 없이 쉬운 말로 만든다.

입력: 추천 기록(그때 빈자리·프로필), 열람실(합성 환경 원값), 사용자 취향(직접 설정·설문·카톡·앉은 기록).
- 자리 특징(facts): 원값(거리 m, 조도, 바람 세기 등)을 말로 바꾼다. 숫자는 응답에 넣지 않는다.
- 추천 이유(reasons): 모델이 이 자리 점수를 올린 특징(reason_items) 순서대로, 그 취향이 어디서 왔는지와 함께.
- 아쉬운 점(cautions): 내가 원하는 방향과 반대인 특징.
- 내 취향과 비교(matches): 취향 항목마다 잘 맞음/보통/안 맞음.
"""

from __future__ import annotations

import json

FEATURE_NAME = {
    "outletAvailable": "콘센트",
    "partition": "칸막이",
    "windowProximity": "창가",
    "brightness": "밝기",
    "airflow": "에어컨 바람",
    "deskWidth": "책상 크기",
    "noise_index": "조용함",
    "toiletProximity": "화장실 거리",
    "entranceProximity": "출입구 거리",
    "viewQuality": "전망",
    "laptopAllowed": "노트북",
}
# 설문 질문 → 관련 특징
PERSONA_FEATURE = {
    "outlet": ["outletAvailable", "laptopAllowed"],
    "hvac_proximity": ["airflow"],
    "noise_sensitivity": ["noise_index"],
    "partition": ["partition"],
}
PERSONA_WORD = {
    ("outlet", "required"): "설문에서 노트북·콘센트가 필요하다고 하셨어요",
    ("hvac_proximity", "near"): "설문에서 에어컨 가까운 자리를 좋아한다고 하셨어요",
    ("hvac_proximity", "not_near"): "설문에서 에어컨에서 먼 자리를 좋아한다고 하셨어요",
    ("noise_sensitivity", "high"): "설문에서 소음에 많이 민감하다고 하셨어요",
    ("noise_sensitivity", "low"): "설문에서 소음에 조금 민감하다고 하셨어요",
    ("partition", "yes"): "설문에서 칸막이 있는 자리를 좋아한다고 하셨어요",
    ("partition", "no"): "설문에서 트인 자리를 좋아한다고 하셨어요",
}
SLOT_WORD = {"morning": "오전", "noon": "낮", "evening": "저녁"}


def _q(v, a, b):
    """0~1 값 3단계: 높음(≥a)·중간(≥b)·낮음"""
    return 2 if v >= a else 1 if v >= b else 0


def seat_facts(room, sid, slot, occupied):
    """자리 특징을 말로. key별 good 방향(high=값이 클수록 일반적으로 좋음)과 단계(level 0~2)를 같이 낸다."""
    r, f = room.raw[sid], room.feat[sid]
    lux = r["lux"][slot]
    facts = []

    def add(key, icon, title, value, detail, level=None, featureId=None):
        facts.append(
            {
                "key": key,
                "icon": icon,
                "title": title,
                "value": value,
                "detail": detail,
                "level": level,
                "featureId": featureId,
            }
        )

    w = r.get("width_cm") or 90
    add(
        "desk",
        "desk",
        "책상",
        f"{room_desk_label(r['desk_type'])}",
        "넓어서 책과 노트북을 함께 펼치기 좋아요"
        if w >= 120
        else "혼자 공부하기 알맞은 크기예요"
        if w >= 85
        else "아담해서 노트 한 권 정도가 알맞아요",
        2 if w >= 120 else 1 if w >= 85 else 0,
        "deskWidth",
    )
    add(
        "partition",
        "partition",
        "칸막이",
        "있어요" if r["partition"] else "없어요",
        "옆사람 시선이 가려져 집중하기 좋아요"
        if r["partition"]
        else "트여 있어 답답하지 않아요",
        2 if r["partition"] else 0,
        "partition",
    )
    if r.get("desk_outlet"):
        add(
            "outlet",
            "outlet",
            "콘센트",
            "책상에 있어요",
            "노트북·휴대폰을 바로 충전할 수 있어요",
            2,
            "outletAvailable",
        )
    elif f["outletAvailable"]:
        add(
            "outlet",
            "outlet",
            "콘센트",
            "바로 옆에 있어요",
            "가까운 벽 콘센트를 쓸 수 있어요",
            2,
            "outletAvailable",
        )
    else:
        add(
            "outlet",
            "outlet",
            "콘센트",
            "멀어요",
            "충전이 필요하면 다른 자리가 나아요",
            0,
            "outletAvailable",
        )
    dw = r["dist_window_m"]
    lv = 2 if dw <= 2.5 else 1 if dw <= 6 else 0
    add(
        "window",
        "window",
        "창가",
        ["안쪽 자리", "창가에서 가까운 편", "창가 바로 옆"][lv],
        [
            "창에서 떨어져 있어 바깥 빛·소리 영향이 적어요",
            "고개를 들면 창밖이 보여요",
            "자연광이 잘 들고 바깥이 보여요",
        ][lv],
        lv,
        "windowProximity",
    )
    lvb = 2 if lux >= 550 else 1 if lux >= 330 else 0
    add(
        "light",
        "light",
        f"밝기({SLOT_WORD.get(slot, '지금')})",
        ["은은한 편", "적당히 밝아요", "밝은 편"][lvb],
        [
            "눈부심이 적고 차분해요",
            "책 읽기에 알맞은 밝기예요",
            "글씨가 또렷하게 보여요",
        ][lvb],
        lvb,
        "brightness",
    )
    a = r["airflow_index"]
    lva = _q(a, 0.5, 0.2)
    add(
        "air",
        "air",
        "에어컨 바람",
        ["거의 닿지 않아요", "약하게 닿아요", "직접 닿는 편"][lva],
        [
            "바람이 직접 오지 않아 추위를 덜 타요",
            "은은하게 시원해요",
            "시원하지만 오래 있으면 추울 수 있어요",
        ][lva],
        lva,
        "airflow",
    )
    t = r.get("temperature_c")
    if t is not None:
        lvt = 2 if t >= 26.5 else 1 if t >= 23 else 0
        add(
            "temp",
            "temp",
            "체감 온도",
            ["서늘한 편", "쾌적한 편", "따뜻한 편"][lvt],
            [
                "겉옷을 챙기면 좋아요",
                "오래 앉아 있기 편해요",
                "더위를 타면 에어컨 가까운 자리가 나아요",
            ][lvt],
            lvt,
        )
    n = r["noise_index"]
    lvn = 2 if n <= 0.25 else 1 if n <= 0.5 else 0
    add(
        "noise",
        "noise",
        "주변 소리",
        ["오가는 소리가 있는 편", "보통이에요", "조용한 편"][lvn],
        [
            "출입구·통로와 가까워 발소리가 들릴 수 있어요",
            "가끔 사람이 지나가요",
            "사람 왕래가 적어 집중하기 좋아요",
        ][lvn],
        lvn,
        "noise_index",
    )
    de = r["dist_entrance_walk_m"]
    lve = 2 if de <= 10 else 1 if de <= 25 else 0
    add(
        "entrance",
        "entrance",
        "출입구",
        ["멀리 떨어져 있어요", "적당히 떨어져 있어요", "가까워요"][lve],
        [
            "드나드는 사람이 적어요",
            "오가기도 편하고 북적이지도 않아요",
            "들어오고 나가기 편해요",
        ][lve],
        lve,
        "entranceProximity",
    )
    dt = r["dist_toilet_walk_m"]
    lvw = 2 if dt <= 25 else 1 if dt <= 60 else 0
    add(
        "toilet",
        "toilet",
        "화장실",
        ["먼 편이에요", "보통 거리예요", "가까워요"][lvw],
        [
            "오가는 사람이 적은 대신 조금 걸어야 해요",
            "잠깐 다녀오기 무난해요",
            "금방 다녀올 수 있어요",
        ][lvw],
        lvw,
        "toiletProximity",
    )
    vq = f["viewQuality"]
    lvv = _q(vq, 0.5, 0.2)
    add(
        "view",
        "view",
        "전망",
        ["창밖이 거의 안 보여요", "창밖이 조금 보여요", "창밖 전망이 좋아요"][lvv],
        ["바깥에 신경 쓰이지 않아요", "가끔 쉬어 가기 좋아요", "눈을 쉬게 하기 좋아요"][
            lvv
        ],
        lvv,
        "viewQuality",
    )
    add(
        "laptop",
        "laptop",
        "노트북",
        "쓸 수 있어요" if f["laptopAllowed"] else "쓸 수 없어요",
        "타자 소리가 허용되는 자리예요"
        if f["laptopAllowed"]
        else "조용한 열람 전용 자리예요",
        2 if f["laptopAllowed"] else 0,
        "laptopAllowed",
    )
    mates = [s for s in room.group_of(sid) if s != sid]
    if mates:
        busy = sum(s in occupied for s in mates)
        lvm = 2 if busy == 0 else 1 if busy <= len(mates) / 2 else 0
        add(
            "neighbors",
            "people",
            "같은 책상",
            [
                "다른 분들이 많이 앉아 있어요",
                "몇 분이 앉아 있어요",
                "지금 다른 분이 없어요",
            ][lvm],
            ["조금 붐빌 수 있어요", "적당히 여유 있어요", "넓게 쓰기 좋아요"][lvm],
            lvm,
        )
    else:
        add(
            "neighbors",
            "people",
            "같은 책상",
            "혼자 쓰는 책상이에요",
            "옆자리를 신경 쓰지 않아도 돼요",
            2,
        )
    return facts


def room_desk_label(desk_type):
    from .recommend import DESK_KO

    return DESK_KO.get(desk_type, desk_type)


def _source_of(featureId, p, learned):
    """이 특징 취향이 어디서 왔는지 → (source, 사용자에게 보여줄 말)"""
    ex = {i["featureId"]: i for i in p["explicit"]["items"]}
    from .recommend import CATALOG

    if featureId in ex and ex[featureId]["choice"] != "any":
        c = CATALOG[featureId]
        lab = next(
            (x["label"] for x in c["choices"] if x["value"] == ex[featureId]["choice"]),
            "",
        )
        return "explicit", f"내 취향에서 '{lab}'을 고르셨어요"
    if featureId == "laptopAllowed" and p["explicit"]["laptopRequired"]:
        return "explicit", "내 취향에서 노트북을 쓴다고 하셨어요"
    pref = p["prefs"].get(featureId)
    if pref and pref["source"] == "kakao_lab":
        return "kakao_lab", "카톡 대화 분석에서 이런 자리를 좋아하시는 것 같았어요"
    for q, feats in PERSONA_FEATURE.items():
        if featureId in feats:
            for (qq, v), word in PERSONA_WORD.items():
                if qq == q and p.get("answers", {}).get(q) == v:
                    return "persona", word
    if featureId in learned:
        return "history", "지금까지 이런 자리를 자주 고르셨어요"
    return "population", "많은 분들이 좋아하는 조건이에요"


# 추천 이유 특징 → (이 자리 사실 문장 고르기)
def _reason_sentence(featureId, fact_by_feature, high):
    fct = fact_by_feature.get(featureId)
    base = {
        "outletAvailable": "콘센트를 바로 쓸 수 있는 자리예요",
        "partition": "칸막이가 있어 집중하기 좋은 자리예요"
        if high
        else "트여 있어 답답하지 않은 자리예요",
        "windowProximity": "창가 가까이 있는 자리예요"
        if high
        else "창에서 떨어진 안쪽 자리예요",
        "brightness": "밝아서 글씨가 또렷하게 보여요"
        if high
        else "은은한 조명이라 눈이 편해요",
        "airflow": "에어컨 바람이 시원하게 닿아요"
        if high
        else "에어컨 바람이 직접 닿지 않아요",
        "deskWidth": "책상이 넓어 여러 권을 펼칠 수 있어요"
        if high
        else "아담한 1인 자리예요",
        "noise_index": "주변이 조용해 집중하기 좋아요"
        if not high
        else "활기 있는 구역이에요",
        "toiletProximity": "화장실이 가까워요" if high else "화장실 쪽 왕래가 적어요",
        "entranceProximity": "출입구가 가까워 드나들기 편해요"
        if high
        else "출입구에서 떨어져 오가는 사람이 적어요",
        "viewQuality": "창밖 전망이 좋아요"
        if high
        else "바깥에 신경 쓰이지 않는 자리예요",
        "laptopAllowed": "노트북을 쓸 수 있는 자리예요",
        "keyboardAllowed": "키보드를 쓸 수 있는 자리예요",
        "deskHeight": "책상 높이가 편한 자리예요",
    }.get(featureId)
    return base or (fct["value"] if fct else None)


def explain(svc, user_id, rec_row, seat_id):
    """svc: RecommendationService, rec_row: reco_recommendations 행"""
    import numpy as np
    from datetime import datetime
    from seat_model.train.userstate import SEAT_KEYS

    room = svc.room(rec_row["room_id"])
    if seat_id not in room.raw:
        from .security import api_error

        api_error(404, "SEAT_NOT_FOUND", "이 열람실에 없는 좌석입니다.")
    occ = set(json.loads(rec_row["occupied_json"]))
    slot = rec_row["time_slot"]
    ranked = json.loads(rec_row["ranked_json"])
    p = svc.profile(user_id)
    with svc.store.db() as db:
        p["answers"] = {
            r["question_id"]: r["answer"]
            for r in db.execute(
                "SELECT question_id,answer FROM persona_answers WHERE user_id=?",
                (user_id,),
            )
        }
    facts = seat_facts(room, seat_id, slot, occ)
    fact_by_feature = {f["featureId"]: f for f in facts if f.get("featureId")}
    available = seat_id not in occ

    # 학습된 취향(앉은 기록): 확신이 충분한 특징
    with svc.lock:
        st, bu = svc._user_state(user_id, p["prior"])
        prof = svc.rec.inferred_profile(user_id)
        n_hist = st.n
        items = []
        if available and len(room.seats) - len(occ) >= 2:
            snap = svc._snapshot(
                room, occ, datetime.fromisoformat(rec_row["requested_at"]), slot
            )
            out = svc.rec.recommend(p["user"], snap, k=len(room.seats), bias=p["bias"])
            items = next(
                (x for x in out["recommendations"] if x["seat_id"] == seat_id), {}
            ).get("reason_items", [])
    learned = {
        i["featureId"]: i for i in prof["all"] if abs(i["z"]) >= 1.0 and n_hist >= 3
    }

    # 추천 이유
    reasons, used = [], set()
    for it in items:
        fid = it.get("featureId")
        if it["code"] == "QUIET_NEIGHBORHOOD":
            reasons.append(
                {
                    "text": "같은 책상 주변 자리가 비어 있어 여유로워요",
                    "source": "situation",
                    "because": "지금 열람실 상황을 함께 봤어요",
                }
            )
            continue
        if it["code"] == "HISTORY_HABIT":
            reasons.append(
                {
                    "text": "전에 앉았던 자리이거나 그 근처예요",
                    "source": "history",
                    "because": "익숙한 자리를 다시 고르시는 편이에요",
                }
            )
            continue
        if not fid or fid in used:
            continue
        used.add(fid)
        high = it["code"] == "FEATURE_HIGH"
        src, because = _source_of(fid, p, learned)
        sent = _reason_sentence(fid, fact_by_feature, high)
        if sent:
            reasons.append(
                {"text": sent, "source": src, "because": because, "featureId": fid}
            )

    # 내 취향과 비교 + 아쉬운 점
    wants = {}  # featureId → (원하는 방향 +1/-1, 출처 말)
    for fid, pref in p["prefs"].items():
        if fid in fact_by_feature:
            wants[fid] = (
                1 if pref["target"] >= 0.5 else -1,
                _source_of(fid, p, learned)[1],
            )
    for j, k in enumerate(SEAT_KEYS):
        b = p["bias"][j]
        if b and k in fact_by_feature:
            wants[k] = (1 if b > 0 else -1, _source_of(k, p, learned)[1])
    for fid, m in p["prior"].items():  # 설문으로 정한 초기값(칸막이·소음)
        if m and fid in fact_by_feature and fid not in wants:
            wants[fid] = (1 if m > 0 else -1, _source_of(fid, p, learned)[1])
    for fid, li in learned.items():
        if fid in fact_by_feature and fid not in wants:
            wants[fid] = (
                1 if li["direction"] == "high" else -1,
                "지금까지 앉은 기록에서 알게 됐어요",
            )
    matches, cautions = [], []
    for fid, (sign, why) in wants.items():
        fc = fact_by_feature[fid]
        lvl = fc["level"]
        if fid == "noise_index":  # 조용함 level은 이미 '조용할수록 높음'
            good = lvl if sign < 0 else 2 - lvl
        else:
            good = lvl if sign > 0 else 2 - lvl
        verdict = "good" if good == 2 else "ok" if good == 1 else "bad"
        want_word = _want_word(fid, sign)
        matches.append(
            {
                "featureId": fid,
                "name": FEATURE_NAME.get(fid, fid),
                "want": want_word,
                "seat": fc["value"],
                "verdict": verdict,
                "because": why,
            }
        )
        if verdict == "bad":
            cautions.append(
                f"{_eul(want_word)} 원하시는데, 이 자리 {fc['title']}{_eun(fc['title'])} '{fc['value']}'"
            )
    matches.sort(key=lambda m: {"good": 0, "ok": 1, "bad": 2}[m["verdict"]])

    rank = ranked.index(seat_id) + 1 if seat_id in ranked else None
    goods = [m["want"] for m in matches if m["verdict"] == "good"][:2]
    if not available:
        summary = "지금은 다른 분이 쓰고 있는 자리예요."
    elif reasons:
        summary = ". ".join(r["text"] for r in reasons[:2]) + "."
    elif goods:
        summary = f"{_eul(', '.join(goods))} 원하시는 취향과 잘 맞아요."
    else:
        summary = "열람실의 빈자리 중 무난하게 좋은 자리예요."
    nearby = _nearby(room, seat_id)
    hours = None
    try:
        from .recommend import _hours_today, KST

        hours = _hours_today(room.opening_hours, datetime.now(KST))
    except Exception:
        pass
    return {
        "seatId": seat_id,
        "name": room.seat_name(seat_id),
        "roomId": room.room_id,
        "roomName": room.room_name,
        "libraryName": room.library_name,
        "rank": rank if rank and rank <= 5 else None,
        "recommended": bool(rank and rank <= 5),
        "available": available,
        "summary": summary,
        "reasons": reasons[:5],
        "cautions": cautions[:3],
        "matches": matches,
        "facts": facts,
        "nearby": nearby,
        "room": {
            "laptopAllowed": room.laptop_allowed,
            "hours": hours,
            "deskType": room_desk_label(room.raw[seat_id]["desk_type"]),
        },
        "learning": {
            "historySessions": n_hist,
            "text": f"지금까지 고른 자리로 취향을 배우고 있어요."
            if n_hist
            else "자리를 고를수록 내 취향을 더 잘 알게 돼요.",
        },
        "virtual": "자리 환경(콘센트·조명·바람 등)은 실제 측정이 아닌 가상 데이터예요.",
    }


def _has_batchim(word):
    ch = word.strip()[-1] if word.strip() else ""
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def _eul(w):
    return w + ("을" if _has_batchim(w) else "를")


def _eun(w):
    return "은" if _has_batchim(w) else "는"


def _i(w):
    return "이" if _has_batchim(w) else "가"


def _want_word(fid, sign):
    return {
        "outletAvailable": ("콘센트 있는 자리", "콘센트 없어도 되는 자리"),
        "partition": ("칸막이 자리", "트인 자리"),
        "windowProximity": ("창가 자리", "안쪽 자리"),
        "brightness": ("밝은 자리", "은은한 자리"),
        "airflow": ("시원한 자리", "바람 약한 자리"),
        "deskWidth": ("넓은 책상", "아담한 책상"),
        "noise_index": ("시끌벅적한 자리", "조용한 자리"),
        "toiletProximity": ("화장실 가까운 자리", "화장실에서 먼 자리"),
        "entranceProximity": ("출입구 가까운 자리", "출입구에서 먼 자리"),
        "viewQuality": ("전망 좋은 자리", "전망 상관없는 자리"),
        "laptopAllowed": ("노트북 되는 자리", "노트북 안 되는 자리"),
    }.get(fid, (fid, fid))[0 if sign > 0 else 1]


def _nearby(room, sid):
    """가까운 시설을 말로: 가장 가까운 창문·냉방기·콘센트·출입구 방향."""
    b = room.bbox_px[sid]
    cx, cy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    m = room.mpp
    out = []

    def dist(pt):
        return ((pt[0] - cx) ** 2 + (pt[1] - cy) ** 2) ** 0.5 * m

    def word(d):
        return (
            "바로 옆"
            if d <= 2
            else "가까이"
            if d <= 5
            else "조금 떨어져"
            if d <= 10
            else "멀리"
        )

    for t, name in (
        ("air_conditioner", "에어컨"),
        ("outlet", "콘센트"),
        ("entrance", "출입구"),
        ("ceiling_light", "천장 조명"),
    ):
        pts = [o["point"] for o in room.objects if o.get("type") == t]
        if pts:
            d = min(dist(p) for p in pts)
            out.append({"type": t, "text": f"{name}{_i(name)} {word(d)} 있어요"})
    wins = [o for o in room.objects if o.get("type") == "window"]
    if wins:
        d = min(
            dist(
                (
                    (w["line"][0][0] + w["line"][1][0]) / 2,
                    (w["line"][0][1] + w["line"][1][1]) / 2,
                )
            )
            for w in wins
        )
        out.append({"type": "window", "text": f"창문이 {word(d)} 있어요"})
    return out
