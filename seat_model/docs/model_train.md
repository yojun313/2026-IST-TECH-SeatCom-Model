# 작업: 좌석 추천 모델 학습 파이프라인 (v1 데이터: synth-1.0)

## 배경
v1 합성 데이터(`data/libraries/scripts/generate_synthetic.py --users 3000 --sessions 165000 --seed 20261114`, `load_mongo.py`로 MongoDB `library`에 적재)로 "추천 요청 시점의 빈자리 중 사용자가 고를 좌석"을 맞히는 순위 모델을 학습하고, 여러 방법론을 같은 기준으로 비교한다.
- 참고: `seat_model/docs/library_db_schema.md`, `recommender/preference_adapter.py`(`to_profile`, 현재 서비스 점수식)
- v2 설계 기준 원본은 `seat_model/docs/model_train_v2.md`에 있다. v2 데이터(`ml_v2_*`)가 생기면 그 문서를 쓴다.
- 코드: `seat_model/train/`, 설정: `seat_model/config/train.yaml`, 테스트: `seat_model/tests/`

## 0단계: 진입 조건 확인 (통과 전 학습 금지)
1. DB 컬렉션 건수가 `meta.synthetic_manifest.counts`와 같은지, DB와 `data/libraries/synthetic/*.jsonl` 표본이 같은지 확인한다.
2. 규모를 보고한다: 사용자, 요청, 요청당 빈자리 수 분포, 선택 없는 요청, 노출 비율, 추천 내 선택 비율
3. 실행 환경: `nvidia-smi`로 GPU별 **여유** 메모리(다른 작업과 공유 중), RAM. 의존성은 `seat_model/.venv`(uv, Python 3.11): numpy, pandas, pyarrow, scipy, scikit-learn, lightgbm, xgboost, optuna, pymongo, pyyaml, pytest. torch는 6단계 승인 후 설치한다.

## v2 → v1 대응표 (v2 이름을 그대로 쓰면 안 되는 곳)
| v2 설계 이름 | v1 실제 | 비고 |
|---|---|---|
| `ml_v2_requests` | `synth_sessions` | 요청 1건 = 세션 1건 |
| `ml_v2_candidates` | 시나리오 좌석 − `occupied_seat_ids` | 빈자리 **전체**를 복원한다. `negative_seat_ids`(최대 20개)는 쓰지 않는다 |
| `ml_v2_exposures` | `synth_sessions.exposed`(rank, engine_score) | 관측 프로필 점수 상위 5개(결정적). **inclusion_prob 없음** |
| `synth_v2_users` | `synth_users`의 `preference_profile`, `requirements`, `kakaotalk_profile.preferences.*.{preference,confidence,inference_type}` | 온보딩 설문 없음 |
| `synth_v2_room_rules` | `synth_seat_env.features.laptopAllowed` | v1은 좌석·시나리오 단위 |
| `synth_v2_days` | `synth_scenarios.conditions`(season, weather, base_temperature_c) | 공휴일·학사 구분 없음 |
| `brightness_day` | `synth_seat_env.brightness_by_slot[time_slot]` | |
| `desk_layout` | `synth_seat_env.raw.desk_type`, `raw.desk_group` | |
| `ml_v2_labels.chosen_seat_id` | `synth_sessions.chosen_seat_id` | null = 선택 없음 |
| `use_confirmed` | `synth_events` `useConfirmed` | v1에서는 효용과 무관한 70% 무작위라 정보가 없다 |
| `early_leave`, `moved_to_request_id` | 없음 | |
| `ml_v2_feedback` | `synth_sessions.satisfaction`, `synth_events` `ratingSubmitted` | 숨은 취향 적합도 + 노이즈(σ 0.03)로 생성 → 사실상 정답 |
| `synth_v2_truth_*` | `synth_users.latent`, `synth_sessions.chosen_true_utility`, `chosen_preference_fit`, `kakaotalk_profile.*.is_correct_vs_latent` | 입력과 같은 문서에 섞여 있다 → 필드 단위 허용 목록 |
| `meta.synthetic_manifest_v2.splits` | 없음 | `exports/train_v1/splits.json`에 새로 만든다 |
| `U_true`, `oracle_seat_id` | latent로 계산: `Σ w(1−|t−x|) − 0.5·noise_aversion·noise_index` | 노트북 사용자 × 노트북 금지 좌석 = 선택 불가 |
| `no_exposure_choice_seat_id` | 없음 | |

**v1에서 할 수 없는 항목**(리포트에 "해당 없음"으로 적는다)
- `survey_` 특징, 4단계 변형 (c)·(d)·(e)(설문·laptop_carried·config 우선순위 없음)
- 7단계 W2·W3(결과 신호 없음), E3 IPS·오프폴리시 평가(노출 확률 없음, 로깅 정책이 결정적)
- 평가 지표 "노출 없는 선택 HitRate"

## 데이터 사용 규칙 (가장 중요)
| 구분 | 허용 대상 | 용도 |
|---|---|---|
| 입력 특징 | `synth_users`(위 허용 필드), `synth_seat_env`(features, brightness_by_slot, raw.noise_index, raw.desk_type, raw.desk_group), `synth_scenarios.conditions`, `synth_sessions`(time_slot, occurred_at, occupied_seat_ids, exposed) | 특징 |
| 학습 라벨 | `synth_sessions.chosen_seat_id` **하나만** | 정답 |
| 분할 층화 | `synth_users.archetype` | 분할에만. 특징 금지 |
| 평가 전용 | `synth_users.latent`, `archetype` | `exports/train_v1/eval_only/`에만 저장 |
| **사용 금지** | `satisfaction`, `ratingSubmitted`, `synth_events` 전체, `choice_origin`, `chosen_true_utility`, `chosen_preference_fit`, `is_correct_vs_latent`, `reason` | 특징·라벨·가중치·튜닝·모델 선택 어디에도 쓰지 않는다 |

- 로더는 허용 목록 방식(`train/common.py` `FeatureReader`)으로 필요한 필드만 읽는다. 목록 밖 컬렉션·필드를 요청하면 예외를 던진다.
- `choice_origin`도 금지한다. "추천 안에서 골랐는지"를 알려주는 사후 정보다.
- 만족도를 빼는 이유: v1 만족도는 숨은 취향 적합도에 작은 노이즈를 더한 값이라, 학습에 쓰면 정답이 새어 들어간다.

## 1단계: 학습 데이터 파이프라인 (`seat_model/train/data.py`)
- 출력: `seat_model/exports/train_v1/`
  - `candidates.parquet`: 요청 × 빈자리 1행
  - `sessions.parquet`: 요청 메타. 특징이 아니다
  - `splits.json`, `data_report.json`
- 요청은 `occurred_at` 순서로 처리한다. `hist_`·`inf_`는 그 요청 **이전** 상태로 계산한 뒤 선택으로 갱신한다(`train/userstate.py`). 서비스 추론도 같은 코드를 쓴다.

| 접두어 | 내용 |
|---|---|
| `seat_` | laptopAllowed, keyboardAllowed, partition, outletAvailable, windowProximity, toiletProximity, entranceProximity, brightness(시간대), airflow, deskHeight, deskWidth, viewQuality, noise_index, desk_type one-hot |
| `seatc_` | 같은 특징에서 그 요청 빈자리 평균을 뺀 값(상대적 장점) |
| `pref_` | 관측 프로필 importance·target(8개 featureId), 알려진 항목 수, 카카오톡 inference_type |
| `match_` | `importance × (1 − |target − x|)`, 합계 점수(= 현재 서비스 featureScore) |
| `ctx_` | time_slot, 요일, 혼잡도, 빈자리 수, 날씨·계절·기온, `ctx_req_ok`(필수 조건 충족) |
| `rt_` | 같은 책상 묶음 크기·점유 수·빈 비율 |
| `hist_` | 과거 착석 횟수, 직전 좌석·책상 묶음과 같은지, 이 좌석·묶음 과거 선택 횟수 |
| `inf_` | **착석 기록으로 추정한 취향**(11단계): `r_k`, `r_k × seatc_k`, 합계 |
| `exp_` | 노출 여부, 순위, (6 − 순위)/5 |

- 라벨: `y_chosen`(0/1)만 둔다.
- 제외: 선택 없는 요청(24,677건), 빈자리 1개 이하 요청(1,345건). 단, 빈자리 1개 이하 요청도 이력 상태 갱신에는 넣는다.
- 표준화(로짓)는 학습 분할 통계로만 적합한다.

## 2단계: 평가 모듈 (`seat_model/train/evaluate.py`), 모델보다 먼저
**분할** (`splits.json`, seed 20261114)
- 사용자: archetype 층화 70/15/15
- 열람실 홀드아웃: 서로 다른 도서관에서 4개 고른다. 이 열람실 요청은 모든 학습·검증에서 뺀다.
- 평가 집합
  - (A) 테스트 사용자, 홀드아웃 아닌 열람실
  - (B) 학습 사용자, 홀드아웃 열람실
  - (C) 학습 사용자의 10월 요청. 모델은 9월 요청만으로 **다시 학습**한다.
- 검증: 검증 사용자, 홀드아웃 아닌 열람실

**점수 규칙**
- 모든 모델 점수에 필수 조건(노트북 규정) 규칙 필터를 똑같이 적용한다. 조건을 만족하는 좌석을 먼저 놓는다.
- 노출 특징은 점수 계산 때 0(미노출)으로 둔다. 새 정책은 기존 노출을 알 수 없기 때문이다.

**지표**

| 지표 | 계산 근거 | 모델 선택·튜닝에 사용 |
|---|---|---|
| HitRate@1/3/5, MRR, 로그 우도 | `y_chosen` | ⭕ |
| NDCG@5 | latent `U_true` 순위 등급(상위 5%=3, 20%=2, 50%=1) | ❌ 보고만 |
| Oracle HitRate@1 | `U_true` 최대 좌석 | ❌ 보고만 |
| 예상 체험 효용 | 상위 1개 좌석의 `U_true` 백분위 | ❌ 보고만 |

- **주의:** v1 선택의 81%는 추천 상위 5개 안에서 나왔다. 그래서 `y_chosen` 지표는 로깅 정책(현재 서비스 규칙)을 흉내 낼수록 높아진다. 리포트에서는 `y_chosen` 지표와 truth 지표를 함께 보고, 차이가 나면 그 이유를 적는다.
- 부트스트랩 95% 신뢰구간은 **사용자 단위** 재표본 1,000회로 낸다. 같은 사용자가 요청을 평균 46건 갖고 있기 때문이다.
- 세부 분석
  - archetype(평가 전용)
  - 관측 unknown 개수
  - 프로필 유무
  - 노트북 필수 조건
  - 혼잡도
  - 빈자리 수
  - **과거 착석 횟수**(11·12단계 학습 곡선)

## 3단계: 기준선
1. 무작위
2. 출입구 최근접
3. 인기도(학습 분할 좌석 선택률, 수축)
4. **현재 서비스 규칙**(`match_score` = featureScore, 로깅 정책과 같은 점수)
5. 동일 가중 규칙: 관측 목표에 대한 `1 − |t − x|` 합. config 평균 중요도가 v1에 없어서 대체했다.
6. (상한선, 보고용) `U_true`

## 4단계: 조건부 로짓 (`seat_model/train/models/clogit.py`)
- numpy + scipy L-BFGS로 직접 구현한다. 요청별 softmax 우도와 L2 정규화를 쓰고, 표준오차는 헤시안 역행렬로 구한다.
- `V = Σ_k [β_k,pop·seat_k + β_k,pers·match_k + β_k,inf·inf_x_k] + γ·(rt, hist, req_ok) + δ·exp`
- 변형
  - a_full: 전체
  - b_no_exp: `exp_` 제외
  - g_no_profile: **취향 미입력**(`match_` 없이 착석 기록만)
  - h_profile_only: 착석 기록(`hist_`·`inf_`) 미사용
  - i_population: 개인화 없음
- 해석
  - 계수표
  - 학습된 좌석 특징 중요도와 latent 평균 중요도의 켄달 타우(파라미터 복원 테스트)

## 5단계: 그래디언트 부스팅 순위 (`seat_model/train/models/gbdt.py`)
- LightGBM `lambdarank`(group = 요청)가 기본이고, 비교용으로 XGBoost `rank:ndcg`(GPU, 안 되면 CPU)를 쓴다. 라벨은 `y_chosen`만 쓴다.
- Optuna 50 trial로 튜닝한다. 학습 요청의 25% 표본으로 학습하고, 검증 MRR로 고르며, 시드를 고정한다. 최종 모델은 최적 파라미터로 학습 분할 전체에 다시 학습한다.
- 변형: a_full, b_no_exp, g_no_profile, h_profile_only
- SHAP은 LightGBM 내장 TreeSHAP(`pred_contrib`)으로 구하고, 그룹별 평균 |SHAP|을 낸다.

## 6단계: 신경망 순위 (GPU, `seat_model/train/models/neural.py`)
- 내용은 v2 문서와 같다. 4~5단계 결과를 보고한 뒤 **승인받고** 진행한다.
- GPU는 공유 중이다. 시작할 때 여유 메모리를 확인하고, 한 장의 80% 이내로 배치를 정한다.

## 7단계: 샘플 가중치·노출 편향 실험 (승인 후 진행)
| 설정 | v1 |
|---|---|
| W0 가중치 없음 / W1 `log(빈자리 수)` | 가능 |
| W2·W3 결과 신호 | 해당 없음(결과 신호 없음) |
| E1 `exp_` 포함 / E2 `exp_` 제외 | 가능(4·5단계 a·b 변형) |
| E3 IPS, 오프폴리시 평가 | 해당 없음(노출 확률 없음, 결정적 로깅 정책) |
| E4 노출 요청 가중치 낮춤 | 가능 |

## 8단계: 보조 구성요소
1. 콜드스타트: 11단계 온라인 학습으로 대체한다. 첫 방문은 전역 모델(인구 평균 + 관측 프로필)로 추천하고, 착석할 때마다 개인화한다.
2. 좌석 유형 군집: `seat_` 특징 K-means(k = 4~8, 실루엣 점수). 이름을 붙여 추천 설명에 쓴다.

(사용 후 만족도로 학습하는 보조 모델은 만들지 않는다.)

## 9단계: 추론 모듈 (`seat_model/train/inference.py`)
- `SeatRecommender.load(models/<버전>)`
- `recommend(user, snapshot, k)`
  - 필수 조건 규칙 필터를 먼저 적용한 뒤 순위를 낸다.
  - 상위 k개와 점수, 이유 3개를 준다. 이유는 계수 × (좌석 − 빈자리 평균) 기여를 한국어 템플릿으로 바꾼 것이다.
- `observe(user_id, snapshot, chosen_seat_id, exposed_rank)`: 착석 기록 1건을 바로 반영한다(12단계).
- `inferred_profile(user_id)`: 착석 기록으로 파악한 취향을 서비스 `preference_profile` 형식으로 준다(11단계).
- 저장 위치: `seat_model/models/<버전>/`. 모델, 표준화 통계, 특징 목록, config, 지표 JSON, 온라인 상태를 함께 둔다.

## 10단계: 리포트 (`seat_model/reports/training_report.md`)
- 모델 × 분할(A/B/C) × 지표 표, 사용자 단위 부트스트랩 신뢰구간
- 중요도 복원: 로짓 β, SHAP, latent 평균의 켄달 타우
- 11·12단계 결과: 착석 횟수별 학습 곡선, 취향 복원 상관, 주간 재학습
- 최종 모델과 선택 이유, 한계
  - 합성 데이터
  - 노출 편향
  - 카카오톡 프로필이 표현하지 못하는 취향

## 11단계(신규): 취향 입력 없이 착석 기록으로 취향 파악
- **목표:** 카카오톡·설문 취향이 없어도 좌석 선택 기록만으로 개인 취향(방향·강도·확신도)을 추정하고 추천에 쓴다. 관측 프로필에 없는 항목(칸막이, 출입구, 소음 등)도 찾아낸다.
- **방법 1: 드러난 선호 특징 `inf_`** (`userstate.py`)
  - `r_k = Σ(선택 좌석 x_k − 그때 빈자리 평균 x_k) / (n + m0)`, m0 = 3
  - 전역 모델(로짓·GBDT)이 `r_k × seatc_k`를 얼마나 믿을지 데이터로 학습한다.
- **방법 2: 사용자별 베이지안 계수 μ_u** (`online.py`)
  - `V = g(전역 점수) + μ_u · z`, z = 표준화한 `seatc_`
  - 사전 N(0, τ²I)에서 시작해 선택마다 softmax 우도로 뉴턴 1스텝(라플라스)을 밟는다.
  - 갱신 우도에는 실제로 보였던 노출을 넣는다. 노출에 끌린 선택을 개인 취향으로 오해하지 않기 위해서다.
- **취향 프로필 내보내기:** `infer_profile()`이 μ_u와 사후 표준편차로 항목별 방향, 강도, z, 확신도를 준다.
  - 착석 3회 이상이고 |z| ≥ 1인 항목만 `preference_profile`(featureId, target 0/1, importance)로 낸다.
  - 출처는 `source: "seat_history"`로 표시한다.
- **평가**
  - 취향 미입력 변형(g_no_profile, i_population + 온라인)의 A·B·C 지표
  - 착석 횟수 구간별 지표(0, 1~2, 3~5, 6~10, 11~20, 21~40, 41+)
  - 착석 n회 시점 μ_u와 latent 취향(방향 × 중요도)의 특징별 스피어만 상관. 관측 프로필의 상관과 비교한다.

## 12단계(신규): 이용 중 착석 기록의 연속 반영
- **사용자 층(즉시):** 서비스는 착석이 확정될 때마다 `observe()`를 호출한다. `UserState`(hist·inf)와 μ_u가 바로 갱신되고, 다음 추천부터 반영된다. 전역 모델 재학습은 필요 없다. 상태는 `online_state.json`으로 저장·복원한다.
- **전역 층(주기적):** `retrain_every_days`(7일)마다 새로 쌓인 학습 사용자 요청으로 조건부 로짓을 웜스타트(이전 β에서 시작) 재학습한다.
- **평가**
  - 사용자 층: 시간 순서 prequential 평가(예측 → 기록 → 갱신)로 정적 모델과 온라인 모델을 비교한다.
  - 전역 층: 주별로 "지난주까지로 재학습" 모델과 "첫 2주로 1회 학습" 정적 모델을 다음 주 테스트 사용자 요청에서 비교한다.
- **누설 방지:** 점수는 항상 갱신 **전** 상태로 낸다. pytest가 "라벨을 바꿔도 갱신 전 점수가 같다"를 검사한다.

## 공통 원칙
- 테스트 분할(A)은 최종 평가에서 한 번만 쓴다. 튜닝·모델 선택은 검증 분할의 `y_chosen` 지표로만 한다.
- `pytest seat_model/tests`
  - 특징 열에 금지 필드명·사후 정보가 없는지
  - 로더가 latent·만족도·`synth_events` 접근 시 실패하는지
  - 특징 코드가 참값 경로를 참조하지 않는지
  - 이력·취향 추정이 그 요청 이전 선택만으로 계산되는지
  - 온라인 점수가 라벨 이전 상태인지
- 수치와 스위치는 `seat_model/config/train.yaml`에서 관리하고, 시드를 고정한다.
- DB와 생성 데이터는 읽기만 한다. 결과는 `seat_model/exports/`, `seat_model/models/`, `seat_model/reports/`에만 쓴다.
- 단계마다 결과를 짧게 보고한다. 6·7단계는 승인 후 진행한다.
