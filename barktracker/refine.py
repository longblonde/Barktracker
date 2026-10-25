"""Second pass over a finished episode's clip.

Live detection has to decide within about a second, with only a little audio after each trigger.
Once an episode is over the whole clip is available, so this pass:
  1. finds every bark-like attack in the clip's bark-band envelope (10 ms resolution),
  2. classifies each one on its own, with the neighbouring sound faded out so a nearby bark
     can't make a non-bark look like one,
  3. replaces the episode's barks with the confirmed ones: exact positions, real lengths,
     barks the live pass missed, and duplicates removed.
Labels you already gave are carried over to the nearest refined bark.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import features as F
from .audio import read_wav
from .classifier import WINDOW
from .storage import NOT_BARK, Store

log = logging.getLogger(__name__)


def refine_episode(store: Store, episode_id: int, classifier, identifier, threshold: float,
                   min_rise: float = 6.0) -> dict:
    ep = store.q1("SELECT * FROM episodes WHERE id=?", (episode_id,))
    if not ep:
        return {"skipped": "missing"}
    path = ep["clip_path"]
    if not path or not Path(path).exists() or ep["clip_seconds"] is None:
        store.set_episode(episode_id, refined=1)
        return {"skipped": "no clip"}
    x, sr = read_wav(path)
    x = F.resample(x, sr)
    clip_start = ep["clip_start_ts"] or ep["start_ts"]
    clip_len = len(x) / F.SR

    env = F.band_envelope(x)
    attacks = sorted(F.find_attacks(env, min_rise=min_rise), key=lambda a: a["onset"])
    starts = [max(0, F.frame_to_sample(a["onset"])) for a in attacks]
    found, last_end, scored = [], -1, []
    for idx, a in enumerate(attacks):
        on = starts[idx]
        if on < last_end - int(0.03 * F.SR):
            continue                                  # another peak inside a bark already counted
        nxt = next((s_ for s_ in starts[idx + 1:] if s_ > on + int(0.1 * F.SR)), len(x))
        end = min(len(x), F.frame_to_sample(a["end"]) + F.HOP, nxt)
        end = max(end, on + int(0.08 * F.SR))
        # Judge this sound alone, but with its natural lead-in and decay: up to 0.15 s before (never
        # back into the previous sound) and up to 0.35 s after, stopping at the next sound that's
        # about as loud (a double "woof" keeps its second pulse; a noise before a bark never
        # borrows the bark).
        prev_end = max(0, F.frame_to_sample(attacks[idx - 1]["end"]) + F.HOP) if idx else 0
        lead = max(0, min(int(0.15 * F.SR), on - prev_end))
        strong_next = next((starts[k] for k in range(idx + 1, len(attacks))
                            if starts[k] > on + int(0.1 * F.SR) and attacks[k]["peak_db"] >= a["peak_db"] - 6.0),
                           len(x))
        tail = min(len(x), end + int(0.35 * F.SR), strong_next - int(0.02 * F.SR))
        w0 = max(0, on - int(0.25 * F.SR))
        win = F.isolate(x, on, max(end, tail), pad_before=lead / F.SR, pad_after=0.0)[w0:w0 + WINDOW]
        if len(win) < WINDOW:
            win = np.pad(win, (0, WINDOW - len(win)))
        prob, _ = classifier.bark_probability(win)
        scored.append((on, end, float(prob)))
        if prob < threshold:
            continue
        last_end = end
        seg = x[max(0, on - int(0.03 * F.SR)):max(end, on + int(0.2 * F.SR))]
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        dog, conf = (None, None)
        if feats is not None and identifier is not None and identifier.ready:
            dog, conf = identifier.predict_one(feats)
        found.append({"ts": clip_start + on / F.SR, "duration": (end - on) / F.SR, "prob": float(prob),
                      "peak": F.dbfs(x[on:end]), "feats": feats, "dog": dog, "conf": conf})

    old = store.q("SELECT id, ts, label_dog_id FROM barks WHERE episode_id=? ORDER BY ts", (episode_id,))

    # Safety net: a bark the live pass already confirmed (with full context) is kept if it sits on
    # an attack that scored at least 60% of the threshold alone. Old, misplaced marks never sit on
    # an attack, so they are still replaced.
    for o in old:
        t = o["ts"] - clip_start
        if any(abs(nb["ts"] - o["ts"]) < 0.15 for nb in found):
            continue
        cand = [c for c in scored if abs(c[0] / F.SR - t) < 0.1 and c[2] >= 0.6 * threshold]
        if not cand:
            continue
        on, end, prob = max(cand, key=lambda c: c[2])
        seg = x[max(0, on - int(0.03 * F.SR)):max(end, on + int(0.2 * F.SR))]
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        dog, conf = (None, None)
        if feats is not None and identifier is not None and identifier.ready:
            dog, conf = identifier.predict_one(feats)
        found.append({"ts": clip_start + on / F.SR, "duration": (end - on) / F.SR, "prob": prob,
                      "peak": F.dbfs(x[on:end]), "feats": feats, "dog": dog, "conf": conf})
    found.sort(key=lambda b: b["ts"])
    if not found:
        # Nothing confirmed in isolation (very faint or distant barking): keep what live found.
        store.set_episode(episode_id, refined=1)
        return {"kept": len(old), "found": 0}

    # Barks past the end of a clip that hit max_clip_seconds can't be re-checked: keep those.
    keep = [o for o in old if o["ts"] - clip_start > clip_len - 0.5]
    replace = [o for o in old if o not in keep]

    # Carry labels: each labelled old mark goes to the closest new bark (old marks were often early).
    labelled = [o for o in replace if o["label_dog_id"] is not None]
    for o in labelled:
        best, gap = None, 1.2
        for nb in found:
            d = nb["ts"] - o["ts"]
            if -0.4 <= d <= 1.2 and abs(d) < gap and "label" not in nb:
                best, gap = nb, abs(d)
        if best is not None:
            best["label"] = o["label_dog_id"]

    store.replace_barks(episode_id, [o["id"] for o in replace], found)
    rows = store.barks_for(episode_id)
    counted = [r for r in rows if r["dog_id"] != NOT_BARK] or rows
    start = min(r["ts"] for r in counted)
    end = max(r["ts"] + r["duration"] for r in counted)
    store.update_episode_span(episode_id, start, end, len(rows),
                              max(r["peak_db"] or -120 for r in rows), max(r["prob"] for r in rows))
    store.refresh_episode_flags(episode_id)
    store.set_episode(episode_id, refined=1)
    return {"before": len(old), "after": len(rows), "labels_carried": sum(1 for nb in found if "label" in nb)}
