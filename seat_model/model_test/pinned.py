"""고정 생성기(synth-1.0, gen_v1_pinned.py)를 경로만 바꿔 불러온다.

gen_v1_pinned.py는 커밋 adb80af의 생성기와 바이트 단위로 같아야 한다(수정 금지).
원본은 자기 파일 위치 기준으로 data/libraries 경로를 잡으므로, 불러온 뒤 LIB·ROOT·OUT만 바꾼다.
출력(OUT)은 반드시 호출자가 지정한 폴더로 가고, data/libraries/synthetic 에는 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PINNED = Path(__file__).with_name("gen_v1_pinned.py")
LIB = REPO / "data" / "libraries"
TRAIN_OUT = LIB / "synthetic"


def load_config(path=None) -> dict:
    p = Path(path) if path else REPO / "seat_model" / "config" / "model_test.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    for k, v in cfg["paths"].items():
        cfg["paths"][k] = str(REPO / v)
    return cfg


def check_pinned(commit: str):
    """복사본이 커밋의 생성기와 같은 git blob인지 확인한다."""
    want = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO),
            "rev-parse",
            f"{commit}:data/libraries/scripts/generate_synthetic.py",
        ],
        text=True,
    ).strip()
    data = PINNED.read_bytes()
    got = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    if got != want:
        raise RuntimeError(
            f"gen_v1_pinned.py가 {commit} 생성기와 다르다 ({got} != {want})"
        )
    return got


def load_generator(out_dir):
    out = Path(out_dir).resolve()
    if out == TRAIN_OUT.resolve() or TRAIN_OUT.resolve() in out.parents:
        raise RuntimeError("학습 데이터 폴더(data/libraries/synthetic)에는 쓰지 않는다")
    spec = importlib.util.spec_from_file_location("gen_v1_pinned", PINNED)
    g = importlib.util.module_from_spec(spec)
    sys.modules["gen_v1_pinned"] = g  # 멀티프로세스 작업 함수 직렬화(pickle)에 필요
    spec.loader.exec_module(g)
    if g.GEN_VERSION != "synth-1.0":
        raise RuntimeError(f"고정 생성기 버전이 synth-1.0이 아니다: {g.GEN_VERSION}")
    g.LIB, g.ROOT, g.OUT = LIB, LIB / "real", out
    out.mkdir(parents=True, exist_ok=True)
    return g
