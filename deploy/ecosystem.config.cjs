// SeatCom pm2 설정: pm2 startOrReload deploy/ecosystem.config.cjs --update-env && pm2 save
//   seatcom-frontend  0.0.0.0:8000   프론트 저장소 server.mjs: 정적 파일 + /api 프록시
//   포트 기본값은 프론트 8000·백엔드 8001. 바꾸려면 deploy/.env 에 SEATCOM_FRONTEND_PORT·SEATCOM_BACKEND_PORT
//   seatcom-backend   0.0.0.0:8001   데이터셋 뷰어(/) + FastAPI: 회원가입(이메일 인증)·페르소나·카카오톡 실험실·좌석 추천 모델
// 설정·비밀값은 backend/.env (백엔드가 직접 읽는다)
const path = require('path')

const DEPLOY = __dirname
const REPO = path.resolve(DEPLOY, '..')
const FE = path.resolve(REPO, '..', '2026-IST-TECH-SeatCom-FE')
const FE_PORT = process.env.SEATCOM_FRONTEND_PORT || '8000'
const BE_PORT = process.env.SEATCOM_BACKEND_PORT || '8001'
const common = { autorestart: true, max_restarts: 20, min_uptime: '10s', restart_delay: 3000, time: true, merge_logs: true }
const logs = (name) => ({ out_file: path.join(DEPLOY, 'logs', `${name}.out.log`), error_file: path.join(DEPLOY, 'logs', `${name}.err.log`) })

module.exports = {
  apps: [
    {
      ...common, ...logs('seatcom-backend'),
      name: 'seatcom-backend',
      cwd: REPO,
      script: path.join(REPO, '.venv', 'bin', 'python'),
      args: `-m uvicorn backend.main:app --host 0.0.0.0 --port ${BE_PORT} --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1`,
      interpreter: 'none',
      env: { PYTHONPATH: REPO, PYTHONUNBUFFERED: '1', TZ: 'Asia/Seoul' },
    },
    {
      ...common, ...logs('seatcom-frontend'),
      name: 'seatcom-frontend',
      cwd: FE,
      script: path.join(FE, 'server.mjs'),   // 프론트 저장소의 서버(npm start 와 같음)
      interpreter: 'node',
      env: { PORT: FE_PORT, HOST: '0.0.0.0', FE_DIST: path.join(FE, 'dist'), BACKEND_URL: `http://127.0.0.1:${BE_PORT}` },
    },
  ],
}
