"""Tuning: why a sound was or wasn't counted, "I hear barking" captures, and setting suggestions.

Every sound passes three checks before it's logged as a bark:
  1. loud enough: its level rises `trigger_db` over the background, and is louder than
     `min_level_dbfs` (the gate that keeps the sound model asleep in a quiet room),
  2. bark-like: the sound model scores it at least `bark_threshold`,
  3. not a known false alarm: it doesn't match the clips you marked "Not a bark".

A capture keeps the last 15 to 60 seconds of audio with the levels the detector saw. Every sound in
it is scored the same way the detector does, so the review page can say which check stopped each
one, and recompute that for "what if" settings without re-running the model.
"""
from __future__ import annotations

import json
import logging
import math
import time
import wave
from pathlib import Path

import numpy as np

from . import features as F
from .classifier import WINDOW
from .storage import NOT_BARK, Store

log = logging.getLogger(__name__)

FRAME = int(0.05 * F.SR)
PRE_ONSET = int(0.25 * F.SR)
ALT_SHIFT = int(0.2 * F.SR)
CANDIDATE_RISE_DB = 6.0          # sounds this far over the clip's background become candidates

SCHEMA = """
CREATE TABLE IF NOT EXISTS missed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL NOT NULL,
    start_ts REAL NOT NULL,
    seconds REAL NOT NULL,
    clip_path TEXT,
    trace TEXT,
    events TEXT,
    analysis TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    episode_id INTEGER,
    marked TEXT,
    note TEXT DEFAULT ''
);
"""


# ---- the three checks ---------------------------------------------------------------------
def verdict(c: dict, s: dict) -> dict:
    """Which check (if any) stops this sound under settings `s` (trigger_db, min_level,
    threshold, suppress). Returns kind (bark | quiet | not_bark | filtered | unscored) and why."""
    peak, rise, prob = c.get("peak"), c.get("rise"), c.get("prob")
    if peak is not None and peak <= s["min_level"]:
        return {"kind": "quiet", "check": 1,
                "why": f"{peak:.0f} dBFS is quieter than the “ignore quieter than” level ({s['min_level']:.0f} dBFS)"}
    if rise is not None and rise < s["trigger_db"]:
        return {"kind": "quiet", "check": 1,
                "why": f"only {rise:.1f} dB over the background; the loudness trigger needs {s['trigger_db']:g} dB"}
    if prob is None:
        return {"kind": "unscored", "check": 2, "why": "loud enough to be checked (not scored yet)"}
    if prob < s["threshold"]:
        other = f"; it sounded most like {c['other'].split(',')[0].lower()}" if c.get("other") and (c.get("other_score") or 0) >= 0.3 else ""
        return {"kind": "not_bark", "check": 2,
                "why": f"the sound model was {prob * 100:.0f}% sure it's barking; it needs {s['threshold'] * 100:.0f}%{other}"}
    if c.get("filtered") and s.get("suppress", True):
        return {"kind": "filtered", "check": 3, "why": "it matched sounds you marked “Not a bark”"}
    return {"kind": "bark", "check": 0, "why": "counted as a bark"}


def current_settings(cfg) -> dict:
    d = cfg.detection
    return {"trigger_db": d.trigger_db, "min_level": d.min_level_dbfs, "threshold": d.bark_threshold,
            "suppress": d.suppress_learned_false_positives}


def settings_from(cfg, trigger_db=None, min_level=None, threshold=None) -> dict:
    s = current_settings(cfg)
    if trigger_db is not None:
        s["trigger_db"] = float(trigger_db)
    if min_level is not None:
        s["min_level"] = float(min_level)
    if threshold is not None:
        s["threshold"] = float(threshold)
    return s


# ---- analysing a capture ---------------------------------------------------------------------
def frame_levels(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    if not n:
        return np.zeros(0)
    fr = x[:n * FRAME].reshape(n, FRAME).astype(np.float64)
    return 10 * np.log10(np.mean(fr * fr, axis=1) + 1e-12)


def analyze(x: np.ndarray, trace: list, classifier, identifier=None, live_events: list | None = None) -> list[dict]:
    """Every sound in a capture, measured and scored the way the live detector would."""
    lv = frame_levels(x)
    if trace and len(trace) >= len(lv) * 0.8:
        fl = np.array([t[2] for t in trace], dtype=float)
        fl = np.pad(fl, (0, max(0, len(lv) - len(fl))), mode="edge")[:len(lv)]
    else:                                   # no live background trace: estimate it from the clip
        fl = np.full(len(lv), float(np.percentile(lv, 20)) if len(lv) else -90.0)
    env = F.band_envelope(x)
    attacks = sorted(F.find_attacks(env, min_rise=CANDIDATE_RISE_DB), key=lambda a: a["onset"])
    out: list[dict] = []
    for a in attacks:
        on = max(0, F.frame_to_sample(a["onset"]))
        end = min(len(x), F.frame_to_sample(a["end"]) + F.HOP)
        if out and on < int(out[-1]["end"] * F.SR) + int(0.05 * F.SR):
            continue                         # part of the previous sound
        k0, k1 = on // FRAME, max(on // FRAME + 1, min(len(lv), (on + int(0.5 * F.SR)) // FRAME + 1))
        if k0 >= len(lv):
            continue
        peak = float(lv[k0:k1].max())
        floor = float(fl[max(0, k0 - 1)])
        if peak - floor < 2.0:
            continue                         # a flicker in the band, not a sound you'd hear
        c = {"t": round(on / F.SR, 3), "end": round(max(end, on + int(0.08 * F.SR)) / F.SR, 3),
             "peak": round(peak, 1), "floor": round(floor, 1), "rise": round(peak - floor, 1)}
        if classifier is not None:
            best, detail = -1.0, {}
            for shift in (0, ALT_SHIFT):              # same two placements the live detector tries
                w0 = on - PRE_ONSET - shift
                win = x[max(0, w0):max(0, w0) + WINDOW]
                if w0 < 0:
                    win = np.concatenate([np.zeros(-w0, np.float32), x[:max(0, WINDOW + w0)]])
                if len(win) < WINDOW:
                    win = np.pad(win, (0, WINDOW - len(win)))
                p, d = classifier.bark_probability(win)
                if p > best:
                    best, detail = p, d
            c.update(prob=round(float(best), 3), other=detail.get("other"),
                     other_score=round(float(detail.get("other_score", 0.0)), 2))
        seg = x[max(0, on - int(0.03 * F.SR)):max(end, on + int(0.2 * F.SR))]
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        if feats is not None and identifier is not None and identifier.ready:
            dog, conf = identifier.predict_one(feats)
            c["filtered"] = dog == NOT_BARK
            c["dog"], c["dog_conf"] = (None if dog == NOT_BARK else dog), (round(conf, 2) if conf else None)
        if live_events:
            near = [e for e in live_events if abs(e["t"] - c["t"]) <= 0.35]
            if near:
                c["live"] = min(near, key=lambda e: abs(e["t"] - c["t"]))["kind"]
        out.append(c)
    return out


# ---- the review pool ---------------------------------------------------------------------------
def ensure_schema(store: Store) -> None:
    with store.tx() as c:
        c.executescript(SCHEMA)


def write_wav(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(F.SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def row_view(r: dict, s: dict | None = None) -> dict:
    cands = json.loads(r["analysis"]) if r.get("analysis") else None
    marked = set(json.loads(r["marked"])) if r.get("marked") else set()
    out = {"id": r["id"], "created": r["created"], "start_ts": r["start_ts"], "seconds": r["seconds"],
           "status": r["status"], "episode_id": r["episode_id"], "note": r.get("note") or "",
           "analyzed": cands is not None,
           "has_audio": bool(r["clip_path"]) and Path(r["clip_path"]).exists()}
    if cands is not None and s is not None:
        for i, c in enumerate(cands):
            c["i"] = i
            c["verdict"] = verdict(c, s)
            c["marked"] = i in marked
        out["candidates"] = cands
        out["summary"] = {k: sum(1 for c in cands if c["verdict"]["kind"] == k)
                          for k in ("bark", "quiet", "not_bark", "filtered")}
    return out


def suggest(store: Store, cfg) -> dict:
    """From the barks you marked in captures (and the sounds you didn't), the gentlest settings
    that would have counted them, and what else those settings would let through."""
    rows = store.q("SELECT analysis, marked, status FROM missed WHERE analysis IS NOT NULL"
                   " AND status IN ('added', 'dismissed')")
    barks, others = [], []
    for r in rows:
        cands = json.loads(r["analysis"])
        marked = set(json.loads(r["marked"])) if r["marked"] else set()
        for i, c in enumerate(cands):
            (barks if i in marked else others).append(c)
    cur = current_settings(cfg)
    if not barks:
        return {"barks": 0, "others": len(others), "current": cur}
    missed = [c for c in barks if verdict(c, cur)["kind"] != "bark"]
    by_check = {n: sum(1 for c in missed if verdict(c, cur)["check"] == n) for n in (1, 2, 3)}
    sug = dict(cur)
    peaks = [c["peak"] for c in missed if c.get("peak") is not None]
    rises = [c["rise"] for c in missed if c.get("rise") is not None]
    probs = [c["prob"] for c in missed if c.get("prob") is not None and c.get("rise", 99) >= 0]
    if peaks and min(peaks) <= cur["min_level"]:
        sug["min_level"] = float(max(-90, math.floor(min(peaks)) - 2))
    if rises and min(rises) < cur["trigger_db"]:
        sug["trigger_db"] = float(max(3, math.floor(min(rises) * 2) / 2 - 0.5))
    low = [p for p in probs if p < cur["threshold"]]
    if low:
        sug["threshold"] = float(max(0.1, math.floor((min(low) - 0.02) * 20) / 20))
    caught = sum(1 for c in barks if verdict(c, sug)["kind"] == "bark")
    extra = sum(1 for c in others if verdict(c, sug)["kind"] == "bark")
    extra_now = sum(1 for c in others if verdict(c, cur)["kind"] == "bark")
    return {"barks": len(barks), "others": len(others), "current": cur, "suggested": sug,
            "missed_now": len(missed), "missed_by_check": by_check,
            "caught": caught, "extra": extra, "extra_now": extra_now,
            "changed": [k for k in ("trigger_db", "min_level", "threshold") if sug[k] != cur[k]]}


def prune(store: Store, older_than_days: int) -> int:
    """Captures you never reviewed (or dismissed) are removed with the clip retention."""
    if not older_than_days:
        return 0
    cut = time.time() - older_than_days * 86400
    n = 0
    for r in store.q("SELECT id, clip_path FROM missed WHERE created < ? AND status != 'added'", (cut,)):
        if r["clip_path"]:
            Path(r["clip_path"]).unlink(missing_ok=True)
        with store.tx() as c:
            c.execute("DELETE FROM missed WHERE id=?", (r["id"],))
        n += 1
    return n
