"""
SeatCom integration: Gmail TXT -> KNPU GPT-OSS -> SQLite -> JSON.

Put this file beside api.py, extractor.py and txt_from_gmail.py.

Dependencies:
    uv pip install fastapi uvicorn google-api-python-client google-auth-oauthlib \
        google-auth-httplib2 openai pydantic python-dotenv

Environment (.env):
    OSS_API_KEY=...
    OSS_BASE_URL=https://llm0.knpu.re.kr/v1
    OSS_MODEL=openai/gpt-oss-120b
    KAKAO_TARGET_NAME=강민서  # The KakaoTalk sender name to analyze
    # Optional: KAKAO_SENDER_NAME_MAP={"person@example.com":"카카오톡닉네임"}
    SEATCOM_API_KEY=some-long-random-private-value

CLI listener:
    python seatcom_pipeline.py

REST API (one worker only):
    uvicorn seatcom_pipeline:app --host 127.0.0.1 --port 8000 --workers 1

    curl -H 'X-API-Key: YOUR_KEY' http://127.0.0.1:8000/profiles
    curl -H 'X-API-Key: YOUR_KEY' http://127.0.0.1:8000/profiles/PROFILE_ID
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException

from api import PersonalityAnalysis
from get_gmail_text_api.txt_from_gmail import TxtFromGmail

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)


class SeatComPipeline:
    def __init__(
        self,
        target_name: str | None = None,
        poll_seconds: int = 20,
        directory: str | Path | None = None,
        sender_name_map: dict[str, str] | None = None,
    ):
        self.directory = Path(directory or BASE_DIR).resolve()
        self.gmail = TxtFromGmail(poll_seconds=poll_seconds, directory=self.directory)
        self.analyzer = PersonalityAnalysis()  # Uses existing KNPU API, not local vLLM
        self.default_target = (target_name or os.getenv("KAKAO_TARGET_NAME", "")).strip()

        env_map = json.loads(os.getenv("KAKAO_SENDER_NAME_MAP", "{}"))
        names = sender_name_map if sender_name_map is not None else env_map
        self.sender_name_map = {str(k).lower(): str(v).strip() for k, v in names.items()}

        self.db_path = self.directory / "seatcom_profiles.sqlite3"
        self._stop_event = threading.Event()
        self._gmail_lock = threading.Lock()
        self._queue: Queue[str] = Queue()
        self._scheduled: set[str] = set()
        self._schedule_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._init_db()

    def _connect_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def _db(self):
        db = self._connect_db()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init_db(self) -> None:
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    attachment_id TEXT UNIQUE NOT NULL,
                    message_id TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    subject TEXT,
                    mail_date TEXT,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    target_name TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    profile_json TEXT,
                    duplicate_of TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_profiles_sender ON profiles(sender_email)")
            # A prior process may have stopped in the middle of model inference.
            db.execute("UPDATE profiles SET status='pending' WHERE status='processing'")

    def _target_for(self, sender: str) -> str:
        name = self.sender_name_map.get(sender.lower(), self.default_target)
        if not name:
            raise ValueError(
                f"카카오톡 대상 이름이 없습니다 (sender={sender}). "
                "KAKAO_TARGET_NAME 또는 KAKAO_SENDER_NAME_MAP을 설정하세요."
            )
        return name

    def _register_attachment(self, key: str, meta: dict[str, Any]) -> str:
        with self._db() as db:
            db.execute("""
                INSERT OR IGNORE INTO profiles (
                    id, attachment_id, message_id, sender_email, subject,
                    mail_date, filename, file_path, sha256, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                uuid.uuid4().hex, key, str(meta["message_id"]),
                str(meta.get("sender", "")).lower(), str(meta.get("subject", "")),
                str(meta.get("date", "")), str(meta.get("original_filename", "")),
                str(meta["saved_path"]), str(meta["sha256"]),
            ))
            row = db.execute(
                "SELECT id FROM profiles WHERE attachment_id=?", (key,)
            ).fetchone()
            return str(row["id"])

    def _enqueue(self, profile_id: str) -> bool:
        with self._schedule_lock:
            if profile_id in self._scheduled:
                return False
            with self._db() as db:
                row = db.execute(
                    "SELECT status FROM profiles WHERE id=?", (profile_id,)
                ).fetchone()
            if row is None or row["status"] != "pending":
                return False
            self._scheduled.add(profile_id)
            self._queue.put(profile_id)
            return True

    def poll_once(self) -> int:
        """Fetch Gmail TXT attachments and enqueue each unprocessed attachment."""
        with self._gmail_lock:
            # txt_from_gmail() persists one metadata entry per attachment.
            # Its combined string return is deliberately not used here.
            self.gmail.txt_from_gmail()
            items = list(self.gmail.index.get("attachments", {}).items())

        for key, meta in items:
            self._register_attachment(key, meta)

        with self._db() as db:
            pending = db.execute(
                "SELECT id FROM profiles WHERE status='pending' ORDER BY created_at, id"
            ).fetchall()
        queued = sum(self._enqueue(row["id"]) for row in pending)
        if queued:
            print(f"[Gmail] 분석 대기열에 {queued}개 추가", flush=True)
        return queued

    def save_to_db(self, profile_id: str, analysis: dict[str, Any]) -> str:
        """Save a JSON analysis under its stable UUID; return that UUID."""
        encoded = json.dumps(analysis, ensure_ascii=False, separators=(",", ":"))
        with self._db() as db:
            cur = db.execute("""
                UPDATE profiles
                   SET profile_json=?, status='done', error=NULL,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
            """, (encoded, profile_id))
            if cur.rowcount != 1:
                raise KeyError(f"존재하지 않는 프로파일 ID: {profile_id}")
        return profile_id

    def get_by_id(self, profile_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["analysis"] = json.loads(result.pop("profile_json")) if result["profile_json"] else None
        return result

    def list_profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("""
                SELECT id, sender_email, filename, target_name, status,
                       duplicate_of, error, created_at, updated_at
                  FROM profiles ORDER BY created_at DESC, id DESC LIMIT ?
            """, (min(max(limit, 1), 500),)).fetchall()
        return [dict(row) for row in rows]

    def retry(self, profile_id: str) -> bool:
        """Requeue a failed analysis (for example after fixing the KakaoTalk name)."""
        with self._db() as db:
            cur = db.execute("""
                UPDATE profiles SET status='pending', error=NULL,
                                    updated_at=CURRENT_TIMESTAMP
                 WHERE id=? AND status='error'
            """, (profile_id,))
        return bool(cur.rowcount) and self._enqueue(profile_id)

    def _analyze_one(self, profile_id: str) -> None:
        with self._db() as db:
            cur = db.execute("""
                UPDATE profiles SET status='processing', updated_at=CURRENT_TIMESTAMP
                 WHERE id=? AND status='pending'
            """, (profile_id,))
            if cur.rowcount != 1:
                return
            row = dict(db.execute(
                "SELECT * FROM profiles WHERE id=?", (profile_id,)
            ).fetchone())

        try:
            sender = row["sender_email"]
            name = self._target_for(sender)
            path = Path(row["file_path"]).resolve()
            if not path.is_relative_to(self.gmail.storage.resolve()):
                raise ValueError("허용되지 않은 TXT 파일 경로입니다.")

            raw = path.read_bytes()
            if len(raw) > int(os.getenv("SEATCOM_MAX_TXT_BYTES", "20971520")):
                raise ValueError("TXT가 최대 허용 크기를 초과했습니다.")
            source: str = self.gmail._decode(raw)
            if not source.strip():
                raise ValueError("TXT 파일이 비어 있습니다.")

            with self._db() as db:
                db.execute("""
                    UPDATE profiles SET target_name=?, updated_at=CURRENT_TIMESTAMP
                     WHERE id=?
                """, (name, profile_id))
                prior = db.execute("""
                    SELECT id, profile_json FROM profiles
                     WHERE sender_email=? AND sha256=? AND target_name=?
                       AND status='done' AND id<>?
                     LIMIT 1
                """, (sender, row["sha256"], name, profile_id)).fetchone()

            if prior and prior["profile_json"]:
                with self._db() as db:
                    db.execute("""
                        UPDATE profiles SET status='done', profile_json=?,
                                            duplicate_of=?, error=NULL,
                                            updated_at=CURRENT_TIMESTAMP
                         WHERE id=?
                    """, (prior["profile_json"], prior["id"], profile_id))
                print(f"[중복 분석 재사용] id={profile_id}, 원본={prior['id']}", flush=True)
                return

            # This is the existing KNPU API method (its internal chunk calls are parallel).
            analysis = self.analyzer.analyze_by_GPT_OSS_API(
                source=source,
                name=name,
            )
            self.save_to_db(profile_id, analysis)
            print(json.dumps({
                "id": profile_id,
                "sender_email": sender,
                "filename": row["filename"],
                "analysis": analysis,
            }, ensure_ascii=False, indent=2), flush=True)

        except Exception as exc:
            with self._db() as db:
                db.execute("""
                    UPDATE profiles SET status='error', error=?,
                                        updated_at=CURRENT_TIMESTAMP
                     WHERE id=?
                """, (f"{type(exc).__name__}: {exc}", profile_id))
            print(f"[분석 실패] id={profile_id}: {type(exc).__name__}: {exc}", flush=True)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                profile_id = self._queue.get(timeout=1.0)
            except Empty:
                continue
            try:
                self._analyze_one(profile_id)
            finally:
                with self._schedule_lock:
                    self._scheduled.discard(profile_id)
                self._queue.task_done()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                print(f"[Gmail 조회 오류] {type(exc).__name__}: {exc}", flush=True)
            self._stop_event.wait(self.gmail.poll_seconds)

    def start(self) -> None:
        if self._threads:
            return
        self._stop_event.clear()
        self._threads = [
            threading.Thread(target=self._poll_loop, name="gmail-poller", daemon=True),
            threading.Thread(target=self._worker_loop, name="seatcom-analyzer", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        print("[SeatCom] Gmail 감시 및 KNPU 분석 시작", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads = []

    def listen(self) -> None:
        self.start()
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1)
        except KeyboardInterrupt:
            print("\n[SeatCom] 종료 요청", flush=True)
        finally:
            self.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = SeatComPipeline()
    app.state.pipeline.start()
    try:
        yield
    finally:
        app.state.pipeline.stop()


app = FastAPI(title="SeatCom Gmail Analysis API", lifespan=lifespan)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("SEATCOM_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="SEATCOM_API_KEY 설정이 필요합니다.")
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/profiles", dependencies=[Depends(require_api_key)])
def get_profiles(limit: int = 100):
    return app.state.pipeline.list_profiles(limit)


@app.get("/profiles/{profile_id}", dependencies=[Depends(require_api_key)])
def get_profile(profile_id: str):
    record = app.state.pipeline.get_by_id(profile_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return record


@app.post("/scan", dependencies=[Depends(require_api_key)])
def scan_now():
    return {"queued": app.state.pipeline.poll_once()}


@app.post("/profiles/{profile_id}/retry", dependencies=[Depends(require_api_key)])
def retry_profile(profile_id: str):
    if not app.state.pipeline.retry(profile_id):
        raise HTTPException(status_code=409, detail="Only error profiles can be retried")
    return {"id": profile_id, "status": "pending"}


if __name__ == "__main__":
    SeatComPipeline().listen()
