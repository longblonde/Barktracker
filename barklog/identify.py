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

    def predict(self, feats: np.ndarray) -> list[tuple[int | None, float]]:
        """Returns (dog_id or None if unsure, confidence) per row."""
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
                best = max(votes, key=votes.get)
                conf = votes[best] / sum(votes.values())
                if d[i, idx[0]] > self.radius:
                    conf *= 0.5  # unlike anything labelled so far
                out.append((best if conf >= self.min_conf else None, round(float(conf), 3)))
            return out

    def predict_one(self, feat: np.ndarray) -> tuple[int | None, float]:
        return self.predict(feat[None, :])[0]


def retrain_and_repredict(store: Store, ident: DogIdentifier) -> dict:
    X, y = store.training_set()
    info = ident.fit(X, y)
    ids, F = store.unlabeled_features()
    if ids:
        preds = ident.predict(F)
        store.set_predictions([(p, c if p is not None else None, i) for i, (p, c) in zip(ids, preds)])
        # episodes whose barks all became not-a-bark get flagged (and vice versa)
        eps = {r["episode_id"] for r in store.q(
            "SELECT DISTINCT episode_id FROM barks WHERE label_dog_id IS NULL")}
        for e in eps:
            store.refresh_episode_flags(e)
    info["repredicted"] = len(ids)
    info["not_bark_examples"] = int((y == NOT_BARK).sum()) if len(y) else 0
    return info
