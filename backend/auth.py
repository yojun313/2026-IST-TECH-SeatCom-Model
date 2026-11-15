"""회원가입·로그인: 이메일 인증 코드(Gmail SMTP) + 닉네임·비밀번호 → JWT(HS256).

    POST /api/v1/auth/email-codes   {email}                              인증 코드 6자리 메일 발송(5분 유효)
    POST /api/v1/auth/signup        {email, code, nickname, password}    코드 확인 후 가입 → accessToken
    POST /api/v1/auth/login         {nickname, password}                 → accessToken
    GET  /api/v1/auth/me                                                  내 계정
    POST /api/v1/auth/email-codes   {email, purpose:'reset'}             비밀번호 찾기 코드(가입한 이메일만)
    POST /api/v1/auth/password-reset {email, code, password}             코드 확인 후 새 비밀번호 → 닉네임·accessToken
    DELETE /api/v1/auth/me          {password}                           회원 탈퇴(모든 기록 삭제, 기존 토큰 무효)

- 비밀번호는 scrypt(솔트)로만 저장한다. 인증 코드도 해시로만 저장한다.
- 메일 계정: 환경변수 MAIL_SENDER, MAIL_PASSWORD(Gmail 앱 비밀번호). 값은 backend/.env에만 둔다.
- 실험실 본인 확인(LocalIdentityVerifier)도 여기서 한다: 메일 보낸 주소 = 가입 때 인증한 이메일.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import jwt

from .security import api_error
from .store import Store, now_iso

log = logging.getLogger('seatcom.auth')
CODE_TTL_S = 5 * 60            # 프론트 타이머와 같다
CODE_RESEND_S = 30
CODE_MAX_PER_HOUR = 8
CODE_MAX_ATTEMPTS = 5
TOKEN_DAYS = 30
EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS auth_users (
 user_id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, nickname TEXT NOT NULL, nickname_key TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_email_codes (
 email TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 sent_at REAL NOT NULL, sent_count_hour INTEGER NOT NULL DEFAULT 1, hour_start REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_revocations (
 user_id TEXT PRIMARY KEY, revoked_before INTEGER NOT NULL
);
'''
# 탈퇴할 때 지울 사용자 기록(테이블, 사용자 열)
USER_TABLES = [('persona_answers', 'user_id'), ('persona_results', 'user_id'), ('lab_results', 'user_id'),
               ('reco_events', 'user_id'), ('reco_selections', 'user_id'), ('reco_exclusions', 'user_id'),
               ('reco_user_state', 'user_id'), ('user_preferences', 'user_id'), ('reco_recommendations', 'user_id')]
FOREVER = 2 ** 62


def _norm_email(e: str) -> str:
    return (e or '').strip().lower()


def _nick_key(n: str) -> str:
    return unicodedata.normalize('NFC', (n or '').strip()).casefold()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f'scrypt$16384$8$1${salt.hex()}${h.hex()}'


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split('$')
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(h)))
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(self, settings, store: Store):
        self.s = settings
        self.store = store
        with self.store.db() as db:
            db.executescript(SCHEMA)
        self._fail: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 토큰
    def issue_token(self, user_id: str) -> str:
        if self.s.auth_jwt_algorithm != 'HS256' or not self.s.auth_jwt_secret or not self.s.auth_issuer or not self.s.auth_audience:
            api_error(503, 'AUTH_NOT_CONFIGURED', '로그인 서버 설정이 필요합니다.')
        now = datetime.now(timezone.utc)
        claims = {'sub': user_id, 'iss': self.s.auth_issuer, 'aud': self.s.auth_audience,
                  'iat': int(now.timestamp()), 'exp': int((now + timedelta(days=TOKEN_DAYS)).timestamp()), 'jti': uuid.uuid4().hex}
        return jwt.encode(claims, self.s.auth_jwt_secret, algorithm='HS256')

    # ---------------------------------------------------------------- 인증 코드
    def _code_hash(self, email: str, code: str) -> str:
        return hmac.new(self.s.auth_jwt_secret.encode(), f'{email}:{code}'.encode(), hashlib.sha256).hexdigest()

    def send_code(self, email: str, purpose: str = 'signup'):
        email = _norm_email(email)
        reset = purpose == 'reset'
        if not EMAIL_RE.fullmatch(email) or len(email) > 254:
            api_error(422, 'INVALID_EMAIL', '이메일 형식을 확인해 주세요.')
        sender, password = os.getenv('MAIL_SENDER', ''), os.getenv('MAIL_PASSWORD', '')
        if not sender or not password:
            api_error(503, 'MAIL_NOT_CONFIGURED', '인증 메일 발송 설정이 필요합니다.')
        now = time.time()
        with self.store.db() as db:
            exists = db.execute('SELECT 1 FROM auth_users WHERE email=?', (email,)).fetchone()
            if exists and not reset:
                api_error(409, 'EMAIL_TAKEN', '이미 가입한 이메일이에요. 로그인해 주세요.')
            if reset and not exists:
                api_error(404, 'EMAIL_NOT_FOUND', '이 이메일로 가입한 계정이 없어요. 이메일을 확인해 주세요.')
            row = self.store.row(db, 'SELECT * FROM auth_email_codes WHERE email=?', (email,))
        if row:
            if now - row['sent_at'] < CODE_RESEND_S:
                api_error(429, 'TOO_MANY_REQUESTS', f'{CODE_RESEND_S}초 뒤에 다시 받아 주세요.')
            in_hour = now - row['hour_start'] < 3600
            if in_hour and row['sent_count_hour'] >= CODE_MAX_PER_HOUR:
                api_error(429, 'TOO_MANY_REQUESTS', '인증 메일을 너무 많이 요청했어요. 잠시 뒤에 다시 시도해 주세요.')
            count, hour_start = (row['sent_count_hour'] + 1, row['hour_start']) if in_hour else (1, now)
        else:
            count, hour_start = 1, now
        code = f'{secrets.randbelow(10 ** 6):06d}'
        msg = EmailMessage()
        what = '비밀번호 재설정' if reset else '이메일 인증'
        msg['Subject'] = f'[SeatCom] {what} 코드'
        msg['From'] = f'SeatCom <{sender}>'
        msg['To'] = email
        msg.set_content(f'SeatCom {what} 코드: {code}\n\n5분 안에 입력해 주세요. 요청하지 않았다면 이 메일을 무시하세요.')
        msg.add_alternative(
            f'<div style="font-family:sans-serif;line-height:1.6"><p>SeatCom {what} 코드</p>'
            f'<p style="font-size:28px;font-weight:bold;letter-spacing:6px">{code}</p>'
            f'<p style="color:#666">5분 안에 입력해 주세요. 요청하지 않았다면 이 메일을 무시하세요.</p></div>', subtype='html')
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=20) as smtp:
                smtp.login(sender, password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError):
            log.exception('verification mail failed')
            api_error(503, 'MAIL_UNAVAILABLE', '인증 메일을 보내지 못했어요. 잠시 뒤에 다시 시도해 주세요.')
        with self.store.db() as db:
            db.execute('INSERT INTO auth_email_codes(email,code_hash,expires_at,attempts,sent_at,sent_count_hour,hour_start) VALUES(?,?,?,0,?,?,?) '
                       'ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash, expires_at=excluded.expires_at, attempts=0, '
                       'sent_at=excluded.sent_at, sent_count_hour=excluded.sent_count_hour, hour_start=excluded.hour_start',
                       (email, self._code_hash(email, code), now + CODE_TTL_S, now, count, hour_start))
        return {'sent': True, 'expires_in_seconds': CODE_TTL_S}

    def _consume_code(self, db, email: str, code: str):
        row = self.store.row(db, 'SELECT * FROM auth_email_codes WHERE email=?', (email,))
        if not row:
            api_error(400, 'CODE_NOT_REQUESTED', '먼저 인증 코드를 받아 주세요.')
        if time.time() > row['expires_at']:
            api_error(400, 'CODE_EXPIRED', '인증 코드 시간이 지났어요. 코드를 다시 받아 주세요.')
        if row['attempts'] >= CODE_MAX_ATTEMPTS:
            api_error(429, 'CODE_LOCKED', '인증 코드를 너무 많이 틀렸어요. 코드를 다시 받아 주세요.')
        if not re.fullmatch(r'\d{6}', code or '') or not hmac.compare_digest(self._code_hash(email, code), row['code_hash']):
            db.execute('UPDATE auth_email_codes SET attempts=attempts+1 WHERE email=?', (email,))
            api_error(400, 'INVALID_CODE', '인증 코드가 맞지 않아요.')
        db.execute('DELETE FROM auth_email_codes WHERE email=?', (email,))

    # ---------------------------------------------------------------- 가입·로그인
    def signup(self, email: str, code: str, nickname: str, password: str):
        email, nick = _norm_email(email), unicodedata.normalize('NFC', (nickname or '').strip())
        if not (1 <= len(nick) <= 20):
            api_error(422, 'INVALID_NICKNAME', '닉네임은 1~20자로 입력해 주세요.')
        if len(password or '') < 6 or len(password) > 128:
            api_error(422, 'WEAK_PASSWORD', '비밀번호는 6자 이상으로 입력해 주세요.')
        uid = 'usr_' + uuid.uuid4().hex
        with self.store.db() as db:
            if db.execute('SELECT 1 FROM auth_users WHERE email=?', (email,)).fetchone():
                api_error(409, 'EMAIL_TAKEN', '이미 가입한 이메일이에요. 로그인해 주세요.')
            if db.execute('SELECT 1 FROM auth_users WHERE nickname_key=?', (_nick_key(nick),)).fetchone():
                api_error(409, 'NICKNAME_TAKEN', '이미 쓰고 있는 닉네임이에요. 다른 닉네임으로 가입해 주세요.')
            self._consume_code(db, email, code)          # 틀리면 시도 횟수만 늘고 예외
            try:
                db.execute('INSERT INTO auth_users VALUES(?,?,?,?,?,?)', (uid, email, nick, _nick_key(nick), hash_password(password), now_iso()))
            except sqlite3.IntegrityError:
                api_error(409, 'NICKNAME_TAKEN', '이미 가입한 이메일이거나 쓰고 있는 닉네임이에요.')
        return {'userId': uid, 'nickname': nick, 'accessToken': self.issue_token(uid), 'tokenType': 'Bearer'}

    def login(self, nickname: str, password: str):
        key = _nick_key(nickname)
        now = time.time()
        with self._lock:
            fails = [t for t in self._fail.get(key, []) if now - t < 600]
            self._fail[key] = fails
            if len(fails) >= 10:
                api_error(429, 'TOO_MANY_REQUESTS', '로그인 시도가 너무 많아요. 10분 뒤에 다시 시도해 주세요.')
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT user_id,nickname,password_hash FROM auth_users WHERE nickname_key=?', (key,))
        if not row or not verify_password(password or '', row['password_hash']):
            with self._lock:
                self._fail.setdefault(key, []).append(now)
            api_error(401, 'INVALID_CREDENTIALS', '닉네임 또는 비밀번호가 맞지 않아요.')
        with self._lock:
            self._fail.pop(key, None)
        return {'userId': row['user_id'], 'nickname': row['nickname'], 'accessToken': self.issue_token(row['user_id']), 'tokenType': 'Bearer'}

    def me(self, user_id: str):
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT user_id,email,nickname,created_at FROM auth_users WHERE user_id=?', (user_id,))
        if not row:
            api_error(404, 'NOT_FOUND', '계정을 찾을 수 없습니다.')
        return {'userId': row['user_id'], 'email': row['email'], 'nickname': row['nickname'], 'createdAt': row['created_at']}

    # ---------------------------------------------------------------- 비밀번호 찾기·탈퇴
    def reset_password(self, email: str, code: str, password: str):
        """메일 코드로 본인 확인 → 새 비밀번호. 로그인은 닉네임으로 하므로 닉네임도 알려 준다. 이전 토큰은 모두 끊는다."""
        email = _norm_email(email)
        if len(password or '') < 6 or len(password) > 128:
            api_error(422, 'WEAK_PASSWORD', '비밀번호는 6자 이상으로 입력해 주세요.')
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT user_id,nickname,nickname_key FROM auth_users WHERE email=?', (email,))
            if not row:
                api_error(404, 'EMAIL_NOT_FOUND', '이 이메일로 가입한 계정이 없어요.')
            self._consume_code(db, email, code)
            db.execute('UPDATE auth_users SET password_hash=? WHERE user_id=?', (hash_password(password), row['user_id']))
            self._revoke(db, row['user_id'], int(time.time()))
        with self._lock:
            self._fail.pop(row['nickname_key'], None)
        return {'userId': row['user_id'], 'nickname': row['nickname'], 'accessToken': self.issue_token(row['user_id']), 'tokenType': 'Bearer'}

    def delete_account(self, user_id: str, password: str):
        """비밀번호 확인 후 계정과 모든 기록(설문·추천·착석·취향·실험실 대화 파일)을 지운다."""
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT email,password_hash FROM auth_users WHERE user_id=?', (user_id,))
        if not row:
            api_error(404, 'NOT_FOUND', '계정을 찾을 수 없습니다.')
        if not verify_password(password or '', row['password_hash']):
            api_error(401, 'INVALID_CREDENTIALS', '비밀번호가 맞지 않아요.')
        files = []
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            ups = [r['upload_id'] for r in db.execute('SELECT upload_id FROM lab_sessions WHERE user_id=?', (user_id,))]
            for r in db.execute('SELECT a.file_id,a.path FROM attachments a JOIN lab_sessions s ON s.file_id=a.file_id WHERE s.user_id=?', (user_id,)):
                files.append(r['path'])
                db.execute('DELETE FROM attachments WHERE file_id=?', (r['file_id'],))
            for u in ups:
                db.execute('DELETE FROM ingested_events WHERE upload_id=?', (u,))
            db.execute('DELETE FROM lab_sessions WHERE user_id=?', (user_id,))
            for t, col in USER_TABLES:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                    db.execute(f'DELETE FROM {t} WHERE {col}=?', (user_id,))
            db.execute('DELETE FROM auth_email_codes WHERE email=?', (row['email'],))
            db.execute('DELETE FROM auth_users WHERE user_id=?', (user_id,))
            self._revoke(db, user_id, FOREVER)
            db.commit()
        for f in files:
            try:
                os.unlink(f)
            except OSError:
                pass
        return {'deleted': True}

    def _revoke(self, db, user_id: str, before: int):
        db.execute('INSERT INTO auth_revocations(user_id,revoked_before) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET '
                   'revoked_before=excluded.revoked_before', (user_id, before))

    def check_token(self, claims: dict) -> str:
        """탈퇴한 계정이거나 비밀번호를 바꾸기 전에 받은 토큰이면 거절한다."""
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT revoked_before FROM auth_revocations WHERE user_id=?', (claims['sub'],))
        if row and int(claims.get('iat') or 0) < row['revoked_before']:
            api_error(401, 'UNAUTHORIZED', '다시 로그인해 주세요.')
        return claims['sub']

    def email_of(self, user_id: str):
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT email FROM auth_users WHERE user_id=?', (user_id,))
        return row['email'] if row else None


class LocalIdentityVerifier:
    """카카오톡 실험실 본인 확인(백엔드 내장). 실험실 메일을 보낸 주소가 그 사용자가 가입 때 인증한 이메일인지 대조한다.
    카톡 이름(target_name)은 사용자가 세션을 만들 때 직접 입력한 값이라, 비어 있지 않은지만 본다."""

    def __init__(self, store: Store):
        self.store = store

    def verify(self, request: dict) -> dict:
        with self.store.db() as db:
            row = self.store.row(db, 'SELECT email FROM auth_users WHERE user_id=?', (request['user_id'],))
        email_match = bool(row and row['email'].casefold() == (request.get('sender_email') or '').strip().casefold())
        name_match = bool((request.get('target_name') or '').strip())
        ok = email_match and name_match
        return {'verification_request_id': request['verification_request_id'], 'user_id': request['user_id'],
                'matched': ok, 'verification': 'approved' if ok else 'rejected',
                'checks': {'email_match': email_match, 'name_match': name_match},
                **({} if ok else {'reason_code': 'EMAIL_MISMATCH' if not email_match else 'NAME_MISMATCH'}),
                'verified_at': datetime.now(timezone.utc).isoformat()}
