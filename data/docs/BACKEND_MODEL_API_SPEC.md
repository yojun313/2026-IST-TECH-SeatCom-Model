# 실시간 개인별 공공도서관·열람실·좌석 추천 API 명세서

- 버전: **2.0.0-design** (1.0.0-design 개정)
- 작성일: 2026-11-14
- 상태: **구현 전 계약 설계**. 추천시스템이나 API 서버가 이미 구현됐거나 정확도가 검증됐다는 뜻이 아니다.
- 기준 문서: 저장소 루트의 「백엔드 ↔ 모델 API 명세서 (v0.1 초안)」, 1.0.0-design 명세, 27관 데이터 구축 결과(`data/BUILD_STATUS.md`), 합성 데이터 명세(`data/libraries/docs/SYNTHETIC_DATA.md`), 카카오톡 취향 분석 출력 형식(`kakao_analysis/schema/kakaotalk_schema.json`, `kakao_analysis/schema/kakaotalk_analysis.md`).
- 대상: 학교도서관을 제외한 공공도서관. **사용자가 지금까지 앉아 온 좌석 선택 이력에서 취향을 학습하고, 실시간 좌석 현황에서 지금 비어 있는 최적의 자리를 추천**한다. 도서관·열람실 추천과 현재 위치·거리 결합은 유지한다.
- 내부 모델 서버의 기계 판독 계약: [MODEL_API.openapi.json](be-model/MODEL_API.openapi.json). 백엔드 좌석 좌표·현황 계약: [BACKEND_SEATS_API.openapi.json](be-model/BACKEND_SEATS_API.openapi.json).

### 2.0.0 변경 요약

| 구분 | 1.0.0-design | 2.0.0-design |
|---|---|---|
| 취향의 출처 | 설문·직접 설정·명시적 교정 | + 카카오톡·자연어 분석 결과(사전값, M09) + **좌석 선택 이력 학습(M10)** |
| 선택 이력 반영 | 같은 대상 재선택 가산(choiceAffinity)만 | 선택 당시의 빈자리 집합과 비교해 특성 취향(target·importance)을 학습 |
| 실시간 | 이용 가능 필터만 | 필터 + 실시간 특성(`neighborOccupancy`, `roomOccupancy`) |
| 피처 레지스트리 | `features-1`(12개) | `features-2`(정적 12개 + 실시간 2개) |
| 실행 모드 | `live`, `preview` | + `simulation`(합성 환경·합성 현황, 개발·학습·시연 전용) |
| 이벤트 | 노출·선택·이용신고·평가·거절 | + `useEnded`, 모든 선택에 현황 스냅샷·후보 집합 연결 |
| 탐색 | 제거 | 선택 옵션으로 재도입(기본 꺼짐), 노출 확률 기록 |

## 1. 핵심 판단: 무엇을 구현할 수 있는가

### 1.1 이번 버전의 ‘실시간 개인화’

이 서비스의 개인화는 두 축이다.

1. **이력에서 취향 찾기:** 사용자가 지금까지 앉아 온 좌석을 **그때 비어 있던 다른 좌석들과 비교**해 어떤 특성(콘센트·창가·밝기·칸막이·주변 혼잡 등)을 얼마나 중시하는지 학습한다. 카카오톡·자연어 분석 결과는 이력이 쌓이기 전의 사전값으로만 쓴다.
2. **지금 빈 자리 중 최적 고르기:** 요청 시각의 실시간 현황에서 신선하게 확인된 빈 좌석만 남기고, 학습된 취향으로 순위를 매긴다.

사용자가 현재 위치나 오늘의 조건을 바꾸거나, 새 현황과 선택·피드백이 저장되면 그 시점의 입력으로 추천 순위를 다시 계산한다. **앞으로 몇 번 좌석이 언제 비는지를 예측하는 기능은 포함하지 않는다.** 현황이 실제로 연동된 기관에서만 관측 시각과 유효기간을 표시한다.

엔진은 단계적으로 활성화한다. 학습 모델이 없거나 이력이 부족하면 1.0의 **내용 기반 점수 계산**(6.2절)과 사용자가 직접 정한 취향·사전값을 쓴다. 학습 모델(6.6절)은 오프라인 평가에서 규칙 엔진보다 낫다고 확인된 뒤에만 live에 켠다. 검증하지 않은 학습 모델의 정확도·만족 확률을 API 응답으로 만들지 않는다.

### 1.2 현재 확인한 데이터와 기능의 경계

| 자료 | 현재 상태(2026-11-14) | 활용 가능한 범위 | 보장할 수 없는 범위 |
|---|---|---|---|
| 좌석 좌표 보유 11관 (`data/libraries/real/`) | 국립중앙 디지털, 성남중앙 A구역, 용인 9관. 좌석 2,111개, 모두 `model_candidate`(사람 검수 0건). 공식 ID 대응 340석 | preview·simulation 좌석도, 학습용 좌석 배치 | 운영용 번호·좌표 정확성(live) |
| 좌석 좌표 없는 16관 (`data/libraries/backup/`) | 성남 12관·용인 중앙·구성·해운대·계양. 열람실·규정 일부만 | 열람실 추천(P1) 후보 | 좌석 추천 |
| 시설·환경 실측 | 일부 도면의 화장실·출입구·승강기 후보 73건. 냉방·콘센트·조도·책상 실측 0건 | 같은 지도에 있는 시설 후보 | 환경 특성 기반 live 추천 |
| 합성 환경·사용자 데이터 (MongoDB `library.synth_*`) | 실제 좌석 배치 위 합성 환경 720개, 좌석 특성 63,330행, 합성 사용자 3,000명, 세션 59,496건 | `simulation` 모드, 모델 학습·평가 파이프라인 검증, 시연 | 실제 도서관 상태·실제 사용자 행동 |
| 카카오톡·자연어 취향 분석 (팀원 담당) | 참여자별 7개 항목의 범주값·확신도·추론 유형·근거 | 취향 사전값(M09) | 확신도를 실제 정답 확률로 해석 |
| 실시간 좌석 현황 | 허가된 좌석 단위 공급원 연동 0건. 성남 사이트는 robots.txt 전체 금지 | simulation의 합성 현황 | live 좌석 단위 빈자리(P4) |
| 기관 위경도 | 27관 모두 null | — | 주변 도서관 추천(P2) |

**현재 자료로 운영(live) 가능한 기능은 없다.** API·모델 개발과 학습 파이프라인 검증은 `simulation` 모드로 진행하고, 실제 기관별 좌석 검수·현황 연동·위경도 연결이 끝난 단계부터 live를 연다.

### 1.3 단계별 활성화 조건

| 단계 | 제공 기능 | 활성화 조건 |
|---|---|---|
| P0 | 미검수 좌석도와 추천 계산 미리보기 | `executionMode=preview`. 실제 선택·이용 신고 불가 |
| P1 | 취향에 맞는 열람실 추천 | 내부 기관/열람실 ID와 특성 근거 검증 |
| P2 | 현재 위치·거리·취향으로 갈 도서관 추천 | 검증된 기관 좌표와 대표 열람실·특성 |
| P3 | 검증된 개별 좌석의 취향 추천 | 내부 좌석 ID·표시번호·지도 위치 전수 검수 |
| P4 | 지금 확인된 빈 개별 좌석만 추천 | **좌석 단위** 현황 공급원과 신선도 검증 |
| P5 | 착석 이력에서 학습한 취향으로 실시간 빈 좌석 추천 | P4 + 학습 모델의 오프라인 평가 통과(6.6.5절) + 사용자별 최소 관측 수 |
| S | 합성 환경·합성 현황으로 전 기능 시연·학습 | `executionMode=simulation`. 운영 선택·이용 신고 불가 |

P3에서 좌석 현황이 없으면 ‘취향에 맞는 자리, 이용 가능 여부 미확인’으로만 제공한다. 열람실 잔여석 집계만으로 P4를 활성화하지 않는다. P5 이전에는 학습 취향 대신 직접 설정·사전값·명시적 교정으로 만든 프로필을 사용한다.

## 2. 기존 초안의 유지·수정·삭제

| 기존 항목 | 결정 | 이유·대체 |
|---|---|---|
| `GET /health` | 유지 | 모델 프로세스 생존과 엔진 버전 확인 |
| `GET /personas` | 모델 API에서 제거 | 페르소나 카드는 백엔드의 온보딩 설정. 모델은 명시적 취향 초기화 계산 |
| `POST /recommend` | 세 추천 API로 분리 | 도서관·열람실·좌석은 데이터 단위와 현황 해석이 다름 |
| `POST /feedback` | 저장과 계산 분리 | 백엔드가 이벤트 저장, 모델은 명시적 특성 교정 계산 |
| `POST /zones/jibak` | 삭제 | 좌석별 장기 점유·자리비움 기록이 없어 검증 불가 |
| `POST /predict/occupancy` | 삭제 | 좌석별 시계열·실제 퇴실 관측·예측 모델과 평가 데이터 없음 |
| `emptyProb`, `expectedVacateMinutes` | 삭제 | 산출할 근거와 검증된 확률 모델 없음 |
| `occupiedMinutes`, `awayMinutes`, 성별 상태 | 삭제 | 현재 확보하지 않은 개인 점유 정보. 추천 정적 입력에서 제외 |
| `row`, `col` | 지도 좌표로 교체 | 실제 배치는 비정형. `mapId`와 정규화 bbox 사용 |
| `first`, `second` | `results[]`로 교체 | 후보 0·1·여러 개와 상위 K개 지원 |
| `explore`, `exploit` | v2에서 선택 옵션으로 재도입 | 기본 꺼짐. 켜면 노출 확률을 기록해 편향 없는 학습·평가에 사용(6.5절) |
| 임의 `comfort` 점수 | 제거 | 관측 의미가 불명확. 책상·조도 등 정의된 특성으로 분리 |
| API 미수신을 전석 공석으로 처리 | 금지 | 미수신·만료는 `unknown`, 결측은 `null` |
| 주변 도서관 추천 | 추가 | 현재 위치·직선거리·대표 공간의 취향 점수를 결합 |
| 기능·피처 조회 | 추가 | 엔진 기능과 기관별 데이터 준비 상태를 분리 |
| 지도·데이터 버전 | 추가 | 서로 다른 프레임·스냅샷을 잘못 결합하지 않도록 함 |
| 자연어 취향 사전값 | v2 추가(M09) | 팀원의 카카오톡·자연어 분석 결과를 features-2 프로필로 변환 |
| 이력 기반 취향 학습 | v2 추가(M10) | 선택 당시 빈자리 집합 대비 선택을 관측해 target·importance 학습 |
| 실시간 특성 | v2 추가 | 주변 좌석 점유·열람실 혼잡도를 취향 특성으로 사용 |
| simulation 모드 | v2 추가 | 합성 데이터를 live·preview와 섞지 않고 개발·학습·시연에 사용 |

이 버전은 공식 예약·좌석배정, QR 검증, 도보 길찾기, 장기 점유 점수, 빈자리 예측을 제공하지 않는다. 미래 자료를 확보하면 별도 버전으로 설계한다.

## 3. 백엔드와 모델 서버의 책임

### 3.1 백엔드 API 서버

백엔드는 인증, 기관·열람실·좌석·지도·시설 DB, 원본 정제와 검수 상태, 현황 수집·캐시, 사용자 취향과 이벤트, 추천 결과 저장을 담당한다. NestJS 등 구현 프레임워크는 선택 사항이다.

모델에는 백엔드가 관리하는 내부 ID와 검증된 스냅샷을 전달한다. 클라이언트가 보낸 좌석 특성·검수 여부를 그대로 신뢰하지 않는다. 추천 결과를 예약이나 빈자리 확보로 표현하지 않는다.

### 3.2 모델 API 서버

모델은 **요청별 무상태 계산 서비스**다. 계약 검사, 필수 조건 필터, 취향 점수·선택 이력·직선거리 계산, 순위·근거·제외 사유, 명시적 취향 교정값, 자연어 분석 결과의 프로필 변환(M09), 선택 이력 기반 취향 학습(M10)을 반환한다.

사용자별 상태는 요청에 담겨 오고 모델은 저장하지 않는다. 단, 전체 사용자 행동에서 오프라인으로 학습한 **모집단 파라미터**(사전값 보정 계수, 특성별 기본 분포)는 버전이 있는 모델 산출물(`modelVersion`)로 배포해 읽기 전용으로 적재한다. 학습 산출물이 없으면 규칙 엔진만으로 동작한다.

모델은 사용자 DB를 변경하거나 외부 도서관·지도·공공 API·현황 공급원을 직접 호출하지 않는다.

### 3.3 호출 흐름

```text
[가입·연동]  카카오톡·자연어 분석 결과(팀원 서비스)
 → 백엔드 B12 → 모델 M09(프로필 변환) → 사전값으로 프로필 CAS 저장

[추천]  현재 위치·오늘 조건 입력
 → 백엔드: 기관/공간 마스터, 현황 캐시(스냅샷ID 고정), 프로필, 선택 이력 조회, 실시간 특성 계산
 → 모델: 적격성·현황·필수 조건 필터 → 점수 → 순위·근거
 → 백엔드: recommendationId 발급, 입력 스냅샷(후보·현황·피처·프로필revision)·결과 저장
 → 프론트: 추천 도서관·대표 공간·거리·지도·관측 시각 표시

[행동 기록]
 → 백엔드: 노출·선택·이용 신고·종료·평가 이벤트 기록. 선택에는 추천 당시 후보 집합·현황 스냅샷을 연결
 → 명시적 특성 교정이 있으면 모델 M08 계산 후 프로필 CAS 저장

[취향 학습]  선택·이용 종료가 저장될 때(또는 주기 배치)
 → 백엔드: 최근 선택 관측(선택 좌석 + 당시 빈자리 집합 + 결과 신호) 구성
 → 모델 M10: 사전값 + 이력 → 학습 취향·불확실성·근거 요약
 → 백엔드: 잠금(locked) 항목을 제외하고 프로필 CAS 저장 → 다음 추천에 반영
```

도서관을 선택하면 열람실 추천, 열람실을 선택하면 검증된 좌석 추천을 추가 호출한다. 좌석 데이터가 없는 기관은 열람실 수준까지만 제공하며 결과 종류를 몰래 바꾸지 않는다.

## 4. 공통 HTTP·ID·오류 규칙

### 4.1 요청과 버전

- 내부 모델 주소 예: `http://model-api:8000`. 운영에서는 백엔드만 접근하고 서비스 인증을 사용한다.
- 모델 경로는 `/v1/...`, 사용자용 백엔드는 `/api/v1/...`다.
- JSON, camelCase. 숫자는 유한한 값만 허용한다. 알 수 없는 필드·피처 ID·열거값은 422다.
- 시간은 오프셋을 포함한 RFC3339다. 예: `2026-11-14T17:30:00+09:00`. 오프셋 없는 시각은 거절한다.
- 내부 요청은 `requestId`와 백엔드가 고정한 `evaluatedAt`을 사용한다. 같은 입력·버전이면 같은 점수와 순위를 반환한다.
- `datasetVersion`, `featureRegistryVersion`, `engineVersion`, `modelVersion`을 구분한다. 서로 다른 스냅샷을 섞지 않는다. `modelVersion`은 학습 산출물 버전이며 산출물이 없으면 null이다.
- 예외: M09의 `analysis` 본문은 팀원 분석기의 원본 형식(snake_case, 예: `inference_type`)을 그대로 받는다. 변환 결과는 camelCase다.
- 후보 최대 5,000개, 선호·필수 조건 각각 최대 20개, `topK` 1~20. 중복 후보 ID는 422다.
- 빈 후보 배열은 정상 입력이다. 200과 빈 결과를 반환한다.
- 모델 p95 500ms, 백엔드 캐시 기반 추천 p95 1초는 **개발 목표**다. 아직 측정한 성능이 아니며 CPU·후보 수·요청 크기를 고정한 부하 시험으로 검증한다.

### 4.2 ID와 좌표

내부 `candidateId`, `libraryId`, `roomId`, `seatId`는 전역 문자열 ID다. 외부 기관은 `stdgCd+pblibId`, 열람실은 여기에 `rdrmId`를 더해 연결한다. 같은 이름의 중앙도서관을 이름으로 조인하지 않는다.

외부 ID가 없어도 검증된 내부 ID는 사용할 수 있다. 다만 번호·위치가 미검증인 기하 객체를 운영용 좌석으로 승격하면 안 된다.

`mapAnchor`는 `{mapId,x,y,width,height}`다. 좌상단 원점, X 오른쪽·Y 아래이며 해당 프레임의 0~1 정규화 값이다. `x+width<=1`, `y+height<=1`을 검사한다. 스크린샷 버튼의 크기는 실제 책상 크기나 물리 축척이 아니다.

정적 원본의 한글/snake_case 필드는 백엔드 수입 어댑터가 API의 camelCase로 변환한다. 정적 데이터 번들과 API 요청은 같은 파일 형식이 아니다.

### 4.3 오류

```json
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "latitude must be between -90 and 90",
    "requestId": "req-demo-001",
    "details": [{"path":"origin.latitude","reason":"OUT_OF_RANGE"}]
  }
}
```

| HTTP | 대표 코드 | 처리 |
|---|---|---|
| 401/403 | `UNAUTHENTICATED`, `FORBIDDEN` | 인증·권한 확인 |
| 404 | `NOT_FOUND` | 기관·지도·추천 ID 확인 |
| 409 | `REVISION_CONFLICT`, `RECOMMENDATION_EXPIRED`, `AVAILABILITY_CHANGED`, `PREVIEW_NOT_SELECTABLE`, `IDEMPOTENCY_CONFLICT` | 최신 데이터로 재요청. 필수 조건 자동 완화 금지 |
| 422 | `INVALID_INPUT`, `FEATURE_VERSION_MISMATCH`, `UNSUPPORTED_FEATURE` | 계약·필드 수정 |
| 429 | `RATE_LIMITED` | 재시도 간격 적용 |
| 503 | `NOT_READY`, `MODEL_UNAVAILABLE` | 미검증 예시 데이터로 대체하지 않음 |

`noEligibleCandidates`는 잘못된 입력이 아니라 정상 도메인 결과다. 200과 제외 사유를 반환하고 사용자가 조건을 변경하게 한다.

## 5. 공통 요청 데이터

### 5.1 취향 프로필

```json
{
  "revision":3,
  "featureRegistryVersion":"features-2",
  "preferences":[
    {"featureId":"laptopAllowed","target":1,"importance":1,"source":"explicit","locked":true},
    {"featureId":"outletAvailable","target":1,"importance":0.82,"source":"learned","uncertainty":0.12,"evidenceCount":17},
    {"featureId":"brightness","target":0.85,"importance":0.3,"source":"nlAnalysis","uncertainty":0.6,"evidenceCount":0},
    {"featureId":"neighborOccupancy","target":0,"importance":0.45,"source":"learned","uncertainty":0.3,"evidenceCount":9}
  ]
}
```

`target`은 원하는 수준, `importance`는 0~1 중요도다. 이진 특성의 target은 0 또는 1, 연속 특성은 0~1이다. 중요도 0은 점수에서 제외한다. 기존 초안의 음수·양수 의미가 모호한 Weights를 교체한다.

v2에서 각 항목은 선택 필드로 출처와 신뢰 정보를 가진다.

| 필드 | 의미 |
|---|---|
| `source` | `explicit`(사용자 직접 설정) \| `survey`(M04 설문) \| `nlAnalysis`(M09 자연어 분석 사전값) \| `learned`(M10 이력 학습) \| `correction`(M08 명시적 교정). 생략 시 explicit |
| `locked` | true면 학습·사전값 갱신이 바꾸지 않는다. 사용자가 직접 정한 항목의 기본값 |
| `uncertainty` | 0~1. 학습 취향의 사후 불확실성을 정규화한 값. 0에 가까울수록 확신. 사전값만 있으면 크다 |
| `evidenceCount` | 이 항목 학습에 사용한 선택 관측 수 |

취향이 없으면 빈 배열이다. 개인의 학습 취향은 **그 사용자 본인의 선택 관측**에서만 갱신한다. 다른 사용자 행동은 모집단 사전 분포(모델 산출물)로만 쓰이며 특정 개인의 취향으로 표시하지 않는다. 오늘의 `preferenceOverrides`는 같은 피처의 값을 요청에서만 덮어쓰며 저장 프로필을 바꾸지 않는다. ‘노트북 필수’는 선호뿐 아니라 `requirements`에 넣는다.

### 5.2 피처 레지스트리 `features-2`

이 목록은 엔진이 해석할 수 있는 특성 사전이며 **현재 기관에 그 데이터가 있다는 뜻은 아니다.** 출처·측정 단위·적용 범위가 검증된 값만 운영 추천에 사용한다. `features-2`는 `features-1`의 정적 특성 12개를 그대로 두고 실시간 특성 2개를 추가한다. 정적 특성의 정규화는 바뀌지 않았다.

**정적 특성** (후보의 `features`, 정적 데이터·검수 근거에서 생성)

| featureId | 의미 | 자료 조건·정규화 |
|---|---|---|
| `laptopAllowed` | 노트북 허용1/금지0 | 규정·명시적 구역명. 일반열람실이라는 이름만으로0 생성 금지 |
| `keyboardAllowed` | 키보드 작업 허용1/금지0 | 해당 규정 확인 |
| `partition` | 칸막이 있음1/없음0 | 실제 좌석 또는 균일 구역 확인 |
| `outletAvailable` | 해당 좌석에서 사용 가능한 콘센트 있음1 | 전기도·현장 접근성 확인 |
| `windowProximity` | 창문에 가까울수록1 | 검증된 미터 거리 d: max(0,1-d/10) |
| `toiletProximity` | 화장실에 가까울수록1 | 문·통로의 최단 보행거리 d(m): max(0,1-d/100) |
| `entranceProximity` | 출입구에 가까울수록1 | 보행거리 d(m): max(0,1-d/50) |
| `brightness` | 어두움0~밝음1 | 조도 lux: clamp((lux-100)/900,0,1). 시간·차광 조건 확인 |
| `airflow` | 약한 직접 바람0~강한 직접 바람1 | 동일 척도의 관측·평가. 에어컨 위치만으로 생성 금지 |
| `deskHeight` | 낮은 책상0~높은 책상1 | 높이 cm: clamp((cm-60)/40,0,1) |
| `deskWidth` | 좁은 책상0~넓은 책상1 | 폭 cm: clamp((cm-60)/120,0,1) |
| `viewQuality` | 제한된 시야0~좋은 전망1 | 공통 척도의 확인된 평가. 창가=좋은 경치로 자동 추정 금지 |

**실시간 특성** (후보의 `realtime`, 요청 시각의 현황 스냅샷에서 백엔드가 계산)

| featureId | 의미 | 계산·정규화 |
|---|---|---|
| `neighborOccupancy` | 주변이 비어 있음0~주변이 모두 참1 | 같은 지도에서 이 좌석 중심으로부터 반경 안(기본: 좌석 버튼 짧은 변의 2.5배) 좌석 중 신선하게 occupied·reserved로 관측된 비율. 반경 안 좌석 상태가 하나라도 unknown이면 null |
| `roomOccupancy` | 한산함0~만석1 | 열람실의 (정원-잔여석)/정원. roomAggregate 또는 좌석 상태 집계. 신선하지 않으면 null |

실시간 특성은 현황이 신선한 동안에만 값이 있다. 만료되면 미상(null)이며 0(비어 있음)으로 바꾸지 않는다. 미래 혼잡을 예측한 값이 아니다.

상수는 비교 가능성을 위한 **우리 v1 설계 정책**이며 실증된 쾌적성 기준이 아니다. 다른 기관의 픽셀 거리나 다른 척도의 후기를 같은 값으로 섞지 않는다. 정규화가 바뀌면 레지스트리 버전을 변경한다.

사용자는 자료가 없는 취향도 저장할 수 있다. 단, 해당 기관 추천에 반영되지 않은 항목을 명시해야 한다.

`simulation` 모드에서는 합성 환경 데이터(`verification=synthetic`)의 특성값을 쓴다. 합성값은 live·preview 요청에서 거절되며(422), simulation 결과를 실제 기관의 특성·현황으로 표시하지 않는다.

### 5.3 피처 값과 적용 범위

```json
{
  "value":1,
  "verification":"verified",
  "scope":"room",
  "appliesToAllSeats":true,
  "sourceId":"DEMO-SOURCE-RULE-1",
  "validUntil":null
}
```

- 값 누락·`null`은 미상이며 false나0이 아니다.
- `verification`은 `verified|unverified|missing|synthetic`이다. `synthetic`은 simulation에서만 허용한다.
- 운영 모드에서 `unverified` 값은 미상이다. 시간 조건이 있는 값은 `validUntil` 만료 시 미상이다.
- room 특성을 seat에 상속하려면 그 특성이 해당 방 전 좌석에 적용된다는 `appliesToAllSeats=true` 근거가 필요하다.
- 일부 구역의 콘센트·칸막이를 방 전체 좌석에 전파하지 않는다.
- 모델은 백엔드가 보낸 근거 ID와 검수 상태를 해석하며 원본을 다시 열어 사실 검증하지 않는다. 그 책임은 수입·검수 단계에 있다.

### 5.4 추천 후보

```json
{
  "candidateId":"DEMO-ROOM-A",
  "kind":"room",
  "libraryId":"DEMO-LIB-A",
  "roomId":"DEMO-ROOM-A",
  "seatId":null,
  "identityVerified":true,
  "geometryVerified":false,
  "mapAnchor":null,
  "operatingState":"unknown",
  "features":{},
  "availability":{"scope":"none"},
  "realtime":null,
  "choiceAffinity":0
}
```

seat 후보의 실시간 특성 예:

```json
{
  "snapshotId":"DEMO-SNAP-0001",
  "observedAt":"2026-11-14T17:29:30+09:00",
  "neighborOccupancy":0.25,
  "roomOccupancy":0.62
}
```

`realtime.snapshotId`는 백엔드가 이 추천에 고정한 현황 스냅샷 ID다. 같은 요청의 모든 후보는 같은 snapshotId를 가져야 한다(다르면 422). 현황이 없으면 `realtime=null`이다.

room 후보는 `candidateId=roomId`, seatId=null이다. seat 후보는 `candidateId=seatId`, roomId 필수다. `operatingState`는 `open|closed|unknown`이고 closed는 제외한다. unknown을 개관 확인으로 표현하지 않는다.

`identityVerified`는 실제 공간·표시번호와 내부 ID의 대응, `geometryVerified`는 지도 프레임과 위치 확인이다. live에서 room은 검증된 식별자가 필요하고 seat는 식별자·지도 위치·mapAnchor가 모두 필요하다.

`choiceAffinity`는 해당 사용자가 **같은 좌석·열람실**을 다시 고르는 습관을 백엔드가 계산한0~1 값이다(6.2절). 특성 취향은 choiceAffinity가 아니라 M10의 학습 프로필로 반영한다. 미제공은0이며 다른 사용자 인기점수나 빈자리 확률이 아니다.

### 5.5 현황: 단위·신선도·미상 처리

현황은 아래 중 하나다.

```json
{"scope":"none"}
```

```json
{
  "scope":"roomAggregate",
  "totalSeats":100,
  "remainingSeats":20,
  "observedAt":"2026-11-14T17:29:30+09:00",
  "expiresAt":"2026-11-14T17:31:30+09:00",
  "sourceId":"DEMO-ROOM-STATUS"
}
```

```json
{
  "scope":"seat",
  "state":"available",
  "observedAt":"2026-11-14T17:29:30+09:00",
  "expiresAt":"2026-11-14T17:30:30+09:00",
  "sourceId":"DEMO-SEAT-STATUS"
}
```

seat.state는 `available|occupied|reserved|blocked|unknown`이다. now는 요청의 `evaluatedAt`이다. `observedAt<=now<expiresAt`이고 최대 관측 나이 이내일 때만 신선하다. `maxAvailabilityAgeSeconds`는1~300, 기본120이며 공급원의 갱신주기를 검증해 백엔드가 설정한다.

- 미수신·만료·기준시각 미상은 effectiveState=unknown이다.
- seat 후보에 roomAggregate만 있으면 해당 seat의 상태는unknown이다. 잔여석20개를 임의의20좌석에 연결하지 않는다.
- room의 신선한 remainingSeats>0은 해당 열람실에 잔여석이 관측됐다는 의미다. 0이면 이용 가능한 후보에서 제외한다.
- `0<=remainingSeats<=totalSeats`가 필요하다. 정원 미상을0으로 바꾸지 않는다.
- 미래 관측 시각, expiresAt<=observedAt은422다.
- `availabilityPolicy=confirmedOnly`가 기본이다. 해당 추천 단위에서 이용 가능이 신선하게 확인된 후보만 남긴다.
- `allowUnknown`은 사용자가 명시적으로 허용한 경우다. 미상도 남기지만 `availabilityVerified=false`를 표시한다. 확인된 점유·예약·차단·잔여석0은 남기지 않는다.

현재 정적 좌석 파일에는 현황이 없다. 연동 시 백엔드의 별도 현황 캐시에서 결합하고 정적 CSV를 덮어쓰지 않는다. simulation의 현황은 합성 점유 시뮬레이터가 같은 형식(`sourceId`가 `SIM-`로 시작)으로 공급한다.

### 5.6 방문 맥락

추천 요청과 선택 관측에 선택적으로 붙는다. 같은 사람도 목적에 따라 다른 자리를 고르므로 학습과 점수에서 맥락별 보정에 쓴다.

```json
{"purpose":"laptopWork","expectedStayMinutes":180}
```

`purpose`는 `study|laptopWork|reading|shortStay|unknown`, `expectedStayMinutes`는 10~720 또는 null이다. 사용자가 입력하지 않은 맥락을 추정해 채우지 않는다(unknown).

### 5.7 선택 관측 (이력 학습 입력)

이력 학습(M10)의 단위다. **고른 좌석만이 아니라 그때 고를 수 있었던 후보 집합**이 있어야 취향을 학습할 수 있다. 창가만 남아 있어 창가에 앉은 선택은 창가 취향의 근거가 되지 않는다.

```json
{
  "eventId":"event-demo-sel-0017",
  "occurredAt":"2026-11-10T13:05:00+09:00",
  "roomId":"DEMO-ROOM-A",
  "snapshotId":"DEMO-SNAP-0017",
  "context":{"purpose":"laptopWork","expectedStayMinutes":null},
  "chosenCandidateId":"DEMO-SEAT-12",
  "origin":"recommendation",
  "exposedRank":2,
  "exposurePropensity":null,
  "choiceSet":[
    {"candidateId":"DEMO-SEAT-12","values":{"outletAvailable":1,"windowProximity":0.2,"brightness":0.55,"neighborOccupancy":0.0}},
    {"candidateId":"DEMO-SEAT-40","values":{"outletAvailable":0,"windowProximity":0.9,"brightness":0.8,"neighborOccupancy":0.5}}
  ],
  "outcome":{"useConfirmed":true,"stayMinutes":165,"endedEarly":false,"rating":4}
}
```

- `choiceSet`은 선택 시점에 신선하게 이용 가능하던 후보(추천 노출 여부와 무관)다. 선택 좌석을 반드시 포함하며 1~500개다. 후보가 선택 좌석 하나뿐이면 취향 근거가 없으므로 학습에서 제외한다(CHOICE_SET_TOO_SMALL). 백엔드가 추천 당시 저장한 스냅샷에서 만들고 클라이언트 값을 쓰지 않는다.
- `values`는 해당 시점의 정규화 특성값이다. 미상은 null이며 그 특성의 학습에서 해당 후보를 제외한다.
- `exposedRank`·`exposurePropensity`는 추천에서 고른 경우의 노출 순위·확률이다. 추천 위치 효과를 분리하는 데 쓴다.
- `outcome`은 선택의 강도다. 오래 머문 선택은 강한 근거, 일찍 떠나거나 낮게 평가한 선택은 약한 근거 또는 반대 근거다.
- 좌석 현황이 없어 choiceSet을 만들 수 없는 선택(allowUnknown 선택 등)은 학습에 쓰지 않고 choiceAffinity에만 반영한다.

## 6. 추천 계산 규칙

### 6.1 필터 순서

1. ID·종류·형식·버전 검사. 중복 ID와 계약 위반은422다.
2. live의 검증 조건 적용. 미검증 식별자·좌표는 각각 `UNVERIFIED_IDENTITY`, `UNVERIFIED_GEOMETRY`로 제외한다.
3. closed, 사용자 제외 목록, 확인된 예약·점유·차단을 제외한다.
4. 현황의 단위·신선도와 availabilityPolicy를 적용한다.
5. 모든 필수 조건을 적용한다. `{featureId,operator:eq|gte|lte,value}`이며 이진 특성은eq만 허용한다. 미상·미검수·만료값도 조건 불충족이다.
6. `minFeatureCoverage`를 적용한다. 활성 취향의 중요도 합이0이면 coverage=null이고 이 조건은 적용하지 않는다.
7. 남은 후보를 점수화한다. 필수 조건·반경을 자동 완화하지 않는다.

preview는 검수 전 자료의 화면 검토용이다. 결과는 `previewOnly=true`이며 백엔드 선택 API가 거절한다. preview도 점유 상태나 필수 조건을 무시하지 않는다.

simulation은 합성 환경·합성 현황·합성 사용자로 전 과정을 재현하는 개발·학습·시연 모드다. 검수 전 좌석과 `synthetic` 특성을 허용하고, 결과는 `previewOnly=true`와 경고 `SIMULATION_DATA`를 가진다. simulation 이벤트는 live 사용자 이력·학습에 섞지 않는다.

### 6.2 취향·이력 점수

활성 취향은 importance>0인 항목이다. 프로필은 직접 설정·사전값·학습 취향이 합쳐진 저장 프로필에 오늘의 override를 덮은 것이다. 실시간 특성(neighborOccupancy, roomOccupancy)도 같은 방식으로 점수에 들어간다. 확인된 값 x와 목표 t의 만족도는 `u=1-|t-x|`다.

```text
W = Σ importance[k]
featureScore[i] = Σ(확인된 k의 importance[k] * u[i,k]) / W
featureCoverage[i] = Σ(확인된 k의 importance[k]) / W
personalization[i] = 0.9 * featureScore[i] + 0.1 * choiceAffinity[i]
```

W=0이면 featureScore와 featureCoverage는 모두 null이다. W>0이지만 확인된 활성 특성이 하나도 없으면 featureScore=null, featureCoverage=0이다. 따라서 minFeatureCoverage>0인 요청에서 정보가 전혀 없는 후보는 제외한다. 분모가 모든 활성 취향의 중요도 합이므로 정보가 부족한 후보가 알려진 항목 하나만으로 만점을 받지 않는다. 이는 확인된 정보의 보수적 점수이며 미상 특성을0으로 저장한다는 뜻은 아니다.

featureScore=null이면 choiceAffinity>0일 때 personalization=choiceAffinity, 아니면null이다. room/seat의 최종 점수는 personalization 또는0이다. 모두0이면 `unpersonalized`와 `INSUFFICIENT_PREFERENCE_DATA`를 표시하고 candidateId 오름차순으로 정렬한다. 개인에게 잘 맞는 공간이라고 주장하지 않는다.

선택 이력 정책은 백엔드가 적용한다.

```text
최근30일의 중복 제거된 candidateSelected 이벤트만 사용
ageDays = evaluatedAt과 선택 시각의 차이(일)
decayedCount = Σ 2^(-ageDays/7)
choiceAffinity = min(1, decayedCount/5)
```

선택 횟수는 공식 예약 성공이나 실제 착석 확률이 아니다. choiceAffinity는 같은 대상을 다시 고르는 습관이며, 밝기·콘센트 같은 **특성 취향은 6.6절의 이력 학습으로만** 바꾼다. 선택 한 번은 학습 취향을 조금만 움직이며(사전값과 불확실성에 비례), 근거 수가 적은 학습 항목은 불확실성과 함께 표시한다.

### 6.3 주변 도서관: 거리와 취향 결합

origin과 기관 좌표로 Haversine 직선거리를 계산한다. 지구 반지름은6,371,008.8m다. 도보거리·자동차거리·소요시간이 아니며 길찾기 연동 없이 ETA를 만들지 않는다.

```text
distanceUtility = max(0, 1 - distanceMeters/maxDistanceMeters)
libraryScore = (1-distanceWeight)*representativePersonalization
             + distanceWeight*distanceUtility
```

- 기본 반경5,000m, 허용100~50,000m. 기본 distanceWeight=0.35, 허용0~1.
- 반경 밖, 좌표 없는 기관, 적격 대표 후보 없는 기관은 제외한다.
- 대표 후보는 해당 기관에서 personalization이 가장 높은 room 또는 seat 한 개다. 동점이면candidateId 오름차순이다.
- 여러 방의 정원이나 점수를 합산해 큰 도서관을 무조건 우대하지 않는다.
- 대표 취향 점수가null이면 final=distanceUtility, `rankingBasis=distanceOnly`, `effectiveDistanceWeight=1`이다.
- 기관 점수가 같으면 거리 오름차순, libraryId 오름차순이다.
- 좌석 특성이 없는 기관은 `candidateLevel=room`, 검증된 개별 좌석이 있는 경우만seat를 요청한다. 한 요청에서 두 수준을 섞지 않는다.
- 모델이 후보 수준이나 반경을 몰래 바꾸지 않는다. 수준 변경·반경 확대는 사용자에게 알리고 새 요청으로 처리한다.

### 6.4 근거와 점수의 해석

score는 정렬 점수이며 만족도 백분율이나 빈자리 확률이 아니다. 구성 점수와 정보 coverage를 분리해 반환한다. 이유는 확인된 피처의 템플릿으로 생성해 LLM 없이 구현할 수 있다. 확인하지 않은 콘센트·전망·의자 푹신함을 추천 이유로 생성하지 않는다.

학습 취향이 점수에 기여한 항목은 `reasons[].code=HISTORY_PATTERN`으로 근거 요약을 붙일 수 있다. 예: “최근 20번 중 콘센트 자리를 17번 고르셨어요(그때 빈자리 중 콘센트 자리는 40%).” 문구의 숫자는 M10 근거 요약(`evidence`)에서만 가져온다.

### 6.5 탐색 (선택)

`options.exploration.enabled=true`이면 상위 K개 중 마지막 자리 한 개를, 확률 `rate`(0~0.3, 기본 0.1)로 상위 K개 밖의 적격 후보 중 무작위 하나와 바꾼다. 각 결과에 `exposurePropensity`(이 후보가 이 순위에 노출될 확률)를 반환하고, 백엔드는 이를 노출·선택 이벤트와 함께 저장한다. 필수 조건·현황 필터를 통과한 후보만 탐색한다. 기본은 꺼짐이며 live에서는 학습 모델 평가 체계가 갖춰진 뒤 켠다. 탐색 결과에는 경고 `EXPLORATION_APPLIED`를 붙인다.

### 6.6 이력 기반 취향 학습 (M10)

#### 6.6.1 선택 모형

선택 관측 n에서 후보 집합 S_n 중 좌석 i를 고를 확률을 조건부 로짓으로 둔다.

```text
V[n,i] = Σ_k w[u,k] * (1 - |t[u,k] - x[n,i,k]|)  +  γ * positionBias(exposedRank)  +  δ[u,purpose]·x[n,i]
P(i | S_n) = exp(V[n,i]) / Σ_{j∈S_n} exp(V[n,j])
```

- `w[u,k]`(importance), `t[u,k]`(target)가 학습 대상이다. x가 null인 특성은 그 관측에서 해당 항을 0으로 두고 근거 수에 세지 않는다.
- 사전 분포: `w[u,k] ~ Normal(μ_k, σ_k²)`. μ_k는 프로필의 사전값(nlAnalysis·survey)이 있으면 그 importance를 모집단 보정식으로 변환한 값, 없으면 모집단 평균이다. σ_k는 사전값의 confidence·inference_type이 낮을수록 크다. 보정 계수는 `modelVersion` 산출물에 포함된다.
- 관측 가중치: `outcome`에 따라 이용 종료까지 머문 선택은 1, 일찍 떠남·평점≤2는 0.3, 이용 확인 없음은 0.6(v2 정책값). 시간 감쇠는 반감기 `halfLifeDays`(기본 60일).
- 위치 편향 γ는 추천 노출 순위의 효과를 분리한다. 탐색 노출은 `exposurePropensity`로 역가중한다.

#### 6.6.2 갱신 규칙

- 사후 분포의 MAP 추정치를 importance·target으로 반환하고, 사후 표준편차를 0~1로 정규화해 `uncertainty`로 반환한다.
- `locked=true` 항목은 바꾸지 않는다. 직접 설정(explicit)은 기본 잠금이다. 사용자가 잠금을 풀면 학습 대상이 된다.
- 유효 관측 수가 `minObservations`(기본 3) 미만이면 `status=insufficientData`이고 사전값을 그대로 돌려준다.
- 이진 특성의 target은 0 또는 1로만 반환한다(학습 중 연속값은 내부에서만 사용).
- importance 변화가 0.02 미만이면 변경 없음으로 본다.

#### 6.6.3 근거 요약

각 학습 항목에 대해 `evidence={featureId, observations, chosenRate, availableRate}`를 반환한다. 이진 특성은 “고른 좌석 중 특성 보유 비율” 대 “그때 빈자리 중 특성 보유 비율”, 연속 특성은 고른 좌석의 평균값 대 빈자리 평균값이다. 화면 문구와 HISTORY_PATTERN 이유는 이 값만 사용한다.

#### 6.6.4 습관과 취향의 구분

같은 좌석 번호를 반복하는 습관은 choiceAffinity(그 도서관 안에서만 유효)로, 특성 선호는 학습 프로필(다른 도서관에도 적용)로 분리한다. 특정 좌석에만 앉는 사용자의 특성 취향은 choiceSet 비교로만 추정하며 습관을 특성 취향으로 과대 해석하지 않는다.

#### 6.6.5 활성화 기준

학습 모델은 오프라인에서 다음을 통과해야 live(P5)에 켠다. 통과 전에는 `capabilities.preferenceLearning=false`이거나 simulation에서만 사용한다.

1. 사용자 단위 시간순 분할(각 사용자 마지막 방문들 보류)에서, 선택 시점의 실제 빈자리 중 실제 선택 좌석의 Hit@5·NDCG@5가 규칙 엔진(6.2절, 학습 없는 프로필)보다 통계적으로 유의하게 높다.
2. 관측 수 구간(0, 1~3, 4~10, 11회 이상)별로 성능이 사전값만 쓴 경우보다 나빠지지 않는다.
3. 필수 조건 위반 0건, 미상 특성의 0 대체 0건.
4. simulation(합성 정답 취향)에서 관측 수가 늘수록 학습 가중치와 정답 가중치의 상관이 증가함을 확인한다. 합성 데이터 결과는 파이프라인 검증이며 실제 성능의 근거로 쓰지 않는다.

## 7. 내부 모델 API 목록

| 번호 | 메서드·경로 | 기능 |
|---|---|---|
| M01 | `GET /health` | 모델 프로세스 생존·버전 확인 |
| M02 | `GET /v1/capabilities` | 구현된 연산·계약·최대 크기 조회 |
| M03 | `GET /v1/features` | 특성 ID·단위·해석 기준 조회 |
| M04 | `POST /v1/preferences/initialize` | 설문으로 초기 취향 계산 |
| M05 | `POST /v1/recommendations/rooms` | 열람실 개인화 추천 |
| M06 | `POST /v1/recommendations/seats` | 검증된 개별 좌석 추천 |
| M07 | `POST /v1/recommendations/libraries` | 위치·거리·대표 공간 취향으로 도서관 추천 |
| M08 | `POST /v1/preferences/update` | 명시적 특성 피드백 보정 |
| M09 | `POST /v1/preferences/from-analysis` | 카카오톡·자연어 분석 결과를 사전값 프로필로 변환 |
| M10 | `POST /v1/preferences/learn` | 선택 이력(당시 빈자리 집합 포함)에서 취향 학습 |

**이후 모든 `DEMO-*` ID와 예시 수치는 계약 검증용 합성 예시다. 실제 기관의 환경·잔여석 자료로 저장하지 않는다.**

### M01. `GET /health` — 모델 서버 생존 확인

**기능과 호출 시점:** 컨테이너·로드밸런서가 프로세스의 HTTP 처리 가능 여부를 검사한다. 기관 데이터가 충분하거나 공공 API가 연결됐는지를 뜻하지 않는다.

**입력:** 없음.

**출력:** `status`, `serviceVersion`, `engineVersion`, `modelVersion`.

```json
{"status":"ok","serviceVersion":"2.0.0","engineVersion":"seatrec-2.0.0","modelVersion":null}
```

**예외·제약:** 프로세스가 정상 응답할 수 없으면503이다. engineVersion은 점수·필터 규칙의 버전이다. modelVersion=null은 학습 산출물이 적재되지 않아 규칙 엔진만 동작한다는 뜻이다.

### M02. `GET /v1/capabilities` — 연산 지원 여부 조회

**기능과 호출 시점:** 백엔드 시작·배포 시 모델이 지원하는 추천 단위, 모드, 거리 종류, 입력 한도를 읽는다. 미지원 연산을 프론트에 노출하지 않도록 한다.

**입력:** 없음.

**출력 예시:**

```json
{
  "contractVersion":"2.0.0",
  "engineVersion":"seatrec-2.0.0",
  "featureRegistryVersion":"features-2",
  "trainedModel":false,
  "modelVersion":null,
  "supportedTargets":["library","room","seat"],
  "supportedModes":["live","preview","simulation"],
  "distanceMetric":"straightLine",
  "maxCandidates":5000,
  "maxTopK":20,
  "maxObservations":200,
  "nlPreferenceImport":true,
  "preferenceLearning":false,
  "realtimeFeatures":true,
  "exploration":true,
  "occupancyPrediction":false,
  "seatReservation":false
}
```

`preferenceLearning=false`이면 M10은 사전값을 그대로 돌려주는 `status=priorOnly`로만 동작한다. 학습 산출물이 적재되고 6.6.5절 기준을 통과한 배포에서만 true다.

**예외·제약:** 엔진의 seat 추천 연산 지원과 기관별 좌석 데이터 준비 상태는 다르다. 후자는 백엔드 카탈로그에서 관리한다. 구현하지 않은 연산은 지원한다고 표시하지 않는다.

### M03. `GET /v1/features` — 취향 항목 사전 조회

**기능과 호출 시점:** 백엔드와 프론트가 같은 피처 ID·이진/연속 구분·정규화 기준으로 설문과 입력을 만든다. 설정 화면 초기화와 계약 버전 점검에 사용한다.

**입력:** 없음.

**출력:** `{featureRegistryVersion,features:[{featureId,label,type,source,normalization,requiresData}]}`. 5.2절 목록을 반환한다. `source`는 `static|realtime`이다.

**예외·제약:** 기관에 해당 피처 자료가 없어도 저장 가능한 취향은 존재할 수 있다. UI는 ‘저장 가능하지만 이 기관 추천에는 미반영’을 구분한다. 피처 정규화 변경은 버전을 변경한다.

### M04. `POST /v1/preferences/initialize` — 초기 취향 계산

**기능과 호출 시점:** 가입·설문 완료 직후 사용자가 직접 답한 취향을 초기 프로필로 만든다. 설문을 건너뛰면 빈 취향으로 시작한다.

**요청:**

```json
{
  "requestId":"req-demo-init",
  "featureRegistryVersion":"features-2",
  "answers":[{"featureId":"laptopAllowed","target":1,"importance":1}]
}
```

**출력:** `preferences`, `engineVersion`, `featureRegistryVersion`. 각 항목은 `source=survey`다. DB revision과 사용자 ID는 백엔드가 부여한다. 입력 답변을 계약에 맞게 검사·정리한 결정적 결과이며 숨은 성격 추정은 하지 않는다.

**예외·제약:** 피처 중복·미등록 ID·이진 target=0.5·범위 밖 중요도는422다. 사용자 DB 저장은 이 API의 책임이 아니다.

### 공통 추천 요청

```json
{
  "requestId":"req-demo-room",
  "evaluatedAt":"2026-11-14T17:30:00+09:00",
  "datasetVersion":"DEMO-DATA-v1",
  "featureRegistryVersion":"features-2",
  "executionMode":"live",
  "profile":{"revision":0,"featureRegistryVersion":"features-2","preferences":[]},
  "preferenceOverrides":[],
  "requirements":[],
  "excludedCandidateIds":[],
  "context":{"purpose":"unknown","expectedStayMinutes":null},
  "options":{"topK":5,"availabilityPolicy":"allowUnknown","maxAvailabilityAgeSeconds":120,"minFeatureCoverage":0,"exploration":{"enabled":false,"rate":0.1}},
  "candidates":[]
}
```

`context`와 `options.exploration`은 선택 필드다.

후보의 features·검수 여부·현황·이력 보정값은 백엔드가 생성한다. 사용자 이름·메일·학번은 계산에 필요하지 않다. 오늘의 조건만 바꾸는 경우 영구 프로필을 수정하지 않는다.

### M05. `POST /v1/recommendations/rooms` — 열람실 추천

**기능:** 선택한 기관들에서 노트북 규정·칸막이·키보드 사용 등 확인된 특성으로 열람실을 순위화한다. 좌석 ID나 좌표가 없는 기관에서도 사용할 수 있는 핵심 기능이다.

**호출 시점:** 도서관 선택 후, 오늘 취향·필수 조건 변경 후, 해당 열람실 현황 갱신 후.

**입력:** 공통 추천 요청. candidates는 모두kind=room이다. `roomAggregate`가 신선하면 잔여석이 관측된 열람실을 필터링할 수 있다.

**처리:** 공통 필터와 점수 규칙을 적용한다. 열람실 규정은 확인된 범위에서만 사용하며 개별 좌석 상태를 생성하지 않는다.

**출력:** results의targetType=room, targetId=roomId. mapAnchor는 확보했을 때만 제공한다. 열람실에 잔여석이 관측됐더라도 특정 좌석의 이용 가능·예약을 보장하지 않는다.

**오류·빈 결과:** candidates에seat가 섞이면422다. 모든 후보가 노트북 필수 조건 미상 또는 confirmedOnly의 현황 미상으로 탈락하면200/noEligibleCandidates와 제외 사유를 반환한다.

### M06. `POST /v1/recommendations/seats` — 개별 좌석 추천

**기능:** 지정한 열람실의 검증된 좌석을 취향과 개인 선택 이력으로 순위화하고 웹 지도 위치와 연결한다. 지금 확인된 빈자리만 필요하면confirmedOnly를 사용한다.

**호출 시점:** 열람실 안의 자리를 고를 때, 좌석 취향 변경 후, 좌석 단위 현황 갱신 후, 선택·교정 이후 재추천할 때.

**입력:** 공통 추천 요청. candidates는kind=seat이며 live에서는identityVerified=true, geometryVerified=true, mapAnchor가 필요하다.

**처리:** 현재 11관 좌석 후보 2,111개는 모두 사람 검수 전(`model_candidate`)이므로 live 후보로 넣으면 제외돼야 한다. preview는 화면 검토, simulation은 합성 환경 시연·학습에만 사용한다. roomAggregate만 있으면 특정 좌석 상태는unknown이고 실시간 특성 neighborOccupancy는null이다. 학습 프로필이 있으면 6.2절 식에 그대로 들어가며 근거 요약이 있는 항목에 HISTORY_PATTERN 이유를 붙인다.

**출력:** targetType=seat, targetId=seatId, 지도 위치, 취향 점수, 정보 coverage, 관측된 현황과 근거다. 좌표에 없는 화장실·에어컨을 생성하지 않는다.

**오류·빈 결과:** 잘못된 bbox·종류·ID는422다. 검수 전 좌석이나 현황 미상으로 전부 제외돼도200/noEligibleCandidates다. 상태 공급원이 없으면allowUnknown의 ‘취향 자리 후보’까지만 제공할 수 있다. 예약·배정 명령은 수행하지 않는다.

### M07. `POST /v1/recommendations/libraries` — 현재 위치·거리·취향으로 도서관 추천

**기능:** 사용자의 출발 위치에서 반경 안 도서관을 찾고, 각 기관의 가장 적합한 대표 열람실 또는 좌석을 고른 뒤 거리와 취향을 함께 평가한다. 단순 근처 목록과 달리 ‘그 도서관에 내 취향 공간이 있는가’를 반영한다.

**호출 시점:** 홈의 ‘어디로 갈까’ 진입, 위치·반경·거리 중요도·오늘 조건 변경 후.

**공통 요청에 추가하는 필드:**

```json
{
  "origin":{"latitude":37.5,"longitude":127.0},
  "maxDistanceMeters":5000,
  "distanceWeight":0.35,
  "candidateLevel":"room",
  "libraries":[
    {"libraryId":"DEMO-LIB-A","latitude":37.5,"longitude":127.01},
    {"libraryId":"DEMO-LIB-B","latitude":37.5,"longitude":127.02}
  ]
}
```

이 추가 필드만으로 완전한 요청이 되지는 않는다. 공통 profile/options/candidates와 함께 보낸다. libraries와 candidates는 내부libraryId로 연결한다.

**처리:** 반경·기관 좌표·후보 적격성을 검사한다. candidateLevel=room이면 대표 열람실, seat이면 검증된 대표 좌석을 선택한다. 두 수준을 섞지 않는다. 6.3절 식으로 점수를 결합한다.

**출력:** 기관 ID, 대표 후보·방·좌석 ID, 직선거리meters, distanceUtility, 취향 점수·coverage, 결합 점수, 관측 현황과 이유다. `distanceMetric=straightLine`을 명시하며 도보 시간·도보거리는 이 계약에서 제공하지 않는다.

**순위 예:** 가까운A의 대표 공간이 노트북 가능·칸막이 없음이고, 조금 먼B가 노트북 가능·칸막이 있음이라면 칸막이 선호가 높을 때B가1순위일 수 있다. 거리 중요도를 높이면A가 우선할 수 있으며 같은 식으로 순위가 재현돼야 한다.

**오류·빈 결과:** origin 누락·잘못된 위경도·중복 기관 ID는422다. 좌표 없는 후보 기관은MISSING_LIBRARY_LOCATION, 반경 밖은OUTSIDE_RADIUS로 제외한다. 좌석 자료가 없으면 열람실로 몰래 바꾸지 않고 빈 결과를 반환한다. GPS가 없으면 사용자가 지도에서 정한 출발 좌표를 같은 계약으로 받을 수 있지만 geocoding API는 제공하지 않는다.

**한계:** 확인 시점에 남은 좌석이 있어도 도착 시점의 빈자리를 보장하지 않는다. 원본 좌표 없이 기관 이름으로 임의 위치를 만들지 않는다.

### 세 추천 API의 공통 응답

```json
{
  "requestId":"req-demo-room",
  "engineVersion":"seatrec-2.0.0",
  "featureRegistryVersion":"features-2",
  "datasetVersion":"DEMO-DATA-v1",
  "evaluatedAt":"2026-11-14T17:30:00+09:00",
  "executionMode":"live",
  "outcome":"noCandidates",
  "results":[],
  "excluded":[],
  "warnings":[],
  "statistics":{"inputCandidates":0,"eligibleCandidates":0,"returnedTargets":0}
}
```

결과가 있으면 각 항목에 다음 정보가 있다.

| 필드 | 의미 |
|---|---|
| `rank`, `targetType`, `targetId` | 순위와 실제 추천 단위·ID |
| `libraryId`, `roomId`, `seatId` | 기관·방·좌석 연결. 해당 없는 값은null |
| `representativeCandidateId` | 도서관을 대표한 후보. 다른 추천에서는 해당 후보 |
| `mapAnchor` | 지도 프레임의 정규화 위치. 없으면null |
| `rankingBasis` | `preferencesAndHistory\|preferences\|history\|distanceOnly\|unpersonalized` |
| `scores` | featureScore, featureCoverage, choiceAffinity, personalizationScore, distanceUtility, effectiveDistanceWeight, finalScore |
| `availability` | effectiveState, scope, observedAt, expiresAt |
| `availabilityVerified` | 이 추천 단위에서 신선한 이용 가능 관측을 사용했는지 |
| `distance` | 도서관 추천의 `{meters,metric:straightLine}`, 그 외null |
| `reasons` | `{code,featureId,sourceId,text}` 배열 |
| `warnings` | 미상·부분 자료·거리 종류 등의 주의 코드 |
| `previewOnly` | true이면 운영 선택·이용 신고 불가(preview·simulation) |
| `exposurePropensity` | 탐색을 켠 요청에서 이 후보가 이 순위에 노출될 확률. 탐색이 꺼져 있으면 null |

outcome은 `ranked|noCandidates|noEligibleCandidates`다. 점유·예약·차단 후보는 결과에 남기지 않으므로 결과의 effectiveState는available 또는unknown이다.

대표 제외 코드: CLOSED, EXCLUDED_BY_USER, UNVERIFIED_IDENTITY, UNVERIFIED_GEOMETRY, KNOWN_UNAVAILABLE, AVAILABILITY_UNKNOWN, HARD_REQUIREMENT_UNKNOWN, HARD_REQUIREMENT_NOT_MET, INSUFFICIENT_FEATURE_COVERAGE, MISSING_LIBRARY_LOCATION, OUTSIDE_RADIUS.

대표 경고 코드: AVAILABILITY_UNKNOWN, OPERATING_STATE_UNKNOWN, FEATURES_PARTIALLY_UNKNOWN, INSUFFICIENT_PREFERENCE_DATA, PREVIEW_ONLY, STRAIGHT_LINE_DISTANCE_ONLY, SIMULATION_DATA, EXPLORATION_APPLIED, LEARNED_PREFERENCES_UNCERTAIN, REALTIME_FEATURES_UNKNOWN.

대표 이유 코드: FEATURE_MATCH, HISTORY_PATTERN, HABIT(같은 좌석·열람실 재방문), REALTIME_QUIET(주변이 비어 있음), AVAILABILITY_CONFIRMED.

### M08. `POST /v1/preferences/update` — 명시적 취향 피드백 보정

**기능:** ‘조명을 더 어둡게 원함’, ‘칸막이가 더 중요함’처럼 사용자가 직접 교정한 특성을 현재 저장 프로필에 반영할 새 값을 계산한다. DB는 변경하지 않는다.

**호출 시점:** 이용 후 평가에서 구체적인 특성 교정이 제출됐을 때. 단순 선택이나 별점만으로 호출하지 않아도 된다.

**요청:** requestId, eventId, 현재profile, corrections 배열. 교정 항목은 `{featureId,target,importance}`다. 백엔드가 이벤트 소유권·중복 여부와 최신 프로필을 확인한다.

**계산:** 기존 연속 항목은 target과importance 각각 `0.8*old+0.2*correction`으로 보정한다. 이진 target은 교정값0/1을 직접 적용하여0.2 같은 잘못된 이진 목표를 만들지 않는다. 새 항목은 교정값으로 추가한다. 학습된 최적 계수가 아닌 v1 고정 정책이다.

**출력:** baseRevision, updatedPreferences, changed, changes[]. 변경이 있으면 백엔드가 revision CAS 비교 후 한 번만 저장한다. 충돌 시 최신 프로필로 같은 명시적 교정 이벤트를 다시 계산한다. 모델은 새 revision을 발급하지 않는다.

**오류·제약:** 미등록 ID·중복 교정·버전 불일치는422다. 클릭·미선택·별점만으로 모든 피처를 변경하거나 오래된 추천 당시 프로필로 최신 취향을 덮어쓰지 않는다. 오늘의 일시 override를 영구 저장하지 않는다. 교정된 항목은 `source=correction`이며, 사용자가 잠그지 않는 한 이후 M10 학습의 사전값으로 쓰인다.

### M09. `POST /v1/preferences/from-analysis` — 자연어 분석 결과를 사전값으로 변환

**기능:** 팀원의 카카오톡·자연어 분석기가 낸 참여자별 결과(`kakao_analysis/schema/kakaotalk_schema.json` 형식)를 features-2 프로필 항목으로 바꾼다. 결과는 이력이 쌓이기 전의 사전값이며 `source=nlAnalysis`다. 이 API는 텍스트를 분석하지 않는다.

**호출 시점:** 사용자가 대화 분석을 연동·갱신했을 때(B12).

**요청:**

```json
{
  "requestId":"req-demo-nl",
  "featureRegistryVersion":"features-2",
  "analysis":{
    "participant_id":"user-pseudo-001",
    "preferences":{
      "restroom":{"preference":"unknown","confidence":0.0,"inference_type":"unknown","reason":"근거 없음"},
      "lighting":{"preference":"bright","confidence":0.45,"inference_type":"contextual","reason":"..."},
      "air_conditioning":{"preference":"avoid_direct_airflow","confidence":0.8,"inference_type":"direct","reason":"..."},
      "desk":{"preference":"height_adjustable","confidence":0.65,"inference_type":"direct","reason":"..."},
      "laptop_usage":{"preference":"uses_laptop","confidence":0.95,"inference_type":"direct","reason":"..."},
      "outlet":{"preference":"required","confidence":0.93,"inference_type":"direct","reason":"..."},
      "view":{"preference":"window","confidence":0.55,"inference_type":"contextual","reason":"..."}
    },
    "evidence":[{"feature":"outlet","message_id":"msg_102","reason":"..."}]
  },
  "currentProfile":null
}
```

`analysis`는 분석기 원본 형식을 그대로 받는다(snake_case 예외). `participant_id`는 백엔드가 내부 가명 ID로 바꿔 보낸다. 실명·연락처를 넣지 않는다. `behavioral_summary`, `reason`, `evidence`, `seat_recommendation`은 점수 계산에 쓰지 않고 감사 기록용으로만 받는다.

**값 정규화:** 분석기 문서의 허용 값 표와 응답 예시 어휘를 **모두** 받는다. 대소문자·공백·하이픈은 무시한다.

| 항목 | 받는 값 → 표준값 | features-2 변환 (target, 기본 importance, 대응) |
|---|---|---|
| restroom | near, close → near / no_preference | near → toiletProximity(1, 0.7, direct) |
| lighting | bright / dim, dark | bright → brightness(0.85, 0.7, direct), dim → brightness(0.3, 0.6, direct) |
| air_conditioning | cool, cold / warm / **avoid_direct_airflow**, no_direct_airflow | cool → airflow(0.7, 0.6, proxy), warm → airflow(0.1, 0.6, proxy), avoid_direct_airflow → airflow(0.0, 0.9, direct) |
| desk | large, wide / compact, small / **height_adjustable** | large → deskWidth(1, 0.6), compact → deskWidth(0.3, 0.3), height_adjustable → 대응 특성 없음(unmapped) |
| laptop_usage | yes, **uses_laptop** / no, no_laptop | yes → laptopAllowed(1, 1.0, direct). confidence≥0.6이면 requirements에도 추가 |
| outlet | required, needed / not_required | required → outletAvailable(1, 0.9, direct) |
| view | window, near_window / no_preference | window → windowProximity(1, 0.6, direct) + viewQuality(1, 0.4, proxy) |

`unknown`·빈 값은 신호 없음이다. 허용 목록에 없는 값은 422가 아니라 경고와 함께 unknown으로 처리한다(분석기 어휘 변경에 추천 전체가 실패하지 않도록). no_preference·not_required·no는 해당 특성을 활성화하지 않는다.

**importance 계산:** `기본 importance × confidence × 추론계수(direct 1.0, contextual 0.7, unknown 0) × 대응계수(direct 1, proxy 0.5)`. 같은 featureId가 여러 항목에서 나오면 큰 값을 쓴다. 확신도는 분석기 문서대로 통계적 확률이 아니므로, 학습 산출물이 있으면 모집단 보정식으로 다시 변환한다(6.6.1절). `uncertainty`는 `1 - confidence×추론계수`, evidenceCount는 0이다.

**병합:** `currentProfile`이 있으면 locked 항목과 explicit·correction 항목은 유지하고, nlAnalysis·learned가 아닌 빈 자리에만 사전값을 채운 병합 결과도 함께 반환한다. 학습된(learned) 항목은 덮어쓰지 않고 사전값만 갱신한다.

**출력:** `preferences`(변환 결과), `mergedProfile`(병합 결과 또는 null), `requirements`, `items[]`(항목별 원값·표준값·상태·변환 내역), `unmapped[]`, `warnings[]`, `mappingVersion`.

**오류·제약:** analysis가 객체가 아니거나 preferences가 없으면 422다. 7개 외 항목은 경고 후 무시한다. 분석 결과로 필수 조건을 강제하지 않으며, 노트북 필수 조건은 사용자 확인 후 저장하도록 백엔드가 UI에서 묻는다.

### M10. `POST /v1/preferences/learn` — 선택 이력에서 취향 학습

**기능:** 사용자의 선택 관측(5.7절)을 받아 그 사람의 특성 취향(importance·target)과 불확실성을 계산한다. **고른 좌석을 그때 비어 있던 좌석들과 비교**하므로 붐벼서 어쩔 수 없이 앉은 자리는 약한 근거, 빈자리가 많았는데도 고른 자리는 강한 근거가 된다. DB는 변경하지 않는다.

**호출 시점:** candidateSelected·useEnded가 저장된 뒤(사용자별 디바운스), 또는 야간 배치. 추천 요청마다 호출하지 않는다.

**요청:**

```json
{
  "requestId":"req-demo-learn",
  "evaluatedAt":"2026-11-14T17:30:00+09:00",
  "featureRegistryVersion":"features-2",
  "executionMode":"simulation",
  "profile":{"revision":5,"featureRegistryVersion":"features-2","preferences":[
    {"featureId":"laptopAllowed","target":1,"importance":1,"source":"explicit","locked":true},
    {"featureId":"brightness","target":0.85,"importance":0.32,"source":"nlAnalysis","uncertainty":0.69,"evidenceCount":0}
  ]},
  "observations":[ "5.7절 선택 관측 1~200개" ],
  "options":{"halfLifeDays":60,"minObservations":3,"maxFeatures":14}
}
```

**출력:**

```json
{
  "requestId":"req-demo-learn",
  "engineVersion":"seatrec-2.0.0",
  "modelVersion":"prefmodel-sim-0",
  "featureRegistryVersion":"features-2",
  "status":"learned",
  "baseRevision":5,
  "observationsUsed":18,
  "observationsIgnored":[{"eventId":"event-demo-sel-0003","reason":"CHOICE_SET_TOO_SMALL"}],
  "learnedPreferences":[
    {"featureId":"laptopAllowed","target":1,"importance":1,"source":"explicit","locked":true},
    {"featureId":"outletAvailable","target":1,"importance":0.82,"source":"learned","uncertainty":0.12,"evidenceCount":18},
    {"featureId":"brightness","target":0.7,"importance":0.25,"source":"learned","uncertainty":0.41,"evidenceCount":18}
  ],
  "changed":true,
  "changes":[{"featureId":"outletAvailable","before":null,"after":{"featureId":"outletAvailable","target":1,"importance":0.82}}],
  "evidence":[{"featureId":"outletAvailable","observations":18,"chosenRate":0.89,"availableRate":0.41}]
}
```

`status`는 `learned|insufficientData|priorOnly`다. insufficientData·priorOnly이면 learnedPreferences는 입력 프로필과 같다. 출력 프로필은 최대 20개이며 importance가 낮은 학습 항목부터 잘린다. 계산은 6.6절을 따른다.

**ignored 사유:** CHOICE_SET_TOO_SMALL(빈자리 1개뿐), CHOSEN_NOT_IN_CHOICE_SET, STALE_SNAPSHOT(선택 시각과 스냅샷 관측 차이가 maxAvailabilityAgeSeconds 초과), MODE_MISMATCH(simulation 관측을 live 요청에 섞음), DUPLICATE_EVENT.

**오류·제약:** 관측 200개 초과, 중복 eventId, 선택 후보 누락이 전부인 요청, 미등록 featureId는 422다. live 요청에 synthetic 관측을 넣으면 422다. 백엔드는 revision CAS로 저장하며 충돌 시 최신 프로필로 다시 호출한다. 모델은 revision을 발급하지 않는다. 학습 결과를 성격·건강 등 민감 속성으로 해석하거나 표시하지 않는다.

## 8. 실시간 갱신과 사용자 이벤트 정합성

### 8.1 현황과 캐시

백엔드가 공급원별 현황 캐시를 갱신하고 요청 시 스냅샷을 결합한다. 프론트는 모델·공공 API를 직접 호출하지 않는다. 공급원이 허용한 주기보다 빠르게 수집하지 않는다. v1은 SSE/WebSocket 없이 주기적인 백엔드 조회와 명시적 재추천으로 구현할 수 있다.

사용자 취향·선택 변경은 해당 사용자 추천 캐시, 자료·현황 버전 변경은 해당 공간 캐시를 무효화한다. 키에는 프로필revision, 검색조건, 정적버전, 현황스냅샷ID, modelVersion을 포함한다.

**학습을 위한 스냅샷 보존:** 좌석 추천 시 사용한 현황 스냅샷(좌석별 state·observedAt)과 후보 특성값을 snapshotId로 저장한다. 선택 이벤트는 이 snapshotId를 참조하며, M10의 choiceSet은 여기서 만든다. 스냅샷은 좌석 상태만 담고 다른 사용자의 신원·이용시간은 담지 않는다. 보존 기간은 학습 반감기의 2배(기본 120일)를 넘기지 않는다. 정밀 출발 좌표를 일반 로그나 장기 캐시에 보존하지 않는 것을 기본으로 한다.

백엔드 추천의expiresAt은 생성 후60초와 실제 사용한 신선한 현황 만료시각 중 이른 값이다. 선택 직전에 상태·지도버전·필수 조건을 재검증한다. 모델 계산과 실제 선택 사이에 빈자리가 달라질 수 있다.

### 8.2 이벤트의 의미

| 이벤트 | 관측한 사실 | 반영 |
|---|---|---|
| recommendationExposed | 실제 노출된 결과·순서 | 평가 분모. 미노출 후보를 음성라벨로 사용하지 않음 |
| candidateSelected | 서비스에서 선택한 공간 | choiceAffinity 반영 + 선택 관측(5.7절) 생성 → M10 학습 |
| useConfirmed | 사용자의 이용 시작 자기신고 | 공식 배정·실제 착석 검증과 구분. 선택 관측의 outcome.useConfirmed |
| useEnded | 이용 종료 자기신고 또는 자동 종료(앱 퇴실·시간 초과) | outcome.stayMinutes·endedEarly. 일찍 떠난 선택은 약한 근거 |
| ratingSubmitted | 만족도·명시적 특성 교정 | 평가 저장. 교정이 있을 때만M08 계산 |
| candidateRejected | 명시적 회피와 이유 | 사용자가 요청한 제외 목록 갱신 |

선택·신고·평가 시각은 서비스 행동 DB에 저장한다. 외부 도서관의 개인 점유시간 수집과 다르며 정적 좌석 파일과 분리한다. 노출됐지만 고르지 않은 좌석을 곧바로 싫어요로 학습하지 않는다. 대신 선택이 있었던 관측에서는 **같은 시점에 이용 가능했던 다른 후보 전체**와의 상대 비교로만 학습한다(조건부 로짓). 선택이 없었던 노출(이탈)은 학습에 쓰지 않는다.

### 8.3 멱등성·동시성

- 백엔드 추천 생성·이벤트·프로필 수정은 `Idempotency-Key`를 사용한다. 같은 사용자/경로/키에 같은 본문은 같은 결과, 다른 본문은409다.
- 같은 키의 추천 재시도는 원래 스냅샷을 반환한다. 최신 현황은 새 키로 재추천한다. 만료된 추천으로 새 선택을 허용하지 않는다.
- eventId는 사용자 범위에서 유일하다. 한 선택을 이력에 두 번 반영하지 않는다.
- 프로필PUT은 expectedRevision CAS 저장이다. 모델은 revision을 발급하지 않는다.
- 백엔드는 추천 당시 취향·피처·노출순서·버전·후보와 이벤트를 연결한다. 클라이언트가 보낸 피처로 학습하지 않는다.
- 모델 실패 시503 또는 유효기간 안의 이전 결과임을 명시한다. MOCK_LIBRARIES로 대체하지 않는다.

## 9. 사용자용 백엔드 API

프론트가 호출하는 API다. 내부 모델 API와 저장·인증 책임을 섞지 않는다. 개인 API는 인증된 사용자 기준이며 본문의userId로 타인의 프로필을 선택할 수 없다. 성공 응답은 `{data,meta:{requestId}}`, 오류는4.3절 형식이다.

### B01. `GET /api/v1/libraries` — 기관 목록과 데이터 지원 범위

**기능·시점:** 지도·기관 목록에서 기관 마스터와 실제 지원 단계를 확인한다. 개인화 주변 순위는B10이다.

**입력:** 선택적cursor, limit1~100(기본20).

**출력:** `{items:[{libraryId,name,latitude,longitude,datasetVersion,capabilities:{roomRecommendation,seatRecommendation,seatMap,roomAvailability,seatAvailability,facilityFeatures}}],nextCursor}`.

**예외·제약:** 미확인 좌표는null, 목록 없음은200과빈items다. capabilities는 해당 기관의 검수·연동 상태이며 후보 링크만 있는 기관을 모두ready로 표시하지 않는다.

### B02. `GET /api/v1/libraries/{libraryId}/rooms` — 열람실과 규정 조회

**기능·시점:** 도서관 선택 후 열람실·층·정원·규정·지원 기능을 보여준다.

**입력:** libraryId.

**출력:** `{libraryId,items:[{roomId,name,floor,totalSeats,featureSummary,mapId,capabilities,sourceDate,datasetVersion}]}`.

**예외·제약:** 미존재 기관404. 정원은 빈자리 수가 아니다. 노트북실이 있어도 좌석 소속이 미매핑이면seatRecommendation=false다.

### B03. `GET /api/v1/rooms/{roomId}/map` — 웹 좌석도·시설 레이어

**기능·시점:** 좌석 지도, 추천 좌석 강조, 등록된 시설 아이콘을 표시한다. 모델이 그림을 생성하는 API가 아니다.

**출력:** `{roomId,mapId,datasetVersion,mapVersion,width,height,coordinateUnit:"normalized",backgroundUrl,verification,seats:[{seatId,label,x,y,width,height,verification}],facilities:[{facilityId,type,geometry,verification}],sourceDate}`.

**예외·제약:** 지도 원본 미확보는404/MAP_NOT_AVAILABLE. 검수 전 지도는 별도preview환경에서만 제공한다. facilities=[]는 등록 자료 없음이지 실제 시설 부재가 아니다. 서비스가 관리하는 자원 URL만 반환하고 임의 사용자 URL을 프록시하지 않는다. 정적 레이어에 현황 색·성별·사용시간을 넣지 않으며 현황은B04로 분리한다.


**응답 형식 예시(합성 자료):**

```json
{
  "data": {
    "roomId": "DEMO-ROOM-A",
    "mapId": "DEMO-MAP-A",
    "datasetVersion": "DEMO-DATA-v1",
    "mapVersion": "DEMO-MAP-v1",
    "width": 1000,
    "height": 700,
    "coordinateUnit": "normalized",
    "backgroundUrl": null,
    "verification": "verified",
    "seats": [
      {
        "seatId": "DEMO-SEAT-1",
        "label": "1",
        "x": 0.1,
        "y": 0.2,
        "width": 0.03,
        "height": 0.04,
        "verification": "verified"
      }
    ],
    "facilities": [],
    "sourceDate": "2026-11-14"
  },
  "meta": {
    "requestId": "req-demo-map"
  }
}
```

### B04. `GET /api/v1/rooms/{roomId}/availability` — 마지막 관측 현황 조회

**기능·시점:** 현황 배지·좌석 상태를 새로고침하며 기준시각과 유효기간을 보여준다.

**입력:** roomId, 선택적 `mode=live|simulation`(기본 live, simulation은 개발·시연 환경 전용).

**출력:** `{roomId,mode,snapshotId,roomAggregate:null|{totalSeats,remainingSeats,observedAt,expiresAt,sourceId},seatStates:[{seatId,state,observedAt,expiresAt,sourceId}],receivedAt}`. `snapshotId`는 이 관측을 가리키는 ID로, 추천·선택 이벤트와 M10 학습의 choiceSet이 참조한다. 기계 판독 계약은 [BACKEND_SEATS_API.openapi.json](be-model/BACKEND_SEATS_API.openapi.json)에 있다.

**예외·제약:** 개별 좌석 연동이 없으면seatStates=[]다. 다른 이용자의 신원·이용시간은 포함하지 않는다. 현황이 전혀 없어도200과집계null/빈배열로 응답한다. 수집 실패를 remainingSeats=totalSeats로 치환하지 않는다. 미래 예측을 반환하지 않는다.

### B05. `GET /api/v1/users/me/preferences` — 저장 취향 조회

**기능·시점:** 설정 화면·오늘 조건 입력을 초기화한다.

**출력:** `{revision,featureRegistryVersion,preferences}`. 신규 사용자는revision0/preferences=[]다.

**제약:** 백엔드 사용자 저장소를 읽는다. 모델에 사용자의 취향 조회 책임을 넘기지 않는다.

### B06. `PUT /api/v1/users/me/preferences` — 직접 취향 설정

**기능·시점:** 사용자가 중요도·목표값을 명시적으로 저장한다.

**입력:** `{expectedRevision,featureRegistryVersion,preferences}`. 신규도expectedRevision0이다.

**처리·출력:** M03 규격을 검사하고 필요하면M04로 초기화한 뒤 CAS 저장한다. 직접 설정은 입력값을 그대로 적용하며M08의0.2보정 비율을 적용하지 않는다. 저장한 새revision의 프로필을 반환한다.

**예외·제약:** revision충돌409. 오늘 일시 조건을 이 API로 자동 영구 저장하지 않는다.

### B07. `POST /api/v1/recommendations/rooms` — 내 취향 열람실 추천 생성

**기능·시점:** 지정한 기관 안에서 열람실의 실제 규정·특성에 맞는 공간을 찾는다.

**입력:** `{libraryIds:[...],preferenceOverrides:[],requirements:[],options:{topK,availabilityPolicy,minFeatureCoverage}}`. libraryIds는1~20개다.

**처리:** profile/features/verification/현황/이력은 백엔드가 주입한다. M05를 호출하고 입력 스냅샷과 결과를 저장한다.

**출력:** `{recommendationId,createdAt,expiresAt,profileRevision,result}`. result는 내부 모델 응답이고 백엔드가 기관·열람실 표시명을 더할 수 있다.

**예외·제약:** 후보가 없거나 조건 탈락이어도 같은 저장 형식의 빈 결과다. 사용자가 검수 여부와 특성을 조작할 수 없다.

### B08. `POST /api/v1/recommendations/seats` — 내 취향 좌석 추천 생성

**기능·시점:** 사용자가 특정 열람실 안의 자리를 고를 때 추천한다.

**입력:** `{roomId,preferenceOverrides:[],requirements:[],options:{topK,availabilityPolicy,minFeatureCoverage}}`.

**처리·출력:** M06의 운영 적격 좌석만 사용하며 응답은B07과 같다. mapId/mapVersion은B03과 일치해야 한다.

**예외·제약:** 기본confirmedOnly에서는 개별 현황 미연동으로 빈 결과일 수 있다. 사용자가allowUnknown을 선택하면 이용 가능 미확인 취향 자리임을 명시한다. 캡처 추출 직후의 미검수 좌석을 운영 추천으로 제공하지 않는다. 지도 버전이 바뀌면 선택 전에 재조회한다.

### B09. `POST /api/v1/recommendations/{recommendationId}/events` — 노출·선택·이용 신고·평가 기록

**기능·시점:** 결과가 실제로 보였을 때, 공간을 골랐을 때, 이용을 신고하거나 평가했을 때 기록한다. 선택 여부를 다음 개인화에 반영하는 핵심 저장 API다.

**공통 입력:** eventId, type, occurredAt. 추천 소유권·후보ID·스냅샷을 백엔드가 검사한다.

| type | 추가 입력 | 처리 |
|---|---|---|
| recommendationExposed | visibleTargetIds[] | 저장 결과의 부분집합과 노출 순서 검증 |
| candidateSelected | targetId, origin:recommendation\|outsideRecommendations | 같은 추천 단위의 실제 대상 검증, 개인 이력 반영 |
| useConfirmed | targetId | 앞선 선택 대상과 연결해selfReport로 기록 |
| ratingSubmitted | targetId, rating1~5, corrections[] | 평가 저장. 명시적 교정이 있으면 최신 프로필로M08/CAS 처리 |
| candidateRejected | targetId, reason, excludeFromFuture | 명시적으로 요청한 제외 여부 반영. reason은 `tooNoisy\|tooBright\|tooDark\|tooCold\|tooHot\|noOutlet\|tooCrowded\|tooFar\|other` |
| useEnded | targetId, endedAt, endedBy:`user\|auto` | 체류시간 계산. 선택 관측 outcome 갱신 후 M10 재학습 대상으로 표시 |

**출력:** `{eventId,accepted:true,effect:storedOnly|historyUpdated|preferencesUpdated|learningQueued,profileRevision}`. 중복 이벤트는 같은 결과다. 좌석 선택은 백엔드가 추천 당시 snapshotId를 자동으로 연결하며 클라이언트가 보내지 않는다.

**예외·제약:** 추천 외 선택도 정상 마스터의 실제 대상이어야 하며 클라이언트 피처는 받지 않는다. 추천 외 선택의 choiceSet도 같은 추천의 현황 스냅샷에서 만든다. 도서관 선택은 특정 좌석 이용 확인이 아니다. useConfirmed/useEnded/rating은room/seat추천에만 허용한다. preview·simulation 추천은 실제 사용자 이력으로 선택·신고 불가다(simulation은 별도 합성 이력 저장소에만 기록).

새 선택은 만료·필수조건·지도 버전·현황을 재검증한다. confirmedOnly에서 이용 가능이 더 이상 확인되지 않으면409/AVAILABILITY_CHANGED다. allowUnknown의 미상 선택은 가능하지만 이용 가능 확인으로 기록하지 않는다. 늦게 도착한 노출·평가 이벤트는 발생/수신시각을 분리할 수 있지만 만료된 추천으로 새 선택을 허용하지 않는다. 공식 배정이 없으므로 이용 자기신고를 실제 착석 검증 완료라고 표현하지 않는다.


**선택 요청·응답 예시:** 이 선택만으로 저장 취향의 revision이나 피처 목표를 변경하지 않는다. 다음 추천의 개인 이력값이 달라진다.

```json
{
  "eventId": "event-demo-selected",
  "type": "candidateSelected",
  "occurredAt": "2026-11-14T17:30:10+09:00",
  "targetId": "DEMO-ROOM-B",
  "origin": "recommendation"
}
```

```json
{
  "data": {
    "eventId": "event-demo-selected",
    "accepted": true,
    "effect": "historyUpdated",
    "profileRevision": 3
  },
  "meta": {
    "requestId": "req-demo-event"
  }
}
```

### B10. `POST /api/v1/recommendations/libraries` — 지금 갈 주변 도서관 추천

**기능·시점:** 현재 위치와 취향을 기준으로 근처 공공도서관 중 갈 곳을 고른다. 결과 카드에 거리·대표 열람실/좌석·취향 이유·관측 시각을 함께 표시한다.

**요청 예시:**

```json
{
  "origin":{"latitude":37.5,"longitude":127.0},
  "maxDistanceMeters":5000,
  "distanceWeight":0.35,
  "candidateLevel":"room",
  "preferenceOverrides":[{"featureId":"partition","target":1,"importance":1}],
  "requirements":[{"featureId":"laptopAllowed","operator":"eq","value":1}],
  "options":{"topK":3,"availabilityPolicy":"allowUnknown","minFeatureCoverage":0.5}
}
```

**처리:** 백엔드가 위치와 공간 마스터를 구성해M07을 호출한다. origin은 위치 권한을 받은 좌표 또는 사용자가 선택한 출발점이다. 프로필은 서버 저장값과 오늘 조건을 결합한다.

**출력:** B07 형식의 targetType=library 결과다. 대표candidateId/roomId/seatId, 직선거리, 점수 구성, rankingBasis, 관측 근거가 포함된다.

**예외·제약:** 위치 누락·범위 오류422. 위치 없는 기관을 이름으로 임의 geocoding하지 않는다. 좌석 수준 지원 기관이 없으면 빈 결과와 이유를 반환한다. 프론트가 ‘열람실 수준 검색’을 안내하고 사용자가 선택하면새 요청한다. 반경 확대와 거리 중요도 변경도 새 요청이다. 도보ETA·도착 시점 빈자리 확률을 제공하지 않는다.


**저장된 추천 응답 예시(합성 자료):** 아래 값은 두 취향의 중요도가 각각 1인 내부 모델 예시와 짝을 이룬다. 위 B10 요청의 중요도 override를 그대로 실행한 결과라는 뜻은 아니다. 대표 공간의 환경값과 실제 현황은 아직 수집된 자료가 아니다.

```json
{
  "data": {
    "recommendationId": "DEMO-REC-LIB-1",
    "createdAt": "2026-11-14T17:30:00+09:00",
    "expiresAt": "2026-11-14T17:31:00+09:00",
    "profileRevision": 3,
    "result": {
      "requestId": "req-demo-libraries",
      "engineVersion": "seatrec-2.0.0",
      "featureRegistryVersion": "features-2",
      "datasetVersion": "DEMO-DATA-v1",
      "evaluatedAt": "2026-11-14T17:30:00+09:00",
      "executionMode": "live",
      "outcome": "ranked",
      "results": [
        {
          "rank": 1,
          "targetType": "library",
          "targetId": "DEMO-LIB-B",
          "libraryId": "DEMO-LIB-B",
          "roomId": "DEMO-ROOM-B",
          "seatId": null,
          "representativeCandidateId": "DEMO-ROOM-B",
          "mapAnchor": null,
          "rankingBasis": "preferences",
          "scores": {
            "featureScore": 1,
            "featureCoverage": 1,
            "choiceAffinity": 0,
            "personalizationScore": 0.9,
            "distanceUtility": 0.6471320473550803,
            "effectiveDistanceWeight": 0.35,
            "finalScore": 0.8114962165742782
          },
          "availability": {
            "effectiveState": "unknown",
            "scope": "none",
            "observedAt": null,
            "expiresAt": null
          },
          "availabilityVerified": false,
          "distance": {
            "meters": 1764.339763,
            "metric": "straightLine"
          },
          "reasons": [
            {
              "code": "FEATURE_MATCH",
              "featureId": "laptopAllowed",
              "sourceId": "DEMO-SOURCE-RULE-1",
              "text": "노트북 사용이 허용된 열람실입니다."
            }
          ],
          "warnings": [
            "AVAILABILITY_UNKNOWN",
            "STRAIGHT_LINE_DISTANCE_ONLY"
          ],
          "previewOnly": false
        },
        {
          "rank": 2,
          "targetType": "library",
          "targetId": "DEMO-LIB-A",
          "libraryId": "DEMO-LIB-A",
          "roomId": "DEMO-ROOM-A",
          "seatId": null,
          "representativeCandidateId": "DEMO-ROOM-A",
          "mapAnchor": null,
          "rankingBasis": "preferences",
          "scores": {
            "featureScore": 0.5,
            "featureCoverage": 1,
            "choiceAffinity": 0,
            "personalizationScore": 0.45,
            "distanceUtility": 0.8235660234284482,
            "effectiveDistanceWeight": 0.35,
            "finalScore": 0.5807481081999568
          },
          "availability": {
            "effectiveState": "unknown",
            "scope": "none",
            "observedAt": null,
            "expiresAt": null
          },
          "availabilityVerified": false,
          "distance": {
            "meters": 882.169883,
            "metric": "straightLine"
          },
          "reasons": [
            {
              "code": "FEATURE_MATCH",
              "featureId": "laptopAllowed",
              "sourceId": "DEMO-SOURCE-RULE-1",
              "text": "노트북 사용이 허용된 열람실입니다."
            }
          ],
          "warnings": [
            "AVAILABILITY_UNKNOWN",
            "STRAIGHT_LINE_DISTANCE_ONLY"
          ],
          "previewOnly": false
        }
      ],
      "excluded": [],
      "warnings": [],
      "statistics": {
        "inputCandidates": 2,
        "eligibleCandidates": 2,
        "returnedTargets": 2
      }
    }
  },
  "meta": {
    "requestId": "req-demo-libraries"
  }
}
```

### B12. `POST /api/v1/users/me/preference-analyses` — 자연어 취향 분석 결과 연동

**기능·시점:** 팀원의 카카오톡·자연어 분석 결과를 내 프로필의 사전값으로 등록한다. 사용자가 대화 분석에 동의하고 결과가 도착했을 때 호출한다(분석 서비스가 서버 간 호출로 대신 보낼 수 있다).

**입력:** `{expectedRevision,analysis}`. analysis는 분석기 원본 형식이다. 백엔드는 participant_id를 내부 가명 ID로 바꾸고 원문 대화·실명을 저장하지 않는다.

**처리:** M09를 `currentProfile`과 함께 호출하고, `mergedProfile`을 CAS 저장한다. 분석 결과 원본은 감사용으로 버전별 보관하며(재분석 시 새 버전), 프로필에는 변환 결과만 반영한다. `requirements`의 노트북 필수는 사용자 확인 후에만 저장한다.

**출력:** `{profileRevision,preferences,unmapped,warnings,analysisVersion}`.

**예외·제약:** 동의 없는 분석 결과는 403, 형식 오류는 422, revision 충돌은 409다. 분석기 경고(허용 외 값 등)는 실패가 아니라 warnings로 반환한다.

### B13. `GET /api/v1/users/me/preferences/insights` — 이력에서 찾은 내 취향 보기

**기능·시점:** 설정·마이페이지에서 학습된 취향과 근거를 보여주고, 사용자가 항목을 잠그거나 지울 수 있게 한다.

**출력:** `{revision,modelVersion,learnedAt,items:[{featureId,target,importance,source,uncertainty,evidenceCount,locked,evidence:{observations,chosenRate,availableRate},text}]}`. text 예: “최근 18번 중 콘센트 자리를 16번 고르셨어요(그때 빈자리 중 콘센트 자리는 41%).”

**예외·제약:** 근거 관측이 3회 미만인 학습 항목은 “아직 파악 중”으로 표시한다. 잠금·삭제는 B06(직접 설정, locked=true)으로 저장하며 이후 학습이 바꾸지 않는다. 학습 결과를 성격·건강 등으로 해석한 문구를 만들지 않는다.

### B11. `GET /api/v1/rooms/{roomId}/seats` — 좌석 좌표 목록 조회

**기능:** 열람실의 좌석번호와 좌표를 JSON 목록으로 반환한다. 프론트는 이 목록으로 좌석 버튼을 그리거나, 모델 추천 결과의seatId를 같은 좌표에 연결한다. B03이 배경·시설을 포함한 지도 레이어를 제공하는 API라면, B11은 개별 좌석 좌표를 페이지 단위로 읽는 API다. 모델이 좌표를 새로 추정하는 API가 아니다.

**호출 시점:** 열람실 좌석 화면을 처음 열 때, 지도·데이터 버전 변경 후, 관리자가 추출 결과를 검수할 때 호출한다.

**요청:** 경로의roomId와 다음 query를 사용한다.

| query | 형식·기본값 | 의미 |
|---|---|---|
| `view` | `live` 또는 `preview`, 기본live | live는 검증된 좌석만, preview는 명시적 권한·서비스 설정 아래 미검수 자료도 조회 |
| `limit` | 정수1~500, 기본100 | 페이지 최대 좌석 수 |
| `cursor` | 선택적 불투명 문자열 | roomId·view·datasetVersion·mapVersion에 묶인 다음 페이지 위치 |

**응답:** data에는 roomId, datasetVersion, map, view, items, total, nextCursor가 있다. total은 현재view에서 조회 가능한 좌석 수다. map은mapId·mapVersion·표시 프레임 가로세로와pixel단위를 명시한다. items에는 내부seatId, 외부externalSeatId(null가능), label, pixel/normalized기하정보, verificationStatus, selectable이 있다.

아래는 **제공받은 성남중앙 A구역 캡처에서 추출한 A93 한 건**을 API 형식으로 표현한 preview 예시다. 기관·방·지도ID와version은 앞으로 백엔드가 등록할 내부 예시ID이며, 좌표 숫자는 실제 추출 파일과 일치한다. 전체169건 중 한 건만 표시했으며nextCursor문자열은 실제 발급된 값이 아닌 형식 예시다.

```json
{
  "data": {
    "roomId": "PREVIEW-SN-CT-A",
    "datasetVersion": "preview-sn-ct-a-20261114",
    "map": {
      "mapId": "PREVIEW-SN-CT-A-MAP",
      "mapVersion": "preview-20261114-1",
      "width": 3772,
      "height": 1786,
      "coordinateUnit": "pixel",
      "origin": "topLeft"
    },
    "view": "preview",
    "items": [
      {
        "seatId": "PREVIEW-SN-CT-A-G001",
        "externalSeatId": null,
        "label": "A93",
        "geometry": {
          "pixel": {"x": 2112, "y": 117, "width": 66, "height": 56},
          "normalized": {"x": 0.55991516, "y": 0.06550952, "width": 0.01749735, "height": 0.03135498}
        },
        "verificationStatus": "unverified",
        "selectable": false
      }
    ],
    "total": 169,
    "nextCursor": "opaque-preview-cursor-example"
  },
  "meta": {"requestId": "req-preview-seat-list"}
}
```

**좌표 해석:** pixel은 이 응답의 지도 표시 프레임 내 좌상단 기준 UI 픽셀이다. 원본 캡처 전체 좌표와 다르며 물리 미터 거리가 아니다. x/y는 각각 0~width/height 범위 안이고, x+가로와 y+세로가 프레임을 벗어나지 않는다. normalized는 해당 pixel 값을 map.width/map.height로 나눈 값이다. 프론트는 좌표를 다른 이미지 크기나 버전에 그대로 붙이지 않는다.

**선택 가능 여부:** preview는 항상 selectable=false다. live의 selectable=true도 식별·지도 검수가 끝나 서비스 내 선택 대상으로 사용할 수 있다는 뜻이지, 지금 빈자리라는 뜻이 아니다. 실제 선택 시 B09에서 현황·필수 조건·만료를 다시 검사한다. 현재 11관 2,111개 좌석은 모두 preview 자료(model_candidate)이며 검증된 운영 좌석은 0개다. live 조회는 200과 빈 items, total=0을 반환한다.

**오류·제약:** 미존재 roomId는 404, preview 권한 없음은 403, limit/view/cursor 형식 오류는 422다. cursor가 가리키는 datasetVersion/mapVersion이 바뀌면 409/CURSOR_VERSION_CHANGED로 첫 페이지부터 다시 조회하게 한다. 다음 페이지가 없으면 nextCursor=null이다. 좌표가 없는 좌석에 임의 위치를 생성하지 않으며 해당 좌표 목록에 넣지 않는다. 이용 상태·성별·개인 이용시간은 이 정적 응답에 포함하지 않고 B04 현황과 분리한다.

## 10. 구현 순서와 인수 기준

### 10.1 구현 순서

1. M01~M06과 M09를 규칙 엔진으로 구현하고 **simulation 모드**(MongoDB `library.synth_*` 합성 환경·합성 현황)로 전 과정을 연결한다. 빈 후보·미상 특성·필수조건 미상 처리를 먼저 검증한다.
2. 합성 데이터 v2(실시간 점유 시뮬레이션, 사용자별 장기 방문 이력, 선택 시점 전체 빈자리 스냅샷)를 만들고, 이벤트·스냅샷 저장(8.1절)과 선택 관측(5.7절) 구성을 구현한다.
3. M10 학습을 구현하고 6.6.5절 오프라인 평가를 simulation에서 통과시킨다(파이프라인 검증).
4. 실제 기관: 11관 좌석 후보를 전수 검수해 live 좌석으로 승격하고 위경도를 연결한다. 시설·측정 자료가 확보된 피처만 live에서 활성화한다. 합성값으로 live 특성을 채우지 않는다.
5. 허용된 좌석 단위 현황 공급원을 연결하고 TTL·실시간 특성 계산을 검증한다(P4).
6. 실사용 선택 로그가 쌓이면 모집단 파라미터를 실제 로그로 재학습하고 6.6.5절을 실데이터로 다시 통과한 뒤 P5를 켠다. 탐색은 그 뒤에 낮은 비율로 켠다.
7. M07/B10의 위치·반경·대표 공간·거리 결합을 구현한다.

### 10.2 필수 인수 시나리오

- 후보0개는200/noCandidates, 전부탈락은200/noEligibleCandidates다.
- room잔여석20개와seat후보100개가 있어도 특정seat상태는unknown이다.
- 만료 현황은unknown이며confirmedOnly에서 제외된다.
- 필수조건 값null은 제외, soft선호 null은coverage로 드러난다.
- 모든 특성 미상·이력 없음은 비개인화 또는 거리 기반이라고 명시한다.
- 미검수 좌석 후보(현재 11관 2,111개)를live로 보내면 운영 부적격이다. preview·simulation 결과에는previewOnly=true가 있다.
- 같은 위치의 거리는0m다. 반경 밖·기관 좌표 없음은 제외한다. 동명 기관은 합쳐지지 않는다.
- 거리 비중 변경에 따라 공개 식으로 순위가 달라진다. 도보ETA·빈자리 확률은 응답에 없다.
- 동일eventId·Idempotency-Key는 한 번만 저장한다. stale revision은409다.
- 단순 선택은 choiceAffinity와 선택 관측 생성, 별점만 제출은 피처 변경 없음, 명시적 교정은M08계산이다.
- 공급원 실패는미상 또는503이며 가상 기관·모의 좌석값으로 대체하지 않는다.
- M09: 표 어휘(`yes`)와 예시 어휘(`uses_laptop`)가 같은 결과를 낸다. `avoid_direct_airflow`는 airflow target 0의 direct 항목, `height_adjustable`은 unmapped로 반환된다. 허용 외 값은 경고와 unknown이다.
- M10: 빈자리가 창가뿐인 관측만 있는 사용자는 창가 importance가 사전값에서 거의 바뀌지 않는다. 빈자리가 다양한데 매번 콘센트 좌석을 고른 사용자는 outletAvailable importance가 오르고 uncertainty가 내려간다.
- M10: locked 항목은 학습 후에도 그대로다. 관측 3개 미만은 insufficientData다. simulation 관측을 live 요청에 넣으면 422다.
- 실시간 특성: 반경 안 좌석 상태가 unknown이면 neighborOccupancy=null이며 0으로 계산하지 않는다.
- simulation 결과는 항상 previewOnly=true와 SIMULATION_DATA 경고가 있고 B09에서 실제 선택으로 저장되지 않는다.

노출 대비 선택률, 선택 시점 빈자리 대비 선택 좌석 순위(Hit@5·NDCG@5), 이용 자기신고율, 체류시간, 만족도, 필수조건 위반률, 정보 미상 비율, 자료·현황 신선도를 나눠 평가한다. 클릭률만으로 실제 좌석 추천 품질을 판정하지 않는다. 혼잡·빈자리 예측은 이 버전의 범위가 아니다.

## 11. 근거와 후속 데이터 작업

- [데이터 구축 인계서](HANDOFF.md): 도서관별 원본·구축 방법·검수 경계.
- [도서관별 타당성](../catalog/library_feasibility.json), [원본 출처·해시](../catalog/source_manifest.json).
- [전국 공공도서관 실시간 API15142580](https://www.data.go.kr/data/15142580/openapi.do): 기관·열람실 집계. 실제 인증 응답과 기관 가용성은 추가 검증 필요.
- [전국도서관표준데이터15013109](https://www.data.go.kr/data/15013109/standard.do): 위치·운영시간 보조 자료. 좌석 환경 자료가 아니다.
- [용인 좌석정보15154878](https://www.data.go.kr/data/15154878/fileData.do), [성남 열람실정보15032433](https://www.data.go.kr/data/15032433/fileData.do): 실제 공개 필드에 따른 기능 범위.
- [합성 데이터 명세](../libraries/docs/SYNTHETIC_DATA.md), [데이터셋 구조](../libraries/README.md), [27관 구축 현황](../BUILD_STATUS.md).
- 자연어 분석 출력 형식: 저장소 `kakao_analysis/schema/kakaotalk_analysis.md`, `kakao_analysis/schema/kakaotalk_schema.json`. M09 변환 참조 구현: 저장소 `recommender/preference_adapter.py`.
- 조건부 로짓·혼합 로짓: 선택 집합 대비 선택을 관측하는 표준 이산선택 모형. 본 문서의 정책 상수(관측 가중치·반감기·활성화 기준)는 우리 설계값이며 실증 성능 주장이 아니다.
- [Google 내용 기반 추천 설명](https://developers.google.com/machine-learning/recommendation/content-based/basics): 사용자 명시 취향·행동과 아이템 특성을 연결하는 접근. 본 문서의 구체 계수·정규화 상수는 우리 정책이며 외부 자료의 실증 성능 주장이 아니다.

시설 추천을 활성화하려면 화장실·출입구·복도는 접근점과 경로 그래프, 에어컨은 기계도·현장 위치와 별도 바람 관측, 콘센트는 전기도·접근성, 책상은 실측 치수, 조명은 시간대별 측정, 경치는 앉은 높이의 시야 평가가 필요하다. AI가 도면에서 읽지 못한 항목을 사실처럼 생성하지 않는다.

## 13. 계약 파일과 검증 범위

- [내부 모델 OpenAPI](be-model/MODEL_API.openapi.json): M01~M10의 10개 연산, 필드 형식, 요청·응답 예시.
- [백엔드 좌석 OpenAPI](be-model/BACKEND_SEATS_API.openapi.json): B11 좌표 목록과 B04 좌석 현황의 경로·query·응답·오류. 나머지 백엔드 API는 본 문서의 상세 설계가 계약이다.
- [계약 검증 결과](../validation/API_SPEC_VALIDATION.json): OpenAPI 3.1 구조, 77개 요청·응답 예시의 JSON Schema, 거절돼야 할 입력 10종, simulation 좌석 점수·M09 변환·M10 근거 요약 재계산, Markdown 예시·링크. 재실행: `python3 data/scripts/validate_api_spec.py`(openapi-spec-validator 설치 시 구조 검증 포함).

B11의 selectable은 정적 식별·지도 검수 및 서비스 권한상 선택 화면에 노출할 수 있다는 뜻이다. 빈자리·예약 성공을 의미하지 않는다. 실제 새 선택은 B09에서 해당 요청의 현황 정책과 유효기간을 다시 검사한다. preview에서는 항상 false다.

명세와 예시 검증은 서버 구현·외부 API 연동·실측 추천 품질·성능 시험을 대신하지 않는다. 모든 API는 구현 전 설계 상태이며, 현재 데이터의 운영 적격 조건을 통과한 기관부터 활성화한다.
