# SeatCom 배포 (pm2)

| pm2 이름 | 주소 | 내용 |
|---|---|---|
| `seatcom-frontend` | `0.0.0.0:8027` | 프론트(`../2026-IST-TECH-SeatCom-FE/dist`) 정적 파일 + `/api`·`/health` → 백엔드 프록시 |
| `seatcom-backend` | `127.0.0.1:8028` | `backend/`: 회원가입(이메일 인증)·페르소나·카카오톡 실험실(본인 확인 내장)·좌석 추천 모델(`seat_model/models/v1-20261115`) |

```bash
bash deploy/deploy.sh             # 환경 설치 → 프론트 빌드 → pm2 시작/재시작 → pm2 save → 상태 확인
bash deploy/deploy.sh --no-build  # 프론트 빌드 없이 재시작
pm2 logs seatcom-backend          # 로그 (파일: deploy/logs/)
```

- **설정·비밀값:** `backend/.env`(권한 600, git 제외). 형식은 `backend/.env.example`. 바꾼 뒤 `pm2 restart seatcom-backend`.
  - `MAIL_SENDER`, `MAIL_PASSWORD`: 회원가입 인증 메일(Gmail 앱 비밀번호)
  - `AUTH_JWT_SECRET`: 로그인 토큰 서명 키
  - `OSS_API_KEY`: 카카오톡 분석 LLM. 비어 있으면 실험실 분석이 실패한다
  - `GMAIL_POLL_ENABLED`: 실험실 메일 수신. 꺼져 있으면 실험실이 메일 대기에서 멈춘다
- **실행 데이터:** `deploy/runtime/`(SQLite: 계정, 설문 답, 추천·선택 기록, 사용자별 온라인 학습 상태).
- **프론트 빌드 값:** `VITE_MOCK_AUTH=false`(실제 로그인), `SEATCOM_ROOM_ID`(기본 `yongin-giheung:room:1`). 빌드할 때 `FE/.env.production.local`에 기록된다.

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
- **카카오톡 실험실:** `OSS_API_KEY`와 Gmail 수신을 켜야 분석까지 진행된다.
