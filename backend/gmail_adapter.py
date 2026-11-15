"""Pulls only email associated with a live opt-in upload session.

Reuses the user's original TxtFromGmail helper methods, but intentionally does NOT
call txt_from_gmail(): that legacy method saves every TXT in the inbox and prints
full chat content. This adapter checks the session-specific subject before decode.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from email.utils import parseaddr

from .models import AttachmentIn, EmailEventIn

log=logging.getLogger(__name__)

class GmailAdapter:
    def __init__(self, service):
        from kakao_analysis.get_gmail_text_api.txt_from_gmail import TxtFromGmail
        self.service=service
        self.collector=TxtFromGmail(
            email=service.settings.recipient_email,
            poll_seconds=service.settings.gmail_poll_seconds,
            directory=service.settings.mail_directory,
        )

    def poll_once(self):
        with self.service.store.db() as db:
            waiting={f'SeatCom {r["upload_code"]}': dict(r) for r in db.execute(
                "SELECT upload_id, upload_code, created_at, expires_at FROM lab_sessions WHERE status='waiting_email'")}
        if not waiting:return 0
        processed=0
        for mid in self.collector._message_ids():
            with self.service.store.db() as db:
                if db.execute('SELECT 1 FROM ingested_events WHERE message_id=? LIMIT 1',(mid,)).fetchone():continue
            msg=self.collector.gmail.users().messages().get(userId='me',id=mid,format='full').execute()
            subject=self.collector._from_header(msg,'Subject')
            if subject not in waiting:continue
            target=waiting[subject]
            received=datetime.fromtimestamp(int(msg.get('internalDate', '0'))/1000, tz=timezone.utc)
            if received < datetime.fromisoformat(target['created_at']) or received > datetime.fromisoformat(target['expires_at']):continue
            sender_field=self.collector._from_header(msg,'From')
            sender_name,sender_email=parseaddr(sender_field)
            for idx,part in enumerate(self.collector._parts(msg.get('payload',{}))):
                filename=part.get('filename','')
                if not filename.lower().endswith('.txt'):continue
                body=part.get('body') or {}
                if int(body.get('size',0)) > self.service.settings.max_txt_bytes:continue
                attachment_id=body.get('attachmentId') or f'part-{idx}'
                if body.get('attachmentId'):
                    body=self.collector.gmail.users().messages().attachments().get(
                        userId='me',messageId=mid,id=attachment_id).execute()
                raw=self.collector._b64(body.get('data',''))
                staged=self.service.store_attachment(AttachmentIn(
                    message_id=mid, attachment_id=attachment_id, filename=filename,
                    content_base64=base64.b64encode(raw).decode('ascii')))
                event=EmailEventIn.model_validate({
                    'event_id':f'gmail-{mid}-{idx}', 'upload_id':target['upload_id'],
                    'upload_code':target['upload_code'],
                    'sender':{'name':sender_name,'email':sender_email},
                    'email':{'subject':subject,'message_id':mid,'received_at':received.isoformat()},
                    'attachment':{'file_id':staged['file_id'],'filename':filename,
                                  'file_type':'txt','validation_status':'valid'},
                })
                self.service.ingest_email(event)
                processed+=1
                # One file per session avoids unexpected multi-chat analysis.
                break
        return processed
