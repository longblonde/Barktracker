"""Dogs you haven't named yet, and how each dog barks over time.

discovered(): groups recent barks that no dog was matched to by how they sound (and, when the
direction is known, where they came from). A group that keeps turning up on several days is
probably a dog you haven't added; the Dogs page shows it with a few example barks to listen to.

rhythm(): for each dog, the pauses between its consecutive barks and how fast it barks, which
shows a dog's style (steady single woofs, rapid bursts, long howls) better than clip totals do.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from . import voice as V
from .storage import NOT_BARK, Store

LOOKBACK_DAYS = 60
MAX_BARKS = 2000              # newest unidentified barks considered (keeps it quick on a Pi)
MIN_GROUP = 12                # barks before a group is worth showing
MIN_EPISODES = 3              # ...heard in at least this many separate clips
SAMPLES = 5
PCS = 4                       # dimensions kept for grouping


def _kmeans(X: np.ndarray, k: int, seed: int = 0, iters: int = 30) -> tuple[np.ndarray, np.ndarray, float]:
    rng = np.random.default_rng(seed)
    # k-means++ start
    C = [X[rng.integers(len(X))]]
    for _ in range(1, k):
        d = np.min(((X[:, None, :] - np.array(C)[None]) ** 2).sum(-1), axis=1)
        C.append(X[rng.choice(len(X), p=d / d.sum())] if d.sum() > 0 else X[rng.integers(len(X))])
    C = np.array(C)
    for _ in range(iters):
        D = ((X[:, None, :] - C[None]) ** 2).sum(-1)
        lab = D.argmin(1)
        newC = np.array([X[lab == j].mean(0) if (lab == j).any() else C[j] for j in range(k)])
        if np.allclose(newC, C):
            break
        C = newC
    D = ((X[:, None, :] - C[None]) ** 2).sum(-1)
    lab = D.argmin(1)
    return lab, C, float(D[np.arange(len(X)), lab].sum())


def discovered(store: Store, now: float, use_direction: bool = True) -> list[dict]:
    rows = store.q(
        "SELECT b.id, b.episode_id, b.ts, b.duration, b.features, e.clip_start_ts, e.start_ts, e.clip_path"
        " FROM barks b JOIN episodes e ON e.id=b.episode_id"
        " WHERE b.label_dog_id IS NULL AND b.pred_dog_id IS NULL AND b.features IS NOT NULL"
        " AND e.false_positive=0 AND b.ts >= ? ORDER BY b.ts DESC LIMIT ?",
        (now - LOOKBACK_DAYS * 86400, MAX_BARKS))
    if len(rows) < MIN_GROUP:
        return []
    feats = np.stack([np.frombuffer(r["features"], dtype=np.float32) for r in rows])
    X = (feats - feats.mean(0)) / (feats.std(0) + 1e-3)
    bearing = {}
    if use_direction:
        try:
            ts = [r["ts"] for r in rows]
            for d in store.q("SELECT ts, bearing FROM bark_dirs WHERE valid=1 AND ts BETWEEN ? AND ?",
                             (min(ts) - 1, max(ts) + 1)):
                bearing[round(d["ts"], 1)] = d["bearing"]
        except Exception:  # noqa: BLE001
            bearing = {}
    br = np.array([_near(bearing, r["ts"]) for r in rows], dtype=float)
    if np.isfinite(br).sum() >= len(rows) * 0.5:
        # direction as two extra coordinates (unknown = middle), weighted like a few voice features
        rad = np.radians(np.where(np.isfinite(br), br, 90.0))
        X = np.hstack([X, 3.0 * np.cos(rad)[:, None], 3.0 * np.sin(rad)[:, None]])
    # keep the strongest few directions of variation, so dozens of noisy measurements don't drown
    # out the few that actually differ between dogs
    Xc = X - X.mean(0)
    _, _, vt = np.linalg.svd(Xc, full_matrices=False)
    X = Xc @ vt[:PCS].T
    # pick k by the elbow: stop when another group explains under 25% more
    best_lab, best_k, prev = np.zeros(len(X), int), 1, None
    for k in range(1, min(6, len(X) // MIN_GROUP) + 1):
        lab, _, cost = _kmeans(X, k)
        if prev is not None and cost > prev * 0.75:
            break
        best_lab, best_k, prev = lab, k, cost
    groups = []
    for j in range(best_k):
        idx = np.where(best_lab == j)[0]
        eps = {rows[i]["episode_id"] for i in idx}
        if len(idx) < MIN_GROUP or len(eps) < MIN_EPISODES:
            continue
        centre = X[idx].mean(0)
        near = idx[np.argsort(((X[idx] - centre) ** 2).sum(1))]
        samples, seen = [], set()
        for i in near:                         # examples from different clips, closest to typical
            r = rows[i]
            if r["episode_id"] in seen:
                continue
            seen.add(r["episode_id"])
            samples.append({"bark_id": r["id"], "episode_id": r["episode_id"],
                            "offset": round(r["ts"] - (r["clip_start_ts"] or r["start_ts"]), 2),
                            "duration": round(r["duration"], 2), "ts": r["ts"], "audio": bool(r["clip_path"])})
            if len(samples) >= SAMPLES:
                break
        prof = V.summarize([feats[i] for i in idx])
        b = br[idx][np.isfinite(br[idx])]
        days = {int(rows[i]["ts"] // 86400) for i in idx}
        groups.append({
            "key": f"g{min(rows[i]['id'] for i in idx)}",
            "barks": int(len(idx)), "episodes": len(eps), "days": len(days),
            "first_ts": min(rows[i]["ts"] for i in idx), "last_ts": max(rows[i]["ts"] for i in idx),
            "profile": prof,
            "bearing": round(float(np.median(b)), 1) if len(b) >= 5 else None,
            "spread": round(float(np.percentile(np.abs(b - np.median(b)), 75)), 1) if len(b) >= 5 else None,
            "samples": samples,
            "bark_ids": [int(rows[i]["id"]) for i in idx],
        })
    groups.sort(key=lambda g: -g["barks"])
    return groups


def _near(bearing: dict, ts: float) -> float:
    for d in (0.0, -0.1, 0.1, -0.2, 0.2, -0.3, 0.3):
        v = bearing.get(round(ts + d, 1))
        if v is not None:
            return v
    return float("nan")


PAUSE_BINS = [0, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8]      # seconds between barks in one clip


def rhythm(store: Store) -> dict[int, dict]:
    """Per dog: typical pause between its barks, barks per minute while barking, burst length."""
    rows = store.q(
        "SELECT b.episode_id e, b.ts, NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) d"
        " FROM barks b JOIN episodes ep ON ep.id=b.episode_id WHERE ep.false_positive=0"
        " AND COALESCE(b.label_dog_id, b.pred_dog_id) > 0 ORDER BY b.episode_id, b.ts")
    by: dict[tuple[int, int], list[float]] = {}
    for r in rows:
        if r["d"] in (None, NOT_BARK):
            continue
        by.setdefault((int(r["d"]), r["e"]), []).append(r["ts"])
    out: dict[int, dict] = {}
    pauses: dict[int, list[float]] = {}
    bursts: dict[int, list[int]] = {}
    rates: dict[int, list[float]] = {}
    for (d, _), ts in by.items():
        gaps = np.diff(ts)
        gaps = gaps[gaps < PAUSE_BINS[-1]]
        pauses.setdefault(d, []).extend(gaps.tolist())
        # a burst: barks with pauses under 1.5 s between them
        run = 1
        for g in np.diff(ts):
            if g < 1.5:
                run += 1
            else:
                bursts.setdefault(d, []).append(run)
                run = 1
        bursts.setdefault(d, []).append(run)
        if len(ts) >= 3 and ts[-1] > ts[0]:
            rates.setdefault(d, []).append(60 * (len(ts) - 1) / (ts[-1] - ts[0]))
    for d, g in pauses.items():
        if len(g) < 5:
            continue
        hist = np.histogram(g, bins=PAUSE_BINS)[0].tolist()
        bl = Counter(bursts.get(d, []))
        out[d] = {"pause_s": round(float(np.median(g)), 2), "hist": hist, "bins": PAUSE_BINS,
                  "per_min": round(float(np.median(rates[d])), 1) if rates.get(d) else None,
                  "burst": round(float(np.median(bursts.get(d, [1]))), 1),
                  "singles": round(bl.get(1, 0) / max(1, sum(bl.values())), 2), "n": len(g)}
    return out
