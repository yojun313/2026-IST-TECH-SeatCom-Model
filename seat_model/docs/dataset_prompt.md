# 작업: 좌석 추천 학습 데이터셋 v2 구축 (데이터셋 구성 전용)

> 이 문서만 읽고 바로 작업을 시작할 수 있도록 쓴 실행 지시서다.
> **모델 학습은 이번 범위가 아니다.** 학습 가중치, 모델 비교, 순서 복원 평가(조건부 로짓 등)는 데이터셋이 완성된 뒤 별도 프롬프트에서 다룬다.

---

## 0. 한눈에 보기

### 0.1 목표
MongoDB `library`의 **실좌석(`seats`)** 위에서 61일 동안 열람실 이용을 하루 단위로 시뮬레이션한다. 추천 요청마다 아래 세 가지를 분리해서 저장한다.

| 구분 | 내용 | 모델 입력 |
|---|---|---|
| 입력 | 그 순간 고를 수 있었던 좌석 전체와 각 좌석의 속성 | ✅ |
| 정답 | 사용자가 실제로 고른 좌석. **반드시 MongoDB `seats` 컬렉션의 실좌석 레코드** | ❌ |
| 참값 | 정답을 만든 효용·노이즈. 평가 전용 | ❌ |

### 0.2 작업 순서 (체크리스트)
1. [ ] **계획 보고 후 승인받기**(쓰기 금지 단계). 보고할 내용:
   - 컬렉션 구조, 예상 데이터 크기(§12)
   - 보정 방법(§10)
   - 기준 시나리오 표(§3.2), 노트북 규정표(§3.3)
   - 운영시간 기본값을 적용할 열람실 목록(§4)
   - 일관성 파라미터 표(§6.2)
2. [ ] 환경 준비: `seat_model/.venv`, `seat_model/config/synth_v2.yaml`. 루트 `.gitignore`에 `seat_model/exports/`를 추가한다.
   - 무작위 생성은 모두 `synth_v2/dist.py`의 확률분포 함수로만 한다(§14).
3. [ ] 소스 적재 + 좌석 테이블 생성(§2, §3). `seats`와 조인이 100%인지 확인한다.
4. [ ] 사용자 생성(§5): 관측값과 참값을 분리한다. `--users`로 인원을 조절한다.
5. [ ] 소규모 실행(`--users 300 --days 7 --sims 1`)으로 구조 검증(§11의 1~4)과 분포 적합 검사(§11-10)를 통과시킨다.
6. [ ] 보정(§10): 혼잡 곡선 → 노이즈 규모 s → 재선택률 순서로 진행한다.
7. [ ] 본 실행. 기본값은 `--users 10000 --days 61 --sims 1`이다.
8. [ ] 전체 검증(§11)을 실행하고 리포트 `seat_model/reports/synth_v2_dataset_report.md`를 작성한다.

### 0.3 참고 자료
- DB 스키마: `seat_model/docs/library_db_schema.md`
- 이전 설계: `seat_model/docs/agent_prompt.md`. 이 문서와 다르면 **이 문서가 우선**이다.
- v1 생성기: `data/libraries/scripts/generate_synthetic.py`. 추가 사용자 생성에 `make_user`를 재사용한다.
- 서비스 선호 변환기: `recommender/preference_adapter.py`(`to_profile`)
- 접속: 저장소 루트 `.env`의 `MONGO_HOST/PORT/USER/PASSWORD/AUTH_DB`. 접속 정보를 코드·로그·리포트·Parquet 메타데이터에 남기지 않는다.

### 0.4 `agent_prompt.md` 대비 삭제한 것
- 방문 목적(`purpose_prior`, 목적별 가중치 보정)
- 방문마다 바뀌는 노트북 지참(사용자별 상수로 바꿈, §6.1)
- 기온에 따른 에어컨 가중치 보정
- 공통 비용항 `λ·(1−entranceProximity)`와 별도 소음항. 출입구와 소음은 8개 그룹 안으로 들어갔다.
- 학습 설정별 비교, 조건부 로짓 평가(다음 프롬프트로 넘김)

---

## 1. 소스 데이터 (읽기 전용)

| 컬렉션 | 쓰는 내용 | 비고 |
|---|---|---|
| `seats` | **정답 좌석의 원본.** seat_id, library_id, room_id, map_id, seat_label, external_seat_id, bbox, norm_bbox, verification, source_id | `dataset_status: "active"`만 사용 |
| `rooms` | 열람실명, `opening_hours`, `usage_rules.laptop_allowed` / `keyboard_allowed` | null은 추정하지 않음 |
| `room_features` | `laptopAllowed`, `keyboardAllowed`(실데이터 규정) | active만 사용 |
| `libraries` | 도서관명 | 도시 구분은 config 매핑을 씀 |
| `synth_scenarios` | 기준 시나리오 선택, `conditions`, `desk_groups` | |
| `synth_seat_env` | 좌석 속성(`features`, `raw`, `brightness_by_slot`) | 기준 시나리오의 행만 사용 |
| `synth_users` | v1 사용자 3,000명: archetype, 이용 도서관, `kakaotalk_profile`, `latent` | |

- 합성 대상 열람실은 `synth_scenarios`에 있는 24개(모두 active)이고, 이 열람실의 좌석은 2,111개다.
- 기존 컬렉션은 **수정·삭제하지 않는다**. 출력은 §8의 새 컬렉션과 Parquet에만 쓴다.

---

## 2. 좌석 데이터와 좌석 속성 (정답 라벨의 기준)

### 2.1 정답 = MongoDB `seats`의 실좌석
- 모든 정답 `chosen_seat_id`는 **MongoDB `seats._id`에 존재하고 `dataset_status: "active"`인 좌석**이어야 한다. 요청의 `room_id`와 좌석의 `room_id`도 같아야 한다.
- 정답 레코드(`ml_v2_labels`)에는 `seats`에서 **실행 시점에 직접 조회한** 좌석 원본을 `chosen_seat`으로 넣는다. `synth_seat_env`에서 복사하지 않는다.
  - `seat_id`, `library_id`, `room_id`, `map_id`
  - `seat_label`: 원본이 null이면 null 그대로 둔다. 번호를 추정하지 않는다.
  - `external_seat_id`, `bbox{x,y,width,height}`, `norm_bbox[4]`, `verification`, `source_id`
- 함께 넣는 것:
  - `chosen_seat_attributes`: 아래 11개 속성의 **그 요청 시점 값**
  - `seat_ref: {"collection": "seats", "_id": <seat_id>}`
- 후보 좌석(`ml_v2_candidates`)의 `seat_id`도 모두 `seats`에 있어야 한다(검증 §11-3).

### 2.2 데이터셋에 넣는 좌석 속성 11개 (첨부 표 기준)
요청 시점 값으로 계산해서 `ml_v2_candidates`의 모든 행과 `ml_v2_labels.chosen_seat_attributes`에 넣는다.

| # | 컬럼명 | 의미 | 원천 | 요청 시점 값 계산 | 연결된 사람 취향 (원하는 값) | 취향 관측 여부 |
|---|---|---|---|---|---|---|
| 1 | `toiletProximity` | 화장실 근접 | `features.toiletProximity` | 고정 | restroom: near (1) | 카카오톡 |
| 2 | `brightness` | 밝기 | `brightness_by_slot[slot]` | **시간대·날씨 반영** `brightness_day`(§7.1) | lighting: bright / dim (0.85 / 0.3) | 카카오톡 |
| 3 | `airflow` | 직접 바람 | `features.airflow` (= `raw.airflow_index`) | 고정 | air_conditioning: cool / warm (0.7 / 0.05) | 카카오톡 |
| 4 | `deskWidth` | 책상 폭 | `features.deskWidth` | 고정 | desk: large / compact (1 / 0.3) | 카카오톡 |
| 5 | `partition` | 칸막이 | `features.partition` | 고정 | desk: compact일 때 + 숨은 취향(사용자 35%) (1) | compact는 카카오톡, 나머지는 숨김 |
| 6 | `laptopAllowed` | 노트북 허용 | **`synth_v2_room_rules.laptop_allowed`**(§3.3) | 열람실 단위로 고정 | laptop_usage: yes (1). 필수 조건으로도 작동 | 카카오톡 |
| 7 | `outletAvailable` | 콘센트 | `features.outletAvailable` | 고정 | outlet: required (1) | 카카오톡 |
| 8 | `windowProximity` | 창문 근접 | `features.windowProximity` | 고정 | view: window (1) | 카카오톡 |
| 9 | `viewQuality` | 전망 | `features.viewQuality` | 고정 | view: window (1) | 카카오톡 |
| 10 | `entranceProximity` | 출입구 근접 | `features.entranceProximity` | 고정 | **숨은 취향만**(사용자 30%) (1) | 숨김 |
| 11 | `noise_index` | 소음 | `raw.noise_index` | 고정 | **숨은 취향만**(모든 사용자의 소음 회피 0~1). 낮을수록 좋음 | 숨김 |

- `laptopAllowed`는 `synth_seat_env.features.laptopAllowed`가 아니라 열람실 규칙 테이블 값을 쓴다. 두 값이 다른 좌석 수를 리포트에 남긴다.
- **보조 컬럼**(입력 허용, 속성 11개와 별도로 둔다):
  - 밝기 관련: `brightness_base_slot`(보정 전 시간대 값), `brightness_origin`
  - `temperature_day`(§7.1)
  - 책상 관련: `deskHeight`, `desk_type`, `desk_layout`, `desk_group`, `width_cm`, `height_cm`
    - `desk_layout` 대응: four_person_table과 long_shared_table → `shared_table`, 나머지 네 유형은 같은 이름
  - 거리: `dist_window_m`, `dist_toilet_walk_m`, `dist_entrance_walk_m`, `dist_outlet_m`, `dist_ac_m`
- 좌표는 map별 픽셀이므로 모델 입력에 넣지 않는다. 좌표는 라벨의 `chosen_seat.bbox`에만 둔다.

---

## 3. 열람실 환경: 기준 시나리오와 노트북 규정

### 3.1 원칙
- 열람실의 물리 환경(책상·콘센트·창문·에어컨 배치)은 기간 내내 **기준 시나리오 1개로 고정**한다.
- v1 시나리오 30개는 서로 배치가 다르다. 좌석 2,111개 모두 시나리오마다 `desk_type`과 `outletAvailable`이 바뀐다. 날마다 시나리오를 바꾸면 "같은 자리 재선택"을 정의할 수 없다.
- 날짜에 따라 바뀌는 것은 밝기(시간대·날씨)와 체감 온도뿐이다(§7.1).

### 3.2 기준 시나리오 선택 규칙과 0단계 계산 결과
- 규칙: `spring_fall/clear` → `spring_fall/cloudy` → `spring_fall/rain` → `summer/clear` → `summer/cloudy` → `summer/rain` 순서로 찾는다. 각 조합 안에서는 가장 작은 `scenario_index`를 고른다.
- config `base_scenario_override`로 열람실별로 직접 지정할 수 있다.
- 2026-11-14에 이 규칙으로 계산한 결과, 24개 열람실 모두 `spring_fall/clear`에서 골라졌다. 실행할 때 다시 계산하고, 결과가 이 표와 다르면 리포트에 적는다.

| room_id | 열람실명 | 좌석 | 기준 시나리오 | laptop_allowed | 출처 |
|---|---|---:|---|---|---|
| nl-digital:room:curved-desks | 곡선 책상 171~200구역 | 30 | s000 | false | synthetic |
| nl-digital:room:digital-left | 디지털열람석 1~84구역 | 84 | s002 | true | synthetic |
| nl-digital:room:digital-top-right | 상단 우측 85~170구역 | 86 | s002 | true | synthetic |
| nl-digital:room:laptop-bottom | 노트북·모바일이용석(하단) | 85 | s010 | true | real_usage_rules |
| nl-digital:room:media-data | 미디어자료이용석 | 36 | s000 | true | synthetic |
| nl-digital:room:media-edit | 미디어편집석 | 12 | s001 | false | synthetic |
| nl-digital:room:wonmun-db | 원문DB석 | 20 | s006 | false | synthetic |
| seongnam-central:room:3f-1-A | 3층1열람실(A구역) | 169 | s000 | true | synthetic |
| yongin-bora:room:reading-1f | 보라도서관 열람실 | 134 | s007 | false | synthetic |
| yongin-dongbaek:room:1 | 열람실(일반) | 156 | s002 | false | synthetic |
| yongin-dongbaek:room:2 | 열람실(청소년) | 102 | s018 | true | synthetic |
| yongin-dongbaek:room:4 | 노트북석 | 29 | s001 | true | real_usage_rules |
| yongin-giheung:room:1 | 1열람실 | 148 | s008 | true | synthetic |
| yongin-heungdeok:room:01 | 열람실 | 180 | s013 | true | synthetic |
| yongin-heungdeok:room:02 | 노트북실 | 56 | s009 | true | real_usage_rules |
| yongin-jukjeon:room:1 | 1열람실 | 180 | s004 | true | synthetic |
| yongin-jukjeon:room:2 | 2열람실 | 54 | s012 | false | synthetic |
| yongin-pogok:room:1 | 열람실 | 140 | s007 | true | synthetic |
| yongin-pogok:room:2 | 노트북실 | 19 | s001 | true | real_usage_rules |
| yongin-sanghyeon:room:1 | 열람실 | 180 | s003 | true | synthetic |
| yongin-sanghyeon:room:2 | 노트북실 | 30 | s008 | true | real_usage_rules |
| yongin-seonong:room:1 | 열람실 | 80 | s008 | true | synthetic |
| yongin-seonong:room:2 | 노트북실 | 32 | s000 | true | real_usage_rules |
| yongin-suji:room:1 | 2층노트북석 | 69 | s028 | true | real_usage_rules |

합계: 노트북 허용 18개(실데이터 7, 합성 11), 금지 6개(전부 합성)

### 3.3 열람실 규칙 테이블 `synth_v2_room_rules` (모델 입력)
| 필드 | 설명 |
|---|---|
| `room_id`, `library_id`, `room_name` | |
| `laptop_allowed` | 시뮬레이션에서 쓰는 값. 출처 우선순위: `rooms.usage_rules.laptop_allowed` → active `room_features.laptopAllowed` → 기준 시나리오 `conditions.laptop_allowed` |
| `laptop_rule_origin` | `real_usage_rules` / `real_room_feature` / `synthetic` |
| `laptop_allowed_real` | 실데이터 값. 없으면 **null 그대로** 둔다 |
| `keyboard_allowed`, `keyboard_rule_origin`, `keyboard_allowed_real` | 같은 규칙으로 정한다 |
| `base_scenario_id`, `base_scenario_rule` | 예: `"spring_fall/clear"` |
| `seat_count` | 이 열람실의 `seats` 수 |

---

## 4. 달력과 날짜 테이블 `synth_v2_days` (모델 입력)

날짜 × 열람실당 1행이다.

- **기간**: 2026-09-01부터 `--days`일(기본 61일, 10-31까지)
- **요일**: `day_of_week`는 0=일요일 ~ 6=토요일이다. `day_type`은 `weekday` / `weekend` / `holiday`다.
- **공휴일**: config `holidays`(기본 정책 `closed`)
  - 기본 목록: 09-24 ~ 09-26(추석), 10-03(개천절), 10-05(대체공휴일), 10-09(한글날)
- **학사 구분**: `normal` / `midterm`. 중간고사 기본 기간은 10-19 ~ 10-30이다.
- **운영시간**: `rooms.opening_hours.weekly`를 쓴다. 빠진 요일은 휴관이다.
  - null인 11개 열람실(nl-digital 7개, seongnam-central, yongin-pogok 2개, yongin-sanghyeon:room:2)은 config `opening_hours_default`를 쓰고 `opening_hours_origin = "config_default"`로 기록한다.
  - `effective_from`/`effective_until` 기간 밖의 날짜는 `"config_default_out_of_effective_range"`로 기록한다. 예: yongin-dongbaek 3개 열람실은 9/1~10/11이 해당한다.
  - 원본에서 그대로 쓴 값은 `"rooms.opening_hours"`로 기록한다.
- **날씨**: config `library_city`로 같은 도시면 같은 날 같은 날씨로 묶는다. ID를 파싱하지 않는다.
  - 도시: yongin 10곳, seongnam(seongnam-central), seoul(nl-digital)
  - 날씨 값: clear / cloudy / rain. 월별 확률은 config.
- **일 기온**: config의 날짜별 기준 곡선(9월 초 약 27℃ → 10월 말 약 15℃) + N(0, σ)
- 저장 필드: `date, room_id, library_id, city, day_of_week, day_type, academic_period, is_open, opens, closes, opening_hours_origin, weather, outdoor_temp_c, base_scenario_id`

---

## 5. 사용자 (`--users`로 인원 조절)

### 5.1 사용자 집합 만들기
| `--users` 값 | 처리 |
|---|---|
| ≤ 3,000 | v1 `synth_users`에서 archetype별 층화 표본을 시드로 고정해 뽑는다 |
| > 3,000 | v1 3,000명 전원 + 부족분을 v1 생성기 `make_user`로 새로 만든다 |

- 새로 만드는 사용자: `random.Random(seed + 7)`을 쓰고, active 도서관 11개를 넘긴다. ID는 `synth-user:03000`부터 이어서 붙인다.
- 모든 사용자에 `user_origin: "v1_synth_users" | "v2_generated"`를 기록한다.
- v1 `visit_rate_per_week`는 쓰지 않는다. 방문 빈도는 새로 생성한다(주 2~5회, 주마다 ±1 변동). 원값은 truth에 `v1_visit_rate_per_week`로 남긴다.

### 5.2 관측 메타데이터 `synth_v2_users` (모델 입력)
1. **카카오톡 관측 프로필**: v1 7개 카테고리와 노이즈 구조를 유지한다.
   - 참값과 일치할 확률: direct 0.947 / contextual 0.849
   - 확신도: direct 0.70~0.95 / contextual 0.35~0.70
   - 사용자별 unknown 비율을 유지한다.
   - `is_correct_vs_latent`는 truth로 옮긴다.
   - **넣지 않는 것**: 소음, 숨은 칸막이, 출입구 취향
2. **온보딩 리커트 설문(5점)** `onboarding_survey`
   - 중요도 문항 7개: 바람, 소음("조용한 자리가 중요하다"), 칸막이, 밝기, 책상폭, 창가·전망, 화장실
   - 방향 문항 5개
     - 추위를 많이 탄다 → warm
     - 밝은 곳이 좋다 → bright
     - 넓은 책상이 좋다 → large
     - 노트북을 자주 가져온다 → laptop_usage
     - 콘센트가 꼭 필요하다 → outlet
   - **출입구 문항은 넣지 않는다**(config `survey_include_entrance: false`).
   - 생성: 안정 선호(§6.2)에서 만든다. 가중치 분위를 1~5 구간으로 바꾸고, 확률 `survey_shift_prob`(기본 0.25)로 ±1 이동한다.
   - 결측 10%: `survey_completed: false`, 모든 문항 null
   - 같은 숫자를 연속 선택하는 응답 3%: truth에만 `straightliner: true`
3. `laptop_prior`: 관측 프로필과 리커트에서 계산한 노트북 지참 추정 확률
4. `preference_profile_v2`, `requirements`: `to_profile(kakaotalk_profile)`로 관측값만 써서 계산한다.
   - requirement 조건: laptop_usage = yes이고 확신도 ≥ 0.6이면 `laptopAllowed eq 1`
   - v1 대응표와의 차이(warm일 때 airflow 목표값 0.05와 0.1, compact일 때 partition 항 유무)를 리포트에 남긴다.
5. 저장 필드: `user_id, user_origin, archetype, home_library_id, secondary_library_ids, kakaotalk_profile_v2, onboarding_survey, survey_completed, laptop_prior, preference_profile_v2, requirements, split`

### 5.3 참값 `synth_v2_truth_users` (평가 전용)
- 안정 선호: 목표값 `t_uk`, 중요도 `w_uk`(§6.2)
- 숨은 취향 대상 여부: `hidden_partition`, `hidden_entrance`
- `noise_aversion`, `rationality`, `rec_trust`: v1 `latent`에서 가져온다. 새 사용자는 `make_user`가 만든 값을 쓴다.
- 참 `laptop_usage`, 참 `outlet`
- **루틴**
  - 방문 요일 집합, 주별 방문 횟수
  - 요일 유형별 도착 시각 분포
  - 체류 시간 분포
  - 홈 열람실: `home_library_id`의 합성 열람실 중 좌석 수 비례로 뽑는다.
  - 대체 열람실 순위: 같은 도서관 → `secondary_library_ids` 순서
- **행동 성향**
  - 습관 강도 κ(같은 좌석), κ_zone(같은 desk_group)
  - 양옆 빈자리 선호 η
  - 혼잡 이동 임계값 θ_move
- 기타: `straightliner`, `is_correct_vs_latent`, `v1_visit_rate_per_week`

### 5.4 분할 (사용자 단위, 모든 회차에 공통)
- archetype별로 층화해 70/15/15(`train/val/test`)로 나눈다. `synth_v2_users.split`에 저장한다.
- 열람실 홀드아웃 4개(서로 다른 도서관)를 고르고 manifest에 기록한다.
  - 홀드아웃 열람실 요청과, 그 요청과 `chain_id`로 연결된 요청에는 `room_holdout: true`를 표시한다.
  - 학습에서 빼는 것은 다음 프롬프트에서 처리한다. 이번 범위는 표시까지다.

---

## 6. 사람 취향 모델

### 6.1 바뀌지 않는 요인: 노트북 허용과 콘센트
같은 사람은 날짜·시간대·상황과 관계없이 모든 방문에서 똑같이 동작한다.
- **좌석 쪽**: `laptopAllowed`(열람실 규칙)와 `outletAvailable`(기준 시나리오)은 기간 내내 고정이다.
- **노트북**
  - 참 `laptop_usage = yes`인 사용자는 모든 방문에 노트북을 가져온다. `laptop_carried`는 사용자별 상수다.
  - 관측 requirement가 있으면 노트북 허용 열람실만 후보다. 홈 열람실이 금지면 대체 열람실로 간다(`move_reason: "laptop_rule"`).
  - 참값은 yes인데 관측 requirement가 없는 사용자는 홈 열람실이 금지일 때 확률 `p_laptop_room_switch`(기본 0.8)로 허용 열람실로 간다. 그대로 금지 열람실에 들어가면:
    - `laptop_rule_conflict: true`를 기록한다.
    - 그 방문의 효용에서 노트북 관련 항목(콘센트 항)만 0으로 둔다.
- **콘센트**
  - 참 `outlet = required`인 사용자는 매 방문 같은 고정 가중치 `w_outlet_fixed`(기본 0.30)를 갖는다.
  - 이 가중치는 일관성 메커니즘과 재정규화에서 제외한다.
  - config `outlet_as_hard_constraint: true`면 콘센트 없는 좌석을 eligible에서 뺀다. 기본값은 false다.

### 6.2 나머지 8개 그룹: 우선순위가 높을수록 일관적
**안정 선호 (사용자별, truth)**

| 그룹 | 바람 | 소음 | 칸막이 | 밝기 | 책상폭 | 창문+전망 | 화장실 | 출입구 |
|---|---|---|---|---|---|---|---|---|
| 우선순위 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| 디리클레 평균 | 0.22 | 0.18 | 0.15 | 0.13 | 0.11 | 0.09 | 0.07 | 0.05 |
| ρ_k (일관 확률) | 0.95 | 0.90 | 0.86 | 0.82 | 0.78 | 0.72 | 0.66 | 0.60 |
| σ_k (중요도 흔들림) | 0.10 | 0.14 | 0.18 | 0.22 | 0.26 | 0.30 | 0.34 | 0.38 |
| 일치값 m_k | `1−|airflow−t|` | `1−noise_index` | `partition` | `1−|brightness−t|` | `1−|deskWidth−t|` | `0.6·windowProximity+0.4·viewQuality` | `toiletProximity` | `entranceProximity` |
| 목표값 t | cool 0.7 / warm 0.05 | – | – | bright 0.85 / dim 0.3 | large 1 / compact 0.3 | – | – | – |

- 중요도 `w_uk`는 디리클레 `α = 평균 × 20`에서 뽑는다.
- archetype 보정 후 합 1로 정규화한다(배수는 config).
  - heat_sensitive: 바람 ×1.4
  - exam_focus: 칸막이·소음 ×1.3
  - comfort_seeker: 밝기·창문 ×1.2
  - frequent_break: 화장실 ×1.6
  - laptop_worker: 책상폭 ×1.2
  - casual_reader: 보정 없음
- 취향이 없는 그룹은 0으로 두고 나머지로 재정규화한다.
  - 해당: view = no_preference, restroom = no_preference, 칸막이 숨은 취향 비대상이면서 compact도 아님, 출입구 숨은 취향 비대상
  - 소음은 모든 사용자에게 있다. 강도는 `noise_aversion`으로 `w_noise`를 조정한 뒤 재정규화한다.
- 숨은 취향 비율(칸막이 35%, 출입구 30%)은 config 값이다.

**방문별 실현 선호 (일관성 메커니즘)**
방문마다 그룹 k에 대해 독립적으로 정한다.
- 확률 ρ_k로 일관된 방문: 목표값은 `t_uk`, 중요도는 `w_uk · exp(N(0, σ_k))`
- 확률 1−ρ_k로 벗어난 방문
  - 방향형(바람, 밝기, 책상폭): 확률 0.5로 목표값을 반대로 바꾼다(cool↔warm, bright↔dim, large↔compact). 나머지 경우는 중요도에 `U(0, 0.3)`을 곱한다.
  - 단방향형(소음, 칸막이, 창문+전망, 화장실, 출입구): 중요도에 `U(0, 0.3)`을 곱한다.
- 실현된 8개 중요도는 합 1로 재정규화한다(`w_outlet_fixed` 제외).
- config 검증: ρ가 우선순위 순으로 엄격히 감소하지 않거나 σ가 엄격히 증가하지 않으면 실행을 중단한다.

---

## 7. 하루 시뮬레이션

### 7.1 그날의 좌석 측정값
- 밝기: `brightness_day[slot] = clip(brightness_by_slot[slot] × (1 + γ[weather] × windowProximity), 0, 1)`
  - `γ[w] = day_f[w] / day_f[기준 시나리오 날씨] − 1`. `day_f`는 clear 1.0, cloudy 0.55, rain 0.3이다(v1 생성기 값).
  - 기준 날씨가 clear이면 cloudy −0.45, rain −0.70이다.
  - 근사식이므로 `brightness_origin: "weather_adjusted_approx"`로 표시한다.
- 시각 → slot: morning < 12:00 ≤ noon < 17:00 ≤ evening (config `time_slot_bounds`)
- 체감 온도: `temperature_day = raw.temperature_c − 기준 base_temperature_c + indoor_base(outdoor_temp_c)`. 저장만 하고 효용에는 쓰지 않는다.

### 7.2 진행 순서 (한 날짜의 24개 열람실을 하나의 시간순 이벤트 큐로 처리)
열람실 사이 이동이 있으므로 **열람실별로 따로 돌리지 않는다.**

1. 운영 시작 시각에 모든 좌석을 `available`로 연다.
   - 운영상 막힌 좌석은 `blocked`로 둔다. 비율은 config `blocked_rate`(기본 2%)이고, 하루 단위 또는 구간 단위다.
2. 이벤트를 큐에 넣는다.
   - 루틴에 따른 사용자 도착. 휴관일 방문은 만들지 않고, 건너뛴 수를 기록한다.
   - 15분 간격 혼잡 보정 틱
   - 퇴실, 자리 비움 시작·복귀
3. **혼잡 보정 틱**: 목표 혼잡 곡선(열람실·요일 유형·학사 구분별)과 현재 점유율의 차이만큼 배경 이용자를 입실·퇴실시킨다.
   - 곡선 모양: 오전에 차고, 점심에 잠깐 빠지고, 저녁에 줄어들며, 시험 기간에는 더 붐빈다.
   - 배경 이용자는 로그를 남기지 않는다. 좌석은 인기도(창가·콘센트·밝기 가중, config)로 고른다.
   - 사용자만으로 목표를 넘으면 배경은 0이다. 이 경우 `over_target_minutes`로 기록한다.
4. **사용자 도착 → 추천 요청 생성**
   1. 열람실 선택: 홈 열람실에서 시작한다. 노트북 규칙(§6.1)과 혼잡(점유율 > θ_move)에 걸리면 대체 열람실로 간다.
      - 이동할 때마다 새 요청을 만들고 `prev_request_id`, `chain_id`, `move_reason`으로 연결한다.
      - 이동할 곳이 없으면 `outcome: "abandoned_no_eligible"`이다.
   2. `reservable_seat_ids`: 그 시각 `available`인 좌석 전체. 사용자 조건과 무관하다.
   3. `eligible_seat_ids`: reservable 중 `requirements`(와 `outlet_as_hard_constraint`)를 통과한 좌석
   4. 추천 노출(§7.3) → 선택(§9) → 좌석을 `occupied`로 바꾸고 퇴실 이벤트를 넣는다.
5. **이용 중**
   - 자리 비움: `reserved_away` 구간을 만든다. 점유자는 그대로이고, 다른 사람은 고를 수 없다.
   - 조기 퇴실: 만족도가 낮을수록 확률이 높다.
   - 같은 방문 안 자리 이동: 확률은 config이고 만족도가 낮을수록 높다. 새 요청으로 기록하고 `move_reason: "in_visit_move"`를 남긴다.
6. 상태가 바뀔 때마다 `synth_v2_seat_states`에 구간을 닫고 연다.

### 7.3 추천 노출 (로깅 정책)
- 관측값만 쓰는 규칙 점수로 eligible 좌석에서 Plackett-Luce 샘플링을 해서 5개를 노출한다(온도 config). eligible이 5개 미만이면 전부 노출한다.
  - 규칙 점수: `preference_profile_v2`의 featureScore = `Σ importance·(1−|target−f|) / Σ importance`
  - 프로필이 비어 있으면 모든 좌석의 점수가 같다.
- 저장할 값:
  - 노출 목록의 로그 확률 `list_logprob`
  - 좌석별 노출 포함 확률 `inclusion_prob`(몬테카를로 200회)
  - `policy_version`(`"pl-rule-v1"`)

### 7.4 좌석 상태 원장 `synth_v2_seat_states`
- 필드: `sim_id, room_id, date, seat_id, state(available|occupied|reserved_away|blocked), from, to, occupant_type(user|background|null), occupant_id, request_id`
- `from`/`to`는 `+09:00`이 붙은 ISO 8601 문자열이고, 구간은 `[from, to)`이다. 비교할 때는 datetime으로 변환한다.
- 이 원장만으로 (`sim_id`, `room_id`, 시각)의 열람실 전체 상태를 복원할 수 있어야 한다(§11-1).

---

## 8. 저장 구조

### 8.1 이름 규칙
- 모델 입력과 라벨: `ml_v2_*`
- 시뮬레이션 원장: `synth_v2_*`
- 평가 전용 참값: `synth_v2_truth_*`

### 8.2 테이블
모든 행에 `sim_id`(회차)를 넣는다. 모든 문서에 `is_synthetic: true`, `verification: "synthetic"`, `generator_version: "synth-2.0"`을 붙인다.

| 저장 위치 | 한 행 | 주요 필드 | 모델 입력 |
|---|---|---|---|
| `synth_v2_users` | 사용자 | §5.2 | ✅ |
| `synth_v2_room_rules` | 열람실 | §3.3 | ✅ |
| `synth_v2_days` | 날짜 × 열람실 | §4 | ✅ |
| `synth_v2_seat_states` | 상태 구간 | §7.4 | 특징 계산용 |
| `ml_v2_requests` | 추천 요청 | 아래 목록 | ✅ |
| `ml_v2_exposures` | 요청 × 노출 좌석 | request_id, seat_id, rank, inclusion_prob, list_logprob, policy_version | ✅ (분리 컬럼) |
| `ml_v2_candidates` (Parquet) | 요청 × eligible 좌석 **전체** | 아래 목록 | ✅ |
| `ml_v2_labels` | 추천 요청 | 아래 목록 | ❌ 정답 |
| `ml_v2_label_rows` (Parquet) | 요청 × eligible 좌석 | request_id, seat_id, chosen(0/1) | ❌ 정답 |
| `ml_v2_feedback` | 추천 요청 | 리커트 11문항(§9.3), responded | ❌ 정답 |
| `synth_v2_truth_requests` | 추천 요청 | 아래 목록 | ❌ 평가 전용 |
| `synth_v2_truth_users` | 사용자 | §5.3 | ❌ 평가 전용 |
| `meta` `_id: "synthetic_manifest_v2"` | – | generator_version, seed, CLI 인자(users, sims, days), config 전체, 소스 컬렉션, 기준 시나리오, 홀드아웃 열람실, 보정 결과, 행 수·용량, 생성 시각 | 메타 |

**`ml_v2_requests` 필드**
- 식별: `sim_id, request_id, chain_id, prev_request_id, user_id, room_id, library_id`
- 시각: `requested_at, date, day_of_week, day_type, academic_period, time_slot, weather`
- 좌석 집합: `reservable_seat_ids`(전체), `eligible_seat_ids`, `n_reservable`, `n_eligible`, `occupancy_rate`
- 노트북: `room_laptop_allowed, laptop_carried, laptop_prior, laptop_rule_conflict`
- 이동: `move_reason`(null / congestion / laptop_rule / no_eligible / in_visit_move)
- 분할: `split, room_holdout`
- 공급 품질 확장용(이번엔 고정값): `snapshot_id, observed_at, observation_age_s`(0)

**`ml_v2_candidates` 컬럼**
- 키: `sim_id, request_id, seat_id, room_id`
- **좌석 속성 11개**(§2.2)와 보조 컬럼
- 실시간: `group_occupied_count`(같은 desk_group 점유 수), `group_empty_ratio`, `occupancy_rate`
- 관측 선호 × 확신도
  - `obs_air`(cool +, warm −), `obs_light`(bright +, dim −), `obs_desk`(large +, compact −)
  - `obs_view`, `obs_restroom`, `obs_outlet`, `obs_laptop`
  - 상호작용 항 `x_air = obs_air·(airflow−0.5)` 등
- 이력: `is_last_seat, is_last_zone, past_seat_count, past_zone_count`. 같은 회차 안의 과거 요청만으로 계산한다.
- 노출: `exposed, exposure_rank, inclusion_prob`
- 리커트는 `user_id`로 조인한다. 이 테이블에 복사하지 않는다.

**`ml_v2_labels` 필드**
- 키: `sim_id, request_id, user_id, room_id`
- 결과: `outcome`(chosen / moved_room / abandoned_no_eligible)
- 정답 좌석: `chosen_seat_id`, `chosen_seat`(§2.1 `seats` 원본), `chosen_seat_attributes`(속성 11개), `seat_ref`
- `remaining_seat_ids`: eligible 중 고르지 않은 좌석 전체
- 이용 결과: `use_confirmed, stay_minutes, left_at, early_leave, moved_to_request_id`
- `outcome ≠ chosen`이면 정답 좌석 관련 필드는 null이고 `remaining_seat_ids`는 eligible 전체다.

**`synth_v2_truth_requests` 필드**
- `realized_w`(8개), `realized_target`, `consistent`(그룹별 true/false/null), `w_outlet_fixed_applied`
- 좌석별 `U_det`(ε 제외), `eps`, `expo_bonus`, `habit`, `neighbor`
- `noise_scale`, `chosen_no_exposure_seat_id`, `oracle_seat_id`
- `change_flags`(§9.2), `experienced_utility_pct`

### 8.3 저장 형식
- Parquet가 기본이다: `seat_model/exports/ml_v2/{테이블}/sim_id={k}/part-*.parquet` (pyarrow, zstd). 용량을 맞추기 위해 날짜 단위로 파일을 나눈다.
- MongoDB에는 `--write-mongo`일 때만 쓴다. `ml_v2_candidates`와 `ml_v2_label_rows`는 config `mongo_write_candidates`(기본 false)가 켜졌을 때만 쓴다.
- 덮어쓰기는 `--overwrite`일 때만 한다. 허용 목록(§8.2의 새 컬렉션 이름)에 있는 컬렉션만 비우고, 그 밖의 컬렉션 이름이 나오면 즉시 중단한다.

---

## 9. 선택(정답 생성)과 만족도

### 9.1 효용
```
U(seat) = w_outlet_fixed · outletAvailable · [참 outlet=required] · [laptop_rule_conflict 아님]
        + Σ_{k∈8그룹} w_k(방문) · m_k(방문 목표값, 그날 측정값)
        + κ·[지난 좌석] + κ_zone·[지난 desk_group]
        + η · group_empty_ratio
        + rec_trust · β_rank · (6 − rank)/5 · [노출됨]
        + ε,      ε ~ Gumbel(0, s / rationality)
```
- 후보는 `eligible_seat_ids` 전체다.
- **실제 선택(정답)** = argmax U. `ml_v2_labels.chosen_seat_id`에 저장한다.
- 노출이 없었다면의 선택 = argmax(U − 노출 보너스). 같은 ε를 쓴다.
- 참 최적 좌석 = argmax(U − ε)
- 위 두 값은 truth에만 저장한다.

### 9.2 선택 변경 원인 플래그 (truth)
같은 사용자의 직전 방문(같은 회차)과 좌석이 다르면 해당하는 것을 모두 기록한다.
- `prev_seat_unavailable`, `room_changed`, `time_slot_changed`, `weather_changed`, `brightness_condition_changed`
- `preference_shift:{그룹}`: 그 방문에서 일관성 메커니즘으로 벗어난 그룹
- `exposure_effect`
- 위 어느 것에도 해당하지 않으면 `noise_only`

### 9.3 만족도 리커트 (5점, `ml_v2_feedback`)
- 문항
  - 전체 만족 1문항
  - 그룹별 9문항: 바람, 소음, 칸막이, 밝기, 책상폭, 창문·전망, 화장실, 출입구, 콘센트
  - "다음에도 이 자리에 앉겠다" 1문항
- 체험 효용 = U − ε − 노출 보너스. 그 방문의 실현 선호로 계산한다.
- 전체 만족: 체험 효용의 eligible 내 백분위 q를 쓴다. 순서형 임계값 `[0.2, 0.4, 0.6, 0.8]`에 노이즈 N(0, 0.1)을 더해 1~5로 정한다.
- 그룹별 문항: `m_k`와 실현 중요도로 만든다. 취향이 없는 그룹은 3(보통) 근처로 둔다.
- 응답률은 config(기본 1.0)이고 `responded` 필드를 둔다.

---

## 10. 보정 (이 순서로 진행한다)
1. **혼잡 곡선**: 배경 이용자 투입 규칙을 목표 곡선에 맞춘다. 시간대별 평균 점유율 오차가 ±5%p 이내여야 한다. 예약 가능 좌석 수가 HitRate에 영향을 주므로 반드시 먼저 한다.
2. **노이즈 규모 s**: 정답 모델(argmax U−ε)의 HitRate@5 = 0.70(허용 0.65~0.75)이 되게 이분 탐색한다.
   - 파일럿(7일, train 사용자, `sim_id=0`)의 요청별 `U_det`를 저장해 두고, s만 바꿔 ε를 다시 뽑아 빠르게 계산한다.
   - 본 실행 후 실제 값을 다시 측정해 리포트에 쓴다. HitRate@1도 함께 보고한다.
3. **재선택률**: 같은 좌석 재선택률이 15~50% 안에 들어오게 κ 분포를 조정한다. 조정 후 2단계를 다시 한다. 조정 이력을 manifest에 남긴다.
- `--skip-calibration --noise-scale 0.12`처럼 보정을 건너뛸 수 있다. 이 경우 manifest에 `calibrated: false`로 남긴다.

---

## 11. 검증 (모두 통과해야 완료)
1. **스냅샷 정합성**: 모든 요청의 `reservable_seat_ids`가 `synth_v2_seat_states`로 복원한 결과와 정확히 같아야 한다.
2. **좌석 구간**: 같은 좌석의 occupied·reserved_away 구간이 서로 겹치지 않아야 한다. 같은 좌석의 구간은 빈틈없이 이어져야 한다.
3. **정답 좌석 정합성 (MongoDB `seats` 기준)**
   - 모든 `chosen_seat_id`와 후보 `seat_id`가 `seats`에 있고 active여야 한다.
   - 요청 `room_id`와 좌석 `room_id`가 같아야 한다.
   - `chosen_seat`의 모든 필드가 `seats` 원본과 같아야 한다. 다시 조회해서 비교한다.
   - `chosen ∈ eligible ⊆ reservable`이고 `remaining ∪ {chosen} = eligible`이어야 한다.
   - `ml_v2_label_rows`에서 요청당 `chosen=1`은 정확히 1개(outcome=chosen일 때)여야 한다.
   - `chosen_seat_attributes`가 같은 요청의 candidates 행 값과 같아야 한다.
4. **누설 검사**: 입력 테이블(`ml_v2_requests`, `ml_v2_exposures`, `ml_v2_candidates`, `synth_v2_users`, `synth_v2_room_rules`, `synth_v2_days`)에 다음 필드가 없어야 한다. 필드명을 재귀로 훑어서 자동 검사한다.
   - `synth_v2_truth_*`의 모든 필드명, `latent*`
   - `U_det`, `eps`, `realized_*`, `consistent`, `hidden_*`, `w_outlet_fixed*`
   - `oracle_*`, `chosen_no_exposure*`, `is_correct_vs_latent`, `straightliner`, 루틴, κ, η, θ_move
5. **속성 계산 검증**: 표본 500행에서 확인한다.
   - `brightness`가 요청 time_slot·날씨 식(§7.1)과 같은지
   - `laptopAllowed`가 `synth_v2_room_rules`와 같은지
   - 나머지 9개 속성이 기준 시나리오 `synth_seat_env` 값과 같은지
6. **고정 요인**
   - 노트북 yes 사용자의 `laptop_carried`가 모든 방문에서 true인지
   - requirement 사용자의 선택이 모두 노트북 허용 열람실에 있는지
   - 콘센트 required 사용자의 고정 가중치 분산이 0인지
   - 콘센트 좌석 선택 비율
7. **일관성 순서**
   - truth: 그룹별 일관 방문 비율이 ρ_k ± 0.02 안에 있고 우선순위 순으로 감소하는지
   - 행동: 그룹별 고른 좌석 m_k의 사용자별 변동계수가 우선순위 순으로 커지는지(스피어만 ≥ 0.8)
8. **분포 리포트**
   - 관측 vs 숨김 사용자 수
   - 리커트 문항별 분포, 전체 만족도와 체험 효용 백분위의 상관
   - 온보딩 설문과 안정 중요도의 순위 상관(straightliner 포함·제외)
9. **행동 지표**
   - 열람실 이동(사유별), 규정 충돌률, 조기 퇴실률, 자리 이동률, 포기율
   - 노출 효과(실제 선택 ≠ 노출 없었다면의 선택)
   - 선택 변경 원인 플래그 분포
   - 요일 유형별 선택 좌석 특징 차이: 맨-휘트니 U와 Cohen's d
10. **분포 적합 검사 (§14)**: 생성값이 지정한 확률분포를 따르는지 `scipy.stats`로 검정한다. α = 0.001이며, 실패하면 완료가 아니다.
    - 베르누이 비율(`binomtest`): 숨은 칸막이·출입구, 그룹별 일관 비율 ρ_k, 관측 direct·일치 비율, 설문 결측·straightliner, 노트북 열람실 전환, 사용 확정
    - 로그정규(`kstest`): 사용자 체류 기대값, κ·κ_zone·η, 배경 체류. 자르기 전 원값으로 검정한다.
    - 굼벨(`kstest`): 표준화한 선택 노이즈 `eps / (s / rationality)`
    - 디리클레: 원 표본 평균이 config 평균 ±0.01
    - 베타: θ_move 표본의 평균이 `0.80 + 0.15·a/(a+b)` ±0.01
    - 날씨 마르코프 유지 비율, 기온 AR(1) 계수(추세 제거 후 1차 자기상관 0.7 ± 0.1)
    - 도착 시각 혼합 분포: 요일 유형별 히스토그램과 이론 혼합의 `chisquare`(30분 구간)
    - 포아송: 배경 입실 수의 평균/분산 비 1 ± 0.1
    - 소스 검사: `dist.py` 밖에서 전역 난수 호출이 0건인지(§15.6 grep)
    - 표본 통계와 이론값을 manifest `distributions`와 리포트 표로 남긴다.
11. 결과를 `seat_model/reports/synth_v2_dataset_report.md`에 정리한다. 포함할 것:
    - CLI 인자, 행 수·용량
    - 기준 시나리오 표, 노트북 규정표, config 기본값 적용 목록
    - 일관성 파라미터 표, 보정 결과
    - 분포 카탈로그(§14.3)의 이론값 대비 표본 통계

---

## 12. 데이터 규모 (`--users`, `--sims`, `--days`로 조절)
**추정식** (실제 값은 리포트에 기록한다)
- 요청 수 ≈ `users × 0.5(1일 평균 방문) × 운영일수 × 1.1(이동 포함) × sims`
- 후보 행 수 ≈ 요청 수 × 평균 eligible 좌석 수(약 45)

| 설정 | 요청 수 | 후보 행 | Parquet 용량(추정) | 용도 |
|---|---:|---:|---:|---|
| `--users 300 --days 7 --sims 1` | 약 1천 | 약 5만 | < 10 MB | 구조 검증 |
| `--users 3000 --days 61 --sims 1` | 약 9만 | 약 4백만 | 약 0.3~0.5 GB | 중간 규모 |
| `--users 10000 --days 61 --sims 1` (기본) | 약 31만 | 약 1천4백만 | 약 1~2 GB | 본 데이터 |
| `--users 10000 --days 61 --sims 3` | 약 92만 | 약 4천2백만 | 약 3~6 GB | 증강 |

- 사용자 1만 명이면 하루 약 5,000회 방문이 생긴다. 좌석이 2,111개뿐이라 사용자만으로 혼잡 목표를 넘는 시간대가 생길 수 있다. `over_target_minutes`와 포기율을 반드시 리포트에 쓴다.
- `--sims` 회차들은 사용자·루틴·분할은 같고 날마다의 무작위성만 다르다(회차 시드 = `seed + 1000·sim_id`). 회차마다 좌석 상태와 이력이 독립이다.

---

## 13. 예시 데이터셋 5건
> - **좌석 정보(seat_id, bbox, 속성값)는 실제 MongoDB 값이다.** 2026-11-14 조회, 각 열람실의 기준 시나리오 기준이다.
> - 사용자·요청·효용 값은 설명용으로 만든 것이다.
> - 지면을 줄이려고 후보 행은 일부만 보여 준다. 실제 데이터는 eligible 좌석 전체다.
> - 효용 값은 §9.1 식으로 계산했다(β_rank = 0.06, s는 보정 전 예시값).

### 공통: 정답 좌석 레코드 형식 (`ml_v2_labels`)
```json
{
  "sim_id": 0, "request_id": "<opaque>", "outcome": "chosen",
  "chosen_seat_id": "<seats._id>",
  "chosen_seat": {"seat_id": "...", "library_id": "...", "room_id": "...", "map_id": "...",
                  "seat_label": null, "external_seat_id": null,
                  "bbox": {"x": 0, "y": 0, "width": 0, "height": 0}, "norm_bbox": [0, 0, 0, 0],
                  "verification": "model_candidate", "source_id": "..."},
  "chosen_seat_attributes": {"toiletProximity": 0, "brightness": 0, "airflow": 0, "deskWidth": 0,
                             "partition": 0, "laptopAllowed": 0, "outletAvailable": 0,
                             "windowProximity": 0, "viewQuality": 0, "entranceProximity": 0,
                             "noise_index": 0},
  "seat_ref": {"collection": "seats", "_id": "<seats._id>"},
  "remaining_seat_ids": ["..."]
}
```

### 예시 1. 일관된 방문 + 습관 (보라도서관, 평일 오전, 노트북 금지 열람실)
- **상황**
  - exam_focus 사용자, 노트북을 쓰지 않는다.
  - 8개 그룹 모두 일관된 방문이다.
  - 지난번 좌석(0021)이 비어 있어 습관 가산점이 붙었다.
- **요청** `ml_v2_requests`
```json
{"sim_id":0,"request_id":"req:b5af6ab12e5a590c","chain_id":"chn:3fce4a2a8b49e785","prev_request_id":null,
 "user_id":"synth-user:00412","room_id":"yongin-bora:room:reading-1f","library_id":"yongin-bora",
 "requested_at":"2026-09-08T09:40:12+09:00","date":"2026-09-08","day_of_week":2,"day_type":"weekday",
 "academic_period":"normal","time_slot":"morning","weather":"clear",
 "n_reservable":79,"n_eligible":79,"occupancy_rate":0.41,
 "reservable_seat_ids":["yongin-bora:seat:0001","yongin-bora:seat:0021","yongin-bora:seat:0064","yongin-bora:seat:0072","... (79개)"],
 "eligible_seat_ids":["... reservable과 같음(requirements 없음)"],
 "room_laptop_allowed":false,"laptop_carried":false,"laptop_prior":0.18,"laptop_rule_conflict":false,
 "move_reason":null,"split":"train","room_holdout":false,
 "snapshot_id":"snap:997d768aa1a1d667","observed_at":"2026-09-08T09:40:12+09:00","observation_age_s":0}
```
- **사용자 관측값** `synth_v2_users` 일부
```json
{"user_id":"synth-user:00412","user_origin":"v1_synth_users","archetype":"exam_focus",
 "kakaotalk_profile_v2":{"preferences":{
   "air_conditioning":{"preference":"cool","confidence":0.85,"inference_type":"direct"},
   "lighting":{"preference":"bright","confidence":0.55,"inference_type":"contextual"},
   "desk":{"preference":"compact","confidence":0.5,"inference_type":"contextual"},
   "laptop_usage":{"preference":"no","confidence":0.8,"inference_type":"direct"},
   "outlet":{"preference":"unknown","confidence":0.0,"inference_type":"unknown"},
   "view":{"preference":"window","confidence":0.6,"inference_type":"contextual"},
   "restroom":{"preference":"unknown","confidence":0.0,"inference_type":"unknown"}}},
 "onboarding_survey":{"imp_airflow":5,"imp_noise":4,"imp_partition":4,"imp_brightness":4,"imp_deskWidth":2,"imp_window":3,"imp_toilet":1,
   "dir_cold_sensitive":2,"dir_bright":4,"dir_wide_desk":2,"dir_laptop":1,"dir_outlet_needed":1},
 "survey_completed":true,"requirements":[],"split":"train"}
```
- **후보** `ml_v2_candidates` (4행 발췌; 11개 속성은 실제 DB 값, `brightness` = morning·맑음)
```json
{"request_id":"req:b5af6ab12e5a590c","seat_id":"yongin-bora:seat:0021","toiletProximity":0.836,"brightness":0.584,"airflow":0.516,"deskWidth":0.333,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.853,"viewQuality":0.559,"entranceProximity":0.761,"noise_index":0.22,"brightness_base_slot":0.584,"brightness_origin":"weather_adjusted_approx","desk_type":"individual_carrel","desk_layout":"individual_carrel","desk_group":10,"width_cm":100,"is_last_seat":1,"is_last_zone":1,"past_seat_count":3,"exposed":1,"exposure_rank":2,"inclusion_prob":0.71,"obs_air":0.85,"obs_light":0.55,"obs_desk":-0.5,"obs_view":0.6}
{"request_id":"req:b5af6ab12e5a590c","seat_id":"yongin-bora:seat:0001","toiletProximity":0.826,"brightness":0.67,"airflow":0.407,"deskWidth":0.267,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.853,"viewQuality":0.559,"entranceProximity":0.741,"noise_index":0.403,"desk_type":"individual_carrel","desk_group":0,"is_last_seat":0,"is_last_zone":0,"exposed":1,"exposure_rank":1,"inclusion_prob":0.78}
{"request_id":"req:b5af6ab12e5a590c","seat_id":"yongin-bora:seat:0072","toiletProximity":0.8,"brightness":0.262,"airflow":0.96,"deskWidth":0.375,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.0,"viewQuality":0.018,"entranceProximity":0.689,"noise_index":0.184,"desk_type":"four_person_table","desk_layout":"shared_table","desk_group":35,"exposed":1,"exposure_rank":3,"inclusion_prob":0.52}
{"request_id":"req:b5af6ab12e5a590c","seat_id":"yongin-bora:seat:0064","toiletProximity":0.89,"brightness":0.414,"airflow":0.894,"deskWidth":0.45,"partition":0,"laptopAllowed":0,"outletAvailable":0,"windowProximity":0.51,"viewQuality":0.237,"entranceProximity":0.87,"noise_index":0.446,"desk_type":"four_person_table","desk_layout":"shared_table","desk_group":31,"exposed":0,"exposure_rank":null,"inclusion_prob":0.04}
```
- **정답** `ml_v2_labels`
```json
{"sim_id":0,"request_id":"req:b5af6ab12e5a590c","user_id":"synth-user:00412","room_id":"yongin-bora:room:reading-1f","outcome":"chosen",
 "chosen_seat_id":"yongin-bora:seat:0021",
 "chosen_seat":{"seat_id":"yongin-bora:seat:0021","library_id":"yongin-bora","room_id":"yongin-bora:room:reading-1f","map_id":"yongin-bora:map:seat-layout-1f",
   "seat_label":"6","external_seat_id":null,"bbox":{"x":38.0,"y":237.5,"width":34,"height":28},"norm_bbox":[0.038,0.339286,0.034,0.04],
   "verification":"model_candidate","source_id":"yongin-bora:source:seatmate_page"},
 "chosen_seat_attributes":{"toiletProximity":0.836,"brightness":0.584,"airflow":0.516,"deskWidth":0.333,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.853,"viewQuality":0.559,"entranceProximity":0.761,"noise_index":0.22},
 "seat_ref":{"collection":"seats","_id":"yongin-bora:seat:0021"},
 "remaining_seat_ids":["yongin-bora:seat:0001","yongin-bora:seat:0064","yongin-bora:seat:0072","... (78개)"],
 "use_confirmed":true,"stay_minutes":214,"left_at":"2026-09-08T13:14:40+09:00","early_leave":false,"moved_to_request_id":null}
```
- **만족도** `ml_v2_feedback`
```json
{"request_id":"req:b5af6ab12e5a590c","responded":true,"overall":5,"g_airflow":5,"g_noise":4,"g_partition":5,"g_brightness":4,"g_deskWidth":4,"g_window":4,"g_toilet":3,"g_entrance":3,"g_outlet":3,"sit_again":5}
```
- **참값** `synth_v2_truth_requests` 일부
```json
{"request_id":"req:b5af6ab12e5a590c",
 "realized_w":{"airflow":0.27,"noise":0.21,"partition":0.19,"brightness":0.15,"deskWidth":0.10,"window":0.08,"toilet":0.0,"entrance":0.0},
 "realized_target":{"airflow":"cool","brightness":"bright","deskWidth":"compact"},
 "consistent":{"airflow":true,"noise":true,"partition":true,"brightness":true,"deskWidth":true,"window":true,"toilet":null,"entrance":null},
 "w_outlet_fixed_applied":0.0,"noise_scale":0.12,
 "seats":{"yongin-bora:seat:0021":{"U_det":0.9495,"eps":-0.012,"expo_bonus":0.0298,"habit":0.08},
          "yongin-bora:seat:0001":{"U_det":0.822,"eps":0.031,"expo_bonus":0.0372,"habit":0.0},
          "yongin-bora:seat:0072":{"U_det":0.7384,"eps":0.044,"expo_bonus":0.0223,"habit":0.0},
          "yongin-bora:seat:0064":{"U_det":0.5356,"eps":-0.02,"expo_bonus":0.0,"habit":0.0}},
 "chosen_no_exposure_seat_id":"yongin-bora:seat:0021","oracle_seat_id":"yongin-bora:seat:0021",
 "change_flags":[],"experienced_utility_pct":1.0}
```

### 예시 2. 노트북 필수 조건 + 콘센트 고정 가중치 (수지도서관 노트북석, 토요일 낮)
- **상황**
  - laptop_worker 사용자. 관측 requirement(`laptopAllowed eq 1`)가 있어서 노트북 허용 열람실(실데이터 규정)만 갈 수 있다.
  - 참 `outlet=required`라서 `w_outlet_fixed = 0.30`이 매 방문 같다.
  - 숨은 출입구 취향 대상이다.
- **요청**
```json
{"sim_id":0,"request_id":"req:04dc2395443b235f","chain_id":"chn:c05a8f0afcccad5e","prev_request_id":null,
 "user_id":"synth-user:01877","room_id":"yongin-suji:room:1","library_id":"yongin-suji",
 "requested_at":"2026-09-19T13:05:44+09:00","date":"2026-09-19","day_of_week":6,"day_type":"weekend","academic_period":"normal",
 "time_slot":"noon","weather":"clear","n_reservable":22,"n_eligible":22,"occupancy_rate":0.68,
 "reservable_seat_ids":["yongin-suji:seat:0036","yongin-suji:seat:0047","yongin-suji:seat:0048","yongin-suji:seat:0067","... (22개)"],
 "eligible_seat_ids":["... 22개(노트북 허용 열람실이라 전부 통과)"],
 "room_laptop_allowed":true,"laptop_carried":true,"laptop_prior":0.93,"laptop_rule_conflict":false,
 "move_reason":null,"split":"train","room_holdout":false,"observation_age_s":0}
```
- **열람실 규칙** `synth_v2_room_rules`
```json
{"room_id":"yongin-suji:room:1","library_id":"yongin-suji","room_name":"2층노트북석","laptop_allowed":true,"laptop_rule_origin":"real_usage_rules",
 "laptop_allowed_real":true,"base_scenario_id":"yongin-suji:synth:2F-notebook-seatbutton:1:s028","base_scenario_rule":"spring_fall/clear","seat_count":69}
```
- **후보** (발췌)
```json
{"request_id":"req:04dc2395443b235f","seat_id":"yongin-suji:seat:0036","toiletProximity":0.633,"brightness":0.16,"airflow":0.258,"deskWidth":0.633,"partition":0,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.546,"viewQuality":0.137,"entranceProximity":0.591,"noise_index":0.0,"desk_type":"wide_individual_desk","desk_layout":"wide_individual_desk","width_cm":136,"group_empty_ratio":1.0,"exposed":1,"exposure_rank":2,"obs_laptop":0.9,"obs_outlet":0.8,"obs_air":-0.75,"obs_desk":0.7}
{"request_id":"req:04dc2395443b235f","seat_id":"yongin-suji:seat:0048","toiletProximity":0.701,"brightness":0.659,"airflow":0.461,"deskWidth":0.225,"partition":1,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.812,"viewQuality":0.254,"entranceProximity":0.726,"noise_index":0.369,"desk_type":"individual_carrel","exposed":1,"exposure_rank":1}
{"request_id":"req:04dc2395443b235f","seat_id":"yongin-suji:seat:0067","toiletProximity":0.812,"brightness":0.107,"airflow":0.406,"deskWidth":0.558,"partition":0,"laptopAllowed":1,"outletAvailable":0,"windowProximity":0.0,"viewQuality":0.041,"entranceProximity":0.948,"noise_index":0.616,"desk_type":"wide_individual_desk","exposed":0}
```
- **정답**
```json
{"sim_id":0,"request_id":"req:04dc2395443b235f","outcome":"chosen","chosen_seat_id":"yongin-suji:seat:0036",
 "chosen_seat":{"seat_id":"yongin-suji:seat:0036","library_id":"yongin-suji","room_id":"yongin-suji:room:1","map_id":"yongin-suji:map:2F-notebook-seatbutton",
   "seat_label":"36","external_seat_id":"36","bbox":{"x":200.0,"y":446.7,"width":27.0,"height":37.0},"norm_bbox":[0.2,0.637233,0.027,0.052782],
   "verification":"model_candidate","source_id":"yongin-suji:source:official_seatmate_sj"},
 "chosen_seat_attributes":{"toiletProximity":0.633,"brightness":0.16,"airflow":0.258,"deskWidth":0.633,"partition":0,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.546,"viewQuality":0.137,"entranceProximity":0.591,"noise_index":0.0},
 "seat_ref":{"collection":"seats","_id":"yongin-suji:seat:0036"},"remaining_seat_ids":["yongin-suji:seat:0047","yongin-suji:seat:0048","yongin-suji:seat:0067","... (21개)"],
 "use_confirmed":true,"stay_minutes":305,"early_leave":false}
```
- **참값 요약**
  - `realized_w = {airflow 0.30, noise 0.24, brightness 0.12, deskWidth 0.22, entrance 0.12}`, 목표 warm / dim / large
  - `w_outlet_fixed_applied = 0.30`
  - 좌석별 `U_det`: 0036 = 1.1374, 0048 = 0.8747, 0047 = 0.8728, 0067 = 0.6187(콘센트 없음)
  - 실제 선택 = 노출이 없었다면의 선택 = 참 최적 = 0036
  - `change_flags: ["time_slot_changed"]`(지난 방문은 저녁), overall 5

### 예시 3. 평소 자리가 참 + 선호가 벗어난 방문 (국립중앙도서관 디지털열람석, 수요일 저녁, 비)
- **상황**
  - 운영시간 null 열람실이라 config 기본값을 쓴다.
  - 평소 자리(003)는 이미 다른 사람이 쓰고 있다.
  - 이번 방문은 바람 그룹이 일관성 메커니즘으로 벗어나서 cool → warm으로 뒤집혔다.
  - 비 오는 저녁이라 창가 밝기가 크게 낮아졌다: 003은 0.214 → 0.092.
  - 정답 모델은 002를 1순위로 보지만, ε 때문에 074가 선택됐다.
- **날짜** `synth_v2_days`
```json
{"date":"2026-10-14","room_id":"nl-digital:room:digital-left","library_id":"nl-digital","city":"seoul","day_of_week":3,"day_type":"weekday",
 "academic_period":"normal","is_open":true,"opens":"09:00","closes":"22:00","opening_hours_origin":"config_default",
 "weather":"rain","outdoor_temp_c":17.8,"base_scenario_id":"nl-digital:synth:plan-2020:digital-left:s002"}
```
- **요청**: `time_slot: "evening"`, `requested_at: "2026-10-14T19:20:03+09:00"`, `n_reservable: 31`, `room_laptop_allowed: true`(합성), `laptop_carried: false`
- **후보** (발췌, `brightness` = evening × (1 − 0.70·windowProximity))
```json
{"request_id":"req:9a6a6bee496b0267","seat_id":"nl-digital:seat:digital-left-074","toiletProximity":0.765,"brightness":0.157,"airflow":0.141,"deskWidth":0.133,"partition":0,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.019,"viewQuality":0.071,"entranceProximity":0.633,"noise_index":0.259,"brightness_base_slot":0.159,"desk_type":"long_shared_table","desk_layout":"shared_table","is_last_seat":0,"is_last_zone":0,"exposed":1,"exposure_rank":3}
{"request_id":"req:9a6a6bee496b0267","seat_id":"nl-digital:seat:digital-left-002","toiletProximity":0.808,"brightness":0.094,"airflow":0.272,"deskWidth":0.308,"partition":1,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.814,"viewQuality":0.517,"entranceProximity":0.719,"noise_index":0.091,"brightness_base_slot":0.218,"desk_type":"individual_carrel","is_last_seat":0,"is_last_zone":1,"exposed":1,"exposure_rank":1}
{"request_id":"req:9a6a6bee496b0267","seat_id":"nl-digital:seat:digital-left-047","toiletProximity":0.774,"brightness":0.095,"airflow":0.396,"deskWidth":0.217,"partition":0,"laptopAllowed":1,"outletAvailable":0,"windowProximity":0.268,"viewQuality":0.132,"entranceProximity":0.652,"noise_index":0.121,"brightness_base_slot":0.117,"desk_type":"long_shared_table","exposed":1,"exposure_rank":2}
```
- **정답**
```json
{"request_id":"req:9a6a6bee496b0267","outcome":"chosen","chosen_seat_id":"nl-digital:seat:digital-left-074",
 "chosen_seat":{"seat_id":"nl-digital:seat:digital-left-074","library_id":"nl-digital","room_id":"nl-digital:room:digital-left","map_id":"nl-digital:map:plan-2020",
   "seat_label":"74","external_seat_id":null,"bbox":{"x":674.0,"y":431.5,"width":16.5,"height":13.5},"norm_bbox":[0.603943,0.553205,0.014785,0.017308],
   "verification":"model_candidate","source_id":"nl-digital:source:plan-bin0007"},
 "chosen_seat_attributes":{"toiletProximity":0.765,"brightness":0.157,"airflow":0.141,"deskWidth":0.133,"partition":0,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.019,"viewQuality":0.071,"entranceProximity":0.633,"noise_index":0.259},
 "seat_ref":{"collection":"seats","_id":"nl-digital:seat:digital-left-074"},"stay_minutes":96,"early_leave":true}
```
- **참값 요약**
  - `realized_target.airflow = "warm"`(안정값 cool), `consistent.airflow = false`
  - `realized_w = {airflow 0.36, noise 0.17, brightness 0.16, deskWidth 0.06, window 0.03, toilet 0.12, entrance 0.10}`
  - 좌석별 `U_det`: 002 = 0.7106, 074 = 0.6839, 047 = 0.6246
  - `oracle_seat_id = 002`, 실제 선택 = 074(ε: 074 +0.035, 002 −0.010)
  - `change_flags: ["prev_seat_unavailable","preference_shift:airflow","time_slot_changed","weather_changed","brightness_condition_changed"]`
  - 체험 효용 백분위 0.67 → overall 4

### 예시 4. 혼잡으로 열람실 이동 (동백도서관 일반 → 청소년 열람실, 중간고사 기간)
- **상황**
  - 홈 열람실(room:1, 노트북 금지)의 점유율이 0.94로 사용자 임계값 θ_move = 0.85를 넘었다.
  - 같은 도서관의 room:2로 이동했다. 요청 2개가 `chain_id`로 연결된다.
  - 운영시간은 `effective_from` 2026-10-12 이후라 원본 값을 쓴다.
- **요청 1** (이동 전)
```json
{"request_id":"req:02c1d9c157aafa3f","chain_id":"chn:071f2a27e19b766b","prev_request_id":null,"user_id":"synth-user:00733",
 "room_id":"yongin-dongbaek:room:1","requested_at":"2026-10-21T12:31:09+09:00","academic_period":"midterm","time_slot":"noon","weather":"clear",
 "n_reservable":9,"n_eligible":9,"occupancy_rate":0.94,"room_laptop_allowed":false,"move_reason":null}
```
- **정답 1**: `{"request_id":"req:02c1d9c157aafa3f","outcome":"moved_room","chosen_seat_id":null,"chosen_seat":null,"remaining_seat_ids":["... 9개 전체"],"moved_to_request_id":"req:5bebb6053010d231"}`
- **요청 2** (이동 후)
```json
{"request_id":"req:5bebb6053010d231","chain_id":"chn:071f2a27e19b766b","prev_request_id":"req:02c1d9c157aafa3f","user_id":"synth-user:00733",
 "room_id":"yongin-dongbaek:room:2","requested_at":"2026-10-21T12:34:40+09:00","academic_period":"midterm","time_slot":"noon","weather":"clear",
 "n_reservable":27,"n_eligible":27,"occupancy_rate":0.74,"room_laptop_allowed":true,"laptop_carried":false,"move_reason":"congestion"}
```
- **후보** (발췌)
```json
{"request_id":"req:5bebb6053010d231","seat_id":"yongin-dongbaek:seat:0173","toiletProximity":0.673,"brightness":0.879,"airflow":0.62,"deskWidth":0.158,"partition":1,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.798,"viewQuality":0.399,"entranceProximity":0.672,"noise_index":0.217,"desk_type":"long_shared_table","desk_layout":"shared_table","group_empty_ratio":0.5,"exposed":1,"exposure_rank":2}
{"request_id":"req:5bebb6053010d231","seat_id":"yongin-dongbaek:seat:0208","toiletProximity":0.758,"brightness":0.908,"airflow":0.512,"deskWidth":0.242,"partition":1,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.812,"viewQuality":0.156,"entranceProximity":0.841,"noise_index":0.323,"desk_type":"long_shared_table","exposed":1,"exposure_rank":1}
{"request_id":"req:5bebb6053010d231","seat_id":"yongin-dongbaek:seat:0185","toiletProximity":0.706,"brightness":0.206,"airflow":0.793,"deskWidth":0.283,"partition":1,"laptopAllowed":1,"outletAvailable":0,"windowProximity":0.363,"viewQuality":0.134,"entranceProximity":0.737,"noise_index":0.255,"desk_type":"individual_carrel","exposed":1,"exposure_rank":3}
```
- **정답 2**
```json
{"request_id":"req:5bebb6053010d231","outcome":"chosen","chosen_seat_id":"yongin-dongbaek:seat:0173",
 "chosen_seat":{"seat_id":"yongin-dongbaek:seat:0173","library_id":"yongin-dongbaek","room_id":"yongin-dongbaek:room:2","map_id":"yongin-dongbaek:map:seat_layout:2",
   "seat_label":null,"external_seat_id":null,"bbox":{"x":258.75,"y":542.0,"width":25,"height":20},"norm_bbox":[0.25875,0.774286,0.025,0.028571],
   "verification":"model_candidate","source_id":"yongin-dongbaek:src:seatmate2"},
 "chosen_seat_attributes":{"toiletProximity":0.673,"brightness":0.879,"airflow":0.62,"deskWidth":0.158,"partition":1,"laptopAllowed":1,"outletAvailable":1,"windowProximity":0.798,"viewQuality":0.399,"entranceProximity":0.672,"noise_index":0.217},
 "seat_ref":{"collection":"seats","_id":"yongin-dongbaek:seat:0173"},"stay_minutes":240,"early_leave":false}
```
- `seat_label`이 null인 좌석이다. 원본 그대로 null을 유지하고 번호를 추정하지 않는다.
- **참값 요약**
  - `U_det`: 0173 = 0.9053, 0208 = 0.8446, 0185 = 0.7504
  - 실제 선택 = 참 최적 = 0173
  - `change_flags: ["room_changed"]`, overall 5

### 예시 5. 노트북 규정 충돌 + 노출 효과 (죽전도서관 2열람실, 화요일 저녁, 흐림)
- **상황**
  - 참 `laptop_usage = yes`, `outlet = required`인 사용자다. 카카오톡에서 노트북 언급이 없어서(`unknown`) 관측 requirement가 없다.
  - 홈 열람실 room:2(노트북 금지, 합성 규정)에 들어갔다(`p_laptop_room_switch` 표본에서 이동하지 않음). 그래서 `laptop_rule_conflict = true`이고 콘센트 항은 0이다.
  - 추천 1순위(2-046)가 선택됐지만, 노출이 없었다면 2-033을 골랐을 것이다.
- **요청**
```json
{"request_id":"req:663499e41aafd0ef","user_id":"synth-user:02291","room_id":"yongin-jukjeon:room:2","requested_at":"2026-10-06T18:02:27+09:00",
 "day_of_week":2,"day_type":"weekday","time_slot":"evening","weather":"cloudy","n_reservable":18,"n_eligible":18,"occupancy_rate":0.67,
 "room_laptop_allowed":false,"laptop_carried":true,"laptop_prior":0.41,"laptop_rule_conflict":true,"move_reason":null}
```
- **노출** `ml_v2_exposures`
```json
{"request_id":"req:663499e41aafd0ef","seat_id":"yongin-jukjeon:seat:2-046","rank":1,"inclusion_prob":0.64,"list_logprob":-6.92,"policy_version":"pl-rule-v1"}
{"request_id":"req:663499e41aafd0ef","seat_id":"yongin-jukjeon:seat:2-048","rank":2,"inclusion_prob":0.58,"list_logprob":-6.92,"policy_version":"pl-rule-v1"}
{"request_id":"req:663499e41aafd0ef","seat_id":"yongin-jukjeon:seat:2-012","rank":3,"inclusion_prob":0.21,"list_logprob":-6.92,"policy_version":"pl-rule-v1"}
```
- **후보** (발췌, `brightness` = evening × (1 − 0.45·windowProximity))
```json
{"request_id":"req:663499e41aafd0ef","seat_id":"yongin-jukjeon:seat:2-046","toiletProximity":0.524,"brightness":0.09,"airflow":0.532,"deskWidth":0.258,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.806,"viewQuality":0.264,"entranceProximity":0.437,"noise_index":0.28,"brightness_base_slot":0.142,"desk_type":"individual_carrel","exposed":1,"exposure_rank":1}
{"request_id":"req:663499e41aafd0ef","seat_id":"yongin-jukjeon:seat:2-033","toiletProximity":0.726,"brightness":0.119,"airflow":0.48,"deskWidth":0.65,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.0,"viewQuality":0.003,"entranceProximity":0.842,"noise_index":0.489,"brightness_base_slot":0.119,"desk_type":"wide_individual_desk","exposed":0,"exposure_rank":null}
{"request_id":"req:663499e41aafd0ef","seat_id":"yongin-jukjeon:seat:2-048","toiletProximity":0.524,"brightness":0.053,"airflow":0.653,"deskWidth":0.258,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.716,"viewQuality":0.21,"entranceProximity":0.438,"noise_index":0.25,"desk_type":"individual_carrel","exposed":1,"exposure_rank":2}
```
- **정답**
```json
{"request_id":"req:663499e41aafd0ef","outcome":"chosen","chosen_seat_id":"yongin-jukjeon:seat:2-046",
 "chosen_seat":{"seat_id":"yongin-jukjeon:seat:2-046","library_id":"yongin-jukjeon","room_id":"yongin-jukjeon:room:2","map_id":"yongin-jukjeon:map:room2-seatmate",
   "seat_label":null,"external_seat_id":null,"bbox":{"x":118.0,"y":428.5,"width":28,"height":24},"norm_bbox":[0.118,0.612143,0.028,0.034286],
   "verification":"model_candidate","source_id":"yongin-jukjeon:source:seatmate-room2"},
 "chosen_seat_attributes":{"toiletProximity":0.524,"brightness":0.09,"airflow":0.532,"deskWidth":0.258,"partition":1,"laptopAllowed":0,"outletAvailable":1,"windowProximity":0.806,"viewQuality":0.264,"entranceProximity":0.437,"noise_index":0.28},
 "seat_ref":{"collection":"seats","_id":"yongin-jukjeon:seat:2-046"},"stay_minutes":71,"early_leave":true}
```
- **참값 요약**
  - `w_outlet_fixed_applied = 0`(규정 충돌)
  - `U_det`: 2-046 = 0.7034(노출 보너스 0.0528 포함), 2-033 = 0.7350
  - `chosen_no_exposure_seat_id = 2-033`, `oracle_seat_id = 2-033`
  - `change_flags: ["exposure_effect","weather_changed"]`
  - 체험 효용 백분위 0.5 → overall 3. 조기 퇴실했다.

---

## 14. 확률분포 기반 생성 규칙 (완전 랜덤 금지)

### 14.1 원칙
- 데이터셋의 모든 무작위 값은 **이름 있는 확률분포**에서 뽑는다. 분포의 파라미터는 `synth_v2.yaml`에 둔다.
- "아무 값이나 고르는" 균등 추출은 아래 카탈로그에 균등분포로 명시된 항목에만 쓴다.
- 모든 추출은 `synth_v2/dist.py`의 함수를 거친다. 다른 모듈에서 다음을 직접 호출하지 않는다.
  - `random` 모듈
  - `np.random.rand` 같은 전역 함수
  - `Generator` 메서드
  - `validate.py`가 소스 코드를 grep해서 자동으로 확인한다.
  - 예외: §5.1의 추가 사용자 생성은 v1 `make_user`(파이썬 `random.Random`)를 그대로 재사용한다.
- 분포끼리 계층을 이룬다.
  - **모집단 분포**(config 평균)가 사용자 파라미터를 낳는다.
  - **사용자 파라미터**(디리클레·로그정규·베타)가 방문별 값을 낳는다.
  - 그래서 사람마다 성향이 다르면서도 같은 사람은 일관된 패턴을 보인다.
- 뽑은 값과 그 분포의 이론값이 맞는지 생성 후 검정한다(§11-10).

### 14.2 난수 스트림과 재현성
- `np.random.Generator`(PCG64)만 쓴다.
- 스트림은 용도와 위치로 시드를 나눈다: `np.random.default_rng([seed, STREAM, sim_id, date_ordinal, entity_index])`
  - `STREAM`: USERS=1, CALENDAR=2, SIM=3, CALIBRATION=4, OBSERVATION=5, FEEDBACK=6
  - 처리 순서나 병렬화와 관계없이 같은 인자면 같은 값이 나온다.
- ID는 `hashlib.sha1`로 만든 재현 가능한 문자열이다. 순번에 의존하지 않는다.
  - `request_id = "req:" + sha1("{sim_id}|{user_id}|{room_id}|{requested_at}")[:16]`
  - `chain_id = "chn:" + sha1("{sim_id}|{user_id}|{첫 요청 시각}")[:16]`
  - `snapshot_id = "snap:" + sha1("{sim_id}|{room_id}|{시각}")[:16]`
  - 배경 이용자: `"bg:" + sha1("{sim_id}|{room_id}|{입실 시각}|{k}")[:16]`
- 시각: `zoneinfo.ZoneInfo("Asia/Seoul")`로 만들고 `isoformat(timespec="seconds")`로 `+09:00` 문자열을 저장한다.

### 14.3 확률분포 카탈로그
| # | 생성 대상 | 분포 | 파라미터 (config 키) | numpy 함수 |
|---|---|---|---|---|
| **사용자 (안정 성향)** |||||
| 1 | 8개 그룹 안정 중요도 `w_uk` | 디리클레 Dir(α = 평균 × 20) → archetype 배수 → 재정규화 | `groups.dirichlet_mean`, `groups.concentration` | `rng.dirichlet` |
| 2 | 숨은 칸막이·출입구 취향 대상 | 베르누이(0.35 / 0.30) | `groups.hidden.*` | `rng.random(n) < p` |
| 3 | 주간 기본 방문 횟수 | 이산균등 {2,3,4,5} | `behavior.visits_per_week` | `rng.integers(2, 6)` |
| 4 | 주별 방문 횟수 변동 | 범주 {−1, 0, +1}, p = [0.2, 0.6, 0.2] | `behavior.weekly_jitter_p` | `rng.choice` |
| 5 | 사용자별 요일 선호 | 디리클레 Dir(모집단 요일 가중 × 5) | `routine.dow_weights`, `routine.dow_concentration` | `rng.dirichlet` |
| 6 | 그 주의 방문 요일 | 요일 선호에 따른 비복원 가중 추출 | – | `rng.choice(7, k, replace=False, p=…)` |
| 7 | 도착 시각대 선호(사용자별 혼합 가중) | 디리클레 Dir(모집단 성분 가중 × 10) | `routine.arrival_mixture.*` | `rng.dirichlet` |
| 8 | 사용자별 도착 시각 오프셋 | 정규 N(0, 20분) | `routine.user_offset_sd_min` | `rng.normal` |
| 9 | 체류 시간 기대값(사용자) | 로그정규(평균 180분, sd 70분) | `behavior.stay_minutes` | `rng.lognormal` |
| 10 | 습관 κ, κ_zone, 양옆 선호 η | 로그정규(평균·sd로 μ·σ 환산) | `behavior.kappa`, `kappa_zone`, `eta` | `rng.lognormal` |
| 11 | 혼잡 이동 임계값 θ_move | 0.80 + 0.15·Beta(4, 2) | `behavior.theta_move` | `rng.beta` |
| 12 | 홈 열람실 | 범주(좌석 수 비례) | – | `rng.choice(p=…)` |
| 13 | 사용자 표본(≤ 3,000명) | archetype 층별 비복원 추출 | – | `rng.choice(replace=False)` |
| 14 | 분할 70/15/15 | archetype 층별 순열 | `split.ratios` | `rng.permutation` |
| **관측 메타데이터** |||||
| 15 | 카테고리가 unknown인지 | 베르누이(사용자의 v1 unknown 비율) | – | `rng.random() < p` |
| 16 | direct / contextual | 베르누이(0.35) | `observation.direct_rate` | `rng.random() < p` |
| 17 | 관측이 참값과 일치하는지 | 베르누이(0.947 / 0.849) | `observation.*_correct` | `rng.random() < p` |
| 18 | 확신도 | 균등 U(0.70, 0.95) / U(0.35, 0.70) | `observation.*_conf` | `rng.uniform` |
| 19 | 설문 응답 이동 | 베르누이(0.25) × 방향 베르누이(0.5) → ±1 | `survey.shift_prob` | `rng.random` |
| 20 | 설문 결측 / straightliner | 베르누이(0.10) / 베르누이(0.03) | `survey.*_rate` | `rng.random() < p` |
| **달력** |||||
| 21 | 날씨 | 1차 마르코프 연쇄: 확률 0.6으로 전날 유지, 아니면 월별 주변분포 | `calendar.weather_probs`, `weather_persistence` | `rng.random`, `rng.choice` |
| 22 | 일 기온 | 선형 추세 + AR(1) 잡음 `e_t = 0.7·e_{t−1} + N(0, sd·√(1−0.7²))` | `calendar.outdoor_temp` | `rng.normal` |
| 23 | 운영상 막힌 좌석 | 좌석-일 베르누이(0.02). 종일(0.5) 또는 구간: 시작 균등, 길이 로그정규(평균 120분) | `occupancy.blocked_*` | `rng.random`, `rng.uniform`, `rng.lognormal` |
| **하루 시뮬레이션** |||||
| 24 | 방문 도착 시각 | 혼합 정규: 사용자 성분 가중(#7)으로 성분을 고르고 N(μ_c + 오프셋, sd_c). 운영시간으로 자른다 | `routine.arrival_mixture` | `rng.choice`, `rng.normal` |
| 25 | 이번 방문 체류 시간 | 로그정규(사용자 기대값 #9, cv 0.3). 최소 30분, 폐관까지 | `behavior.stay_cv` | `rng.lognormal` |
| 26 | 배경 이용자 입실 수(틱당) | 포아송(λ = max(0, 목표 − 현재) × 좌석 수 × 0.8) | `occupancy.fill_rate` | `rng.poisson` |
| 27 | 배경 이용자 좌석 | 범주 softmax(인기도 / τ) | `occupancy.background_popularity`, `background_tau` | `rng.choice(p=…)` |
| 28 | 배경 이용자 체류 | 로그정규(평균 150분, sd 80분) | `occupancy.background_stay` | `rng.lognormal` |
| 29 | 자리 비움 횟수 / 시작 / 길이 | 포아송(0.15 × 체류 시간) / 체류 구간 균등 / 균등 U(5, 40)분 | `behavior.away` | `rng.poisson`, `rng.uniform` |
| 30 | 노트북 허용 열람실로 갈지 | 베르누이(0.8) | `fixed.p_laptop_room_switch` | `rng.random() < p` |
| **선호 실현·선택** |||||
| 31 | 그룹 k를 일관되게 따르는지 | 베르누이(ρ_k) | `groups.rho` | `rng.random(8) < rho` |
| 32 | 일관된 방문의 중요도 흔들림 | 로그정규 LogN(0, σ_k). `exp(N(0, σ_k))`와 같은 분포 | `groups.sigma` | `rng.lognormal(0, σ_k)` |
| 33 | 벗어난 방문: 목표값 반전 / 축소 | 베르누이(0.5) / 균등 U(0, 0.3) | `groups.deviate_*` | `rng.random`, `rng.uniform` |
| 34 | 선택 노이즈 ε | 굼벨 Gumbel(0, s / rationality) | 보정된 s | `rng.gumbel` |
| 35 | 추천 노출 5개 | Plackett-Luce = 굼벨 top-k(점수/온도 + Gumbel(0, 1)) | `exposure.temperature` | `rng.gumbel` |
| 36 | 노출 포함 확률 | 굼벨 top-k 몬테카를로 200회 | `exposure.mc_runs` | `rng.gumbel` |
| **이용 결과·만족도** |||||
| 37 | 조기 퇴실 여부 | 베르누이(sigmoid(logit 0.05 + 4·(0.5 − q))) | `behavior.early_leave` | `rng.random() < p` |
| 38 | 조기 퇴실 시점 | 예정 체류 × U(0.2, 0.7) | – | `rng.uniform` |
| 39 | 같은 방문 안 자리 이동 | 베르누이(sigmoid(logit 0.02 + 3·(0.5 − q))) | `behavior.in_visit_move` | `rng.random() < p` |
| 40 | 사용 확정 | 베르누이(0.97) | `behavior.use_confirm_p` | `rng.random() < p` |
| 41 | 만족도 리커트 | 순서형 프로빗: q + N(0, 0.1)을 임계값 [0.2, 0.4, 0.6, 0.8]로 자른다 | `feedback.*` | `rng.normal` |
| 42 | 응답 여부 | 베르누이(응답률) | `feedback.response_rate` | `rng.random() < p` |

- q는 체험 효용의 eligible 내 백분위다(§9.3).
- 로그정규는 평균 m과 표준편차 sd로 적고 코드에서 `σ² = ln(1 + sd²/m²)`, `μ = ln m − σ²/2`로 환산한다.
- 모든 분포의 파라미터와 실제 표본 통계는 manifest의 `distributions` 항목에 함께 기록한다.

### 14.4 분포 모듈 `synth_v2/dist.py` (모든 추출의 단일 진입점)
```python
"""모든 확률 추출의 단일 진입점. 다른 모듈은 이 함수만 호출한다."""
import hashlib
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

KST = ZoneInfo("Asia/Seoul")
STREAM = {"USERS": 1, "CALENDAR": 2, "SIM": 3, "CALIBRATION": 4, "OBSERVATION": 5, "FEEDBACK": 6}


def rng_for(seed: int, stream: str, *keys: int) -> np.random.Generator:
    """용도·위치별 독립 스트림. 같은 인자면 처리 순서와 무관하게 같은 난수열."""
    return np.random.default_rng([seed, STREAM[stream], *keys])


def make_id(prefix: str, *parts) -> str:
    return f"{prefix}:" + hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:16]


def iso(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


# ---------- 기본 분포 ----------
def lognormal_ms(rng, mean, sd, size=None):
    """평균·표준편차로 지정하는 로그정규."""
    s2 = math.log1p((sd / mean) ** 2)
    return rng.lognormal(math.log(mean) - s2 / 2, math.sqrt(s2), size)


def bernoulli(rng, p, size=None):
    return rng.random(size) < p


def scaled_beta(rng, lo, hi, a, b, size=None):
    return lo + (hi - lo) * rng.beta(a, b, size)


def categorical(rng, items, p):
    p = np.asarray(p, float)
    return items[rng.choice(len(items), p=p / p.sum())]


def softmax_choice(rng, scores, tau):
    z = np.asarray(scores, float) / tau
    p = np.exp(z - z.max())
    return int(rng.choice(len(z), p=p / p.sum()))


def gumbel_noise(rng, scale, size):
    return rng.gumbel(0.0, scale, size)


# ---------- 사용자 ----------
def stable_weights(rng, mean, concentration, multipliers, active_mask):
    w = rng.dirichlet(np.asarray(mean) * concentration)
    w = w * np.asarray(multipliers) * np.asarray(active_mask, float)   # archetype 배수, 취향 없음=0
    return w / w.sum() if w.sum() > 0 else w


def user_routine(rng, cfg):
    r, b = cfg["routine"], cfg["behavior"]
    mix = r["arrival_mixture"]
    return {
        "visits_base": int(rng.integers(b["visits_per_week"][0], b["visits_per_week"][1] + 1)),
        "dow_p": rng.dirichlet(np.asarray(r["dow_weights"]) * r["dow_concentration"]),
        "arrival_p": {dt: rng.dirichlet(np.asarray(c["weights"]) * r["arrival_concentration"]) for dt, c in mix.items()},
        "arrival_offset_min": float(rng.normal(0.0, r["user_offset_sd_min"])),
        "stay_mean_min": float(lognormal_ms(rng, b["stay_minutes"]["mean"], b["stay_minutes"]["sd"])),
        "kappa": float(lognormal_ms(rng, **b["kappa"])),
        "kappa_zone": float(lognormal_ms(rng, **b["kappa_zone"])),
        "eta": float(lognormal_ms(rng, **b["eta"])),
        "theta_move": float(scaled_beta(rng, *b["theta_move"]["range"], *b["theta_move"]["ab"])),
    }


def week_visit_days(rng, routine, cfg, open_dows):
    jit = rng.choice([-1, 0, 1], p=cfg["behavior"]["weekly_jitter_p"])
    k = int(np.clip(routine["visits_base"] + jit, 1, len(open_dows)))
    p = routine["dow_p"][open_dows]
    return np.asarray(open_dows)[rng.choice(len(open_dows), size=k, replace=False, p=p / p.sum())]


def arrival_minute(rng, routine, day_type, cfg, opens_min, closes_min):
    comp = cfg["routine"]["arrival_mixture"][day_type]
    c = rng.choice(len(comp["mu_min"]), p=routine["arrival_p"][day_type])
    t = rng.normal(comp["mu_min"][c] + routine["arrival_offset_min"], comp["sd_min"][c])
    return float(np.clip(t, opens_min, closes_min - 30))


def stay_minutes(rng, routine, cfg):
    m = routine["stay_mean_min"]
    return float(max(cfg["behavior"]["stay_minutes"]["min"], lognormal_ms(rng, m, m * cfg["behavior"]["stay_cv"])))


# ---------- 달력 ----------
def weather_chain(rng, months, probs, persistence):
    out, prev = [], None
    for m in months:
        if prev is not None and rng.random() < persistence:
            out.append(prev)
            continue
        pm = probs[m]
        prev = str(categorical(rng, np.array(list(pm)), list(pm.values())))   # 저장용 파이썬 str
        out.append(prev)
    return out


def temperature_series(rng, n, start_c, end_c, sd, phi=0.7):
    trend = np.linspace(start_c, end_c, n)
    e = np.zeros(n)
    for t in range(1, n):
        e[t] = phi * e[t - 1] + rng.normal(0.0, sd * math.sqrt(1 - phi ** 2))
    return trend + e


# ---------- 방문별 선호 ----------
def realize_visit(rng, stable_w, stable_dir, cfg, flip_map):
    G, g = cfg["groups"]["order"], cfg["groups"]
    w = np.array(stable_w, float)
    d = dict(stable_dir)
    keep = rng.random(len(G)) < np.asarray(g["rho"])                     # 베르누이(ρ_k)
    jitter = rng.lognormal(0.0, np.asarray(g["sigma"]))                   # LogN(0, σ_k)
    consistent = {}
    for k, name in enumerate(G):
        if w[k] == 0:
            consistent[name] = None
        elif keep[k]:
            w[k] *= jitter[k]
            consistent[name] = True
        else:
            consistent[name] = False
            if name in flip_map and rng.random() < g["deviate_flip_prob"]:
                d[name] = flip_map[name][d[name]]
            else:
                w[k] *= rng.uniform(*g["deviate_shrink"])
    return (w / w.sum() if w.sum() > 0 else w), d, consistent


# ---------- 노출(Plackett-Luce = 굼벨 top-k) ----------
def pl_topk(rng, scores, k, temp):
    z = np.asarray(scores, float) / temp
    order = np.argsort(-(z + rng.gumbel(0.0, 1.0, len(z))))[: min(k, len(z))]
    logp, avail = 0.0, np.ones(len(z), bool)
    for j in order:                                                       # 목록 로그확률
        zz = z[avail]
        logp += z[j] - (zz.max() + math.log(np.exp(zz - zz.max()).sum()))
        avail[j] = False
    return order.tolist(), float(logp)


def pl_inclusion(rng, scores, k, temp, runs):
    z = np.asarray(scores, float) / temp
    g = rng.gumbel(0.0, 1.0, (runs, len(z)))
    top = np.argsort(-(z + g), axis=1)[:, : min(k, len(z))]
    return np.bincount(top.ravel(), minlength=len(z)) / runs


# ---------- 이용 결과·만족도 ----------
def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def early_leave(rng, q, cfg):
    e = cfg["behavior"]["early_leave"]
    return bool(rng.random() < sigmoid(math.log(e["base"] / (1 - e["base"])) + e["slope"] * (0.5 - q)))


def likert(rng, q, thresholds, sd):
    return int(1 + np.searchsorted(thresholds, q + rng.normal(0.0, sd)))
```

---

## 15. Python 구현 방법

### 15.1 환경과 사용 라이브러리
```bash
cd /home/stadium/2026-IST-TECH-SeatCom-Model/seat_model
uv venv .venv
uv pip install --python .venv/bin/python numpy pandas pyarrow scipy pymongo pyyaml
echo "seat_model/exports/" >> ../.gitignore   # 한 번만
```

| 라이브러리 | 역할 |
|---|---|
| numpy | 모든 확률 추출과 벡터 계산. `np.random.Generator`로 디리클레, 굼벨, 로그정규, 베르누이, 균등, 베타, 포아송 분포를 뽑고(§14), 좌석 × 사용자 효용을 배열로 한 번에 계산한다 |
| pandas | 사용자, 열람실, 날짜, 요청 테이블 가공과 집계 |
| pyarrow | 후보 테이블(요청 × 좌석, 수백만~천만 행대)을 Parquet으로 날짜·열람실 단위로 나눠 저장 |
| scipy | 보정: `optimize.bisect`로 노이즈 규모 s를 맞춘다. 검증: `stats.spearmanr`, `kendalltau`, 효과 크기 검정(`mannwhitneyu`, Cohen's d), 분포 적합 검정(`kstest`, `binomtest`, `chisquare`) |
| pymongo | 원본 컬렉션 읽기, 결과 컬렉션 `bulk_write`로 저장 |
| pyyaml | `synth_v2.yaml`에서 가중치, ρ·σ, 분포 파라미터, 혼잡 곡선, 휴일 등 모든 수치를 읽는다 |
| 표준 라이브러리 | `heapq`(시간순 사건 처리), `datetime`·`zoneinfo`(+09:00 시각 문자열), `hashlib`(재현 가능한 ID) |

### 15.2 디렉토리 구조
```
seat_model/
├── config/synth_v2.yaml          # 모든 수치(§15.3)
├── synth_v2/
│   ├── __init__.py
│   ├── cli.py                    # 진입점: --users, --sims, --days ...
│   ├── config.py                 # YAML 로드 + 검증(ρ 감소·σ 증가 등)
│   ├── dist.py                   # 모든 확률 추출·난수 스트림·ID(§14.4) ← 다른 모듈은 이것만 호출
│   ├── db.py                     # .env → MongoClient (접속정보 출력 금지)
│   ├── sources.py                # 열람실·기준 시나리오·규칙·좌석 테이블 적재(§1~3)
│   ├── calendar.py               # synth_v2_days(§4)
│   ├── users.py                  # 사용자 선택/추가 생성, 관측·참값 분리(§5)
│   ├── preferences.py            # 안정 선호 + 방문별 실현 선호(§6)
│   ├── seat_attrs.py             # 좌석 속성 11개 계산(§2.2, §7.1)
│   ├── exposure.py               # Plackett-Luce 노출(§7.3)
│   ├── choice.py                 # 효용·Gumbel 선택·만족도(§9)
│   ├── simulate.py               # 날짜별 이벤트 큐 시뮬레이션(§7.2)
│   ├── labels.py                 # 정답 레코드(seats 원본 조인, §2.1)
│   ├── writers.py                # Parquet / MongoDB 저장(§8.3)
│   ├── calibrate.py              # 혼잡·s·κ 보정(§10)
│   ├── validate.py               # 검증(§11)
│   └── report.py                 # 리포트 작성
├── reports/
└── exports/ml_v2/
```

### 15.3 설정 파일 `config/synth_v2.yaml` (골격)
```yaml
generator_version: synth-2.0
seed: 20261114
users: {count: 10000, v1_source: synth_users, new_user_rng_offset: 7}
period: {start: "2026-09-01", days: 61}
sources:
  db: library
  scenarios: synth_scenarios
  seat_env: synth_seat_env
  seats: seats
  feature_registry_version: features-1
base_scenario:
  order: [[spring_fall, clear], [spring_fall, cloudy], [spring_fall, rain], [summer, clear], [summer, cloudy], [summer, rain]]
  override: {}            # room_id: scenario_id
library_city: {nl-digital: seoul, seongnam-central: seongnam, yongin-bora: yongin, yongin-dongbaek: yongin, yongin-giheung: yongin,
               yongin-heungdeok: yongin, yongin-jukjeon: yongin, yongin-pogok: yongin, yongin-sanghyeon: yongin, yongin-seonong: yongin, yongin-suji: yongin}
calendar:
  holidays: {policy: closed, dates: ["2026-09-24", "2026-09-25", "2026-09-26", "2026-10-03", "2026-10-05", "2026-10-09"]}
  midterm: ["2026-10-19", "2026-10-30"]
  opening_hours_default: {weekday: ["09:00", "22:00"], weekend: ["09:00", "18:00"]}
  time_slot_bounds: {noon: "12:00", evening: "17:00"}
  weather_probs: {9: {clear: 0.55, cloudy: 0.30, rain: 0.15}, 10: {clear: 0.60, cloudy: 0.30, rain: 0.10}}
  weather_persistence: 0.6               # 1차 마르코프: 전날 날씨 유지 확률
  outdoor_temp: {start_c: 27.0, end_c: 15.0, sd: 1.5, ar_phi: 0.7}   # 선형 추세 + AR(1)
  day_f: {clear: 1.0, cloudy: 0.55, rain: 0.30}
groups:
  order: [airflow, noise, partition, brightness, deskWidth, window, toilet, entrance]
  dirichlet_mean: [0.22, 0.18, 0.15, 0.13, 0.11, 0.09, 0.07, 0.05]
  concentration: 20
  rho:   [0.95, 0.90, 0.86, 0.82, 0.78, 0.72, 0.66, 0.60]
  sigma: [0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38]
  deviate_flip_prob: 0.5
  deviate_shrink: [0.0, 0.3]
  targets: {airflow: {cool: 0.7, warm: 0.05}, brightness: {bright: 0.85, dim: 0.3}, deskWidth: {large: 1.0, compact: 0.3}}
  archetype_multipliers:
    heat_sensitive: {airflow: 1.4}
    exam_focus: {partition: 1.3, noise: 1.3}
    comfort_seeker: {brightness: 1.2, window: 1.2}
    frequent_break: {toilet: 1.6}
    laptop_worker: {deskWidth: 1.2}
    casual_reader: {}
  hidden: {partition_rate: 0.35, entrance_rate: 0.30}
fixed:
  w_outlet_fixed: 0.30
  outlet_as_hard_constraint: false
  p_laptop_room_switch: 0.8
observation: {direct_rate: 0.35, direct_correct: 0.947, contextual_correct: 0.849, direct_conf: [0.70, 0.95], contextual_conf: [0.35, 0.70], laptop_req_min_conf: 0.6}
survey: {missing_rate: 0.10, straightliner_rate: 0.03, shift_prob: 0.25, include_entrance: false}
routine:                                   # §14.3 #5~#8, #24
  dow_weights: [0.10, 0.17, 0.17, 0.17, 0.17, 0.12, 0.10]   # 일~토 모집단 요일 가중
  dow_concentration: 5                     # 사용자 요일 선호 Dir(가중 × 5)
  arrival_concentration: 10                # 사용자 도착 성분 가중 Dir(가중 × 10)
  user_offset_sd_min: 20
  arrival_mixture:                         # 혼합 정규(자정 기준 분)
    weekday: {weights: [0.45, 0.30, 0.25], mu_min: [570, 810, 1110], sd_min: [40, 50, 40]}   # 09:30 / 13:30 / 18:30
    weekend: {weights: [0.60, 0.40], mu_min: [630, 840], sd_min: [60, 60]}                  # 10:30 / 14:00
behavior:
  visits_per_week: [2, 5]                  # 이산균등
  weekly_jitter_p: [0.2, 0.6, 0.2]         # {−1, 0, +1}
  stay_minutes: {mean: 180, sd: 70, min: 30}   # 사용자 기대값 로그정규
  stay_cv: 0.3                             # 방문별 체류 로그정규 변동계수
  kappa: {mean: 0.08, sd: 0.04}            # 로그정규
  kappa_zone: {mean: 0.03, sd: 0.02}
  eta: {mean: 0.03, sd: 0.02}
  theta_move: {range: [0.80, 0.95], ab: [4, 2]}   # 0.80 + 0.15·Beta(4,2)
  away: {rate_per_hour: 0.15, minutes: [5, 40]}   # 포아송 횟수 + 균등 길이
  early_leave: {base: 0.05, slope: 4.0}    # 베르누이(sigmoid(logit base + slope·(0.5−q)))
  in_visit_move: {base: 0.02, slope: 3.0}
  use_confirm_p: 0.97
occupancy:
  tick_minutes: 15
  fill_rate: 0.8                           # 배경 입실 포아송 λ 계수
  background_tau: 0.2                      # 배경 좌석 softmax 온도
  background_stay: {mean: 150, sd: 80}     # 로그정규
  blocked_rate: 0.02                       # 좌석-일 베르누이
  blocked_whole_day_p: 0.5
  blocked_minutes: {mean: 120, sd: 60}     # 로그정규
  curve: {weekday: {"09": 0.35, "11": 0.65, "13": 0.50, "15": 0.70, "18": 0.55, "21": 0.25}, weekend: {"10": 0.40, "13": 0.60, "16": 0.65, "18": 0.35}}
  midterm_multiplier: 1.2
  background_popularity: {window: 0.3, outlet: 0.3, brightness: 0.2}
exposure: {k: 5, temperature: 0.1, mc_runs: 200, beta_rank: 0.06, policy_version: pl-rule-v1}
feedback: {response_rate: 1.0, thresholds: [0.2, 0.4, 0.6, 0.8], noise_sd: 0.1}
calibration:
  hitrate5_target: 0.70
  hitrate5_tolerance: 0.05
  pilot_days: 7
  reselect_rate_range: [0.15, 0.50]
  noise_scale_bounds: [0.01, 2.0]
split: {ratios: [0.70, 0.15, 0.15], room_holdout_count: 4}
output:
  dir: exports/ml_v2
  parquet_compression: zstd
  mongo_write_candidates: false
```

### 15.4 CLI (`synth_v2/cli.py`): 시뮬레이션 수·인간 수 옵션
```python
import argparse
from pathlib import Path

MODEL_ROOT = Path(__file__).resolve().parents[1]

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="좌석 추천 데이터셋 v2 생성기 (synth-2.0)")
    ap.add_argument("--users", type=int, default=None,
                    help="인간(사용자) 수. 기본: config users.count. 3000 초과분은 v1 make_user로 추가 생성")
    ap.add_argument("--sims", type=int, default=1,
                    help="시뮬레이션 회차 수. 같은 사용자·기간을 회차 시드(seed+1000·sim_id)로 반복해 데이터를 늘린다")
    ap.add_argument("--days", type=int, default=None, help="시작일부터 시뮬레이션할 날짜 수(1~61). 기본: config period.days")
    ap.add_argument("--rooms", nargs="*", default=None, help="room_id 제한(테스트용). 기본: 24개 전체")
    ap.add_argument("--seed", type=int, default=None, help="기본: config seed")
    ap.add_argument("--config", type=Path, default=MODEL_ROOT / "config/synth_v2.yaml")
    ap.add_argument("--out-dir", type=Path, default=MODEL_ROOT / "exports/ml_v2")
    ap.add_argument("--write-mongo", action="store_true", help="새 v2 컬렉션에도 저장")
    ap.add_argument("--overwrite", action="store_true", help="허용 목록의 v2 컬렉션/출력 폴더만 비우고 다시 쓴다")
    ap.add_argument("--skip-calibration", action="store_true")
    ap.add_argument("--noise-scale", type=float, default=None, help="보정 생략 시 s 값")
    ap.add_argument("--validate-only", action="store_true", help="기존 출력에 대해 §11 검증만 실행")
    a = ap.parse_args(argv)
    if a.users is not None and a.users < 1:
        ap.error("--users 는 1 이상")
    if a.sims < 1:
        ap.error("--sims 는 1 이상")
    if a.days is not None and not 1 <= a.days <= 61:
        ap.error("--days 는 1~61")
    if a.skip_calibration and a.noise_scale is None:
        ap.error("--skip-calibration 에는 --noise-scale 이 필요")
    return a

def main(argv=None):
    from . import config, db, sources, calendar, users, calibrate, simulate, writers, validate, report
    a = parse_args(argv)
    cfg = config.load(a.config, overrides={"users.count": a.users, "period.days": a.days, "seed": a.seed})
    mdb = db.get_db(cfg)                                   # 읽기 전용 핸들(쓰기는 writers만)
    src = sources.load(mdb, cfg, rooms=a.rooms)            # 열람실·규칙·기준 시나리오·좌석 테이블
    days = calendar.build(src, cfg)                        # synth_v2_days
    pop = users.build(mdb, src, cfg)                       # 관측(users) / 참값(truth_users) / split
    out = writers.Writer(a.out_dir, mdb if a.write_mongo else None, cfg, overwrite=a.overwrite)
    if a.validate_only:
        return validate.run_all(out, mdb, cfg)
    s = a.noise_scale if a.skip_calibration else calibrate.run(src, days, pop, cfg)
    out.write_static(src.room_rules, days, pop)
    for sim_id in range(a.sims):
        simulate.run(sim_id, src, days, pop, cfg, noise_scale=s, writer=out)   # 날짜 단위로 flush
    out.write_manifest(args=vars(a), noise_scale=s, src=src)
    res = validate.run_all(out, mdb, cfg)
    report.write(cfg, out, res, path=Path(cfg.root) / "reports/synth_v2_dataset_report.md")
    if not res.passed:
        raise SystemExit("검증 실패: 리포트 확인")

if __name__ == "__main__":
    main()
```

**실행 예**
```bash
cd seat_model
.venv/bin/python -m synth_v2.cli --users 300 --days 7 --sims 1 --skip-calibration --noise-scale 0.12   # 구조 검증
.venv/bin/python -m synth_v2.cli --users 3000 --sims 1                                                 # 중간 규모
.venv/bin/python -m synth_v2.cli --users 10000 --sims 3 --write-mongo                                  # 본 데이터 + 증강
```

### 15.5 핵심 코드

**DB 연결 (`db.py`)**: 접속 정보를 출력하거나 저장하지 않는다.
```python
from pathlib import Path
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]          # 저장소 루트

def _env(path=ROOT / ".env"):
    d = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip("'\"")
    return d

def get_db(cfg):
    e = _env()
    client = MongoClient(host=e["MONGO_HOST"], port=int(e["MONGO_PORT"]), username=e.get("MONGO_USER"),
                         password=e.get("MONGO_PASSWORD"), authSource=e.get("MONGO_AUTH_DB", "admin"),
                         serverSelectionTimeoutMS=10000)
    return client[cfg["sources"]["db"]]
```

**좌석 테이블 (`sources.py`)**: 기준 시나리오 환경과 `seats` 원본을 조인한다.
```python
import pandas as pd

LAYOUT = {"four_person_table": "shared_table", "long_shared_table": "shared_table"}
SEAT_MASTER = ["seat_id", "library_id", "room_id", "map_id", "seat_label", "external_seat_id",
               "bbox", "norm_bbox", "verification", "source_id"]

def pick_base_scenario(db, room_id, cfg):
    if room_id in cfg["base_scenario"]["override"]:
        sid = cfg["base_scenario"]["override"][room_id]
        return db.synth_scenarios.find_one({"_id": sid}), "override"
    scen = list(db.synth_scenarios.find({"room_id": room_id}, {"objects": 0, "brightness_grid": 0}).sort("scenario_index", 1))
    for season, weather in cfg["base_scenario"]["order"]:
        for s in scen:
            if s["conditions"]["season"] == season and s["conditions"]["weather"] == weather:
                return s, f"{season}/{weather}"
    raise RuntimeError(f"기준 시나리오 없음: {room_id}")

def room_rule(db, room, base):
    real = (room.get("usage_rules") or {}).get("laptop_allowed")
    rf = db.room_features.find_one({"room_id": room["_id"], "name": "laptopAllowed", "dataset_status": "active"})
    if real is not None:
        val, origin = real, "real_usage_rules"
    elif rf is not None:
        val, origin = bool(rf["value"]), "real_room_feature"
    else:
        val, origin = bool(base["conditions"]["laptop_allowed"]), "synthetic"
    return {"room_id": room["_id"], "library_id": room["library_id"], "room_name": room["name"],
            "laptop_allowed": val, "laptop_rule_origin": origin,
            "laptop_allowed_real": real if real is not None else (bool(rf["value"]) if rf else None),
            "base_scenario_id": base["_id"]}

def seat_table(db, base_ids, rules):
    rows = []
    for d in db.synth_seat_env.find({"scenario_id": {"$in": base_ids}}):
        f, r = d["features"], d["raw"]
        rows.append({
            "seat_id": d["seat_id"], "room_id": d["room_id"], "scenario_id": d["scenario_id"],
            "toiletProximity": f["toiletProximity"], "airflow": f["airflow"], "deskWidth": f["deskWidth"],
            "partition": f["partition"], "outletAvailable": f["outletAvailable"],
            "windowProximity": f["windowProximity"], "viewQuality": f["viewQuality"],
            "entranceProximity": f["entranceProximity"], "noise_index": r["noise_index"],
            "laptopAllowed": int(rules[d["room_id"]]["laptop_allowed"]),          # 열람실 규칙 우선
            "laptopAllowed_env": f["laptopAllowed"],                                # 차이 리포트용(입력 제외)
            "b_morning": d["brightness_by_slot"]["morning"], "b_noon": d["brightness_by_slot"]["noon"],
            "b_evening": d["brightness_by_slot"]["evening"],
            "deskHeight": f["deskHeight"], "desk_type": r["desk_type"], "desk_layout": LAYOUT.get(r["desk_type"], r["desk_type"]),
            "desk_group": r["desk_group"], "width_cm": r["width_cm"], "height_cm": r["height_cm"],
            "temperature_c": r["temperature_c"], "dist_window_m": r["dist_window_m"],
            "dist_toilet_walk_m": r["dist_toilet_walk_m"], "dist_entrance_walk_m": r["dist_entrance_walk_m"],
            "dist_outlet_m": r["dist_outlet_m"], "dist_ac_m": r["dist_ac_m"],
        })
    env = pd.DataFrame(rows)
    master = pd.DataFrame(list(db.seats.find({"_id": {"$in": env.seat_id.tolist()}, "dataset_status": "active"},
                                             {k: 1 for k in SEAT_MASTER})))
    master = master.drop(columns="_id")
    seats = env.merge(master, on=["seat_id", "room_id"], how="left", validate="one_to_one", indicator=True)
    missing = seats[seats["_merge"] != "both"]
    if len(missing):                                     # 정답 좌석은 반드시 seats에 있어야 한다
        raise RuntimeError(f"seats 조인 실패 {len(missing)}건")
    return seats.drop(columns="_merge")
```

**좌석 속성 11개 (`seat_attrs.py`)**
```python
import numpy as np

ATTRS = ["toiletProximity", "brightness", "airflow", "deskWidth", "partition", "laptopAllowed",
         "outletAvailable", "windowProximity", "viewQuality", "entranceProximity", "noise_index"]

def gamma(weather, base_weather, day_f):
    return day_f[weather] / day_f[base_weather] - 1.0

def attrs_at(seats_room, slot, weather, base_weather, cfg):
    """요청 시점의 속성 11개 + 보조 컬럼(DataFrame, 좌석당 1행)."""
    df = seats_room.copy()
    base = df[f"b_{slot}"].to_numpy()
    g = gamma(weather, base_weather, cfg["calendar"]["day_f"])
    df["brightness_base_slot"] = base
    df["brightness"] = np.clip(base * (1 + g * df["windowProximity"].to_numpy()), 0, 1).round(3)
    df["brightness_origin"] = "weather_adjusted_approx"
    return df
```

**사용자 안정 선호와 방문별 실현 선호 (`preferences.py`)**: 추출은 모두 `dist`를 거친다.
```python
import numpy as np
from . import dist

FLIP = {"airflow": {"cool": "warm", "warm": "cool"}, "brightness": {"bright": "dim", "dim": "bright"},
        "deskWidth": {"large": "compact", "compact": "large"}}

def build_stable(rng, user, cfg):
    """디리클레(§14.3 #1) + 숨은 취향 베르누이(#2). 취향 없는 그룹은 0."""
    g = cfg["groups"]
    hidden_part = bool(dist.bernoulli(rng, g["hidden"]["partition_rate"]))
    hidden_ent = bool(dist.bernoulli(rng, g["hidden"]["entrance_rate"]))
    lat = user["latent"]["preferences"]
    active = {"airflow": True, "noise": True,
              "partition": lat["desk"] == "compact" or hidden_part,
              "brightness": True, "deskWidth": True,
              "window": lat["view"] == "window", "toilet": lat["restroom"] == "near",
              "entrance": hidden_ent}
    mult = [g["archetype_multipliers"][user["archetype"]].get(k, 1.0) for k in g["order"]]
    w = dist.stable_weights(rng, g["dirichlet_mean"], g["concentration"], mult, [active[k] for k in g["order"]])
    dirs = {"airflow": lat["air_conditioning"], "brightness": lat["lighting"], "deskWidth": lat["desk"]}
    return w, dirs, {"hidden_partition": hidden_part, "hidden_entrance": hidden_ent}

def realize(rng, stable_w, stable_dir, cfg):
    """일관성 메커니즘: 베르누이(ρ_k) · 로그정규(0, σ_k) · 반전 베르누이 · 축소 균등(§14.3 #31~#33)."""
    return dist.realize_visit(rng, stable_w, stable_dir, cfg, FLIP)
```
- `noise` 그룹의 강도는 `w_noise × noise_aversion`으로 조정한 뒤 재정규화한다. 이 단계는 `stable_weights` 다음에 적용하며, 위 코드에서는 생략했다.

**효용과 선택 (`choice.py`)**
```python
import numpy as np
from . import dist

def match_matrix(c, dirs, cfg):
    """c: 후보 DataFrame(속성 11개 포함) → (n, 8) 일치값 m_k."""
    T = cfg["groups"]["targets"]
    t_air = T["airflow"].get(dirs.get("airflow"), 0.0)
    t_bri = T["brightness"].get(dirs.get("brightness"), 0.0)
    t_desk = T["deskWidth"].get(dirs.get("deskWidth"), 0.0)
    return np.column_stack([
        1 - np.abs(c.airflow - t_air),
        1 - c.noise_index,
        c.partition,
        1 - np.abs(c.brightness - t_bri),
        1 - np.abs(c.deskWidth - t_desk),
        0.6 * c.windowProximity + 0.4 * c.viewQuality,
        c.toiletProximity,
        c.entranceProximity,
    ]).astype(float)

def utilities(c, w, dirs, user, ctx, cfg):
    """ε를 뺀 효용 성분. ctx: last_seat, last_zone, exposure rank 맵, laptop_rule_conflict."""
    base = match_matrix(c, dirs, cfg) @ w
    outlet = 0.0
    if user["outlet_required"] and not ctx["laptop_rule_conflict"]:
        outlet = cfg["fixed"]["w_outlet_fixed"] * c.outletAvailable.to_numpy()
    habit = user["kappa"] * (c.seat_id == ctx["last_seat"]).to_numpy() \
          + user["kappa_zone"] * (c.desk_group == ctx["last_zone"]).to_numpy()
    neighbor = user["eta"] * c.group_empty_ratio.to_numpy()
    rank = c.seat_id.map(ctx["exposure_rank"]).to_numpy(dtype=float)       # 미노출 NaN
    expo = np.where(np.isnan(rank), 0.0, user["rec_trust"] * cfg["exposure"]["beta_rank"] * (6 - rank) / 5)
    return {"base": base, "outlet": outlet, "habit": habit, "neighbor": neighbor, "expo": expo,
            "U_det": base + outlet + habit + neighbor + expo}

def choose(parts, rationality, s, rng):
    eps = dist.gumbel_noise(rng, s / rationality, len(parts["U_det"]))      # Gumbel(0, s/rationality), §14.3 #34
    U = parts["U_det"] + eps
    return {"chosen": int(np.argmax(U)),
            "no_exposure": int(np.argmax(U - parts["expo"])),
            "oracle": int(np.argmax(parts["U_det"])),
            "eps": eps}

def experienced_pct(parts, idx):
    x = parts["U_det"] - parts["expo"]
    return float((x <= x[idx]).mean())
```

**Plackett-Luce 노출 (`exposure.py`)**: 굼벨 top-k와 같은 분포다(§14.3 #35, #36).
```python
import numpy as np
from . import dist

def rule_scores(cand, profile):
    """관측 프로필 featureScore = Σ imp·(1−|target−f|) / Σ imp. 프로필이 비면 모든 좌석이 같은 점수."""
    prefs = profile["preferences"]
    W = sum(p["importance"] for p in prefs)
    if W == 0:
        return np.zeros(len(cand))
    s = sum(p["importance"] * (1 - np.abs(p["target"] - cand[p["featureId"]].to_numpy())) for p in prefs)
    return s / W

def expose(rng, cand, profile, cfg):
    e = cfg["exposure"]
    sc = rule_scores(cand, profile)
    order, logp = dist.pl_topk(rng, sc, e["k"], e["temperature"])
    incl = dist.pl_inclusion(rng, sc, e["k"], e["temperature"], e["mc_runs"])
    return order, logp, incl
```

**정답 레코드 (`labels.py`)**: `seats` 원본을 쓴다.
```python
ATTRS = ["toiletProximity", "brightness", "airflow", "deskWidth", "partition", "laptopAllowed",
         "outletAvailable", "windowProximity", "viewQuality", "entranceProximity", "noise_index"]
MASTER = ["seat_id", "library_id", "room_id", "map_id", "seat_label", "external_seat_id",
          "bbox", "norm_bbox", "verification", "source_id"]

def label_row(sim_id, req, cand, chosen_idx, outcome, usage):
    eligible = cand.seat_id.tolist()
    if outcome != "chosen":
        return {"sim_id": sim_id, "request_id": req["request_id"], "user_id": req["user_id"], "room_id": req["room_id"],
                "outcome": outcome, "chosen_seat_id": None, "chosen_seat": None, "chosen_seat_attributes": None,
                "seat_ref": None, "remaining_seat_ids": eligible, **usage}
    row = cand.iloc[chosen_idx]
    return {"sim_id": sim_id, "request_id": req["request_id"], "user_id": req["user_id"], "room_id": req["room_id"],
            "outcome": "chosen", "chosen_seat_id": row.seat_id,
            "chosen_seat": {k: row[k] for k in MASTER},                       # seats 원본(sources.seat_table에서 조인)
            "chosen_seat_attributes": {k: row[k] for k in ATTRS},
            "seat_ref": {"collection": "seats", "_id": row.seat_id},
            "remaining_seat_ids": [s for s in eligible if s != row.seat_id], **usage}
```

**시뮬레이션 루프 (`simulate.py`)**: 날짜마다 24개 열람실을 하나의 이벤트 큐로 처리한다.
```python
import heapq
from . import dist

def run(sim_id, src, days, pop, cfg, noise_scale, writer):
    history = {}                                            # user_id → (last_seat, last_zone, last_ctx), 회차별
    for date in days.dates():
        # 날짜별 독립 스트림: 처리 순서·병렬화와 무관하게 재현된다(§14.2)
        rng = dist.rng_for(cfg["seed"], "SIM", sim_id, date.toordinal())
        state = SeatState(src, date, days, rng, cfg)        # 좌석 상태 + seat_states 원장 기록
        q = []                                               # (시각, 순번, 이벤트종류, payload)
        for v in pop.visits_on(date, rng, days):             # 루틴 → 방문(도착 시각, 체류, 홈 열람실)
            heapq.heappush(q, (v.arrive, next(state.seq), "arrive", v))
        for t in state.ticks():                              # 15분 혼잡 보정
            heapq.heappush(q, (t, next(state.seq), "tick", None))
        while q:
            t, _, kind, p = heapq.heappop(q)
            if kind == "tick":
                state.fill_background(t)                     # 목표 곡선까지 배경 입·퇴실
            elif kind == "arrive":
                handle_arrival(sim_id, t, p, state, src, days, pop, cfg, noise_scale, rng, history, writer, q)
            elif kind in ("leave", "away_start", "away_end", "move"):
                state.apply(kind, t, p, q)
        state.close_day()
        writer.flush_day(sim_id, date, state.ledger())      # seat_states, requests, candidates, labels ... 저장
```
- `pop.visits_on`: 사용자 루틴에서 만든다.
  - 그 주의 방문 요일: `dist.week_visit_days`
  - 도착 시각: 혼합 정규 `dist.arrival_minute`
  - 체류 시간: 로그정규 `dist.stay_minutes`
- `state.fill_background`: 입실 수는 포아송, 좌석은 softmax 범주, 체류는 로그정규로 뽑는다(§14.3 #26~#28).

`handle_arrival` 순서:
1. 열람실을 고른다.
   - 노트북 규칙: 허용 열람실로 갈지 베르누이(0.8)로 정한다.
   - 혼잡: 점유율이 θ_move를 넘으면 이동한다.
   - 이동하면 `dist.make_id("req", …)`로 요청을 새로 만들고 `chain_id`로 연결한다.
2. `reservable = state.available(room, t)`, `eligible = requirements 필터`
3. `attrs_at(...)`으로 속성 11개를 계산하고, 관측 선호 × 확신도·이력·실시간 컬럼을 붙인다.
4. 노출: `exposure.expose`(굼벨 top-k)
5. `preferences.realize` → `choice.utilities` → `choice.choose`(굼벨 ε)
6. 상태를 `occupied`로 바꾸고 이벤트를 넣는다.
   - 퇴실: 로그정규 체류
   - 자리 비움: 포아송 횟수 + 균등 길이
   - 조기 퇴실·자리 이동: 시그모이드 베르누이
7. labels / feedback(순서형 프로빗 리커트) / truth 레코드를 버퍼에 쌓는다.

**저장 (`writers.py`)**: 허용 목록 밖 컬렉션에는 쓰지 않는다.
```python
ALLOWED = {"synth_v2_users", "synth_v2_room_rules", "synth_v2_days", "synth_v2_seat_states", "ml_v2_requests",
           "ml_v2_exposures", "ml_v2_candidates", "ml_v2_labels", "ml_v2_label_rows", "ml_v2_feedback",
           "synth_v2_truth_requests", "synth_v2_truth_users"}

def write_parquet(df, out_dir, table, sim_id, part):
    import pyarrow as pa, pyarrow.parquet as pq
    path = out_dir / table / f"sim_id={sim_id}" / f"part-{part}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path, compression="zstd")

def write_mongo(db, name, docs, batch=5000):
    """bulk_write + ReplaceOne(upsert): 같은 시드로 다시 돌리면 같은 _id(hashlib ID)에 덮어써서 중복이 생기지 않는다."""
    from pymongo import ReplaceOne
    if name not in ALLOWED:
        raise RuntimeError(f"쓰기 금지 컬렉션: {name}")
    ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
    for i in range(0, len(ops), batch):
        db[name].bulk_write(ops[i:i + batch], ordered=False)
```
- 문서 `_id`는 `request_id`나 `dist.make_id(...)`로 만든 키를 쓴다. 예: candidates는 `request_id|seat_id`의 해시.
- `meta` 컬렉션에는 `_id: "synthetic_manifest_v2"` 문서 하나만 `replace_one(..., upsert=True)`로 쓴다.

**보정: 노이즈 규모 s (`calibrate.py`, `scipy.optimize.bisect`)**
```python
import numpy as np
from scipy.optimize import bisect
from . import dist

def hitrate_at5(pilot, s, G):
    """pilot: 요청별 (U_det, rationality). G: 요청별로 미리 뽑은 표준 굼벨(공통 난수) → s에 대해 단조."""
    hits = 0
    for (u, rat), g in zip(pilot, G):
        chosen = int(np.argmax(u + (s / rat) * g))
        hits += chosen in np.argsort(-u)[:5]
    return hits / len(pilot)

def calibrate_noise_scale(pilot, cfg):
    c = cfg["calibration"]
    rng = dist.rng_for(cfg["seed"], "CALIBRATION", 0)
    G = [rng.gumbel(0.0, 1.0, len(u)) for u, _ in pilot]        # 공통 난수로 고정
    f = lambda s: hitrate_at5(pilot, s, G) - c["hitrate5_target"]
    lo, hi = c["noise_scale_bounds"]
    if f(lo) < 0 or f(hi) > 0:
        raise RuntimeError("s 탐색 구간이 목표 HitRate@5를 감싸지 않음: noise_scale_bounds 조정")
    return bisect(f, lo, hi, xtol=1e-3)
```

**검증: 분포 적합과 순서 검정 (`validate.py`, `scipy.stats`)**
```python
import numpy as np
from scipy import stats

def check_bernoulli(successes, n, p, alpha=0.001):
    """예: 숨은 칸막이 0.35, 그룹별 일관 비율 ρ_k, 설문 결측 0.10."""
    return stats.binomtest(successes, n, p).pvalue > alpha

def check_lognormal(x, mean, sd, alpha=0.001):
    """예: 사용자 체류 기대값, κ. 자르기(clip) 전 원값으로 검정한다."""
    s2 = np.log1p((sd / mean) ** 2)
    return stats.kstest(x, "lognorm", args=(np.sqrt(s2), 0, mean * np.exp(-s2 / 2))).pvalue > alpha

def check_gumbel(eps_std, alpha=0.001):
    """eps / (s / rationality) 를 표준 굼벨과 비교."""
    return stats.kstest(eps_std, "gumbel_r").pvalue > alpha

def check_dirichlet_mean(w_raw, mean, tol=0.01):
    """archetype 배수 전 원 디리클레 표본의 평균이 config 평균 ±0.01."""
    return np.all(np.abs(w_raw.mean(0) - np.asarray(mean)) < tol)

def check_weather_persistence(seq, persistence, probs_by_day):
    """전날 유지 비율의 기대값 = persistence + (1−persistence)·Σ p_w² 와 비교(이항 검정)."""
    stay = sum(a == b for a, b in zip(seq[:-1], seq[1:]))
    exp = np.mean([persistence + (1 - persistence) * sum(v * v for v in p.values()) for p in probs_by_day[1:]])
    return check_bernoulli(stay, len(seq) - 1, exp)

def consistency_order(cv_by_group):
    """그룹별 m_k 변동계수가 우선순위 순으로 커지는지: 스피어만 ≥ 0.8, 켄달 타우도 함께 보고."""
    rank = np.arange(1, len(cv_by_group) + 1)
    rho, _ = stats.spearmanr(rank, cv_by_group)
    tau, _ = stats.kendalltau(rank, cv_by_group)
    return rho >= 0.8, {"spearman": rho, "kendall": tau}

def day_type_effect(x_weekday, x_weekend):
    """요일 유형별로 고른 좌석 특징의 차이: 맨-휘트니 U + Cohen's d(효과 크기)."""
    u = stats.mannwhitneyu(x_weekday, x_weekend)
    sp = np.sqrt((np.var(x_weekday, ddof=1) + np.var(x_weekend, ddof=1)) / 2)
    return {"p": u.pvalue, "cohens_d": (np.mean(x_weekday) - np.mean(x_weekend)) / sp}
```

**검증 예: 정답 좌석이 MongoDB `seats`와 같은지 (`validate.py`)**
```python
def check_labels_against_seats(labels, db):
    ids = {l["chosen_seat_id"] for l in labels if l["outcome"] == "chosen"}
    master = {d["_id"]: d for d in db.seats.find({"_id": {"$in": list(ids)}})}
    bad = []
    for l in labels:
        if l["outcome"] != "chosen":
            continue
        m = master.get(l["chosen_seat_id"])
        if m is None or m.get("dataset_status") != "active" or m["room_id"] != l["room_id"]:
            bad.append((l["request_id"], "missing_or_inactive_or_room_mismatch"))
            continue
        for k, v in l["chosen_seat"].items():
            if m.get(k) != v:
                bad.append((l["request_id"], f"field_mismatch:{k}"))
    return bad          # 빈 리스트여야 통과
```

### 15.6 구현 시 주의
- 무작위 값은 반드시 `dist.py`의 함수로만 뽑는다(§14.1).
  - `import random`, `np.random.seed`, `np.random.<분포>` 같은 전역 호출은 금지다. v1 `make_user` 재사용만 예외다.
  - `validate.py`가 `grep -nE "np\.random\.(rand|seed|choice|normal|uniform)|^import random"`으로 소스를 검사한다.
- 날짜·시각은 `zoneinfo.ZoneInfo("Asia/Seoul")`로 만들고 `+09:00` ISO 문자열로 저장한다. 계산할 때는 `datetime`으로 변환한다.
- ID(seat_id, room_id 등)는 문자열 그대로 쓰고 파싱하지 않는다. 도시 구분은 `library_city`를 쓴다.
- null(`seat_label`, `external_seat_id`, `laptop_allowed_real` 등)은 그대로 저장하고 채우지 않는다.
- `ml_v2_candidates`는 날짜 단위로 Parquet에 flush해서 메모리를 제한한다. 1만 명 기준 하루 후보는 약 23만 행이다.
- 같은 `--seed`, `--users`, `--sims`, `--days`이면 출력이 바이트 단위로 같아야 한다. `--users 300 --days 3`으로 두 번 돌려 해시를 비교한다.

---

## 지켜야 할 원칙
- `dataset_status: "active"`만 사용한다. 정답 좌석은 반드시 MongoDB `seats`의 active 좌석이다.
- ID는 불투명 문자열로 다룬다. 좌표는 map별 픽셀이고, 거리는 synth 미터값만 쓴다.
- null은 추정하지 않는다. 시뮬레이션에 필요해서 채운 값은 별도 필드와 `*_origin`으로 구분한다.
- 기존 컬렉션은 수정·삭제하지 않는다. 쓰기는 §8의 허용 목록과 `meta.synthetic_manifest_v2`뿐이다.
- 접속 정보는 코드·로그·리포트·출력 파일 어디에도 남기지 않는다.
- 모든 생성은 시드로 고정하고, 수치는 `seat_model/config/synth_v2.yaml` 한 곳에서 관리한다.
