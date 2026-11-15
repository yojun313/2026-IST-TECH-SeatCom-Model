# 합성 데이터 명세 (`synth-1.1`)

생성기: `libraries/scripts/generate_synthetic.py` · 기본 seed `20261114` · 출력 `libraries/synthetic/` · MongoDB `library.synth_*`

## 1. 목적과 범위

실측 환경 데이터(화장실 거리, 냉방기, 콘센트, 창문, 책상 형태, 조도)가 없어서 **실제 좌석 배치(real 11관 2,111석, 좌표·ID 그대로)** 위에 환경을 무작위로 여러 벌 합성했다. 그 위에서 카카오톡 분석 형식의 사용자 프로필과 추천·선택 이력을 생성해 다음 학습에 쓴다.

- 좌석 특성 예측/보간: 환경 객체 → 좌석 특성
- 선호 추론: 카카오톡 프로필(관측) → 잠재 선호(정답)
- 추천·랭킹: 세션별 노출·선택·비선택 좌석으로 learning-to-rank / choice model 학습

**합성 값은 실제 도서관 상태가 아니다.** 운영 추천에 쓰지 말고 `is_synthetic=true`로만 다룬다. 실데이터에서 가져온 것은 좌석 bbox·ID·라벨과, 같은 지도에 있는 출입구·화장실 후보(`origin="real_model_candidate"`), 열람실 노트북 규정(`laptop_rule_origin="real_room_feature"`)뿐이다.

## 2. 좌표·단위

- 좌표: 원본 지도 픽셀(top-left 원점, x→오른쪽, y→아래). 실좌석 `map_id`와 같다.
- 미터 환산: `synthetic_meters_per_px = 0.75 / median(좌석 버튼 짧은 변 px)`. 좌석 버튼 한 변을 0.75m로 **가정**했다. 실측 축척이 아니다.
- 열람실 경계(`room_bounds_px`): 좌석 외곽 + 1.5m 여백. 환경 객체는 이 경계의 벽에 놓인다.
- 거리: 보행거리는 맨해튼 거리. 화장실 거리는 좌석→출입구(맨해튼) + 출입구→화장실(직선×1.15)이다.

## 3. 생성 규칙 (열람실 단위 × 시나리오 30개)

| 객체 | 규칙 |
|---|---|
| 출입구 | 같은 지도에 실제 후보가 있으면 사용, 없으면 임의 벽 위 한 점(좌석과 0.8m 이상) |
| 창문 | 출입구가 없는 벽 1~3개에 벽 길이 40~90% 덮음. 1.5~3m 창 단위. 벽별 `view_score` 0.2~1, 20%는 블라인드 닫힘(채광 없음). 창 단위가 좌석 위를 지나면(지도 가장자리, 다른 열람실과 겹치는 벽) 두지 않음 |
| 화장실 | 실제 후보가 있으면 사용, 없으면 출입구 벽 바깥쪽(법선 ±80°)으로 3~25m에 1~2개. 열람실 영역(좌석 외곽+1.5m)에서 1.5m 이상, 같은 지도의 다른 열람실 좌석에서도 1.5m 이상 떨어진 곳만 허용 |
| 냉방기 | 60% 천장형 격자(6~9m 간격, 도달 3~5m), 40% 벽걸이(벽 1~2개, 5~8m 간격, 실내 방향 바람). 30% 확률로 스탠드형 추가(좌석에서 0.6m 이상 떨어진 통로·벽가). 벽걸이는 다른 열람실 좌석과 0.5m 안이면 두지 않음 |
| 조명 | 천장 격자 2.4~3.6m 간격, 기준 450~750lux. 5~12%는 고장·어두움(0~40%) |
| 콘센트 | 벽 2~4m 간격(85% 존재, 다른 열람실 좌석 위면 제외) + 책상 묶음별 책상 콘센트(시나리오마다 20~85% 묶음) |
| 책상 | 맞닿은 좌석 버튼을 한 책상 묶음으로 본다(union-find). 묶음 크기·창가 여부로 타입 선택 |
| 조건 | 계절(summer/winter/spring_fall) → 기준온도, 날씨(clear/cloudy/rain) → 채광 계수. 온도는 여름 냉방 −3.5·기류, 겨울 난방 +1.5·기류, 봄·가을 송풍 −1.0·기류 |

책상 타입: `individual_carrel`, `long_shared_table`, `four_person_table`, `wide_individual_desk`, `window_counter`, `standing_desk`. 타입마다 폭·높이 범위가 다르다. 칸막이(`partition`)는 `individual_carrel`(1인 칸막이 책상)만 1이고 나머지 타입은 모두 0이다.

조도 = 조명 커널 합(무한 격자 평균 합 π/2로 정규화, 실내 중앙 ≈ 기준 조도) + 창 채광 `E0·날씨·시간대·exp(-d/2.5m)`. 시간대는 morning 10시, noon 13시, evening 19시다.
기류 = 천장형은 `strength·exp(-(d/throw)²)`, 벽걸이는 `strength·cos²θ·exp(-d/throw)`, 0~1로 자른다.

### 세션(방문) 일정

- 사용자마다 하루 최대 1회 방문한다. 방문 확률은 `visit_rate_per_week/7`이다. 시간대는 사람마다 즐겨 가는 시간대(습관 가중치)에서 뽑는다.
- 기간은 기대 방문 수 합이 `--sessions`(목표값)가 되도록 정하고, 2026-11-13에 끝난다. 실제 세션 수는 목표와 조금 다르다.
- 계절은 날짜의 달로 정한다(6~8월 summer, 12~2월 winter, 나머지 spring_fall). 같은 열람실·같은 날의 세션은 그 계절의 시나리오 하나를 공유한다. 그래서 같은 날 같은 방의 날씨·환경이 같다.
- 한계: 같은 시각 같은 방에 있는 다른 합성 사용자의 선택 좌석은 `occupied_seat_ids`에 반영하지 않는다. 점유는 세션마다 따로 뽑는다.

### synth-1.0 → 1.1 변경

- 화장실·스탠드형 냉방기·출입구가 좌석 위에 놓이던 문제를 고쳤다. 1.0에서는 합성 화장실 949개 중 19%가 열람실 안에 있었고, 26개는 좌석 위였다. 스탠드형 냉방기는 54%가 좌석과 0.5m 안이었다.
- 창·벽걸이 냉방기·벽 콘센트가 다른 열람실 좌석을 지나던 문제를 고쳤다(국립 디지털도서관처럼 한 지도에 열람실이 겹쳐 있는 경우).
- 조명 정규화 상수 오류(2.6 → π/2)를 고쳤다. 1.0은 실내 조도가 기준의 약 60%라 `brightness` 중앙값이 0.22였고, 1.1은 0.39다.
- 겨울에 바람이 셀수록 온도가 내려가던 것을 난방으로 바꿨다.
- 세션: 1.0은 사용자를 무작위로 뽑아 하루에 여러 번 방문하거나 1시간 안에 다른 도서관에 가는 경우(56,226쌍)가 있었다. 또 9~10월 날짜에 여름·겨울 시나리오가 섞였다. 1.1은 위의 방문 일정으로 바꿨다.

## 4. 파일·필드

### synth_scenarios (열람실×시나리오, 720건)
`scenario_id`(`{library_id}:synth:{map}:{room}:s000`), `library_id`, `room_id`, `map_id`, `synthetic_meters_per_px`, `room_bounds_px`, `conditions{season,weather,base_temperature_c,laptop_allowed,keyboard_allowed,laptop_rule_origin}`, `objects[]`, `desk_groups[]`, `brightness_grid`, `counts`

- `objects[]`의 `type`: `entrance|window|toilet|air_conditioner|ceiling_light|outlet`. 점은 `point`, 창은 `line`으로 표현하고 `origin`을 함께 둔다.
- `brightness_grid`: 정오 lux 2D 배열 `values[row][col]`. 셀 크기는 약 1m(최대 4,000셀)이고 `origin_px`, `cell_px`로 픽셀 좌표로 바꾼다.

### synth_seat_env (시나리오×좌석, 63,330건)
- `_id` = `{scenario_id}|{seat_id}`. 실좌석 `seat_id`와 조인한다.
- `features`: features-1 레지스트리 12개, 0~1 정규화. 명세 §5.2 정규화식을 그대로 썼다.
  - 이진: `laptopAllowed`, `keyboardAllowed`, `partition`, `outletAvailable`(책상 콘센트 또는 1.2m 이내)
  - 연속: `windowProximity`, `toiletProximity`, `entranceProximity`, `brightness`(정오), `airflow`, `deskHeight`, `deskWidth`, `viewQuality`
- `brightness_by_slot{morning,noon,evening}`: 정규화 밝기
- `raw`: `dist_window_m`, `dist_toilet_walk_m`, `dist_entrance_walk_m`, `dist_outlet_m`, `dist_ac_m`, `airflow_index`, `temperature_c`, `lux{...}`, `noise_index`, `desk_group`, `desk_type`, `width_cm`, `height_cm`, `partition`, `desk_outlet`

### synth_users (3,000건)
- `archetype`: `laptop_worker|exam_focus|comfort_seeker|heat_sensitive|frequent_break|casual_reader`
- `home_library_id`, `secondary_library_ids`
- `kakaotalk_profile`: `kakao_analysis/schema/kakaotalk_schema.json`과 같은 형식. 7개 항목(restroom, lighting, air_conditioning, desk, laptop_usage, outlet, view)마다 `preference|confidence|inference_type|reason`이 있고, 정답 대비 정오 `is_correct_vs_latent`를 함께 둔다. 대화량(`text_richness`)에 따라 `unknown` 누락이 생기고, 직접 언급은 5%, 맥락 추론은 15% 확률로 반대값을 낸다.
- `preference_profile`: 카카오톡 프로필을 features-1 `{featureId,target,importance}`로 바꾼 값이다. 백엔드 M04/M05 입력 형식과 같다.
- `requirements`: 노트북 사용 확신 ≥0.6이면 `laptopAllowed eq 1`
- **`latent` (정답, 학습 라벨)**: `preferences`(진짜 7항목), `utility_weights{featureId:{target,weight}}`, `noise_aversion`, `rationality`(MNL β), `rec_trust`

카카오톡 → 피처 매핑:

| 항목 | 값 | featureId(target) |
|---|---|---|
| restroom | near | toiletProximity(1) |
| lighting | bright / dim | brightness(0.85 / 0.3) |
| air_conditioning | cool / warm | airflow(0.7 / 0.05) |
| desk | large / compact | deskWidth(1) / deskWidth(0.3)+partition(1) |
| laptop_usage | yes | laptopAllowed(1) |
| outlet | required | outletAvailable(1) |
| view | window | windowProximity(1)+viewQuality(1) |

잠재 효용에는 프로필에 드러나지 않는 항목도 있다. 입구 근접(30%), 칸막이(35%), 소음 회피가 그렇고, 모델은 이를 행동 이력에서 학습해야 한다.

### synth_sessions (163,306건)
`user_id`, `library_id`, `scenario_id`, `time_slot`, `occurred_at`(2026-06-26~2026-11-13, 141일. 기간은 `manifest.json`의 `period`), `occupancy_rate`, `eligible_count`, `ranking_basis`, `exposed[{seat_id,rank,engine_score}]`(베이스라인 top-5), `chosen_seat_id`, `choice_origin`(`recommendation|outsideRecommendations|null`), `chosen_true_utility`, `negative_seat_ids`(선택되지 않은 가용 좌석 최대 20개), `occupied_seat_ids`(그 시점 다른 사람이 사용 중이라 고를 수 없던 좌석 전체. 빈자리 전체 = 시나리오 좌석 − 이 목록), `chosen_preference_fit`(0~1), `satisfaction`(0~1 실수, 선택 없음이면 null)

생성 과정:
1. 점유: 인기 특성(창가·콘센트·밝음)이 있는 좌석일수록 먼저 차도록 무작위로 정한다.
2. 베이스라인 추천: 관측 프로필로 명세 §6.2 `featureScore`를 계산해 top-5를 뽑는다.
3. 선택: `rec_trust` 확률로 노출 좌석 안에서 고른다(순위 보너스 포함). 아니면 전체 가용 좌석에서 잠재 효용 MNL(β=`rationality`)로 고른다.
4. 만족도: 선택 좌석의 **절대** 취향 적합도다. 다른 빈자리와 비교하지 않으므로 취향에 맞을수록 높다.
   - `chosen_preference_fit` = Σ wᵢ·(1−|targetᵢ−xᵢ|) + a·(1−noise_index), 이를 (Σ wᵢ + a)로 나눈 값이다. wᵢ와 targetᵢ는 잠재 `utility_weights`이고, a = 0.5·`noise_aversion`이다. `true_utility`와 같은 항목을 0~1로 정규화한 것이다.
   - `satisfaction` = clip(`chosen_preference_fit` + N(0, 0.03), 0, 1). 노이즈 크기는 `SAT_NOISE` 상수로 조정한다.

`chosen_seat_id=null`(약 15%)은 노트북 사용자가 노트북 금지 열람실에 들어간 경우처럼 가능한 좌석이 없는 세션이다.

### synth_events (454,103건)
B07 이벤트 형식이다: `recommendationExposed`, `candidateSelected`, `useConfirmed`(70%), `ratingSubmitted`(40%, payload `satisfaction` 0~1), `candidateRejected`(5%). `session_id`로 세션과 조인한다. 선택 이력 기반 `choiceAffinity` 계산(최근 30일, 반감기 7일)을 재현할 수 있다.

## 5. 학습 사용 예

```python
import sys

sys.path.insert(0, "libraries/scripts")
from load import load_synthetic

env = {(r["scenario_id"], r["seat_id"]): r for r in load_synthetic("synth_seat_env")}
users = {u["user_id"]: u for u in load_synthetic("synth_users")}
for s in load_synthetic("synth_sessions"):
    if not s["chosen_seat_id"]:
        continue
    pos = env[(s["scenario_id"], s["chosen_seat_id"])]
    negs = [env[(s["scenario_id"], x)] for x in s["negative_seat_ids"]]
    x_user = users[s["user_id"]]["preference_profile"]  # 입력
    # (x_user, pos/negs의 features, time_slot) → pairwise/listwise 랭킹 학습
```

평가 split 권장: 사용자 기준 분리(`user_id` 해시), 또는 시나리오 기준 분리(새 환경 일반화).

## 6. 검증 결과 (seed 20261114, `--users 3000 --sessions 165000`, 2026-11-15 적재본)

- ID 중복 0건, 좌석·시나리오·세션·사용자 외래키 오류 0건, 피처 범위 위반 0건
- 시나리오별 좌석 수는 실좌석 수와 일치
- 세션 163,306건(방문 일정 164,750건 중 빈자리 없는 1,444건 제외). 사용자당 4~115건, 평균 54.4건(방문 빈도 `visit_rate_per_week`에 비례)
- 같은 사용자가 같은 날 두 번 방문한 경우 0건, 날짜 계절과 시나리오 계절이 다른 세션 0건, 한 열람실·하루에 시나리오가 둘 이상인 경우 0건
- 합성 화장실·출입구·창문·스탠드형 냉방기 가운데 좌석 위에 놓이거나 좌석을 가로지르는 것 0건
- 선택 세션 135,207건 중 추천 내 선택 110,285건, 추천 외 24,922건. 추천 1순위 선택 비율 35%
- 만족도(0~1, 135,207건): 평균 0.722, 표준편차 0.116, 범위 0.078~1.000. 적합도와의 상관 0.966
- 학습 코드 검사 결과와 기준 학습 성능: [recommender/TRAINING.md](../../../recommender/TRAINING.md)

## 7. 한계

- 축척, 벽, 창 위치는 가정이다. 실제 건물 구조와 다르다.
- 효용 함수와 사용자 행동은 설계한 모형이다. 이 데이터로 얻은 성능은 실사용 성능이 아니다.
- 흥덕(303쌍)·상현(90쌍)의 겹치는 좌석 사각형은 원본 그대로이며, 같은 책상 묶음으로 합쳐질 수 있다.
