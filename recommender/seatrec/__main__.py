import argparse
import json
from pathlib import Path
import joblib
from threadpoolctl import threadpool_limits
from .data import prepare, mongo, load
from .models import METHODS, train, evaluate
from .predict import recommend

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description='Seat recommendation training and live snapshot ranking')
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--source', choices=['jsonl', 'mongo'], default='jsonl')
    p.add_argument('--data-dir', default=str(ROOT / 'data/libraries/synthetic'))
    p.add_argument('--env', default=str(ROOT / '.env'))
    p.add_argument('--db', default='library')
    p.add_argument('--output', default=str(ROOT / 'recommender/artifacts/dataset'))
    p.add_argument('--negatives', type=int, default=10)
    p.add_argument('--max-sessions', type=int, default=0)
    p.add_argument('--seed', type=int, default=20261114)
    p = sub.add_parser('train')
    p.add_argument('--data', default=str(ROOT / 'recommender/artifacts/dataset'))
    p.add_argument('--output', default=str(ROOT / 'recommender/artifacts/models'))
    p.add_argument('--models', nargs='+', choices=METHODS, default=list(METHODS))
    p.add_argument('--epochs', type=int, default=10)
    p.add_argument('--rounds', type=int, default=150)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--seed', type=int, default=20261114)
    p = sub.add_parser('predict')
    p.add_argument('--model', required=True)
    p.add_argument('--request', required=True)
    p.add_argument('--top-k', type=int, default=5)
    p.add_argument('--max-age-seconds', type=float, default=120)
    a = ap.parse_args()
    if a.command == 'prepare':
        client, db = mongo(a.env, a.db) if a.source == 'mongo' else (None, None)
        try:
            result = prepare(a.data_dir, a.output, a.negatives, a.seed, a.max_sessions, db)
        finally:
            if client is not None:
                client.close()
    elif a.command == 'train':
        if min(a.epochs, a.rounds, a.threads) < 1:
            ap.error('epochs, rounds and threads must be positive')
        meta, data = load(a.data)
        out = Path(a.output)
        out.mkdir(parents=True, exist_ok=True)
        results = {}
        g = meta['group_splits']
        with threadpool_limits(limits=a.threads):
            for method in a.models:
                print(f'Training {method}...', flush=True)
                bundle = train(method, meta, data, a.epochs, a.rounds, a.seed, a.threads)
                bundle['training_manifest'] = meta
                joblib.dump(bundle, out / f'{method}.joblib')
                results[method] = dict(validation=evaluate(bundle, data, g[1], g[2]),
                                       test=evaluate(bundle, data, g[2], g[3]))
                print(json.dumps({method: results[method]}, ensure_ascii=False), flush=True)
        best = max(results, key=lambda m: results[m]['validation']['ndcg@5'])
        # Selection uses validation only; the test set is report-only.
        result = dict(best_model_by_validation_ndcg5=best, metrics=results, dataset=meta,
                      config={k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()})
        (out / 'metrics.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    else:
        # Only load model artifacts from a trusted source (joblib uses pickle).
        bundle = joblib.load(a.model)
        request = json.loads(Path(a.request).read_text(encoding='utf-8'))
        with threadpool_limits(limits=bundle.get('threads', 2)):
            result = recommend(bundle, request, a.top_k, a.max_age_seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
