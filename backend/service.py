from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
import time
import uuid
from openai import APITimeoutError, APIConnectionError, APIStatusError
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .config import Settings
from .models import SessionIn
from .questions import IDS, ALLOWED
from .security import api_error
from .store import Store, now_iso

log = logging.getLogger(__name__)


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


def to_dict(row):
    return dict(row) if row is not None else None


def decode_txt(raw) -> str:
    if not raw or b"\x00" in raw:
        api_error(422, "INVALID_TXT", "비어 있거나 올바르지 않은 TXT 파일입니다.")
    for codec in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return raw.decode(codec)
        except UnicodeError:
            pass
    api_error(422, "INVALID_TXT", "지원하지 않는 TXT 인코딩입니다.")


class SeatComService:
    def __init__(self, settings: Settings, *, analyzer=None):
        self.settings = settings
        self.store = Store(settings.database_path)
        self.analyzer = analyzer
        self._analyzer_lock = threading.Lock()
        # 카카오톡 대화 원문: 이 dict(프로세스 메모리)에만 있다. 디스크·DB·로그에 쓰지 않는다.
        self._jobs = {}  # upload_id -> {'text','target_name','user_id'}
        self._jobs_lock = threading.Lock()
        self._uploads = defaultdict(
            deque
        )  # user_id -> 최근 업로드 시각(monotonic), 시간당 제한용
        self._executor = None

    def persona_answers(self, user_id):
        with self.store.db() as db:
            data = {
                r["question_id"]: r["answer"]
                for r in db.execute(
                    "SELECT question_id,answer FROM persona_answers WHERE user_id=?",
                    (user_id,),
                )
            }
        answers = {q: data.get(q) for q in IDS}
        count = sum(v is not None for v in answers.values())
        return {
            "answers": answers,
            "answered_count": count,
            "required_count": 4,
            "can_analyze": count == 4,
        }

    def put_answer(self, user_id, qid, value):
        if qid not in ALLOWED:
            api_error(404, "NOT_FOUND", "해당 질문이 없습니다.")
        if value not in ALLOWED[qid]:
            api_error(
                422,
                "INVALID_ANSWER",
                "허용되지 않은 답변입니다.",
                question_id=qid,
                allowed_values=sorted(ALLOWED[qid]),
            )
        now = now_iso()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO persona_answers(user_id,question_id,answer,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,question_id) DO UPDATE SET answer=excluded.answer,updated_at=excluded.updated_at",
                (user_id, qid, value, now),
            )
            # Changing answers invalidates previous recommendation snapshot.
            db.execute("DELETE FROM persona_results WHERE user_id=?", (user_id,))
            db.commit()
        state = self.persona_answers(user_id)
        return {
            "question_id": qid,
            "answer": value,
            "saved": True,
            "answered_count": state["answered_count"],
            "required_count": 4,
            "can_analyze": state["can_analyze"],
            "updated_at": now,
        }

    def analyze_persona(self, user_id):
        data = self.persona_answers(user_id)
        if not data["can_analyze"]:
            api_error(
                422,
                "INCOMPLETE_ANSWERS",
                "모든 질문에 답변해야 합니다.",
                missing_questions=[k for k, v in data["answers"].items() if v is None],
            )
        analysis_id = new_id("prs")
        when = now_iso()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = {
                r["question_id"]: r["answer"]
                for r in db.execute(
                    "SELECT question_id,answer FROM persona_answers WHERE user_id=?",
                    (user_id,),
                )
            }
            if set(rows) != set(IDS):
                db.rollback()
                api_error(
                    409,
                    "ANSWERS_CHANGED",
                    "설문 응답이 변경되었습니다. 다시 분석해 주세요.",
                )
            answers = {k: rows[k] for k in IDS}
            db.execute(
                "INSERT INTO persona_results VALUES(?,?,?,?)",
                (analysis_id, user_id, json.dumps(answers, ensure_ascii=False), when),
            )
            db.commit()
        return self._persona_response(analysis_id, answers, when)

    @staticmethod
    def _persona_response(analysis_id, answers, when):
        return {
            "analysis_id": analysis_id,
            "analysis_method": "persona",
            "status": "completed",
            "seat_preferences": answers,
            "source": "self_report",
            "completed_at": when,
        }

    def get_persona_result(self, user_id):
        with self.store.db() as db:
            row = self.store.row(
                db,
                "SELECT * FROM persona_results WHERE user_id=? ORDER BY completed_at DESC LIMIT 1",
                (user_id,),
            )
        if not row:
            api_error(404, "PERSONA_NOT_FOUND", "완료된 페르소나 분석 결과가 없습니다.")
        return self._persona_response(
            row["analysis_id"], json.loads(row["answers_json"]), row["completed_at"]
        )

    # ================================================================== 카카오톡 실험실
    # 흐름: 세션 만들기(동의·카톡 이름) → 웹에서 TXT 업로드(raw body) → 메모리에서 분석 → 결과만 저장
    #
    # 보안 원칙
    #  - 대화 원문은 요청 본문을 메모리로 읽어 바로 문자열로 바꾸고, 분석이 끝나면 참조를 버린다.
    #    파일·DB·로그·임시파일 어디에도 쓰지 않는다(multipart 는 큰 파일을 임시파일로 내리므로 쓰지 않는다).
    #  - 파일 이름은 저장하지 않는다(내보낸 파일 이름에 상대방 이름이 들어간다).
    #  - 로그에는 업로드 ID와 예외 종류만 남긴다(예외 메시지에 대화나 이름이 섞일 수 있다).
    #  - 서버가 재시작되면 메모리의 원문은 사라지고, 진행 중이던 세션은 실패(다시 업로드)로 바뀐다.

    LAB_ACTIVE = ("waiting_upload", "analyzing")

    def create_session(self, user_id, body: SessionIn):
        if not body.consent:
            api_error(
                400,
                "CONSENT_REQUIRED",
                "카카오톡 분석을 진행하려면 데이터 처리에 동의해야 합니다.",
            )
        if user_id.startswith("guest-"):
            api_error(
                403,
                "ACCOUNT_REQUIRED",
                "카카오톡 분석은 회원가입한 계정만 쓸 수 있습니다.",
            )
        # Session is explicitly an optional feature after the four-question onboarding.
        self.get_persona_result(user_id)
        uid = new_id("upl")
        created = datetime.now(timezone.utc)
        expires = created + timedelta(minutes=self.settings.upload_expiry_minutes)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            # 한 사용자는 진행 중인 세션을 하나만 갖는다. 이전 대기 세션은 취소한다.
            db.execute(
                "UPDATE lab_sessions SET status='cancelled' WHERE user_id=? AND status='waiting_upload'",
                (user_id,),
            )
            db.execute(
                "INSERT INTO lab_sessions(upload_id,upload_code,user_id,target_name,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (
                    uid,
                    "UP-" + secrets.token_hex(8).upper(),
                    user_id,
                    body.target_name,
                    "waiting_upload",
                    created.isoformat(),
                    expires.isoformat(),
                ),
            )
            db.commit()
        return {
            "upload_id": uid,
            "target_name": body.target_name,
            "status": "waiting_upload",
            "upload_url": f"/api/v1/lab/sessions/{uid}/file",
            "max_bytes": self.settings.max_txt_bytes,
            "accepted_content_types": ["text/plain", "application/octet-stream"],
            "expires_at": expires.isoformat(),
            "poll_interval_seconds": 5,
        }

    def _session(self, upload_id):
        with self.store.db() as db:
            row = self.store.row(
                db, "SELECT * FROM lab_sessions WHERE upload_id=?", (upload_id,)
            )
        if not row:
            api_error(404, "NOT_FOUND", "해당 업로드 세션이 없습니다.")
        if row["status"] == "waiting_upload" and datetime.fromisoformat(
            row["expires_at"]
        ) < datetime.now(timezone.utc):
            with self.store.db() as db:
                db.execute(
                    "UPDATE lab_sessions SET status='expired' WHERE upload_id=? AND status='waiting_upload'",
                    (upload_id,),
                )
            row["status"] = "expired"
        return row

    def current_session(self, user_id):
        with self.store.db() as db:
            row = self.store.row(
                db,
                "SELECT upload_id FROM lab_sessions WHERE user_id=? AND status!='cancelled' ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            )
        if not row:
            return None
        session = self.owned(row["upload_id"], user_id)
        status = self.status(session["upload_id"], user_id)
        return {
            "upload_id": session["upload_id"],
            "target_name": session["target_name"],
            "status": status["status"],
            "last_status": status["status"],
            "analysis_id": status["analysis"]["analysis_id"],
            "upload_url": f"/api/v1/lab/sessions/{session['upload_id']}/file",
            "max_bytes": self.settings.max_txt_bytes,
            "expires_at": session["expires_at"],
            "poll_interval_seconds": 5,
        }

    def owned(self, upload_id, user_id):
        row = self._session(upload_id)
        if row["user_id"] != user_id:
            # 다른 사용자의 세션은 있는지 여부도 알려 주지 않는다
            api_error(404, "NOT_FOUND", "해당 업로드 세션이 없습니다.")
        return row

    def check_upload(self, user_id, upload_id):
        """본문을 읽기 전에 검사: 소유자, 상태, 업로드 횟수, 서버 여유."""
        row = self.owned(upload_id, user_id)
        if row["status"] == "expired":
            api_error(
                410,
                "UPLOAD_EXPIRED",
                "업로드 시간이 지났습니다. 처음부터 다시 시작해 주세요.",
            )
        if row["status"] != "waiting_upload":
            api_error(409, "UPLOAD_ALREADY_USED", "이미 파일을 받은 세션입니다.")
        now = time.monotonic()
        with self._jobs_lock:
            q = self._uploads[user_id]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.settings.lab_uploads_per_hour:
                api_error(
                    429,
                    "TOO_MANY_UPLOADS",
                    "업로드가 너무 잦습니다. 잠시 뒤에 다시 해 주세요.",
                )
            if len(self._jobs) >= self.settings.lab_max_pending:
                api_error(
                    503,
                    "LAB_BUSY",
                    "지금은 분석 요청이 많습니다. 잠시 뒤에 다시 해 주세요.",
                )
        return row

    def accept_upload(self, user_id, upload_id, raw: bytearray):
        """메모리의 TXT → 형식·대상 이름 확인 → 분석 작업 등록. raw 는 확인 후 0으로 덮어쓴다."""
        try:
            if len(raw) > self.settings.max_txt_bytes:
                api_error(413, "FILE_TOO_LARGE", "TXT 파일 크기 제한을 초과했습니다.")
            text = decode_txt(raw)
        finally:
            raw[:] = bytes(
                len(raw)
            )  # 바이트 버퍼는 지울 수 있다(파이썬 문자열은 지울 수 없어 참조만 버린다)
        row = self.check_upload(user_id, upload_id)
        from kakao_analysis.extractor import NameExtractor

        try:
            records = NameExtractor().extract_context_records(text, row["target_name"])
        except Exception:
            records = []
        if not any(r.get("kind") == "message" for r in records):
            api_error(
                422,
                "INVALID_KAKAO_TXT",
                "카카오톡에서 내보낸 대화 TXT 파일이 아닌 것 같습니다.",
            )
        if not any(r.get("is_target") for r in records):
            api_error(
                422,
                "TARGET_NOT_FOUND",
                "입력한 이름의 메시지를 대화에서 찾지 못했습니다. 카톡에 보이는 내 이름을 확인해 주세요.",
            )
        del records
        with self._jobs_lock:
            if (
                upload_id in self._jobs
                or len(self._jobs) >= self.settings.lab_max_pending
            ):
                api_error(
                    503,
                    "LAB_BUSY",
                    "지금은 분석 요청이 많습니다. 잠시 뒤에 다시 해 주세요.",
                )
            with self.store.db() as db:
                cur = db.execute(
                    "UPDATE lab_sessions SET status='analyzing',email_received=1,attempts=attempts+1,last_attempt_at=?,analysis_id=? WHERE upload_id=? AND user_id=? AND status='waiting_upload'",
                    (now_iso(), new_id("lab"), upload_id, user_id),
                )
            if not cur.rowcount:
                api_error(409, "UPLOAD_ALREADY_USED", "이미 파일을 받은 세션입니다.")
            self._jobs[upload_id] = {
                "text": text,
                "target_name": row["target_name"],
                "user_id": user_id,
            }
            self._uploads[user_id].append(time.monotonic())
        del text
        self._pool().submit(self._run_job, upload_id)
        return self.status(upload_id, user_id)

    def _pool(self):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.settings.lab_max_concurrent,
                thread_name_prefix="SeatComLab",
            )
        return self._executor

    def _analyzer(self):
        with self._analyzer_lock:
            if self.analyzer is None:
                # Reuse uploaded original analysis method, no reimplementation of inference.
                from kakao_analysis.api import PersonalityAnalysis

                self.analyzer = PersonalityAnalysis()
            return self.analyzer

    def _run_job(self, upload_id):
        with self._jobs_lock:
            job = self._jobs.get(upload_id)
        if job is None:
            return
        try:
            # 분석기에 문자열을 넘기면 파일이 아니라 대화 내용으로 다룬다(kakao_analysis.extractor._load)
            result = self._analyzer().analyze_by_GPT_OSS_API(
                job["text"], job["target_name"]
            )
            if not isinstance(result, dict):
                raise RuntimeError("AI result must be JSON object")
            when = now_iso()
            with self.store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                cur = self.store.row(
                    db,
                    "SELECT status,analysis_id,user_id FROM lab_sessions WHERE upload_id=?",
                    (upload_id,),
                )
                # 분석 중 취소·탈퇴했으면 결과도 남기지 않는다
                if (
                    cur
                    and cur["status"] == "analyzing"
                    and cur["user_id"] == job["user_id"]
                ):
                    db.execute(
                        "INSERT OR REPLACE INTO lab_results(analysis_id,upload_id,user_id,result_json,completed_at) VALUES(?,?,?,?,?)",
                        (
                            cur["analysis_id"],
                            upload_id,
                            job["user_id"],
                            json.dumps(result, ensure_ascii=False),
                            when,
                        ),
                    )
                    db.execute(
                        "UPDATE lab_sessions SET status='completed' WHERE upload_id=?",
                        (upload_id,),
                    )
                db.commit()
        except Exception as e:
            # 예외 메시지·traceback 에 대화나 이름이 섞일 수 있어 종류만 남긴다
            log.warning(
                "Lab analysis failed for upload %s: %s", upload_id, type(e).__name__
            )
            timed_out = isinstance(e, (TimeoutError, APITimeoutError))
            code = "AI_ANALYSIS_TIMEOUT" if timed_out else "AI_ANALYSIS_FAILED"
            message = (
                "AI 서버 응답이 늦어 분석을 중단했습니다. 잠시 후 다시 시도해 주세요."
                if timed_out
                else "AI 분석 중 오류가 발생했습니다. 다시 시도해 주세요."
            )
            if getattr(e, "code", None) in ("AI_EMPTY_RESPONSE", "AI_RESPONSE_INVALID"):
                code = e.code
                message = "AI 응답 형식을 확인하지 못했습니다. 파일을 다시 올려 분석해 주세요."
            elif isinstance(e, (APIConnectionError, APIStatusError)) and not timed_out:
                code = "AI_SERVICE_UNAVAILABLE"
                message = "AI 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."
            with self.store.db() as db:
                db.execute(
                    "UPDATE lab_sessions SET status='failed',error_code=?,error_message=? WHERE upload_id=? AND status='analyzing'",
                    (code, message, upload_id),
                )
        finally:
            with self._jobs_lock:
                self._jobs.pop(upload_id, None)
            job.clear()
            job = None

    def drop_user_jobs(self, user_id):
        """탈퇴 등: 그 사용자의 메모리 원문을 즉시 버린다(진행 중 분석은 결과를 저장하지 않는다)."""
        with self._jobs_lock:
            for k in [k for k, v in self._jobs.items() if v["user_id"] == user_id]:
                self._jobs.pop(k, None)
            self._uploads.pop(user_id, None)

    def wait_idle(self, timeout=10):
        """테스트용: 메모리 작업이 모두 끝날 때까지 기다린다."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._jobs_lock:
                if not self._jobs:
                    return True
            time.sleep(0.02)
        return False

    def status(self, uid, user_id):
        s = self.owned(uid, user_id)
        sta = s["status"]
        analysis_status = {
            "analyzing": "processing",
            "completed": "completed",
            "failed": "failed",
        }.get(sta, "not_started")
        result = {
            "upload_id": uid,
            "status": sta,
            "file_received": bool(s["email_received"]),
            "analysis": {
                "analysis_id": s["analysis_id"]
                if sta in ("analyzing", "completed")
                else None,
                "status": analysis_status,
            },
        }
        if sta == "analyzing":
            result["analysis"]["model"] = os.getenv("OSS_MODEL", "openai/gpt-oss-120b")
        # 화면을 나갔다 와도 경과 시간을 이어서 보이도록 분석 시작 시각을 준다
        if sta == "analyzing":
            result["analysis"]["started_at"] = s["last_attempt_at"]
        messages = {
            "waiting_upload": "카카오톡 대화 TXT 파일을 올려 주세요.",
            "analyzing": "최근 대화를 여러 구간으로 분석해 근거를 종합하고 있습니다. 약 1분을 목표로 하며, 지연되어도 분석은 계속됩니다. 대화 원문은 서버에 저장하지 않습니다.",
            "completed": "카카오톡 AI 분석이 완료되었습니다. 대화 원문은 남기지 않았습니다.",
            "expired": "업로드 세션이 만료되었습니다.",
            "cancelled": "분석이 취소되었습니다.",
        }
        if sta == "failed":
            result["error"] = {
                "code": s["error_code"] or "PROCESS_FAILED",
                "message": s["error_message"] or "처리에 실패했습니다.",
            }
        else:
            result["message"] = messages.get(sta, "")
        return result

    def analysis_result(self, user_id, analysis_id):
        with self.store.db() as db:
            row = self.store.row(
                db, "SELECT * FROM lab_results WHERE analysis_id=?", (analysis_id,)
            )
            if not row:
                s = self.store.row(
                    db,
                    "SELECT user_id,status FROM lab_sessions WHERE analysis_id=?",
                    (analysis_id,),
                )
                if s and s["user_id"] == user_id and s["status"] == "analyzing":
                    api_error(
                        409, "ANALYSIS_NOT_READY", "분석이 아직 완료되지 않았습니다."
                    )
                api_error(404, "NOT_FOUND", "해당 분석 결과가 없습니다.")
        if row["user_id"] != user_id:
            api_error(404, "NOT_FOUND", "해당 분석 결과가 없습니다.")
        return {
            "analysis_id": analysis_id,
            "analysis_method": "kakao_lab",
            "status": "completed",
            "source": "kakao_conversation",
            "analysis": json.loads(row["result_json"]),
            "completed_at": row["completed_at"],
        }

    def delete_session(self, user_id, upload_id):
        self.owned(upload_id, user_id)
        with self._jobs_lock:
            self._jobs.pop(upload_id, None)  # 분석 대기 중이면 원문을 바로 버린다
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM lab_results WHERE upload_id=?", (upload_id,))
            db.execute(
                "DELETE FROM lab_sessions WHERE upload_id=? AND user_id=?",
                (upload_id, user_id),
            )
            db.commit()
        return {"deleted": True, "upload_id": upload_id}

    def purge_legacy_raw(self):
        """예전 메일 수집 방식이 남긴 대화 원문 파일·메타데이터를 지운다. 진행 중이던 세션은 재시작으로 원문이 사라졌으므로 실패 처리한다."""
        d = self.settings.attachment_dir
        if d.is_dir():
            for f in d.iterdir():
                try:
                    if f.is_file() or f.is_symlink():
                        f.unlink()
                    else:
                        shutil.rmtree(f)
                except OSError:
                    log.warning("Could not remove legacy lab file %s", f.name)
            try:
                d.rmdir()
            except OSError:
                pass
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for t in ("attachments", "ingested_events"):
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
                ).fetchone():
                    db.execute(f"DELETE FROM {t}")
            db.execute(
                "UPDATE lab_sessions SET filename=NULL,sender_email=NULL,file_id=NULL"
            )
            db.execute("""UPDATE lab_sessions SET status='failed',error_code='UPLOAD_LOST',
                          error_message='서버가 다시 시작되어 분석을 마치지 못했습니다. 파일을 다시 올려 주세요.'
                          WHERE status IN ('analyzing','verified','verifying_identity')""")
            db.execute(
                "UPDATE lab_sessions SET status='expired' WHERE status='waiting_email'"
            )
            db.commit()

    def start(self):
        # 프로세스가 죽어도 메모리(대화 원문)가 코어 덤프 파일로 디스크에 남지 않게 한다
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ImportError, ValueError, OSError):
            log.warning("Could not disable core dumps")
        self.purge_legacy_raw()

    def stop(self):
        with self._jobs_lock:
            self._jobs.clear()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
