"""Gmail 수신함에서 카카오톡 TXT 첨부파일을 수집하는 간단한 폴링 리스너."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from email.utils import parseaddr
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


class TxtFromGmail:
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(
        self,
        email: str = "taywook74@gmail.com",
        poll_seconds: int = 20,
        directory: str | Path | None = None,
    ):
        self.email = email.lower()
        self.poll_seconds = max(5, poll_seconds)
        self.directory = Path(directory or Path(__file__).resolve().parent)
        self.storage = self.directory / "gmail_txt"
        self.storage.mkdir(parents=True, exist_ok=True)
        self.storage.chmod(0o700)
        self.token_path = self.directory / "token.json"
        self.credentials_path = self.directory / "credentials.json"
        self.index_path = self.storage / "index.json"

        self.index = self._load_index()
        self.gmail = self._connect()
        actual = (
            self.gmail.users().getProfile(userId="me").execute()["emailAddress"].lower()
        )
        if actual != self.email:
            raise RuntimeError(f"인증된 계정이 다릅니다: {actual} (필요: {self.email})")

    def _load_index(self) -> dict:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return {"seen_messages": [], "attachments": {}}

    def _save_index(self) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.index_path)

    def _connect(self):
        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(self.token_path), self.SCOPES
            )
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"OAuth 파일이 없습니다: {self.credentials_path}"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            self.token_path.chmod(0o600)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    @staticmethod
    def _parts(part: dict):
        if part.get("parts"):
            for child in part["parts"]:
                yield from TxtFromGmail._parts(child)
        else:
            yield part

    @staticmethod
    def _decode(raw: bytes) -> str:
        for encoding in ("utf-8-sig", "cp949", "utf-16"):
            try:
                return raw.decode(encoding)
            except UnicodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _from_header(message: dict, name: str) -> str:
        for header in message.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == name.lower():
                return header.get("value", "")
        return ""

    @staticmethod
    def _b64(data: str) -> bytes:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    def _message_ids(self):
        page_token = None
        while True:
            kwargs = {
                "userId": "me",
                "labelIds": ["INBOX"],
                "q": "has:attachment filename:txt",
                "maxResults": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            page = self.gmail.users().messages().list(**kwargs).execute()
            for item in page.get("messages", []):
                yield item["id"]
            page_token = page.get("nextPageToken")
            if not page_token:
                break

    def txt_from_gmail(self) -> str:
        """수집되지 않은 TXT 첨부파일들의 내용을 하나의 str로 반환합니다."""
        contents: list[str] = []
        seen = set(self.index["seen_messages"])
        for message_id in self._message_ids():
            if message_id in seen:
                continue
            message = (
                self.gmail.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            sender = parseaddr(self._from_header(message, "From"))[1]
            subject = self._from_header(message, "Subject")
            date = self._from_header(message, "Date")

            for position, part in enumerate(self._parts(message.get("payload", {}))):
                filename = part.get("filename", "")
                if not filename.lower().endswith(".txt"):
                    continue
                unique_id = f"{message_id}:{position}"
                if unique_id in self.index["attachments"]:
                    continue
                body = part.get("body", {})
                if body.get("attachmentId"):
                    body = (
                        self.gmail.users()
                        .messages()
                        .attachments()
                        .get(userId="me", messageId=message_id, id=body["attachmentId"])
                        .execute()
                    )
                raw = self._b64(body.get("data", ""))
                text = self._decode(raw)
                checksum = hashlib.sha256(raw).hexdigest()
                path = self.storage / f"{checksum}.txt"
                is_new_content = not path.exists()
                if is_new_content:
                    path.write_bytes(raw)

                self.index["attachments"][unique_id] = {
                    "message_id": message_id,
                    "sender": sender,
                    "subject": subject,
                    "date": date,
                    "original_filename": filename,
                    "saved_path": str(path),
                    "sha256": checksum,
                }
                self._save_index()
                if is_new_content:
                    contents.append(text)
                    print(f"[새 TXT] {sender} | {filename} | {subject}", flush=True)
                else:
                    print(f"[중복 TXT: 매핑만 저장] {sender} | {filename}", flush=True)

            self.index["seen_messages"].append(message_id)
            seen.add(message_id)
            self._save_index()
        return "\n\n".join(contents)

    def listen(self) -> None:
        """Gmail 수신함을 주기적으로 확인합니다. Ctrl+C로 종료합니다."""
        print(
            f"[Gmail 감시 시작] {self.email} / {self.poll_seconds}초 간격", flush=True
        )
        try:
            while True:
                try:
                    result = self.txt_from_gmail()
                    if result:
                        print(
                            "\n===== TXT 내용 =====\n",
                            result,
                            "\n====================\n",
                            flush=True,
                        )
                except Exception as exc:
                    print(f"[Gmail 조회 오류] {type(exc).__name__}: {exc}", flush=True)
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            print("\n[Gmail 감시 종료]", flush=True)


if __name__ == "__main__":
    collector = TxtFromGmail()
    collector.listen()
