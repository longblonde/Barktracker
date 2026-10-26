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

REFINE_VERSION = 2                  # bump when this logic changes: the Pi re-scans saved clips once
OFFSETS = (0.05, 0.15, 0.25, 0.4)   # where the attack sits in the ~1 s model window; best one wins
MIN_RISE_DB = 12.0                  # a strong attack, well above the room...
MIN_DOGNESS = 0.15                  # ...that sounds like a dog counts, inside a confirmed episode
SIMILAR_CORR = 0.7                  # or one shaped like a bark already confirmed in this clip...
SIMILAR_RISE_DB = 6.0               # ...and nearly as loud as those barks (repeat barks of the same dog)
LOUDNESS_MATCH_DB = 12.0            # lenient rules only for sounds within this of the sure barks' loudness
TAIL_DB = 15.0                      # a sound right after a bark and this much quieter is its tail


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

    # ---- pass 1: score every attack on its own
    cands = []
    for idx, a in enumerate(attacks):
        on = starts[idx]
        if cands and on < cands[-1]["end"] - int(0.03 * F.SR) and a["peak_db"] <= cands[-1]["peak_db"]:
            continue                                  # a weaker peak inside the previous sound
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
        iso = F.isolate(x, on, max(end, tail), pad_before=lead / F.SR, pad_after=0.0)
        prob = dogness = 0.0
        for pre in OFFSETS:                 # the model is sensitive to where the bark sits in its window
            w0 = max(0, on - int(pre * F.SR))
            win = iso[w0:w0 + WINDOW]
            if len(win) < WINDOW:
                win = np.pad(win, (0, WINDOW - len(win)))
            p_b, p_d = classifier.dog_scores(win)
            prob, dogness = max(prob, p_b), max(dogness, p_d)
        cands.append({"a": a, "on": on, "end": end, "rise": a["peak_db"] - a["floor"],
                      "peak_db": a["peak_db"], "prob": float(prob), "dogness": float(dogness)})
    
    # ---- pass 2: decide.
    # Sure barks pass on the model alone. Distant barks over road noise often score low as "bark",
    # so a strong attack that sounds like a dog also counts, but only if it's about as loud as the
    # sure barks in this clip (a faint sound before loud barking doesn't ride along).
    sure = [c for c in cands if c["prob"] >= threshold]
    ref_rise = float(np.median([c["rise"] for c in sure])) if sure else None
    for c in cands:
        c["ok"] = c["prob"] >= threshold or (
            c["rise"] >= MIN_RISE_DB and c["dogness"] >= MIN_DOGNESS
            and (ref_rise is None or c["rise"] >= ref_rise - LOUDNESS_MATCH_DB))

    # Third chance: a dog usually repeats itself. A strong attack the model was unsure about still
    # counts if it's nearly as loud as the accepted barks and spectrally shaped like one of them
    # (compared after removing the clip's background noise).
    accepted = [c for c in cands if c["ok"]]
    if accepted and len(accepted) < len(cands):
        mel = F.power_spectrogram(x) @ F.mel_filterbank().T
        bg = np.median(mel, axis=0)

        def shape(a):
            k0, k1 = a["onset"], max(a["end"], a["onset"] + 8)
            seg = mel[k0:k1]
            loud = seg.sum(axis=1)
            v = 10 * np.log10(np.maximum(seg[loud >= np.percentile(loud, 50)].mean(axis=0) - bg, 1e-12))
            return v - v.mean()

        ref = [shape(c["a"]) for c in accepted]
        rise_ok = max(MIN_RISE_DB, float(np.median([c["rise"] for c in accepted])) - SIMILAR_RISE_DB)
        for c in cands:
            if not c["ok"] and c["rise"] >= rise_ok:
                sh = shape(c["a"])
                if max(float(np.corrcoef(sh, rf)[0, 1]) for rf in ref) >= SIMILAR_CORR:
                    c["ok"], c["prob"] = True, max(c["prob"], threshold)

    for c in cands:
        log.debug("candidate %.2fs rise=%.1fdB bark=%.2f dog=%.2f -> %s", c["on"] / F.SR, c["rise"],
                  c["prob"], c["dogness"], "bark" if c["ok"] else "no")
    found, prev = [], None
    for c in cands:
        if not c["ok"]:
            continue
        on, end = c["on"], c["end"]
        if prev is not None:
            if on < prev["_end"] - int(0.03 * F.SR):
                continue                              # overlaps a bark already counted
            if on - prev["_end"] < int(0.12 * F.SR) and c["peak_db"] <= prev["_peak"] - TAIL_DB:
                prev["_end"] = max(prev["_end"], end)  # the previous bark's tail/echo, not a new bark
                prev["duration"] = (prev["_end"] - prev["_on"]) / F.SR
                continue
        seg = x[max(0, on - int(0.03 * F.SR)):max(end, on + int(0.2 * F.SR))]
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        dog, conf = (None, None)
        if feats is not None and identifier is not None and identifier.ready:
            dog, conf = identifier.predict_one(feats)
        prev = {"ts": clip_start + on / F.SR, "duration": (end - on) / F.SR,
                "prob": float(max(c["prob"], min(threshold, c["dogness"]))),
                "peak": F.dbfs(x[on:end]), "feats": feats, "dog": dog, "conf": conf,
                "_on": on, "_end": end, "_peak": c["peak_db"]}
        found.append(prev)
    old = store.q("SELECT id, ts, label_dog_id FROM barks WHERE episode_id=? ORDER BY ts", (episode_id,))

    # Safety net: a bark the live pass already confirmed (with full context) is kept if it sits on
    # an attack that scored at least 60% of the threshold alone. Old, misplaced marks never sit on
    # an attack, so they are still replaced.
    for o in old:
        t = o["ts"] - clip_start
        if any(abs(nb["ts"] - o["ts"]) < 0.15 for nb in found):
            continue
        cand = [c for c in cands if abs(c["on"] / F.SR - t) < 0.1 and c["prob"] >= 0.6 * threshold]
        if not cand:
            continue
        best_c = max(cand, key=lambda c: c["prob"])
        on, end, prob = best_c["on"], best_c["end"], best_c["prob"]
        # same rules as above: not inside a counted bark, and not just a counted bark's tail
        if any(b["_on"] - int(0.03 * F.SR) < on < b["_end"] or
               (0 <= on - b["_end"] < int(0.12 * F.SR) and best_c["peak_db"] <= b["_peak"] - TAIL_DB)
               for b in found):
            continue
        seg = x[max(0, on - int(0.03 * F.SR)):max(end, on + int(0.2 * F.SR))]
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        dog, conf = (None, None)
        if feats is not None and identifier is not None and identifier.ready:
            dog, conf = identifier.predict_one(feats)
        found.append({"ts": clip_start + on / F.SR, "duration": (end - on) / F.SR, "prob": prob,
                      "peak": F.dbfs(x[on:end]), "feats": feats, "dog": dog, "conf": conf,
                      "_on": on, "_end": end, "_peak": best_c["peak_db"]})
    found.sort(key=lambda b: b["ts"])
    for b in found:
        for k in ("_on", "_end", "_peak"):
            b.pop(k, None)
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
