
import re
from pathlib import Path


class NameExtractor:
    TIMESTAMP = (
        r"\d{4}년\s+\d{1,2}월\s+\d{1,2}일\s+"
        r"(?:오전|오후)\s+\d{1,2}:\d{2}"
    )

    MESSAGE_PATTERN = re.compile(
        rf"^{TIMESTAMP},\s*"
        r"(?P<sender>.+?)\s+:\s"
        r"(?P<message>.*)$"
    )

    DATE_PATTERN = re.compile(
        rf"^{TIMESTAMP}(?:,|$)"
    )

    MEDIA_PATTERN = re.compile(
        r"사진(?:\s+\d+장)?|동영상|이모티콘|"
        r"음성메시지|파일:.*|톡게시판.*"
    )

    def extract(self, source: str | Path, name: str) -> str:
        if isinstance(source, Path):
            text = source.read_text(encoding="utf-8-sig")
        else:
            text = source

        messages = []
        current_sender = None
        current_lines = []

        def flush():
            if current_sender != name:
                return

            message = "\n".join(current_lines).strip()

            if message and not self.MEDIA_PATTERN.fullmatch(message):
                messages.append(message)

        for line in text.splitlines():
            # Markdown 코드 블록 구분자 제외
            if line.strip() == "```":
                continue

            match = self.MESSAGE_PATTERN.match(line)

            if match:
                flush()

                current_sender = match.group("sender")
                current_lines = [match.group("message")]

            elif (
                self.DATE_PATTERN.match(line)
                or line.strip() == "메시지가 삭제되었습니다."
            ):
                flush()
                current_sender = None
                current_lines = []

            elif current_sender is not None:
                # 여러 줄에 걸친 메시지
                current_lines.append(line)

        flush()

        return "\n".join(messages)
