"""학습/분석용 로더. 예: from load import load_table; seats = load_table('seats')"""
import json
from pathlib import Path

U = Path(__file__).parent / "_unified"


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
