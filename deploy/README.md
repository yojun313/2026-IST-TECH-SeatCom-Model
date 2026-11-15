# SeatCom 배포 (pm2)

백엔드는 MongoDB 없이 단일 SQLite로 동작한다. 최초 시작 때 `data/sqlite_seed/release.json`에 지정된 GitHub Release의 SQLite를
다운로드·검증하여 15개 컬렉션을 `DATABASE_PATH`에 적재한다. 기존 사용자 테이블은 유지한다.
`uv sync --frozen` 후 `uv run --frozen python -m backend`만으로도 실행할 수 있다.
pm2 배포도 저장소 루트 `.venv`와 `uv.lock`을 사용한다.

| pm2 이름 | 주소 | 내용 |
|---|---|---|
| `seatcom-frontend` | `0.0.0.0:8000` | 프론트(`../2026-IST-TECH-SeatCom-FE/dist`) 정적 파일 + `/api`·`/health` → 백엔드 프록시 |
| `seatcom-backend` | `0.0.0.0:8001` | `backend/`: 회원가입(이메일 인증)·페르소나·카카오톡 실험실(웹 업로드·원문 미저장)·좌석 추천 모델(`seat_model/models/v1-20261115`) + 데이터셋 뷰어 `/`(`seatviz/`, 읽기 전용, 공개) |

포트 기본값은 프론트 8000·백엔드 8001이다. 서버별로 바꾸려면 `deploy/.env.example`을 `deploy/.env`로 복사해 `SEATCOM_FRONTEND_PORT`·`SEATCOM_BACKEND_PORT`를 적는다(`deploy.sh`와 pm2 설정이 읽는다).

```bash
bash deploy/deploy.sh             # 환경 설치 → 프론트 빌드 → pm2 시작/재시작 → pm2 save → 상태 확인
bash deploy/deploy.sh --no-build  # 프론트 빌드 없이 재시작
pm2 logs seatcom-backend          # 로그 (파일: deploy/logs/)
```

- **설정·비밀값:** `backend/.env`(권한 600, git 제외). 형식은 `backend/.env.example`. 바꾼 뒤 `pm2 restart seatcom-backend`.
  - `MAIL_SENDER`, `MAIL_PASSWORD`: 회원가입 인증 메일(Gmail 앱 비밀번호)
  - `AUTH_JWT_SECRET`: 로그인 토큰 서명 키
  - `OSS_API_KEY`: 카카오톡 분석 LLM. 비어 있으면 실험실 분석이 실패한다
  - `LAB_MAX_CONCURRENT`·`LAB_MAX_PENDING`·`LAB_UPLOADS_PER_HOUR`: 실험실 동시 분석 수·메모리 대기 상한·사용자당 시간당 업로드
- **실행 데이터:** `DATABASE_PATH`의 SQLite(기본 `backend/runtime/seatcom.sqlite3`). 기존 배포가 `deploy/runtime/`를 설정했다면 그 경로를 그대로 사용한다. 도서관 데이터와 계정·설문·추천·선택 기록·개인 학습 상태를 같은 DB에 저장한다.
- **프론트 빌드 값:** 프론트 저장소에 커밋된 `.env.production`(`VITE_MOCK_AUTH=false`, `VITE_ROOM_ID=yongin-giheung:room:1`). 의존성은 `package-lock.json` 기준 `npm ci`로 설치한다.

## API 흐름
1. `POST /api/v1/auth/email-codes` → `POST /api/v1/auth/signup` → `POST /api/v1/auth/login`(accessToken, 30일)
2. 페르소나 4문항 `PUT /api/v1/persona/answers/{id}` → `POST /api/v1/persona/analyze`
3. `POST /api/v1/recommendations/seats` → 설문(+카카오톡 분석)과 착석 기록으로 빈자리 순위
4. `POST /api/v1/recommendations/{id}/events`
   - `candidateSelected`: 개인 모델을 즉시 갱신(`effect: learned`)하고, 그 좌석을 3시간 사용 중으로 표시
   - `candidateRejected` + `excludeFromFuture`: 그 좌석을 다시 추천하지 않음
5. `GET /api/v1/users/me/preferences/insights` → 착석 기록으로 파악한 취향

## 한계
- **좌석 현황:** 실시간 수집원이 없어 10분 단위 모의 현황이다(`source: "simulated"`). 좌석 환경 특징은 v1 합성 환경이다.
- **카카오톡 실험실:** 웹에서 TXT를 올리면(`POST /api/v1/lab/sessions/{id}/file`, raw body) 메모리에서만 분석하고 원문은 저장하지 않는다. `OSS_API_KEY`가 있어야 분석된다. 백엔드를 재시작하면 분석 중이던 대화는 사라지고 다시 올려야 한다. 백엔드는 워커 1개로 돌린다(메모리 작업 목록이 프로세스 안에 있음).
- **HTTPS:** 프론트(8000)·백엔드(8001) 서버는 평문 HTTP다. 외부에 공개할 때는 반드시 TLS(리버스 프록시) 뒤에 둔다. 그렇지 않으면 업로드한 대화가 네트워크에서 그대로 보인다.
