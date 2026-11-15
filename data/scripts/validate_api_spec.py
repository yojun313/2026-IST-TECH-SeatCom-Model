"""API 명세 검증: OpenAPI 구조, 모든 요청·응답 예시의 JSON Schema, 거절돼야 할 입력, 예시 계산값, Markdown 예시·링크.

    uv run data/scripts/validate_api_spec.py             # 결과를 data/validation/API_SPEC_VALIDATION.json 에 기록
openapi-spec-validator 가 설치돼 있으면 OpenAPI 3.1 구조 검증도 수행한다(없으면 건너뛰고 결과에 기록).
"""

import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

import jsonschema

DATA = Path(__file__).resolve().parents[1]
DOCS = DATA / "docs"
API_DOCS = DOCS / "be-model"  # OpenAPI·API_REFERENCE 위치
REPO = DATA.parent
sys.path.insert(0, str(REPO / "recommender"))
checks, failures, skipped = [], [], []


def ok(msg):
    checks.append(msg)


def fail(msg):
    failures.append(msg)


def validator(doc, ref):
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": ref,
        "components": doc["components"],
    }
    return jsonschema.Draft202012Validator(
        root, format_checker=jsonschema.FormatChecker()
    )


def examples(doc):
    for path, item in doc["paths"].items():
        for method, op in item.items():
            rb = op.get("requestBody", {}).get("content", {}).get("application/json")
            if rb:
                for name, ex in (rb.get("examples") or {}).items():
                    yield (
                        f"{method.upper()} {path} request[{name}]",
                        rb["schema"]["$ref"],
                        ex["value"],
                    )
            for code, r in op["responses"].items():
                c = r.get("content", {}).get("application/json")
                if c:
                    for name, ex in (c.get("examples") or {}).items():
                        yield (
                            f"{method.upper()} {path} {code}[{name}]",
                            c["schema"]["$ref"],
                            ex["value"],
                        )


def check_doc(fname):
    doc = json.loads((API_DOCS / fname).read_text(encoding="utf-8"))
    try:
        from openapi_spec_validator import validate

        validate(doc)
        ok(f"{fname}: OpenAPI {doc['openapi']} 구조 검증 통과")
    except ImportError:
        skipped.append(f"{fname}: openapi-spec-validator 미설치로 구조 검증 생략")
    except Exception as e:  # noqa: BLE001
        fail(f"{fname}: OpenAPI 구조 오류 {e}")
    n = 0
    for label, ref, value in examples(doc):
        errs = sorted(validator(doc, ref).iter_errors(value), key=lambda e: e.path)
        n += 1
        if errs:
            fail(f"{fname} {label}: {errs[0].message} @ {list(errs[0].path)}")
    ok(f"{fname}: 요청·응답 예시 {n}개 JSON Schema 검증")
    return doc, n


model, n_model = check_doc("MODEL_API.openapi.json")
backend, n_backend = check_doc("BACKEND_SEATS_API.openapi.json")

# ---------------------------------------------------------------- 거절돼야 할 입력
ex = lambda path, kind="request", name=None, code="200": next(
    v
    for l, r, v in examples(model)
    if l.startswith(f"POST {path} ")
    and (f"request[{name}]" in l if kind == "request" else f" {code}[{name}]" in l)
)
neg = []
seat = copy.deepcopy(ex("/v1/recommendations/seats", name="simulationLearned"))
bad = copy.deepcopy(seat)
bad["candidates"][0]["features"]["neighborOccupancy"] = bad["candidates"][0][
    "features"
]["brightness"]
neg.append(
    (
        "실시간 특성을 정적 features 에 넣음",
        "#/components/schemas/SeatRecommendationRequest",
        bad,
    )
)
bad = copy.deepcopy(seat)
bad["profile"]["preferences"][0]["target"] = 0.5
neg.append(
    ("이진 특성 target=0.5", "#/components/schemas/SeatRecommendationRequest", bad)
)
bad = copy.deepcopy(seat)
bad["profile"]["preferences"][0]["source"] = "persona"
neg.append(
    ("미등록 preference source", "#/components/schemas/SeatRecommendationRequest", bad)
)
bad = copy.deepcopy(seat)
bad["options"]["exploration"]["rate"] = 0.5
neg.append(
    ("탐색 비율 0.3 초과", "#/components/schemas/SeatRecommendationRequest", bad)
)
bad = copy.deepcopy(seat)
bad["candidates"][0]["features"]["outletAvailable"]["verification"] = "guessed"
neg.append(
    ("미등록 verification", "#/components/schemas/SeatRecommendationRequest", bad)
)
learn = ex("/v1/preferences/learn", name="simulation")
bad = copy.deepcopy(learn)
bad["executionMode"] = "preview"
neg.append(("M10 preview 모드", "#/components/schemas/LearnPreferencesRequest", bad))
bad = copy.deepcopy(learn)
bad["observations"][0]["choiceSet"] = []
neg.append(("M10 빈 choiceSet", "#/components/schemas/LearnPreferencesRequest", bad))
bad = copy.deepcopy(learn)
bad["observations"] = [learn["observations"][0]] * 201
neg.append(("M10 관측 200개 초과", "#/components/schemas/LearnPreferencesRequest", bad))
nl = ex("/v1/preferences/from-analysis", name="synthetic")
bad = copy.deepcopy(nl)
del bad["analysis"]["preferences"]
neg.append(
    ("M09 preferences 누락", "#/components/schemas/PreferenceFromAnalysisRequest", bad)
)
bad = copy.deepcopy(nl)
bad["analysis"]["preferences"]["outlet"]["confidence"] = 1.5
neg.append(
    (
        "M09 confidence 범위 밖",
        "#/components/schemas/PreferenceFromAnalysisRequest",
        bad,
    )
)
for label, ref, value in neg:
    if validator(model, ref).is_valid(value):
        fail(f"거절돼야 하는 입력이 통과: {label}")
ok(f"거절 입력 {len(neg)}종 확인: " + ", ".join(l for l, _, _ in neg))

# ---------------------------------------------------------------- 계산값 검산
res = ex("/v1/recommendations/seats", "response", "simulationLearned")
prefs = seat["profile"]["preferences"]
W = sum(p["importance"] for p in prefs)
cands = {c["candidateId"]: c for c in seat["candidates"]}
prev = None
for r in res["results"]:
    c = cands[r["targetId"]]
    x = {k: v["value"] for k, v in c["features"].items()}
    x.update(
        neighborOccupancy=c["realtime"]["neighborOccupancy"],
        roomOccupancy=c["realtime"]["roomOccupancy"],
    )
    fs = (
        sum(p["importance"] * (1 - abs(p["target"] - x[p["featureId"]])) for p in prefs)
        / W
    )
    pers = 0.9 * fs + 0.1 * c["choiceAffinity"]
    if (
        abs(fs - r["scores"]["featureScore"]) > 1e-3
        or abs(pers - r["scores"]["finalScore"]) > 1e-3
    ):
        fail(f"좌석 점수 불일치 {r['targetId']}")
    if prev is not None and pers > prev:
        fail("좌석 순위가 점수 내림차순이 아님")
    prev = pers
if len({c["realtime"]["snapshotId"] for c in seat["candidates"]}) != 1:
    fail("한 요청의 snapshotId 불일치")
ok(
    "simulation 좌석 예시: 학습 프로필+실시간 특성으로 6.2절 점수·순위 재계산 일치, snapshotId 단일"
)

from preference_adapter import to_profile  # noqa: E402

P = lambda **kw: {
    "preferences": {
        k: {"preference": v, "confidence": 0.9, "inference_type": "direct"}
        for k, v in kw.items()
    }
}
a, b = to_profile(P(laptop_usage="yes")), to_profile(P(laptop_usage="uses_laptop"))
if a["preferences"] != b["preferences"] or a["requirements"] != b["requirements"]:
    fail("M09 어휘 동치 실패(yes vs uses_laptop)")
af = {
    p["featureId"]: p
    for p in to_profile(P(air_conditioning="avoid_direct_airflow"))["preferences"]
}
if af.get("airflow", {}).get("target") != 0.0:
    fail("M09 avoid_direct_airflow 변환 오류")
if to_profile(P(desk="height_adjustable"))["unmapped"][0]["raw"] != "height_adjustable":
    fail("M09 height_adjustable unmapped 아님")
m09 = ex("/v1/preferences/from-analysis", "response", "synthetic")
ref = to_profile(nl["analysis"])
if [(p["featureId"], p["target"], p["importance"]) for p in m09["preferences"]] != [
    (p["featureId"], p["target"], p["importance"]) for p in ref["preferences"]
]:
    fail("M09 응답 예시가 참조 구현 결과와 다름")
ok(
    "M09: 표·예시 어휘 동치(yes=uses_laptop), avoid_direct_airflow→airflow 0 direct, height_adjustable→unmapped, 응답 예시=참조 구현 결과"
)

m10 = ex("/v1/preferences/learn", "response", "simulation")
ignored = {o["eventId"] for o in m10["observationsIgnored"]}
used = [o for o in learn["observations"] if o["eventId"] not in ignored]
if len(used) != m10["observationsUsed"]:
    fail("M10 observationsUsed 불일치")
for e in m10["evidence"]:
    f = e["featureId"]
    ch = sum(
        next(c for c in o["choiceSet"] if c["candidateId"] == o["chosenCandidateId"])[
            "values"
        ][f]
        for o in used
    ) / len(used)
    av = sum(
        sum(c["values"][f] for c in o["choiceSet"]) / len(o["choiceSet"]) for o in used
    ) / len(used)
    if abs(ch - e["chosenRate"]) > 1e-3 or abs(av - e["availableRate"]) > 1e-3:
        fail(f"M10 근거 요약 불일치 {f}")
for o in learn["observations"]:
    if o["chosenCandidateId"] not in {c["candidateId"] for c in o["choiceSet"]}:
        fail(f"M10 선택 좌석이 choiceSet 에 없음 {o['eventId']}")
locked_in = {
    p["featureId"]: p for p in learn["profile"]["preferences"] if p.get("locked")
}
locked_out = {p["featureId"]: p for p in m10["learnedPreferences"]}
if any(locked_out.get(k) != v for k, v in locked_in.items()):
    fail("M10 locked 항목이 바뀜")
ok(
    "M10: 제외 관측(선택지 1개) 반영 observationsUsed, 근거 요약 chosenRate·availableRate 재계산 일치, locked 불변, 선택 좌석⊂choiceSet"
)

# ---------------------------------------------------------------- Markdown
md = (DOCS / "BACKEND_MODEL_API_SPEC.md").read_text(encoding="utf-8")
blocks = re.findall(r"```json\n(.*?)```", md, re.S)
for i, bl in enumerate(blocks):
    try:
        json.loads(bl)
    except json.JSONDecodeError as e:
        fail(f"Markdown JSON 예시 {i + 1} 파싱 실패: {e}")
for m in re.findall(r"\]\(([^)#]+?)\)", md):
    if not m.startswith("http") and not (DOCS / m).exists():
        fail(f"Markdown 내부 링크 없음: {m}")
mids = sorted(set(re.findall(r"^### (M\d\d)\.", md, re.M)))
bids = sorted(set(re.findall(r"^### (B\d\d)\.", md, re.M)))
if mids != [f"M{i:02d}" for i in range(1, 11)] or bids != [
    f"B{i:02d}" for i in range(1, 14)
]:
    fail(f"API 섹션 누락: {mids} {bids}")
if "features-1" in json.dumps(model) or "features-1" in json.dumps(backend):
    fail("OpenAPI 에 features-1 잔존")
ok(
    f"Markdown JSON 예시 {len(blocks)}개 파싱/내부 링크/모델 {len(mids)}+백엔드 {len(bids)}개 섹션 확인"
)

out = {
    "status": "passed" if not failures else "failed",
    "specVersion": "2.0.0-design",
    "validatedExamples": n_model + n_backend,
    "checks": checks,
    "failures": failures,
    "skipped": skipped,
    "limitations": [
        "API 서버·추천 엔진·M10 학습 모델은 미구현. 예시의 학습값은 형식 예시이며 모델 출력이 아니다.",
        "합성(DEMO·SIM) 예시와 계산식만 검사했다. 실환경 현황 연동·추천 품질·성능을 입증하지 않는다.",
    ],
    "데이터기준일자": date.today().isoformat(),
}
(DATA / "validation" / "API_SPEC_VALIDATION.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(out, ensure_ascii=False, indent=2))
sys.exit(1 if failures else 0)
