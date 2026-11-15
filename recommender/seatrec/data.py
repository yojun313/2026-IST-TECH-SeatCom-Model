"""JSONL/MongoDB readers. Never pass latent or post-choice metadata to features."""
import json
from datetime import datetime
from pathlib import Path
import numpy as np
from .features import NAMES, eligible, vector

FIELDS = {
    'synth_users': ['user_id', 'preference_profile', 'requirements'],
    'synth_seat_env': ['scenario_id', 'seat_id', 'library_id', 'room_id', 'features', 'brightness_by_slot', 'raw'],
    'synth_sessions': ['session_id', 'user_id', 'scenario_id', 'library_id', 'room_id', 'time_slot',
                       'occurred_at', 'occupancy_rate', 'chosen_seat_id', 'negative_seat_ids', 'satisfaction'],
}


def rows(name, directory, db=None):
    fields = FIELDS[name]
    if db is not None:
        yield from db[name].find({}, {**{k: 1 for k in fields}, '_id': 0})
        return
    path = Path(directory) / f'{name}.jsonl'
    if not path.exists():
        raise FileNotFoundError(f'{path}: run data/libraries/scripts/generate_synthetic.py first')
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                r = json.loads(line)
                yield {k: r[k] for k in fields if k in r}


def mongo(env_file, name):
    from dotenv import dotenv_values
    from pymongo import MongoClient
    import os
    e = {**dotenv_values(env_file), **os.environ}
    kwargs = dict(serverSelectionTimeoutMS=10000)
    if e.get('MONGO_USER'):
        kwargs.update(username=e['MONGO_USER'], password=e['MONGO_PASSWORD'], authSource=e.get('MONGO_AUTH_DB', 'admin'))
    client = MongoClient(e.get('MONGO_HOST', 'localhost'), int(e.get('MONGO_PORT', 27017)), **kwargs)
    client.admin.command('ping')
    return client, client[name]


def prepare(directory, out, negatives=10, seed=20261114, max_sessions=0, db=None):
    if negatives < 1 or max_sessions < 0:
        raise ValueError('negatives >= 1 and max_sessions >= 0 required')
    users = {r['user_id']: r for r in rows('synth_users', directory, db)}
    seats = {(r['scenario_id'], r['seat_id']): r for r in rows('synth_seat_env', directory, db)}
    sessions = list(rows('synth_sessions', directory, db))
    # ISO strings may contain different timezone offsets; sort actual instants.
    sessions.sort(key=lambda s: (datetime.fromisoformat(s['occurred_at']).timestamp(), s['session_id']))
    rng = np.random.default_rng(seed)
    if max_sessions and len(sessions) > max_sessions:
        sessions = [sessions[i] for i in sorted(rng.choice(len(sessions), max_sessions, replace=False))]
    groups, skipped = [], {'no_choice': 0, 'ineligible_choice': 0, 'no_negative': 0}
    seen = set()
    for s in sessions:
        if s['session_id'] in seen:
            raise ValueError(f"Duplicate session: {s['session_id']}")
        seen.add(s['session_id'])
        if not s['chosen_seat_id']:
            skipped['no_choice'] += 1
            continue
        u = users[s['user_id']]
        pos = seats[(s['scenario_id'], s['chosen_seat_id'])]
        if not eligible(u, pos, s['time_slot']):
            skipped['ineligible_choice'] += 1
            continue
        neg = [sid for sid in sorted(set(s['negative_seat_ids'])) if sid != s['chosen_seat_id']
               and eligible(u, seats[(s['scenario_id'], sid)], s['time_slot'])]
        if not neg:
            skipped['no_negative'] += 1
            continue
        if len(neg) > negatives:
            neg = list(rng.choice(neg, negatives, replace=False))
        ids = sorted([s['chosen_seat_id']] + neg)  # ties cannot favor positive position
        groups.append((s, ids))
    if len(groups) < 10:
        raise ValueError('Need at least 10 usable sessions for train/validation/test')
    times = np.array([datetime.fromisoformat(s['occurred_at']).timestamp() for s, _ in groups])
    cut1 = int(np.searchsorted(times, times[int(.7 * len(groups))]))
    cut2 = int(np.searchsorted(times, times[int(.85 * len(groups))]))
    if not 0 < cut1 < cut2 < len(groups):
        raise ValueError('Need distinct timestamps for chronological splits')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    sizes = np.array([len(ids) for _, ids in groups], dtype=np.int32)
    offsets = np.r_[0, np.cumsum(sizes)]
    n = int(offsets[-1])
    X = np.lib.format.open_memmap(out / 'X.npy', mode='w+', dtype='float32', shape=(n, len(NAMES)))
    y = np.zeros(n, dtype=np.int8)
    satisfaction = np.full(n, np.nan, dtype=np.float32)
    with (out / 'groups.jsonl').open('w', encoding='utf-8') as stream:
        for g, (s, ids) in enumerate(groups):
            u = users[s['user_id']]
            for j, sid in enumerate(ids, int(offsets[g])):
                seat = seats[(s['scenario_id'], sid)]
                if seat['library_id'] != s['library_id'] or seat['room_id'] != s['room_id']:
                    raise ValueError('Candidate belongs to another library or room')
                X[j] = vector(u, seat, s)
                y[j] = int(sid == s['chosen_seat_id'])
                if y[j] and s.get('satisfaction') is not None:
                    satisfaction[j] = s['satisfaction']
            stream.write(json.dumps({k: s[k] for k in ('session_id', 'user_id', 'occurred_at')}) + '\n')
    X.flush()
    for name, arr in [('y', y), ('satisfaction', satisfaction), ('offsets', offsets)]:
        np.save(out / f'{name}.npy', arr)
    meta = dict(schema_version=1, features=NAMES, seed=seed, negatives=negatives, rows=n,
                groups=len(groups), group_splits=[0, cut1, cut2, len(groups)],
                row_splits=[0, int(offsets[cut1]), int(offsets[cut2]), n], skipped=skipped,
                split='chronological 70/15/15, timestamp ties stay together',
                evaluation='sampled eligible negatives; not full available-seat ranking',
                source='mongo' if db is not None else str(Path(directory).resolve()))
    (out / 'manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return meta


def load(path):
    p = Path(path)
    meta = json.loads((p / 'manifest.json').read_text())
    if meta['features'] != NAMES or meta['schema_version'] != 1:
        raise ValueError('Incompatible feature schema; prepare dataset again')
    return meta, {k: np.load(p / f'{k}.npy', mmap_mode='r') for k in ('X', 'y', 'satisfaction', 'offsets')}
