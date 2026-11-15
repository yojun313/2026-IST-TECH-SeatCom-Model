"""Gmail TXT 수신 분기 테스트 (실제 계정·네트워크·토큰 사용 없음).

프로젝트 루트에서 실행:
    python -m unittest backend.tests.test_gmail_adapter -v

실제 GmailAdapter, MIME 순회/base64 헬퍼, 입력 모델을 사용한다.
Gmail API와 저장/수집 서비스는 mock, 세션 조회는 메모리 SQLite다.
실제 OAuth 인증, Gmail 검색 결과, 디스크 저장 성공 여부는 검증하지 않는다.
"""

import base64
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from backend.gmail_adapter import GmailAdapter
from kakao_analysis.get_gmail_text_api.txt_from_gmail import TxtFromGmail


class GmailAdapterTxtTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE lab_sessions (
                upload_id TEXT, upload_code TEXT, created_at TEXT,
                expires_at TEXT, status TEXT
            );
            CREATE TABLE ingested_events (message_id TEXT);
            INSERT INTO lab_sessions VALUES (
                'upload-1', 'ABC123', '2026-11-15T00:00:00+00:00',
                '2026-11-15T01:00:00+00:00', 'waiting_email'
            );
        """)
        self.service = SimpleNamespace(
            settings=SimpleNamespace(max_txt_bytes=1024),
            store=SimpleNamespace(db=lambda: self.db),
            store_attachment=Mock(return_value={"file_id": "file-1"}),
            ingest_email=Mock(),
        )
        # 생성자의 OAuth 연결/폴더 생성을 건너뛰고 실제 헬퍼를 유지한다.
        self.collector = TxtFromGmail.__new__(TxtFromGmail)
        self.collector.gmail = Mock()
        self.messages = self.collector.gmail.users.return_value.messages.return_value
        self.messages.list.return_value.execute.return_value = {
            "messages": [{"id": "message-1"}]
        }
        self.raw = "카카오톡 대화 내용\n첨부파일 수신 테스트".encode("utf-8")
        self.encoded = base64.urlsafe_b64encode(self.raw).decode("ascii").rstrip("=")
        self.part = {
            "mimeType": "text/plain", "filename": "대화.txt",
            "body": {"attachmentId": "attachment-1", "size": len(self.raw)},
        }
        self.message = {
            "internalDate": self.timestamp("2026-11-15T00:30:00+00:00"),
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "Subject", "value": "SeatCom ABC123"},
                    {"name": "From", "value": "Tester <tester@example.com>"},
                ],
                "parts": [self.part],
            },
        }
        self.messages.get.return_value.execute.return_value = self.message
        self.attachments = self.messages.attachments.return_value
        self.attachments.get.return_value.execute.return_value = {
            "data": self.encoded, "size": len(self.raw)
        }
        self.adapter = GmailAdapter.__new__(GmailAdapter)
        self.adapter.service = self.service
        self.adapter.collector = self.collector

    @staticmethod
    def timestamp(value):
        return str(int(datetime.fromisoformat(value).timestamp() * 1000))

    def assert_received(self, attachment_id="attachment-1"):
        self.assertEqual(self.adapter.poll_once(), 1)
        self.service.store_attachment.assert_called_once()
        stored = self.service.store_attachment.call_args.args[0]
        self.assertEqual(base64.b64decode(stored.content_base64), self.raw)
        self.assertEqual(stored.filename, self.part["filename"])
        self.assertEqual(stored.attachment_id, attachment_id)
        self.assertEqual(stored.message_id, "message-1")
        self.service.ingest_email.assert_called_once()
        event = self.service.ingest_email.call_args.args[0]
        self.assertEqual(event.upload_id, "upload-1")
        self.assertEqual(event.upload_code, "ABC123")
        self.assertEqual(event.sender.email, "tester@example.com")
        self.assertEqual(event.attachment.file_id, "file-1")
        self.assertEqual(event.email.received_at.tzinfo, timezone.utc)

    def assert_skipped(self):
        self.assertEqual(self.adapter.poll_once(), 0)
        self.service.store_attachment.assert_not_called()
        self.service.ingest_email.assert_not_called()
        self.attachments.get.assert_not_called()

    def test_download_txt_by_attachment_id(self):
        self.assert_received()
        self.attachments.get.assert_called_once_with(
            userId="me", messageId="message-1", id="attachment-1"
        )

    def test_inline_txt_without_attachment_id(self):
        self.part["body"] = {"data": self.encoded, "size": len(self.raw)}
        self.assert_received(attachment_id="part-0")
        self.attachments.get.assert_not_called()

    def test_nested_mime_and_uppercase_extension(self):
        self.part["filename"] = "대화.TXT"
        self.message["payload"]["parts"] = [{
            "mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/html", "filename": "", "body": {}},
                self.part,
            ],
        }]
        self.assert_received()

    def test_no_waiting_session_does_not_query_gmail(self):
        self.db.execute("UPDATE lab_sessions SET status='expired'")
        self.assert_skipped()
        self.messages.list.assert_not_called()

    def test_no_search_results(self):
        self.messages.list.return_value.execute.return_value = {}
        self.assert_skipped()
        self.messages.get.assert_not_called()

    def test_subject_must_match_exactly(self):
        for subject in ("대화 내용", "Re: SeatCom ABC123", "SeatCom ABC123 "):
            with self.subTest(subject=subject):
                self.message["payload"]["headers"][0]["value"] = subject
                self.assert_skipped()

    def test_outside_session_time_is_skipped(self):
        for received in ("2026-11-14T23:59:59+00:00", "2026-11-15T01:00:01+00:00"):
            with self.subTest(received=received):
                self.message["internalDate"] = self.timestamp(received)
                self.assert_skipped()

    def test_already_ingested_message_is_skipped(self):
        self.db.execute("INSERT INTO ingested_events VALUES ('message-1')")
        self.assert_skipped()
        self.messages.get.assert_not_called()

    def test_oversized_txt_is_skipped_before_download(self):
        self.part["body"]["size"] = self.service.settings.max_txt_bytes + 1
        self.assert_skipped()

    def test_txt_at_size_limit_is_accepted(self):
        self.service.settings.max_txt_bytes = len(self.raw)
        self.assert_received()

    def test_non_txt_attachment_is_skipped(self):
        self.part["filename"] = "대화.zip"
        self.assert_skipped()

    def test_only_first_txt_in_message_is_ingested(self):
        self.message["payload"]["parts"].append({
            "filename": "두번째.txt",
            "body": {"attachmentId": "attachment-2", "size": len(self.raw)},
        })
        self.assert_received()
        self.attachments.get.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
