# SeatCom: 도서관 열람실 개인 맞춤 좌석 추천

회원가입(이메일 인증) → 4문항 취향 설문(선택: 카카오톡 대화 분석) → 학습한 모델로 빈자리 추천 → 고른 좌석이 바로 개인 모델에 반영된다.

## 다른 서버에서 실행 (MongoDB 불필요)

Python 3.11 이상과 uv를 준비하고 저장소를 클론한 뒤 실행한다.

```bash
uv sync --frozen
uv run --frozen python -m backend --host 0.0.0.0 --port 8028
```

첫 실행 시 `data/sqlite_seed/release.json`에 고정된 GitHub Release의 압축 SQLite
(약 97MB)를 자동 다운로드하고 SHA-256을 검증한 뒤
`backend/runtime/seatcom.sqlite3`에 자동 적재한다. 원본 MongoDB `library`의
15개 컬렉션 전체(학습용 합성 사용자·세션·이벤트 포함)가 들어 있으며,
도서관·좌석·환경 데이터와 새 회원·설문·추천·이용 기록이 **하나의 SQLite**를 쓴다.
원본 MongoDB의 내부 `_id`만 제외하며 도서관·좌석·사용자 등의 업무 ID는 보존한다.
초기 DB는 약 1.4GB이며 첫 적재·갱신용 임시 공간까지 캐시와 작업 공간을 포함해 최소 6GB의 여유 공간을 권장한다.
초기 적재는 시간이 걸릴 수 있고, 이후 실행에서는 버전을 확인해 건너뛴다.
운영 중에는 SQLite가 `-wal`, `-shm` 보조 파일을 만들 수 있다.

`GET /health`와 `GET /api/v1/system/model`로 실행을 확인한다.
서버·추천 모델은 비밀값 없이 시작할 수 있지만, 회원가입·로그인·메일 인증과
외부 AI 분석을 쓰려면 `backend/.env.example`을 `backend/.env`로 복사하고
`AUTH_JWT_SECRET`(서버별 충분히 긴 무작위 값), `MAIL_SENDER`, `MAIL_PASSWORD`,
`OSS_API_KEY`를 설정한다. 예시의 `AUTH_JWT_ALGORITHM=HS256`, issuer/audience도 유지한다.
기존 서버는 기존 JWT 설정을 유지해야 이미 발급한 토큰을 계속 쓸 수 있다.

`DATABASE_PATH`로 DB 위치를 변경할 수 있다. 기존 DB를 지정하면 **사용자 테이블은
보존하고 catalog 테이블만 추가/갱신**한다. 기존 운영 계정과 개인 분석 결과는
Git에 포함하지 않는다. 이전하려면 SQLite backup API로 백업한 운영 DB와 기존
서버 설정을 별도로 옮긴다. 실행 중인 DB 본체만 복사하면 WAL의 기록이 빠질 수 있다.
초기 스냅샷은 기본 `~/.cache/seatcom/`에 캐시한다(`SEATCOM_DATA_CACHE`로 변경).
실행 중 조회·저장은 `DATABASE_PATH` 하나만 사용하며 MongoDB에 연결하지 않는다.
Release 파일을 직접 다운로드했다면 아래처럼 오프라인 실행도 가능하다.

```bash
SEATCOM_SEED_PATH=/path/to/seatcom-library-sqlite-data-20261115-v1.sqlite3.gz \
  uv run --frozen python -m backend
```

[SQLite 데이터 Release](https://github.com/yojun313/2026-IST-TECH-SeatCom-Model/releases/tag/sqlite-data-20261115-v1)
· [직접 다운로드](https://github.com/yojun313/2026-IST-TECH-SeatCom-Model/releases/download/sqlite-data-20261115-v1/seatcom-library-sqlite-data-20261115-v1.sqlite3.gz)

다운로드 실패·체크섬 불일치 때는 불완전한 데이터로 시작하지 않고 오류를 반환한다.
이미 같은 버전이 DB에 적재됐다면 네트워크나 캐시 없이 재시작할 수 있다.

데이터 내용·체크섬·재추출: [data/sqlite_seed/README.md](data/sqlite_seed/README.md)

## 구조

| 폴더 | 역할 |
|---|---|
| `backend/` | FastAPI 서버: 회원가입·로그인(이메일 인증), 페르소나 설문, 카카오톡 실험실(웹 TXT 업로드, 원문은 메모리에서만 처리·저장 안 함), **좌석 추천 API**. 설정은 `backend/.env` |
| `seat_model/` | 좌석 추천 모델: 학습 파이프라인(`train/`), 서비스 모델(`models/v1-20261115/`), 리포트·문서 |
| `kakao_analysis/` | 카카오톡 대화에서 좌석 취향 추출(LLM), 출력 스키마(`schema/`). `get_gmail_text_api/`·`pipeline.py`는 예전 Gmail 수집용 단독 도구로 백엔드는 쓰지 않는다 |
| `recommender/` | 취향 변환기(`preference_adapter.py`: 카카오톡 분석 → 모델 취향 프로필), 기준 모델 `seatrec` |
| `data/` | 도서관·열람실·좌석 데이터 구축, 합성 데이터와 SQLite 초기 스냅샷 |
| `seatviz/` | 데이터 시각화 사이트(pm2 `seatviz`, 8026) |
| `deploy/` | 배포: pm2 설정, 프론트엔드 서버, 배포 스크립트 |
| `docs/` | API 명세 초안, 조사 자료(`research/`) |

프론트엔드는 별도 저장소 `../2026-IST-TECH-SeatCom-FE`.

카카오톡 실험실은 대상자 발언이 있는 최근 최대 18구간을 분석한다. 기본 구간
크기는 직렬화 기준 24,000자이며 앞뒤 대화를 겹쳐 맥락을 유지한다. 기본 3개씩
병렬 분석한 뒤, 6구간씩 근거를 병합하고 최종 판단한다(최대 22회 모델 호출).
120개 메시지 제한은 없으며 긴 말풍선은 여러 조각으로 보존한다. 같은 원문 ID는
중복 증거로 합산하지 않고 직접성·반복·최근성·반대 근거를 함께 비교한다.
약 1분은 목표 시간이고 전체 작업의 강제 종료 기한은 없다. 지연되어도 계속
분석하며, 개별 HTTP 요청의 기존 네트워크 타임아웃(기본 180초)·재시도 설정은 유지한다.
`analysis_scope`에 실제 분석 메시지 수·구간 수·호출 수·생략 여부를,
`collected_evidence`에 구간별로 모은 중복 제거 근거를 제공한다.

## 배포 (pm2)

```bash
bash deploy/deploy.sh      # 파이썬 환경 → 프론트 빌드 → pm2 시작/재시작 → 상태 확인
```

| pm2 | 주소 | 내용 |
|---|---|---|
| `seatcom-frontend` | `0.0.0.0:8027` | 프론트 정적 파일 + `/api` → 백엔드 프록시 |
| `seatcom-backend` | `127.0.0.1:8028` | `backend.main:app` + 좌석 추천 모델 |

자세한 내용: [deploy/README.md](deploy/README.md)

## 개발

```bash
uv sync --frozen                                             # 백엔드·데이터·모델 공통 환경(.venv)
uv run data/libraries/scripts/generate_synthetic.py --users 3000 --sessions 165000
uv run python -m seat_model.train.data                       # SQLite 학습 데이터 읽기
uv run python -m pytest backend/tests -q                    # 백엔드 테스트
uv run python -m pytest seat_model/tests -q                 # 모델 테스트(일부는 학습 exports 필요)
```

참고: [seat_model/README.md](seat_model/README.md) · [seat_model/reports/training_report.md](seat_model/reports/training_report.md) · [data/libraries/README.md](data/libraries/README.md) · [recommender/TRAINING.md](recommender/TRAINING.md) · [seatviz/README.md](seatviz/README.md)
