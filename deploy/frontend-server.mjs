// SeatCom 프론트엔드 서버: 빌드된 정적 파일(dist) + /api 프록시(백엔드). 의존성 없음(Node 18+).
//   PORT=8027 FE_DIST=/path/to/dist BACKEND_URL=http://127.0.0.1:8028 node frontend-server.mjs
// 브라우저는 같은 출처(8027)로만 요청하므로 CORS 설정이 필요 없다.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'

const PORT = Number(process.env.PORT || 8027)
const HOST = process.env.HOST || '0.0.0.0'
const DIST = path.resolve(process.env.FE_DIST || '../2026-IST-TECH-SeatCom-FE/dist')
const BACKEND = new URL(process.env.BACKEND_URL || 'http://127.0.0.1:8028')
const PROXY_PREFIXES = ['/api/', '/health']

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.txt': 'text/plain; charset=utf-8', '.map': 'application/json',
}

function proxy(req, res) {
  const headers = { ...req.headers, host: BACKEND.host, 'x-forwarded-for': req.socket.remoteAddress ?? '', 'x-forwarded-proto': 'http' }
  const up = http.request(
    { hostname: BACKEND.hostname, port: BACKEND.port, path: req.url, method: req.method, headers, timeout: 120_000 },
    (r) => {
      res.writeHead(r.statusCode ?? 502, r.headers)
      r.pipe(res)
    },
  )
  up.on('timeout', () => up.destroy(new Error('upstream timeout')))
  up.on('error', () => {
    if (!res.headersSent) {
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
      res.end(JSON.stringify({ error: { code: 'BACKEND_UNAVAILABLE', message: '백엔드 서버에 연결할 수 없습니다.' } }))
    } else res.destroy()
  })
  req.pipe(up)
}

function sendFile(res, file, status = 200) {
  const ext = path.extname(file).toLowerCase()
  const immutable = file.includes(`${path.sep}assets${path.sep}`)
  res.writeHead(status, {
    'content-type': TYPES[ext] ?? 'application/octet-stream',
    'cache-control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
    'x-content-type-options': 'nosniff',
  })
  fs.createReadStream(file).pipe(res)
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://x')
  if (PROXY_PREFIXES.some((p) => url.pathname === p.replace(/\/$/, '') || url.pathname.startsWith(p))) return proxy(req, res)
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { allow: 'GET, HEAD' })
    return res.end()
  }
  let rel
  try {
    rel = decodeURIComponent(url.pathname)
  } catch {
    res.writeHead(400)
    return res.end()
  }
  const file = path.resolve(DIST, '.' + rel)
  if (!file.startsWith(DIST + path.sep) && file !== DIST) {
    res.writeHead(403)
    return res.end()
  }
  fs.stat(file, (err, st) => {
    if (!err && st.isFile()) return sendFile(res, file)
    // 확장자가 있는 정적 파일이 없으면 404, 그 외 경로는 SPA 진입점
    if (path.extname(rel)) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      return res.end('Not found')
    }
    sendFile(res, path.join(DIST, 'index.html'))
  })
})

if (!fs.existsSync(path.join(DIST, 'index.html'))) {
  console.error(`index.html 없음: ${DIST} — 먼저 프론트엔드를 빌드하세요 (deploy/deploy.sh)`)
  process.exit(1)
}
server.listen(PORT, HOST, () => console.log(`SeatCom frontend http://${HOST}:${PORT}  dist=${DIST}  api→${BACKEND.origin}`))
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => server.close(() => process.exit(0)))
