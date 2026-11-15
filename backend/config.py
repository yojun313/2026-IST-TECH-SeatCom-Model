from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / 'backend' / '.env', override=False)


def enabled(name: str, default: str = 'false') -> bool:
    return os.getenv(name, default).strip().lower() in ('1', 'true', 'yes')


@dataclass(frozen=True)
class Settings:
    database_path: Path
    attachment_dir: Path
    mail_directory: Path
    recipient_email: str
    auth_issuer: str
    auth_audience: str
    auth_jwt_algorithm: str
    auth_jwt_secret: str
    auth_jwt_public_key: str
    internal_service_token: str
    gmail_enabled: bool
    worker_enabled: bool
    poll_seconds: int
    gmail_poll_seconds: int
    upload_expiry_minutes: int
    max_txt_bytes: int
    cors_origins: tuple[str, ...]
    # 좌석 추천 모델(seat_model/models/<버전>)과 게스트 토큰(프론트 가짜 로그인용, 운영 인증 전까지)
    model_dir: str = str(PROJECT_ROOT / 'seat_model' / 'models' / 'v1-20261115')
    allow_guest_tokens: bool = False


def get_settings() -> Settings:
    root = PROJECT_ROOT / 'backend'
    return Settings(
        database_path=Path(os.getenv('DATABASE_PATH', str(root / 'runtime' / 'seatcom.sqlite3'))).expanduser().resolve(),
        attachment_dir=Path(os.getenv('ATTACHMENT_DIR', str(root / 'runtime' / 'attachments'))).expanduser().resolve(),
        mail_directory=Path(os.getenv('GMAIL_AUTH_DIR', str(root / 'runtime' / 'gmail'))).expanduser().resolve(),
        recipient_email=os.getenv('GMAIL_RECIPIENT', 'taywook74@gmail.com'),
        auth_issuer=os.getenv('AUTH_ISSUER', ''),
        auth_audience=os.getenv('AUTH_AUDIENCE', ''),
        auth_jwt_algorithm=os.getenv('AUTH_JWT_ALGORITHM', 'RS256'),
        auth_jwt_secret=os.getenv('AUTH_JWT_SECRET', ''),
        auth_jwt_public_key=os.getenv('AUTH_JWT_PUBLIC_KEY', '').replace('\\n', '\n'),
        internal_service_token=os.getenv('INTERNAL_SERVICE_TOKEN', ''),
        gmail_enabled=enabled('GMAIL_POLL_ENABLED'),
        worker_enabled=enabled('LAB_WORKER_ENABLED', 'true'),
        poll_seconds=max(1, int(os.getenv('LAB_WORKER_POLL_SECONDS', '2'))),
        gmail_poll_seconds=max(5, int(os.getenv('GMAIL_POLL_SECONDS', '20'))),
        upload_expiry_minutes=max(1, int(os.getenv('UPLOAD_EXPIRY_MINUTES', '30'))),
        max_txt_bytes=min(20_000_000, max(1024, int(os.getenv('MAX_TXT_BYTES', '5000000')))),
        cors_origins=tuple(x.strip() for x in os.getenv('CORS_ORIGINS', '').split(',') if x.strip()),
        model_dir=os.getenv('SEAT_MODEL_DIR', str(PROJECT_ROOT / 'seat_model' / 'models' / 'v1-20261115')),
        allow_guest_tokens=enabled('ALLOW_GUEST_TOKENS'),
    )
