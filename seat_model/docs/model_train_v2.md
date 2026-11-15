# 작업: 좌석 추천 모델 학습 파이프라인 (v2 데이터 전용)

## 배경
v2 데이터(`seat_model/synth_v2/`로 생성)로 "추천 요청 시점의 eligible 좌석 중 사용자가 고를 좌석"을 맞히는 순위 모델을 학습하고, 여러 방법론을 같은 기준으로 비교한다.
참고: `seat_model/docs/library_db_schema.md`, `seat_model/docs/dataset_prompt.md`, `seat_model/reports/data_contract.json`, `recommender/preference_adapter.py`(`to_profile`, 현재 서비스 점수식)
**v1 데이터(`synth_sessions` 등)는 쓰지 않는다.**

## 0단계: 진입 조건 확인 (통과 전 학습 금지)
1. `python seat_model/synth_v2/check_contract.py`를 실행한다. 실패하면 **학습을 시작하지 말고** 실패 항목을 보고한 뒤 멈춘다.
2. 규모를 보고한다: 사용자, 요청, 요청당 eligible 좌석 수 분포, 이탈·포기 요청, 노출 로그 비율
3. 실행 환경을 확인한다: `nvidia-smi`로 GPU별 **여유** 메모리(다른 작업과 공유 중), RAM. 의존성은 `seat_model/.venv`(uv)에 설치한다: scikit-learn, lightgbm, xgboost, optuna, shap, torch, pyarrow, pandas, scipy, pymongo, pyyaml, pytest
4. 단계별 계획을 보고하고 승인받는다.

## 데이터 사용 규칙 (가장 중요)
| 구분 | 허용 대상 | 용도 |
|---|---|---|
| 입력 특징 | `ml_v2_requests`, `ml_v2_exposures`, `ml_v2_candidates`, `synth_v2_users`, `synth_v2_room_rules`, `synth_v2_days` | 특징 |
| 학습 라벨 | `ml_v2_labels.chosen_seat_id` **하나만** | 정답 |
| 결과 신호 | `ml_v2_labels`의 `use_confirmed`, `early_leave`, `moved_to_request_id` | 7단계 가중치 **실험에서만** |
| 평가 전용 | `synth_v2_truth_*` | 평가 지표 계산에서만 |
| **사용 금지** | `ml_v2_feedback` 전체(`feedback_*`) | 특징·라벨·가중치·튜닝·모델 선택 어디에도 쓰지 않는다 |

- 로더는 **허용 목록(allowlist) 방식**으로 필요한 필드만 읽는다. 금지 목록 방식은 쓰지 않는다.
- 특징 생성 코드가 truth·feedback 컬렉션에 접근하면 예외를 던진다.
- 만족도를 빼는 이유: 합성 데이터의 만족도는 참 효용에 노이즈를 더한 값이라, 학습에 쓰면 정답이 새어 들어간다.
- 같은 이유로 결과 신호(조기 퇴실 등)도 참 효용에서 만들어졌을 수 있다. 그래서 메인 모델에는 쓰지 않고, 7단계 실험에서 영향을 따로 보고한다.

## 1단계: 학습 데이터 파이프라인 (`seat_model/train/data.py`)
- 출력: `seat_model/exports/train_v2/` Parquet. 요청 × eligible 좌석 1행, `request_id`로 묶는다.
- 특징 그룹(접두어 단위로 켜고 끈다. 데이터에 없는 그룹은 자동으로 끄고 리포트에 기록한다)

| 접두어 | 내용 |
|---|---|
| `seat_` | airflow, noise_index, partition, brightness_day, deskWidth, windowProximity, viewQuality, toiletProximity, entranceProximity, outletAvailable, room_laptop_allowed, desk_layout one-hot |
| `pref_` | 카카오톡 관측 선호의 그룹별 `방향 × 확신도`, unknown 플래그, inference_type |
| `match_` | 관측 목표값 기준 일치값 `m_k_obs`(unknown이면 0 + 플래그), `pref × seat` 상호작용 |
| `survey_` | 온보딩 설문(입장 전), `survey_completed` |
| `ctx_` | time_slot, 요일 유형, 학사 구분, 날씨, 혼잡도, eligible 좌석 수, laptop_prior, laptop_carried |
| `rt_` | 주변 점유 수, 양옆 빈자리 |
| `hist_` | 직전 좌석·구역과 같은지, 좌석·구역 과거 선택 횟수. **시간 순서상 과거, 학습 분할 요청만** 쓴다 |
| `exp_` | 노출 여부, 순위, inclusion_prob |

- 라벨: `y_chosen`(0/1)만 둔다. 결과 신호는 별도 파일 `weights_signals.parquet`(요청 단위)로 분리하고 특징 테이블에는 넣지 않는다.
- 인코딩·정규화는 학습 분할로만 적합한다.
- 선택이 없는 요청과 eligible이 1개 이하인 요청은 제외하고 개수를 기록한다.

## 2단계: 평가 모듈 (`seat_model/train/evaluate.py`), 모델보다 먼저
**분할** (`meta.synthetic_manifest_v2.splits` 사용, 새로 만들지 않는다)
- (A) 사용자 홀드아웃 테스트
- (B) 열람실 홀드아웃(학습 사용자 요청)
- (C) 시간 분할(10월)

**지표**
| 지표 | 계산 근거 | 모델 선택·튜닝에 사용 |
|---|---|---|
| HitRate@1/3/5, MRR, 로그 우도 | `y_chosen` | ⭕ |
| NDCG@5 | truth `U_true` 순위로 만든 등급(상위 5%=3, 20%=2, 50%=1) | ❌ 보고만 |
| Oracle HitRate@1 | truth `oracle_seat_id` | ❌ 보고만 |
| 노출 없는 선택 HitRate | truth `no_exposure_choice_seat_id` | ❌ 보고만 |
| 예상 체험 효용 | 상위 1개 좌석의 truth 체험 효용 백분위 | ❌ 보고만 |

- 모델 선택과 하이퍼파라미터 튜닝은 **검증 분할의 `y_chosen` 지표로만** 한다. truth 지표로 튜닝하지 않는다.
- 세부 분석
  - archetype
  - 관측 unknown 개수(0~2 / 3~4 / 5+)
  - `survey_completed`
  - straightliner(truth 표시, 평가에서만)
  - 노트북 사용자
  - 혼잡도 구간
  - eligible 좌석 수 구간
- 부트스트랩 95% 신뢰구간(요청 단위 1,000회)을 낸다.
- (선택, 보고 전용) `ml_v2_feedback`을 **평가 단계에서만** 읽어, "모델 상위 1개 = 실제 선택"인 요청과 아닌 요청의 평균 만족도를 비교할 수 있다. 이 결과는 모델 선택에 쓰지 않는다고 리포트에 명시한다.

## 3단계: 기준선
1. 무작위
2. 출입구 최근접
3. 인기도(학습 분할 좌석 선택률)
4. **현재 서비스 규칙**(`to_profile` featureScore, 로깅 정책과 같은 점수)
5. config 평균 중요도 × `m_k_obs`
6. (상한선, 보고용) truth `U_true`

## 4단계: 조건부 로짓 (`seat_model/train/models/clogit.py`)
- numpy + scipy L-BFGS로 직접 구현한다. 요청별 softmax 우도와 L2 정규화를 쓴다.
- 효용 구조: `V = Σ_k [β_k,pop·seat_k + β_k,pers·(pref_k × seat_k)] + γ·(ctx, rt, hist) + δ·exp`
- 변형
  - (a) 기본
  - (b) `exp_` 제외
  - (c) `survey_` 추가
  - (d) `laptop_carried` 대신 `laptop_prior`만 사용
  - (e) 8개 그룹 `β_pop`을 config 우선순위대로 양수 증분 누적(softplus) 파라미터화
- 해석
  - 그룹별 β와 표준오차(헤시안)
  - 학습된 그룹 중요도 순서와 **config 우선순위**의 켄달 타우
  - **truth 평균 중요도**와의 비교(파라미터 복원 테스트)
  - 노트북·콘센트(고정 요인) 계수는 따로 보고한다.

## 5단계: 그래디언트 부스팅 순위 (`seat_model/train/models/gbdt.py`)
- LightGBM `LGBMRanker`(lambdarank, `group = request_id`)가 기본이고, 비교용으로 XGBoost `rank:ndcg`를 쓴다. 라벨은 `y_chosen`만 쓴다.
- Optuna 50 trial(검증 분할 MRR 기준, 시드 고정). GPU 학습은 여유 메모리 안에서 시도하고, 안 되면 CPU로 한다.
- 4단계 변형 (a)~(d)를 같은 방식으로 적용한다.
- SHAP(TreeExplainer): 그룹별 평균 |SHAP| 순위와 config 우선순위의 켄달 타우

## 6단계: 신경망 순위 (GPU, `seat_model/train/models/neural.py`)
- PyTorch, 요청 단위 softmax(listwise) 교차 엔트로피
- 모델 2종
  1. 사용자-좌석 상호작용 MLP
  2. 같은 요청의 eligible 좌석을 함께 보는 Transformer 인코더(2층, 마스킹)
- 사용자 ID 임베딩은 쓰지 않는다(콜드스타트·사용자 홀드아웃).
- **GPU는 다른 작업과 공유 중**이다. 시작 시 여유 메모리를 확인해서 한 장의 여유 메모리 80% 이내로 배치를 정한다. 혼합 정밀도, 조기 종료(검증 MRR), 시드 고정을 쓴다.
- 4~5단계 대비 개선이 신뢰구간에서 유의하지 않으면 "단순 모델 권장"으로 결론낸다.
- 6단계는 4~5단계 결과를 보고한 뒤 승인받고 진행한다.

## 7단계: 샘플 가중치·노출 편향 실험
4단계 (a)와 5단계 기본 모델로 비교한다. 설정은 `config/train.yaml`에서 켜고 끈다.

| 설정 | 내용 |
|---|---|
| W0 | 가중치 없음 |
| **W1 (메인)** | `log(eligible 좌석 수)` |
| W2 (실험) | W1 + 결과 신호: use_confirmed ×1.2, early_leave·moved ×0.5 |
| W3 (실험) | W2 + 자리 이동 전 좌석을 강한 오답으로 추가 |
| E1 | `exp_` 포함 |
| E2 | `exp_` 제외 |
| E3 | IPS: `1 / inclusion_prob`로 가중(상한 clip 10) |
| E4 | 노출된 요청 가중치를 config 비율(기본 0.5)로 낮춤 |

- W2·W3은 결과 신호가 참 효용에서 생성됐을 수 있으므로, **"합성 데이터에서는 성능이 과대평가될 수 있음"**을 결과와 함께 명시한다. 메인 모델은 W1로 둔다.
- 각 설정에서 `y_chosen` 지표와 "노출 없는 선택" HitRate를 함께 보고해, 노출 편향이 실제로 줄었는지 확인한다.
- **오프폴리시 평가**: 노출 로그와 `list_prob`·`inclusion_prob`로 새 모델의 상위 5개 정책을 IPS·SNIPS로 평가하고, 로깅 정책(현재 서비스 규칙)과 비교한다.

## 8단계: 보조 구성요소
1. **콜드스타트**: `pref_`·`survey_` 벡터로 K-means 페르소나(k = 4~8, 실루엣 점수로 선택)를 만든다. 정보가 부족한 사용자(unknown 5개 이상 또는 설문 미완료)에게 페르소나 평균을 대입했을 때 성능 변화를 본다.
2. **좌석 유형 군집**: `seat_` 특징으로 K-means를 돌려 유형 이름을 붙이고, 추천 설명에 쓴다.

(사용 후 만족도로 학습하는 보조 모델은 만들지 않는다.)

## 9단계: 추론 모듈 (`seat_model/train/inference.py`)
- 입력: 요청 스냅샷(eligible 좌석 + 사용자 관측 프로필 + 상황)
- 처리: 노트북 규정 같은 필수 조건은 모델 밖 규칙 필터로 먼저 적용한다.
- 출력: 상위 k개 좌석, 점수, 추천 이유 상위 3개(로짓이면 계수 × 특징, GBM이면 SHAP → 한국어 문장 템플릿)
- `recommender/`와 시그니처를 맞추고, 최종 모델을 `seat_model/models/<버전>/`에 저장한다: 모델, 전처리기, 특징 목록, config, 지표 JSON

## 10단계: 리포트 (`seat_model/reports/training_report.md`)
- 모델 × 분할(A/B/C) × 지표 표, 부트스트랩 신뢰구간
- 중요도 복원: 로짓 β, SHAP 순위, config 우선순위, truth 평균의 비교와 켄달 타우
- 가중치·노출 실험 결과와 오프폴리시 평가
- 세부 그룹별 성능
- 최종 모델과 선택 이유, 한계(합성 데이터, 숨은 취향, 결과 신호의 과대평가 가능성)

## 공통 원칙
- 테스트 분할은 최종 평가에서 한 번만 쓴다.
- `pytest` 누설 테스트를 둔다.
  - 특징 테이블에 `synth_v2_truth_*`·`feedback_*`·`latent*`·`is_correct_vs_latent`·`straightliner` 필드명이 없는지
  - 선택 이후 정보(use_confirmed, stay_minutes, early_leave, moved)가 특징에 없는지
  - 로더가 truth·feedback 컬렉션에 접근하면 실패하는지
  - 이력 특징이 미래 요청을 참조하지 않는지
- 수치와 스위치는 `seat_model/config/train.yaml`에서 관리하고, 시드를 고정한다.
- DB와 생성 데이터는 읽기만 한다. 결과는 `seat_model/exports/`, `seat_model/models/`, `seat_model/reports/`에만 쓴다.
- 단계마다 결과를 짧게 보고한다.