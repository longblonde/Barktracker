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
        ring_s = 10.0 + 3.0   # max pre-roll (adjustable at runtime) + classifier look-ahead
        self.ring: collections.deque = collections.deque(maxlen=int(ring_s * F.SR / FRAME) + 2)
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
        self.last_detail: dict = {}
        self.last_classify_ms = 0.0
        self.last_bark_wall = 0.0
        self.started = time.time()

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
        self.n += FRAME
        if self.floor is None:
            self.floor = level

        thr = max(self.floor + self.det.trigger_db, self.det.min_level_dbfs)
        trig = level > thr
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
            self._classify(self.pending.pop(0)[0])

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

    def _classify(self, onset: int) -> None:
        if onset < self.busy_until:
            return
        win = self._audio(onset - PRE_ONSET, onset + POST_ONSET)
        t0 = time.perf_counter()
        prob, detail = self.classifier.bark_probability(win)
        self.last_classify_ms = (time.perf_counter() - t0) * 1000
        self.last_prob, self.last_detail = prob, detail
        self.stats["classified"] += 1
        if prob < self.det.bark_threshold:
            return

        # bark extent: from the onset until the sound decays 15 dB below its peak, sinks back
        # toward the noise floor, or the next bark's attack begins
        lv = [(i, l) for i, l in self.levels if i >= onset]
        cutoff = (self.floor or -90) + 6.0
        end, pk, prev = onset + FRAME, -120.0, None
        for i, l in lv:
            if i > onset and (l < cutoff or l < pk - 15.0
                              or (prev is not None and l - prev > 6.0 and i >= onset + 2 * FRAME)):
                break
            pk, prev, end = max(pk, l), l, i + FRAME
        end = min(end, onset + int(1.5 * F.SR))
        peak = max((l for i, l in lv if i < end), default=-120.0)

        seg = self._audio(max(onset - int(0.03 * F.SR), onset - PRE_ONSET), max(end, onset + int(0.2 * F.SR)))
        feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
        dog, conf = (None, None)
        if feats is not None and self.identifier is not None and self.identifier.ready:
            dog, conf = self.identifier.predict_one(feats)
        if dog == NOT_BARK and self.det.suppress_learned_false_positives:
            self.stats["suppressed"] += 1
            log.debug("Suppressed learned false positive (conf %.2f)", conf or 0)
            return

        self.busy_until = end
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
