
import re
from pathlib import Path


class NameExtractor:
    TIME = (
        r"\d{4}년\s+\d{1,2}월\s+\d{1,2}일\s+"
        r"(?:오전|오후)\s+\d{1,2}:\d{2}"
    )

    MESSAGE = re.compile(
        rf"^(?P<time>{TIME}),\s*"
        r"(?P<name>.+?)\s+:\s"
        r"(?P<text>.*)$"
    )

    DATE = re.compile(rf"^{TIME}$")

    MEDIA = re.compile(
        r"(?:사진(?:\s+\d+장)?|동영상|이모티콘|"
        r"음성메시지|파일:.*|톡게시판.*)"
    )

    def extract_records(
        self, source: str | Path, name: str
    ) -> list[dict[str, str]]:

        if isinstance(source, Path):
            data = source.read_bytes()

            for encoding in ("utf-8-sig", "cp949"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise UnicodeError(
                    "지원하지 않는 TXT 인코딩입니다."
                )
        else:
            text = source

        result = []
        sender = None
        timestamp = ""
        body = []
        message_no = 0

        def flush():
            content = "\n".join(body).strip()

            if (
                sender == name
                and content
                and not self.MEDIA.fullmatch(content)
            ):
                result.append({
                    "message_id": f"msg_{message_no:06d}",
                    "timestamp": timestamp,
                    "text": content,
                })

        for line in text.splitlines():
            match = self.MESSAGE.match(line)

            if match:
                flush()
                message_no += 1
                sender = match.group("name")
                timestamp = match.group("time")
                body = [match.group("text")]

            elif (
                self.DATE.fullmatch(line.strip())
                or line.strip()
                == "메시지가 삭제되었습니다."
            ):
                flush()
                sender = None
                timestamp = ""
                body = []

            elif sender is not None:
                body.append(line)

        flush()
        return result

    def extract(
        self, source: str | Path, name: str
    ) -> str:
        records = self.extract_records(source, name)
        return "\n".join(
            record["text"] for record in records
        )
