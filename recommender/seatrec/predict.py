"""Rank one live room snapshot. The caller supplies current status, never historical negatives."""

from datetime import datetime, timezone
import numpy as np
from .features import eligible, vector
from .models import scores


def recommend(bundle, request, k=5, max_age_seconds=120, now=None):
    if k < 1 or max_age_seconds <= 0:
        raise ValueError("k and max_age_seconds must be positive")
    snapshot = request["snapshot"]
    updated = datetime.fromisoformat(snapshot["updated_at"])
    if updated.tzinfo is None:
        raise ValueError("snapshot.updated_at must include timezone")
    age = ((now or datetime.now(timezone.utc)) - updated).total_seconds()
    if age < -5 or age > max_age_seconds:
        raise ValueError(
            "Seat snapshot is stale or has a future timestamp; refresh before recommending"
        )
    context = request["context"]
    user = request["user"]
    candidates, seen = [], set()
    for seat in snapshot["seats"]:
        if seat["seat_id"] in seen:
            raise ValueError("Duplicate seat_id in snapshot")
        seen.add(seat["seat_id"])
        if (
            seat.get("library_id") != context["library_id"]
            or seat.get("room_id") != context["room_id"]
        ):
            continue
        if seat.get("status") != "empty" or not eligible(
            user, seat, context["time_slot"]
        ):
            continue
        candidates.append(seat)
    candidates.sort(key=lambda s: s["seat_id"])
    if not candidates:
        return []
    X = np.stack([vector(user, s, context) for s in candidates])
    values = scores(bundle, X)
    order = np.argsort(-values, kind="stable")[:k]
    return [
        dict(
            seat_id=candidates[i]["seat_id"],
            rank=rank,
            score=float(values[i]),
            preference_score=float(X[i, -1]),
            snapshot_updated_at=snapshot["updated_at"],
        )
        for rank, i in enumerate(order, 1)
    ]
