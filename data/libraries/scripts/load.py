"""학습/분석용 로더. 예: import sys; sys.path.insert(0,'libraries/scripts'); from load import load_table, load_synthetic"""

import json
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
U = LIB / "unified"
S = LIB / "synthetic"


def load_table(name, library_id=None):
    out = []
    with open(U / f"{name}.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if library_id is None or r["library_id"] == library_id:
                out.append(r)
    return out


def load_df(name, library_id=None):
    import pandas as pd

    return pd.DataFrame(load_table(name, library_id))


def load_synthetic(name):
    """name: synth_scenarios | synth_seat_env | synth_users | synth_sessions | synth_events"""
    with open(S / f"{name}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]
