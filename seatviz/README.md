# SeatViz — SeatCom 데이터셋 뷰어

MongoDB `library` DB(실데이터 27관 + 합성 데이터)를 조회하는 FastAPI 웹앱. 백엔드(JSON API)와 프론트엔드(Jinja 템플릿 + 바닐라 JS/SVG)를 함께 제공한다. 구조는 UnivDash를 따른다.

## 실행

```bash
cd seatviz                   # seatviz는 별도 uv 프로젝트(Python 3.12, seatviz/.venv)
uv sync
cp .env.example .env         # HOST/PORT. Mongo 접속은 저장소 루트 ../.env 의 MONGO_* 사용
uv run run.py                # 기본 127.0.0.1:8026
```

pm2: `~/ecosystem.config.js`의 `seatviz` 앱이 `uv run --frozen python run.py`로 실행한다 (`pm2 restart seatviz`, 로그 `pm2 logs seatviz`). `app/`, `run.py` 변경 시 자동 재시작.

## 화면

| 경로 | 내용 |
|---|---|
| `/` | 개요: 컬렉션 건수, 27관 표(학습 대상/보관), 합성 생성 정보 |
| `/floor` | **도서관 뷰어**: 실좌석 배치 + 합성 환경을 평면도로 렌더링. 시나리오 30개, 시간대(아침/정오/저녁), 좌석 색칠(특성 12개·원값·이용), 바닥 히트맵(조도·바람), 레이어 토글, 좌석 상세 |
| `/real` | 실데이터: 지도 프레임별 좌석 bbox·시설 기하, 열람실·좌석·시설·특성·출처·구축 보고서 표 |
| `/users`, `/users/{id}` | 합성 사용자: 카카오톡 관측 프로필 vs 잠재 선호(정답), 엔진 입력, 효용 가중치, 세션 이력 |
| `/sessions`, `/sessions/{id}` | 합성 세션: 평면 위 추천 Top5·선택·비선택 좌석, 특성 비교표, 이벤트 타임라인 |
| `/stats` | 특성 분포, 책상·조도·환경 조건, 도서관별 평균, 선택 경로·순위·만족도(0~1), 프로필 정확도 |
| `/browse` | 모든 컬렉션 원본 문서 조회 (MongoDB JSON 필터·정렬·페이지) |

API: `/api/overview`, `/api/libraries/{id}`, `/api/scenarios/{id}`, `/api/users[/{id}]`, `/api/sessions[/{id}]`, `/api/stats`, `/api/collections[/{name}]`, 문서 `/api/docs`. 모두 읽기 전용.

## 구조

```text
seatviz/
├── run.py                 # uvicorn 진입점 (HOST/PORT)
├── pyproject.toml, uv.lock
├── app/
│   ├── main.py            # FastAPI 앱, /static, /healthz
│   ├── db.py              # MongoDB 접속(../.env), ObjectId 직렬화
│   ├── routes/api.py      # JSON API
│   ├── routes/pages.py    # HTML 페이지
│   ├── templates/         # _layout + 페이지별 템플릿
│   └── static/            # app.css, common.js, floor.js(평면 렌더러), vendor/(Chart.js·Font Awesome·폰트)
```

렌더링 주의: 미터 축척·벽·창·냉방기·조명·콘센트·책상 형태는 합성 값이다(`합성` 배지). 좌석 bbox·ID와 같은 지도의 출입구·화장실 후보만 실데이터다.
