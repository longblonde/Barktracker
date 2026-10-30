"""Which dog barked? A small k-nearest-neighbour model trained on barks you label in the dashboard.

Labels are per bark (labelling an episode labels all its barks). The special class NOT_BARK (0)
lets you teach it about recurring false positives (a squeaky gate, the neighbour's door).
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from .storage import NOT_BARK, Store

log = logging.getLogger(__name__)


class DogIdentifier:
    def __init__(self, k: int = 7, min_labels: int = 3, min_conf: float = 0.55):
        self.k, self.min_labels, self.min_conf = k, min_labels, min_conf
        self._lock = threading.Lock()
        self.X: np.ndarray | None = None
        self.y: np.ndarray | None = None
        self.mu = self.sd = None
        self.radius = np.inf
        self.classes: dict[int, int] = {}

    @property
    def ready(self) -> bool:
        return self.X is not None and len(self.classes) >= 1

    def fit(self, X: np.ndarray, y: np.ndarray) -> dict:
        with self._lock:
            if len(X) == 0:
                self.X = self.y = None
                self.classes = {}
                return {"trained": False, "reason": "no labels yet"}
            counts = {int(c): int((y == c).sum()) for c in np.unique(y)}
            keep = np.array([counts[int(c)] >= self.min_labels for c in y])
            if keep.sum() == 0:
                self.X = self.y = None
                self.classes = {}
                return {"trained": False, "reason": f"need {self.min_labels}+ labelled barks per dog",
                        "counts": counts}
            X, y = X[keep], y[keep]
            self.mu = X.mean(axis=0)
            self.sd = X.std(axis=0) + 1e-3
            self.X = (X - self.mu) / self.sd
            self.y = y
            self.classes = {int(c): int((y == c).sum()) for c in np.unique(y)}
            # "too far from anything we've seen" radius: 95th pct of within-class NN distance x 1.6
            if len(self.X) > 1:
                d = self._dist(self.X, self.X)
                np.fill_diagonal(d, np.inf)
                same = (y[:, None] == y[None, :])
                d_same = np.where(same, d, np.inf).min(axis=1)
                finite = d_same[np.isfinite(d_same)]
                self.radius = float(np.percentile(finite, 95) * 1.6) if len(finite) else np.inf
            else:
                self.radius = np.inf
            log.info("Dog identifier trained on %d barks, classes=%s", len(y), self.classes)
            return {"trained": True, "counts": self.classes}

    @staticmethod
    def _dist(A: np.ndarray, B: np.ndarray) -> np.ndarray:
        aa = (A * A).sum(1)[:, None]
        bb = (B * B).sum(1)[None, :]
        return np.sqrt(np.maximum(aa + bb - 2 * A @ B.T, 0))

    def predict(self, feats: np.ndarray, priors: list[dict | None] | None = None) -> list[tuple[int | None, float]]:
        """Returns (dog_id or None if unsure, confidence) per row. `priors` (one per row, or None)
        weighs each dog's vote, e.g. by how well the bark's direction fits where that dog barks."""
        with self._lock:
            if self.X is None or len(feats) == 0:
                return [(None, 0.0)] * len(feats)
            Q = (np.atleast_2d(feats) - self.mu) / self.sd
            d = self._dist(Q, self.X)
            k = min(self.k, len(self.X))
            nn = np.argsort(d, axis=1)[:, :k]
            out = []
            for i in range(len(Q)):
                idx = nn[i]
                w = 1.0 / (d[i, idx] + 1e-3)
                votes: dict[int, float] = {}
                for j, wj in zip(idx, w):
                    votes[int(self.y[j])] = votes.get(int(self.y[j]), 0.0) + float(wj)
                pr = priors[i] if priors is not None else None
                if pr:
                    votes = {d: v * pr.get(d, pr.get("other", 1.0)) for d, v in votes.items()}
                    if not sum(votes.values()):
                        out.append((None, 0.0))
                        continue
                best = max(votes, key=votes.get)
                conf = votes[best] / sum(votes.values())
                if d[i, idx[0]] > self.radius:
                    conf *= 0.5  # unlike anything labelled so far
                out.append((best if conf >= self.min_conf else None, round(float(conf), 3)))
            return out

    def predict_one(self, feat: np.ndarray) -> tuple[int | None, float]:
        return self.predict(feat[None, :])[0]


MIN_DIR_BARKS = 5          # confirmed barks with a clear direction before a dog's direction counts


def direction_priors(store: Store, ids: list[int]) -> list[dict | None] | None:
    """For each bark: how well its direction fits where each dog usually barks from (learned only
    from barks you labelled, so a wrong guess can't teach itself). None when there's nothing to go on."""
    try:
        prof = store.q(
            "SELECT b.label_dog_id d, bd.bearing FROM barks b JOIN bark_dirs bd"
            " ON bd.ts BETWEEN b.ts - 0.3 AND b.ts + 0.3 WHERE bd.valid=1 AND b.label_dog_id > 0")
    except Exception:  # noqa: BLE001   no direction table yet
        return None
    by: dict[int, list[float]] = {}
    for r in prof:
        by.setdefault(int(r["d"]), []).append(r["bearing"])
    model = {}
    for d, v in by.items():
        if len(v) >= MIN_DIR_BARKS:
            a = np.array(v)
            med = float(np.median(a))
            spread = float(np.percentile(np.abs(a - med), 75))
            model[d] = (med, max(8.0, 1.5 * spread))
    if not model or not ids:
        return None
    rows = store.q("SELECT b.id, bd.bearing FROM barks b JOIN bark_dirs bd ON bd.ts BETWEEN b.ts - 0.3 AND b.ts + 0.3"
                   f" WHERE bd.valid=1 AND b.id IN ({','.join('?' * len(ids))})", ids)
    bearing = {r["id"]: r["bearing"] for r in rows}
    out: list[dict | None] = []
    for i in ids:
        b = bearing.get(i)
        if b is None:
            out.append(None)
            continue
        pr: dict = {d: 0.15 + 0.85 * float(np.exp(-0.5 * ((b - m) / sg) ** 2)) for d, (m, sg) in model.items()}
        pr["other"] = 0.5                       # dogs whose direction isn't known yet
        out.append(pr)
    return out


def retrain_and_repredict(store: Store, ident: DogIdentifier, use_direction: bool = False) -> dict:
    X, y = store.training_set()
    info = ident.fit(X, y)
    ids, F = store.unlabeled_features()
    if ids:
        priors = direction_priors(store, ids) if use_direction else None
        preds = ident.predict(F, priors)
        store.set_predictions([(p, c if p is not None else None, i) for i, (p, c) in zip(ids, preds)])
        # episodes whose barks all became not-a-bark get flagged (and vice versa)
        eps = {r["episode_id"] for r in store.q(
            "SELECT DISTINCT episode_id FROM barks WHERE label_dog_id IS NULL")}
        for e in eps:
            store.refresh_episode_flags(e)
    info["repredicted"] = len(ids)
    info["not_bark_examples"] = int((y == NOT_BARK).sum()) if len(y) else 0
    return info
