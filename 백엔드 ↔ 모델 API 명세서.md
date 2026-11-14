# 백엔드 ↔ 모델 API 명세서 (v0.1 초안)

# 백엔드 ↔ 모델 API 명세서 (v0.1 초안)

Nov 14, 2026 · @심지민

## 개요와 설계 원칙

백엔드(NestJS)가 모델 서버를 호출할 때 주고받는 요청과 응답만 정리했어요. 회의 전 초안이라 회의에서 정할 부분은 \*\*\[회의\]\*\*로 표시했어요.

- **모델 서버는 상태를 갖지 않음**: 필요한 데이터는 매 요청에 담겨 오고, 모델은 계산 결과만 반환.
- **연산은 모델이 담당**: 점수 계산, 추천, 추천 이유 생성, 가중치 업데이트, 빈자리 예측, 지박령 점수.

## 공통 규칙과 데이터 형식

| 항목 | 규칙 |
| --- | --- |
| Base URL | `http://{모델서버}:{포트}` (예: `http://localhost:8000`) |
| 형식 | JSON, 필드명 camelCase |
| 시간 | ISO 8601 문자열. 시뮬레이션 중에는 가상 시각 |
| 응답 시간 목표 | `/recommend` 500ms 이내 (시뮬레이션에서 수천 번 호출) |

에러 응답

```json
{ "error": "INVALID_INPUT", "message": "seats is empty" }
```

### 취향 가중치 (Weights) \[회의: 피처 목록\]

```json
{
  "outlet": 0.8,
  "window": 0.3,
  "aircon": -0.5,
  "light": 0.2,
  "toilet": 0.1,
  "comfort": 0.4
}
```

- 각 값은 -1 \~ 1. 양수면 선호, 음수면 회피.
- 키 목록은 회의에서 확정. 백엔드는 이 JSON을 그대로 `User.preferences`에 저장.

### 좌석 (Seat)

```json
{
  "seatId": "B-4",
  "row": 1,
  "col": 3,
  "features": {
    "outlet": 1,
    "window": 1,
    "aircon": 0.2,
    "light": 0.8,
    "toilet": 0.5,
    "comfort": 0.6
  },
  "status": "empty",
  "occupiedMinutes": 0,
  "isFixedReserved": false
}
```

- `features`: 0 \~ 1로 정규화한 값. 키는 Weights와 같아야 함.
- `status`: `"empty" | "occupied" | "away" | "reserved"`
- `occupiedMinutes`: 현재 사용자가 앉은 지 몇 분 됐는지 (빈자리면 0)

### 상황 (Context) \[회의: 상황 반영 여부\]

```json
{
  "now": "2026-11-20T14:30:00",
  "hour": 14,
  "dayOfWeek": 4,
  "examPeriod": false
}
```

## 0. 헬스 체크

`GET /health`

```json
{ "status": "ok", "modelVersion": "0.1.0" }
```

## 1. 페르소나 목록 (콜드 스타트)

`GET /personas`

회원가입 직후 프론트가 보여줄 페르소나 카드와 초기 가중치예요. \[회의: 4번 콜드 스타트 방식\]

```json
{
  "personas": [
    {
      "id": "laptop",
      "name": "노트북파",
      "description": "콘센트가 없으면 공부가 안 돼요",
      "initialWeights": { "outlet": 0.9, "window": 0.1, "aircon": 0.0, "light": 0.3, "toilet": 0.1, "comfort": 0.2 }
    },
    {
      "id": "focus",
      "name": "집중파",
      "description": "조용하고 구석진 자리가 좋아요",
      "initialWeights": { "outlet": 0.2, "window": -0.2, "aircon": -0.3, "light": 0.5, "toilet": -0.4, "comfort": 0.4 }
    }
  ]
}
```

## 2. 좌석 추천 (핵심)

`POST /recommend`

요청

```json
{
  "user": {
    "userId": 12,
    "persona": "laptop",
    "weights": { "outlet": 0.9, "window": 0.1, "aircon": 0.0, "light": 0.3, "toilet": 0.1, "comfort": 0.2 }
  },
  "context": { "now": "2026-11-20T14:30:00", "hour": 14, "dayOfWeek": 4, "examPeriod": false },
  "seats": [],
  "jibakScores": { "A-1": 0.9, "B-4": 0.1 },
  "strategy": "personalized"
}
```

- `seats`: 열람실 전체 좌석의 Seat 배열
- `jibakScores`: 3번 API 결과. 없으면 생략 가능
- `strategy`: `"personalized" | "baseline"`. 개인화 효과 비교용 \[회의: 8번\]

응답

```json
{
  "first": {
    "seatId": "B-4",
    "score": 0.87,
    "emptyProb": 0.85,
    "mode": "exploit",
    "reasons": ["콘센트가 있어요", "창가 자리예요"]
  },
  "second": {
    "seatId": "C-2",
    "score": 0.71,
    "emptyProb": 0.78,
    "mode": "explore",
    "reasons": ["퓹신한 의자예요", "새로운 자리를 추천해 봤어요"]
  }
}
```

- `mode`: `"exploit"`(배운 취향대로) / `"explore"`(탐색용) \[회의: 5번\]
- `reasons`: 프론트에 그대로 보여줄 문장 \[회의: 7번\]
- 모델이 할 일: 예약석·점유석 제외, 취향 점수 계산, 빈자리 확률 반영, 지박령 페널티, 1·2순위 선택, 이유 생성

## 3. 지박령 구역 점수

`POST /zones/jibak`

좌석별로 "장시간 점유가 반복되는 정도"를 계산해요. 개인이 아니라 **좌석 단위**예요. \[회의: 9번 프라이버시\]

요청

```json
{
  "sessions": [
    { "seatId": "A-1", "startedAt": "2026-11-19T09:00:00", "endedAt": "2026-11-19T21:00:00", "awayMinutes": 120 },
    { "seatId": "B-4", "startedAt": "2026-11-19T13:00:00", "endedAt": "2026-11-19T15:00:00", "awayMinutes": 0 }
  ],
  "from": "2026-11-13T00:00:00",
  "to": "2026-11-20T00:00:00"
}
```

- 사용자 ID는 보내지 않음

응답

```json
{ "jibakScores": { "A-1": 0.92, "B-4": 0.10 } }
```

- 0 \~ 1. 높을수록 지박령 출몰 구역.

## 4. 빈자리 예측

`POST /predict/occupancy`

요청

```json
{
  "context": { "now": "2026-11-20T14:30:00", "hour": 14, "dayOfWeek": 4, "examPeriod": false },
  "seats": [],
  "horizonMinutes": 60
}
```

응답

```json
{
  "predictions": [
    { "seatId": "A-1", "emptyProb": 0.05, "expectedVacateMinutes": 240 },
    { "seatId": "B-4", "emptyProb": 0.85, "expectedVacateMinutes": null }
  ]
}
```

- `emptyProb`: 앞으로 `horizonMinutes` 동안 비어 있을 확률
- `expectedVacateMinutes`: 지금 앉은 사람이 일어날 때까지 예상 시간 (빈자리면 null)
- `/recommend` 내부에서도 이 계산을 쓰지만, 프론트의 "곧 빌 자리" 표시용으로 따로 열어 둠

## 5. 피드백 반영 (가중치 업데이트)

`POST /feedback`

요청

```json
{
  "user": {
    "userId": 12,
    "weights": { "outlet": 0.9, "window": 0.1, "aircon": 0.0, "light": 0.3, "toilet": 0.1, "comfort": 0.2 }
  },
  "recommendation": { "firstSeatId": "B-4", "secondSeatId": "C-2" },
  "choice": "SECOND",
  "chosenSeatId": "C-2",
  "seatFeatures": {
    "B-4": { "outlet": 1, "window": 1, "aircon": 0.2, "light": 0.8, "toilet": 0.5, "comfort": 0.6 },
    "C-2": { "outlet": 0, "window": 0, "aircon": 0.7, "light": 0.6, "toilet": 0.3, "comfort": 0.9 }
  }
}
```

- `choice`: `"FIRST" | "SECOND" | "OTHER" | "NONE"` \[회의: 2번 피드백 신호\]
- `chosenSeatId`: OTHER일 때 실제로 고른 자리. 그 좌석의 features도 `seatFeatures`에 포함.

응답

```json
{
  "updatedWeights": { "outlet": 0.82, "window": 0.05, "aircon": 0.03, "light": 0.3, "toilet": 0.1, "comfort": 0.29 },
  "delta": { "outlet": -0.08, "window": -0.05, "aircon": 0.03, "comfort": 0.09 }
}
```

## 회의에서 확정할 것

- [ ]  Weights 키 목록 (피처 5\~6개)
- [ ]  `choice` 값 종류와 체류 시간 신호 사용 여부
- [ ]  개인화 단위 (페르소나 + 사용자별 조정 여부)
- [ ]  `mode` 탐색 방식 사용 여부
- [ ]  `context`에 넣을 항목