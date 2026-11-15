"""CPU models: content baseline, pointwise/pairwise logistic, satisfaction GBDT, LambdaMART."""
from copy import deepcopy
import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.ensemble import HistGradientBoostingRegressor
from .features import NAMES

METHODS = ('content', 'pointwise', 'pairwise', 'satisfaction', 'lambdamart')


def scores(bundle, X):
    if bundle['features'] != NAMES:
        raise ValueError('Model feature schema does not match serving code')
    if bundle['method'] == 'content':
        return np.asarray(X[:, -1])
    m = bundle['model']
    if bundle['method'] in ('pointwise', 'pairwise'):
        return m.decision_function(X)
    if bundle['method'] == 'lambdamart':
        return m.booster_.predict(X, num_threads=bundle.get('threads', 2))
    return m.predict(X)


def evaluate(bundle, data, start_group, end_group, k=5):
    offsets, X, y = data['offsets'], data['X'], data['y']
    a, b = int(offsets[start_group]), int(offsets[end_group])
    pred = np.concatenate([scores(bundle, X[i:min(i + 16384, b)]) for i in range(a, b, 16384)])
    ranks = []
    for g in range(start_group, end_group):
        lo, hi = int(offsets[g]), int(offsets[g + 1])
        order = np.argsort(-pred[lo-a:hi-a], kind='stable')
        ranks.append(int(np.flatnonzero(y[lo:hi][order])[0]) + 1)
    ranks = np.array(ranks)
    result = {'sessions': len(ranks), f'hit@{k}': float(np.mean(ranks <= k)),
              f'ndcg@{k}': float(np.mean(np.where(ranks <= k, 1 / np.log2(ranks + 1), 0))),
              'mrr': float(np.mean(1 / ranks)), 'top1': float(np.mean(ranks == 1)),
              'mean_candidates': float((b-a) / len(ranks))}
    if bundle['method'] == 'satisfaction':
        labels = data['satisfaction'][a:b]
        mask = np.isfinite(labels)
        result['satisfaction_rmse_chosen_only'] = (float(np.sqrt(np.mean((pred[mask] - labels[mask])**2))) if mask.any() else None)
    return result


def train(method, meta, data, epochs=10, rounds=150, seed=20261114, threads=2):
    bundle = dict(method=method, features=NAMES, schema_version=1, seed=seed, threads=threads, model=None)
    if method == 'content':
        return bundle
    end = meta['row_splits'][1]
    ng = meta['group_splits'][1]
    X, y, offsets = data['X'], data['y'], data['offsets']
    rng = np.random.default_rng(seed)
    if method in ('pointwise', 'pairwise'):
        model = SGDClassifier(loss='log_loss', alpha=.0001, fit_intercept=method == 'pointwise',
                              learning_rate='constant', eta0=.01, average=True, random_state=seed)
        best, best_value = None, -1
        for epoch in range(epochs):
            # Group batches keep positives and negatives together without copying the full matrix.
            for first in rng.permutation(np.arange(0, ng, 512)):
                last = min(int(first) + 512, ng)
                if method == 'pointwise':
                    a, b = int(offsets[first]), int(offsets[last])
                    xx, yy = X[a:b], y[a:b]
                else:
                    pairs = []
                    for g in range(int(first), last):
                        a, b = int(offsets[g]), int(offsets[g+1])
                        group = X[a:b]
                        pairs.append(group[y[a:b] == 1] - group[y[a:b] == 0])
                    diff = np.concatenate(pairs)
                    xx = np.concatenate((diff, -diff))
                    yy = np.r_[np.ones(len(diff)), np.zeros(len(diff))]
                model.partial_fit(xx, yy, classes=[0, 1])
            bundle['model'] = model
            value = evaluate(bundle, data, ng, meta['group_splits'][2])['ndcg@5']
            if value > best_value:
                best, best_value = deepcopy(model), value
                bundle['selected_epoch'] = epoch + 1
        bundle['model'] = best
    elif method == 'satisfaction':
        idx = np.flatnonzero(np.isfinite(data['satisfaction'][:end]))
        if len(idx) < 2:
            raise ValueError('Satisfaction training needs at least two observed ratings')
        # No randomly held-out candidate rows; temporal validation is external.
        model = HistGradientBoostingRegressor(max_iter=rounds, max_leaf_nodes=15,
                                             l2_regularization=1, early_stopping=False, random_state=seed)
        model.fit(X[idx], data['satisfaction'][idx])
        bundle['model'] = model
    elif method == 'lambdamart':
        from lightgbm import LGBMRanker, early_stopping, log_evaluation
        v_end = meta['row_splits'][2]
        vg = meta['group_splits'][2]
        model = LGBMRanker(objective='lambdarank', n_estimators=rounds, learning_rate=.05,
                          num_leaves=15, reg_lambda=1, random_state=seed, n_jobs=threads,
                          verbosity=-1, deterministic=True, force_col_wise=True)
        model.fit(X[:end], y[:end], group=np.diff(offsets[:ng+1]),
                  eval_set=[(X[end:v_end], y[end:v_end])], eval_group=[np.diff(offsets[ng:vg+1])],
                  eval_at=[5], callbacks=[early_stopping(20, verbose=False), log_evaluation(0)])
        bundle['model'] = model
    else:
        raise ValueError(f'Unknown method: {method}')
    return bundle
