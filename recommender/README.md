# 실시간 좌석 추천 학습

`generate_synthetic.py`가 만드는 JSONL 또는 MongoDB `library` DB를 그대로 읽어, 동일한 세션 분할로 다섯 추천 방법을 비교한다. 추천 요청마다 최신 좌석 상태로 후보를 걸러낸 다음 사용자 취향으로 순위를 매긴다. CPU로 실행할 수 있고 사용자/좌석 ID 임베딩에 의존하지 않아 새 사용자와 새 좌석에도 특성만 있으면 점수를 계산한다.

## 방법론

| CLI 이름 | 방법 | 학습 목적 |
|---|---|---|
| `content` | 가중 취향 일치도 기준선 | 학습 없이 `importance × (1 - abs(target - seat))`를 정규화 |
| `pointwise` | SGD 로지스틱 회귀 | 선택 좌석 1 / 같은 세션의 비교 좌석 0 분류 |
| `pairwise` | 좌석 쌍 로지스틱 순위 학습 | `score(chosen) > score(negative)`를 학습; 선형 RankNet 방식 |
| `satisfaction` | Histogram Gradient Boosting 회귀 | 선택 좌석의 합성 `satisfaction` 예측 |
| `lambdamart` | LightGBM LambdaMART | 세션을 query group으로 하여 NDCG 순위 학습 |

선택 예측과 만족도 예측은 목적이 다르다. 선택한 좌석을 맞히는 모델이 반드시 사용 만족도를 높이지는 않는다. `pairwise`는 사용자 ID 행렬분해(BPR-MF)가 아니라 취향과 좌석 특성을 이용하는 선형 모델이다.

## 실행

명령은 **저장소 루트**에서 uv로 실행한다(루트 `pyproject.toml`, Python 3.11). 단계별 상세 설명은 [TRAINING.md](TRAINING.md).

```bash
cd /home/stadium/2026-IST-TECH-SeatCom-Model
uv sync                                   # 최초 1회

# 데이터가 아직 없다면 먼저 실행
uv run data/libraries/scripts/generate_synthetic.py --users 3000 --sessions 165000 --seed 20261114

# MongoDB 적재 없이 로컬 JSONL만으로 학습 가능
uv run python -m recommender.seatrec prepare
uv run python -m recommender.seatrec train
```

uv를 쓸 수 없는 환경에서는 `recommender/requirements.txt`로 venv를 만들어도 된다(Python 3.8에서도 동작 확인).

MongoDB에 이미 적재했다면 `prepare`만 다음 명령으로 바꾼다. 루트 `.env`의 `MONGO_HOST/PORT/USER/PASSWORD/AUTH_DB`를 사용하며 환경변수가 우선한다. DB 읽기만 수행한다.

```bash
uv run python -m recommender.seatrec prepare --source mongo --db library
```

빠른 시험과 모델 지정:

```bash
uv run python -m recommender.seatrec prepare --max-sessions 3000 --negatives 10 --output /tmp/seatrec-small/data
uv run python -m recommender.seatrec train --data /tmp/seatrec-small/data --output /tmp/seatrec-small/models --epochs 3 --rounds 30
uv run python -m recommender.seatrec train --models content pairwise lambdamart --threads 4
```

기본값은 전체 세션, 비교 좌석 최대 10개, SGD 10 epoch, 트리 최대 150회, CPU 스레드 2개다. `--negatives 20`이면 생성기가 저장한 비교 좌석을 최대한 활용한다. `--max-sessions`는 전체 기간에서 seed 고정으로 표본을 뽑은 후 시간순으로 분할한다. 출력 경로를 재사용하면 해당 데이터/모델 파일을 덮어쓰므로 실험별 `--output`을 구분한다.

결과는 `recommender/artifacts/` 아래 저장되며 Git에서 제외된다.

- `dataset/X.npy`: float32 메모리 매핑 특성 행렬. 최대 165,000 × 11 × 67개 값일 때 약 486MB(필터링 전 상한).
- `dataset/y.npy`, `satisfaction.npy`, `offsets.npy`: 선택 여부, 선택 좌석 만족도, 세션 경계.
- `dataset/manifest.json`, `groups.jsonl`: 분할, 특성 순서, 제외 건수, 세션 식별자.
- `models/*.joblib`: 공통 특성 명세를 포함한 모델.
- `models/metrics.json`: 모든 모델의 validation/test Hit@5, NDCG@5, MRR, Top1, 후보 수와 검증 NDCG@5 기준 최상위 모델 이름. 만족도 모델에는 선택 좌석 RMSE도 기록.

행렬은 디스크에 두고 SGD는 배치 처리한다. 원본 사용자/좌석/세션 메타데이터는 메모리에 로딩하며 트리 모델도 별도 메모리를 쓰므로 전체 실행에는 수 GB의 여유 RAM이 필요하다. 만족도 결측치는 0으로 바꾸지 않고 학습에서 제외한다.

## 누수 방지와 평가 해석

세션 시각 기준 70/15/15 분할이며 같은 타임스탬프는 한 분할에만 들어간다. 한 세션의 모든 후보도 함께 이동한다. 모델 선택과 SGD epoch 선택, LambdaMART 조기 종료에는 검증 세트만 사용한다. 같은 사용자가 분할 사이에 등장할 수 있으므로 신규 사용자 전용 평가는 아니다.

입력은 12개 좌석 특성, 결측 마스크, 관측 취향의 목표·중요도·일치도, 소음, 점유율, 시간대, 기준선 점수 총 67개다. 시간대별 밝기를 우선 적용한다. `latent`, `archetype`, `is_correct_vs_latent`, `chosen_true_utility`, `chosen_preference_fit`, 추천 노출 점수·순위, 선택 결과는 입력으로 쓰지 않는다. `satisfaction`은 회귀 정답으로만 쓴다. 취향이나 특성이 없으면 중립값과 결측 마스크를 사용하고, 필수 조건에 필요한 특성이 없으면 해당 좌석을 제외한다.

현재 생성기는 전체 `available_seat_ids`를 저장하지 않는다. 따라서 **선택 좌석 + 저장된 가용 비교 좌석**으로 평가하며 전체 빈자리 추천 성능으로 해석하면 안 된다. 미선택은 불만족의 확정 증거가 아니다. 필수 조건을 위반한 선택, 선택 없는 세션, 비교 좌석 없는 세션은 제외하고 건수를 기록한다. 필수 조건은 `eq/ne/gte/lte/gt/lt`를 지원하며 미지원 연산자는 오류로 처리한다.

생성기가 content 기준선으로 추천을 노출하고 사용자가 그 추천을 따르므로 선택 로그에는 노출 편향이 있다. 만족도도 생성기의 잠재 효용에 따른 합성 라벨이다. 합성 점수로 실제 서비스 품질이나 모델 우열을 확정할 수 없다. 이후 실제 선택·사용 확인·평점과 당시 전체 가용 후보, 노출 확률을 수집해 별도 평가가 필요하다. 현재 코드는 propensity 보정이나 온라인 재학습을 수행하지 않는다.

## 실시간 추론 연결

[examples/live_request.json](examples/live_request.json)이 입력 계약이다. 서버가 실제 좌석 데이터의 정규화 특성과 최신 상태를 결합해 넘긴다. `snapshot.updated_at`은 timezone이 있는 실제 조회 시각으로 갱신한다. 예제는 고정 시각이므로 그대로 실행하면 오래된 스냅샷 오류가 발생한다.

```bash
uv run python -m recommender.seatrec predict \
  --model recommender/artifacts/models/lambdamart.joblib \
  --request /path/to/current_request.json --top-k 5
```

```python
import joblib
from recommender.seatrec.predict import recommend

model = joblib.load('recommender/artifacts/models/lambdamart.joblib')
# current_request: 실제 최신 좌석 스냅샷을 예제 계약으로 변환한 dict
recommendations = recommend(model, current_request, k=5)
```

라이브러리에서는 모델을 프로세스 시작 시 한 번 로딩해 재사용한다. 신뢰하는 곳에서 생성한 joblib만 로딩한다. 이 패키지는 실시간 좌석 수집 서버나 HTTP API 자체를 구현하지 않으며 기존 백엔드에서 `recommend()`를 호출한다.

- 요청한 library/room에 속한 `status == "empty"` 좌석만 추천한다. `occupied/away/reserved`, 상태 누락·미확인은 제외한다.
- 120초를 초과한 스냅샷과 미래 시각을 거부한다. 운영 정책에 맞게 `max_age_seconds`를 조정할 수 있다.
- 필수 조건 → 모델 점수 → 상위 K 순서로 처리한다. 후보가 없으면 빈 리스트다.
- 출력 `score`는 모델별 순위 점수이며 확률이나 모델 간 공통 척도가 아니다. `preference_score`는 설명용 취향 일치도다.
- 추천은 예약을 보장하지 않는다. 예약 확정은 백엔드에서 해당 좌석의 최신 점유를 다시 확인하며 원자적으로 처리해야 한다.
- 기존 `preference_adapter.to_profile()` 결과의 `preferences`를 `user.preference_profile.preferences`, `requirements`를 `user.requirements`로 전달할 수 있다. 현재 모델은 생성기의 12개 공통 특성만 사용하며 features-2의 추가 특성은 모델에 반영되지 않는다. 합성과 실서비스 프로필 매핑 차이는 실제 데이터 재학습 시 점검한다.

## 검증

```bash
uv run python -m unittest discover -s recommender/tests -v
```

특성 누수 차단, 시간대별 밝기, 점유/필수 조건/다른 열람실/오래된 스냅샷 제외, 지표의 수기 기대값, 시간순 분할, 모든 모델 학습 및 저장·재로딩을 검증한다.

기존 로컬 JSONL에서 1,500개 세션 표본을 뽑아 실행한 smoke test: 사용 가능 1,271세션, 13,101후보, 검증/테스트 각 191세션. SGD 3 epoch, 트리 30회로 다섯 방법 모두 실행 완료했다. 이는 실행 확인용이며 165,000세션 전체 학습 결과가 아니다. MongoDB 경로는 연결 정보를 쓰지 않고 구현만 했으며 실제 DB 연결 검증은 하지 않았다.

구현 참고: [SGDClassifier의 partial_fit](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.SGDClassifier.html), [LightGBM의 query group 기반 LGBMRanker](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html).
