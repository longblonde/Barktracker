import time

import numpy as np
from fastapi.testclient import TestClient

from barktracker import synth
from barktracker.app import BarkApp
from barktracker.array import SPEED_OF_SOUND, angle_from_tau, gcc_phat
from barktracker.audio import FileSource
from barktracker.classifier import HeuristicClassifier
from barktracker.config import Config
from barktracker.detector import BarkDetector, run_source
from barktracker.web import create_app


def delayed(x, tau, sr=16000):
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    return np.fft.irfft(np.fft.rfft(x) * np.exp(-2j * np.pi * f * tau), n).astype(np.float32)


def stereo_scene(path, angles, spacing, sr=16000, seed=1):
    """Barks from different directions, recorded by two mics `spacing` m apart."""
    rng = np.random.default_rng(seed)
    n = int((2 + 2.5 * len(angles)) * sr)
    a = rng.normal(0, 0.002, n).astype(np.float32)
    b = rng.normal(0, 0.002, n).astype(np.float32)
    for i, ang in enumerate(angles):
        x = synth.bark(rng=rng) * 0.4
        clip = np.zeros(n, np.float32)
        s = int((1.5 + 2.5 * i) * sr)
        clip[s:s + len(x)] = x
        a += clip
        b += delayed(clip, spacing * np.cos(np.radians(ang)) / SPEED_OF_SOUND)
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(np.stack([a, b], 1), -1, 1) * 32767).astype("<i2").tobytes())


def test_gcc_sign_and_angle():
    rng = np.random.default_rng(0)
    x = np.concatenate([np.zeros(1000), synth.bark(rng=rng), np.zeros(1000)]).astype(np.float32)
    for ang in (30, 90, 150):
        tau = 0.5 * np.cos(np.radians(ang)) / SPEED_OF_SOUND
        t, sharp = gcc_phat(x, delayed(x, tau), 16000, 0.5 / SPEED_OF_SOUND * 1.2)
        assert abs(angle_from_tau(t, 0.5)[0] - ang) < 2 and sharp > 5


def test_stereo_directions_from_a_file(tmp_path):
    wav = tmp_path / "stereo.wav"
    angles = [30, 90, 150, 60]
    stereo_scene(wav, angles, 0.5)
    cfg = Config(data_dir=str(tmp_path / "data"))
    cfg.dev.array, cfg.dev.array_mode, cfg.dev.array_spacing_cm = True, "stereo", 50
    t0 = time.time() - 60
    src = FileSource(str(wav), start_ts=t0)
    app = BarkApp(cfg, src, start_detector=False)
    assert src.tap == app.array.on_tap
    app.detector = BarkDetector(cfg, HeuristicClassifier(), app.store)
    run_source(app.detector, src)
    assert app.array.process(now=time.time()) >= len(angles)
    barks = [e for e in app.array.events if e["kind"] == "bark" and e["valid"]]
    assert len(barks) >= len(angles)
    got = [min(barks, key=lambda e: abs(e["ts"] - (t0 + 1.5 + 2.5 * i)))["angle"] for i in range(len(angles))]
    assert all(abs(g - a) < 5 for g, a in zip(got, angles)), got
    # swapped mics mirror the angle
    cfg.dev.array_swap = True
    app.array.done.clear()
    app.array.process(now=time.time())
    sw = [e for e in app.array.events if e["kind"] == "bark" and e["valid"]][-len(barks):]
    assert abs(min(sw, key=lambda e: abs(e["ts"] - (t0 + 1.5)))["angle"] - 150) < 5
    app.shutdown()


def test_pair_mode_calibration(tmp_path):
    """Two separate mics: the second stream runs 37 ms late; calibrating on a sound from
    straight ahead removes that offset."""
    cfg = Config(data_dir=str(tmp_path))
    cfg.dev.array, cfg.dev.array_mode, cfg.dev.array_spacing_cm = True, "pair", 100
    app = BarkApp(cfg, None, start_detector=False)
    app.detector = BarkDetector(cfg, HeuristicClassifier(), app.store)
    rng = np.random.default_rng(3)
    sr, lag = 16000, 0.037
    t0 = time.time() - 30

    def run(angle, start):
        n = 4 * sr
        x = np.zeros(n, np.float32)
        bark = synth.bark(rng=rng) * 0.4
        x[sr:sr + len(bark)] = bark
        a = x + rng.normal(0, 0.002, n).astype(np.float32)
        b = delayed(x, 1.0 * np.cos(np.radians(angle)) / SPEED_OF_SOUND) + rng.normal(0, 0.002, n).astype(np.float32)
        for i in range(0, n, 800):
            app.array.on_tap(a[i:i + 800, None], start + i / sr, sr)
            app.array.feed_b(b[i:i + 800], start + i / sr + lag, sr)   # its clock is 37 ms off
        app.detector.base_ts = start - app.detector.received / sr
        app.detector.feed(a, start)
        app.detector.flush()
        app.array.process(now=time.time() + 100)
        mine = [e for e in app.array.events if e["ts"] >= start]
        return max(mine, key=lambda e: e["sharp"])          # the bark itself (clearest sound)

    app.array.calibrating = True
    e = run(90, t0)                                      # a sound from straight ahead
    assert not app.array.calibrating and abs(cfg.dev.array_offset_ms - lag * 1000) < 0.3
    e = run(45, t0 + 6)
    assert e["valid"] and abs(e["angle"] - 45) < 5
    c = TestClient(create_app(app))
    f = c.get("/api/array/feed").json()
    assert f["status"]["mode"] == "pair" and f["events"]
    assert c.get(f"/api/array/feed?since={f['events'][-1]['n']}").json()["events"] == []
    assert c.post("/api/array/calibrate", json={"clear": True}).json()["offset_ms"] == 0
    app.shutdown()


def test_off_by_default(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    app.array.on_tap(np.zeros((800, 2), np.float32), time.time(), 16000)
    assert not app.array.st_a.blocks                     # ignores audio until switched on
    c = TestClient(create_app(app))
    assert c.post("/api/array/calibrate", json={}).status_code == 400
    assert c.put("/api/settings", json={"values": {"dev.array_mode": "triple"}}).status_code == 400
    app.shutdown()
