"""Clip files: streaming WAV writer, retention, and disk-cap enforcement."""
from __future__ import annotations

import logging
import os
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from . import features as F

log = logging.getLogger(__name__)


class ClipWriter:
    def __init__(self, path: Path, max_seconds: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_samples = int(max_seconds * F.SR)
        self.samples = 0
        self._w = wave.open(str(path), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(F.SR)

    def write(self, x: np.ndarray) -> None:
        if self._w is None or self.samples >= self.max_samples:
            return
        x = x[: self.max_samples - self.samples]
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
        self._w.writeframes(pcm.tobytes())
        self.samples += len(x)

    @property
    def seconds(self) -> float:
        return self.samples / F.SR

    def close(self) -> None:
        if self._w is not None:
            self._w.close()
            self._w = None


def clip_path_for(clips_dir: Path, ts: float) -> Path:
    d = datetime.fromtimestamp(ts)
    return clips_dir / d.strftime("%Y-%m-%d") / (d.strftime("bark_%Y%m%d_%H%M%S_")
                                                  + f"{int((ts % 1) * 1000):03d}.wav")


def enforce_retention(store, clips_dir: Path, retention_days: int, max_disk_mb: int) -> int:
    """Deletes clip audio (episode stats are kept) older than retention, then oldest-first over the cap."""
    removed = 0
    if retention_days and retention_days > 0:
        for row in store.old_clips(time.time() - retention_days * 86400):
            _rm(row["clip_path"])
            store.clear_clip(row["id"])
            removed += 1
    if max_disk_mb and max_disk_mb > 0:
        rows = store.q("SELECT id, clip_path FROM episodes WHERE clip_path IS NOT NULL ORDER BY start_ts")
        sizes = [(r, _size(r["clip_path"])) for r in rows]
        total = sum(s for _, s in sizes)
        cap = max_disk_mb * 1024 * 1024
        for r, s in sizes:
            if total <= cap:
                break
            _rm(r["clip_path"])
            store.clear_clip(r["id"])
            total -= s
            removed += 1
    # tidy empty day folders
    if clips_dir.exists():
        for d in clips_dir.iterdir():
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    if removed:
        log.info("Retention removed %d clips", removed)
    return removed


def _size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _rm(p: str | None) -> None:
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
