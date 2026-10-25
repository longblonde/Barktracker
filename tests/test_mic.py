"""Exercises MicSource against a fake sounddevice (no hardware needed)."""
import sys
import threading
import types

import numpy as np

from barklog import features as F
from barklog.audio import MicSource


def fake_sd(native_rate=48000, supports_16k=False):
    sd = types.ModuleType("sounddevice")

    def check_input_settings(device=None, samplerate=None, **kw):
        if samplerate != native_rate and not supports_16k:
            raise RuntimeError("Invalid sample rate")
    sd.check_input_settings = check_input_settings
    sd.query_devices = lambda dev=None, kind=None: (
        {"default_samplerate": float(native_rate), "name": "USB mic", "max_input_channels": 1}
        if kind else [{"name": "USB mic", "max_input_channels": 1}])

    class InputStream:
        def __init__(self, samplerate, blocksize, callback, channels=1, **kw):
            self.sr, self.bs, self.cb, self.ch = samplerate, blocksize, callback, channels
            self.stop = threading.Event()

        def __enter__(self):
            def run():
                t = 0
                while not self.stop.is_set() and t < 40:
                    x = np.sin(2 * np.pi * 440 * (np.arange(self.bs) + t * self.bs) / self.sr)
                    self.cb(x.reshape(-1, 1).astype(np.float32) * 0.1, self.bs, None, None)
                    t += 1
            threading.Thread(target=run, daemon=True).start()
            return self

        def __exit__(self, *a):
            self.stop.set()
    sd.InputStream = InputStream
    return sd


def test_mic_falls_back_to_native_rate_and_resamples(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd())
    src = MicSource(device="usb", samplerate=16000)
    it = iter(src)
    chunks = [next(it) for _ in range(10)]
    assert src.sr == 48000
    for x, ts in chunks:
        assert x.dtype == np.float32
        assert abs(len(x) - 0.05 * F.SR) <= 1     # 50 ms at 16 kHz after resampling
    assert chunks[-1][1] >= chunks[0][1]
