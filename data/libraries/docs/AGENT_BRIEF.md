# 도서관 데이터셋 구축 에이전트 공통 지시 (27관)

너는 도서관 1곳의 정적 좌석·시설 데이터셋을 구축하는 에이전트다. 저장소 루트: /home/stadium/2026-IST-TECH-SeatCom-Model

## 먼저 읽을 것 (순서대로)
1. data/docs/DATASET_BUILD_TASKS.md  — 납품 파일, 필드, 형식, build_report 기준 (최우선 규칙)
2. data/catalog/library_build_tasks.csv — 네 작업ID 행 (원본 URL, 첫 작업)
3. data/docs/HANDOFF.md — 해당 도서관 절(§4~§7)과 §8 파이프라인
4. data/docs/target_schema.json — v1.1 스키마
5. data/libraries/registry/ID_REGISTRY.json — 네 library_id (이 ID를 모든 파일에서 그대로 사용)
6. 필요 시 data/docs/BACKEND_MODEL_API_SPEC.md (피처 레지스트리), data/source_data/extracted/, data/scripts/structure_seat_screenshot.py

## 산출물
- data/libraries/real/{library_id}/ 아래에만 쓴다. 다른 도서관·기존 파일(seats/, candidates/, catalog/, docs/)은 절대 수정하지 않는다. 필요한 기존 데이터는 읽어서 가져온다.
- 파일: dataset.json, libraries.json, rooms.json, maps.json, seats.json, facilities.json, room_features.json, seat_features.json, walkway_nodes.json, walkway_edges.json, map_transforms.json, sources.json, build_report.json, assets/ (+ 표 형태 파일의 CSV 사본: UTF-8 BOM, 한글 컬럼, 마지막 컬럼 데이터기준일자).
- 기존 확보 데이터(성남중앙 A구역 169석, 용인 후보 사각형)가 있으면 변환해서 넣되 verification은 model_candidate로 둔다. 공식 ID·표시번호는 확인된 것만, 아니면 null.
- 자료가 없으면 빈 배열 + build_report에 사유. 가짜 좌표·추측 시설·추측 특성 값 금지. 미확인은 null.
- 학습용 로딩을 위해 모든 레코드에 library_id를 넣고(스키마에 없으면 추가 필드로 허용), 외래키 ID는 `{library_id}:` 접두사로 전역 유일하게 만든다 (예: yongin-dongbaek:seat:0001).

## 반드시 지킬 규칙
- 대학도서관 제외. 실시간 현황·색·성별·이용시각 저장 금지.
- robots.txt를 먼저 확인. 금지 경로(특히 snlib.go.kr 전체)는 수집하지 말 것. 로그인/접근제한/타임아웃이면 숨은 경로 추측 없이 멈추고 build_report.blockers에 기록.
- 요청 간격 1초 이상, 재시도 2회 이하, User-Agent에 목적 명시(예: SeatComMetadata/1.0 hackathon static layout research).
- 공식 내비게이션에 실제 링크된 공개 경로만 사용. 이용조건 불명확한 이미지는 assets에 넣지 말고 sources에 license_status 기록.
- 성남 12관(snlib)은 직접 수집 금지: 로컬에 사용자 제공 파일이 있는 경우(성남중앙 캡처 /home/stadium/.univdash/uploads/ 아래)만 처리. 열람실 정보는 data.go.kr 공개 CSV(data/source_data/public_datasets/ 아래)에서 만들 수 있다. 좌석·시설은 원본 없으면 비워 두고 blockers에 기록.
- 열람실 마스터(rooms), 이용규정, room_features는 data/source_data/public_datasets/의 공개 CSV와 명칭에 직접 쓰인 특성만으로 만든다. 일반열람실이라는 이름만으로 노트북 불가 등 추정 금지.
- 도서관 위경도: 근거가 있는 공식 자료에서만. 없으면 null (거리 추천 비활성). 지오코딩 API로 추정해 넣지 말 것.
- 파이썬 실행은 `python3 -I`로 다운로드 파일을 읽을 때 주의, 다운로드물은 새 디렉터리(/tmp/claude-... 스크래치 또는 data/libraries/{id}/assets 밖)에 두기. 개인 정보·키 노출 금지.

## 검증 (끝내기 전에 실제로 실행)
- 개별 배열과 dataset.json 일치, 외래키(room/map/seat/source/facility/node) 무결성, ID 중복 없음, bbox 범위, CSV BOM·JSON 동일성, target_schema.json 통과(가능하면 jsonschema).
- build_report.json은 DATASET_BUILD_TASKS.md §7 형식. status는 discovery/partial/verified_scope. 수치(expected/extracted/verified)는 실제 계산값.

## 최종 보고 (짧게, 10줄 이내)
library_id, 구축한 파일/행 수(좌석·시설·열람실), 좌표 확보 여부, 막힌 점, 다음 작업. 거짓 완료 주장 금지.
