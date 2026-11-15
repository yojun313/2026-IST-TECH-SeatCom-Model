from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


from .config import Settings
from .models import AttachmentIn, EmailEventIn, SessionIn, ApprovalIn
from .questions import IDS, ALLOWED
from .security import api_error
from .store import Store, now_iso
from .auth import LocalIdentityVerifier

log = logging.getLogger(__name__)


def new_id(prefix):
    return prefix + '_' + uuid.uuid4().hex


def to_dict(row):
    return dict(row) if row is not None else None


def decode_txt(raw: bytes) -> str:
    if not raw or b'\x00' in raw:
        api_error(422,'INVALID_TXT','비어 있거나 올바르지 않은 TXT 파일입니다.')
    for codec in ('utf-8-sig','cp949','utf-16'):
        try:
            return raw.decode(codec)
        except UnicodeError:
            pass
    api_error(422,'INVALID_TXT','지원하지 않는 TXT 인코딩입니다.')


class SeatComService:
    def __init__(self, settings: Settings, *, analyzer=None, identity_client=None):
        self.settings=settings
        self.store=Store(settings.database_path)
        self.identity=identity_client or LocalIdentityVerifier(self.store)   # 본인 확인은 백엔드 내장
        self.analyzer=analyzer
        settings.attachment_dir.mkdir(parents=True,exist_ok=True)
        try: settings.attachment_dir.chmod(0o700)
        except OSError: pass
        self._stop=threading.Event()
        self._thread=None
        self._gmail_thread=None
        self._active_lock=threading.Lock()
        self._active_analyses=set()

    def persona_answers(self, user_id):
        with self.store.db() as db:
            data={r['question_id']:r['answer'] for r in db.execute('SELECT question_id,answer FROM persona_answers WHERE user_id=?',(user_id,))}
        answers={q:data.get(q) for q in IDS}
        count=sum(v is not None for v in answers.values())
        return {'answers':answers,'answered_count':count,'required_count':4,'can_analyze':count==4}

    def put_answer(self,user_id,qid,value):
        if qid not in ALLOWED:
            api_error(404,'NOT_FOUND','해당 질문이 없습니다.')
        if value not in ALLOWED[qid]:
            api_error(422,'INVALID_ANSWER','허용되지 않은 답변입니다.',question_id=qid,allowed_values=sorted(ALLOWED[qid]))
        now=now_iso()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO persona_answers(user_id,question_id,answer,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,question_id) DO UPDATE SET answer=excluded.answer,updated_at=excluded.updated_at',(user_id,qid,value,now))
            # Changing answers invalidates previous recommendation snapshot.
            db.execute('DELETE FROM persona_results WHERE user_id=?',(user_id,))
            db.commit()
        state=self.persona_answers(user_id)
        return {'question_id':qid,'answer':value,'saved':True,'answered_count':state['answered_count'],'required_count':4,'can_analyze':state['can_analyze'],'updated_at':now}

    def analyze_persona(self,user_id):
        data=self.persona_answers(user_id)
        if not data['can_analyze']:
            api_error(422,'INCOMPLETE_ANSWERS','모든 질문에 답변해야 합니다.',missing_questions=[k for k,v in data['answers'].items() if v is None])
        analysis_id=new_id('prs'); when=now_iso()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows={r['question_id']:r['answer'] for r in db.execute('SELECT question_id,answer FROM persona_answers WHERE user_id=?',(user_id,))}
            if set(rows) != set(IDS):
                db.rollback(); api_error(409,'ANSWERS_CHANGED','설문 응답이 변경되었습니다. 다시 분석해 주세요.')
            answers={k:rows[k] for k in IDS}
            db.execute('INSERT INTO persona_results VALUES(?,?,?,?)',(analysis_id,user_id,json.dumps(answers,ensure_ascii=False),when))
            db.commit()
        return self._persona_response(analysis_id,answers,when)

    @staticmethod
    def _persona_response(analysis_id, answers, when):
        return {'analysis_id':analysis_id,'analysis_method':'persona','status':'completed',
                'seat_preferences':answers,'source':'self_report','completed_at':when}

    def get_persona_result(self,user_id):
        with self.store.db() as db:
            row=self.store.row(db,'SELECT * FROM persona_results WHERE user_id=? ORDER BY completed_at DESC LIMIT 1',(user_id,))
        if not row: api_error(404,'PERSONA_NOT_FOUND','완료된 페르소나 분석 결과가 없습니다.')
        return self._persona_response(row['analysis_id'],json.loads(row['answers_json']),row['completed_at'])

    def create_session(self,user_id,body: SessionIn):
        if not body.consent:
            api_error(400,'CONSENT_REQUIRED','카카오톡 분석을 진행하려면 데이터 처리에 동의해야 합니다.')
        # Session is explicitly an optional feature after the four-question onboarding.
        self.get_persona_result(user_id)
        code='SC-'+secrets.token_hex(8).upper()
        uid=new_id('upl')
        created=datetime.now(timezone.utc)
        expires=created+timedelta(minutes=self.settings.upload_expiry_minutes)
        with self.store.db() as db:
            db.execute('INSERT INTO lab_sessions(upload_id,upload_code,user_id,target_name,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?)',
                (uid,code,user_id,body.target_name,'waiting_email',created.isoformat(),expires.isoformat()))
        return {'upload_id':uid,'upload_code':code,'recipient_email':self.settings.recipient_email,
                'mail_subject':f'SeatCom {code}','target_name':body.target_name,'status':'waiting_email',
                'expires_at':expires.isoformat(),'poll_interval_seconds':5}

    def _session(self,upload_id):
        with self.store.db() as db:
            row=self.store.row(db,'SELECT * FROM lab_sessions WHERE upload_id=?',(upload_id,))
        if not row: api_error(404,'NOT_FOUND','해당 업로드 세션이 없습니다.')
        if row['status']=='waiting_email' and datetime.fromisoformat(row['expires_at']) < datetime.now(timezone.utc):
            with self.store.db() as db:
                db.execute("UPDATE lab_sessions SET status='expired' WHERE upload_id=? AND status='waiting_email'",(upload_id,))
            row['status']='expired'
        return row

    def owned(self,upload_id,user_id):
        row=self._session(upload_id)
        if row['user_id'] != user_id:
            api_error(403,'FORBIDDEN','다른 사용자의 세션에 접근할 수 없습니다.')
        return row

    def store_attachment(self, body: AttachmentIn):
        if not body.filename.lower().endswith('.txt'):
            api_error(422,'INVALID_TXT','TXT 형식만 허용합니다.')
        if len(body.content_base64) > ((self.settings.max_txt_bytes + 2)//3*4 + 16):
            api_error(413,'FILE_TOO_LARGE','TXT 파일 크기 제한을 초과했습니다.')
        try:
            data=base64.b64decode(body.content_base64,validate=True)
        except (ValueError,binascii.Error):
            api_error(422,'INVALID_TXT','Base64 데이터가 올바르지 않습니다.')
        if len(data)>self.settings.max_txt_bytes:
            api_error(413,'FILE_TOO_LARGE','TXT 파일 크기 제한을 초과했습니다.')
        decode_txt(data)
        with self.store.db() as db:
            old=self.store.row(db,'SELECT file_id FROM attachments WHERE message_id=? AND attachment_id=?',(body.message_id,body.attachment_id))
        if old: return {'file_id':old['file_id'],'stored':True,'duplicate':True}
        fid=new_id('file'); path=self.settings.attachment_dir / f'{fid}.txt'
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            with os.fdopen(fd,'wb') as f:f.write(data)
            with self.store.db() as db:
                db.execute('INSERT INTO attachments(file_id,message_id,attachment_id,filename,sha256,path,byte_size,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (fid,body.message_id,body.attachment_id,body.filename,hashlib.sha256(data).hexdigest(),str(path),len(data),now_iso()))
        except sqlite3.IntegrityError:
            path.unlink(missing_ok=True)
            with self.store.db() as db:
                old=self.store.row(db,'SELECT file_id FROM attachments WHERE message_id=? AND attachment_id=?',(body.message_id,body.attachment_id))
            return {'file_id':old['file_id'],'stored':True,'duplicate':True}
        except BaseException:
            path.unlink(missing_ok=True);raise
        return {'file_id':fid,'stored':True,'duplicate':False}

    def ingest_email(self,event: EmailEventIn):
        # Backend-generated session code (not a sender name) binds the message to the request.
        session=self._session(event.upload_id)
        if session['upload_code']!=event.upload_code or event.email.subject != f'SeatCom {session["upload_code"]}':
            api_error(409,'INVALID_UPLOAD_CODE','이메일 제목 또는 코드가 업로드 세션과 일치하지 않습니다.')
        if session['status']=='expired':
            api_error(410,'UPLOAD_EXPIRED','업로드 세션이 만료되었습니다.')
        with self.store.db() as db:
            att=self.store.row(db,'SELECT * FROM attachments WHERE file_id=?',(event.attachment.file_id,))
        if not att or att['message_id']!=event.email.message_id:
            api_error(422,'ATTACHMENT_NOT_STAGED','메타데이터에 대응하는 저장된 TXT 파일이 없습니다.')
        if att['filename']!=event.attachment.filename:
            api_error(422,'INVALID_ATTACHMENT','파일명과 등록된 첨부파일이 일치하지 않습니다.')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',event.sender.email.strip()):
            api_error(422,'INVALID_EMAIL','이메일 주소가 올바르지 않습니다.')
        if datetime.fromisoformat(session['created_at']) > event.email.received_at.astimezone(timezone.utc) + timedelta(minutes=2):
            api_error(409,'MESSAGE_TOO_OLD','업로드 세션 생성 이전의 메일입니다.')
        if event.email.received_at.astimezone(timezone.utc) > datetime.fromisoformat(session['expires_at']):
            api_error(410,'UPLOAD_EXPIRED','만료된 업로드 세션으로 전송된 메일입니다.')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=self.store.row(db,'SELECT * FROM ingested_events WHERE event_id=? OR (message_id=? AND file_id=?)',(event.event_id,event.email.message_id,event.attachment.file_id))
            if existing:
                db.commit()
                if existing['upload_id']!=event.upload_id:api_error(409,'UPLOAD_ALREADY_USED','이미 다른 세션에 사용된 첨부파일입니다.')
                return {'success':True,'upload_id':event.upload_id,'email_received':True,'identity_verified':False,
                        'status':self._session(event.upload_id)['status'],'message':'이미 접수된 이메일입니다.'}
            if session['status']!='waiting_email':
                db.rollback();api_error(409,'UPLOAD_ALREADY_USED','이미 처리된 업로드 세션입니다.')
            db.execute('INSERT INTO ingested_events VALUES(?,?,?,?,?)',
                       (event.event_id,event.email.message_id,event.attachment.file_id,event.upload_id,now_iso()))
            db.execute('UPDATE lab_sessions SET status=?, sender_email=?,filename=?,file_id=?,email_received=1 WHERE upload_id=?',
                       ('verifying_identity',event.sender.email.strip().casefold(),event.attachment.filename,event.attachment.file_id,event.upload_id))
            db.commit()
        return {'success':True,'upload_id':event.upload_id,'email_received':True,'identity_verified':False,
                'status':'verifying_identity','message':'이메일 수신이 확인되었습니다. 사용자 정보를 대조합니다.'}

    def verify_one(self, upload_id):
        row=self._session(upload_id)
        if row['status']!='verifying_identity': return
        request_id=row['verification_request_id'] or new_id('ver')
        with self.store.db() as db:
            db.execute("UPDATE lab_sessions SET verification_request_id=? WHERE upload_id=? AND status='verifying_identity'",(request_id,upload_id))
        payload={'verification_request_id':request_id,'user_id':row['user_id'],'upload_id':upload_id,
                 'sender_email':row['sender_email'],'target_name':row['target_name']}
        try:
            resp=self.identity.verify(payload)
        except Exception as e:
            log.warning('Identity verification unavailable for upload %s: %s',upload_id,type(e).__name__)
            with self.store.db() as db:
                db.execute("UPDATE lab_sessions SET status='failed',error_code='IDENTITY_SERVICE_UNAVAILABLE',error_message='본인 확인 중 오류가 발생했습니다.' WHERE upload_id=? AND status='verifying_identity'",(upload_id,))
            self._remove_raw_for_upload(upload_id)
            return
        checks=resp.get('checks',{})
        approved=(resp.get('matched') is True and resp.get('verification')=='approved'
                  and checks.get('email_match') is True and checks.get('name_match') is True)
        with self.store.db() as db:
            if approved:
                db.execute("UPDATE lab_sessions SET status='verified',verification='approved',analysis_id=? WHERE upload_id=? AND status='verifying_identity'",(new_id('lab'),upload_id))
            else:
                db.execute("UPDATE lab_sessions SET status='rejected',verification='rejected',error_code='IDENTITY_MISMATCH',error_message='등록된 사용자 정보와 일치하지 않습니다.' WHERE upload_id=? AND status='verifying_identity'",(upload_id,))
        if not approved:
            self._remove_raw_for_upload(upload_id)

    def _analyzer(self):
        if self.analyzer is None:
            # Reuse uploaded original analysis method, no reimplementation of inference.
            from kakao_analysis.api import PersonalityAnalysis
            self.analyzer=PersonalityAnalysis()
        return self.analyzer

    def run_analysis(self, upload_id):
        with self._active_lock:
            if upload_id in self._active_analyses:return
            self._active_analyses.add(upload_id)
        try:
            row=self._session(upload_id)
            if row['status']=='verified':
                with self.store.db() as db:
                    cur=db.execute("UPDATE lab_sessions SET status='analyzing',attempts=attempts+1,last_attempt_at=? WHERE upload_id=? AND status='verified'",(now_iso(),upload_id))
                    if not cur.rowcount:return
                row=self._session(upload_id)
            if row['status']!='analyzing':return
            with self.store.db() as db:
                att=self.store.row(db,'SELECT path FROM attachments WHERE file_id=?',(row['file_id'],))
            if not att or not Path(att['path']).is_file():
                raise RuntimeError('Attachment missing')
            result=self._analyzer().analyze_by_GPT_OSS_API(att['path'],row['target_name'])
            if not isinstance(result,dict):
                raise RuntimeError('AI result must be JSON object')
            when=now_iso()
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=self.store.row(db,'SELECT status FROM lab_sessions WHERE upload_id=?',(upload_id,))
                if current and current['status']=='analyzing':
                    db.execute('INSERT OR REPLACE INTO lab_results(analysis_id,upload_id,user_id,result_json,completed_at) VALUES(?,?,?,?,?)',
                        (row['analysis_id'],upload_id,row['user_id'],json.dumps(result,ensure_ascii=False),when))
                    db.execute("UPDATE lab_sessions SET status='completed' WHERE upload_id=?",(upload_id,))
                db.commit()
            # Minimize raw chat retention; the extracted profile is separately persisted.
            Path(att['path']).unlink(missing_ok=True)
        except Exception as e:
            log.exception('Analysis failed for upload %s',upload_id)
            with self.store.db() as db:
                db.execute("UPDATE lab_sessions SET status='failed',error_code='AI_ANALYSIS_FAILED',error_message='AI 분석 중 오류가 발생했습니다.' WHERE upload_id=? AND status IN ('verified','analyzing')",(upload_id,))
            self._remove_raw_for_upload(upload_id)
        finally:
            with self._active_lock:self._active_analyses.discard(upload_id)

    def approve_start(self, body: ApprovalIn):
        row=self._session(body.upload_id)
        v=body.identity_verification;a=body.analysis
        if (row['user_id']!=body.user_id or row['verification']!='approved' or
            v.get('matched') is not True or v.get('verification')!='approved' or
            v.get('verification_request_id')!=row['verification_request_id'] or
            a.get('file_id')!=row['file_id'] or a.get('target_name')!=row['target_name'] or
            a.get('analysis_type')!='kakao_chat'):
            api_error(403,'FORBIDDEN','검증 완료된 분석 요청과 일치하지 않습니다.')
        if row['status'] not in ('verified','analyzing','completed'):
            api_error(409,'INVALID_STATE','현재 상태에서 분석을 시작할 수 없습니다.')
        return {'success':True,'upload_id':row['upload_id'],'analysis_id':row['analysis_id'],
                'status':'analyzing' if row['status']!='completed' else 'completed',
                'message':'사용자 정보 대조가 완료되었습니다. AI 분석이 진행됩니다.'}

    def status(self,uid,user_id):
        s=self.owned(uid,user_id)
        sta=s['status']; approved=s['verification']=='approved'; received=bool(s['email_received'])
        analysis_status={ 'verified':'pending','analyzing':'processing','completed':'completed','failed':'failed' if approved else 'not_started' }.get(sta,'not_started')
        identity_verification='approved' if approved else ('rejected' if sta=='rejected' else 'pending' if sta=='verifying_identity' else 'not_started')
        result={'upload_id':uid,'status':sta,'email_received':received,
                'identity':{'matched': True if approved else False if sta=='rejected' else None,
                            'verification':identity_verification},
                'analysis':{'analysis_id':s['analysis_id'],'status':analysis_status}}
        if approved:result['identity']['result']='OK'
        if sta=='rejected':result['identity']['result']='NOT_MATCHED'
        if s['filename']:result['filename']=s['filename']
        if sta=='analyzing':result['analysis']['model']=os.getenv('OSS_MODEL','openai/gpt-oss-120b')
        messages={'waiting_email':'카카오톡 TXT 파일이 첨부된 이메일을 기다리고 있습니다.',
            'verifying_identity':'TXT 파일을 수신했습니다. 사용자 정보를 확인하고 있습니다.',
            'verified':'등록된 사용자 정보가 확인되었습니다. AI 분석을 준비합니다.',
            'analyzing':'사용자 정보 대조가 완료되었습니다. AI가 카카오톡 대화를 분석하고 있습니다.',
            'completed':'카카오톡 AI 분석이 완료되었습니다.',
            'expired':'업로드 세션이 만료되었습니다.','cancelled':'분석이 취소되었습니다.'}
        if sta in ('failed','rejected'):
            result['error']={'code':s['error_code'] or 'PROCESS_FAILED','message':s['error_message'] or '처리에 실패했습니다.'}
        else:result['message']=messages.get(sta,'')
        return result

    def analysis_result(self,user_id,analysis_id):
        with self.store.db() as db:
            row=self.store.row(db,'SELECT * FROM lab_results WHERE analysis_id=?',(analysis_id,))
            if not row:
                s=self.store.row(db,'SELECT user_id,status FROM lab_sessions WHERE analysis_id=?',(analysis_id,))
                if s and s['user_id']!=user_id:api_error(403,'FORBIDDEN','다른 사용자의 분석 결과입니다.')
                if s and s['status'] not in ('completed','cancelled'):
                    api_error(409,'ANALYSIS_NOT_READY','분석이 아직 완료되지 않았습니다.')
                api_error(404,'NOT_FOUND','해당 분석 결과가 없습니다.')
        if row['user_id']!=user_id:api_error(403,'FORBIDDEN','다른 사용자의 분석 결과입니다.')
        return {'analysis_id':analysis_id,'analysis_method':'kakao_lab','status':'completed',
                'source':'kakao_conversation','analysis':json.loads(row['result_json']),
                'completed_at':row['completed_at']}

    def delete_session(self,user_id,upload_id):
        s=self.owned(upload_id,user_id)
        with self.store.db() as db:
            row=self.store.row(db,'SELECT path FROM attachments WHERE file_id=?',(s['file_id'],)) if s['file_id'] else None
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM lab_results WHERE upload_id=?',(upload_id,))
            db.execute('DELETE FROM ingested_events WHERE upload_id=?',(upload_id,))
            db.execute('DELETE FROM lab_sessions WHERE upload_id=? AND user_id=?',(upload_id,user_id))
            db.commit()
        if row:Path(row['path']).unlink(missing_ok=True)
        return {'deleted':True,'upload_id':upload_id}

    def _remove_raw_for_upload(self, upload_id):
        with self.store.db() as db:
            row=self.store.row(db,"SELECT a.path FROM attachments a JOIN lab_sessions s ON s.file_id=a.file_id WHERE s.upload_id=?",(upload_id,))
        if row:
            Path(row['path']).unlink(missing_ok=True)

    def cleanup_unused_files(self):
        """Remove orphaned staged text after one day; never interrupt active processing."""
        deadline=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
        with self.store.db() as db:
            candidates=db.execute("""SELECT a.file_id,a.path FROM attachments a
                LEFT JOIN lab_sessions s ON s.file_id=a.file_id
                WHERE (s.upload_id IS NULL AND a.created_at<?)
                   OR (s.status IN ('completed','rejected','failed','cancelled','expired'))""",(deadline,)).fetchall()
        for item in candidates:
            Path(item['path']).unlink(missing_ok=True)

    def run_once(self):
        self.cleanup_unused_files()
        with self.store.db() as db:
            ver=db.execute("SELECT upload_id FROM lab_sessions WHERE status='verifying_identity' ORDER BY created_at LIMIT 5").fetchall()
        for r in ver:self.verify_one(r['upload_id'])
        with self.store.db() as db:
            ready=db.execute("SELECT upload_id FROM lab_sessions WHERE status='verified' ORDER BY created_at LIMIT 1").fetchall()
        for r in ready:self.run_analysis(r['upload_id'])

    def _work_loop(self):
        while not self._stop.is_set():
            try:self.run_once()
            except Exception:log.exception('Lab worker iteration failed')
            self._stop.wait(self.settings.poll_seconds)

    def _gmail_loop(self):
        from .gmail_adapter import GmailAdapter
        try:adapter=GmailAdapter(self)
        except Exception:
            log.exception('Gmail initialization failed (check credentials and dependencies)')
            return
        while not self._stop.is_set():
            try:adapter.poll_once()
            except Exception:log.exception('Gmail poll failed')
            self._stop.wait(self.settings.gmail_poll_seconds)

    def start(self):
        if self.settings.worker_enabled:
            self._thread=threading.Thread(target=self._work_loop,name='SeatComLabWorker',daemon=True)
            self._thread.start()
        if self.settings.gmail_enabled:
            self._gmail_thread=threading.Thread(target=self._gmail_loop,name='SeatComGmailCollector',daemon=True)
            self._gmail_thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:self._thread.join(timeout=2)
        if self._gmail_thread:self._gmail_thread.join(timeout=2)
