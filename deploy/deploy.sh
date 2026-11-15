#!/usr/bin/env bash
# SeatCom 통합 배포: 파이썬 환경 → 프론트 빌드 → pm2 시작/재시작 → 상태 확인
#   bash deploy/deploy.sh            전체
#   bash deploy/deploy.sh --no-build 프론트 빌드 생략(재시작만)
set -euo pipefail
DEPLOY="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DEPLOY")"
FE="$(dirname "$REPO")/2026-IST-TECH-SeatCom-FE"
mkdir -p "$DEPLOY/logs" "$DEPLOY/runtime"

# 서버별 포트(선택): deploy/.env 의 SEATCOM_FRONTEND_PORT(기본 8000)·SEATCOM_BACKEND_PORT(기본 8001)
if [ -f "$DEPLOY/.env" ]; then set -a; . "$DEPLOY/.env"; set +a; fi
FE_PORT="${SEATCOM_FRONTEND_PORT:-8000}"

[ -f "$REPO/backend/.env" ] || { echo "backend/.env 없음: backend/.env.example을 복사해 채우세요"; exit 1; }

echo "== 1. 잠금 파일 기반 파이썬 환경 (.venv)"
(cd "$REPO" && uv sync --frozen --no-dev)

if [ "${1:-}" != "--no-build" ]; then
  echo "== 2. 프론트엔드 빌드 ($FE)"
  # 배포용 값(VITE_MOCK_AUTH=false 등)은 프론트 저장소의 .env.production 에 커밋돼 있다
  (cd "$FE" && npm ci --no-audit --no-fund --loglevel=error && npm run build)
fi

echo "== 3. pm2"
pm2 delete seatcom-identity >/dev/null 2>&1 || true   # 백엔드에 통합되어 더 이상 쓰지 않음
pm2 startOrReload "$DEPLOY/ecosystem.config.cjs" --update-env
pm2 save

echo "== 4. 상태 확인"
for i in $(seq 1 30); do
  curl -fsS -m 3 http://127.0.0.1:$FE_PORT/health >/dev/null 2>&1 && break; sleep 1
done
curl -fsS -m 5 http://127.0.0.1:$FE_PORT/health && echo
curl -fsS -m 5 http://127.0.0.1:$FE_PORT/api/v1/system/model && echo
pm2 ls
