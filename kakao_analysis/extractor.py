"""카카오톡 TXT 대화 내역 파서.

- extract_records(): 기존 호환: 대상 사용자가 작성한 메시지들만 반환
- extract_context_records(): 전체 대화 순서/타인 발언/시스템 메시지 보존
- extract(): 대상 발언만 텍스트로 반환 (with_context=True면 전체 맥락)
"""

from __future__ import annotations

import re
from pathlib import Path


class NameExtractor:
    TIME = (
        r"\d{4}년\s+\d{1,2}월\s+\d{1,2}일\s+"
        r"(?:오전|오후)\s+\d{1,2}:\d{2}"
    )
    MESSAGE = re.compile(
        rf"^(?P<time>{TIME}),\s*(?P<name>.+?)\s+:\s?(?P<text>.*)$"
    )
    DATE = re.compile(rf"^{TIME}$")
    SYSTEM = re.compile(rf"^(?P<time>{TIME}),\s*(?P<text>.+)$")
    MEDIA = re.compile(
        r"(?:사진(?:\s+\d+장)?|동영상|이모티콘|"
        r"음성메시지|파일:.*|톡게시판.*)"
    )

    @staticmethod
    def _load(source: str | Path) -> str:
        if not isinstance(source, Path):
            return source
        data = source.read_bytes()
        for encoding in ("utf-8-sig", "cp949"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                pass
        raise UnicodeError("지원하지 않는 TXT 인코딩입니다 (UTF-8/CP949).")

    def extract_context_records(
        self, source: str | Path, name: str
    ) -> list[dict]:
        """대화 전체를 시간순으로 보존하고 분석 대상의 메시지를 표시합니다.

        각 record: message_id, timestamp, sender, text, kind, is_target
        kind = message | system | date | metadata
        """
        text = self._load(source)
        records: list[dict] = []
        current: dict | None = None

        def append_record(
            kind: str, timestamp: str, sender: str | None, body: str
        ) -> None:
            records.append(
                {
                    "message_id": f"msg_{len(records) + 1:06d}",
                    "timestamp": timestamp,
                    "sender": sender,
                    "text": body,
                    "kind": kind,
                    "is_target": kind == "message" and sender == name,
                }
            )

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            append_record(
                kind="message",
                timestamp=current["timestamp"],
                sender=current["sender"],
                body="\n".join(current["lines"]).strip(),
            )
            current = None

        for line in text.splitlines():
            match = self.MESSAGE.match(line)
            if match:
                flush()
                current = {
                    "timestamp": match.group("time"),
                    "sender": match.group("name"),
                    "lines": [match.group("text")],
                }
                continue

            stripped = line.strip()
            if self.DATE.fullmatch(stripped):
                flush()
                append_record("date", stripped, None, "")
                continue

            # 초대/퇴장 등 시간 정보가 있는 시스템 이벤트
            sys_match = self.SYSTEM.match(line)
            if sys_match:
                flush()
                append_record("system", sys_match.group("time"), None, sys_match.group("text"))
                continue

            if stripped == "메시지가 삭제되었습니다.":
                flush()
                append_record("system", "", None, stripped)
                continue

            if current is not None:
                # 말풍선에 여러 줄이 포함되어 있을 때, 모든 줄과 빈 줄을 보존
                current["lines"].append(line)
            elif stripped:
                # 파일 제목, 저장 시간 등 헤더. 전체 맥락에서는 유지
                append_record("metadata", "", None, line)

        flush()
        return records

    def extract_records(self, source: str | Path, name: str) -> list[dict]:
        """이전 버전과 호환: 해당 사용자가 쓴 텍스트 메시지만 반환."""
        return [
            {
                "message_id": record["message_id"],
                "timestamp": record["timestamp"],
                "text": record["text"],
            }
            for record in self.extract_context_records(source, name)
            if (
                record["is_target"] and record["text"]
                and not self.MEDIA.fullmatch(record["text"])
            )
        ]

    def extract(self, source: str | Path, name: str, *, with_context: bool = False) -> str:
        if not with_context:
            return "\n".join(r["text"] for r in self.extract_records(source, name))

        lines = []
        for record in self.extract_context_records(source, name):
            if record["kind"] == "message":
                marker = "[TARGET]" if record["is_target"] else "[CONTEXT]"
                lines.append(
                    f'{marker} {record["timestamp"]}, {record["sender"]} : {record["text"]}'
                )
            elif record["kind"] == "date":
                lines.append(f'\n===== {record["timestamp"]} =====')
            else:
                lines.append(f'[SYSTEM] {record["timestamp"]} {record["text"]}'.strip())
        return "\n".join(lines)
