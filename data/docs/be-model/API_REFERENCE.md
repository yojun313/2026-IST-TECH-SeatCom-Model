# SeatCom API 레퍼런스 (2.0.0-design)

좌석 선택 이력에서 취향을 학습하고 실시간 좌석 현황에서 최적 좌석을 추천하는 서비스의 API 목록이다. 구현 전 설계 계약이며, 기계 판독 계약은 [MODEL_API.openapi.json](MODEL_API.openapi.json)·[BACKEND_SEATS_API.openapi.json](BACKEND_SEATS_API.openapi.json), 설계 근거는 [BACKEND_MODEL_API_SPEC.md](BACKEND_MODEL_API_SPEC.md)에 있다.

## 목차

| ID | 메서드·경로 | 기능 |
|---|---|---|
| **모델 API** (백엔드만 호출) | | |
| M01 | `GET /health` | 생존·버전 확인 |
| M02 | `GET /v1/capabilities` | 지원 기능 조회 |
| M03 | `GET /v1/features` | 특성 목록 조회 |
| M04 | `POST /v1/preferences/initialize` | 설문 → 초기 취향 |
| M05 | `POST /v1/recommendations/rooms` | 열람실 추천 |
| M06 | `POST /v1/recommendations/seats` | 좌석 추천 |
| M07 | `POST /v1/recommendations/libraries` | 주변 도서관 추천 |
| M08 | `POST /v1/preferences/update` | 명시적 교정 반영 |
| M09 | `POST /v1/preferences/from-analysis` | 카카오톡·자연어 분석 → 사전 취향 |
| M10 | `POST /v1/preferences/learn` | 선택 이력 → 취향 학습 |
| **백엔드 API** (프론트 호출) | | |
| B01 | `GET /api/v1/libraries` | 도서관 목록 |
| B02 | `GET /api/v1/libraries/{libraryId}/rooms` | 열람실 목록 |
| B03 | `GET /api/v1/rooms/{roomId}/map` | 좌석도·시설 레이어 |
| B04 | `GET /api/v1/rooms/{roomId}/availability` | 실시간 좌석 현황 |
| B05 | `GET /api/v1/users/me/preferences` | 내 취향 조회 |
| B06 | `PUT /api/v1/users/me/preferences` | 내 취향 직접 설정 |
| B07 | `POST /api/v1/recommendations/rooms` | 열람실 추천 생성 |
| B08 | `POST /api/v1/recommendations/seats` | 좌석 추천 생성 |
| B09 | `POST /api/v1/recommendations/{recommendationId}/events` | 노출·선택·이용·평가 기록 |
| B10 | `POST /api/v1/recommendations/libraries` | 주변 도서관 추천 생성 |
| B11 | `GET /api/v1/rooms/{roomId}/seats` | 좌석 좌표 목록 |
| B12 | `POST /api/v1/users/me/preference-analyses` | 자연어 분석 결과 등록 |
| B13 | `GET /api/v1/users/me/preferences/insights` | 이력에서 찾은 내 취향 |

---

## 0. 공통 규칙

| 항목 | 모델 API | 백엔드 API |
|---|---|---|
| 주소 | `http://model-api:8000` | `http://backend-api:3000` |
| 인증 | 서비스 Bearer 토큰 | 사용자 Bearer 토큰 |
| 성공 응답 | 본문 그대로 | `{data, meta:{requestId}}` |

- JSON·camelCase. 시간은 오프셋 포함 RFC3339(`2026-11-14T17:30:00+09:00`). 예외: M09의 `analysis`는 분석기 원본(snake_case).
- 알 수 없는 필드·특성 ID·열거값은 422. `null`은 "미상"이며 0·false가 아니다.
- 생성·수정 요청은 `Idempotency-Key` 헤더를 쓴다.

**오류 형식**

```json
{"error":{"code":"INVALID_INPUT","message":"latitude must be between -90 and 90","requestId":"req-1","details":[{"path":"origin.latitude","reason":"OUT_OF_RANGE"}]}}
```

| HTTP | 코드 | 의미 |
|---|---|---|
| 401 / 403 | `UNAUTHENTICATED` / `FORBIDDEN` | 인증·권한 없음 |
| 404 | `NOT_FOUND`, `MAP_NOT_AVAILABLE` | 대상 없음 |
| 409 | `REVISION_CONFLICT`, `RECOMMENDATION_EXPIRED`, `AVAILABILITY_CHANGED`, `PREVIEW_NOT_SELECTABLE`, `IDEMPOTENCY_CONFLICT`, `CURSOR_VERSION_CHANGED` | 최신 상태로 다시 요청 |
| 422 | `INVALID_INPUT`, `FEATURE_VERSION_MISMATCH`, `UNSUPPORTED_FEATURE` | 입력 계약 위반 |
| 429 | `RATE_LIMITED` | 요청 제한 |
| 503 | `NOT_READY`, `MODEL_UNAVAILABLE` | 서비스 준비 안 됨 |

후보가 없거나 모두 조건 탈락한 경우는 오류가 아니라 **200**과 `outcome=noCandidates|noEligibleCandidates`다.

**실행 모드** `executionMode`: `live`(운영) · `preview`(검수 전 자료 미리보기) · `simulation`(합성 데이터).

---

## 1. 공통 데이터 형식

### 특성 목록 (`features-2`)

| featureId | 종류 | 의미 (0 → 1) | 출처 |
|---|---|---|---|
| `laptopAllowed` | 이진 | 노트북 금지 → 허용 | 정적 |
| `keyboardAllowed` | 이진 | 키보드 금지 → 허용 | 정적 |
| `partition` | 이진 | 칸막이 없음 → 있음 | 정적 |
| `outletAvailable` | 이진 | 콘센트 없음 → 있음 | 정적 |
| `windowProximity` | 연속 | 창문에서 멂 → 가까움 | 정적 |
| `toiletProximity` | 연속 | 화장실에서 멂 → 가까움 | 정적 |
| `entranceProximity` | 연속 | 출입구에서 멂 → 가까움 | 정적 |
| `brightness` | 연속 | 어두움 → 밝음 | 정적 |
| `airflow` | 연속 | 직접 바람 약함 → 강함 | 정적 |
| `deskHeight` | 연속 | 낮음 → 높음 | 정적 |
| `deskWidth` | 연속 | 좁음 → 넓음 | 정적 |
| `viewQuality` | 연속 | 시야 제한 → 좋은 전망 | 정적 |
| `neighborOccupancy` | 연속 | 주변 좌석 비어 있음 → 모두 참 | 실시간 |
| `roomOccupancy` | 연속 | 열람실 한산 → 만석 | 실시간 |

### Preference (취향 항목)

| 필드 | 형식 | 설명 |
|---|---|---|
| `featureId` | 특성 ID | |
| `target` | 0~1 | 원하는 수준. 이진 특성은 0 또는 1 |
| `importance` | 0~1 | 중요도. 0이면 점수에서 제외 |
| `source` | `explicit\|survey\|nlAnalysis\|learned\|correction` | 출처(선택, 기본 explicit) |
| `locked` | bool | true면 학습이 바꾸지 않음(선택) |
| `uncertainty` | 0~1 | 학습 불확실성, 0=확신(선택) |
| `evidenceCount` | 정수 | 학습에 쓴 선택 수(선택) |

**Profile**: `{revision, featureRegistryVersion:"features-2", preferences:[Preference ≤20]}`
**Requirement** (필수 조건): `{featureId, operator:eq|gte|lte, value}`. 이진 특성은 `eq`만.

### Candidate (추천 후보)

| 필드 | 설명 |
|---|---|
| `candidateId` | room이면 roomId, seat이면 seatId |
| `kind` | `room\|seat` |
| `libraryId`, `roomId`, `seatId` | seatId는 room 후보에서 null |
| `identityVerified`, `geometryVerified` | 번호·ID 대응 / 지도 위치 검수 여부 |
| `mapAnchor` | `{mapId,x,y,width,height}` 0~1 정규화 좌표 또는 null |
| `operatingState` | `open\|closed\|unknown` |
| `features` | `{featureId: {value, verification, scope, appliesToAllSeats, sourceId, validUntil}}` 정적 특성만. verification=`verified\|unverified\|missing\|synthetic`(synthetic은 simulation 전용) |
| `availability` | 아래 현황 |
| `realtime` | `{snapshotId, observedAt, neighborOccupancy, roomOccupancy}` 또는 null. 한 요청 내 snapshotId 동일 |
| `choiceAffinity` | 0~1. 같은 좌석·열람실 재방문 습관 |

**Availability** (현황, 셋 중 하나)

```json
{"scope":"none"}
{"scope":"roomAggregate","totalSeats":100,"remainingSeats":20,"observedAt":"…","expiresAt":"…","sourceId":"…"}
{"scope":"seat","state":"available|occupied|reserved|blocked|unknown","observedAt":"…","expiresAt":"…","sourceId":"…"}
```

### ChoiceObservation (선택 관측, M10 입력)

| 필드 | 설명 |
|---|---|
| `eventId`, `occurredAt`, `roomId`, `snapshotId` | 선택 이벤트와 당시 현황 스냅샷 |
| `context` | `{purpose:study\|laptopWork\|reading\|shortStay\|unknown, expectedStayMinutes}` (선택) |
| `chosenCandidateId` | 고른 좌석 |
| `origin` | `recommendation\|outsideRecommendations` |
| `exposedRank`, `exposurePropensity` | 추천에서 고른 경우 노출 순위·확률, 아니면 null |
| `choiceSet` | 선택 시점의 빈자리 후보 전체(선택 좌석 포함, 1~500). 각 `{candidateId, values:{featureId: 0~1\|null}}` |
| `outcome` | `{useConfirmed, stayMinutes, endedEarly, rating}` (선택) |

### 추천 공통 요청 (M05·M06·M07)

| 필드 | 필수 | 설명 |
|---|---|---|
| `requestId` | ✔ | |
| `evaluatedAt` | ✔ | 계산 기준 시각 |
| `datasetVersion` | ✔ | 정적 데이터 버전 |
| `featureRegistryVersion` | ✔ | `features-2` |
| `executionMode` | ✔ | `live\|preview\|simulation` |
| `profile` | ✔ | 저장 프로필 |
| `preferenceOverrides` | | 오늘만 적용할 취향(저장 안 됨) |
| `requirements` | | 필수 조건 |
| `excludedCandidateIds` | | 사용자가 제외한 후보 |
| `context` | | 방문 맥락 |
| `options` | | `topK`(1~20, 기본5), `availabilityPolicy`(`confirmedOnly` 기본 \| `allowUnknown`), `maxAvailabilityAgeSeconds`(1~300, 기본120), `minFeatureCoverage`(0~1), `exploration:{enabled, rate 0~0.3}` |
| `candidates` | ✔ | 후보 0~5,000개 |

### 추천 공통 응답

| 필드 | 설명 |
|---|---|
| `requestId`, `engineVersion`, `featureRegistryVersion`, `datasetVersion`, `evaluatedAt`, `executionMode` | 요청·버전 정보 |
| `outcome` | `ranked\|noCandidates\|noEligibleCandidates` |
| `results[]` | 아래 결과 항목 (≤20) |
| `excluded[]` | `{candidateId, reasonCodes[]}` |
| `warnings[]`, `statistics` | 경고, `{inputCandidates, eligibleCandidates, returnedTargets}` |

**결과 항목**: `rank`, `targetType(library|room|seat)`, `targetId`, `libraryId`, `roomId`, `seatId`, `representativeCandidateId`, `mapAnchor`, `rankingBasis`, `scores{featureScore, featureCoverage, choiceAffinity, personalizationScore, distanceUtility, effectiveDistanceWeight, finalScore}`, `availability{effectiveState, scope, observedAt, expiresAt}`, `availabilityVerified`, `distance`, `reasons[{code, featureId, sourceId, text}]`, `warnings[]`, `previewOnly`, `exposurePropensity`.

**제외 코드**: CLOSED, EXCLUDED_BY_USER, UNVERIFIED_IDENTITY, UNVERIFIED_GEOMETRY, KNOWN_UNAVAILABLE, AVAILABILITY_UNKNOWN, HARD_REQUIREMENT_UNKNOWN, HARD_REQUIREMENT_NOT_MET, INSUFFICIENT_FEATURE_COVERAGE, MISSING_LIBRARY_LOCATION, OUTSIDE_RADIUS
**경고 코드**: AVAILABILITY_UNKNOWN, OPERATING_STATE_UNKNOWN, FEATURES_PARTIALLY_UNKNOWN, INSUFFICIENT_PREFERENCE_DATA, PREVIEW_ONLY, SIMULATION_DATA, EXPLORATION_APPLIED, LEARNED_PREFERENCES_UNCERTAIN, REALTIME_FEATURES_UNKNOWN, STRAIGHT_LINE_DISTANCE_ONLY
**이유 코드**: FEATURE_MATCH, HISTORY_PATTERN, HABIT, REALTIME_QUIET, AVAILABILITY_CONFIRMED

---

## 2. 모델 API

### M01. `GET /health` — 생존·버전 확인

- **설명**: 모델 프로세스가 요청을 처리할 수 있는지 확인한다.
- **입력**: 없음
- **출력**: `{status:"ok", serviceVersion, engineVersion, modelVersion}`
- **오류**: 503 처리 불가

### M02. `GET /v1/capabilities` — 지원 기능 조회

- **설명**: 모델이 지원하는 기능과 입력 한도를 반환한다.
- **입력**: 없음
- **출력**

```json
{"contractVersion":"2.0.0","engineVersion":"seatrec-2.0.0","featureRegistryVersion":"features-2","trainedModel":false,"modelVersion":null,
 "supportedTargets":["library","room","seat"],"supportedModes":["live","preview","simulation"],"distanceMetric":"straightLine",
 "maxCandidates":5000,"maxTopK":20,"maxObservations":200,"nlPreferenceImport":true,"preferenceLearning":false,
 "realtimeFeatures":true,"exploration":true,"occupancyPrediction":false,"seatReservation":false}
```
- **오류**: 401, 403, 429, 503

### M03. `GET /v1/features` — 특성 목록 조회

- **설명**: 설문·설정 화면이 쓸 특성 사전을 반환한다.
- **입력**: 없음
- **출력**: `{featureRegistryVersion, features:[{featureId, label, type:binary|continuous, source:static|realtime, normalization, requiresData}]}` (14개)
- **오류**: 401, 403, 429, 503

### M04. `POST /v1/preferences/initialize` — 설문 → 초기 취향

- **설명**: 가입 설문 답변으로 초기 취향을 만든다.
- **입력**: `{requestId, featureRegistryVersion, answers:[Preference]}`
- **출력**: `{requestId, engineVersion, featureRegistryVersion, preferences:[Preference(source=survey)]}`
- **오류**: 422 특성 중복·미등록 ID·이진 target이 0/1 아님·범위 밖 중요도

### M05. `POST /v1/recommendations/rooms` — 열람실 추천

- **설명**: 취향에 맞는 열람실을 추천한다.
- **입력**: 추천 공통 요청. candidates는 모두 `kind=room`.
- **출력**: 추천 공통 응답(`targetType=room`)
- **오류**: 422 seat 후보 혼입·중복 ID·계약 위반 / 200 `noEligibleCandidates` 전부 조건 탈락

### M06. `POST /v1/recommendations/seats` — 좌석 추천

- **설명**: 열람실 안에서 지금 이용 가능한 좌석 중 사용자 취향에 맞는 좌석을 추천한다.
- **입력**: 추천 공통 요청. candidates는 모두 `kind=seat`. live에서는 `identityVerified=true`, `geometryVerified=true`, `mapAnchor` 필수.
- **출력**: 추천 공통 응답(`targetType=seat`)

```json
{"rank":1,"targetType":"seat","targetId":"SIM-SEAT-12","rankingBasis":"preferencesAndHistory",
 "scores":{"featureScore":0.94,"featureCoverage":1,"choiceAffinity":0.2,"personalizationScore":0.866,"distanceUtility":null,"effectiveDistanceWeight":null,"finalScore":0.866},
 "availability":{"effectiveState":"available","scope":"seat","observedAt":"2026-11-14T17:29:30+09:00","expiresAt":"2026-11-14T17:30:30+09:00"},
 "reasons":[{"code":"HISTORY_PATTERN","featureId":"outletAvailable","sourceId":"SIM-ENV-s013","text":"최근 18번 중 콘센트 자리를 16번 고르셨어요(그때 빈자리 중 콘센트 자리는 41%)."}],
 "previewOnly":true}
```
- **오류**: 422 room 후보 혼입·잘못된 bbox·snapshotId 불일치·live에 synthetic 특성 / 200 `noEligibleCandidates` 적격 좌석 없음

### M07. `POST /v1/recommendations/libraries` — 주변 도서관 추천

- **설명**: 현재 위치와 취향으로 갈 만한 주변 도서관을 추천한다.
- **입력**: 추천 공통 요청 +

| 필드 | 설명 |
|---|---|
| `origin` | `{latitude, longitude}` |
| `maxDistanceMeters` | 100~50,000, 기본 5,000 |
| `distanceWeight` | 0~1, 기본 0.35 |
| `candidateLevel` | `room\|seat` (섞지 않음) |
| `libraries` | `[{libraryId, latitude, longitude}]` |

- **출력**: 추천 공통 응답(`targetType=library`, `distance:{meters, metric:"straightLine"}`)
- **오류**: 422 origin 누락·잘못된 위경도·중복 기관 ID / 제외 `MISSING_LIBRARY_LOCATION`, `OUTSIDE_RADIUS`

### M08. `POST /v1/preferences/update` — 명시적 교정 반영

- **설명**: "더 어둡게", "칸막이가 더 중요" 같은 사용자 교정을 반영한 새 취향을 반환한다.
- **입력**: `{requestId, eventId, profile, corrections:[Preference 1~20]}`
- **출력**: `{requestId, eventId, engineVersion, featureRegistryVersion, baseRevision, updatedPreferences, changed, changes:[{featureId, before, after}]}`
- **오류**: 422 미등록 ID·중복 교정·버전 불일치

### M09. `POST /v1/preferences/from-analysis` — 카카오톡·자연어 분석 → 사전 취향

- **설명**: 카카오톡·자연어 분석 결과(`kakao_analysis/schema/kakaotalk_schema.json` 형식)를 취향 항목으로 변환한다. 분석기 문서 표의 값(`yes`, `cool` 등)과 응답 예시의 값(`uses_laptop`, `avoid_direct_airflow` 등)을 모두 받는다.
- **입력**

```json
{"requestId":"req-nl","featureRegistryVersion":"features-2","currentProfile":null,
 "analysis":{"participant_id":"user-pseudo-001","preferences":{
   "lighting":{"preference":"bright","confidence":0.45,"inference_type":"contextual"},
   "air_conditioning":{"preference":"avoid_direct_airflow","confidence":0.8,"inference_type":"direct"},
   "laptop_usage":{"preference":"uses_laptop","confidence":0.95,"inference_type":"direct"},
   "outlet":{"preference":"required","confidence":0.93,"inference_type":"direct"}}}}
```

  `participant_id`는 내부 가명 ID. `currentProfile`을 주면 병합 결과(`mergedProfile`)도 반환.
- **출력**: `{requestId, engineVersion, featureRegistryVersion, mappingVersion, preferences:[Preference(source=nlAnalysis)], mergedProfile, requirements, items:[{feature, raw, canonical, status, confidence, inferenceType, mapped[]}], unmapped:[{feature, raw, reason}], warnings[]}`
  - status: `mapped|noSignal|unknownValue|noFeature|notActivated|missing`
- **오류**: 422 analysis·preferences 누락, confidence 범위 밖 / 허용되지 않은 값은 오류 없이 `warnings`에 표시

### M10. `POST /v1/preferences/learn` — 선택 이력 → 취향 학습

- **설명**: 사용자의 좌석 선택 이력(당시 빈자리 포함)에서 취향을 학습해 반환한다.
- **입력**

| 필드 | 필수 | 설명 |
|---|---|---|
| `requestId`, `evaluatedAt` | ✔ | |
| `featureRegistryVersion` | ✔ | `features-2` |
| `executionMode` | ✔ | `live\|simulation` (섞지 않음) |
| `profile` | ✔ | 현재 프로필(사전값·잠금 포함) |
| `observations` | ✔ | ChoiceObservation 1~200개 |
| `options` | | `halfLifeDays`(기본60), `minObservations`(기본3), `maxFeatures`(기본14) |

- **출력**

```json
{"requestId":"req-learn","engineVersion":"seatrec-2.0.0","modelVersion":"prefmodel-sim-0","featureRegistryVersion":"features-2",
 "status":"learned","baseRevision":5,"observationsUsed":3,
 "observationsIgnored":[{"eventId":"sim-sel-004","reason":"CHOICE_SET_TOO_SMALL"}],
 "learnedPreferences":[
   {"featureId":"laptopAllowed","target":1,"importance":1,"source":"explicit","locked":true},
   {"featureId":"outletAvailable","target":1,"importance":0.74,"source":"learned","uncertainty":0.38,"evidenceCount":3}],
 "changed":true,"changes":[{"featureId":"outletAvailable","before":null,"after":{"featureId":"outletAvailable","target":1,"importance":0.74}}],
 "evidence":[{"featureId":"outletAvailable","observations":3,"chosenRate":1.0,"availableRate":0.444}]}
```

  - `status`: `learned` / `insufficientData`(관측 부족) / `priorOnly`(학습 모델 없음)
  - `evidence`: 고른 좌석의 특성 비율(`chosenRate`) vs 당시 빈자리의 특성 비율(`availableRate`)
  - `observationsIgnored[].reason`: CHOICE_SET_TOO_SMALL, CHOSEN_NOT_IN_CHOICE_SET, STALE_SNAPSHOT, MODE_MISMATCH, DUPLICATE_EVENT
- **오류**: 422 관측 200개 초과·중복 eventId·미등록 특성·live 요청에 simulation 관측·preview 모드

---

## 3. 백엔드 API

### B01. `GET /api/v1/libraries` — 도서관 목록

- **설명**: 도서관 목록과 지원 기능을 반환한다.
- **입력**: query `cursor`(선택), `limit`(1~100, 기본20)
- **출력**: `{items:[{libraryId, name, latitude, longitude, datasetVersion, capabilities:{roomRecommendation, seatRecommendation, seatMap, roomAvailability, seatAvailability, facilityFeatures, preferenceLearning}}], nextCursor}`. 좌표 미확인은 null.
- **오류**: 401, 422 limit·cursor 형식

### B02. `GET /api/v1/libraries/{libraryId}/rooms` — 열람실 목록

- **설명**: 도서관의 열람실·층·정원·규정·지원 기능을 반환한다.
- **입력**: path `libraryId`
- **출력**: `{libraryId, items:[{roomId, name, floor, totalSeats, featureSummary, mapId, capabilities, sourceDate, datasetVersion}]}`
- **오류**: 404 기관 없음

### B03. `GET /api/v1/rooms/{roomId}/map` — 좌석도·시설 레이어

- **설명**: 좌석 지도와 시설 레이어를 반환한다.
- **입력**: path `roomId`
- **출력**: `{roomId, mapId, datasetVersion, mapVersion, width, height, coordinateUnit:"normalized", backgroundUrl, verification, seats:[{seatId, label, x, y, width, height, verification}], facilities:[{facilityId, type, geometry, verification}], sourceDate}`
- **오류**: 404 `MAP_NOT_AVAILABLE`, 403 검수 전 지도(preview 권한 없음)

### B04. `GET /api/v1/rooms/{roomId}/availability` — 실시간 좌석 현황

- **설명**: 열람실의 실시간 좌석 현황을 반환한다.
- **입력**: path `roomId`, query `mode`(`live` 기본 \| `simulation`)
- **출력**: `{roomId, mode, snapshotId, roomAggregate:null|{totalSeats, remainingSeats, observedAt, expiresAt, sourceId}, seatStates:[{seatId, state, observedAt, expiresAt, sourceId}], receivedAt}`. 현황이 없으면 `snapshotId=null`, `roomAggregate=null`, `seatStates=[]`.
- **오류**: 401, 403(simulation 권한), 404, 422

### B05. `GET /api/v1/users/me/preferences` — 내 취향 조회

- **설명**: 저장된 취향 프로필을 반환한다.
- **입력**: 없음
- **출력**: `{revision, featureRegistryVersion, preferences:[Preference]}`. 신규 사용자는 `revision=0, preferences=[]`.
- **오류**: 401

### B06. `PUT /api/v1/users/me/preferences` — 내 취향 직접 설정

- **설명**: 사용자가 취향을 직접 저장하거나 항목을 잠근다(`locked=true`).
- **입력**: `{expectedRevision, featureRegistryVersion, preferences:[Preference]}`
- **출력**: 저장된 새 프로필 `{revision, featureRegistryVersion, preferences}`
- **오류**: 409 `REVISION_CONFLICT`, 422 특성 형식 위반

### B07. `POST /api/v1/recommendations/rooms` — 열람실 추천 생성

- **설명**: 선택한 도서관들에서 내 취향에 맞는 열람실을 추천한다.
- **입력**: `{libraryIds:[1~20개], preferenceOverrides, requirements, context, options:{topK, availabilityPolicy, minFeatureCoverage}}`
- **출력**: `{recommendationId, createdAt, expiresAt, profileRevision, snapshotId, result:M05 응답}`
- **오류**: 401, 422, 503 모델 불가 / 조건 탈락은 200 빈 결과

### B08. `POST /api/v1/recommendations/seats` — 좌석 추천 생성

- **설명**: 열람실 안에서 지금 이용 가능한 최적 좌석을 추천한다.
- **입력**: `{roomId, preferenceOverrides, requirements, context, options:{topK, availabilityPolicy, minFeatureCoverage}}`
- **출력**: B07과 같은 형식(`result`는 M06 응답)
- **오류**: 401, 404 열람실 없음, 422, 503 / 적격 좌석 없음은 200 빈 결과

### B09. `POST /api/v1/recommendations/{recommendationId}/events` — 행동 기록

- **설명**: 추천 결과의 노출·선택·이용·평가를 기록한다.
- **입력**: 공통 `{eventId, type, occurredAt}` +

| type | 의미 | 추가 입력 |
|---|---|---|
| `recommendationExposed` | 추천이 화면에 보임 | `visibleTargetIds[]` |
| `candidateSelected` | 좌석·열람실 선택 | `targetId`, `origin:recommendation\|outsideRecommendations` |
| `useConfirmed` | 이용 시작 | `targetId` |
| `useEnded` | 이용 종료 | `targetId`, `endedAt`, `endedBy:user\|auto` |
| `ratingSubmitted` | 평가 | `targetId`, `rating 1~5`, `corrections[]` |
| `candidateRejected` | 추천 거절 | `targetId`, `reason`(tooNoisy, tooBright, tooDark, tooCold, tooHot, noOutlet, tooCrowded, tooFar, other), `excludeFromFuture` |

- **출력**: `{eventId, accepted:true, effect:storedOnly|historyUpdated|preferencesUpdated|learningQueued, profileRevision}`
- **오류**: 404 추천 없음, 409 `RECOMMENDATION_EXPIRED`·`AVAILABILITY_CHANGED`(그 사이 좌석이 참)·`PREVIEW_NOT_SELECTABLE`(preview·simulation 추천), 422 추천에 없는 대상·형식 오류

### B10. `POST /api/v1/recommendations/libraries` — 주변 도서관 추천 생성

- **설명**: 현재 위치와 취향으로 갈 도서관을 추천한다.
- **입력**: `{origin:{latitude, longitude}, maxDistanceMeters, distanceWeight, candidateLevel:room|seat, preferenceOverrides, requirements, context, options}`
- **출력**: B07과 같은 형식(`result`는 M07 응답)
- **오류**: 422 위치 누락·범위 오류 / 해당 기관 없음은 200 빈 결과

### B11. `GET /api/v1/rooms/{roomId}/seats` — 좌석 좌표 목록

- **설명**: 열람실 좌석의 번호·좌표 목록을 반환한다.
- **입력**: path `roomId`, query `view`(`live` 기본 \| `preview`), `limit`(1~500, 기본100), `cursor`
- **출력**: `{roomId, datasetVersion, map:{mapId, mapVersion, width, height, coordinateUnit:"pixel", origin:"topLeft"}, view, items:[{seatId, externalSeatId, label, geometry:{pixel:{x,y,width,height}, normalized:{x,y,width,height}}, verificationStatus, selectable}], total, nextCursor}`
- **오류**: 403 preview 권한 없음, 404 열람실 없음, 409 `CURSOR_VERSION_CHANGED`, 422 query 형식

### B12. `POST /api/v1/users/me/preference-analyses` — 자연어 분석 결과 등록

- **설명**: 카카오톡·자연어 분석 결과를 내 취향 프로필에 등록한다.
- **입력**: `{expectedRevision, analysis}` (analysis는 분석기 원본 형식)
- **출력**: `{profileRevision, preferences, unmapped, warnings, analysisVersion}`
- **오류**: 403 분석 동의 없음, 409 `REVISION_CONFLICT`, 422 형식 오류

### B13. `GET /api/v1/users/me/preferences/insights` — 이력에서 찾은 내 취향

- **설명**: 선택 이력에서 학습된 취향과 그 근거를 반환한다.
- **입력**: 없음
- **출력**: `{revision, modelVersion, learnedAt, items:[{featureId, target, importance, source, uncertainty, evidenceCount, locked, evidence:{observations, chosenRate, availableRate}, text}]}`
  - text 예: "최근 18번 중 콘센트 자리를 16번 고르셨어요(그때 빈자리 중 콘센트 자리는 41%)."
- **오류**: 401
