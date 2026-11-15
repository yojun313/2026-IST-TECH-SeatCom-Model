# SeatCom: 도서관 열람실 개인 맞춤 좌석 추천

회원가입(이메일 인증) → 4문항 취향 설문(선택: 카카오톡 대화 분석) → 학습한 모델로 빈자리 추천 → 고른 좌석이 바로 개인 모델에 반영된다.

## 구조

| 폴더 | 역할 |
|---|---|
| `backend/` | FastAPI 서버: 회원가입·로그인(이메일 인증), 페르소나 설문, 카카오톡 실험실(본인 확인 내장), **좌석 추천 API**. 설정은 `backend/.env` |
| `seat_model/` | 좌석 추천 모델: 학습 파이프라인(`train/`), 서비스 모델(`models/v1-20261115/`), 리포트·문서 |
| `kakao_analysis/` | 카카오톡 대화에서 좌석 취향 추출(LLM), Gmail 첨부 수신, 출력 스키마(`schema/`) |
| `recommender/` | 취향 변환기(`preference_adapter.py`: 카카오톡 분석 → 모델 취향 프로필), 기준 모델 `seatrec` |
| `data/` | 도서관·열람실·좌석 데이터 구축, 합성 데이터 생성·MongoDB 적재 |
| `seatviz/` | 데이터 시각화 사이트(pm2 `seatviz`, 8026) |
| `deploy/` | 배포: pm2 설정, 프론트엔드 서버, 배포 스크립트 |
| `docs/` | API 명세 초안, 조사 자료(`research/`) |

프론트엔드는 별도 저장소 `../2026-IST-TECH-SeatCom-FE`.

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
uv sync                                                     # 데이터·기준 모델 환경(.venv)
uv run data/libraries/scripts/generate_synthetic.py --users 3000 --sessions 165000
uv run data/libraries/scripts/load_mongo.py                 # MongoDB library 적재
seat_model/.venv/bin/python -m seat_model.train.data        # 좌석 모델 학습(seat_model/README.md)
deploy/.venv/bin/python -m pytest backend/tests -q          # 백엔드 테스트(저장소 루트에서)
seat_model/.venv/bin/python -m pytest seat_model/tests -q   # 모델 테스트
```

참고: [seat_model/README.md](seat_model/README.md) · [seat_model/reports/training_report.md](seat_model/reports/training_report.md) · [data/libraries/README.md](data/libraries/README.md) · [recommender/TRAINING.md](recommender/TRAINING.md) · [seatviz/README.md](seatviz/README.md)
