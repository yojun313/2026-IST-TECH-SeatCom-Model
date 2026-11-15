"""최근 구간 선택, 증거 병합, 지연 시 계속 진행하는 동작을 검증한다."""

import itertools
import json
import threading
import time

import pytest

from kakao_analysis import api


def profile(evidence=()):
    return api.SeatProfile(
        participant_id="TARGET",
        preferences=api.Preferences(
            **{
                key: api.Preference(preference="unknown", confidence=0)
                for key in api.ALLOWED
            }
        ),
        evidence=list(evidence),
    )


def fake_model(analyzer, monkeypatch):
    calls = []
    lock = threading.Lock()
    active = 0
    peak = 0

    def call(prompt, content):
        nonlocal active, peak
        payload = json.loads(content)
        with lock:
            calls.append(payload)
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.001)
            if "messages" in payload:
                targets = [r for r in payload["messages"] if r["is_target"]]
                assert targets
                return profile(
                    [
                        api.Evidence(
                            feature="desk",
                            message_id=r["message_id"],
                            reason="작업 근거",
                        )
                        for r in (targets[0], targets[-1])
                    ]
                )
            evidence = [
                api.Evidence(**e)
                for p in payload["chunk_profiles"]
                for e in p["evidence"]
            ]
            return profile(evidence)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(analyzer, "_call_gpt_oss", call)
    return calls, lambda: peak


def chat(count, tail=0):
    return "\n".join(
        f"2026년 11월 15일 오후 1:00, {'나' if i < count else '친구'} : message-{i} "
        + "내용" * 40
        for i in range(count + tail)
    )


@pytest.mark.parametrize("count,tail", [(250, 0), (10000, 0), (250, 1000)])
def test_recent_chunks_and_evidence_merge(monkeypatch, count, tail):
    analyzer = api.PersonalityAnalysis()
    analyzer.oss_workers = 3
    analyzer.oss_merge_group = 6
    calls, peak = fake_model(analyzer, monkeypatch)
    result = analyzer.analyze_by_GPT_OSS_API(chat(count, tail), "나")
    chunks = [c["messages"] for c in calls if "messages" in c]
    merges = [c for c in calls if "chunk_profiles" in c]
    scope = result["analysis_scope"]
    assert 1 < len(chunks) <= 18
    assert 1 <= len(merges) <= 4
    assert len(calls) <= 22
    assert peak() <= 3
    seen = {r["message_id"]: r for c in chunks for r in c if r["kind"] == "message"}
    assert any(f"message-{count - 1} " in r["text"] for r in seen.values())
    assert scope["analyzed_messages"] == len(seen)
    assert scope["analyzed_messages"] > 120
    assert scope["total_messages"] == count + tail
    if count == 10000:
        assert len(chunks) == 18
        assert "msg_000001" not in seen
        assert scope["truncated"]
    if count == 250 and tail == 0:
        assert scope["analyzed_messages"] == 250
        assert not scope["truncated"]
    collected = result["collected_evidence"]
    keys = {(e["feature"], e["message_id"]) for e in collected}
    assert len(keys) == len(collected)
    assert {e["message_id"] for e in result["evidence"]} <= set(seen)
    assert len({(e["feature"], e["message_id"]) for e in result["evidence"]}) == len(
        result["evidence"]
    )
    assert all(e["message_id"] in seen for e in collected)
    merged_ids = {
        e["message_id"]
        for c in merges
        for p in c["chunk_profiles"]
        for e in p["evidence"]
    }
    assert {e["message_id"] for e in collected} <= merged_ids


def test_long_message_is_preserved_as_fragments(monkeypatch):
    analyzer = api.PersonalityAnalysis()
    calls, _ = fake_model(analyzer, monkeypatch)
    body = "긴대화" * 20000
    result = analyzer.analyze_by_GPT_OSS_API(
        "2026년 11월 15일 오후 1:00, 나 : " + body, "나"
    )
    pieces = {
        r["fragment_index"]: r["text"]
        for c in calls
        if "messages" in c
        for r in c["messages"]
    }
    assert "".join(pieces[i] for i in sorted(pieces)) == body
    assert result["analysis_scope"]["analyzed_messages"] == 1
    assert not result["analysis_scope"]["truncated"]


def test_one_minute_is_not_a_deadline(monkeypatch):
    analyzer = api.PersonalityAnalysis()
    calls, _ = fake_model(analyzer, monkeypatch)
    clock = itertools.count(0, 120)
    monkeypatch.setattr(api, "perf_counter", lambda: next(clock))
    result = analyzer.analyze_by_GPT_OSS_API(chat(250), "나")
    assert any("chunk_profiles" in c for c in calls)
    assert result["analysis_scope"]["hard_deadline_seconds"] is None
    assert result["analysis_scope"]["target_seconds"] == 60
