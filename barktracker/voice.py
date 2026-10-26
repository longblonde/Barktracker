"""Per-dog voice profiles and a rough size guess from a bark.

Every logged bark stores a compact descriptor (see features.bark_features): its spectral shape,
brightness (centroid), pitch, how tonal it is, and its length. A dog's profile is the typical
value of those over the barks you've confirmed for it.

Size from sound is only a hint: bigger dogs tend to bark lower, but breed, mood, distance and the
microphone all move it around. It's used to *suggest* a dog for barks before that dog has enough
labels, never to label anything by itself.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from . import features as F
from .storage import NOT_BARK, Store

SIZES = ("small", "medium", "large")
# Rough bark pitch / brightness bands by dog size (fundamental and spectral centroid, Hz).
PITCH_LARGE_BELOW, PITCH_SMALL_ABOVE = 420.0, 680.0
BRIGHT_LARGE_BELOW, BRIGHT_SMALL_ABOVE = 1150.0, 1900.0


def decode(feat: np.ndarray) -> dict:
    """Human-readable numbers from a stored bark descriptor."""
    n = F.N_MELS
    extra = feat[2 * n:2 * n + 8]
    return {
        "shape": feat[:n],                         # loudness-normalised mel spectrum (dB)
        "brightness_hz": float(np.expm1(extra[0])),
        "pitch_hz": float(np.expm1(extra[4])),
        "tonal": float(extra[5]),                  # 0 = noisy / raspy, 1 = clear tone
        "length_s": float(extra[6]),
    }


def size_guess(feat: np.ndarray) -> str:
    v = decode(feat)
    if v["tonal"] >= 0.35 and 0 < v["pitch_hz"] < 1200:   # clear pitch (very high = likely an octave error)
        p = v["pitch_hz"]
        return "large" if p < PITCH_LARGE_BELOW else "small" if p > PITCH_SMALL_ABOVE else "medium"
    b = v["brightness_hz"]                             # raspy bark: go by brightness instead
    return "large" if b < BRIGHT_LARGE_BELOW else "small" if b > BRIGHT_SMALL_ABOVE else "medium"


def summarize(feats: list[np.ndarray]) -> dict | None:
    if not feats:
        return None
    vals = [decode(f) for f in feats]
    shape = np.mean([v["shape"] for v in vals], axis=0)
    sizes = Counter(size_guess(f) for f in feats)
    size, n = sizes.most_common(1)[0]
    return {
        "barks": len(feats),
        "pitch_hz": round(float(np.median([v["pitch_hz"] for v in vals]))),
        "brightness_hz": round(float(np.median([v["brightness_hz"] for v in vals]))),
        "length_s": round(float(np.median([v["length_s"] for v in vals])), 2),
        "tonal": round(float(np.median([v["tonal"] for v in vals])), 2),
        "shape": [round(float(x), 1) for x in (shape - shape.max())],   # 0 dB at the strongest band
        "sounds_like": size, "sounds_like_share": round(n / len(feats), 2),
    }


def _features_by_dog(store: Store, labelled_only: bool = True) -> dict[int, list[np.ndarray]]:
    col = "label_dog_id" if labelled_only else "COALESCE(label_dog_id, pred_dog_id)"
    rows = store.conn.execute(
        f"SELECT {col} d, features FROM barks WHERE {col} IS NOT NULL AND {col} != ? AND features IS NOT NULL",
        (NOT_BARK,)).fetchall()
    out: dict[int, list[np.ndarray]] = {}
    for d, blob in rows:
        out.setdefault(int(d), []).append(np.frombuffer(blob, dtype=np.float32))
    return out


def separation(by_dog: dict[int, list[np.ndarray]], k: int = 5, min_each: int = 3) -> dict:
    """Leave-one-out check on your own labels: for each confirmed bark, would the others have
    matched it to the right dog? Returns per-dog accuracy and who it gets confused with."""
    dogs = [d for d, fs in by_dog.items() if len(fs) >= min_each]
    if len(dogs) < 2:
        return {}
    X = np.stack([f for d in dogs for f in by_dog[d]])
    y = np.array([d for d in dogs for _ in by_dog[d]])
    X = (X - X.mean(0)) / (X.std(0) + 1e-3)
    D = np.sqrt(np.maximum((X * X).sum(1)[:, None] + (X * X).sum(1)[None, :] - 2 * X @ X.T, 0))
    np.fill_diagonal(D, np.inf)
    kk = min(k, len(X) - 1)
    out = {}
    for d in dogs:
        idx = np.where(y == d)[0]
        right, mixed = 0, Counter()
        for i in idx:
            nn = np.argsort(D[i])[:kk]
            votes = Counter(y[nn].tolist())
            guess = votes.most_common(1)[0][0]
            if guess == d:
                right += 1
            else:
                mixed[int(guess)] += 1
        out[d] = {"accuracy": round(right / len(idx), 2),
                  "confused_with": mixed.most_common(1)[0][0] if mixed else None}
    return out


def dog_profiles(store: Store) -> dict[int, dict]:
    labelled = _features_by_dog(store, labelled_only=True)
    sep = separation(labelled)
    out = {}
    for d in store.dogs():
        prof = summarize(labelled.get(d["id"], []))
        out[d["id"]] = {"profile": prof, "separation": sep.get(d["id"])}
    return out


def episode_sound(store: Store, episode_id: int) -> dict | None:
    """What an episode's barks sound like (for suggesting a dog)."""
    rows = store.conn.execute(
        "SELECT features FROM barks WHERE episode_id=? AND features IS NOT NULL"
        " AND COALESCE(label_dog_id, -1) != ?", (episode_id, NOT_BARK)).fetchall()
    feats = [np.frombuffer(r[0], dtype=np.float32) for r in rows]
    s = summarize(feats)
    if not s:
        return None
    return {"sounds_like": s["sounds_like"], "share": s["sounds_like_share"], "pitch_hz": s["pitch_hz"],
            "brightness_hz": s["brightness_hz"]}
