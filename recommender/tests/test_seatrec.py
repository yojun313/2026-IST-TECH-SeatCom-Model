import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import joblib
import numpy as np
from threadpoolctl import threadpool_limits
from recommender.seatrec.features import FEATURES, NAMES, vector
from recommender.seatrec.data import prepare, load
from recommender.seatrec.models import METHODS, train, evaluate, scores
from recommender.seatrec.predict import recommend


def user():
    return dict(user_id='u', preference_profile={'preferences': [
        dict(featureId='brightness', target=.9, importance=1)]},
        requirements=[dict(featureId='laptopAllowed', operator='eq', value=1)])


def seat(sid, light=.9):
    return dict(seat_id=sid, scenario_id='sc', library_id='lib', room_id='r',
                features={f: (light if f == 'brightness' else 1) for f in FEATURES},
                raw={'noise_index': .2}, brightness_by_slot={'evening': .1}, status='empty')


class SeatRecTests(unittest.TestCase):
    def test_feature_allowlist_and_slot(self):
        u, s = user(), seat('a')
        c = {'time_slot': 'noon', 'occupancy_rate': .5}
        x = vector(u, s, c)
        u['latent'] = {'utility_weights': {'brightness': 100}}
        c.update(chosen_true_utility=999, satisfaction=1, chosen_preference_fit=1,
                 exposed=[{'engine_score': 1}], chosen_seat_id='a')
        np.testing.assert_array_equal(x, vector(u, s, c))
        self.assertAlmostEqual(float(vector(u, s, dict(c, time_slot='evening'))[7]), .1)

    def test_serving_filters_and_staleness(self):
        b = dict(method='content', features=NAMES)
        now = datetime.now(timezone.utc)
        seats = [seat(str(i)) for i in range(6)]
        seats[1]['status'] = 'occupied'
        seats[2]['status'] = 'reserved'
        seats[3]['features']['laptopAllowed'] = None
        seats[4]['room_id'] = 'other'
        seats[5].pop('status')
        req = dict(user=user(), context=dict(time_slot='noon', occupancy_rate=.5, library_id='lib', room_id='r'),
                   snapshot=dict(updated_at=now.isoformat(), seats=seats))
        self.assertEqual([x['seat_id'] for x in recommend(b, req, now=now)], ['0'])
        req['snapshot']['seats'] = []
        self.assertEqual(recommend(b, req, now=now), [])
        req['snapshot']['updated_at'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            recommend(b, req, now=now)

    def test_metrics_hand_calculation(self):
        data = dict(X=np.array([[0, .1], [0, .8], [0, .5]], dtype=np.float32),
                    y=np.array([0, 0, 1]), offsets=np.array([0, 3]))
        m = evaluate(dict(method='content', features=NAMES), data, 0, 1)
        self.assertEqual(m['mrr'], .5)
        self.assertAlmostEqual(m['ndcg@5'], 1 / np.log2(3))

    def test_all_models_roundtrip_and_split(self):
        with tempfile.TemporaryDirectory() as td, threadpool_limits(limits=1):
            root = Path(td)
            sessions = [dict(session_id=str(i), user_id='u', scenario_id='sc', library_id='lib', room_id='r',
                             time_slot='noon', occurred_at=f'2026-09-{1+i//24:02d}T{i%24:02d}:00:00+09:00',
                             occupancy_rate=.2, chosen_seat_id='a', negative_seat_ids=['b', 'c'], satisfaction=.9)
                        for i in range(120)]
            for name, records in [('synth_users', [user()]), ('synth_seat_env', [seat('a'), seat('b', .2), seat('c', .5)]),
                                  ('synth_sessions', list(reversed(sessions)))]:
                (root / f'{name}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            prepare(root, root / 'prepared')
            meta, data = load(root / 'prepared')
            self.assertEqual(meta['group_splits'], [0, 84, 102, 120])
            self.assertEqual(meta['rows'], 360)
            for method in METHODS:
                bundle = train(method, meta, data, epochs=2, rounds=5, threads=1)
                path = root / f'{method}.joblib'
                joblib.dump(bundle, path)
                np.testing.assert_allclose(scores(bundle, data['X'][:3]), scores(joblib.load(path), data['X'][:3]))
                metrics = evaluate(bundle, data, 102, 120)
                self.assertGreater(metrics['ndcg@5'], 0)
                self.assertEqual(metrics['sessions'], 18)


if __name__ == '__main__':
    unittest.main()
