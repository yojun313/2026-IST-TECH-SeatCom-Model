"""M4 방향성 검사(특징값을 바꾸지 않는다).

    seat_model/.venv/bin/python -m seat_model.model_test.consistency

- clogit 서비스 모델: match_* 계수 부호가 0 이상인지(계수 검사만). laptopAllowed는 열람실 단위 규정이라 제외.
  inf_x_* 음수 계수는 경고로 보고한다.
- LightGBM: base 테스트 행(그대로)에서 match_k 값과 그 특징 기여도(pred_contrib)의 순위상관이 0보다 큰지.
출력: exports/test_v1/eval/consistency.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from seat_model.train.common import load_config as load_train_config
from seat_model.train.data import PREF_FIDS
from seat_model.train.models.gbdt import neutralize
from seat_model.train.run import MODEL_VERSION

from .pinned import load_config

EXCLUDE = {"laptopAllowed"}


def spearman(a, b):
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    import lightgbm as lgb

    cfg, tcfg = load_config(), load_train_config()
    mdir = Path(tcfg["paths"]["models"]) / MODEL_VERSION
    st = json.loads((mdir / "clogit_b_no_exp.json").read_text())
    beta = dict(zip(st["cols"], st["beta"]))
    feats = [f for f in PREF_FIDS if f not in EXCLUDE]
    match = {f: beta.get(f"match_{f}") for f in feats}
    inf_neg = {c[6:]: b for c, b in beta.items() if c.startswith("inf_x_") and b < 0}
    clogit = {
        "match_coef": match,
        "violations": [f for f, b in match.items() if b is None or b < 0],
        "excluded": {f: beta.get(f"match_{f}") for f in EXCLUDE},
        "inf_x_negative_warning": inf_neg,
    }

    booster = lgb.Booster(model_file=str(mdir / "lgbm_b_no_exp.txt"))
    cols = booster.feature_name()
    cand = pd.read_parquet(
        Path(cfg["paths"]["exports"]) / "eval" / "base" / "candidates.parquet",
        columns=cols,
    )
    idx = np.random.default_rng(0).choice(
        len(cand), min(100_000, len(cand)), replace=False
    )
    X = neutralize(cand.to_numpy(np.float32)[idx], cols)
    contrib = booster.predict(X, pred_contrib=True)
    lg = {}
    for f in feats:
        c = f"match_{f}"
        if c not in cols:
            lg[f] = None
            continue
        j = cols.index(c)
        x = X[:, j]
        nz = x > 0  # 관측 선호가 있는 행(중요도 > 0)
        lg[f] = {
            "spearman": spearman(x[nz], contrib[nz, j]) if nz.sum() > 100 else None,
            "rows": int(nz.sum()),
        }
    lgbm = {
        "direction": lg,
        "violations": [
            f
            for f, v in lg.items()
            if v and v["spearman"] is not None and v["spearman"] <= 0
        ],
    }
    out = {
        "clogit": clogit,
        "lgbm": lgbm,
        "passed": not clogit["violations"] and not lgbm["violations"],
    }
    p = Path(cfg["paths"]["exports"]) / "eval" / "consistency.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
