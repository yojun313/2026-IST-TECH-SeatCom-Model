# `library` MongoDB 데이터베이스 스키마 문서

- 조회 일시: 2026-11-14 (접속 정보: 저장소 루트 `.env`의 `MONGO_HOST/PORT/USER/PASSWORD/AUTH_DB`)
- 조회 방법: 컬렉션별 전수(2,000건 초과 컬렉션은 `$sample` 2,000건) 필드·타입 집계 + 주요 필드 값 분포 + 컬렉션 간 FK 정합성 확인
- 컬렉션 수: **15개** — 실데이터 10개, 합성(synthetic) 데이터 5개

---

## 1. 전체 구조 한눈에 보기

| 구분 | 컬렉션 | 문서 수 | `_id` 형식 | 역할 |
|---|---|---:|---|---|
| 실데이터 | `libraries` | 27 | `library_id` 문자열 | 도서관 마스터 |
| 실데이터 | `rooms` | 94 | `room_id` | 열람실 마스터(정원·운영시간·이용규정) |
| 실데이터 | `seats` | 2,111 | `seat_id` | 좌석 위치(지도 픽셀 bbox) |
| 실데이터 | `maps` | 34 | `map_id` | 좌표계를 정의하는 지도/도면/좌석배치 이미지 |
| 실데이터 | `facilities` | 73 | `facility_id` | 화장실·출입구·엘리베이터·계단 위치 |
| 실데이터 | `walkway_nodes` | 6 | `node_id` | 통로 그래프 노드(현재 출입문만) |
| 실데이터 | `room_features` | 29 | `ObjectId` | 열람실 단위 특성(노트북 허용·칸막이 등) |
| 실데이터 | `sources` | 109 | `source_id` | 모든 레코드의 출처(근거) 메타데이터 |
| 실데이터 | `build_reports` | 27 | `library_id` | 도서관별 데이터 구축 결과·검증·누락 보고서 |
| 메타 | `meta` | 2 | `"registry"`, `"synthetic_manifest"` | 구축 대상 레지스트리, 합성 데이터 생성 매니페스트 |
| 합성 | `synth_scenarios` | 720 | `scenario_id` | 열람실별 가상 환경(창문·조명·에어컨·콘센트·책상) |
| 합성 | `synth_seat_env` | 63,330 | `"{scenario_id}\|{seat_id}"` | 시나리오×좌석별 환경 측정값 + 정규화 feature |
| 합성 | `synth_users` | 3,000 | `user_id` | 가상 사용자(선호 프로필 + 잠재 효용) |
| 합성 | `synth_sessions` | 59,496 | `session_id` | 추천 노출·선택·평점 세션 |
| 합성 | `synth_events` | 166,934 | `"{session_id}:{type}"` | 세션 내 이벤트 로그 |

### 1.1 ER 관계도

```
                         ┌──────────────┐
                         │  libraries   │  library_id
                         └──────┬───────┘
          ┌───────────────┬─────┼──────────────┬──────────────┬───────────────┐
          ▼               ▼     ▼              ▼              ▼               ▼
      ┌───────┐      ┌───────┐ ┌──────────┐ ┌───────────┐ ┌────────────┐ ┌──────────────┐
      │ rooms │◄─────│ maps  │ │ sources  │ │facilities │ │walkway_    │ │build_reports │
      └──┬────┘room_id└──┬───┘ └────▲─────┘ └─────▲─────┘ │nodes       │ └──────────────┘
         │               │          │ source_id   │       └─────┬──────┘
         │ room_id       │ map_id   │ (모든 실데이터)│ target_id    │ map_id
         ▼               ▼          │             └─────────────┘
      ┌──────────────────────┐      │
      │        seats         │──────┘
      └──────────┬───────────┘
   room_features ──► rooms (room_id)
                 │ seat_id
     ┌───────────┴───────────────── 합성 데이터 ─────────────────────────────┐
     │  synth_scenarios (library_id, room_id, map_id)                        │
     │        │ scenario_id                                                  │
     │        ▼                                                              │
     │  synth_seat_env (scenario_id + seat_id)                               │
     │        ▲                                                              │
     │        │ scenario_id, exposed[].seat_id, chosen_seat_id               │
     │  synth_sessions ──user_id──► synth_users (home_library_id)            │
     │        │ session_id                                                   │
     │        ▼                                                              │
     │  synth_events (session_id, user_id, targetId → seat_id)               │
     └───────────────────────────────────────────────────────────────────────┘
```

FK 정합성 확인 결과(조회 시점):
- `seats.room_id` → `rooms`, `seats.map_id` → `maps`, `seats.source_id` → `sources`: 누락 0건
- `synth_seat_env.seat_id` → `seats`: 누락 0건, `synth_scenarios.room_id` → `rooms`: 누락 0건
- `seats`/`rooms`에 등장하는 모든 `library_id`는 `libraries`에 존재

### 1.2 ID 명명 규칙

모든 실데이터 ID는 `{library_id}:{entity}:{local_key}` 형태의 네임스페이스 문자열이며, 대부분 `_id`와 `xxx_id` 필드가 동일한 값을 중복 보유한다.

| 엔터티 | 예시 |
|---|---|
| library | `yongin-bora`, `seongnam-central`, `nl-digital` |
| room | `yongin-bora:room:reading-1f`, `seongnam-gumi:room:04`, `yongin-dongbaek:room:1` |
| seat | `yongin-bora:seat:0001`, `nl-digital:seat:laptop-bottom-031`, `yongin-pogok:seat:r1-012` |
| map | `yongin-bora:map:seat-layout-1f`, `yongin-guseong:map:floor-B1` |
| facility | `yongin-bora:facility:toilet-02` |
| source | `yongin-bora:source:facility_page` (일부 도서관은 `:src:` 사용, 예: `yongin-sanghyeon:src:facility_page`) |
| node | `yongin-pogok:node:door-r1` |
| synth scenario | `{library_id}:synth:{map_key}:{room_key}:s{000-029}` |
| synth user/session | `synth-user:00010`, `synth-session:000001` |

`local_key` 부분은 도서관별 구축자가 달라 형식이 통일되어 있지 않다(`01` vs `1` vs `reading-1f`). ID는 반드시 불투명(opaque) 문자열로 다루고 파싱에 의존하지 말 것.

### 1.3 모든 실데이터 컬렉션의 공통 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `library_id` | string | 소속 도서관. 모든 실데이터 레코드에 존재(원래 target_schema에는 없던 필드를 추가한 것 — `build_reports.notes` 참고) |
| `source_id` | string | 근거 출처 → `sources.source_id` |
| `verification` | enum string | 검증 수준. `human_checked`(사람 확인) > `model_candidate`(모델이 추출한 후보) > `unverified`(미검증). 합성 데이터는 `synthetic` |
| `dataset_status` | enum string | `active`(현재 서비스·합성에 사용하는 도서관) / `backup`(구축은 했으나 비활성 — 좌석 데이터가 없거나 미흡) |
| `is_synthetic` | bool | 실데이터는 `false`, `synth_*`는 `true` |

`dataset_status` 분포:

| 컬렉션 | active | backup |
|---|---:|---:|
| libraries | 11 | 16 |
| rooms | 45 | 49 |
| seats | 2,111 | 0 |
| maps | 26 | 8 |
| facilities | 35 | 38 |
| sources | 70 | 39 |
| room_features | 9 | 20 |
| walkway_nodes | 6 | 0 |
| build_reports | 11 | 16 |

> **active 도서관 11곳** = 좌석 데이터가 있는 도서관 = 합성 데이터 생성 대상:
> `yongin-bora`, `yongin-dongbaek`, `yongin-giheung`, `yongin-heungdeok`, `yongin-jukjeon`, `yongin-pogok`, `yongin-sanghyeon`, `yongin-suji`, `yongin-seonong`, `seongnam-central`, `nl-digital`

### 1.4 좌표계 규약

모든 좌표(`seats.bbox`, `facilities.geometry`, `walkway_nodes.point`, `synth_*`의 `*_px`)는 **해당 `map_id` 이미지의 픽셀 좌표**이다.
- 원점 좌상단(`origin: top_left`), x는 오른쪽, y는 아래쪽 증가
- `maps.meters_per_unit`이 전부 `null` → 실측 축척 없음. 미터 단위 거리는 합성 데이터의 `synthetic_meters_per_px`(가정치)로만 계산됨
- 서로 다른 map 간 변환(map_transforms)은 구축되지 않음 → 좌석 지도와 층별도 좌표는 직접 비교 불가

---

## 2. 실데이터 컬렉션 상세

### 2.1 `libraries` (27건)

인덱스: `_id`, `dataset_status_1`

| 필드 | 타입 | null 허용 | 설명 / 예시 |
|---|---|---|---|
| `_id`, `library_id` | string | | `"yongin-sanghyeon"` |
| `name` | string | | 공식 명칭. `"상현도서관"` |
| `address` | string | ✔ (14/27 null) | 도로명 주소 |
| `latitude`, `longitude` | null | 전부 null | 공식 근거가 없어 지오코딩 추정을 금지 → 거리 기반 추천 비활성 |
| `timezone` | string | | 항상 `"Asia/Seoul"` |
| `external_ids.stdg_cd` | string | ✔ (24/27 null) | 법정동 코드, 예: `"4113000000"` |
| `external_ids.pblib_id` | string | ✔ (24/27 null) | 공공도서관 ID, 예: `"PLR007"` |
| `source_id` | string | | |
| `verification` | enum | | model_candidate 22 / unverified 4 / human_checked 1 |
| `dataset_status`, `is_synthetic` | | | 공통 필드 |

수록 도서관: 용인시 11곳(`yongin-*`), 성남시 13곳(`seongnam-*`), 부산 해운대(`busan-haeundae`), 인천 계양(`incheon-gyeyang`), 국립중앙도서관 디지털도서관(`nl-digital`).

```json
{"_id":"yongin-sanghyeon","library_id":"yongin-sanghyeon","name":"상현도서관",
 "address":"경기도 용인시 수지구 법조로 232(상현동 1129-2)","latitude":null,"longitude":null,
 "timezone":"Asia/Seoul","external_ids":{"stdg_cd":null,"pblib_id":null},
 "source_id":"yongin-sanghyeon:src:facility_page","verification":"model_candidate",
 "dataset_status":"active","is_synthetic":false}
```

### 2.2 `rooms` (94건)

인덱스: `_id`, `library_id_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id`, `room_id` | string | | |
| `library_id` | string | | → libraries |
| `name` | string | | 열람실명. 이름 자체에 규칙이 들어간 경우가 많음(`"제1열람실(성인전용/칸막이)"`, `"1열람실(노트북겸용)"`) |
| `floor_id` | string | ✔ (69 null) | `"4F"`, `"B1"` 등. 표기 통일 안 됨(`"F1"` vs `"1F"`) |
| `capacity` | int | ✔ (20 null) | 정원(좌석 수) |
| `assignment` | enum | | `assigned`(좌석 배정제) 9 / `unknown` 85 |
| `usage_rules.laptop_allowed` | bool | ✔ (74 null) | 노트북 허용 여부 |
| `usage_rules.keyboard_allowed` | bool | ✔ (93 null) | 키보드 허용 여부 |
| `usage_rules.minimum_age` / `maximum_age` | int | ✔ | 이용 연령(예: 14 ~ 100) |
| `opening_hours` | object | ✔ (77 null) | 아래 구조 |
| `opening_hours.timezone` | string | | `"Asia/Seoul"` |
| `opening_hours.weekly[]` | array | | `{day, opens, closes, ends_next_day}` |
| `opening_hours.weekly[].day` | int | | 요일. **0=일요일 … 6=토요일** 로 보임(휴관 요일은 항목이 빠짐, 예: 월요일 `1` 누락) |
| `opening_hours.weekly[].opens/closes` | string | | `"HH:MM"` |
| `opening_hours.weekly[].ends_next_day` | bool | | 자정 넘김 여부 |
| `opening_hours.effective_from/until` | string | ✔ | 적용 기간(`"2026-10-12"`) |
| `source_id`, `verification`, `dataset_status`, `is_synthetic` | | | 공통. verification: model_candidate 75 / unverified 15 / human_checked 4 |
| *(선택)* `external_room_id`, `external_room_id_source_id` | string | | 공공 CSV 등 외부 열람실 ID (1건) |
| *(선택)* `official_room_id` | string | | 공식 좌석앱의 열람실 번호 (3건) |
| *(선택)* `capacity_sources[]` | string[] | | 정원 근거가 여러 개일 때 source_id 목록 (3건) |
| *(선택)* `opening_hours_source_id` | string | | 운영시간 근거가 별도일 때 (3건) |

### 2.3 `seats` (2,111건, 전부 active)

인덱스: `_id`, `library_id_1_room_id_1`, `map_id_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id`, `seat_id` | string | | 내부 좌석 ID |
| `library_id`, `room_id`, `map_id` | string | | FK |
| `seat_label` | string | ✔ (~57% null) | 화면/도면에 표시된 좌석 번호. 원본에서 확인 불가하면 null(**번호 추정 금지 원칙**) |
| `external_seat_id` | string | ✔ (~84% null) | 공공 CSV 등 외부 시스템의 좌석 ID |
| `bbox` | object | | `{x, y, width, height}` — 지도 픽셀 좌표의 좌석 사각형(좌상단 기준) |
| `norm_bbox` | float[4] | | `[x/W, y/H, w/W, h/H]` — map 크기로 정규화한 bbox (0~1) |
| `orientation_degrees` | null | 전부 null | 좌석 방향 미구축 |
| `geometry_object_id` | string | 선택 (421건) | 후보 파일 해시 기반 지오메트리 ID(`"YI-3dfeaca1da81348325fb"`). 공식 ID 아님 |
| `source_id`, `verification`(전부 `model_candidate`), `dataset_status`, `is_synthetic` | | | 공통 |

도서관별 좌석 수: nl-digital 353, yongin-dongbaek 287, yongin-heungdeok 236, yongin-jukjeon 234, yongin-sanghyeon 210, seongnam-central 169, yongin-pogok 159, yongin-giheung 148, yongin-bora 134, yongin-seonong 112, yongin-suji 69.

> 주의: `nl-digital`처럼 좌석번호가 구역마다 1부터 다시 시작하는 경우가 있으므로 좌석 식별은 반드시 `seat_id`(또는 `room_id`+`seat_label`)로 한다. nl-digital의 bbox는 책상 셀이 아닌 "도면에 인쇄된 번호 라벨 영역"이다.

```json
{"_id":"yongin-dongbaek:seat:0040","seat_id":"yongin-dongbaek:seat:0040","external_seat_id":null,
 "seat_label":null,"library_id":"yongin-dongbaek","room_id":"yongin-dongbaek:room:1",
 "map_id":"yongin-dongbaek:map:seat_layout:1","bbox":{"x":198.75,"y":350.0,"width":25,"height":20},
 "orientation_degrees":null,"source_id":"yongin-dongbaek:src:seatmate1","verification":"model_candidate",
 "geometry_object_id":"YI-3dfeaca1da81348325fb","dataset_status":"active","is_synthetic":false,
 "norm_bbox":[0.19875,0.5,0.025,0.028571]}
```

### 2.4 `maps` (34건)

인덱스: `_id`, `library_id_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id`, `map_id` | string | | |
| `library_id` | string | | |
| `floor_id` | string | ✔ (12 null) | 층 지도일 때 |
| `room_id` | string | ✔ (17 null) | 열람실 단위 좌석배치도일 때 → rooms |
| `width`, `height` | int | | 픽셀 크기(예: 1000×700, 1905×863) |
| `unit` | string | | 항상 `"pixel"` |
| `origin` / `x_direction` / `y_direction` | string | | 항상 `top_left` / `right` / `down` |
| `meters_per_unit` | null | 전부 null | 실측 축척 없음 |
| `image_path` | string | ✔ (32 null) | 저장소 내 이미지 경로(재배포 가능한 것만, 예: `assets/seat_layout_A.svg`) |
| `map_kind` | enum | | `seat_button_layout` 17 (좌석예약 앱의 좌석 버튼 배치), `floor_plan` 13 (층별 평면도), `perspective_illustration` 4 (투시도 일러스트) |
| `source_id`, `verification`(unverified / model_candidate), `dataset_status`, `is_synthetic` | | | 공통 |

### 2.5 `facilities` (73건)

인덱스: `_id`, `library_id_1_type_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id`, `facility_id` | string | | |
| `library_id`, `map_id` | string | | 좌표는 `map_id` 픽셀 기준 |
| `type` | enum | | `toilet` 30, `elevator` 17, `stairs` 14, `entrance` 12 |
| `geometry` | GeoJSON 유사 | | `{type, coordinates}` — **픽셀 좌표**이며 경위도 아님 |
| `geometry.type` | enum | | `Polygon` 51 (`coordinates`=`[[x,y],...]`, 닫는 점 생략·중첩 1단계), `Point` 14 (`[x,y]`), `LineString` 8 (`[[x,y],[x,y]]`, 출입구 개구부 등) |
| `access_point` | [x, y] | ✔ (63 null) | 실제 진입 지점(문 위치) |
| `attributes` | object | | 자유 형식. 아래 키들이 혼재 |
| `source_id`, `verification`(전부 model_candidate), `dataset_status`, `is_synthetic` | | | 공통 |

`attributes`에 등장하는 키(구축자별로 다름):

| 키 | 의미 |
|---|---|
| `label`, `label_on_image` | 도면 표기 라벨(`"화장실(남)"`, `"E.V"`) |
| `gender` | `female` / `male` / `accessible` |
| `toilet_kind` | `children` / `male_labeled` / `female_labeled` |
| `accessible` | bool, 장애인용 |
| `floor_id`, `floor`, `floor_label` | 층 표기(`"F3"`, `"1F"`, `"본관 1층"`) |
| `connects_room_id` | 출입구가 연결하는 room_id |
| `direction` | `entry` / `exit` |
| `coord_error_px`, `coordinate_error_px` | 좌표 오차 추정(`"약 ±8px"`) |
| `basis`, `extraction`, `position_note`, `note`, `door_note`, `door_location`, `door_position`, `icon`, `geometry_precision` | 추출 근거·한계 설명 문자열 |

### 2.6 `walkway_nodes` (6건)

인덱스: `_id`

| 필드 | 타입 | 설명 |
|---|---|---|
| `_id`, `node_id` | string | |
| `library_id`, `map_id` | string | |
| `point` | [x, y] | 픽셀 좌표 |
| `kind` | enum | 현재 `door`만 존재 |
| `target_id` | string | 연결된 facility_id (출입구) |
| `source_id`, `verification`, `dataset_status`, `is_synthetic` | | 공통 |

통로 그래프의 엣지(walkway_edges)·지도 변환(map_transforms) 컬렉션은 **존재하지 않음**. 노드도 yongin-pogok, yongin-sanghyeon, nl-digital의 출입문뿐이다.

### 2.7 `room_features` (29건)

열람실 단위로 적용되는 좌석 특성. 인덱스: `_id`(ObjectId), `room_id_1_name_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id` | ObjectId | | (유일하게 ObjectId를 쓰는 실데이터 컬렉션) |
| `room_id`, `library_id` | string | | |
| `name` | enum | | feature 이름: `laptopAllowed` 21, `partition` 7, `keyboardAllowed` 1 (feature registry `features-1`의 ID와 동일) |
| `value` | bool | | 현재 전부 `true` |
| `unit` | null | | |
| `applies_to_all_seats` | bool | ✔ | 방 안 전 좌석에 적용되는지 |
| `method` | enum | | `model_inference` 13, `diagram_extraction` 12, `user_report` 4 |
| `confidence` | float | ✔ | 0.7, 0.8 등 |
| `context` | object | | 근거 설명: `basis`(필수), `observed_on`, `reference_date`, `data_date`, `room_name`, `observed_name`, `observed_in`, `limitation`/`limit`/`currency_limit`, `currentness`, `note`, `method_note`, `timezone` |
| *(선택)* `normalized_value`, `normalization_version` | null | | 1건 |
| `source_id`, `verification`, `dataset_status`, `is_synthetic` | | | 공통 |

> 대부분 "열람실명에 '노트북'이 들어있다" 수준의 명칭 기반 근거라 규정 원문 확인은 안 된 값이다(`context.limitation` 참고).

### 2.8 `sources` (109건)

인덱스: `_id`, `library_id_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id`, `source_id` | string | | |
| `library_id` | string | | |
| `source_type` | enum | | `official_web` 67, `public_dataset` 34, `public_work` 7, `user_supplied` 1 |
| `url` | string | ✔ | 원본 URL (data.go.kr, lib.yongin.go.kr 등) |
| `sha256` | string | ✔ | 수집 파일/추출물 해시 |
| `document_date` | string | ✔ | 원본 문서 기준일(`"2025-11-27"`) |
| `collected_on` | string | | 수집일(전부 `"2026-11-14"`) |
| `license_status` | string | | 재배포·robots.txt 확인 결과 등 자유 서술 |
| `notes` | string | | 수집 방법 설명 |
| `local_path` | string | ✔ | 저장소 내 사본 경로(`data/source_data/public_datasets/15032433.csv`) |
| `dataset_status`, `is_synthetic` | | | 공통 (sources에는 `verification` 없음) |

### 2.9 `build_reports` (27건, 도서관당 1건)

도서관별 데이터 구축 작업 결과 보고서. `_id` = `library_id`. 인덱스: `_id`, `library_id_1`.
구축 에이전트마다 작성 형식이 달라 **선택 필드가 매우 많은 반정형 문서**다.

공통 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `library_id` | string | |
| `status` | enum | `discovery` 14 (조사 단계, 좌석 미구축) / `partial` 13 (일부 구축) |
| `dataset_version` | string | `"draft-1"` |
| `reference_date` | string | `"2026-11-14"` |
| `dataset_status` | enum | active / backup |
| `seat_inventory` | object | `expected_count`(공식 좌석 수, null 가능), `expected_count_source_id`, `extracted_count`(추출 좌석 수), `identity_verified_count`(번호까지 확인된 좌석), `geometry_verified_count`(위치 검수 좌석, 전부 0), *(선택)* `notes` |
| `facility_coverage[]` | array | `{type, expected_count, mapped_count, verification, missing_reason, note?}` — 시설 유형별 커버리지 (toilet, entrance, elevator, stairs, air_conditioner, …) |
| `feature_coverage[]` | array | feature별 커버리지. 키 이름이 구축자마다 다름: `name`/`feature`, `observed_seat_count`/`observed_seats`/`observed_count`, `verified_*`, `target_*`, `scope`/`level`(room/seat), `missing_reason`, `note`, `source` 등 |
| `checks[]` | array | 검증 체크. 문자열이거나 `{check, passed, detail}` / `{name, result, note, checked_on}` / `{status, message, value}` 형식 혼재 |
| `blockers[]` | string[] | 구축을 막는 문제(좌석번호 미공개, 위경도 근거 없음 등) |
| `next_actions[]` | string[] | 후속 작업 |

선택 필드(일부 문서에만 존재): `scope_declared`, `scope`, `scope_summary`, `seat_inventory_notes`, `seat_inventory_by_zone`, `seat_identity_registry`, `seat_geometry_object_ids`(seat_id → geometry_object_id 맵), `room_inventory`, `data_present`, `visible_room_zones`(도면상 방 구획 polygon 참고자료), `facility_floor_plan_extraction`, `extraction_method`, `reference_counts`, `notes`, `notes_schema`.

> 분석 시 이 컬렉션은 정형 테이블로 쓰기보다 "도서관별 데이터 품질/한계 설명서"로 읽는 것이 적절하다.

### 2.10 `meta` (2건)

**`_id: "registry"`** — 구축 작업 목록
```json
{"_id":"registry","items":[{"task_id":"LIB-BUILD-001","library_id":"yongin-central","name":"중앙도서관"}, ... 27개]}
```

**`_id: "synthetic_manifest"`** — 합성 데이터 생성 설정
| 필드 | 값 |
|---|---|
| `generator_version` | `"synth-1.0"` |
| `seed` | 20261114 |
| `params` | `{scenarios: 30, users: 3000, sessions: 60000, seed}` (열람실당 시나리오 30개) |
| `feature_registry_version` | `"features-1"` |
| `libraries` | active 도서관 11개 |
| `room_units` | 24 (합성 대상 열람실 수 → 24 × 30 = 720 시나리오) |
| `counts` | 각 synth 컬렉션 문서 수 |
| `assumptions.seat_button_side_m` | 0.75 (좌석 버튼 한 변 = 0.75m로 가정해 px→m 축척 산출) |
| `assumptions.time_slots_hour` | `{morning: 10, noon: 13, evening: 19}` |
| `assumptions.note` | 모든 환경 객체·측정값·사용자·이벤트는 합성. 실좌석 좌표/ID와 같은 지도의 실제 출입구·화장실 후보만 실데이터에서 가져옴 |
| `generated_at` | `"2026-11-14T21:39:53+09:00"` |

---

## 3. 합성 데이터 컬렉션 상세

공통 필드: `is_synthetic: true`, `verification: "synthetic"`, `generator_version: "synth-1.0"`.
**실좌석(`seats`) 좌표·ID 위에 가상의 환경과 사용자 행동을 생성한 것**이며 추천 모델 학습/평가용이다.

### 3.1 `synth_scenarios` (720건 = 24개 열람실 × 30 시나리오)

인덱스: `_id`, `library_id_1_room_id_1`

| 필드 | 타입 | 설명 |
|---|---|---|
| `_id`, `scenario_id` | string | `yongin-bora:synth:seat-layout-1f:reading-1f:s000` |
| `scenario_index` | int | 0 ~ 29 |
| `library_id`, `library_name`, `room_id`, `room_name`, `map_id` | string | 실데이터 FK + 표시명 |
| `map_size_px` | [W, H] | |
| `synthetic_meters_per_px` | float | 가정 축척(예: 0.027) |
| `room_bounds_px` | [x0, y0, x1, y1] | 좌석 분포로 잡은 방 경계 |
| `seat_count` | int | |
| `conditions.season` | enum | `spring_fall` / `summer` / `winter` |
| `conditions.weather` | enum | `clear` / `cloudy` / `rain` |
| `conditions.base_temperature_c` | float | 기준 실내온도 |
| `conditions.laptop_allowed`, `keyboard_allowed` | bool | |
| `conditions.laptop_rule_origin` | enum | `real_room_feature`(room_features에서 가져옴) / `synthetic` |
| `objects[]` | array | 환경 객체 목록(아래) |
| `desk_groups[]` | array | 책상 그룹(아래) |
| `brightness_grid` | object | 조도 격자: `slot`(`noon`), `unit`(`lux`), `cols`, `rows`, `origin_px`[x,y], `cell_px`[w,h], `values`[rows][cols] (int lux) |
| `counts` | object | 객체 유형별 개수: `window`, `toilet`, `air_conditioner`, `ceiling_light`, `outlet`, `entrance` |

`objects[]` — `type`별 필드 (모든 객체에 `type`, `origin` 존재; `origin`은 `synthetic` 또는 `real_model_candidate`(실데이터 facilities에서 가져온 출입구)):

| type | kind | 위치 필드 | 추가 필드 |
|---|---|---|---|
| `entrance` | – | `point`, `wall`(top/left/bottom/right) | |
| `window` | – | `line`[[x,y],[x,y]], `wall` | `view_score`(0~1, 전망), `blind_closed`(bool) |
| `toilet` | – | `point` (방 밖 좌표일 수 있음, 음수 가능) | |
| `air_conditioner` | `ceiling_cassette` / `wall_mounted` / `standing` | `point` | `direction`([dx,dy] 또는 null), `strength`(0~1), `throw_m`(도달거리 m) |
| `ceiling_light` | – | `point` | `lux_at_desk_below` |
| `outlet` | `wall` / `desk` | `point` | `desk_group`(desk 콘센트일 때 그룹 번호, 아니면 null) |

`desk_groups[]`:

| 필드 | 타입 | 설명 |
|---|---|---|
| `desk_group` | int | 그룹 번호 |
| `seat_ids` | string[] | 같은 책상을 공유하는 seat_id |
| `desk_type` | enum | `four_person_table`, `individual_carrel`, `wide_individual_desk`, `long_shared_table`, `standing_desk`, `window_counter` |
| `width_cm`, `height_cm` | int | 책상 크기 |
| `partition` | bool | 칸막이 유무 |
| `desk_outlet` | bool | 책상 콘센트 유무 |

### 3.2 `synth_seat_env` (63,330건 = 시나리오별 좌석)

인덱스: `_id`, `scenario_id_1`, `seat_id_1`, `library_id_1`
`_id` = `"{scenario_id}|{seat_id}"`

| 필드 | 타입 | 설명 |
|---|---|---|
| `scenario_id`, `library_id`, `room_id`, `map_id`, `seat_id` | string | FK |
| `seat_label` | string \| null | seats에서 복사 |
| `center_px` | [x, y] | 좌석 bbox 중심 |
| **`raw`** | object | 물리량(원시값) |
| `raw.dist_window_m` | float | 가장 가까운 창문까지 직선거리(m) |
| `raw.dist_toilet_walk_m` | float | 화장실까지 보행거리(m) |
| `raw.dist_entrance_walk_m` | float | 출입구까지 보행거리(m) |
| `raw.dist_outlet_m` | float | 콘센트까지 거리(m, 책상 콘센트면 0) |
| `raw.dist_ac_m` | float | 에어컨까지 거리(m) |
| `raw.airflow_index` | float | 체감 기류 0~1 |
| `raw.temperature_c` | float | 체감 온도(℃) |
| `raw.lux.{morning,noon,evening}` | int | 시간대별 조도(lux) |
| `raw.noise_index` | float | 소음 지수 0~1 |
| `raw.desk_group`, `desk_type`, `width_cm`, `height_cm`, `partition`, `desk_outlet` | | 소속 책상 정보 |
| **`features`** | object | 추천 엔진 입력용 정규화 feature (registry `features-1`) |
| `features.laptopAllowed`, `keyboardAllowed`, `partition`, `outletAvailable` | int 0/1 | 이진 feature |
| `features.windowProximity`, `toiletProximity`, `entranceProximity` | float 0~1 | 근접도(1=가까움) |
| `features.brightness` | float 0~1 | noon 기준 밝기 |
| `features.airflow` | float 0~1 | = `raw.airflow_index` |
| `features.deskHeight`, `deskWidth` | float 0~1 | 정규화 책상 크기 |
| `features.viewQuality` | float 0~1 | 창 전망 품질 |
| `brightness_by_slot.{morning,noon,evening}` | float 0~1 | 시간대별 정규화 밝기 |
| `feature_registry_version` | string | `"features-1"` |

> 동일 좌석이 시나리오 30개에 걸쳐 서로 다른 환경값을 가진다. 세션의 `scenario_id` + 좌석 `seat_id`로 조인하면 그 세션 시점의 좌석 feature를 얻는다.

### 3.3 `synth_users` (3,000건)

인덱스: `_id`, `home_library_id_1`, `archetype_1`

| 필드 | 타입 | 설명 |
|---|---|---|
| `_id`, `user_id` | string | `synth-user:00010` |
| `archetype` | enum | `comfort_seeker` 528, `heat_sensitive` 506, `laptop_worker` 506, `casual_reader` 495, `exam_focus` 483, `frequent_break` 482 |
| `home_library_id` | string | 주 이용 도서관 |
| `secondary_library_ids` | string[] | 보조 이용 도서관 |
| `visit_rate_per_week` | float | 주당 방문 횟수 |
| **`kakaotalk_profile`** | object | 카카오톡 대화에서 LLM이 추출했다고 가정한 **관측 선호**(노이즈 포함) |
| `kakaotalk_profile.participant_id` | string | = user_id |
| `kakaotalk_profile.preferences.<카테고리>` | object | 카테고리 7개, 각 `{preference, confidence, inference_type, reason, is_correct_vs_latent?}` |
| `kakaotalk_profile.evidence` | array | 현재 전부 빈 배열 |
| `kakaotalk_profile.behavioral_summary` | string | `"합성 사용자(exam_focus)"` |
| `kakaotalk_profile.seat_recommendation` | null | |
| `kakaotalk_profile.follow_up_question` | string | 빈 문자열 |
| **`preference_profile`** | object | 추천 엔진에 넣는 선호 프로필 |
| `preference_profile.revision` | int | 1 |
| `preference_profile.featureRegistryVersion` | string | `"features-1"` |
| `preference_profile.preferences[]` | array | `{featureId, target(0~1), importance(0~1)}` |
| `requirements[]` | array | 하드 제약 `{featureId, operator, value}` — 현재 `{laptopAllowed, "eq", 1}`만 존재 |
| **`latent`** | object | 시뮬레이션용 **참값**(모델 입력으로 쓰면 안 됨) |
| `latent.preferences.<카테고리>` | string | 실제 선호 |
| `latent.utility_weights.<featureId>` | object | `{target, weight}` — 효용함수 가중치 (brightness, airflow, deskWidth는 항상, 나머지는 사용자별) |
| `latent.noise_aversion` | float | 소음 회피 정도 |
| `latent.rationality` | float | 선택 합리성(softmax 온도 역수) |
| `latent.rec_trust` | float | 추천 신뢰도 |

선호 카테고리와 값:

| 카테고리 | preference 값(관측) | latent 값(참값) | 대응 featureId |
|---|---|---|---|
| `restroom` | near / no_preference / unknown | near / no_preference | toiletProximity |
| `lighting` | bright / dim / unknown | bright / dim | brightness |
| `air_conditioning` | cool / warm / unknown | cool / warm | airflow |
| `desk` | large / compact / unknown | large / compact | deskWidth |
| `laptop_usage` | yes / no / unknown | yes / no | laptopAllowed |
| `outlet` | required / not_required / unknown | required / not_required | outletAvailable |
| `view` | window / no_preference / unknown | window / no_preference | windowProximity, viewQuality |

- `inference_type`: `direct`(직접 언급) / `contextual`(맥락 추론) / `unknown`(근거 부족, confidence 0)
- `is_correct_vs_latent`: 관측 선호가 참값과 일치하는지(unknown이면 필드 없음) — 추출 노이즈 평가용

### 3.4 `synth_sessions` (59,496건)

인덱스: `_id`, `user_id_1_occurred_at_1`, `scenario_id_1`, `library_id_1`

| 필드 | 타입 | null | 설명 |
|---|---|---|---|
| `_id`, `session_id` | string | | |
| `user_id` | string | | → synth_users |
| `library_id`, `room_id`, `scenario_id` | string | | → synth_scenarios / synth_seat_env |
| `time_slot` | enum | | `morning` 20,907 / `noon` 23,706 / `evening` 14,883 |
| `occurred_at` | string (ISO 8601, +09:00) | | 2026-09 ~ 2026-10. **문자열 저장**이므로 날짜 연산 시 변환 필요 |
| `occupancy_rate` | float | | 혼잡도 0~1 |
| `available_seat_ids_count` | int | | 빈 좌석 수 |
| `eligible_count` | int | | 요구조건(requirements) 통과 좌석 수 |
| `ranking_basis` | enum | | `preferences`(개인화) 55,637 / `unpersonalized` 3,859 |
| `exposed[]` | array | | 추천 노출 목록 `{seat_id, rank, engine_score(null 가능)}` (보통 5개) |
| `chosen_seat_id` | string | ✔ | 최종 선택 좌석(이탈 시 null) |
| `choice_origin` | enum | ✔ | `recommendation` / `outsideRecommendations` |
| `chosen_true_utility` | float | ✔ | latent 기준 선택 좌석의 참 효용 |
| `negative_seat_ids` | string[] | | 학습용 네거티브 샘플(선택되지 않은 빈 좌석) |
| `rating` | int 1~5 | ✔ | 사용 후 평점 |

### 3.5 `synth_events` (166,934건)

인덱스: `_id`, `session_id_1`, `user_id_1_type_1`
`_id` = `"{session_id}:{type}"` (세션당 타입별 최대 1건)

공통: `event_id`, `session_id`, `user_id`, `type`, `occurred_at`(ISO 문자열), `is_synthetic`

| `type` | 건수 | 추가 필드 |
|---|---:|---|
| `recommendationExposed` | 56,306 | `visibleTargetIds[]` — 노출된 seat_id 목록 |
| `candidateSelected` | 51,261 | `targetId`(seat_id), `origin`(`recommendation`/`outsideRecommendations`) |
| `useConfirmed` | 36,138 | `targetId` |
| `ratingSubmitted` | 20,403 | `targetId`, `rating`(1~5), `corrections[]`(현재 전부 빈 배열) |
| `candidateRejected` | 2,826 | `targetId`, `reason`(`synthetic_dislike`), `excludeFromFuture`(bool) |

이벤트 퍼널: 노출 → 후보 선택 → 사용 확정 → 평점 (거절은 분기).

---

## 4. 사용 시 주의사항 요약

1. **active만 쓸 것**: 서비스/학습에는 `dataset_status: "active"`로 필터링. backup 도서관은 좌석이 없다.
2. **verification 확인**: 실좌석 좌표는 전부 `model_candidate`(사람 검수 전). `geometry_verified_count`는 전 도서관 0.
3. **좌표는 map별 픽셀**: map 간 변환·실측 축척이 없다. 미터 거리는 합성 가정치(`synthetic_meters_per_px`)뿐.
4. **null은 "확인 불가"를 의미**: `seat_label`, `latitude`, `usage_rules.*` 등의 null은 추정 금지 원칙에 따른 의도적 null이다.
5. **반정형 필드**: `facilities.attributes`, `room_features.context`, `build_reports`의 대부분 필드는 구축자별로 키가 다르다.
6. **latent 누출 금지**: `synth_users.latent`, `synth_sessions.chosen_true_utility`, `kakaotalk_profile.preferences.*.is_correct_vs_latent`는 시뮬레이션 참값이므로 모델 입력 feature로 사용하면 안 된다(평가용).
7. **시간 필드는 문자열**: `occurred_at`, `collected_on`, `opening_hours.*` 등은 BSON Date가 아니라 문자열이다.
8. **조인 키**:
   - 세션의 좌석 feature: `synth_sessions.scenario_id` + `exposed[].seat_id` → `synth_seat_env._id = "{scenario_id}|{seat_id}"`
   - 좌석 메타: `seat_id` → `seats`
   - 사용자 선호: `user_id` → `synth_users.preference_profile`
