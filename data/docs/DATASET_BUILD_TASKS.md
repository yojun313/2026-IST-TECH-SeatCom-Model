# 도서관별 좌석·시설 데이터셋 구축 작업 지시서

기준일: **2026-11-14**. 다음 에이전트가 실제 데이터를 만드는 작업의 기준 문서다. API 서버 구현이나 추천 모델 학습을 하는 작업이 아니다. 학교도서관은 제외한다.

## 1. 읽을 순서와 작업 배정

1. 이 문서: 필수 데이터·형식·납품 기준.
2. [도서관별 작업표 CSV](../catalog/library_build_tasks.csv) 또는 [JSON](../catalog/library_build_tasks.json): 직접 검토한 **27개관 각각의 첫 작업·기존 수량·원본 URL**. 작업ID를 선택해 배정한다.
3. [HANDOFF.md](HANDOFF.md): 해당 도서관의 기존 추출 방법·정합 문제·원본 상세.
4. [target_schema.json](target_schema.json): 최종 번들 **v1.1**의 필드·타입을 검사한다.
5. [API 명세](BACKEND_MODEL_API_SPEC.md): 데이터가 어떤 추천과 좌표 응답에 쓰이는지 확인한다.

참고 저장소의 [91기관 후보](../catalog/reference_library_candidates.json)·[222개 열람실 링크](../catalog/reference_room_candidates.json)는 별도 발견 목록이다. 원본을 직접 검증한 목록이 아니며 27관과 단순 합산하지 않는다. `(법정동코드,도서관ID)`로 대조하고 새 기관의 원본을 확인한 뒤 별도 작업을 추가한다. 이름이 같은 중앙도서관들을 합치지 않는다.

작업표의 `library_id`는 JSON에서 미배정 null, CSV에서 공란이다. `작업ID`는 업무 배정 번호이며 서비스의 도서관 ID가 아니다. 실제 구축 시작 시 내부 전역 ID를 정하고 모든 파일에서 재사용한다.

## 2. 도서관 하나당 납품할 디렉터리

```text
data/libraries/real/{library_id}/
├── dataset.json             # 아래 배열들을 합친 검증 대상 번들
├── libraries.json           # 해당 기관 1행 배열
├── rooms.json
├── maps.json
├── seats.json
├── facilities.json
├── room_features.json
├── seat_features.json
├── walkway_nodes.json
├── walkway_edges.json
├── map_transforms.json
├── sources.json
├── build_report.json        # 완료 범위·결측·검수·남은 작업
└── assets/                  # 이용조건이 확인된 정적 도면·배경만
```

- JSON을 기준 산출물로 한다. `dataset.json`은 `schema_version:"1.1"`과 위 11종 배열을 포함한다. 개별 파일은 해당 배열과 값·행 수가 같아야 한다.
- 표 형태 파일은 CSV 사본도 제공한다. UTF-8 BOM, 한글 컬럼, 마지막 컬럼명은 `데이터기준일자`로 하고 값은 `YYYY-MM-DD` 형식으로 기록한다. 중첩 bbox·좌표 배열·context는 CSV 셀 안에 **JSON 문자열**로 저장하고 Python repr로 쓰지 않는다.
- JSON은 UTF-8, 미확인은 `null`. 빈 배열은 ‘관측·등록 레코드 없음’이며 실제 시설 부재를 뜻하지 않는다.
- 도서관별 레코드에는 해당 `library_id`를 넣는다. 각 CSV의 한글 헤더와 필드 순서는 [`data/libraries/scripts/export_csv.py`](../libraries/scripts/export_csv.py)가 정본이며, 중첩 값은 JSON 문자열로 내보낸다. JSON 번들을 수정한 뒤 이 스크립트를 실행해 CSV 사본을 다시 생성한다.
- 빈 파일과 가짜 좌표를 먼저 양산하지 않는다. 자료가 없는 항목은 `[]` 또는 허용된 `null`로 두고 미구축 사유를 build_report에 기록한다.
- assets에 브라우저 개인 영역·현황 색상·성별·이용 시각이 든 캡처나 원본 현황 HTML을 복제하지 않는다. 사용자가 제공한 원본은 기존 업로드 경로와 해시로 추적하고, 배포용은 상태를 제거한 좌석도나 허용된 정적 원본만 만든다.

## 3. 필수 데이터 목록과 형태

필드명은 정적 데이터의 **snake_case**다. API의 camelCase는 백엔드 어댑터에서 변환한다. 예를 들어 `seat_id→seatId`, `map_id→mapId`, `external_seat_id→externalSeatId`다.

| 파일 | 레코드 단위 | 반드시 구축할 정보 |
|---|---|---|
| libraries.json | 도서관 1개 | library_id, name, address, latitude, longitude, timezone, external_ids{stdg_cd,pblib_id}, source_id, verification |
| rooms.json | 열람실/구역 1개 | room_id, library_id, name, floor_id, capacity, assignment, usage_rules, opening_hours, source_id, verification |
| maps.json | 좌표 프레임/도면 1개 | map_id, library_id, floor_id, room_id, width, height, unit, origin, x_direction, y_direction, meters_per_unit, image_path, map_kind, source_id, verification |
| seats.json | 실제 좌석 1개 | seat_id, external_seat_id, seat_label, library_id, room_id, map_id, bbox{x,y,width,height}, orientation_degrees, source_id, verification |
| facilities.json | 시설/시설 영역 1개 | facility_id, library_id, map_id, type, geometry, access_point, attributes, source_id, verification |
| room_features.json | 열람실의 특성 1개 | room_id, name, value, unit, context, method, source_id, confidence, verification, applies_to_all_seats |
| seat_features.json | 좌석의 특성 1개 | seat_id, name, value, unit, context, method, source_id, confidence, verification |
| walkway_nodes.json | 통행 연결점 1개 | node_id, map_id, point[x,y], kind, target_id, source_id, verification |
| walkway_edges.json | 이동 연결 1개 | from_node_id, to_node_id, length, unit, accessible, source_id, verification |
| map_transforms.json | 도면 간 좌표 변환 1개 | from_map_id, to_map_id, kind, matrix(3×3), control_points[{from:[x,y],to:[x,y]}], source_id, verification |
| sources.json | 근거 원본 1개 | source_id, source_type, url, sha256, document_date, collected_on, license_status, notes. local_path는 선택 |

`verification`은 `unverified|model_candidate|human_checked|field_verified`다. AI 추출은 model_candidate이며, 실제 원본과 전수 대조한 경우에 human_checked로 변경한다. OCR 신뢰도를 검수 완료로 취급하지 않는다.

### 마스터·운영시간

- 도서관 위도·경도는 건물 위치의 도 단위 값이다. 실내 좌석 좌표를 위경도에 섞지 않는다. 좌표가 없으면 null이며 주변 도서관 거리 추천은 비활성화한다.
- `assignment`: assigned/free/unknown. `usage_rules`는 laptop_allowed, keyboard_allowed, minimum_age, maximum_age를 가지며 미상은 null이다.
- `opening_hours`는 미상이면 null. 확인되면 timezone, weekly[{day,opens,closes,ends_next_day}], effective_from, effective_until로 저장한다. day는 **월요일0~일요일6**, 시각은 HH:MM이다. 자정을 넘으면 closes="00:00", ends_next_day=true처럼 표현한다.
- 특정 시험기간의 운영시간을 상시 운영시간으로 옮기지 않는다. 휴관일·예외일이 해결되지 않았으면 운영 중이라고 자동 확정하지 말고 보고서에 제한을 남긴다.

### 좌표·지도

- 현재 구축은 **unit=pixel**, 좌상단 원점, X 오른쪽/Y 아래를 기본으로 한다. 정규화는 `x/width`, `y/height`, `bbox.width/map.width`, `bbox.height/map.height`로 파생한다.
- 모든 geometry는 해당 map_id의 같은 프레임을 사용한다. `width/height`는 이미지 파일명에서 추정하지 않고 실제 크기 또는 명시한 표시 프레임으로 정한다.
- `map_kind`: floor_plan/room_plan/seat_button_layout/perspective_illustration. 버튼 배치는 건축 평면도가 아니며 실제 책상 크기나 통로를 보장하지 않는다.
- image_path는 assets 아래 상대 경로 또는 null이다. 이미지가 없으면 좌석도만으로 렌더링할 수 있지만 원본 도면이 있다고 주장하지 않는다.
- meters_per_unit은 치수·실측으로 검증된 경우만 기록한다. 값이 없으면 null이고 미터 거리를 만들지 않는다.
- `geometry` 형식은 `Point:{coordinates:[x,y]}`, `LineString:{coordinates:[[x,y],...]}`, `Polygon:{coordinates:[[x,y],...]}`이다. **이 스키마의 Polygon은 평면 꼭짓점 목록이며 GeoJSON의 중첩 ring 형식이 아니다.**
- 층별도와 방 도면은 map_id를 분리한다. affine은 비공선 대응점 3개 이상, homography는 적절히 분포한 대응점 4개 이상과 중첩 검수를 요구한다. 공통점이 없으면 변환행렬을 꾸미지 않고 방 출입구 노드로만 연결한다.

## 4. 시설마다 수집할 내용

| 시설 | type / geometry | 추가 확인 |
|---|---|---|
| 화장실 | toilet / Polygon 또는 Point | access_point에 **화장실 문** 위치, 층, 남녀·공용·장애인 여부 |
| 출입구 | entrance / Point 또는 LineString | 통행 방향·출입 제한, 연결되는 방/복도 |
| 에어컨 | air_conditioner / Point 또는 Polygon | 실제 기기 위치. 송풍구는 air_diffuser로 별도 저장 |
| 조명 | light / Point 또는 LineString | 등기구 위치·종류. 실제 조도는 좌석 특성으로 별도 측정 |
| 콘센트 | outlet / Point | 포트 수, 작동 확인, 접근 가능한 좌석과 케이블 제약 |
| 창문 | window / LineString 또는 Polygon | 방향·가림 여부. 전망 평가와 동일시하지 않음 |
| 기둥·벽 | column / Polygon; wall / LineString 또는 Polygon | 장애물과 실제 통행 가능 경로. 벽의 두께를 모르면 임의로 면적을 생성하지 않음 |
| 기타 | water_dispenser/elevator/stairs | 위치와 층 연결. 계단 길이·층간 거리를 모르면 임의 0m 연결 금지 |

시설의 attributes에는 근거가 있는 속성만 넣는다. 통행 불가 벽·영역은 원본 도면과 경로 검수 근거로 남긴다. v1.1의 wall 레이어와 실제 도면을 대조해 `walkway_edges`가 벽을 관통하지 않는지 확인해야 한다.

**에어컨·콘센트·조명 위치가 도면에 안 나오면:** 설비도·전기도·허용된 사진·현장 조사 중 실제 근거를 확보한다. 끝내 확보하지 못하면 미구축으로 보고하고 해당 추천 기능을 비활성화한다. AI의 추측을 실제 위치로 만들지 않는다.

## 5. 취향 추천용 특성

피처 ID는 [API 명세의 레지스트리](BACKEND_MODEL_API_SPEC.md)와 맞춘다.

| name | 저장할 원시 값·단위 | 원본이 없는 경우 |
|---|---|---|
| laptopAllowed, keyboardAllowed, partition, outletAvailable | true/false, unit=null | null. 일반열람실·벽 근처라는 이유로 추정 금지 |
| windowProximity | 창문까지 검증된 거리, meter | null |
| toiletProximity, entranceProximity | 문/복도 그래프 최단거리, meter | 그래프·축척이 없으면 null |
| brightness | 조도, lux; 측정 시각·차광 상태를 context에 저장 | null |
| airflow | 합의된 바람 평가/측정값과 단위·조건 | 기기 위치만으로 값 생성 금지 |
| deskHeight, deskWidth | 실제 높이·폭, centimeter | 치수·실측 없으면 null |
| viewQuality | 같은 척도의 실제 시야 평가와 기준 | 창가라는 이유로 만점 생성 금지 |
| desk_shape, desk_depth | 개인형/공용형/원형 등 원시 분류, 깊이 centimeter | 보이는 형태·치수만 저장. 현재 모델에 전송할 피처는 아니며 후속 계약 확장 필요 |

- value와 unit은 원시 관측값이다. **모델 API의 value는 0~1이므로 그대로 보내면 안 된다.**
- `normalized_value`, `normalization_version`은 선택 필드다. API features-1의 고정 규칙으로 계산한 경우만 두 필드를 함께 기록한다. 모르는 정규화는 null/미제공으로 둔다.
- 예: deskHeight value=72, unit=centimeter이면 normalized_value=(72-60)/40=0.3, normalization_version=features-1이다.
- ‘raw toilet distance=30m’와 ‘정규화된 근접도=0.7’을 같은 value에 혼용하지 않는다. pixel 거리를 meter 거리처럼 정규화하지 않는다.
- context에는 관측 시각·시간대·측정 조건·측정 방법·현재성 제한을 기록한다. 동일 좌석·특성에 여러 관측이 있으면 백엔드가 유효한 값을 선택할 수 있어야 한다.
- room_features.applies_to_all_seats는 그 특성이 모든 좌석에 적용되는지 확인한 경우만 true다. 일부 콘센트·일부 칸막이를 전 좌석에 상속하지 않는다.
- method는 diagram_extraction/field_measurement/sensor/user_report/model_inference. source_id가 없는 관측값은 운영 추천에 사용하지 않는다.

## 6. 도서관별 착수 범위

상세 URL과 개별 수량은 작업표/HANDOFF가 기준이다. 아래 수량은 과거 자료·현재 추출 건수이며 현재 정원 확정값이 아니다.

| 그룹 | 도서관 | 첫 작업 |
|---|---|---|
| 사용자 캡처 있음 | 성남중앙 | A169개 전수 번호 검수, B/4층 추가 원본 확보, 실제 층별 시설·통로 결합. 과거 CSV608석과 캡처671석 차이 해결 |
| 전체 개수의 사각형 있음 | 용인 보라134·동백287·흥덕236·죽전234·상현210·수지69 | 좌석번호·공식ID 전수 대응, 프레임 검증, 시설 원본 확보. 흥덕 CSS/원본1.5배 차이와 동백 배경번호 불일치 우선 해결 |
| 일부 사각형 있음 | 용인 기흥 | 148개 검수와 기존164개 대비 누락16개 원본 확인 |
| 개별 좌석 원본 부족 | 용인 중앙·구성·포곡·서농 | 번호 포함 도면/허용된 화면 확보. 공개 링크 밖 숨은 경로 추측 금지 |
| 개별 좌석 원본 미확보 | 성남 구미·무지개·분당·수정·중원·운중·판교·판교어린이·해오름·서현·복정·위례 | 제공받은 캡처·PDF 또는 별도 허용 배포본 확보. snlib 직접 수집 금지 확인을 우회하지 않음 |
| 과거 도면 있음 | 국립중앙 디지털도서관·해운대 | 현행 배치와 대조 후 추출. 도면에서 보이지 않는 시설은 별도 확보 |
| 방/시설도 있음 | 계양 | 개별 번호도 확보 후 방·화장실·좌석 연결 |

모든 기관에 같은 파일 구조를 사용하되, 자료가 없는 기관을 억지로 완성하지 않는다. **실제로 채운 범위와 부족한 종류를 기관마다 따로 보고**한다.

## 7. build_report.json과 완료 기준

보고서는 다음 키를 가진 객체로 만든다. 이 보고서는 dataset.json의 데이터 배열과 별도 파일이다.

```json
{
  "library_id": "EXAMPLE-ID-NOT-A-REAL-LIBRARY",
  "status": "partial",
  "dataset_version": "draft-1",
  "reference_date": "2026-11-14",
  "seat_inventory": {
    "expected_count": null,
    "expected_count_source_id": null,
    "extracted_count": 0,
    "identity_verified_count": 0,
    "geometry_verified_count": 0
  },
  "facility_coverage": [
    {"type":"toilet","expected_count":null,"mapped_count":0,"verification":"unverified","missing_reason":"원본 미확보"},
    {"type":"air_conditioner","expected_count":null,"mapped_count":0,"verification":"unverified","missing_reason":"설비도 또는 현장 확인 필요"}
  ],
  "feature_coverage": [],
  "checks": [],
  "blockers": [],
  "next_actions": []
}
```

위 객체는 **빈 보고서 형식 예시**이며 실제 도서관의 관측값이 아니다. status는 discovery/partial/verified_scope 중 하나로 관리한다. 기관 전체가 검증됐다는 뜻이 아니라 보고서에 선언한 범위만 뜻한다.

- 화장실·에어컨·콘센트·조명·창문·출입구 등 각 유형의 기대 개수를 모르면 expected_count=null이다. 기대 개수가 없는데 전체coverage100%라고 하지 않는다.
- feature_coverage는 피처별 관측 좌석 수·검수 좌석 수·전체 대상 수·결측 이유를 기록한다. ‘좌표100%’와 ‘환경 특성100%’는 다르다.
- 파일 형식: schema1.1 통과, 개별 배열과 bundle 일치, CSV BOM·JSON 동일성.
- 관계: 전역 ID 중복 없음, room/seat/map/source/시설/그래프의 외래키 연결, 서로 다른 기관을 이름으로 병합하지 않음.
- 좌표: bbox 양수·범위 이내, 정규화 계산 일치, 좌석과 시설의 프레임 일치, 변환 기준점·스케일 확인.
- 그래프: 존재하는 노드끼리 연결, 벽 관통/단절 확인, 미터와 픽셀 혼합 금지, 층 연결 거리 근거 확인.
- 실제 내용: 번호 중복·누락·미등장 번호 전수 대조. 추정으로 빈 번호를 채우지 않음. 과거 정원과 다르면 원인·날짜 보고.
- 출처·현재성·검수: 근거가 없는 시설/특성은 운영용에서 제외. 모델 신뢰도와 사람 검수를 구분.
- 주변 도서관 추천: 유효한 기관 위경도 필요. 실내 도면만으로 기관 위치를 대체하지 않음.
- 실시간 현황·사용자 선택 로그는 이 정적 패키지에 만들지 않는다. 별도 백엔드 저장 계층의 업무다.

## 8. 다른 에이전트에게 그대로 보낼 지시문

> 저장소 루트 data/docs/DATASET_BUILD_TASKS.md, HANDOFF.md, target_schema.json을 읽고 data/catalog/library_build_tasks.csv에서 지정한 작업ID의 공공도서관 데이터셋을 구축하라. data/libraries/real/{library_id}/에 v1.1 JSON 번들과 개별 배열, CSV 사본, build_report.json을 저장하라. 좌석 번호·좌표·공식ID 대응, 지도 프레임, 화장실·출입구·설비·통로와 추천용 특성을 근거가 있는 범위에서 조사하라. 원본 이용조건·robots·수집 간격을 지키고, 접근 제한을 우회하거나 없는 좌석·시설·특성 값을 AI로 만들어 채우지 마라. 미확인은 null 또는 미구축으로 보고하고, 검수 전 후보와 운영 적격 데이터를 분리하라. 기관별로 실제 완료 범위·누락 원인·다음 작업을 보고하라. 다른 도서관이나 기존 산출물을 덮어쓰지 마라.
