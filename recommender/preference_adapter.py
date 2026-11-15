"""카카오톡·자연어 취향 분석 결과(kakao_analysis/schema/kakaotalk_schema.json 형식) → 추천 엔진 취향 프로필(features-2).

두 어휘를 모두 받는다.
  - 문서 표(kakaotalk_analysis.md §2): near, bright/dim, cool/warm, large/compact, yes/no, required/not_required, window/no_preference
  - 응답 예시 어휘: avoid_direct_airflow, height_adjustable, uses_laptop ...
같은 뜻은 하나의 표준값(canonical)으로 묶고, 더 구체적인 값은 의미를 보존한다.
features-2 에 대응 특성이 없는 값은 버리지 않고 `unmapped` 에 남긴다(명세 5.2: 자료 없는 취향도 저장 가능, 반영 안 됨을 명시).

    from preference_adapter import load_analysis, to_profile
    raw = load_analysis("kakao_analysis/schema/kakaotalk_schema.json")      # 깨진 JSON도 가능한 범위에서 복구
    prof = to_profile(raw)                                     # {preferences, requirements, mapping, unmapped, warnings}

python3 -I recommender/preference_adapter.py <파일> 로 변환 결과를 출력한다.
"""

import json
import re
import sys

FEATURE_REGISTRY = "features-2"
FEATURES = (
    "restroom",
    "lighting",
    "air_conditioning",
    "desk",
    "laptop_usage",
    "outlet",
    "view",
)

# 원시값 → 표준값. 키는 정규화(소문자, 공백·하이픈→_) 후 비교한다.
ALIASES = {
    "restroom": {
        "near": "near",
        "close": "near",
        "near_restroom": "near",
        "no_preference": "no_preference",
        "any": "no_preference",
        "dont_care": "no_preference",
    },
    "lighting": {
        "bright": "bright",
        "brighter": "bright",
        "natural_light": "bright",
        "dim": "dim",
        "dark": "dim",
        "darker": "dim",
    },
    "air_conditioning": {
        "cool": "cool",
        "cold": "cool",
        "cooler": "cool",
        "warm": "warm",
        "warmer": "warm",
        "avoid_direct_airflow": "avoid_direct_airflow",
        "no_direct_airflow": "avoid_direct_airflow",
    },
    "desk": {
        "large": "large",
        "wide": "large",
        "big": "large",
        "compact": "compact",
        "small": "compact",
        "height_adjustable": "height_adjustable",
        "adjustable": "height_adjustable",
    },
    "laptop_usage": {
        "yes": "yes",
        "uses_laptop": "yes",
        "laptop": "yes",
        "true": "yes",
        "no": "no",
        "no_laptop": "no",
        "does_not_use_laptop": "no",
        "false": "no",
    },
    "outlet": {
        "required": "required",
        "needed": "required",
        "yes": "required",
        "not_required": "not_required",
        "not_needed": "not_required",
        "no": "not_required",
    },
    "view": {
        "window": "window",
        "window_seat": "window",
        "near_window": "window",
        "no_preference": "no_preference",
        "any": "no_preference",
    },
}
NO_SIGNAL = {"unknown", "", "none", "null", "n/a"}

# 표준값 → [(featureId, target, base_importance, kind)]
#   kind=direct: 특성 정의와 같은 뜻 / proxy: 근사(가중치 절반) / none: 점수에 쓰지 않음(정상적인 '무관')
MAPPING = {
    ("restroom", "near"): [("toiletProximity", 1.0, 0.7, "direct")],
    ("restroom", "no_preference"): [],
    ("lighting", "bright"): [("brightness", 0.85, 0.7, "direct")],
    ("lighting", "dim"): [("brightness", 0.3, 0.6, "direct")],
    # features-2 에도 온도 특성이 없고 airflow(직접 바람 세기)만 있다 → 온도 선호는 근사, 바람 회피는 직접 대응
    ("air_conditioning", "cool"): [("airflow", 0.7, 0.6, "proxy")],
    ("air_conditioning", "warm"): [("airflow", 0.1, 0.6, "proxy")],
    ("air_conditioning", "avoid_direct_airflow"): [("airflow", 0.0, 0.9, "direct")],
    ("desk", "large"): [("deskWidth", 1.0, 0.6, "direct")],
    ("desk", "compact"): [("deskWidth", 0.3, 0.3, "direct")],
    (
        "desk",
        "height_adjustable",
    ): None,  # 대응 특성 없음(deskHeight는 높이 수준이지 조절 가능 여부가 아님) → unmapped
    ("laptop_usage", "yes"): [("laptopAllowed", 1, 1.0, "direct")],
    ("laptop_usage", "no"): [],
    ("outlet", "required"): [("outletAvailable", 1, 0.9, "direct")],
    ("outlet", "not_required"): [],
    ("view", "window"): [
        ("windowProximity", 1.0, 0.6, "direct"),
        ("viewQuality", 1.0, 0.4, "proxy"),
    ],
    ("view", "no_preference"): [],
}
INFERENCE_WEIGHT = {"direct": 1.0, "contextual": 0.7, "unknown": 0.0}
PROXY_FACTOR = 0.5
LAPTOP_REQUIREMENT_MIN_CONF = (
    0.6  # 노트북 사용 확신이 이 이상이면 필수 조건으로도 넣는다(명세 5.1)
)


def norm(v):
    return re.sub(r"[\s\-]+", "_", str(v).strip().lower()) if v is not None else ""


def canonical(feature, raw_value):
    """(표준값|None, 상태) — 상태: ok | no_signal | unknown_value"""
    v = norm(raw_value)
    if v in NO_SIGNAL:
        return None, "no_signal"
    c = ALIASES.get(feature, {}).get(v)
    return (c, "ok") if c else (None, "unknown_value")


def importance(base, conf, inference_type, kind):
    conf = max(0.0, min(1.0, float(conf or 0)))
    w = base * conf * INFERENCE_WEIGHT.get(norm(inference_type), 0.7)
    if kind == "proxy":
        w *= PROXY_FACTOR
    return round(min(1.0, w), 3)


def to_profile(analysis):
    """분석 결과 dict → {featureRegistryVersion, preferences, requirements, mapping, unmapped, warnings}.

    같은 featureId가 여러 항목에서 나오면 importance가 큰 쪽을 쓴다. participant_id 등 개인 식별 필드는 결과에 넣지 않는다.
    """
    prefs_in = (analysis or {}).get("preferences") or {}
    out, mapping, unmapped, warnings, reqs = {}, [], [], [], []
    for f in prefs_in:
        if f not in FEATURES:
            warnings.append(f"알 수 없는 항목 '{f}' 무시")
    for f in FEATURES:
        p = prefs_in.get(f)
        if not isinstance(p, dict):
            mapping.append({"feature": f, "raw": None, "status": "missing"})
            continue
        raw, conf, itype = (
            p.get("preference"),
            p.get("confidence", 0),
            p.get("inference_type"),
        )
        c, status = canonical(f, raw)
        entry = {
            "feature": f,
            "raw": raw,
            "canonical": c,
            "status": status,
            "confidence": conf,
            "inference_type": itype,
            "mapped": [],
        }
        if status == "unknown_value":
            warnings.append(f"{f}: 허용되지 않은 값 '{raw}' → unknown 처리")
            unmapped.append({"feature": f, "raw": raw, "reason": "허용 값 목록에 없음"})
        elif status == "ok":
            targets = MAPPING.get((f, c))
            if targets is None:
                unmapped.append(
                    {
                        "feature": f,
                        "raw": raw,
                        "canonical": c,
                        "reason": "features-2에 대응 특성 없음(저장만, 점수 미반영)",
                    }
                )
                entry["status"] = "no_feature"
            for fid, t, base, kind in targets or []:
                imp = importance(base, conf, itype, kind)
                entry["mapped"].append(
                    {"featureId": fid, "target": t, "importance": imp, "kind": kind}
                )
                if imp > 0 and (fid not in out or imp > out[fid]["importance"]):
                    out[fid] = {"featureId": fid, "target": t, "importance": imp}
            if (
                f == "laptop_usage"
                and c == "yes"
                and float(conf or 0) >= LAPTOP_REQUIREMENT_MIN_CONF
            ):
                reqs.append(
                    {"featureId": "laptopAllowed", "operator": "eq", "value": 1}
                )
        mapping.append(entry)
    return {
        "featureRegistryVersion": FEATURE_REGISTRY,
        "preferences": sorted(out.values(), key=lambda x: x["featureId"]),
        "requirements": reqs,
        "mapping": mapping,
        "unmapped": unmapped,
        "warnings": warnings,
    }


def _repair(text):
    """흔한 출력 오류 복구: 바깥 중괄호 누락, 문자열 값 안의 이스케이프 안 된 큰따옴표(한 줄에 "키": "값" 형태일 때)."""
    t = text.strip()
    if not t.startswith(("{", "[")):
        t = "{" + t + "}"
    fixed = []
    for line in t.splitlines():
        m = re.match(r'^(\s*"[^"]+"\s*:\s*")(.*)("\s*,?\s*)$', line)
        if m and '"' in m.group(2):
            line = (
                m.group(1)
                + m.group(2).replace('\\"', '"').replace('"', '\\"')
                + m.group(3)
            )
        fixed.append(line)
    return "\n".join(fixed)


def load_analysis(path_or_text):
    """파일 경로 또는 JSON 문자열 → (dict | list[dict], warnings). 엄격 파싱 실패 시 복구를 시도하고 경고를 남긴다."""
    text = path_or_text
    if not str(path_or_text).lstrip().startswith(("{", "[", '"')):
        with open(path_or_text, encoding="utf-8") as f:
            text = f.read()
    try:
        return json.loads(text), []
    except json.JSONDecodeError as e:
        first = f"올바른 JSON 아님({e.msg}, {e.lineno}행) → 복구 시도"
    try:
        return json.loads(_repair(text)), [
            first,
            "복구 성공: 원본 출력의 JSON 검증을 요청할 것",
        ]
    except json.JSONDecodeError as e:
        raise ValueError(f"{first}; 복구 실패({e.msg}, {e.lineno}행)") from None


if __name__ == "__main__":
    data, warns = load_analysis(sys.argv[1])
    items = data if isinstance(data, list) else [data]
    for w in warns:
        print("WARN", w, file=sys.stderr)
    print(json.dumps([to_profile(x) for x in items], ensure_ascii=False, indent=2))
