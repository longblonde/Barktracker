"""Audio sources. Each yields (samples_float32_16k_mono, timestamp_of_first_sample)."""
from __future__ import annotations

import logging
import queue
import time
import wave
from typing import Iterator

import numpy as np

from . import features as F

log = logging.getLogger(__name__)


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def list_devices() -> str:
    import sounddevice as sd  # imported lazily: needs PortAudio
    return str(sd.query_devices())


class MicSource:
    def __init__(self, device="auto", samplerate: int = 16000, channels: int = 0,
                 block_seconds: float = 0.05, gain_db: float = 0.0):
        self.device = device
        self.sr = samplerate
        self.channels = channels
        self.block_seconds = block_seconds
        self.block = int(samplerate * block_seconds)
        self.gain = 10 ** (gain_db / 20)
        self.q: queue.Queue = queue.Queue(maxsize=2000)
        self.overflows = 0
        self.device_name = None

    # Names USB webcams/mics show up as in ALSA, best first (Logitech C920/C922/C930e/Brio, etc.)
    AUTO_PATTERNS = ("c920", "c922", "c930", "brio", "logitech", "webcam", "camera", "usb")

    def _resolve_device(self):
        import sounddevice as sd
        if self.device is None or isinstance(self.device, int):
            return self.device
        if str(self.device).isdigit():
            return int(self.device)
        inputs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        if str(self.device).lower() == "auto":
            for pat in self.AUTO_PATTERNS:
                for i, d in inputs:
                    if pat in d["name"].lower():
                        log.info("Auto-selected input device %d: %s", i, d["name"])
                        return i
            log.info("No USB webcam/mic found; using the default input")
            return None
        for i, d in inputs:
            if str(self.device).lower() in d["name"].lower():
                return i
        raise ValueError(f"No input device matching {self.device!r}. Devices:\n{sd.query_devices()}")

    def __iter__(self) -> Iterator[tuple[np.ndarray, float]]:
        import sounddevice as sd

        def cb(indata, frames, t, status):
            if status:
                self.overflows += 1
            try:
                self.q.put_nowait((indata.copy(), time.time() - frames / self.sr))
            except queue.Full:
                self.overflows += 1

        dev = self._resolve_device()
        info = sd.query_devices(dev, "input")
        self.device_name = info.get("name", "default")
        if not self.channels:   # 0 = auto: webcams are usually stereo; we mix down to mono
            self.channels = max(1, min(2, int(info["max_input_channels"])))
        try:
            sd.check_input_settings(device=dev, samplerate=self.sr, channels=self.channels, dtype="float32")
        except Exception as e:  # noqa: BLE001 - many webcams only do 32/44.1/48 kHz
            native = int(info["default_samplerate"])
            log.warning("Input doesn't support %d Hz (%s); using %d Hz and resampling", self.sr, e, native)
            self.sr = native
            self.block = int(native * self.block_seconds)
        log.info("Opening input %s (%d ch) @ %d Hz", self.device_name, self.channels, self.sr)
        with sd.InputStream(device=dev, samplerate=self.sr, channels=self.channels, dtype="float32",
                            blocksize=self.block, callback=cb):
            while True:
                data, ts = self.q.get()
                x = data.mean(axis=1) if data.ndim > 1 else data
                x = F.resample(x * self.gain, self.sr) if self.sr != F.SR else x * self.gain
                yield x.astype(np.float32), ts


class FileSource:
    """Replays a WAV file as if it were live (optionally faster than real time)."""

    def __init__(self, path: str, start_ts: float | None = None, speed: float = 0.0,
                 block_seconds: float = 0.05, loop: bool = False):
        x, sr = read_wav(path)
        self.x = F.resample(x, sr)
        self.start_ts = start_ts if start_ts is not None else time.time()
        self.speed = speed  # 0 = as fast as possible, 1 = real time
        self.block = int(F.SR * block_seconds)
        self.loop = loop

    def __iter__(self):
        t0 = self.start_ts
        while True:
            for i in range(0, len(self.x), self.block):
                chunk = self.x[i:i + self.block]
                if self.speed > 0:
                    time.sleep(len(chunk) / F.SR / self.speed)
                yield chunk, t0 + i / F.SR
            if not self.loop:
                return
            t0 += len(self.x) / F.SR
