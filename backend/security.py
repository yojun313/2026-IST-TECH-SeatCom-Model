from __future__ import annotations

import hashlib
import hmac
import re
from typing import Annotated

import jwt
from fastapi import Header, HTTPException
from .config import Settings


GUEST_TOKEN = re.compile(r'mock-token-[A-Za-z0-9-]{8,80}')


def api_error(status: int, code: str, message: str, **extra):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message, **extra})


def subject_from_token(authorization: str | None, settings: Settings) -> str:
    return claims_from_token(authorization, settings)['sub']


def claims_from_token(authorization: str | None, settings: Settings) -> dict:
    """검증된 토큰의 sub·iat. 게스트 토큰은 iat=0."""
    if not authorization or not authorization.startswith('Bearer '):
        api_error(401, 'UNAUTHORIZED', 'Bearer 토큰이 필요합니다.')
    token = authorization[7:].strip()
    # 게스트 토큰: 프론트 가짜 로그인(VITE_MOCK_AUTH)이 브라우저마다 만드는 무작위 토큰. 운영 인증이 생기기 전까지만 켠다.
    if settings.allow_guest_tokens and GUEST_TOKEN.fullmatch(token):
        return {'sub': 'guest-' + hashlib.sha256(token.encode()).hexdigest()[:32], 'iat': 0}
    alg = settings.auth_jwt_algorithm
    if alg not in {'RS256', 'HS256'}:
        api_error(503, 'AUTH_NOT_CONFIGURED', '지원되지 않는 JWT 알고리즘입니다.')
    if not settings.auth_issuer or not settings.auth_audience:
        api_error(503, 'AUTH_NOT_CONFIGURED', '인증 서버 구성이 필요합니다.')
    key = settings.auth_jwt_public_key if alg == 'RS256' else settings.auth_jwt_secret
    if not key:
        api_error(503, 'AUTH_NOT_CONFIGURED', '인증키가 설정되지 않았습니다.')
    try:
        claims = jwt.decode(
            token, key, algorithms=[alg], issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            options={'require': ['sub', 'exp', 'iat', 'iss', 'aud']},
        )
    except jwt.PyJWTError:
        api_error(401, 'UNAUTHORIZED', '유효하지 않거나 만료된 인증 토큰입니다.')
    sub = claims.get('sub')
    if not isinstance(sub, str) or not sub.strip():
        api_error(401, 'UNAUTHORIZED', '유효한 사용자 ID가 없습니다.')
    return {'sub': sub, 'iat': claims.get('iat', 0)}


def require_internal(authorization: str | None, settings: Settings) -> None:
    if not settings.internal_service_token:
        api_error(503, 'SERVICE_NOT_CONFIGURED', '서버 간 인증키가 설정되지 않았습니다.')
    supplied = (authorization or '').removeprefix('Bearer ').strip()
    if not authorization or not authorization.startswith('Bearer ') or not hmac.compare_digest(
        supplied, settings.internal_service_token
    ):
        api_error(401, 'UNAUTHORIZED', '허용되지 않은 서버 간 요청입니다.')
