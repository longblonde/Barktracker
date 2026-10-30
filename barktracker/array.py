"""EXPERIMENTAL: direction of sounds from two microphones (developer test page).

Nothing here changes detection. It listens alongside the detector and, for every sound the
detector decided about, estimates where it came from:

  stereo  - the two capsules of one stereo device (e.g. a C920 webcam). Same clock, so the time
            difference of arrival (TDOA) between channels is real. Small spacing = coarse angle.
  pair    - the main mic plus a second USB mic. Two clocks, so the measured delay includes an
            unknown offset between the streams; "calibrate" with a sound from straight ahead to
            remove it (it drifts, so recalibrate now and then). The level difference between the
            mics is reported too and needs no calibration.

Angle convention (a line between mic A and mic B): 0 deg = toward mic A, 90 deg = straight
ahead/behind (front and back can't be told apart with two mics), 180 deg = toward mic B.
"""
from __future__ import annotations

import collections
import logging
import math
import threading
import time

import numpy as np

from . import features as F

log = logging.getLogger(__name__)

SPEED_OF_SOUND = 343.0           # m/s at about 20 C
BUFFER_SECONDS = 20.0
WINDOW = (-0.05, 0.35)           # seconds around a sound's onset used for the estimate
PAIR_SLACK = 0.25                # extra lag searched in pair mode (two clocks are never in step)
BAND = (300.0, 4000.0)           # bark band used for the correlation
KEEP_SECONDS = 15 * 60           # estimates older than this are dropped from memory
MAX_PER_PASS = 8                 # estimates per 0.25 s pass, so a noisy yard can't hog the Pi's CPU


def gcc_phat(a: np.ndarray, b: np.ndarray, sr: int, max_tau: float, interp: int = 16,
             band: tuple[float, float] = BAND) -> tuple[float, float]:
    """Delay of b relative to a in seconds (positive: the sound reached a first), and a peak
    sharpness score (peak / mean of |correlation|; about 1-2 = no real peak, 5+ = clear)."""
    n = len(a) + len(b)
    A = np.fft.rfft(a, n)
    B = np.fft.rfft(b, n)
    R = B * np.conj(A)
    R /= np.abs(R) + 1e-12
    f = np.fft.rfftfreq(n, 1.0 / sr)
    R[(f < band[0]) | (f > band[1])] = 0
    cc = np.fft.irfft(R, n)                              # coarse: one value per sample
    half = n // 2 - 1
    m = max(1, min(int(math.ceil(sr * max_tau)), half))
    w = max(m, min(int(sr * 0.005), half))               # judge the peak against +-5 ms of lags
    cc = np.abs(np.concatenate((cc[-w:], cc[:w + 1])))
    k = w - m + int(np.argmax(cc[w - m:w + m + 1]))
    sharp = float(cc[k] / (np.mean(cc) + 1e-12))
    # fine: evaluate the correlation only around that peak, at 1/interp of a sample, straight
    # from the spectrum (much cheaper than an interp-times longer inverse FFT)
    lag0 = k - w
    lags = lag0 + np.arange(-interp, interp + 1) / interp
    keep = np.nonzero(R)[0]
    ph = np.exp(2j * np.pi * np.outer(lags, keep) / n)
    fine = np.abs((ph * R[keep]).real.sum(axis=1))
    best = float(lags[int(np.argmax(fine))])
    best = max(-sr * max_tau, min(sr * max_tau, best))
    return best / sr, sharp


def angle_from_tau(tau: float, spacing_m: float) -> tuple[float, bool]:
    """Bearing in degrees (0 = mic A side, 180 = mic B side) and whether the delay was possible."""
    x = SPEED_OF_SOUND * tau / max(spacing_m, 1e-3)
    ok = abs(x) <= 1.1                  # a little over 1 is measurement noise; far over = bad estimate
    return math.degrees(math.acos(max(-1.0, min(1.0, x)))), ok


class _Chan:
    """Recent audio of one channel with the wall-clock time of each block."""

    def __init__(self):
        self.blocks: collections.deque = collections.deque()
        self.sr: int | None = None
        self.seconds = 0.0

    def add(self, x: np.ndarray, ts: float, sr: int) -> None:
        if self.sr != sr:
            self.blocks.clear()
            self.seconds = 0.0
            self.sr = sr
        self.blocks.append((ts, np.asarray(x, dtype=np.float32)))
        self.seconds += len(x) / sr
        while self.seconds > BUFFER_SECONDS and len(self.blocks) > 1:
            _, old = self.blocks.popleft()
            self.seconds -= len(old) / sr

    def segment(self, t0: float, t1: float) -> np.ndarray | None:
        if not self.blocks or self.sr is None:
            return None
        try:
            blocks = list(self.blocks)
        except RuntimeError:
            return None
        parts, first = [], None
        for ts, x in blocks:
            end = ts + len(x) / self.sr
            if end < t0 or ts > t1:
                continue
            if first is None:
                first = ts
            parts.append(x)
        if not parts:
            return None
        y = np.concatenate(parts)
        i0 = int(round((t0 - first) * self.sr))
        i1 = i0 + int(round((t1 - t0) * self.sr))
        if i0 < 0 or i1 > len(y):
            return None
        return y[i0:i1]

    def latest(self) -> float:
        if not self.blocks:
            return 0.0
        ts, x = self.blocks[-1]
        return ts + len(x) / (self.sr or F.SR)


SCHEMA = """
CREATE TABLE IF NOT EXISTS bark_dirs (
    ts REAL NOT NULL,
    kind TEXT,
    bearing REAL,
    clarity REAL,
    valid INTEGER NOT NULL DEFAULT 0,
    level_a REAL,
    level_b REAL,
    mode TEXT,
    spacing_cm REAL
);
CREATE INDEX IF NOT EXISTS ix_bark_dirs_ts ON bark_dirs(ts);
"""
KEEP_DAYS = 365                  # direction records kept this long (they're tiny)


def ensure_schema(store) -> None:
    with store.tx() as c:
        c.executescript(SCHEMA)


def nearest_dirs(store, stamps: list[float], within: float = 0.3) -> dict[float, dict]:
    """Direction records closest to each timestamp (barks move slightly when clips are re-scanned)."""
    if not stamps:
        return {}
    rows = store.q("SELECT ts, bearing, clarity, valid, level_a, level_b FROM bark_dirs WHERE ts BETWEEN ? AND ?",
                   (min(stamps) - within, max(stamps) + within))
    out = {}
    for t in stamps:
        best = min(rows, key=lambda r: abs(r["ts"] - t), default=None)
        if best is not None and abs(best["ts"] - t) <= within:
            out[t] = best
    return out


def dog_directions(store) -> dict[int, dict]:
    """Where each dog's barks come from: average bearing, spread and how many barks had a clear one."""
    rows = store.q(
        "SELECT NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) d, b.label_dog_id IS NOT NULL lab, bd.bearing"
        " FROM barks b JOIN bark_dirs bd ON bd.ts BETWEEN b.ts - 0.3 AND b.ts + 0.3"
        " WHERE bd.valid = 1 AND COALESCE(b.label_dog_id, b.pred_dog_id) > 0")
    by: dict[int, list] = {}
    for r in rows:
        by.setdefault(int(r["d"]), []).append((r["bearing"], r["lab"]))
    out = {}
    for d, v in by.items():
        a = np.array([x for x, _ in v])
        med = float(np.median(a))
        out[d] = {"bearing": round(med, 1), "spread": round(float(np.percentile(np.abs(a - med), 75)), 1),
                  "n": len(a), "labelled": int(sum(1 for _, l in v if l)),
                  "hist": np.histogram(a, bins=18, range=(0, 180))[0].tolist()}
    return out


class ArrayMonitor:
    def __init__(self, cfg, app=None):
        self.cfg, self.app = cfg, app
        self.a, self.b = _Chan(), _Chan()          # pair mode: main mic (mono) and second mic
        self.st_a, self.st_b = _Chan(), _Chan()    # stereo mode: the main device's two channels
        self.main_channels = 0
        self.events: collections.deque = collections.deque(maxlen=300)
        self.seq = 0
        self.done: set[int] = set()
        self.calibrating = False
        self.error: str | None = None
        self.b_name: str | None = None
        self.b_rate: int | None = None
        self._stop = threading.Event()
        self._b_restart = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def d(self):
        return self.cfg.dev

    # ---- input --------------------------------------------------------------------------
    def on_tap(self, data: np.ndarray, ts: float, sr: int) -> None:
        """Raw blocks from the main input (frames x channels), before it's mixed to mono."""
        if not self.d.array:
            return
        x = np.asarray(data, dtype=np.float32)
        if x.ndim == 1:
            x = x[:, None]
        self.main_channels = x.shape[1]
        if self.d.array_mode == "stereo":
            if x.shape[1] >= 2:
                self.st_a.add(x[:, 0], ts, sr)
                self.st_b.add(x[:, 1], ts, sr)
        else:
            self.a.add(x.mean(axis=1), ts, sr)

    def feed_b(self, x: np.ndarray, ts: float, sr: int) -> None:
        """Blocks from the second microphone (pair mode)."""
        self.b.add(np.asarray(x, dtype=np.float32).reshape(-1), ts, sr)

    # ---- threads --------------------------------------------------------------------------
    def start(self) -> None:
        for fn, name in ((self._loop, "array"), (self._second_mic, "array-mic2")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._b_restart.set()

    def reconfigure(self) -> None:
        """Settings changed: reopen the second mic and start the estimates afresh."""
        for c in (self.a, self.b, self.st_a, self.st_b):
            c.blocks.clear()
            c.seconds = 0.0
        self._b_restart.set()

    def _second_mic(self) -> None:
        while not self._stop.is_set():
            self._b_restart.clear()
            if not (self.d.array and self.d.array_mode == "pair" and self.d.array_device):
                self.b_name = None
                self._b_restart.wait(2.0)
                continue
            try:
                import sounddevice as sd
                from .audio import MicSource
                src = MicSource(self.d.array_device, F.SR, 1, 0.05, 0.0)
                dev = src._resolve_device()
                info = sd.query_devices(dev, "input")
                sr = int(info["default_samplerate"])
                try:
                    sd.check_input_settings(device=dev, samplerate=F.SR, channels=1, dtype="float32")
                    sr = F.SR
                except Exception:  # noqa: BLE001
                    pass
                self.b_name, self.b_rate, self.error = info.get("name"), sr, None

                def cb(indata, frames, t, status):
                    self.feed_b(indata[:, 0].copy(), time.time() - frames / sr, sr)
                with sd.InputStream(device=dev, samplerate=sr, channels=1, dtype="float32",
                                    blocksize=int(sr * 0.05), callback=cb):
                    log.info("Array test: second microphone %s @ %d Hz", self.b_name, sr)
                    while not (self._stop.is_set() or self._b_restart.is_set()):
                        self._b_restart.wait(0.5)
            except Exception as e:  # noqa: BLE001
                self.error = f"Second microphone: {type(e).__name__}: {e}"
                log.warning("Array test: %s", self.error)
                self._b_restart.wait(5.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(0.25)
            if self.d.array:
                self.process()

    def process(self, now: float | None = None) -> int:
        """Estimate a direction for each detector decision whose audio has fully arrived."""
        det = getattr(self.app, "detector", None)
        if det is None:
            return 0
        now = now or time.time()
        try:
            evs = list(det.events)
        except RuntimeError:
            return 0
        n = 0
        for ev in evs:
            if n >= MAX_PER_PASS:
                break                         # a burst of sounds is worked through over a few passes
            if ev["id"] in self.done or ev.get("soft"):
                continue
            if now < ev["ts"] + WINDOW[1] + 0.3:
                continue                      # wait until the sound's audio has arrived
            self.done.add(ev["id"])
            try:
                if self._estimate(ev):
                    n += 1
            except Exception:  # noqa: BLE001
                log.exception("Array estimate failed")
        # forget what's no longer needed: ids the detector has dropped, and old estimates
        self.done &= {e["id"] for e in evs}
        cutoff = now - KEEP_SECONDS
        while self.events and self.events[0]["ts"] < cutoff:
            self.events.popleft()
        if now - getattr(self, "_pruned", 0) > 86400 and getattr(self.app, "store", None) is not None:
            self._pruned = now
            with self.app.store.tx() as c:
                c.execute("DELETE FROM bark_dirs WHERE ts < ?", (now - KEEP_DAYS * 86400,))
        return n

    # ---- estimates --------------------------------------------------------------------------
    def _estimate(self, ev: dict) -> dict | None:
        mode = self.d.array_mode
        t0, t1 = ev["ts"] + WINDOW[0], ev["ts"] + WINDOW[1]
        spacing = max(0.005, self.d.array_spacing_cm / 100.0)
        if mode == "stereo":
            ca, cb, slack = self.st_a, self.st_b, 0.0
        else:
            ca, cb, slack = self.a, self.b, PAIR_SLACK
        if ca.sr is None or cb.sr is None:
            return None
        a = ca.segment(t0, t1)
        # the second stream: a wider window so its clock offset can't push the sound out of it
        if mode == "stereo":
            b = cb.segment(t0, t1)
        else:
            b = cb.segment(t0 - slack, t1 + slack)
        if a is None or b is None or len(a) < 64 or len(b) < 64:
            return None
        if cb.sr != ca.sr:
            b = F.resample(b, cb.sr) if ca.sr == F.SR else np.interp(
                np.arange(0, len(b) * ca.sr / cb.sr) * cb.sr / ca.sr, np.arange(len(b)), b).astype(np.float32)
        sr = ca.sr
        if mode == "stereo":
            raw, sharp = gcc_phat(a, b, sr, spacing / SPEED_OF_SOUND * 1.2 + 0.0002)
            tau = raw
        else:
            # b was cut `slack` earlier and later than a: line a up inside an equally long frame,
            # so a zero delay between the streams measures as zero
            a2 = np.zeros(len(b), np.float32)
            s0 = int(round(slack * sr))
            a2[s0:s0 + len(a)] = a[:max(0, len(b) - s0)]
            raw, sharp = gcc_phat(a2, b, sr, slack + spacing / SPEED_OF_SOUND * 1.2)
            tau = raw - self.d.array_offset_ms / 1000.0
        if self.d.array_swap:              # the mics are the other way round: mirror the angle
            tau = -tau
        ang, ok = angle_from_tau(tau, spacing)
        if mode == "stereo":
            bb = b[:len(a)]
        else:                                   # the same stretch of sound in the second stream
            k = max(0, int(round((slack + raw) * sr)))
            bb = b[k:k + len(a)] if len(b) >= k + len(a) // 2 else b
        la, lb = float(F.dbfs(a)), float(F.dbfs(bb))
        self.seq += 1
        out = {"n": self.seq, "id": ev["id"], "ts": ev["ts"], "kind": ev["kind"], "prob": ev.get("prob"),
               "angle": round(ang, 1), "valid": bool(ok and sharp >= 4.0), "sharp": round(sharp, 1),
               "tau_us": round(tau * 1e6, 1), "raw_us": round(raw * 1e6, 1),
               "level_a": round(la, 1), "level_b": round(lb, 1), "ild_db": round(la - lb, 1), "mode": mode}
        out["dog"] = ev.get("dog")
        self.events.append(out)
        store = getattr(self.app, "store", None)
        if store is not None:                    # kept, so dogs' usual directions can be learned
            try:
                with store.tx() as c:
                    c.execute("INSERT INTO bark_dirs(ts, kind, bearing, clarity, valid, level_a, level_b, mode, spacing_cm)"
                              " VALUES (?,?,?,?,?,?,?,?,?)",
                              (ev["ts"], ev["kind"], out["angle"], out["sharp"], int(out["valid"]),
                               out["level_a"], out["level_b"], mode, self.d.array_spacing_cm))
            except Exception:  # noqa: BLE001
                log.debug("storing direction failed", exc_info=True)
        if self.calibrating and mode == "pair" and sharp >= 4.0 and (ev.get("rise") or 0) >= 8:
            self._apply_calibration(raw)            # first clear, loud sound after pressing Calibrate
        return out

    def _apply_calibration(self, raw: float) -> None:
        """The sound just heard came from straight ahead (90 deg): whatever delay was measured is
        the offset between the two microphones' clocks."""
        self.calibrating = False
        off = round(raw * 1000.0, 3)
        if self.app is not None:
            self.app.update_settings({"dev.array_offset_ms": off})
        else:
            self.d.array_offset_ms = off
        log.info("Array test: calibrated offset %.3f ms", off)

    def status(self) -> dict:
        d = self.d
        chan = self.st_a if d.array_mode == "stereo" else self.a
        return {
            "enabled": d.array, "mode": d.array_mode, "spacing_cm": d.array_spacing_cm,
            "offset_ms": d.array_offset_ms, "swap": d.array_swap, "device_b": d.array_device,
            "main_channels": self.main_channels, "main_rate": chan.sr,
            "second_name": self.b_name, "second_rate": self.b_rate,
            "second_live": bool(self.b.blocks) and time.time() - self.b.latest() < 3.0,
            "stereo_live": bool(self.st_a.blocks) and time.time() - self.st_a.latest() < 3.0,
            "calibrating": self.calibrating, "error": self.error,
            "max_delay_us": round(d.array_spacing_cm / 100.0 / SPEED_OF_SOUND * 1e6, 1),
        }
