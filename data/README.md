# 공공도서관 좌석·시설 데이터 구축 인계

학교도서관을 제외한 **공공도서관** 데이터다. 조사일은 **2026-11-14**이며, 현재 자료는 실제 서비스용 전수 검수를 마친 완성본이 아니다.

다음 에이전트는 먼저 **[구축 인계서](docs/HANDOFF.md)**를 읽고 [도서관별 타당성 표](catalog/library_feasibility.csv)를 확인한다. 사용자가 지정한 참고 저장소는 **[opendata_plr 검토서](docs/OPENDATA_PLR.md)**에 별도로 정리했다. 제공받은 성남중앙 A구역 캡처는 실제로 OCR·좌표화를 실행했다. 용인 데이터는 공식 웹의 정적 사각형 좌표 후보이며 공식 좌석 ID 연결과 배경 정합 검증이 남았다.

| 산출물 | 현재 확보 | 판정 |
|---|---:|---|
| 성남중앙 A구역 | 좌석 사각형·OCR 번호 169개 | 캡처 표기169개와 일치, 고유번호169개, 우선검수8개, 전수검수 전 |
| 용인 7관 | 사각형 좌표 후보1,318개 | 6관1,170개는 과거 공개 좌석목록의 행 수와 일치. 기흥은148/164개 |
| 참고 저장소 링크 후보 | 기관 91개·열람실 222개 | 링크 원본 미접속. 직접 검토 27관과 구분 |
| 도서관별 조사 | 기존24관+추가도면3관=27관 | 전좌석·전시설 완성으로 확인된 곳0관 |
| 화장실·에어컨·콘센트 위치 | 일부 도면 표시 여부만 확인 | 시설 좌표 레코드는 아직 생성하지 않음 |

## 디렉터리

```text
data/
├── README.md
├── requirements.txt
├── docs/
│   ├── HANDOFF.md                    # 도서관별 출처·구축 절차·남은 검증
│   ├── OPENDATA_PLR.md              # 사용자 지정 GitHub 저장소 정적 검토
│   └── target_schema.json            # 최종 데이터의 구조 명세
├── catalog/
│   ├── library_feasibility.csv/json  # 27관별 판정
│   ├── reference_library_candidates.csv/json # 기관 91개 후보
│   ├── reference_room_candidates.csv/json    # 열람실 222개 링크 후보
│   ├── source_manifest.json          # 원본 출처·해시·접근정책·조사 범위
│   └── yongin_layout_sources.json    # 용인11관 공식 안내도 URL
├── seats/
│   └── seongnam_central_A.csv/json   # 사용자 캡처에서 추출한169개 좌석
├── candidates/
│   └── yongin_geometry.csv/json      # 공식좌석ID 미연결 좌표 후보1,318개
├── maps/
│   └── seongnam_central_A.svg        # 상태를 제거한 좌석 버튼 배치 재구성
├── validation/
│   ├── seongnam_central_A.json       # OCR·좌표·개수 검증
│   └── integrity.json                # 파일 간 무결성 점검 결과
└── scripts/
    ├── structure_seat_screenshot.py  # 해당 유형 캡처의 좌표+OCR+SVG 생성
    └── validate_artifacts.py         # CSV/JSON·범위·ID·상태필드 제외 점검
```

CSV는 UTF-8 BOM, 한글 컬럼, 마지막 컬럼은 데이터기준일자다. JSON은 UTF-8이며 공란은 null이다. 좌표 후보를 검증된 좌석처럼 사용하지 않는다. 이용 상태·이용 시각·성별·색상은 좌석 데이터에 저장하지 않았다.

## 실행

저장소 루트에서 Python3.10 이상을 사용한다. 이번 실행은3.12였다.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r data/requirements.txt
.venv/bin/python data/scripts/validate_artifacts.py

# 이 이미지의 프로필에 한정된 재현 명령. 다른 캡처는 frame·검출 크기를 조정해야 한다.
.venv/bin/python data/scripts/structure_seat_screenshot.py \
  /home/stadium/.univdash/uploads/76e3778271ac-pasted-20261114075747.png \
  --frame 48 582 3772 1786 --expected 169 --date 2026-11-14
.venv/bin/python data/scripts/validate_artifacts.py
```

원본 캡처는 사용자 제공 업로드 경로에 있다. 이용 현황과 브라우저 개인 영역을 복제하지 않도록 저장소에 원본 이미지를 복사하지 않았다. 다른 실행환경에서는 동일 원본을 별도로 확보해야 OCR를 재현할 수 있다. SVG는 JSON만으로 웹에 표시할 수 있다.

기존 공개 CSV 원본은 저장소의 대문자 `Data/`에 있다. 이 소문자 `data/`에는 원본을 중복 복사하지 않았다. 실제 파일명이 한글 자모 분해형일 수 있으므로 인계서의 Python 검색 방법을 사용한다.
