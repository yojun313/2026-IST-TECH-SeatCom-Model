"""5단계: 그래디언트 부스팅 순위 모델. LightGBM lambdarank(기본) + XGBoost rank:ndcg(비교).

라벨은 y_chosen만 쓴다. 노출 특징(exp_)은 학습 때 넣고, 평가·서비스 점수에서는 0(미노출)으로 둔다.
SHAP은 LightGBM 내장 TreeSHAP(pred_contrib=True)으로 계산한다(shap 패키지 불필요).
"""

from __future__ import annotations

import numpy as np

from ..data import EXP_COLS, FEATURE_COLS, group_cols

ALL_GROUPS = ["seat", "seatc", "pref", "match", "ctx", "rt", "hist", "inf", "exp"]
VARIANTS = {
    "a_full": ALL_GROUPS,
    "b_no_exp": [g for g in ALL_GROUPS if g != "exp"],
    "g_no_profile": [g for g in ALL_GROUPS if g not in ("pref", "match")],
    "h_profile_only": [g for g in ALL_GROUPS if g not in ("hist", "inf")],
}


def variant_cols(name):
    cols = group_cols(VARIANTS[name], FEATURE_COLS)
    if "pref" not in VARIANTS[name]:
        cols = [c for c in cols if c != "ctx_req_ok"] + [
            "ctx_req_ok"
        ]  # 필수 조건은 규칙이라 유지
    return list(dict.fromkeys(cols))


def neutralize(X, cols):
    X = X.copy()
    for c in EXP_COLS:
        if c in cols:
            X[:, cols.index(c)] = 0
    return X


def _subsample_queries(frame, frac, seed):
    rng = np.random.default_rng(seed)
    m = rng.random(len(frame.starts)) < frac
    return frame.subset(m)


class LGBRanker:
    def __init__(
        self, cols, params=None, max_rounds=600, early_stopping=40, n_jobs=24, seed=0
    ):
        self.cols, self.params, self.max_rounds, self.es, self.n_jobs, self.seed = (
            cols,
            params or {},
            max_rounds,
            early_stopping,
            n_jobs,
            seed,
        )

    def fit(self, train, val, init_model=None):
        import lightgbm as lgb

        p = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "eval_at": [1, 5],
            "verbosity": -1,
            "seed": self.seed,
            "num_threads": self.n_jobs,
            "lambdarank_truncation_level": 20,
            "deterministic": True,
            "force_row_wise": True,
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_data_in_leaf": 100,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l2": 1.0,
        }
        p.update(self.params)
        dtr = lgb.Dataset(
            train.X(self.cols),
            train.y,
            group=train.sizes,
            feature_name=self.cols,
            free_raw_data=True,
        )
        dva = lgb.Dataset(
            neutralize(val.X(self.cols), self.cols),
            val.y,
            group=val.sizes,
            reference=dtr,
        )
        self.booster = lgb.train(
            p,
            dtr,
            num_boost_round=self.max_rounds,
            valid_sets=[dva],
            valid_names=["val"],
            init_model=init_model,
            callbacks=[
                lgb.early_stopping(self.es, first_metric_only=True, verbose=False)
            ],
        )
        self.best_iter = self.booster.best_iteration
        return self

    def score(self, frame, neutral_exp=True):
        X = frame.X(self.cols)
        if neutral_exp:
            X = neutralize(X, self.cols)
        return self.booster.predict(X, num_iteration=self.best_iter)

    def shap(self, frame, n=20000, seed=0):
        rng = np.random.default_rng(seed)
        X = neutralize(frame.X(self.cols), self.cols)
        idx = rng.choice(len(X), min(n, len(X)), replace=False)
        c = self.booster.predict(
            X[idx], num_iteration=self.best_iter, pred_contrib=True
        )
        return dict(zip(self.cols, np.abs(c[:, :-1]).mean(0).tolist()))


def tune_lgb(train, val, cols, cfg, mrr_fn, n_trials, log=print):
    """Optuna: 학습 요청 일부로 학습, 검증 분할 MRR(노출 중립 점수)로 선택."""
    import optuna

    g = cfg["gbdt"]
    sub = _subsample_queries(train, g["tune_query_frac"], cfg["seed"])
    trials = []

    def obj(trial):
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 255, log=True),
            "min_data_in_leaf": trial.suggest_int(
                "min_data_in_leaf", 20, 1000, log=True
            ),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.4, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 30, log=True),
            "lambdarank_truncation_level": trial.suggest_int(
                "lambdarank_truncation_level", 5, 60
            ),
        }
        m = LGBRanker(
            cols, params, g["max_rounds"], g["early_stopping"], seed=cfg["seed"]
        ).fit(sub, val)
        v = mrr_fn(val, m.score(val))
        trials.append({"params": params, "best_iter": m.best_iter, "val_mrr": v})
        log(f"trial {trial.number}: val_mrr={v:.4f} iter={m.best_iter}")
        return v

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    st = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=cfg["seed"])
    )
    st.optimize(obj, n_trials=n_trials)
    return st.best_params, trials


class XGBRanker:
    def __init__(self, cols, params=None, device="cuda", rounds=600, es=40, seed=0):
        self.cols, self.params, self.device, self.rounds, self.es, self.seed = (
            cols,
            params or {},
            device,
            rounds,
            es,
            seed,
        )

    def fit(self, train, val):
        import xgboost as xgb

        dtr = xgb.DMatrix(train.X(self.cols), label=train.y, feature_names=self.cols)
        dtr.set_group(train.sizes)
        dva = xgb.DMatrix(
            neutralize(val.X(self.cols), self.cols),
            label=val.y,
            feature_names=self.cols,
        )
        dva.set_group(val.sizes)
        p = {
            "objective": "rank:ndcg",
            "eval_metric": "ndcg@5",
            "eta": 0.05,
            "max_depth": 8,
            "min_child_weight": 10,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "lambda": 1.0,
            "tree_method": "hist",
            "seed": self.seed,
            "lambdarank_pair_method": "topk",
            "lambdarank_num_pair_per_sample": 20,
        }
        p.update(self.params)
        try:
            p["device"] = self.device
            self.booster = xgb.train(
                p,
                dtr,
                self.rounds,
                evals=[(dva, "val")],
                early_stopping_rounds=self.es,
                verbose_eval=False,
            )
            self.used_device = self.device
        except Exception:
            p["device"] = "cpu"
            self.booster = xgb.train(
                p,
                dtr,
                self.rounds,
                evals=[(dva, "val")],
                early_stopping_rounds=self.es,
                verbose_eval=False,
            )
            self.used_device = "cpu"
        self.best_iter = self.booster.best_iteration
        return self

    def score(self, frame, neutral_exp=True):
        import xgboost as xgb

        X = frame.X(self.cols)
        if neutral_exp:
            X = neutralize(X, self.cols)
        return self.booster.predict(
            xgb.DMatrix(X, feature_names=self.cols),
            iteration_range=(0, self.best_iter + 1),
        )
