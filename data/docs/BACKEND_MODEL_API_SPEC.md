# 실시간 개인별 공공도서관·열람실·좌석 추천 API 명세서

- 버전: **1.0.0-design**
- 작성일: 2026-11-14
- 상태: **구현 전 계약 설계**. 추천시스템이나 API 서버가 이미 구현됐거나 정확도가 검증됐다는 뜻이 아니다.
- 기준 문서: 저장소 루트의 「백엔드 ↔ 모델 API 명세서 (v0.1 초안)」와 공공도서관 데이터 타당성 검토 결과.
- 대상: 학교도서관을 제외한 공공도서관. 현재 위치·거리·개인 취향에 따른 도서관, 열람실, 검증된 개별 좌석 추천.
- 내부 모델 서버의 기계 판독 계약: [MODEL_API.openapi.json](MODEL_API.openapi.json).

## 1. 핵심 판단: 무엇을 구현할 수 있는가

### 1.1 이번 버전의 ‘실시간 개인화’

사용자가 현재 위치나 오늘의 조건을 바꾸거나, 새 현황과 선택·피드백이 저장되면 그 시점의 입력으로 추천 순위를 다시 계산한다. **앞으로 몇 번 좌석이 언제 비는지를 예측하는 기능은 포함하지 않는다.** 현황이 실제로 연동된 기관에서만 관측 시각과 유효기간을 표시한다.

첫 추천 엔진은 학습 데이터 없이 구현할 수 있는 **내용 기반 점수 계산**으로 시작한다. 사용자 설문·직접 설정, 확인된 공간 특성, 해당 사용자의 선택 이력을 이용한다. 아직 없는 학습 모델의 정확도·만족 확률을 API 응답으로 만들지 않는다.

### 1.2 현재 확인한 데이터와 기능의 경계

| 자료 | 확인한 내용 | 활용 가능한 범위 | 보장할 수 없는 범위 |
|---|---|---|---|
| 성남중앙 A구역 사용자 캡처 | 번호·사각형 169개 추출. 전수 육안 검수 전 | 미검수 표시가 있는 좌석도 미리보기 | 운영용 번호·좌표 정확성, 현재 빈자리, 시설 특성 |
| 용인 공식 웹 | 사각형 위치 후보 1,318개. 공식 번호·ID 전수 연결 미완료 | 번호 연결과 지도 정합 작업 | 검수 완료 개별 좌석 추천 |
| 성남 공개 CSV | 13관 40개 열람실. 명칭에 일부 노트북·칸막이·오픈석 정보 | 확인된 명칭·규정에 따른 열람실 추천 | 특정 좌석에 해당 특성이 적용된다는 가정 |
| 용인 공개 좌석 CSV | 11관 2,240개 좌석의 ID·방 ID·표시번호 | 좌석 마스터와 ID 매핑 | 좌표, 개별 점유 상태, 환경 속성 |
| 전국 실시간 API 문서 | 도서관·열람실 단위 위치·운영·잔여석 필드 | 인증 검증 후 기관·열람실 현황 반영 | 특정 N번 좌석의 빈자리·퇴실시간 |
| 참고 GitHub 목록 | 91개 기관·222개 열람실 링크 후보 | 기관별 원본 조사 확대 | 최신 지원기관 전수 목록, 좌표·시설 데이터 |

**현재 저장소만으로 ‘지금 빈 개별 좌석 + 모든 환경 취향’ 서비스를 운영할 수 있는 상태는 아니다.** API 개발은 가능하지만, 실제 기관별 데이터 검수·현재성·연동이 활성화 조건이다. 주변 도서관 추천에 사용할 기관별 위도·경도도 별도로 연결하고 검증해야 한다.

### 1.3 단계별 활성화 조건

| 단계 | 제공 기능 | 활성화 조건 |
|---|---|---|
| P0 | 미검수 좌석도와 추천 계산 미리보기 | `executionMode=preview`. 실제 선택·이용 신고 불가 |
| P1 | 취향에 맞는 열람실 추천 | 내부 기관/열람실 ID와 특성 근거 검증 |
| P2 | 현재 위치·거리·취향으로 갈 도서관 추천 | 검증된 기관 좌표와 대표 열람실·특성 |
| P3 | 검증된 개별 좌석의 취향 추천 | 내부 좌석 ID·표시번호·지도 위치 전수 검수 |
| P4 | 지금 확인된 빈 개별 좌석만 추천 | **좌석 단위** 현황 공급원과 신선도 검증 |

P3에서 좌석 현황이 없으면 ‘취향에 맞는 자리, 이용 가능 여부 미확인’으로만 제공한다. 열람실 잔여석 집계만으로 P4를 활성화하지 않는다.

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
| `explore`, `exploit` | v1에서 제거 | 밴딧과 선택확률 기반 학습을 아직 구현·평가하지 않음 |
| 임의 `comfort` 점수 | 제거 | 관측 의미가 불명확. 책상·조도 등 정의된 특성으로 분리 |
| API 미수신을 전석 공석으로 처리 | 금지 | 미수신·만료는 `unknown`, 결측은 `null` |
| 주변 도서관 추천 | 추가 | 현재 위치·직선거리·대표 공간의 취향 점수를 결합 |
| 기능·피처 조회 | 추가 | 엔진 기능과 기관별 데이터 준비 상태를 분리 |
| 지도·데이터 버전 | 추가 | 서로 다른 프레임·스냅샷을 잘못 결합하지 않도록 함 |

이 버전은 공식 예약·좌석배정, QR 검증, 도보 길찾기, 장기 점유 점수, 빈자리 예측을 제공하지 않는다. 미래 자료를 확보하면 별도 버전으로 설계한다.

## 3. 백엔드와 모델 서버의 책임

### 3.1 백엔드 API 서버

백엔드는 인증, 기관·열람실·좌석·지도·시설 DB, 원본 정제와 검수 상태, 현황 수집·캐시, 사용자 취향과 이벤트, 추천 결과 저장을 담당한다. NestJS 등 구현 프레임워크는 선택 사항이다.

모델에는 백엔드가 관리하는 내부 ID와 검증된 스냅샷을 전달한다. 클라이언트가 보낸 좌석 특성·검수 여부를 그대로 신뢰하지 않는다. 추천 결과를 예약이나 빈자리 확보로 표현하지 않는다.

### 3.2 모델 API 서버

모델은 **요청별 무상태 계산 서비스**다. 계약 검사, 필수 조건 필터, 취향 점수·선택 이력·직선거리 계산, 순위·근거·제외 사유, 명시적 취향 교정값을 반환한다.

모델은 사용자 DB를 변경하거나 외부 도서관·지도·공공 API를 직접 호출하지 않는다. ‘모델 서버’라는 명칭이 있어도 첫 버전은 설명 가능한 규칙 엔진으로 구현할 수 있다.

### 3.3 호출 흐름

```text
현재 위치·오늘 조건·취향 입력
 → 백엔드: 기관/공간 마스터, 현황 캐시, 프로필, 선택 이력 조회
 → 모델: 적격성·현황·필수 조건 필터 → 점수 → 순위·근거
 → 백엔드: recommendationId 발급, 입력 스냅샷·결과 저장
 → 프론트: 추천 도서관·대표 공간·거리·지도·관측 시각 표시
 → 백엔드: 실제 노출·선택·이용 신고·평가 이벤트 기록
 → 다음 추천에 개인 선택 이력 반영
 → 명시적 특성 교정이 있으면 모델 계산 후 프로필 CAS 저장
```

도서관을 선택하면 열람실 추천, 열람실을 선택하면 검증된 좌석 추천을 추가 호출한다. 좌석 데이터가 없는 기관은 열람실 수준까지만 제공하며 결과 종류를 몰래 바꾸지 않는다.

## 4. 공통 HTTP·ID·오류 규칙

### 4.1 요청과 버전

- 내부 모델 주소 예: `http://model-api:8000`. 운영에서는 백엔드만 접근하고 서비스 인증을 사용한다.
- 모델 경로는 `/v1/...`, 사용자용 백엔드는 `/api/v1/...`다.
- JSON, camelCase. 숫자는 유한한 값만 허용한다. 알 수 없는 필드·피처 ID·열거값은 422다.
- 시간은 오프셋을 포함한 RFC3339다. 예: `2026-11-14T17:30:00+09:00`. 오프셋 없는 시각은 거절한다.
- 내부 요청은 `requestId`와 백엔드가 고정한 `evaluatedAt`을 사용한다. 같은 입력·버전이면 같은 점수와 순위를 반환한다.
- `datasetVersion`, `featureRegistryVersion`, `engineVersion`을 구분한다. 서로 다른 스냅샷을 섞지 않는다.
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
  "featureRegistryVersion":"features-1",
  "preferences":[
    {"featureId":"laptopAllowed","target":1,"importance":1},
    {"featureId":"partition","target":1,"importance":0.8},
    {"featureId":"brightness","target":0.5,"importance":0.3}
  ]
}
```

`target`은 원하는 수준, `importance`는 0~1 중요도다. 이진 특성의 target은 0 또는 1, 연속 특성은 0~1이다. 중요도 0은 점수에서 제외한다. 기존 초안의 음수·양수 의미가 모호한 Weights를 교체한다.

취향이 없으면 빈 배열이다. 페르소나나 타인의 행동으로 취향을 학습했다고 표현하지 않는다. 오늘의 `preferenceOverrides`는 같은 피처의 값을 요청에서만 덮어쓰며 저장 프로필을 바꾸지 않는다. ‘노트북 필수’는 선호뿐 아니라 `requirements`에 넣는다.

### 5.2 피처 레지스트리 `features-1`

이 목록은 엔진이 해석할 수 있는 특성 사전이며 **현재 기관에 그 데이터가 있다는 뜻은 아니다.** 출처·측정 단위·적용 범위가 검증된 값만 운영 추천에 사용한다.

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

상수는 비교 가능성을 위한 **우리 v1 설계 정책**이며 실증된 쾌적성 기준이 아니다. 다른 기관의 픽셀 거리나 다른 척도의 후기를 같은 값으로 섞지 않는다. 정규화가 바뀌면 레지스트리 버전을 변경한다.

사용자는 자료가 없는 취향도 저장할 수 있다. 단, 해당 기관 추천에 반영되지 않은 항목을 명시해야 한다.

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
  "choiceAffinity":0
}
```

room 후보는 `candidateId=roomId`, seatId=null이다. seat 후보는 `candidateId=seatId`, roomId 필수다. `operatingState`는 `open|closed|unknown`이고 closed는 제외한다. unknown을 개관 확인으로 표현하지 않는다.

`identityVerified`는 실제 공간·표시번호와 내부 ID의 대응, `geometryVerified`는 지도 프레임과 위치 확인이다. live에서 room은 검증된 식별자가 필요하고 seat는 식별자·지도 위치·mapAnchor가 모두 필요하다.

`choiceAffinity`는 해당 사용자의 선택 이력을 백엔드가 계산한0~1 값이다. 미제공은0이며 다른 사용자 인기점수나 빈자리 확률이 아니다.

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

현재 정적 좌석 파일에는 현황이 없다. 연동 시 백엔드의 별도 현황 캐시에서 결합하고 정적 CSV를 덮어쓰지 않는다.

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

### 6.2 취향·이력 점수

활성 취향은 importance>0인 항목이다. 확인된 값 x와 목표 t의 만족도는 `u=1-|t-x|`다.

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

선택 횟수는 공식 예약 성공이나 실제 착석 확률이 아니다. 단순 선택 한 번으로 밝기·콘센트 등의 원인 취향을 역추론하지 않는다.

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

**이후 모든 `DEMO-*` ID와 예시 수치는 계약 검증용 합성 예시다. 실제 기관의 환경·잔여석 자료로 저장하지 않는다.**

### M01. `GET /health` — 모델 서버 생존 확인

**기능과 호출 시점:** 컨테이너·로드밸런서가 프로세스의 HTTP 처리 가능 여부를 검사한다. 기관 데이터가 충분하거나 공공 API가 연결됐는지를 뜻하지 않는다.

**입력:** 없음.

**출력:** `status`, `serviceVersion`, `engineVersion`.

```json
{"status":"ok","serviceVersion":"1.0.0","engineVersion":"rule-v1.0.0"}
```

**예외·제약:** 프로세스가 정상 응답할 수 없으면503이다. engineVersion은 v1의 규칙 엔진 버전이며 학습 모델이 있다는 뜻이 아니다.

### M02. `GET /v1/capabilities` — 연산 지원 여부 조회

**기능과 호출 시점:** 백엔드 시작·배포 시 모델이 지원하는 추천 단위, 모드, 거리 종류, 입력 한도를 읽는다. 미지원 연산을 프론트에 노출하지 않도록 한다.

**입력:** 없음.

**출력 예시:**

```json
{
  "contractVersion":"1.0.0",
  "engineVersion":"rule-v1.0.0",
  "featureRegistryVersion":"features-1",
  "trainedModel":false,
  "supportedTargets":["library","room","seat"],
  "supportedModes":["live","preview"],
  "distanceMetric":"straightLine",
  "maxCandidates":5000,
  "maxTopK":20,
  "occupancyPrediction":false,
  "seatReservation":false
}
```

**예외·제약:** 엔진의 seat 추천 연산 지원과 기관별 좌석 데이터 준비 상태는 다르다. 후자는 백엔드 카탈로그에서 관리한다. 구현하지 않은 연산은 지원한다고 표시하지 않는다.

### M03. `GET /v1/features` — 취향 항목 사전 조회

**기능과 호출 시점:** 백엔드와 프론트가 같은 피처 ID·이진/연속 구분·정규화 기준으로 설문과 입력을 만든다. 설정 화면 초기화와 계약 버전 점검에 사용한다.

**입력:** 없음.

**출력:** `{featureRegistryVersion,features:[{featureId,label,type,normalization,requiresData}]}`. 5.2절 목록을 반환한다.

**예외·제약:** 기관에 해당 피처 자료가 없어도 저장 가능한 취향은 존재할 수 있다. UI는 ‘저장 가능하지만 이 기관 추천에는 미반영’을 구분한다. 피처 정규화 변경은 버전을 변경한다.

### M04. `POST /v1/preferences/initialize` — 초기 취향 계산

**기능과 호출 시점:** 가입·설문 완료 직후 사용자가 직접 답한 취향을 초기 프로필로 만든다. 설문을 건너뛰면 빈 취향으로 시작한다.

**요청:**

```json
{
  "requestId":"req-demo-init",
  "featureRegistryVersion":"features-1",
  "answers":[{"featureId":"laptopAllowed","target":1,"importance":1}]
}
```

**출력:** `preferences`, `engineVersion`, `featureRegistryVersion`. DB revision과 사용자 ID는 백엔드가 부여한다. 입력 답변을 계약에 맞게 검사·정리한 결정적 결과이며 숨은 성격 추정은 하지 않는다.

**예외·제약:** 피처 중복·미등록 ID·이진 target=0.5·범위 밖 중요도는422다. 사용자 DB 저장은 이 API의 책임이 아니다.

### 공통 추천 요청

```json
{
  "requestId":"req-demo-room",
  "evaluatedAt":"2026-11-14T17:30:00+09:00",
  "datasetVersion":"DEMO-DATA-v1",
  "featureRegistryVersion":"features-1",
  "executionMode":"live",
  "profile":{"revision":0,"featureRegistryVersion":"features-1","preferences":[]},
  "preferenceOverrides":[],
  "requirements":[],
  "excludedCandidateIds":[],
  "options":{"topK":5,"availabilityPolicy":"allowUnknown","maxAvailabilityAgeSeconds":120,"minFeatureCoverage":0},
  "candidates":[]
}
```

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

**처리:** 현재 성남169개·용인후보1,318개는 검수 없이 운영 후보로 넣으면 제외돼야 한다. preview는 화면 검토에만 사용한다. roomAggregate만 있으면 특정 좌석 상태는unknown이다.

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
  "engineVersion":"rule-v1.0.0",
  "featureRegistryVersion":"features-1",
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
| `previewOnly` | true이면 운영 선택·이용 신고 불가 |

outcome은 `ranked|noCandidates|noEligibleCandidates`다. 점유·예약·차단 후보는 결과에 남기지 않으므로 결과의 effectiveState는available 또는unknown이다.

대표 제외 코드: CLOSED, EXCLUDED_BY_USER, UNVERIFIED_IDENTITY, UNVERIFIED_GEOMETRY, KNOWN_UNAVAILABLE, AVAILABILITY_UNKNOWN, HARD_REQUIREMENT_UNKNOWN, HARD_REQUIREMENT_NOT_MET, INSUFFICIENT_FEATURE_COVERAGE, MISSING_LIBRARY_LOCATION, OUTSIDE_RADIUS.

대표 경고 코드: AVAILABILITY_UNKNOWN, OPERATING_STATE_UNKNOWN, FEATURES_PARTIALLY_UNKNOWN, INSUFFICIENT_PREFERENCE_DATA, PREVIEW_ONLY, STRAIGHT_LINE_DISTANCE_ONLY.

### M08. `POST /v1/preferences/update` — 명시적 취향 피드백 보정

**기능:** ‘조명을 더 어둡게 원함’, ‘칸막이가 더 중요함’처럼 사용자가 직접 교정한 특성을 현재 저장 프로필에 반영할 새 값을 계산한다. DB는 변경하지 않는다.

**호출 시점:** 이용 후 평가에서 구체적인 특성 교정이 제출됐을 때. 단순 선택이나 별점만으로 호출하지 않아도 된다.

**요청:** requestId, eventId, 현재profile, corrections 배열. 교정 항목은 `{featureId,target,importance}`다. 백엔드가 이벤트 소유권·중복 여부와 최신 프로필을 확인한다.

**계산:** 기존 연속 항목은 target과importance 각각 `0.8*old+0.2*correction`으로 보정한다. 이진 target은 교정값0/1을 직접 적용하여0.2 같은 잘못된 이진 목표를 만들지 않는다. 새 항목은 교정값으로 추가한다. 학습된 최적 계수가 아닌 v1 고정 정책이다.

**출력:** baseRevision, updatedPreferences, changed, changes[]. 변경이 있으면 백엔드가 revision CAS 비교 후 한 번만 저장한다. 충돌 시 최신 프로필로 같은 명시적 교정 이벤트를 다시 계산한다. 모델은 새 revision을 발급하지 않는다.

**오류·제약:** 미등록 ID·중복 교정·버전 불일치는422다. 클릭·미선택·별점만으로 모든 피처를 변경하거나 오래된 추천 당시 프로필로 최신 취향을 덮어쓰지 않는다. 오늘의 일시 override를 영구 저장하지 않는다.

## 8. 실시간 갱신과 사용자 이벤트 정합성

### 8.1 현황과 캐시

백엔드가 공급원별 현황 캐시를 갱신하고 요청 시 스냅샷을 결합한다. 프론트는 모델·공공 API를 직접 호출하지 않는다. 공급원이 허용한 주기보다 빠르게 수집하지 않는다. v1은 SSE/WebSocket 없이 주기적인 백엔드 조회와 명시적 재추천으로 구현할 수 있다.

사용자 취향·선택 변경은 해당 사용자 추천 캐시, 자료·현황 버전 변경은 해당 공간 캐시를 무효화한다. 키에는 프로필revision, 검색조건, 정적버전, 현황스냅샷ID를 포함한다. 정밀 출발 좌표를 일반 로그나 장기 캐시에 보존하지 않는 것을 기본으로 한다.

백엔드 추천의expiresAt은 생성 후60초와 실제 사용한 신선한 현황 만료시각 중 이른 값이다. 선택 직전에 상태·지도버전·필수 조건을 재검증한다. 모델 계산과 실제 선택 사이에 빈자리가 달라질 수 있다.

### 8.2 이벤트의 의미

| 이벤트 | 관측한 사실 | 반영 |
|---|---|---|
| recommendationExposed | 실제 노출된 결과·순서 | 평가 분모. 미노출 후보를 음성라벨로 사용하지 않음 |
| candidateSelected | 서비스에서 선택한 공간 | 개인 choiceAffinity에 반영 |
| useConfirmed | 사용자의 이용 시작 자기신고 | 공식 배정·실제 착석 검증과 구분 |
| ratingSubmitted | 만족도·명시적 특성 교정 | 평가 저장. 교정이 있을 때만M08 계산 |
| candidateRejected | 명시적 회피와 이유 | 사용자가 요청한 제외 목록 갱신 |

선택·신고·평가 시각은 서비스 행동 DB에 저장한다. 외부 도서관의 개인 점유시간 수집과 다르며 정적 좌석 파일과 분리한다. 미선택은 만석·동선·화면 이탈 때문일 수 있으므로 곧바로 싫어요로 학습하지 않는다.

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

**출력:** `{roomId,roomAggregate:null|{totalSeats,remainingSeats,observedAt,expiresAt,sourceId},seatStates:[{seatId,state,observedAt,expiresAt,sourceId}],receivedAt}`.

**예외·제약:** 개별 좌석 연동이 없으면seatStates=[]다. 현황이 전혀 없어도200과집계null/빈배열로 응답한다. 수집 실패를 remainingSeats=totalSeats로 치환하지 않는다. 미래 예측을 반환하지 않는다.

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
| candidateRejected | targetId, reason, excludeFromFuture | 명시적으로 요청한 제외 여부 반영 |

**출력:** `{eventId,accepted:true,effect:storedOnly|historyUpdated|preferencesUpdated,profileRevision}`. 중복 이벤트는 같은 결과다.

**예외·제약:** 추천 외 선택도 정상 마스터의 실제 대상이어야 하며 클라이언트 피처는 받지 않는다. 도서관 선택은 특정 좌석 이용 확인이 아니다. useConfirmed/rating은room/seat추천에만 허용한다. preview는 선택·신고 불가다.

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
      "engineVersion": "rule-v1.0.0",
      "featureRegistryVersion": "features-1",
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

**선택 가능 여부:** preview는 항상 selectable=false다. live의 selectable=true도 식별·지도 검수가 끝나 서비스 내 선택 대상으로 사용할 수 있다는 뜻이지, 지금 빈자리라는 뜻이 아니다. 실제 선택 시 B09에서 현황·필수 조건·만료를 다시 검사한다. 현재 성남 169개는 preview 자료이며 검증된 운영 좌석은 0개다. live 조회는 200과 빈 items, total=0을 반환한다.

**오류·제약:** 미존재 roomId는 404, preview 권한 없음은 403, limit/view/cursor 형식 오류는 422다. cursor가 가리키는 datasetVersion/mapVersion이 바뀌면 409/CURSOR_VERSION_CHANGED로 첫 페이지부터 다시 조회하게 한다. 다음 페이지가 없으면 nextCursor=null이다. 좌표가 없는 좌석에 임의 위치를 생성하지 않으며 해당 좌표 목록에 넣지 않는다. 이용 상태·성별·개인 이용시간은 이 정적 응답에 포함하지 않고 B04 현황과 분리한다.

## 10. 구현 순서와 인수 기준

### 10.1 구현 순서

1. 공개 CSV를 내부 ID·검증된 특성·출처·검수 상태로 수입한다. 기관별 실제 위경도를 연결한다.
2. M01~M04와room점수 추천을 구현한다. 빈 후보·미상 특성·필수조건 미상 처리를 먼저 검증한다.
3. M07/B10의 위치·반경·대표 공간·거리 결합을 구현한다.
4. 성남169개를 전수 검수하고seat추천·지도에 연결한다. 용인 후보는 번호·프레임 대응 전 운영 노출하지 않는다.
5. 이벤트 저장·프로필CAS·멱등성을 구현한다. 선택이 중복 가산되지 않는지 확인한다.
6. 허용된 현황 공급원을 실제 연결하고TTL을 검증한다. 집계만 있는 기관이 특정 빈 좌석을 제공하지 않도록 한다.
7. 시설·측정 자료가 확보된 피처만 활성화한다. 자료 없는 값을 모의값으로 채워 시연하지 않는다.

### 10.2 필수 인수 시나리오

- 후보0개는200/noCandidates, 전부탈락은200/noEligibleCandidates다.
- room잔여석20개와seat후보100개가 있어도 특정seat상태는unknown이다.
- 만료 현황은unknown이며confirmedOnly에서 제외된다.
- 필수조건 값null은 제외, soft선호 null은coverage로 드러난다.
- 모든 특성 미상·이력 없음은 비개인화 또는 거리 기반이라고 명시한다.
- 미검수169개를live로 보내면 운영 부적격이다. preview결과에는previewOnly=true가 있다.
- 같은 위치의 거리는0m다. 반경 밖·기관 좌표 없음은 제외한다. 동명 기관은 합쳐지지 않는다.
- 거리 비중 변경에 따라 공개 식으로 순위가 달라진다. 도보ETA·빈자리 확률은 응답에 없다.
- 동일eventId·Idempotency-Key는 한 번만 저장한다. stale revision은409다.
- 단순 선택은 이력, 별점만 제출은 피처 변경 없음, 명시적 교정은M08계산이다.
- 공급원 실패는미상 또는503이며 가상 기관·모의 좌석값으로 대체하지 않는다.

노출 대비 선택률, 이용 자기신고율, 만족도, 필수조건 위반률, 정보 미상 비율, 자료·현황 신선도를 나눠 평가한다. 클릭률만으로 실제 좌석 추천 품질을 판정하지 않는다. 학습형 추천·밴딧·혼잡 예측은 이 로그와 평가체계를 확보한 뒤 별도 검토한다.

## 11. 근거와 후속 데이터 작업

- [데이터 구축 인계서](HANDOFF.md): 도서관별 원본·구축 방법·검수 경계.
- [도서관별 타당성](../catalog/library_feasibility.json), [원본 출처·해시](../catalog/source_manifest.json).
- [전국 공공도서관 실시간 API15142580](https://www.data.go.kr/data/15142580/openapi.do): 기관·열람실 집계. 실제 인증 응답과 기관 가용성은 추가 검증 필요.
- [전국도서관표준데이터15013109](https://www.data.go.kr/data/15013109/standard.do): 위치·운영시간 보조 자료. 좌석 환경 자료가 아니다.
- [용인 좌석정보15154878](https://www.data.go.kr/data/15154878/fileData.do), [성남 열람실정보15032433](https://www.data.go.kr/data/15032433/fileData.do): 실제 공개 필드에 따른 기능 범위.
- [Google 내용 기반 추천 설명](https://developers.google.com/machine-learning/recommendation/content-based/basics): 사용자 명시 취향·행동과 아이템 특성을 연결하는 접근. 본 문서의 구체 계수·정규화 상수는 우리 정책이며 외부 자료의 실증 성능 주장이 아니다.

시설 추천을 활성화하려면 화장실·출입구·복도는 접근점과 경로 그래프, 에어컨은 기계도·현장 위치와 별도 바람 관측, 콘센트는 전기도·접근성, 책상은 실측 치수, 조명은 시간대별 측정, 경치는 앉은 높이의 시야 평가가 필요하다. AI가 도면에서 읽지 못한 항목을 사실처럼 생성하지 않는다.

## 13. 계약 파일과 검증 범위

- [내부 모델 OpenAPI](MODEL_API.openapi.json): M01~M08의 8개 연산, 필드 형식, 요청·응답 예시.
- [백엔드 좌석 좌표 OpenAPI](BACKEND_SEATS_API.openapi.json): B11 좌표 목록의 경로·query·페이지네이션·응답·오류. 나머지 백엔드 API는 본 문서의 상세 설계가 계약이다.
- [계약 검증 결과](../validation/API_SPEC_VALIDATION.json): OpenAPI 규격, 62개 요청·응답 예시의 JSON Schema, 거리 결합 점수, 좌표 정규화 검산.

B11의 selectable은 정적 식별·지도 검수 및 서비스 권한상 선택 화면에 노출할 수 있다는 뜻이다. 빈자리·예약 성공을 의미하지 않는다. 실제 새 선택은 B09에서 해당 요청의 현황 정책과 유효기간을 다시 검사한다. preview에서는 항상 false다.

명세와 예시 검증은 서버 구현·외부 API 연동·실측 추천 품질·성능 시험을 대신하지 않는다. 모든 API는 구현 전 설계 상태이며, 현재 데이터의 운영 적격 조건을 통과한 기관부터 활성화한다.
