"""외부 API 분석은 선택 의존성 vllm 없이도 로딩되어야 한다."""

import subprocess
import sys
from pathlib import Path


def test_api_analyzer_without_vllm():
    script = """
import importlib.abc
import sys

class NoVllm(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "vllm" or fullname.startswith("vllm."):
            raise ModuleNotFoundError("No module named 'vllm'", name="vllm")

sys.meta_path.insert(0, NoVllm())
from kakao_analysis.api import PersonalityAnalysis, VLLMPersonalityAnalysis
analyzer = PersonalityAnalysis()
assert callable(analyzer.analyze_by_GPT_OSS_API)
try:
    VLLMPersonalityAnalysis()
except RuntimeError as exc:
    assert "vllm" in str(exc)
else:
    raise AssertionError("Local GPU analysis must require vllm")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
