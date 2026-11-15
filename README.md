# SeatCom 백엔드 · 좌석 추천 모델

도서관 열람실에서 **나에게 맞는 빈자리**를 추천하는 서비스의 백엔드입니다.

회원가입(이메일 인증) → 4문항 취향 설문(선택: 카카오톡 대화 분석) → 학습한 모델이 지금 빈자리 중 추천 → 고른 좌석이 바로 개인 취향에 반영됩니다.

- 백엔드(이 저장소): <https://github.com/yojun313/2026-IST-TECH-SeatCom-Model>
- 프론트엔드: <https://github.com/donghee13/2026-IST-TECH-SeatCom-FE>

## 구성

```
브라우저 ──▶ seatcom-frontend (:8000)  ──/api──▶ seatcom-backend (:8001)
            프론트 dist + API 프록시                FastAPI + 추천 모델 + SQLite 하나
브라우저 ──────────────────────────────────────▶ seatcom-backend (:8001/)  데이터셋 뷰어
```

| 이름 | 주소 | 내용 |
|---|---|---|
| `seatcom-frontend` | `0.0.0.0:8000` | 프론트 빌드 파일 제공, `/api`·`/health`를 백엔드로 전달 (프론트 저장소 `server.mjs`) |
| `seatcom-backend` | `0.0.0.0:8001` | 회원·설문·카카오톡 실험실·좌석 추천 API(`/api/v1/...`, `backend/`) + **데이터셋 뷰어(`/`, `seatviz/`)** |

포트는 기본값이며 서버마다 `deploy/.env`의 `SEATCOM_FRONTEND_PORT`·`SEATCOM_BACKEND_PORT`로 바꿀 수 있습니다(`deploy/.env.example` 참고).

- **DB는 SQLite 파일 하나**입니다. MongoDB는 필요 없습니다.
- 도서관·좌석 데이터는 [GitHub Release](https://github.com/yojun313/2026-IST-TECH-SeatCom-Model/releases/tag/sqlite-data-20261115-v1)에 있고, **첫 실행 때 자동으로 내려받아** 체크섬을 확인한 뒤 DB에 넣습니다(약 97MB → 1.4GB).

## 빠른 배포 (처음 한 번)

**준비물:** Linux 서버, Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+, pm2(`npm i -g pm2`), 디스크 여유 6GB 이상

**1. 두 저장소를 같은 폴더에 나란히 클론합니다.** 폴더 이름도 그대로 둡니다.

```bash
git clone https://github.com/yojun313/2026-IST-TECH-SeatCom-Model.git
git clone https://github.com/donghee13/2026-IST-TECH-SeatCom-FE.git
cd 2026-IST-TECH-SeatCom-Model
```

**2. 설정 파일을 만듭니다.**

```bash
cp backend/.env.example backend/.env
```

`backend/.env`에서 아래 값만 채우면 됩니다(자세한 건 [설정](#설정)).

| 값 | 넣을 것 |
|---|---|
| `AUTH_JWT_SECRET` | 로그인 토큰 서명 키. `openssl rand -hex 32` 결과 |
| `MAIL_SENDER`, `MAIL_PASSWORD` | 인증 메일 보낼 Gmail 주소와 앱 비밀번호 |
| `OSS_API_KEY` | 카카오톡 분석 LLM 키(없으면 실험실 분석만 실패) |

**3. 배포 스크립트를 실행합니다.**

```bash
bash deploy/deploy.sh
```

파이썬 환경 설치 → 프론트 빌드 → pm2 시작 → 상태 확인까지 한 번에 합니다. 첫 실행은 도서관 데이터를 받느라 1~2분 더 걸립니다.

**4. 확인합니다.**

```bash
curl http://127.0.0.1:8000/health                 # {"status":"ok",...}
curl http://127.0.0.1:8000/api/v1/system/model    # "loaded": true
```

브라우저로 `http://서버주소:8000`에 접속합니다. 데이터셋 뷰어는 `http://서버주소:8001/`입니다.

> 방화벽·공유기에서 8000(서비스)과 8001(뷰어) 포트를 열어야 외부에서 접속됩니다.

**5. 서버 재부팅 후에도 자동으로 켜지게 합니다(한 번만).**

```bash
pm2 startup     # 출력되는 sudo 명령을 그대로 복사해 실행
pm2 save
```

> **HTTPS는 꼭 앞단에 두세요.** 8000·8001은 평문 HTTP입니다. 로그인 정보와 카카오톡 파일이 오가므로, 공개 서비스는 nginx·Caddy 등으로 HTTPS를 붙이고 8000으로 전달하세요.

## 업데이트 배포

```bash
cd 2026-IST-TECH-SeatCom-Model && git pull
(cd ../2026-IST-TECH-SeatCom-FE && git pull)
bash deploy/deploy.sh               # 프론트까지 다시 빌드
bash deploy/deploy.sh --no-build    # 백엔드만 바뀌었을 때(재시작만)
```

회원·설문·추천 기록은 DB에 그대로 남습니다. 도서관 데이터 버전이 바뀌었으면 시작할 때 그 부분만 새로 넣습니다.

**자주 쓰는 명령**

```bash
pm2 ls                              # 상태
pm2 logs seatcom-backend            # 로그 (파일: deploy/logs/)
pm2 restart seatcom-backend         # 백엔드만 재시작
```

## 로컬 개발

```bash
uv sync --frozen                                         # .venv 생성
uv run --frozen python -m backend                        # http://127.0.0.1:8001 (뷰어 /, API /api/v1)
uv run --frozen python -m pytest backend/tests -q        # 테스트
```

프론트까지 같이 띄우려면 프론트 저장소 README의 [로컬 개발](https://github.com/donghee13/2026-IST-TECH-SeatCom-FE#로컬-개발)을 보세요. API 문서는 실행 후 <http://127.0.0.1:8001/docs>에 있습니다.

## 설정

모두 `backend/.env`에 씁니다. 예시와 설명은 [`backend/.env.example`](backend/.env.example)에 있습니다.

| 값 | 기본 | 설명 |
|---|---|---|
| `DATABASE_PATH` | `backend/runtime/seatcom.sqlite3` | 도서관 데이터와 회원·기록을 함께 담는 SQLite |
| `AUTH_JWT_SECRET` | (필수) | 바꾸면 기존 로그인이 모두 풀립니다 |
| `MAIL_SENDER`, `MAIL_PASSWORD` | (필수) | 회원가입 인증 메일 |
| `OSS_API_KEY` | (선택) | 카카오톡 분석 LLM |
| `CORS_ORIGINS` | 없음 | 허용할 프론트 주소(쉼표로 여러 개). 프론트 서버(8000) 프록시로만 접속하면 비워도 됩니다 |
| `LAB_MAX_CONCURRENT` · `LAB_MAX_PENDING` · `LAB_UPLOADS_PER_HOUR` | 2 · 6 · 5 | 실험실 동시 분석 수, 대기 상한, 사용자당 시간당 업로드 |
| `SEATCOM_SEED_PATH` | (선택) | 인터넷 없는 서버: 직접 받은 `.sqlite3.gz` 경로 |
| `SEATCOM_DATA_CACHE` | `~/.cache/seatcom` | 내려받은 데이터 캐시 위치 |

## 문제 해결

| 증상 | 해결 |
|---|---|
| 시작 때 다운로드 실패 | 서버에서 github.com 접속을 확인하세요. 안 되면 [Release 파일](https://github.com/yojun313/2026-IST-TECH-SeatCom-Model/releases/download/sqlite-data-20261115-v1/seatcom-library-sqlite-data-20261115-v1.sqlite3.gz)을 직접 옮기고 `SEATCOM_SEED_PATH`에 경로를 넣은 뒤 재시작 |
| `checksum mismatch` | 받다가 끊긴 파일입니다. `~/.cache/seatcom/`을 지우고 재시작 |
| 화면은 뜨는데 API가 502 | 백엔드가 꺼져 있습니다. `pm2 logs seatcom-backend`로 원인 확인 |
| 인증 메일이 안 옴 | `MAIL_SENDER`·`MAIL_PASSWORD`(Gmail 앱 비밀번호) 확인 |
| 카카오톡 분석이 실패 | `OSS_API_KEY` 확인. 로그에는 오류 종류만 남고 대화 내용은 남지 않습니다 |
| 화면이 예전 그대로 | 브라우저 강력 새로고침(Ctrl+Shift+R) |

**서버 옮기기:** 회원 데이터는 Git에 없습니다. 실행 중인 DB를 그냥 복사하면 기록이 빠질 수 있으니 SQLite backup으로 떠서 옮기고, `backend/.env`도 같이 옮기세요(`AUTH_JWT_SECRET`이 같아야 기존 로그인이 유지됩니다).

```bash
.venv/bin/python -c "import sqlite3; sqlite3.connect('deploy/runtime/seatcom.sqlite3').backup(sqlite3.connect('backup.sqlite3'))"
```

## 폴더 구조

| 폴더 | 역할 |
|---|---|
| `backend/` | FastAPI 서버: 회원가입·로그인, 페르소나 설문, 카카오톡 실험실, 좌석 추천 API |
| `seat_model/` | 좌석 추천 모델: 학습(`train/`), 서비스 모델(`models/v1-20261115/`), 리포트 |
| `kakao_analysis/` | 카카오톡 대화에서 좌석 취향 추출(LLM), 출력 스키마 |
| `recommender/` | 취향 변환기(`preference_adapter.py`), 기준 모델 `seatrec` |
| `data/` | 도서관·좌석 데이터 구축, 합성 데이터, SQLite 데이터 Release 정보(`sqlite_seed/`) |
| `seatviz/` | 데이터셋 뷰어. 백엔드에 통합되어 백엔드 주소 `/`에서 제공 |
| `deploy/` | 배포 스크립트·pm2 설정 |
| `hackathon/` | 2026 IT Arena 해커톤 발표 자료(`seatcom.pptx`)·상장(PDF) |

**카카오톡 실험실:** 사용자가 웹에서 올린 TXT는 서버 메모리에서만 처리하고 디스크·DB·로그에 저장하지 않습니다. 분석 결과(취향)만 저장합니다.

참고: [deploy/README.md](deploy/README.md) · [data/sqlite_seed/README.md](data/sqlite_seed/README.md)(데이터 Release 만들기) · [backend/docs/API_SPEC_v2.1.md](backend/docs/API_SPEC_v2.1.md) · [seat_model/README.md](seat_model/README.md) · [data/libraries/README.md](data/libraries/README.md)
