from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)


def enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_path: Path
    # 예전 메일 수집 방식이 첨부파일을 두던 폴더. 지금은 아무것도 쓰지 않고, 시작할 때 남은 파일을 지우기만 한다.
    attachment_dir: Path
    auth_issuer: str
    auth_audience: str
    auth_jwt_algorithm: str
    auth_jwt_secret: str
    auth_jwt_public_key: str
    upload_expiry_minutes: int
    max_txt_bytes: int
    cors_origins: tuple[str, ...]
    # 카카오톡 실험실: 대화 원문은 메모리에서만 다룬다(디스크·DB에 쓰지 않음)
    lab_max_concurrent: int = 2  # 동시에 분석하는 수
    lab_max_pending: int = 6  # 메모리에 올려 둘 수 있는 대화 수(분석 중 + 대기)
    lab_uploads_per_hour: int = 5  # 사용자당 시간당 업로드 횟수
    # 좌석 추천 모델(seat_model/models/<버전>)과 게스트 토큰(프론트 가짜 로그인용, 운영 인증 전까지)
    model_dir: str = str(PROJECT_ROOT / "seat_model" / "models" / "v1-20261115")
    allow_guest_tokens: bool = False


def get_settings() -> Settings:
    root = PROJECT_ROOT / "backend"
    return Settings(
        database_path=Path(
            os.getenv("DATABASE_PATH", str(root / "runtime" / "seatcom.sqlite3"))
        )
        .expanduser()
        .resolve(),
        attachment_dir=Path(
            os.getenv("ATTACHMENT_DIR", str(root / "runtime" / "attachments"))
        )
        .expanduser()
        .resolve(),
        auth_issuer=os.getenv("AUTH_ISSUER", ""),
        auth_audience=os.getenv("AUTH_AUDIENCE", ""),
        auth_jwt_algorithm=os.getenv("AUTH_JWT_ALGORITHM", "RS256"),
        auth_jwt_secret=os.getenv("AUTH_JWT_SECRET", ""),
        auth_jwt_public_key=os.getenv("AUTH_JWT_PUBLIC_KEY", "").replace("\\n", "\n"),
        upload_expiry_minutes=max(1, int(os.getenv("UPLOAD_EXPIRY_MINUTES", "30"))),
        max_txt_bytes=min(
            20_000_000, max(1024, int(os.getenv("MAX_TXT_BYTES", "5000000")))
        ),
        lab_max_concurrent=max(1, int(os.getenv("LAB_MAX_CONCURRENT", "2"))),
        lab_max_pending=max(1, int(os.getenv("LAB_MAX_PENDING", "6"))),
        lab_uploads_per_hour=max(1, int(os.getenv("LAB_UPLOADS_PER_HOUR", "5"))),
        cors_origins=tuple(
            x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()
        ),
        model_dir=os.getenv(
            "SEAT_MODEL_DIR",
            str(PROJECT_ROOT / "seat_model" / "models" / "v1-20261115"),
        ),
        allow_guest_tokens=enabled("ALLOW_GUEST_TOKENS"),
    )
