"""The detection pipeline.

  50 ms frames ──► adaptive noise floor + onset gate ──► (only when something loud happens)
  ~1 s window around the onset ──► classifier (YAMNet / heuristic) ──► bark? ──► which dog?
  barks within `episode_gap` seconds ──► one episode ──► WAV clip with pre/post-roll + DB rows

The gate keeps CPU near idle in a quiet room; the classifier only runs a few times per second
at most, even in continuous noise.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import features as F
from .classifier import WINDOW, Classifier
from .clips import ClipWriter, clip_path_for
from .config import Config
from .identify import DogIdentifier
from .storage import NOT_BARK, Store

log = logging.getLogger(__name__)

FRAME = int(0.05 * F.SR)          # 800 samples
PRE_ONSET = int(0.25 * F.SR)      # classifier window starts this long before the onset
POST_ONSET = WINDOW - PRE_ONSET   # ...and needs this much audio after it
ALT_SHIFT = int(0.2 * F.SR)       # second try with the attack placed later in the window
RING_SECONDS = 63.0               # recent audio kept in memory: "I hear barking" saves up to 60 s
NEAR_DB = 6.0                     # a sound this far over the background that never reaches the
                                  # trigger is logged as "too quiet" for the tuning page
TUNE_ACTIVE_SECONDS = 15.0        # while the tuning page is open, quiet sounds get scored too


def _snap(dq) -> list:
    """Copy a deque another thread may be appending to."""
    for _ in range(5):
        try:
            return list(dq)
        except RuntimeError:
            time.sleep(0.002)
    return list(dq)


@dataclass
class Episode:
    id: int
    start_idx: int
    start_ts: float
    writer: ClipWriter | None
    clip_start_idx: int
    last_bark_end_idx: int
    tail: list = field(default_factory=list)       # frames not yet written to the clip
    tail_start_idx: int = 0
    barks: int = 0
    peak_db: float = -120.0
    max_prob: float = 0.0
    dogs: collections.Counter = field(default_factory=collections.Counter)


class BarkDetector:
    def __init__(self, cfg: Config, classifier: Classifier, store: Store,
                 identifier: DogIdentifier | None = None,
                 on_episode: Callable[[dict], None] | None = None):
        self.cfg, self.det, self.clipcfg = cfg, cfg.detection, cfg.clips
        self.classifier = classifier
        self.store = store
        self.identifier = identifier
        self.on_episode = on_episode
        self.ring: collections.deque = collections.deque(maxlen=int(RING_SECONDS * F.SR / FRAME) + 2)
        self.levels: collections.deque = collections.deque(maxlen=self.ring.maxlen)
        self.carry = np.zeros(0, np.float32)
        self.n = 0                     # absolute index of the next sample to be framed
        self.base_ts: float | None = None
        self.received = 0
        self.floor: float | None = None
        self.prev_level = -120.0
        self.prev_trig = False
        self.pending: list[int] = []
        self.last_sched = -10 ** 12
        self.busy_until = -10 ** 12
        self.ep: Episode | None = None
        self.lock = threading.Lock()
        self.stats = collections.Counter()
        self.history: collections.deque = collections.deque(maxlen=200)   # (ts, level, floor)
        self.eq = np.zeros(F.EQ_BANDS, np.float32)
        self.last_prob = 0.0
        self.heard: tuple[float, str, float] | None = None   # (wall time, model label, score)
        self.last_detail: dict = {}
        self.last_classify_ms = 0.0
        self.last_bark_wall = 0.0
        self.started = time.time()
        # tuning page: per-frame spectrogram columns and level/background, and what was decided
        self.spec: collections.deque = collections.deque(maxlen=self.ring.maxlen)    # (idx, uint8 col)
        self.trace: collections.deque = collections.deque(maxlen=self.ring.maxlen)   # (idx, level, floor)
        self.events: collections.deque = collections.deque(maxlen=800)
        self.event_seq = 0
        self.near: list | None = None          # [start idx, peak level, floor at start] of a quiet sound
        self.in_loud = False
        self.near_pending: list[dict] = []
        self.last_near_scored = -10 ** 12
        self.last_near_end = -10 ** 12
        self.tune_until = 0.0

    # ------------------------------------------------------------------ input
    def ts_of(self, idx: int) -> float:
        return (self.base_ts or 0.0) + idx / F.SR

    def feed(self, x: np.ndarray, ts: float) -> None:
        """x: 16 kHz mono float32; ts: wall-clock time of x[0]."""
        if self.base_ts is None:
            self.base_ts = ts
        expected = self.base_ts + self.received / F.SR
        if abs(ts - expected) > 2.0:   # clock jump / long overflow: re-anchor
            log.warning("Audio clock drifted %.1fs; re-anchoring", ts - expected)
            self.base_ts = ts - self.received / F.SR
        self.received += len(x)
        buf = np.concatenate([self.carry, x]) if len(self.carry) else x
        nfull = len(buf) // FRAME
        for i in range(nfull):
            self._frame(buf[i * FRAME:(i + 1) * FRAME])
        self.carry = buf[nfull * FRAME:].copy()

    def flush(self) -> None:
        """Process whatever is pending and close any open episode (end of file / shutdown)."""
        silence = np.zeros(FRAME, np.float32)
        for _ in range(int(POST_ONSET / FRAME) + 2):
            self._frame(silence * 0)
        if self.ep:
            self._close_episode()

    # ------------------------------------------------------------- per frame
    def _frame(self, frame: np.ndarray) -> None:
        idx = self.n
        level = F.dbfs(frame)
        # live EQ for the dashboard: instant rise, quick decay so 20 Hz readers don't miss peaks
        self.eq = np.maximum(F.eq_bands(frame), self.eq * 0.55)
        self.ring.append((idx, frame))
        self.levels.append((idx, level))
        self.spec.append((idx, F.spec_column(frame)))
        self.n += FRAME
        if self.floor is None:
            self.floor = level
        self.trace.append((idx, round(level, 1), round(self.floor, 1)))

        thr = max(self.floor + self.det.trigger_db, self.det.min_level_dbfs)
        trig = level > thr
        self._track_near(idx, level, trig)
        a = self.det.noise_floor_adapt
        if not trig:
            rate = 0.1 if level < self.floor else a * 0.25
        else:
            rate = a * 0.025          # creep up very slowly under sustained loud noise (fan, rain)
        self.floor += rate * (level - self.floor)

        # Hard onsets are real attacks (silence->loud, or a >6 dB jump). Soft onsets are periodic
        # re-checks during sustained loud sound; a hard onset arriving soon after replaces one.
        rising = trig and (not self.prev_trig or level - self.prev_level > 6.0)
        since = (idx - self.last_sched) / F.SR
        if rising:
            if self.pending and self.pending[-1][1] and idx - self.pending[-1][0] < int(0.6 * F.SR):
                self.pending[-1] = [idx, False]
                self.last_sched = idx
            elif since >= self.det.min_bark_interval:
                self.pending.append([idx, False])
                self.last_sched = idx
        elif trig and since >= max(0.6, self.det.classify_cooldown):
            self.pending.append([idx, True])
            self.last_sched = idx
        self.prev_trig, self.prev_level = trig, level

        if self.ep is not None:
            if not self.ep.tail:
                self.ep.tail_start_idx = idx
            self.ep.tail.append(frame)

        # classify any onsets that now have enough audio after them
        while self.pending and self.n >= self.pending[0][0] + POST_ONSET:
            p = self.pending.pop(0)
            self._classify(p[0], soft=p[1])
        while self.near_pending and self.n >= self.near_pending[0]["idx"] + POST_ONSET:
            self._score_near(self.near_pending.pop(0))

        if (self.ep is not None and not self.pending
                and (self.n - self.ep.last_bark_end_idx) / F.SR > self.det.episode_gap):
            self._close_episode()

        if idx % (FRAME * 2) == 0:
            self.history.append((round(self.ts_of(idx), 2), round(level, 1), round(self.floor, 1)))
        self.stats["frames"] += 1

    def _audio(self, start: int, end: int) -> np.ndarray:
        parts = [f for i, f in self.ring if i + FRAME > start and i < end]
        if not parts:
            return np.zeros(0, np.float32)
        first = next(i for i, f in self.ring if i + FRAME > start and i < end)
        x = np.concatenate(parts)
        return x[max(0, start - first): max(0, start - first) + (end - start)]

    # ---------------------------------------------------- tuning page support
    def tuning_active(self) -> bool:
        return time.time() < self.tune_until

    def _event(self, idx: int, kind: str, **kw) -> dict:
        """Record a decision for the tuning page (and for 'I hear barking' captures)."""
        self.event_seq += 1
        ev = {"id": self.event_seq, "idx": idx, "ts": round(self.ts_of(idx), 3), "kind": kind,
              "trigger_db": self.det.trigger_db, "min_level": self.det.min_level_dbfs,
              "threshold": self.det.bark_threshold, **kw}
        self.events.append(ev)
        return ev

    def _sound_stats(self, onset: int) -> tuple[float, float]:
        """Peak level over the next ~0.5 s, and the background just before (what the gate compared)."""
        peak = max((l for i, l in self.levels if onset - FRAME <= i < onset + int(0.5 * F.SR)), default=-120.0)
        fl = next((f for i, _, f in reversed(self.trace) if i <= onset), self.floor or -90.0)
        return round(float(peak), 1), round(float(fl), 1)

    def _track_near(self, idx: int, level: float, trig: bool) -> None:
        """Sounds that rise over the background but never reach the trigger: the barks a too-strict
        trigger misses. Logged once each, when the sound ends."""
        near = level > (self.floor or level) + NEAR_DB
        if trig:
            self.in_loud, self.near = True, None
            return
        if not near:
            self.in_loud = False
            if self.near is not None:
                start, peak, fl = self.near
                self.near = None
                # at least 100 ms long (noise flickers are shorter than a bark), at most 3 s
                if 2 * FRAME <= idx - start and (idx - start) / F.SR <= 3.0 and start - self.last_near_end >= int(0.3 * F.SR):
                    self.last_near_end = idx
                    reason = "below_min_level" if peak <= self.det.min_level_dbfs else "below_trigger"
                    ev = {"idx": start, "peak": round(peak, 1), "floor": round(fl, 1), "rise": round(peak - fl, 1),
                          "reason": reason}
                    if self.tuning_active():
                        self.near_pending.append(ev)        # score it with the model first
                    else:
                        self._event(start, "quiet", **{k: v for k, v in ev.items() if k != "idx"})
            return
        if self.in_loud:
            return                                          # the tail of a sound that did trigger
        if self.near is None:
            self.near = [idx, level, self.floor]
        else:
            self.near[1] = max(self.near[1], level)

    def _score_near(self, ev: dict) -> None:
        idx = ev.pop("idx")
        if idx - self.last_near_scored >= int(0.3 * F.SR):
            self.last_near_scored = idx
            win = self._audio(idx - PRE_ONSET, idx + POST_ONSET)
            if len(win) >= WINDOW // 2:
                prob, detail = self.classifier.bark_probability(win)
                alt = self._audio(idx - PRE_ONSET - ALT_SHIFT, idx + POST_ONSET - ALT_SHIFT)
                if len(alt) >= WINDOW // 2:
                    p2, d2 = self.classifier.bark_probability(alt)
                    if p2 > prob:
                        prob, detail = p2, d2
                ev.update(prob=round(prob, 3), other=detail.get("other"),
                          other_score=round(detail.get("other_score", 0.0), 2))
        self._event(idx, "quiet", **ev)

    def capture(self, seconds: float = 30.0) -> dict:
        """The last `seconds` of audio plus the levels and decisions over it ("I hear barking")."""
        snap = _snap
        frames = snap(self.ring)
        if not frames:
            return {"audio": np.zeros(0, np.float32), "start_ts": time.time(), "trace": [], "events": []}
        want = int(min(seconds, RING_SECONDS - 3) * F.SR / FRAME)
        frames = frames[-want:]
        i0 = frames[0][0]
        trace = [(round((i - i0) / F.SR, 2), l, f) for i, l, f in snap(self.trace) if i >= i0]
        events = [{**e, "t": round((e["idx"] - i0) / F.SR, 3)} for e in snap(self.events) if e["idx"] >= i0]
        return {"audio": np.concatenate([f for _, f in frames]), "start_ts": self.ts_of(i0),
                "trace": trace, "events": events}

    def tune_feed(self, since_idx: int, since_event: int) -> dict:
        """New spectrogram columns, levels and decisions for the tuning page's live view."""
        self.tune_until = time.time() + TUNE_ACTIVE_SECONDS
        cols = [(i, c) for i, c in _snap(self.spec) if i > since_idx]
        tr = {i: (l, f) for i, l, f in _snap(self.trace) if i > since_idx}
        return {
            "first_idx": cols[0][0] if cols else None, "last_idx": cols[-1][0] if cols else since_idx,
            "cols": b"".join(c.tobytes() for _, c in cols),
            "levels": [tr.get(i, (None, None)) for i, _ in cols],
            "events": [e for e in _snap(self.events) if e["id"] > since_event],
            "base_ts": self.base_ts, "frame": FRAME, "sr": F.SR,
            "floor": round(self.floor or -90, 1),
            "trigger_level": round(max((self.floor or -90) + self.det.trigger_db, self.det.min_level_dbfs), 1),
            "settings": {"trigger_db": self.det.trigger_db, "min_level": self.det.min_level_dbfs,
                         "threshold": self.det.bark_threshold},
        }

    def _classify(self, onset: int, soft: bool = False) -> None:
        if onset < self.busy_until:
            return
        win = self._audio(onset - PRE_ONSET, onset + POST_ONSET)
        t0 = time.perf_counter()
        prob, detail = self.classifier.bark_probability(win)
        if prob < self.det.bark_threshold:
            # the model is sensitive to where the bark sits in its window: try it a little later
            alt = self._audio(onset - PRE_ONSET - ALT_SHIFT, onset + POST_ONSET - ALT_SHIFT)
            p2, d2 = self.classifier.bark_probability(alt)
            if p2 > prob:
                prob, detail = p2, d2
        self.last_classify_ms = (time.perf_counter() - t0) * 1000
        self.last_prob, self.last_detail = prob, detail
        top, score = detail.get("other"), detail.get("other_score")
        if top and score is not None:
            self.heard = (time.time(), top, float(score))   # what else the model recognised (live band note)
        self.stats["classified"] += 1
        peak0, floor0 = self._sound_stats(onset)
        info = {"prob": round(prob, 3), "peak": peak0, "floor": floor0, "rise": round(peak0 - floor0, 1),
                "other": detail.get("other"), "other_score": round(detail.get("other_score", 0.0), 2),
                "soft": soft}
        if prob < self.det.bark_threshold:
            self._event(onset, "not_bark", **info)
            return

        # The loudness trigger often fires on noise just before the bark, and the classifier only
        # says "there's a bark somewhere in this second". Pin the bark to its actual attack: the
        # strongest bark-band attack near the trigger, at 10 ms resolution.
        located = self._locate(win, onset)
        if located is None:
            return
        onset, end = located
        if onset < self.busy_until:          # same bark as one already counted
            return
        lv = [(i, l) for i, l in self.levels if onset - FRAME < i < end]
        peak = max((l for _, l in lv), default=F.dbfs(self._audio(onset, end)))

        seg = self._audio(max(onset - int(0.03 * F.SR), onset - PRE_ONSET), max(end, onset + int(0.2 * F.SR)))
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        dog, conf = (None, None)
        if feats is not None and self.identifier is not None and self.identifier.ready:
            dog, conf = self.identifier.predict_one(feats)
        if dog == NOT_BARK and self.det.suppress_learned_false_positives:
            self._event(onset, "filtered", **info, conf=round(conf or 0, 2))
            self.stats["suppressed"] += 1
            log.debug("Suppressed learned false positive (conf %.2f)", conf or 0)
            return

        self.busy_until = end
        self._event(onset, "bark", **info, dog=dog, conf=round(conf, 2) if conf is not None else None)
        self.stats["barks"] += 1
        self.last_bark_wall = time.time()
        if self.ep is None:
            self._open_episode(onset)
        ep = self.ep
        # flush everything buffered so far into the clip (barks keep the tail alive)
        if ep.writer and ep.tail:
            ep.writer.write(np.concatenate(ep.tail))
        ep.tail = []
        ep.barks += 1
        ep.last_bark_end_idx = max(ep.last_bark_end_idx, end)
        ep.peak_db = max(ep.peak_db, peak)
        ep.max_prob = max(ep.max_prob, prob)
        ep.dogs[dog] += 1
        self.store.add_bark(ep.id, self.ts_of(onset), (end - onset) / F.SR, prob, peak, feats, dog,
                            conf if dog is not None else None)
        self.store.finish_episode(ep.id, self.ts_of(ep.last_bark_end_idx), ep.barks, ep.peak_db,
                                  ep.max_prob, None)

    def _locate(self, win: np.ndarray, onset: int) -> tuple[int, int] | None:
        """Absolute (onset, end) sample indices of the bark inside a classifier window."""
        w0 = onset - PRE_ONSET
        env = F.band_envelope(win)
        attacks = F.find_attacks(env, min_rise=6.0)
        lo, hi = PRE_ONSET - int(0.15 * F.SR), PRE_ONSET + int(0.65 * F.SR)
        fresh = [a for a in attacks if w0 + F.frame_to_sample(a["onset"]) >= self.busy_until]
        near = [a for a in fresh if lo <= F.frame_to_sample(a["onset"]) <= hi] or fresh
        if not near:
            return (onset, onset + int(0.2 * F.SR)) if not attacks else None
        # the earliest attack that's nearly as strong as the strongest one: when two barks share
        # a window, the first is counted now and the second gets its own trigger
        top = max(a["peak_db"] for a in near)
        best = next(a for a in near if a["peak_db"] >= top - 10.0)
        a_on = w0 + max(0, F.frame_to_sample(best["onset"]))
        a_end = w0 + F.frame_to_sample(best["end"]) + F.HOP
        return a_on, max(a_on + int(0.08 * F.SR), min(a_end, a_on + int(1.5 * F.SR)))

    # -------------------------------------------------------------- episodes
    def _open_episode(self, onset: int) -> None:
        start_ts = self.ts_of(onset)
        clip_start = max(onset - int(self.clipcfg.preroll * F.SR), self.ring[0][0])
        path: Path | None = clip_path_for(self.cfg.clips_path, start_ts)
        writer = None
        try:
            writer = ClipWriter(path, self.clipcfg.max_clip_seconds)
            writer.write(self._audio(clip_start, self.n))
        except OSError as e:
            log.error("Could not write clip %s: %s", path, e)
            path = None
        ep_id = self.store.create_episode(start_ts, str(path) if path else None, self.ts_of(clip_start))
        self.ep = Episode(id=ep_id, start_idx=onset, start_ts=start_ts, writer=writer,
                          clip_start_idx=clip_start, last_bark_end_idx=onset)
        log.info("Barking started (episode %d)", ep_id)

    def _close_episode(self) -> None:
        ep, self.ep = self.ep, None
        if ep is None:
            return
        if ep.writer:
            if ep.tail:
                tail = np.concatenate(ep.tail)
                keep = ep.last_bark_end_idx + int(self.clipcfg.postroll * F.SR) - ep.tail_start_idx
                ep.writer.write(tail[:max(0, keep)])
            ep.writer.close()
        if ep.barks < self.det.min_episode_barks:
            path = self.store.delete_episode(ep.id)
            if path:
                Path(path).unlink(missing_ok=True)
            return
        end_ts = self.ts_of(ep.last_bark_end_idx)
        clip_s = ep.writer.seconds if ep.writer else None
        self.store.finish_episode(ep.id, end_ts, ep.barks, ep.peak_db, ep.max_prob, clip_s)
        self.stats["episodes"] += 1
        info = {"id": ep.id, "start_ts": ep.start_ts, "end_ts": end_ts,
                "duration": end_ts - ep.start_ts, "barks": ep.barks,
                "dogs": {str(k): v for k, v in ep.dogs.items()}}
        log.info("Barking ended (episode %d): %d barks over %.1fs", ep.id, ep.barks, info["duration"])
        if self.on_episode:
            try:
                self.on_episode(info)
            except Exception:  # noqa: BLE001
                log.exception("on_episode callback failed")

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        ep = self.ep
        return {
            "classifier": self.classifier.name,
            "level_db": round(self.prev_level, 1),
            "floor_db": round(self.floor, 1) if self.floor is not None else None,
            "trigger_db": round(max((self.floor or -90) + self.det.trigger_db, self.det.min_level_dbfs), 1),
            "last_prob": round(self.last_prob, 3),
            "last_detail": self.last_detail,
            "classify_ms": round(self.last_classify_ms, 1),
            "barking_now": ep is not None,
            "current_episode": None if ep is None else {
                "id": ep.id, "start_ts": ep.start_ts, "barks": ep.barks},
            "stats": dict(self.stats),
            "history": list(self.history)[-120:],
            "uptime_s": round(time.time() - self.started),
            "identifier_ready": bool(self.identifier and self.identifier.ready),
        }


def run_source(detector: BarkDetector, source, stop: threading.Event | None = None) -> None:
    for x, ts in source:
        if stop is not None and stop.is_set():
            break
        detector.feed(x, ts)
    detector.flush()
