"""Allowlisted, pre-decision features shared by training and serving."""
import operator
import numpy as np

FEATURES = ('laptopAllowed', 'keyboardAllowed', 'partition', 'outletAvailable',
            'windowProximity', 'toiletProximity', 'entranceProximity', 'brightness',
            'airflow', 'deskHeight', 'deskWidth', 'viewQuality')
SLOTS = ('morning', 'noon', 'evening')
NAMES = [f'{kind}:{f}' for kind in ('seat', 'known', 'target', 'importance', 'match') for f in FEATURES]
NAMES += ['noise', 'noise_known', 'occupancy'] + [f'slot:{s}' for s in SLOTS] + ['preference_score']
OPS = {'eq': operator.eq, 'ne': operator.ne, 'gte': operator.ge, 'lte': operator.le,
       'gt': operator.gt, 'lt': operator.lt}


def seat_features(seat, slot):
    f = dict(seat.get('features', {}))
    if slot in seat.get('brightness_by_slot', {}):
        f['brightness'] = seat['brightness_by_slot'][slot]
    return f


def eligible(user, seat, slot):
    f = seat_features(seat, slot)
    for r in user.get('requirements', []):
        if r['operator'] not in OPS:
            raise ValueError(f"Unsupported requirement operator: {r['operator']}")
        v = f.get(r['featureId'])
        if v is None or not OPS[r['operator']](v, r['value']):
            return False
    return True


def vector(user, seat, context):
    slot = context['time_slot']
    if slot not in SLOTS:
        raise ValueError(f'Unknown time_slot: {slot}')
    f = seat_features(seat, slot)
    values = np.array([f.get(k) if f.get(k) is not None else .5 for k in FEATURES], dtype=np.float32)
    known = np.array([f.get(k) is not None for k in FEATURES], dtype=np.float32)
    target, weight = np.zeros(12, dtype=np.float32), np.zeros(12, dtype=np.float32)
    for p in user.get('preference_profile', {}).get('preferences', []):
        if p['featureId'] in FEATURES:
            i = FEATURES.index(p['featureId'])
            target[i], weight[i] = p['target'], p['importance']
    match = weight * known * (1 - np.abs(values - target))
    den = (weight * known).sum()
    baseline = match.sum() / den if den > 0 else .5
    noise = seat.get('raw', {}).get('noise_index')
    result = np.concatenate((values, known, target, weight, match,
                             [noise if noise is not None else .5, float(noise is not None),
                              context['occupancy_rate']], [float(slot == s) for s in SLOTS], [baseline])).astype(np.float32)
    if not np.isfinite(result).all() or (result < 0).any() or (result > 1).any():
        raise ValueError('Feature values, targets, importance and occupancy must be finite in [0, 1]')
    return result
