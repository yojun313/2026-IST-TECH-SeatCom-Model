from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS persona_answers (
 user_id TEXT NOT NULL, question_id TEXT NOT NULL, answer TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(user_id, question_id)
);
CREATE TABLE IF NOT EXISTS persona_results (
 analysis_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 answers_json TEXT NOT NULL, completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_persona_user ON persona_results(user_id, completed_at DESC);
CREATE TABLE IF NOT EXISTS lab_sessions (
 upload_id TEXT PRIMARY KEY, upload_code TEXT NOT NULL UNIQUE,
 user_id TEXT NOT NULL, target_name TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 sender_email TEXT, filename TEXT, file_id TEXT, analysis_id TEXT,  -- sender_email·filename·file_id: 예전 메일 방식 열(지금은 항상 NULL)
 verification_request_id TEXT, verification TEXT, email_received INTEGER NOT NULL DEFAULT 0,  -- email_received: 파일 수신 여부
 error_code TEXT, error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 last_attempt_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_owner ON lab_sessions(user_id);
CREATE TABLE IF NOT EXISTS lab_results (
 analysis_id TEXT PRIMARY KEY, upload_id TEXT NOT NULL UNIQUE,
 user_id TEXT NOT NULL, result_json TEXT NOT NULL, completed_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def row(db, sql, args=()):
        r = db.execute(sql, args).fetchone()
        return dict(r) if r is not None else None
