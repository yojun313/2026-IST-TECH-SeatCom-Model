# SQLite 데이터 Release

초기 데이터는 Git 본문 대신 **GitHub Release**로 배포한다.
[데이터 릴리즈](https://github.com/yojun313/2026-IST-TECH-SeatCom-Model/releases/tag/sqlite-data-20261115-v1)
· [압축 SQLite 다운로드](https://github.com/yojun313/2026-IST-TECH-SeatCom-Model/releases/download/sqlite-data-20261115-v1/seatcom-library-sqlite-data-20261115-v1.sqlite3.gz)

`release.json`이 버전, 다운로드 URL, 압축/SQLite 크기와 SHA-256, 컬렉션별 건수를 고정한다.
임의의 최신 릴리즈를 따라가지 않으므로 같은 커밋은 같은 데이터로 실행된다.
현재 약 97MB의 gzip 파일을 풀면 약 1.4GB의 SQLite가 된다.

MongoDB `library`의 15개 컬렉션 전체를 보존한다. MongoDB 내부 `_id`만 제외하고
업무 ID와 모든 필드를 보존했다. `synth_users`, `synth_sessions`, `synth_events`는
학습용 합성 데이터다. 실제 회원·카카오톡 원문·개인 분석 결과·비밀값은 포함하지 않는다.

## 자동/수동 다운로드

```bash
uv sync --frozen
uv run --frozen python -m backend
```

최초 실행에 자동 다운로드한다. 직접 받은 `.sqlite3.gz` 또는 압축을 푼 `.sqlite3`는
`SEATCOM_SEED_PATH=/absolute/path/to/file`로 지정할 수 있다. 캐시는 기본
`~/.cache/seatcom/`이며 `SEATCOM_DATA_CACHE`로 변경한다. 서버 사용자가 이 경로에
쓰기 권한을 가져야 한다. 시작할 때 다운로드에 실패하면 오프라인 파일을 지정하고 재시작한다.

압축 파일과 SQLite 모두 체크섬·크기를 확인한다. SQLite 안에 카탈로그 이외 테이블이
있으면 거절한다. 임시 파일을 완성한 후 원자적으로 교체하므로 중단된 다운로드는 재사용하지 않는다.
검증된 데이터는 `DATABASE_PATH`의 `catalog_documents`와 `catalog_metadata`에 적재한다.
기존 회원·설문·이용 기록 테이블은 건드리지 않으며, 갱신 실패 시 이전 카탈로그로 롤백한다.
이미 같은 버전이 적재된 DB는 인터넷 없이 시작한다. 실행 중 조회·저장은 단일 SQLite다.

웹 API와 모델 학습의 FeatureReader/SplitReader/TruthReader 모두 SQLite를 읽고,
학습 필드 허용 목록·정보 누설 방지 검사를 유지한다. 별도 실험 DB나 seatviz 등
과거 Mongo 전용 도구는 백엔드 전환 대상이 아니다.

## 새 데이터 Release 만들기

일반 실행에는 필요 없다. MongoDB 원본 데이터가 변할 때 관리자가 진행한다.
원본 `.env`의 `MONGO_*`를 쓰며 추출 중 원본 데이터 변경을 멈춘다.

```bash
uv run --group migration python -m data.libraries.scripts.export_sqlite_seed \
  --output deploy/artifacts/library.sqlite3
uv run --frozen python -m backend.package_seed \
  --source deploy/artifacts/library.sqlite3 \
  --tag sqlite-data-YYYYMMDD-vN
cp deploy/artifacts/release.json data/sqlite_seed/release.json
```

내보내기는 허용된 `library` 컬렉션만 읽고 원본을 수정하지 않는다. 패키징은 SQLite
무결성을 검사하고 사용자 테이블이 들어 있는 운영 DB를 거절한다.
배포 파일은 `deploy/artifacts/`에 생성되며 Git에서 제외된다.

테스트 후 코드와 `release.json`을 커밋·푸시한다. GitHub CLI 인증(`gh auth login`) 후
해당 커밋 SHA를 지정해 배포한다. 이미 공개된 버전의 파일은 덮어쓰지 않는다.

```bash
uv run --frozen python -m backend.publish_seed --target COMMIT_SHA
```

이 명령은 초안 Release를 만들고 `.sqlite3.gz`, `release.json`, `SHA256SUMS`를 업로드한
뒤 공개한다. 중간에 중단된 초안은 동일 명령으로 이어갈 수 있다. 공개 후 직접 다운로드
URL의 체크섬과 새 서버의 최초 실행을 확인한다. 운영 DB는 SQLite backup API로 백업한 뒤
재시작해 새 카탈로그를 반영한다. 새 코드 배포 전에 Release 파일이 공개되어 있어야 한다.
