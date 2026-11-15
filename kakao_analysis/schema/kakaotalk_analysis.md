
# [Feature] GPT 기반 카카오톡 대화 분석 및 개인 맞춤형 좌석 선호도 추론 API

## Overview

카카오톡 `.txt` 대화 내역을 OpenAI GPT API로 분석하여 사용자별 좌석 환경 선호도를 추론하는 API를 구현합니다.

사용자가 직접적으로 선호도를 언급하지 않더라도, 대화 속 상황과 행동, 불편함에 대한 표현 등을 분석하여 개인에게 적합한 좌석 조건을 예측하는 것을 목표로 합니다.

추론된 프로파일은 향후 **Seat Recommendation Agent**의 좌석 추천 알고리즘에 활용합니다.

## 1. Analysis Features

다음 7가지 요소를 중심으로 좌석 선호도를 분석합니다.

| Feature | 분석 대상 |
|---|---|
| `restroom` | 화장실과의 거리 및 접근성 |
| `lighting` | 조명 밝기, 자연광 선호도 |
| `air_conditioning` | 에어컨 온도, 냉방 및 직접 바람 선호도 |
| `desk` | 책상 높이, 크기 및 형태 |
| `laptop_usage` | 노트북 사용 여부 및 작업 형태 |
| `outlet` | 콘센트 필요 여부 및 접근성 |
| `view` | 창가, 외부 경치 및 개방감 선호도 |

단, 간접 추론은 근거의 강도에 따라 신뢰도를 구분합니다. 단순히 화장실에 갔다거나 GitHub 링크를 공유했다는 사실만으로 화장실 근접 선호 또는 노트북 사용 여부를 확정하지 않습니다.

## 2. Implementation

### 2.1. KakaoTalk Parser

카카오톡 대화 내역을 파싱하여 분석 가능한 데이터로 변환합니다.

- `.txt` 파일 입력 및 메시지 파싱
- 참여자별 메시지 분리
- 타임스탬프 및 대화 맥락 보존
- 시스템 메시지 및 불필요한 데이터 제거
- 이메일, 연락처 등 개인정보 마스킹

### 2.2. GPT-based Preference Analyzer

GPT API를 사용하여 사용자별 좌석 선호도 프로파일을 생성합니다.

**Inference Process**

1. 대화에서 좌석 환경과 관련된 단서 식별
2. 단서의 맥락과 발화자 분석
3. 직접적 선호와 간접적 추론 구분
4. 7가지 좌석 환경 요소별 선호도 추출
5. 각 추론의 근거 및 신뢰도 기록
6. 근거가 부족한 항목은 `unknown` 처리

간접적인 단서가 여러 개 발견되면 이를 종합하여 분석하되, 상충하는 진술이나 상황에 따라 달라지는 선호도도 고려합니다.

### 2.3. API Endpoint

`POST /api/v1/preferences/analyze`

**Request**

- `file`: KakaoTalk `.txt`
- `participant_id`: 분석 대상 사용자 (Optional)

**Example Response**

```json
{
  "participant_id": "user_01",
  "preferences": {
    "restroom": {
      "preference": "unknown",
      "confidence": 0.0
    },
    "lighting": {
      "preference": "bright",
      "confidence": 0.88
    },
    "air_conditioning": {
      "preference": "avoid_direct_airflow",
      "confidence": 0.91
    },
    "desk": {
      "preference": "height_adjustable",
      "confidence": 0.65
    },
    "laptop_usage": {
      "preference": "uses_laptop",
      "confidence": 0.95
    },
    "outlet": {
      "preference": "required",
      "confidence": 0.93
    },
    "view": {
      "preference": "window",
      "confidence": 0.74
    }
  },
  "evidence": [
    {
      "feature": "outlet",
      "message_id": "msg_102",
      "reason": "사용자가 노트북 충전 필요성을 언급함"
    }
  ]
}
```

| Feature | Possible Values | Description |
|---|---|---|
| `restroom` | `near`, `no_preference`, `unknown` | 화장실 접근성 |
| `lighting` | `bright`, `dim`, `unknown` | 조명 밝기 |
| `air_conditioning` | `cool`, `warm`, `unknown` | 선호 온도 |
| `desk` | `large`, `compact`, `unknown` | 책상 크기 |
| `laptop_usage` | `yes`, `no`, `unknown` | 노트북 사용 여부 |
| `outlet` | `required`, `not_required`, `unknown` | 콘센트 필요 여부 |
| `view` | `window`, `no_preference`, `unknown` | 창가 선호 여부 |


`confidence`는 모델의 근거 기반 추론 확신도이며, 실제 선호도와 일치할 통계적 확률을 의미하지 않습니다.

## 3. Processing Pipeline

1. **Input:** 카카오톡 TXT 업로드
2. **Parsing:** 사용자별 메시지 추출
3. **Preprocessing:** 개인정보 마스킹 및 대화 정제
4. **Inference:** GPT 기반 좌석 선호도 추론
5. **Validation:** 추론 근거 확인 및 JSON Schema 검증
6. **Output:** 사용자별 좌석 선호도 프로파일 반환

긴 대화의 경우 청크 단위 분석 및 결과 통합을 지원하며, 모델이 출력한 근거 메시지 ID가 실제 입력에 존재하는지 검증합니다.

## 4. Tasks

- [x] 카카오톡 TXT Parser 구현
- [x] 참여자별 대화 분류 및 개인정보 마스킹
- [x] 7가지 좌석 선호도 분석 스키마 설계
- [x] GPT API 기반 직접/간접 선호도 추론 구현
- [x] 근거 메시지 추적 및 신뢰도 평가
- [x] 불확실하거나 상충하는 선호도 처리
- [x] 긴 대화 내역 청크 처리 및 결과 통합
- [ ] FastAPI Endpoint 구현
- [x] JSON Schema 검증 및 단위 테스트

## 5. Acceptance Criteria

- 카카오톡 `.txt`에서 참여자별 메시지를 추출할 수 있어야 합니다.
- 7가지 좌석 환경 요소별 선호도를 JSON으로 반환해야 합니다.
- 직접적인 언급과 간접적인 단서를 모두 활용할 수 있어야 합니다.
- 추론마다 근거 메시지와 신뢰도를 확인할 수 있어야 합니다.
- 근거가 부족한 경우 임의의 선호도를 생성하지 않아야 합니다.
- 사용자의 건강 상태 등 민감한 속성을 추측하지 않아야 합니다.
- 참여자 동의와 개인정보 보호를 전제로 분석해야 합니다.
- 생성된 프로파일을 좌석 추천 알고리즘의 입력으로 활용할 수 있어야 합니다.
