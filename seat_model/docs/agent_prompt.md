# 작업: 좌석 추천 학습 데이터 v2 — 하루 단위 시뮬레이션 기반으로 재설계

참고 문서·코드
- DB 스키마: `seat_model/docs/library_db_schema.md`
- v1 생성기: `data/libraries/scripts/generate_synthetic.py` (m_k·feature 정의의 기준)
- 서비스용 선호 변환기: `recommender/preference_adapter.py` (`to_profile`)

## 0. 이전 계획에서 바뀐 점 (먼저 읽을 것)
0단계 확인 결과와 1~5단계 계획 중 다음 내용은 **그대로 유지**한다.
- 효용 일치값 m_k 규칙(에어컨·콘센트·조명·창문·화장실·책상)
- desk_type 대응 규칙과 desk_layout 참값 확률
- 관측 노이즈 수치: direct 0.947 / contextual 0.849, 확신도 범위
- 사용자별 unknown 비율 유지
- 분할 방식: 사용자 층화 70/15/15 + 열람실 4개 홀드아웃 (연결 요청 처리는 §7)
- 조건부 로짓 직접 구현(L-BFGS)
- 위치: `seat_model/.venv`(uv), `seat_model/config/synth_v2.yaml`, `seat_model/synth_v2/`, `seat_model/reports/`, `seat_model/exports/ml_v2/`
  - 1단계 시작 시 `seat_model/exports/`를 루트 `.gitignore`에 추가한다(`.venv`는 이미 들어 있음).

**바뀐 결정**
1. **v1 세션은 재사용하지 않는다.** v1에서는 `synth_users`(user_id, archetype, 이용 도서관, 관측 프로필, `latent`의 방향·noise_aversion·rationality·rec_trust·partition 선호), `synth_scenarios`, `synth_seat_env`만 가져온다. 세션은 하루 단위 시뮬레이션으로 새로 만든다.
2. **요청마다 "선택한 좌석 + 그 순간 예약 가능했던 나머지 좌석 전체"를 저장한다.** 표본 추출로 줄이지 않는다.
3. **열람실의 물리 환경은 기간 내내 고정한다(열람실당 기준 시나리오 1개, §4.1).**
   - v1 시나리오 30개는 날씨뿐 아니라 책상·콘센트·창문·에어컨 배치까지 매번 새로 만든 것이다. 좌석 2,111개 모두 시나리오마다 `desk_type`과 `outletAvailable`이 바뀐다. 날마다 시나리오를 바꾸면 "같은 자리 재선택"이나 습관을 정의할 수 없다.
   - 대신 **같은 사용자가 방문할 때마다 다른 자리를 고를 수 있게 하는 요인**을 명시적으로 만든다. 요인은 실제 로그에서 관찰되거나 행동으로 드러나는 것으로만 만든다(§4.6).
     - 도착 시각(시간대별 조명), 날씨(창가 조도), 일 기온
     - 실시간 혼잡(평소 자리가 찼는지), 양옆 빈자리
     - 방문 목적, 노트북 지참, 예상 체류 시간
     - 요일·학사일정, 습관, 추천 노출
   - "그날의 기분" 같은 심리 변수는 만들지 않는다. 선택 노이즈(Gumbel)는 "기록되지 않은 요인"으로 유지한다.
4. **열람실마다 노트북 규정이 다르다는 점을 데이터셋에 넣는다(§2.5, §4.4).** 규정은 열람실 단위로 고정하고(기준 시나리오 기준), 출처를 함께 저장한다. 노트북을 지참한 사용자의 열람실 선택과 그날의 효용에 반영한다.
5. **현황은 정확한 값으로 만든다.** 공급 지연·미상·잔여석만 제공·좌석 충돌 이벤트는 이번 범위에서 제외하고, 확장할 수 있게 구조만 열어둔다.
6. 추천 노출과 노출 확률을 로그에 넣는다. 노출 관련 필드는 모두 별도 컬럼으로 분리한다.
7. 만족도는 **5점 리커트 척도**로 기록한다. 초기 메타데이터에도 리커트 기반 선호 설문을 추가한다.

**1단계를 시작하기 전에** 이 변경을 반영한 계획을 짧게 보고하고 승인받는다. 보고에 포함할 것:
- 컬렉션 구조와 예상 데이터 크기
- 보정 방법
- 열람실별 기준 시나리오 선택 결과
- 노트북 규정표(24개 열람실, 출처별 개수)
- 운영시간 기본값을 적용할 열람실 목록

## 1. 기간·규모·달력
- 기간: 2026-09-01 ~ 2026-10-31 (61일)
- 열람실: 합성 대상 24개(시나리오가 있는 active 열람실). `rooms`의 active 45개 중 좌석·시나리오가 있는 것만 쓴다.
- 사용자: 10,000명
  - 방문 빈도는 **새로 생성한다**: 주 2~5회 무작위, 주마다 ±1 변동 허용.
  - 기존 `visit_rate_per_week`(0.5~5, 33%가 주 2회 미만)는 쓰지 않는다. 의도된 변경이므로 리포트에 명시하고, 원값은 `synth_v2_truth_users.v1_visit_rate_per_week`에 참고용으로 남긴다.
- **운영시간**
  - `rooms.opening_hours.weekly`를 쓴다. 요일은 0=일요일이고, 빠진 요일은 휴관이다.
  - `opening_hours`가 null인 열람실(현재 11개: nl-digital 7개, seongnam-central, yongin-pogok 2개, yongin-sanghyeon:room:2)은 config `opening_hours_default`를 쓴다. 적용 목록을 리포트에 남긴다. 추정해서 채운 값은 `synth_v2_days.opening_hours_origin = "config_default"`로 구분한다.
  - `effective_from`/`effective_until`이 있으면 적용 기간 밖의 날짜에도 config 기본값을 쓰고 `opening_hours_origin = "config_default_out_of_effective_range"`로 기록한다. 예: yongin-dongbaek 3개 열람실은 `effective_from: 2026-10-12`이므로 9/1~10/11이 해당한다.
  - 지금 데이터는 값이 있는 13개 열람실이 모두 7일 운영으로 되어 있어 정기 휴관일이 없다. 이를 리포트에 적는다.
- **공휴일**
  - config `holidays`에 목록과 정책(`closed` 또는 `weekend_hours`)을 둔다. 기본 정책은 `closed`이다.
  - 기본 목록: 2026-09-24 ~ 09-26(추석), 10-03(개천절), 10-05(대체공휴일), 10-09(한글날)
- **학사 구분**
  - config `academic_calendar`: `normal` / `midterm`
  - 중간고사 기간 기본값은 2026-10-19 ~ 10-30이다.
- **시각 → time_slot 대응**
  - 조도 값이 `morning/noon/evening` 3개뿐이므로 경계를 config `time_slot_bounds`로 정한다.
  - 기본값: morning < 12:00 ≤ noon < 17:00 ≤ evening
- **도시(날씨 묶음)**
  - `libraries`에는 도시 필드가 없고 ID 파싱은 금지이며, 일부 `address`가 null이다(seongnam-central, yongin-dongbaek).
  - 그래서 config `library_city`에 도서관 → 도시 매핑을 명시한다.
  - 기본값: yongin-* 11곳 = `yongin`, seongnam-central = `seongnam`, nl-digital = `seoul`

## 2. 사용자 초기 메타데이터 (관측 = 모델 입력)
`synth_v2_users`에 저장한다.

1. **기존 관측 프로필**: `kakaotalk_profile` + `desk_layout` 카테고리 추가 (이전 계획과 같음). `is_correct_vs_latent`는 제거하고 truth로 옮긴다.
2. **온보딩 리커트 설문 (신규)**: 5점 척도 (1 = 전혀 아니다 ~ 5 = 매우 그렇다)
   - 중요도 문항 6개: "자리를 고를 때 {에어컨 바람/콘센트/책상 형태/조명 밝기/창가/화장실 거리}가 중요하다"
   - 방향 문항 (응답 방향과 참값의 대응을 config에 둔다)
     - 추위를 많이 탄다 → air_conditioning=warm 쪽
     - 밝은 곳이 좋다 → lighting=bright 쪽
     - 창가 자리가 좋다 → view=window 쪽
     - 화장실 가까운 자리가 좋다 → restroom=near 쪽
     - 칸막이 있는 자리가 좋다 → partition 선호 쪽
     - 넓은 책상이 좋다 → desk=large 쪽
     - 노트북을 자주 가져온다 → laptop_prior 쪽 (**신규**, 콘센트 방향을 직접 묻는 문항이 없어 보완)
   - 생성 규칙: 참 가중치와 참 방향을 순서형 응답으로 바꾼다(가중치 분위 → 1~5 구간). 여기에 응답 노이즈를 더한다(±1 이동 확률은 config).
   - 응답 결측(설문 미완료) 비율은 config로 둔다(기본 10%). 결측 사용자는 모든 문항을 null로 두고 `survey_completed: false`로 표시한다.
   - 같은 숫자를 연속으로 고르는 응답 패턴을 소량 넣는다(비율은 config). 이 사용자는 truth에만 `straightliner: true`로 표시한다.
3. **사전 메타데이터로 추정한 방문 맥락 (신규)**
   - `purpose_prior`: 방문 목적별 확률 `{exam_study, assignment_laptop, reading, other}`
   - `laptop_prior`: 노트북 지참 확률
   - 두 값은 관측 프로필, 리커트 응답, archetype에서 계산한다. 참 루틴과는 노이즈가 있는 관계로 만든다.
4. **`preference_profile_v2`, `requirements`**
   - 관측값만으로 계산한다.
   - 변환은 서비스와 맞추기 위해 **`recommender/preference_adapter.py`의 `to_profile`을 호출**해서 만든다.
   - v1 생성기의 대응표와는 다르다(warm일 때 airflow 목표값 0.05와 0.1, compact일 때 partition 항 유무). 이 차이를 리포트에 남긴다.
   - `desk_layout`은 features-1에 대응 feature가 없으므로 `unmapped`에 남는 것이 정상이다.
5. **홈 열람실 정보는 관측 테이블에 넣지 않는다.** 사용자에게는 `home_library_id`만 있다. 홈·대체 열람실 순위는 참값 루틴(§3)이며, 모델은 이력 feature(§5 candidates)로만 이를 본다.

## 2.5 열람실 규칙 테이블 (신규, 모델 입력)
`synth_v2_room_rules`에 열람실당 1행으로 저장한다. 노트북 규정을 열람실별로 다르게 반영하기 위한 테이블이다.

| 필드 | 설명 |
|---|---|
| `room_id`, `library_id` | |
| `laptop_allowed` | 시뮬레이션에서 쓰는 값(bool) |
| `laptop_rule_origin` | `real_usage_rules`(`rooms.usage_rules.laptop_allowed`) > `real_room_feature`(`room_features`의 `laptopAllowed`) > `synthetic`(기준 시나리오의 `conditions.laptop_allowed`) 순으로 우선 |
| `laptop_allowed_real` | 실데이터 값. 없으면 **null 그대로** 둔다(추정 금지). 현재 7개 열람실만 true이다 |
| `keyboard_allowed`, `keyboard_rule_origin` | 같은 규칙으로 정한다 |
| `base_scenario_id` | §4.1에서 고른 기준 시나리오 |

- 기존 `rooms`·`room_features`는 수정하지 않는다. 합성으로 채운 값은 이 테이블에만 있다.
- 리포트에 출처별 개수, 노트북 허용 열람실 수, 도서관마다 노트북 허용 열람실이 하나 이상 있는지를 남긴다.

## 3. 사용자 참값 (정답 생성 전용, 모델 입력 금지)
`synth_v2_truth_users`에 저장한다.
- **요소 가중치**: 디리클레(평균 0.28 / 0.23 / 0.18 / 0.14 / 0.10 / 0.07, 집중도 20) + archetype 보정 후 합 1로 정규화 (이전 계획)
- 방향, `partition` 선호, `desk_layout`, `noise_aversion`, `rationality`, `rec_trust`: 이전 계획과 같다.
- **주간 루틴**
  - 방문 요일 집합, 요일별 도착 시각 분포
  - 요일 유형별(평일 낮 / 평일 저녁 / 주말) 주 방문 목적
  - 목적별 체류 시간 분포
  - **홈 열람실**: `home_library_id`의 합성 대상 열람실 중 좌석 수 비례로 뽑는다.
  - **대체 열람실 순위**: 같은 도서관의 다른 열람실을 먼저, 그다음 `secondary_library_ids`의 열람실 순서이다. 합성 열람실이 1개뿐인 도서관(yongin-bora, yongin-suji, yongin-giheung, seongnam-central)은 대체 열람실이 다른 도서관이 된다. 대체 열람실이 없는 사용자 수를 리포트에 남긴다.
- **목적별 보정**: 그날의 요소 가중치 w_k와 m_desk 내부 비중을 구분해서 적용한다. 적용 후 w는 합 1로, m_desk 내부 비중도 합 1로 다시 정규화한다.

  | 목적 | w_k 배수 | m_desk 내부 비중 배수 (기본 layout 0.5 / 크기 0.3 / 칸막이 0.2) |
  |---|---|---|
  | assignment_laptop | 콘센트 ×1.8 | 크기 ×1.2 |
  | exam_study | 화장실 ×1.2 | layout ×1.5, 칸막이 ×1.5 |
  | reading | 조명 ×1.3, 창문 ×1.3 | – |
  | other | – | – |
- **노트북 지참**
  - 목적과 archetype으로 방문마다 정한다. 지참하지 않은 날은 w_outlet ×0.3 후 재정규화한다.
  - 지참했는데 들어간 열람실이 노트북 금지이면 그날은 노트북을 쓰지 않은 것으로 보고 같은 보정(×0.3)을 적용한다. 요청에 `laptop_rule_conflict: true`를 기록한다.
- **체류 시간 효과**: 예상 체류 3시간 이상이면 w_toilet, w_ac ×1.2 후 재정규화한다.
- **날씨·기온 효과 (신규, 관찰 가능한 요인)**
  - 일 기온이 config `hot_day_c` 이상이면 cool 방향 사용자의 w_ac ×1.2
  - `cold_day_c` 이하이면 warm 방향 사용자의 w_ac ×1.2
- **행동 성향**
  - 습관 강도 κ: 지난 방문의 좌석이 비어 있으면 κ, 같은 구역(desk_group)이면 κ_zone의 가산점. 분포는 config.
  - 양옆 빈자리 선호 강도
  - 혼잡 시 다른 열람실로 이동하는 임계값
  - `rec_trust`: 기존 값 유지
- 기타: `v1_visit_rate_per_week`(참고용), `straightliner`, `is_correct_vs_latent`(관측 노이즈 평가용)

## 4. 하루 시뮬레이션 (열람실 × 날짜, 시간 순서대로)
1. **기준 시나리오 고정 + `synth_v2_days`**
   - **기준 시나리오**: 열람실마다 1개를 골라 61일 전체에 쓴다.
     - 기본 규칙은 `season = spring_fall`이고 `weather = clear`인 시나리오 중 가장 작은 `scenario_index`이다. 없으면 `spring_fall`의 다른 날씨, 그다음 `summer` 순으로 고른다.
     - config `base_scenario_override`로 열람실별로 직접 지정할 수 있다.
     - 선택 결과와 대체 규칙이 적용된 열람실을 리포트에 남긴다.
   - `synth_v2_days`는 날짜 × 열람실당 1행이다.
     - 요일, 공휴일 여부, 학사 구분
     - 운영시간과 `opening_hours_origin`
     - 날씨: 같은 `library_city`는 같은 날 같은 날씨. 월별 확률은 config.
     - 일 기온: config의 날짜별 기준 곡선(9월 초 → 10월 말 하강) + 노이즈
     - `base_scenario_id`
   - **날씨·기온 반영 (환경 구조는 그대로 두고 측정값만 조정)**
     - 조도: `brightness_day[slot] = clamp(brightness_by_slot[slot] × (1 + γ[weather] × windowProximity))`. 기준 시나리오의 날씨를 γ = 0으로 두고, 나머지 날씨의 γ는 config에 둔다(v1 생성기의 낮빛 계수 clear 1.0 / cloudy 0.55 / rain 0.3에서 유도).
     - 이 식은 근사이므로 `brightness_origin: "weather_adjusted_approx"`로 표시한다.
     - 체감 온도: `temperature_day = raw.temperature_c − 기준 시나리오 base_temperature_c + 그날 일 기온 기반 실내 기준온도`
2. **입실·퇴실 사건**
   - 루틴에 따라 그날 방문하는 사용자의 도착 시각을 만든다. 공휴일이나 휴관일에 해당하는 방문은 만들지 않고, 건너뛴 수를 기록한다.
   - **배경 이용자**(로그 없음)를 추가해서 시간대별 혼잡 곡선을 맞춘다. 오전에 차고, 점심에 잠깐 빠지고, 저녁에 줄어들며, 시험 기간에는 더 붐빈다.
   - 배경 이용자의 좌석 선택은 인기도 기반이다. 인기도는 v1의 창가·콘센트·밝기 가중을 따르며 config로 둔다.
   - 잠시 자리 비움(`reserved_away`)과 운영상 막힌 좌석(`blocked`)을 넣는다.
3. **`synth_v2_seat_states`**: 상태가 바뀔 때만 저장한다.
   - 필드: `room_id, date, seat_id, state(available|occupied|reserved_away|blocked), from, to, occupant_type(user|background|null), occupant_id(user_id 또는 배경 이용자 id, blocked/available은 null), request_id(사용자 점유일 때)`
   - `from`/`to`는 `+09:00`이 붙은 ISO 8601 문자열이다. 구간은 `[from, to)` 반열린 구간이다. 운영 시작 시각에 모든 좌석의 첫 구간이 열린다.
   - 이 기록만으로 (`room_id`, 시각)에 대한 열람실 전체 상태를 복원할 수 있어야 한다.
4. **열람실 선택과 추천 요청**
   - **열람실 선택**: 사용자는 홈 열람실을 기본으로 고른다. 그날 노트북을 지참했고 홈 열람실이 노트북 금지(`synth_v2_room_rules.laptop_allowed = false`)이면, 대체 열람실 순위에서 노트북 허용 열람실을 config 확률로 먼저 고른다.
   - **예약 가능 좌석** `reservable_seat_ids`: 그 시각 상태가 `available`인 좌석 전체이다. 사용자별 조건은 넣지 않는다.
   - **`eligible_seat_ids`**: 예약 가능 좌석 중 사용자 `requirements`를 통과한 좌석이다.
     - 노트북 규정은 열람실 단위라서, 노트북 requirement가 있는 사용자는 결과가 열람실 전체 아니면 0석이다.
     - 0석이면 대체 열람실로 이동한다. 이동할 곳이 없으면 이용을 포기하고 `outcome: "abandoned_no_eligible"`로 기록한다.
   - **혼잡에 따른 이동**: 혼잡도가 사용자 임계값을 넘으면 대체 열람실로 이동할 수 있다. 이동하면 요청을 새로 기록하고 `prev_request_id`와 `chain_id`로 이전 요청과 연결한다.
5. **추천 노출 (로깅 정책)**
   - 관측값만 쓰는 규칙 점수로 eligible 좌석에서 Plackett-Luce 샘플링(온도 config)을 해서 5개를 노출한다. eligible이 5개 미만이면 전부 노출한다.
   - 규칙 점수는 adapter 프로필의 featureScore와 같은 식을 쓴다.
   - 저장할 값: 노출 목록의 확률, 좌석별 노출 포함 확률(몬테카를로 200회), 정책 버전
6. **선택**
   - `U = Σ w_k(그날)·m_k(그날 측정값) − λ·(1−entranceProximity) − 0.5·noise_aversion·noise_index + 습관항 + 양옆빈자리항 + rec_trust·노출순위보너스 + ε`
     - w_k(그날): §3의 목적·노트북·체류·기온 보정을 반영한 값
     - m_k의 조명은 `brightness_day[slot]`, 온도 관련 feature는 `temperature_day`를 쓴다.
     - ε ~ Gumbel(0, s / rationality)
   - 후보는 `eligible_seat_ids` 전체이다.
   - **실제 선택**: U의 최댓값. 로그에 남는 값이다.
   - **노출이 없었다면의 선택**: 노출 보너스를 뺀 U의 최댓값. 같은 ε를 쓴다. truth에만 저장한다.
   - **참 최적 좌석**: ε를 뺀 U의 최댓값(노출 보너스 포함). truth에만 저장한다.
   - **선택이 바뀐 이유 기록 (truth)**: 같은 사용자의 직전 방문과 좌석이 다르면 원인 후보를 플래그로 남긴다.
     - `prev_seat_unavailable`, `purpose_changed`, `laptop_changed`, `time_slot_changed`, `weather_changed`, `room_changed`, `exposure_effect`
     - 위 어느 것에도 해당하지 않으면 `noise_only`
7. **이용 결과**
   - 체류 중 혼잡도 변화를 반영해 사용 확정, 실제 퇴실 시각, 조기 퇴실(만족도가 낮을수록 확률 ↑)을 만든다.
   - 같은 방문 안에서 자리 이동이 일어나면 새 요청·선택으로 기록하고 `chain_id`로 연결한다.
8. **만족도 리커트(5점)**
   - 전체 만족 1문항 + 요소별 6문항(에어컨·콘센트·책상·조명·창문·화장실) + "다음에도 이 자리에 앉겠다" 1문항
   - 체험 효용 = ε를 뺀 U에서 노출 보너스도 뺀 값이다.
   - 생성 규칙: 선택 좌석의 체험 효용을 그 순간 eligible 좌석들 중 백분위로 바꾸고, 순서형 임계값 + 노이즈로 1~5에 대응시킨다. 요소별 문항은 해당 요소의 일치값과 중요도로 계산한다.
   - 응답률은 config(기본 1.0)로 두고, `responded` 필드를 남긴다.

## 5. 저장 구조
기존 컬렉션은 수정·삭제하지 않는다. `ml_v2_candidates`는 크기가 크므로 **Parquet 저장을 기본**으로 하고, MongoDB 저장은 config로 선택한다.

**이름 규칙**
- 모델 입력과 라벨: `ml_v2_*`
- 시뮬레이션 원장: `synth_v2_*` (truth 제외)
- 평가 전용 참값: **`synth_v2_truth_*`**
- 누설 검사는 `synth_v2_truth_*`의 모든 필드명이 입력 테이블에 없는지를 자동으로 확인한다.

| 저장 위치 | 한 행 | 주요 필드 | 모델 입력 |
|---|---|---|---|
| `synth_v2_users` | 사용자 | 관측 프로필, 온보딩 리커트, survey_completed, purpose_prior, laptop_prior, preference_profile_v2, requirements | ✅ |
| `synth_v2_room_rules` | 열람실 | laptop_allowed, laptop_rule_origin, laptop_allowed_real, keyboard_*, base_scenario_id | ✅ |
| `synth_v2_days` | 날짜 × 열람실 | 요일, 공휴일, 학사 구분, 운영시간(+origin), 날씨, 일 기온, base_scenario_id | ✅ |
| `synth_v2_seat_states` | 상태 구간 | room_id, date, seat_id, state, from, to, occupant_type, occupant_id, request_id | 특징 계산용 |
| `ml_v2_requests` | 추천 요청 | 아래 목록 참고 | ✅ |
| `ml_v2_exposures` | 요청 × 노출 좌석 | rank, 노출 포함 확률, 목록 확률, policy_version | ✅ (분리 컬럼) |
| `ml_v2_candidates` (Parquet) | 요청 × eligible 좌석 **전체** | 아래 목록 참고 | ✅ |
| `ml_v2_labels` | 추천 요청 | **chosen_seat_id**, **`remaining_seat_ids`**(eligible이지만 고르지 않은 좌석 전체), outcome, use_confirmed, stay_minutes, early_leave, moved_to_request_id | ❌ 정답 |
| `ml_v2_feedback` | 추천 요청 | 리커트 8문항, responded | ❌ 정답 |
| `synth_v2_truth_requests` | 추천 요청 | 좌석별 U·노이즈·노출 보너스, 그날 w_k, 노출이 없었다면의 선택, 참 최적 좌석, 참 방문 목적, 선택 변경 원인 플래그 | ❌ 평가 전용 |
| `synth_v2_truth_users` | 사용자 | 참 가중치, 루틴, 홈·대체 열람실, 습관 강도, 행동 성향, straightliner, is_correct_vs_latent, v1_visit_rate_per_week | ❌ 평가 전용 |
| `meta` `_id: synthetic_manifest_v2` | – | generator `synth-2.0`, seed, config 전체, 소스 컬렉션, 기준 시나리오 목록, 분할, 규모, 생성 시각 | 메타 |

**`ml_v2_requests` 주요 필드**
- 식별·시각: request_id, chain_id, prev_request_id, user_id, room_id, 요청 시각, 요일, time_slot, snapshot_id
- 좌석 집합: **`reservable_seat_ids`(전체)**, `eligible_seat_ids`
- 상황: 혼잡도, purpose_prior, laptop_prior, **`laptop_carried`(그날 실제 지참)**
- 노트북 규정: `room_laptop_allowed`, `laptop_rule_conflict`
- 이동 사유: `move_reason`(congestion / laptop_rule / no_eligible / in_visit_move / null)

**`ml_v2_candidates` 주요 필드**
- 정적 좌석 특징: features-1 + desk_layout one-hot. 조명은 `brightness_day[slot]`, 온도는 `temperature_day`를 쓴다.
- 열람실 규정: `room_laptop_allowed`
- 실시간 특징: 주변 점유 수, 양옆 빈자리, 혼잡도
- 관측 선호: 방향 × 확신도와 상호작용 항
- 이력: 지난 좌석·구역과 같은지, 이 좌석·구역 과거 선택 횟수
- 노출: 노출 여부, 순위
- 리커트 응답은 사용자 단위라 여기에 복사하지 않는다. `user_id`로 조인한다.

**`laptop_carried` 처리**
실제 서비스에서는 사전 메타데이터 추정값(`laptop_prior`)만 알 수 있으므로 두 값을 모두 저장한다. 모델 학습은 두 설정으로 돌릴 수 있게 둔다.
- (a) `laptop_carried` 사용
- (b) `laptop_prior`만 사용

## 6. 보정 (이 순서로 진행한다)
1. **혼잡 곡선**: 배경 이용자 수를 열람실·요일 유형·학사 구분별 목표 혼잡 곡선에 맞춘다. 예약 가능 좌석 수가 HitRate에 영향을 주므로 반드시 먼저 한다.
2. **노이즈 규모 s**
   - λ = 0.08, 소음 계수 0.5는 고정하고 s만 이분 탐색한다.
   - 목표: 참값을 아는 정답 모델(**ε만 뺀 U**, 노출 보너스·습관·양옆 항 포함)의 **HitRate@5 = 0.70**(허용 0.65~0.75). 학습 분할 요청으로 측정한다.
   - HitRate@1은 결과값으로 보고한다.
3. **선택 다양성 확인**: 같은 사용자의 연속 방문 중 같은 좌석을 다시 고른 비율이 config 허용 범위(기본 15~50%) 안에 있는지 확인한다. 범위를 벗어나면 κ 분포를 조정하고 2단계를 다시 한다. 조정 이력을 리포트에 남긴다.

## 7. 분할과 검증 (모두 통과해야 완료)
**분할**
- 사용자를 archetype별로 층화해 70/15/15로 나눈다.
- 열람실 4개 홀드아웃: 서로 다른 도서관에서 고른다.
  - 홀드아웃 열람실에서 생긴 요청과, 그 요청과 `chain_id`로 연결된 요청 전체를 학습·검증에서 뺀다.
  - 열람실 홀드아웃 평가는 학습 분할 사용자의 요청으로 한다.
- 이력 feature는 학습 요청의 이력만으로 계산한다. 홀드아웃 요청은 이력에 넣지 않는다.

**검증**
1. **스냅샷 정합성**: 모든 요청의 `reservable_seat_ids`가 `synth_v2_seat_states`로 (`room_id`, 요청 시각)을 복원한 결과와 정확히 같아야 한다.
2. 같은 `seat_id`의 occupied·reserved_away 구간이 서로 겹치지 않아야 한다(`occupant_id` 기준으로 확인).
3. 좌석 집합 관계:
   - `chosen_seat_id` ∈ `eligible_seat_ids` ⊆ `reservable_seat_ids`
   - `remaining_seat_ids` ∪ {chosen} = `eligible_seat_ids`
   - 노트북 requirement 사용자의 선택은 모두 `room_laptop_allowed = true`인 열람실에 있어야 한다.
4. **누설 검사**: 입력 테이블(`ml_v2_requests`, `ml_v2_exposures`, `ml_v2_candidates`, `synth_v2_users`, `synth_v2_room_rules`, `synth_v2_days`)에 다음 필드가 없어야 한다.
   - `synth_v2_truth_*`의 모든 필드명
   - `latent*`, U, 노이즈, 그날 w_k, 노출이 없었다면의 선택, 참 최적 좌석, 참 방문 목적, 루틴 원본, 습관 강도, `is_correct_vs_latent`, `straightliner`
5. **요일 효과 확인**: 같은 사용자가 요일 유형별로 고른 좌석의 특징 평균(콘센트, 칸막이 등)이 통계적으로 달라야 한다. 효과 크기를 보고한다.
6. **선택 다양성 (신규)**: 사용자별로 아래를 보고한다.
   - 방문 대비 서로 다른 좌석 수, 좌석 선택 엔트로피, 같은 좌석·같은 구역 재선택률
   - 선택 변경 원인 플래그별 비율(§4.6)
   - 노트북 지참 여부별로 고른 좌석의 콘센트 비율 차이
7. 열람실 이동 비율(사유별, 특히 `laptop_rule`), 노트북 규정 충돌률, 조기 퇴실률, 자리 이동률, 포기율을 보고한다.
8. 리커트 결과를 보고한다: 문항별 분포, 전체 만족도와 참 효용 백분위의 상관, 온보딩 설문과 참 가중치의 순위 상관. straightliner를 포함한 경우와 제외한 경우를 함께 낸다.
9. 노출 영향: 실제 선택과 "노출이 없었다면의 선택"이 다른 비율을 보고한다.
10. **조건부 로짓 순서 복원(켄달 타우)**: 아래 네 설정으로 각각 적합하고, 사용자 홀드아웃과 열람실 홀드아웃에서 비교한다.
    - (a) laptop_carried 사용
    - (b) laptop_prior만 사용
    - (c) 리커트 설문 추가
    - (d) 노출 컬럼 제외
11. 결과를 `seat_model/reports/synth_v2_report.md`에 정리한다. 데이터 크기(행 수, 용량), 기준 시나리오 목록, 노트북 규정표, config 기본값을 적용한 항목 목록도 포함한다.

## 8. 이번 범위에서 제외 (구조만 열어둘 것)
- 현황 공급 품질 저하: 관측 지연, 미상, 잔여석만 제공
- 좌석 충돌 이벤트: AVAILABILITY_CHANGED → 재추천, 다른 좌석, 포기
- 빈자리 예측 모델 (명세 범위 밖)
- 열람실 환경의 계절 교체(기준 시나리오를 기간 중 바꾸는 것). config에 `base_scenario_schedule` 키만 두고 이번에는 단일 시나리오로 고정한다.

`ml_v2_requests`에 `snapshot_id`, `observed_at`, `observation_age_s`(이번엔 0) 필드를 미리 두어, 나중에 공급 품질 저하를 추가할 때 구조를 바꾸지 않게 한다.

## 지켜야 할 원칙
- active 데이터만 사용한다. ID는 불투명 문자열로 다룬다(도시·열람실 구분도 ID를 파싱하지 않고 config 매핑을 쓴다).
- 좌표는 map별 픽셀이고 거리는 synth 미터값을 쓴다. 날짜 문자열은 변환해서 쓴다.
- null은 추정하지 않는다. 시뮬레이션에 필요해서 채운 값은 원본 null을 그대로 두고, 별도 필드에 `*_origin`(`config_default`, `synthetic` 등)과 함께 저장한다.
- 기존 컬렉션은 수정·삭제하지 않는다. 접속 정보는 남기지 않는다.
- 모든 생성은 시드로 고정하고, 수치는 config 한 곳에서 관리한다.
