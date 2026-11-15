# 공공도서관 좌석·시설 데이터 구축 인계

**다른 에이전트에게 데이터 구축을 맡길 때:** [데이터셋 구축 작업 지시서](docs/DATASET_BUILD_TASKS.md)와 [도서관별 작업표](catalog/library_build_tasks.csv)를 전달한다. 지시서에는 도서관별 필수 파일·필드·형식·좌표/단위·시설/특성 조사·검수 및 납품 기준, 그대로 복사할 작업 지시문이 있다. 기계 검증용 형식은 [target_schema.json v1.1](docs/target_schema.json)이다.

학교도서관을 제외한 **공공도서관** 데이터다. 조사일은 **2026-11-14**이며, 현재 자료는 실제 서비스용 전수 검수를 마친 완성본이 아니다.

**27개관 데이터 구축 결과와 도서관별 성공/미구축 항목:** [BUILD_STATUS.md](BUILD_STATUS.md). 각 표 셀에서 해당 도서관의 JSON·CSV 파일로 이동할 수 있다.

다음 에이전트는 먼저 **[구축 인계서](docs/HANDOFF.md)**를 읽고 [도서관별 타당성 표](catalog/library_feasibility.csv)를 확인한다. 사용자가 지정한 참고 저장소는 **[opendata_plr 검토서](docs/OPENDATA_PLR.md)**에 별도로 정리했다. 제공받은 성남중앙 A구역 캡처는 실제로 OCR·좌표화를 실행했다. 용인 데이터는 공식 웹의 정적 사각형 좌표 후보이며 공식 좌석 ID 연결과 배경 정합 검증이 남았다.

| 산출물 | 현재 확보 | 판정 |
|---|---:|---|
| 도서관·열람실 마스터 | 27개관·94개실 | 공개 근거가 있는 명칭·정원·일부 이용 규정 구조화 |
| 좌석 위치 | 좌석 도형 후보 2,111개 | 11개관에서 확보. 전부 사람 검수 전 후보이며 표시번호 919개, 공식 ID 연결 340개 |
| 시설 위치 | 후보 73건 | 화장실30·승강기17·계단14·출입구12. 에어컨·조명·콘센트 데이터 없음 |
| 추천 특성 | 열람실 특성 29건 | 노트북 가능21·칸막이7·키보드 가능1; 좌석 특성은 없음 |
| 이미지·지도 | 지도 프레임34개, 로컬 이미지 연결2개 | 도면 재사용조건이 불명확한 원본은 복제하지 않음 |
| 도서관 위치 | 공식 위경도 0/27개관 | 현재 거리 기반 도서관 추천은 비활성 |
| 검증 | 스키마·JSON/CSV 일관성 검사 통과 | 좌석·시설 모두 운영 검수 완료 0건 |

## 디렉터리

```text
data/
├── README.md
├── BUILD_STATUS.md                  # 27관별 통계·성공/미구축 항목 및 파일 링크
├── requirements.txt
├── docs/
│   ├── DATASET_BUILD_TASKS.md        # 실제 구축할 데이터·형식·납품 기준
│   ├── HANDOFF.md                    # 도서관별 출처·구축 절차·남은 검증
│   ├── OPENDATA_PLR.md              # 사용자 지정 GitHub 저장소 정적 검토
│   └── target_schema.json            # 최종 데이터의 구조 명세
├── catalog/
│   ├── library_build_tasks.csv/json  # 27관별 구축 작업표
│   ├── library_feasibility.csv/json  # 27관별 판정
│   ├── reference_library_candidates.csv/json # 기관 91개 후보
│   ├── reference_room_candidates.csv/json    # 열람실 222개 링크 후보
│   ├── source_manifest.json          # 원본 출처·해시·접근정책·조사 범위
│   └── yongin_layout_sources.json    # 용인11관 공식 안내도 URL
├── source_data/
│   ├── public_datasets/              # 보존한 공공 CSV 원본·UTF-8 정리본
│   ├── extracted/                    # OCR·좌석 사각형 추출 중간물
│   └── README.md                     # 보관 기준 및 제외 자료
├── validation/
│   ├── seongnam_central_A.json       # OCR·좌표·개수 검증
│   └── integrity.json                # 파일 간 무결성 점검 결과
└── scripts/
    ├── structure_seat_screenshot.py  # 해당 유형 캡처의 좌표+OCR+SVG 생성
    └── validate_artifacts.py         # CSV/JSON·범위·ID·상태필드 제외 점검
├── libraries/                        # 자세한 구조·재생성 방법: libraries/README.md
│   ├── real/{library_id}/            # 좌석 좌표가 있는 11관 정적 번들(JSON/CSV/assets)
│   ├── backup/{library_id}/          # 좌석 좌표가 없는 16관(보관, 학습 대상 제외)
│   ├── unified/                      # real 11관 통합 JSONL + coverage.csv
│   ├── synthetic/                    # 합성 환경·사용자·선택 이력 JSONL (manifest.json)
│   ├── registry/ID_REGISTRY.json     # 27개 library_id
│   ├── scripts/                      # build_unified·export_csv·load·generate_synthetic·load_mongo
│   └── docs/                         # AGENT_BRIEF.md, SYNTHETIC_DATA.md
```

CSV는 UTF-8 BOM, 한글 컬럼, 마지막 컬럼은 데이터기준일자다. JSON은 UTF-8이며 공란은 null이다. 좌표 후보를 검증된 좌석처럼 사용하지 않는다. 이용 상태·이용 시각·성별·색상은 좌석 데이터에 저장하지 않았다.

## 실행

저장소 루트의 uv 환경(`pyproject.toml`, Python 3.11)을 쓴다. OCR 의존성은 `ocr` 그룹에 있다.

```bash
uv sync --group ocr                       # OCR 재현이 필요할 때만 --group ocr
uv run data/scripts/validate_artifacts.py
uv run data/scripts/validate_api_spec.py  # API 명세(OpenAPI·예시) 검증

# 이 이미지의 프로필에 한정된 재현 명령. 다른 캡처는 frame·검출 크기를 조정해야 한다.
uv run data/scripts/structure_seat_screenshot.py \
  /home/stadium/.univdash/uploads/76e3778271ac-pasted-20261114075747.png \
  --frame 48 582 3772 1786 --expected 169 --date 2026-11-14
uv run data/scripts/validate_artifacts.py
```

원본 캡처는 사용자 제공 업로드 경로에 있다. 이용 현황과 브라우저 개인 영역을 복제하지 않도록 저장소에 원본 이미지를 복사하지 않았다. 다른 실행환경에서는 동일 원본을 별도로 확보해야 OCR를 재현할 수 있다. SVG는 JSON만으로 웹에 표시할 수 있다.

공개 원본과 필요한 UTF-8 정리본은 `source_data/public_datasets/`에 보관한다. 예약 이력, 이번 27개관 구축에서 쓰지 않는 별도 CSV, 중복 확인 결과는 제외했다. 좌석 추출 중간물은 `source_data/extracted/`에 있고, 화면 미리보기 SVG는 `libraries/real/seongnam-central/assets/seat_layout_A.svg`에 있다.

## 백엔드·모델 API 설계

[실시간 개인별 추천 API 명세서](docs/BACKEND_MODEL_API_SPEC.md)에 API별 기능·호출 시점·입출력·오류·데이터 준비 조건을 정리했다. 현재 위치와 거리 기반 도서관 추천, 열람실·좌석 추천, 좌석 좌표 목록 조회, 선택·명시적 취향 피드백이 포함된다. 추천시스템과 서버는 아직 구현되지 않았다.

- [모델 내부 OpenAPI](docs/MODEL_API.openapi.json)
- [백엔드 좌석 좌표 OpenAPI](docs/BACKEND_SEATS_API.openapi.json)
