#!/usr/bin/env bash
# SeatCom 통합 배포: 파이썬 환경 → 프론트 빌드 → pm2 시작/재시작 → 상태 확인
#   bash deploy/deploy.sh            전체
#   bash deploy/deploy.sh --no-build 프론트 빌드 생략(재시작만)
set -euo pipefail
DEPLOY="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DEPLOY")"
FE="$(dirname "$REPO")/2026-IST-TECH-SeatCom-FE"
ROOM_ID="${SEATCOM_ROOM_ID:-yongin-giheung:room:1}"
mkdir -p "$DEPLOY/logs" "$DEPLOY/runtime"

[ -f "$REPO/backend/.env" ] || { echo "backend/.env 없음: backend/.env.example을 복사해 채우세요"; exit 1; }

echo "== 1. 파이썬 환경 (deploy/.venv)"
[ -x "$DEPLOY/.venv/bin/python" ] || uv venv "$DEPLOY/.venv" --python 3.11 -q
VIRTUAL_ENV="$DEPLOY/.venv" uv pip install -q -r "$DEPLOY/requirements.txt"

if [ "${1:-}" != "--no-build" ]; then
  echo "== 2. 프론트엔드 빌드 ($FE)"
  # 배포용 값. *.local 이라 프론트 저장소 git에는 올라가지 않는다
  cat > "$FE/.env.production.local" <<ENV
VITE_MOCK_AUTH=false
VITE_DEV_TOKEN=
VITE_ROOM_ID=$ROOM_ID
ENV
  (cd "$FE" && npm install --no-package-lock --no-audit --no-fund --loglevel=error && npm run build)
fi

echo "== 3. pm2"
pm2 delete seatcom-identity >/dev/null 2>&1 || true   # 백엔드에 통합되어 더 이상 쓰지 않음
pm2 startOrReload "$DEPLOY/ecosystem.config.cjs" --update-env
pm2 save

echo "== 4. 상태 확인"
for i in $(seq 1 30); do
  curl -fsS -m 3 http://127.0.0.1:8027/health >/dev/null 2>&1 && break; sleep 1
done
curl -fsS -m 5 http://127.0.0.1:8027/health && echo
curl -fsS -m 5 http://127.0.0.1:8027/api/v1/system/model && echo
pm2 ls
