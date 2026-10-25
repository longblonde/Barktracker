"""Exercises MicSource against a fake sounddevice (no hardware needed)."""
import sys
import threading
import types

import numpy as np

from barktracker import features as F
from barktracker.audio import MicSource


def fake_sd(native_rate=48000, supports_16k=False):
    sd = types.ModuleType("sounddevice")

    def check_input_settings(device=None, samplerate=None, **kw):
        if samplerate != native_rate and not supports_16k:
            raise RuntimeError("Invalid sample rate")
    sd.check_input_settings = check_input_settings
    devs = [{"name": "bcm2835 Headphones", "max_input_channels": 0, "default_samplerate": 44100.0},
            {"name": "HD Pro Webcam C920: USB Audio (hw:2,0)", "max_input_channels": 2,
             "default_samplerate": float(native_rate)}]
    sd.query_devices = lambda dev=None, kind=None: (devs[1 if dev is None else dev] if kind else devs)

    class InputStream:
        def __init__(self, samplerate, blocksize, callback, channels=1, **kw):
            self.sr, self.bs, self.cb, self.ch = samplerate, blocksize, callback, channels
            self.stop = threading.Event()

        def __enter__(self):
            def run():
                t = 0
                while not self.stop.is_set() and t < 40:
                    x = np.sin(2 * np.pi * 440 * (np.arange(self.bs) + t * self.bs) / self.sr)
                    self.cb(np.repeat(x[:, None], self.ch, axis=1).astype(np.float32) * 0.1, self.bs, None, None)
                    t += 1
            threading.Thread(target=run, daemon=True).start()
            return self

        def __exit__(self, *a):
            self.stop.set()
    sd.InputStream = InputStream
    return sd


def test_mic_falls_back_to_native_rate_and_resamples(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd())
    src = MicSource(device="auto", samplerate=16000)
    it = iter(src)
    chunks = [next(it) for _ in range(10)]
    assert src.sr == 48000
    assert "C920" in src.device_name and src.channels == 2
    for x, ts in chunks:
        assert x.dtype == np.float32
        assert abs(len(x) - 0.05 * F.SR) <= 1     # 50 ms at 16 kHz after resampling
    assert chunks[-1][1] >= chunks[0][1]
