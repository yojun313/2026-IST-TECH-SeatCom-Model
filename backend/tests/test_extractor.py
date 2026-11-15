"""Korean/English KakaoTalk exports: boundaries, context, and text extraction."""

import pytest

from kakao_analysis.extractor import NameExtractor


ENGLISH_CHAT = """Talk_2026.11.15 05:34-1.txt
Date Saved : Nov 15, 2026 at 1:04 PM

Thursday, June 4, 2026
Jun 4, 2026 at 3:55 PM: 배건희 invited (사람) .
Jun 4, 2026 at 3:55 PM, 배건희 : Emoticons
Jun 4, 2026 at 3:56 PM, 배건희 : 상/하반기 멤버 다 같이 엠티가려고 톡 따로 팠고
Jun 4, 2026 at 3:56 PM, 배건희 : 날짜는 6/27~28 금~토로 확정할게요~~
Jun 4, 2026 at 3:57 PM, 배건희 : 각자 인사 나누고 이야기하면 너무 좋겠지만 숙소 예약이 급할 것 같아서….
Jun 4, 2026 at 3:57 PM, 배건희 : 어디갈까
"""


def test_supplied_english_export():
    parser = NameExtractor()
    records = parser.extract_context_records(ENGLISH_CHAT, "배건희")
    assert [r["kind"] for r in records] == [
        "metadata",
        "metadata",
        "date",
        "system",
    ] + ["message"] * 5
    assert records[2]["timestamp"] == "Thursday, June 4, 2026"
    assert records[3]["sender"] is None and not records[3]["is_target"]
    assert records[3]["text"] == "배건희 invited (사람) ."
    assert all(r["sender"] == "배건희" and r["is_target"] for r in records[4:])
    assert [r["message_id"] for r in records] == [f"msg_{i:06d}" for i in range(1, 10)]
    extracted = parser.extract_records(ENGLISH_CHAT, "배건희")
    assert [r["text"] for r in extracted] == [
        "상/하반기 멤버 다 같이 엠티가려고 톡 따로 팠고",
        "날짜는 6/27~28 금~토로 확정할게요~~",
        "각자 인사 나누고 이야기하면 너무 좋겠지만 숙소 예약이 급할 것 같아서….",
        "어디갈까",
    ]
    assert extracted[0]["timestamp"] == "Jun 4, 2026 at 3:56 PM"
    assert parser.extract_records(ENGLISH_CHAT, "다른 사람") == []
    assert parser.extract(ENGLISH_CHAT, "배건희") == "\n".join(
        r["text"] for r in extracted
    )
    assert "[TARGET] Jun 4, 2026 at 3:57 PM, 배건희 : 어디갈까" in parser.extract(
        ENGLISH_CHAT, "배건희", with_context=True
    )


@pytest.mark.parametrize("space", [" ", "\u00a0", "\u202f"])
@pytest.mark.parametrize("period", ["AM", "PM"])
@pytest.mark.parametrize("month", ["Jun", "June", "Nov", "November"])
def test_english_timestamp_variants(space, period, month):
    timestamp = f"{month} 4, 2026 at 12:05{space}{period}"
    records = NameExtractor().extract_context_records(
        f"{timestamp}, Test User : hello", "Test User"
    )
    assert len(records) == 1
    assert records[0]["timestamp"] == timestamp
    assert records[0]["sender"] == "Test User"
    assert records[0]["text"] == "hello" and records[0]["is_target"]


def test_multiline_day_and_system_boundaries():
    chat = (
        "Jun 4, 2026 at 11:59 PM, A : first: https://example.com\n"
        "second line\n\nlast line\n\n"
        "Friday, June 5, 2026\n"
        "Jun 5, 2026 at 12:00 AM, B : reply\n"
        "Jun 5, 2026 at 12:01 AM: B left.\n"
        "Jun 5, 2026 at 12:02 AM, A : final"
    )
    records = NameExtractor().extract_context_records(chat, "A")
    assert [r["kind"] for r in records] == [
        "message",
        "date",
        "message",
        "system",
        "message",
    ]
    assert records[0]["text"] == "first: https://example.com\nsecond line\n\nlast line"
    assert not records[2]["is_target"] and records[2]["text"] == "reply"
    assert records[-1]["text"] == "final"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp949"])
def test_korean_file_regression(tmp_path, encoding):
    chat = (
        "테스트 대화\r\n2026년 6월 4일 오후 3:55\r\n"
        "2026년 6월 4일 오후 3:55, A님이 입장했습니다.\r\n"
        "2026년 6월 4일 오후 3:56, A : 이모티콘\r\n"
        "2026년 6월 4일 오후 3:57, A : 첫 줄\r\n두 번째 줄\r\n"
        "2026년 6월 4일 오후 3:58, B : 답장\r\n"
        "메시지가 삭제되었습니다.\r\n"
    )
    path = tmp_path / "chat.txt"
    path.write_bytes(chat.encode(encoding))
    parser = NameExtractor()
    records = parser.extract_context_records(path, "A")
    assert [r["kind"] for r in records] == [
        "metadata",
        "date",
        "system",
        "message",
        "message",
        "message",
        "system",
    ]
    assert parser.extract(path, "A") == "첫 줄\n두 번째 줄"


def test_english_file_bom_crlf(tmp_path):
    path = tmp_path / "chat.txt"
    path.write_bytes(ENGLISH_CHAT.replace("\n", "\r\n").encode("utf-8-sig"))
    parser = NameExtractor()
    assert parser.extract_context_records(
        path, "배건희"
    ) == parser.extract_context_records(ENGLISH_CHAT, "배건희")


@pytest.mark.parametrize("body", ["Emoticons", "Photo", "Video", "이모티콘"])
def test_media_kept_as_context_but_excluded_from_target_text(body):
    chat = f"Jun 4, 2026 at 3:55 PM, A : {body}"
    parser = NameExtractor()
    assert parser.extract_context_records(chat, "A")[0]["text"] == body
    assert parser.extract_records(chat, "A") == []


def test_media_words_in_real_message_are_preserved():
    chat = "Jun 4, 2026 at 3:55 PM, A : Emoticons are fun\nPhoto album"
    assert NameExtractor().extract(chat, "A") == "Emoticons are fun\nPhoto album"


def test_non_chat_is_not_a_message():
    assert not any(
        r["kind"] == "message"
        for r in NameExtractor().extract_context_records(
            "Date Saved : Nov 15, 2026 at 1:04 PM\nThis is just a note", "A"
        )
    )
