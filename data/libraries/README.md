# libraries/ — 도서관 좌석 데이터셋 (실데이터 + 합성 데이터)

좌석 추천 모델 학습용 데이터 루트. 실데이터는 **좌석 좌표가 있는 11관**만 학습 대상(`real/`)이고, 좌석 좌표가 없는 16관은 `backup/`에 보관한다. 합성 데이터는 `real/`의 실제 좌석 배치 위에 환경(화장실·냉방기·콘센트·창문·책상·조명 밝기)과 사용자·선택 이력을 생성한 것이다.

## 디렉터리 구조

```text
libraries/
├── README.md                    # 이 문서
├── registry/
│   └── ID_REGISTRY.json         # 27관 {task_id, library_id, name} — 모든 ID의 기준
├── real/                        # 학습 대상 11관 (좌석 bbox 있음)
│   └── {library_id}/
│       ├── dataset.json         # v1.1 번들(정본). 아래 개별 파일은 사본
│       ├── {table}.json / {table}.csv   # 11개 배열 (CSV: UTF-8 BOM, 한글 헤더)
│       ├── build_report.json    # 구축 범위·누락·차단 사유
│       └── assets/
├── backup/                      # 좌석 좌표 없는 16관 (성남 12, 용인 중앙·구성, 해운대, 계양) — 구조는 real/과 동일
├── unified/                     # real 11관 통합 JSONL (library_id·norm_bbox 추가) + coverage.csv
├── synthetic/                   # 합성 데이터 (*.jsonl은 git 제외, 재생성 가능)
│   ├── manifest.json            # seed·파라미터·건수·가정
│   ├── synth_scenarios.jsonl    # 열람실 × 시나리오 환경 배치 (720)
│   ├── synth_seat_env.jsonl     # 시나리오별 좌석 특성 (63,330)
│   ├── synth_users.jsonl        # 카카오톡 분석 형식 사용자 프로필 (3,000)
│   ├── synth_sessions.jsonl     # 추천 노출·선택 세션 (163,306)
│   └── synth_events.jsonl       # M/B 이벤트 API 형식 이벤트 (454,103)
├── scripts/
│   ├── build_unified.py         # real/* → unified/*.jsonl, coverage.csv
│   ├── export_csv.py            # real/*/dataset.json → 한글 CSV 사본
│   ├── generate_synthetic.py    # real/* → synthetic/*
│   ├── load_mongo.py            # real+backup+synthetic → MongoDB `library` DB
│   └── load.py                  # 파이썬 로더 (load_table, load_df, load_synthetic)
└── docs/
    ├── AGENT_BRIEF.md           # 27관 구축 에이전트 공통 지시(이력)
    └── SYNTHETIC_DATA.md        # 합성 데이터 스키마·생성 규칙·학습 사용법
```

## 학습 대상 11관

| library_id | 좌석 | 비고 |
|---|---:|---|
| nl-digital | 353 | 평면도 기반, 실제 화장실·출입구 후보 사용 |
| yongin-dongbaek | 287 | |
| yongin-heungdeok | 236 | 겹치는 좌석 사각형 있음(미검수) |
| yongin-jukjeon | 234 | |
| yongin-sanghyeon | 210 | 겹침 90쌍, 실제 출입구 후보 사용 |
| seongnam-central | 169 | A구역만 |
| yongin-pogok | 159 | 공식 ID 159, 실제 출입구 후보 사용 |
| yongin-giheung | 148 | |
| yongin-bora | 134 | |
| yongin-seonong | 112 | 공식 ID 112 |
| yongin-suji | 69 | 공식 ID 69 |

모든 실좌석은 `verification=model_candidate`(사람 검수 0건)다.

## 재생성 순서 (저장소 루트에서, uv 환경)

```bash
uv sync                                                         # 최초 1회: 루트 .venv 생성(pyproject.toml)
uv run data/libraries/scripts/export_csv.py          # dataset.json 수정 후 CSV 사본 갱신
uv run data/libraries/scripts/build_unified.py       # unified/ 갱신
uv run data/libraries/scripts/generate_synthetic.py --users 3000 --sessions 165000   # synthetic/ 생성
uv run data/libraries/scripts/load_mongo.py          # MongoDB 적재 (컬렉션 drop 후 재적재, mongoimport·mongosh 필요)
```

`generate_synthetic.py` 옵션: `--scenarios 30 --users 3000 --sessions 60000 --seed 20261114 --workers <CPU 수>`. 열람실·세션 묶음(1,000건)마다 따로 시드를 쓰므로 `--workers`가 달라도 결과가 같다. 진행률은 tqdm으로 표시한다(uv 환경에 포함, 다른 환경에서 없으면 표시 없이 실행). 예: 3000명 × 165,000세션은 32코어에서 약 10초 걸린다.

## MongoDB `library` DB

접속 정보는 저장소 루트 `.env`(MONGO_HOST/PORT/USER/PASSWORD/AUTH_DB)를 읽는다.

| 컬렉션 | 건수 | 내용 |
|---|---:|---|
| libraries · rooms · maps · seats · facilities · room_features · sources · walkway_nodes | 27 · 94 · 34 · 2,111 · 73 · 29 · 109 · 6 | 27관 실데이터. `dataset_status`=`active`(real) / `backup`, `is_synthetic=false`. 빈 배열 테이블(seat_features, walkway_edges, map_transforms)은 컬렉션이 생기지 않음 |
| build_reports | 27 | 도서관별 build_report |
| meta | 2 | `registry`, `synthetic_manifest` |
| synth_scenarios · synth_seat_env · synth_users · synth_sessions · synth_events | 720 · 63,330 · 3,000 · 163,306 · 444,297 | 합성 데이터. `is_synthetic=true`, `verification=synthetic` |

```python
import sys

sys.path.insert(0, "libraries/scripts")
from load import load_table, load_df, load_synthetic

seats = load_df("seats", "yongin-suji")
env = load_synthetic("synth_seat_env")
```

## 원칙

- 실데이터와 합성 데이터는 파일·컬렉션 단위로 분리하고 합성 레코드에는 항상 `is_synthetic=true`를 붙인다. 합성 값을 `real/`에 쓰지 않는다.
- 실데이터에 없는 값(위경도, 냉방·콘센트·조도 실측)은 `null`이며 합성 값으로 대체하지 않는다.
