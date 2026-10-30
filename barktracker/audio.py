"""Audio sources. Each yields (samples_float32_16k_mono, timestamp_of_first_sample)."""
from __future__ import annotations

import logging
import queue
import re
import threading
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


def read_wav_channels(path: str) -> tuple[np.ndarray, int]:
    """Like read_wav but keeps the channels apart: (frames x channels)."""
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("only 16-bit WAV files")
    return (np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0).reshape(-1, ch), sr


def list_devices() -> str:
    import sounddevice as sd  # imported lazily: needs PortAudio
    return str(sd.query_devices())


def short_name(name: str) -> str:
    """Device name without the ALSA card/device numbers, which can change between reboots."""
    return re.sub(r"\s*\(hw:\d+,\s*\d+\)\s*$", "", name or "").strip()


def list_inputs() -> dict:
    """Input devices PortAudio knows about, plus any USB sound cards plugged in since it started."""
    out = {"devices": [], "unlisted": [], "error": None}
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                out["devices"].append({"index": i, "name": d["name"], "short": short_name(d["name"]),
                                       "channels": int(d["max_input_channels"]),
                                       "samplerate": int(d["default_samplerate"])})
    except Exception as e:  # noqa: BLE001 - no PortAudio (tests, non-Linux dev machines)
        out["error"] = f"{type(e).__name__}: {e}"
    try:
        with open("/proc/asound/cards") as fh:
            cards = re.findall(r"^\s*\d+\s+\[[^\]]*\]:\s*[^-]*-\s*(.+)$", fh.read(), re.M)
        known = " ".join(d["name"].lower() for d in out["devices"])
        out["unlisted"] = [c.strip() for c in cards if c.strip().lower()[:12] not in known
                           and "bcm2835" not in c.lower() and "hdmi" not in c.lower()]
    except OSError:
        pass
    return out


class MicSource:
    def __init__(self, device="auto", samplerate: int = 16000, channels: int = 0,
                 block_seconds: float = 0.05, gain_db: float = 0.0):
        self.device = device
        self._want_sr, self._want_ch = samplerate, channels
        self.sr = samplerate
        self.channels = channels
        self.block_seconds = block_seconds
        self.block = int(samplerate * block_seconds)
        self.gain = 10 ** (gain_db / 20)
        self.q: queue.Queue = queue.Queue(maxsize=2000)
        self.overflows = 0
        self.device_name = None
        self._restart = threading.Event()
        self._rescan = False
        self._closed = False
        self.tap = None              # optional fn(frames x channels, ts, rate) before mono mixing
        self.prefer_native = False   # open at the device's own rate (finer timing for the direction test)

    def switch(self, device) -> None:
        """Move to another input: the open stream closes and reopens on `device` within ~0.5 s."""
        self.device = device or "auto"
        self._rescan = True          # re-read the device list so a newly plugged-in mic is found
        self._restart.set()

    def close(self) -> None:
        """Shutdown: end the stream loop so the stream is closed before Python exits. Left open,
        sounddevice's exit hook (Pa_Terminate) can deadlock with the audio callback thread."""
        self._closed = True
        self._restart.set()

    # Names USB webcams/mics show up as in ALSA, best first (Logitech C920/C922/C930e/Brio, etc.)
    AUTO_PATTERNS = ("c920", "c922", "c930", "brio", "logitech", "webcam", "camera",
                     "voicehat", "googlevoice", "usb")      # AIY Voice HAT (I2S, two MEMS mics)

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
        if self._closed:
            return

        def cb(indata, frames, t, status):
            if status:
                self.overflows += 1
            try:
                self.q.put_nowait((indata.copy(), time.time() - frames / self.sr))
            except queue.Full:
                self.overflows += 1

        if self._rescan:
            try:                     # PortAudio only lists devices present when it started
                sd._terminate()
                sd._initialize()
            except Exception:  # noqa: BLE001
                log.debug("PortAudio re-scan failed", exc_info=True)
            self._rescan = False
        self._restart.clear()
        self.sr, self.channels = self._want_sr, self._want_ch
        self.block = int(self.sr * self.block_seconds)
        dev = self._resolve_device()
        info = sd.query_devices(dev, "input")
        self.device_name = info.get("name", "default")
        if not self.channels:   # 0 = auto: webcams are usually stereo; we mix down to mono
            self.channels = max(1, min(2, int(info["max_input_channels"])))
        native = int(info["default_samplerate"])
        if self.prefer_native and native > self.sr:
            self.sr, self.block = native, int(native * self.block_seconds)
        try:
            sd.check_input_settings(device=dev, samplerate=self.sr, channels=self.channels, dtype="float32")
        except Exception as e:  # noqa: BLE001 - many webcams only do 32/44.1/48 kHz
            log.warning("Input doesn't support %d Hz (%s); using %d Hz and resampling", self.sr, e, native)
            self.sr = native
            self.block = int(native * self.block_seconds)
        log.info("Opening input %s (%d ch) @ %d Hz", self.device_name, self.channels, self.sr)
        with sd.InputStream(device=dev, samplerate=self.sr, channels=self.channels, dtype="float32",
                            blocksize=self.block, callback=cb):
            while not self._restart.is_set():
                try:
                    data, ts = self.q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self.tap is not None:
                    try:
                        self.tap(data * self.gain, ts, self.sr)
                    except Exception:  # noqa: BLE001 - an experiment must never stop listening
                        log.debug("input tap failed", exc_info=True)
                x = data.mean(axis=1) if data.ndim > 1 else data
                x = F.resample(x * self.gain, self.sr) if self.sr != F.SR else x * self.gain
                yield x.astype(np.float32), ts
        while not self.q.empty():        # switching inputs: drop audio from the old one
            self.q.get_nowait()
        log.info("Input stream closed (%s)", "shutting down" if self._closed else "switching microphone")


class FileSource:
    """Replays a WAV file as if it were live (optionally faster than real time)."""

    def __init__(self, path: str, start_ts: float | None = None, speed: float = 0.0,
                 block_seconds: float = 0.05, loop: bool = False):
        x, sr = read_wav(path)
        self.x = F.resample(x, sr)
        self.tap = None
        self.multi = None
        with wave.open(path, "rb") as w:
            ch = w.getnchannels()
        if ch > 1:                   # keep the channels apart for the experimental direction page
            raw, _ = read_wav_channels(path)
            self.multi = np.stack([F.resample(raw[:, c], sr) for c in range(ch)], axis=1)
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
                if self.tap is not None:
                    block = self.multi[i:i + self.block] if self.multi is not None else chunk[:, None]
                    try:
                        self.tap(block, t0 + i / F.SR, F.SR)
                    except Exception:  # noqa: BLE001
                        log.debug("input tap failed", exc_info=True)
                yield chunk, t0 + i / F.SR
            if not self.loop:
                return
            t0 += len(self.x) / F.SR
