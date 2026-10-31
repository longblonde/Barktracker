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
    cfg.direction.enabled, cfg.direction.mode, cfg.direction.spacing_cm = True, "stereo", 50
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
    cfg.direction.swap = True
    app.array.done.clear()
    app.array.process(now=time.time())
    sw = [e for e in app.array.events if e["kind"] == "bark" and e["valid"]][-len(barks):]
    assert abs(min(sw, key=lambda e: abs(e["ts"] - (t0 + 1.5)))["angle"] - 150) < 5
    app.shutdown()


def test_pair_mode_calibration(tmp_path):
    """Two separate mics: the second stream runs 37 ms late; calibrating on a sound from
    straight ahead removes that offset."""
    cfg = Config(data_dir=str(tmp_path))
    cfg.direction.enabled, cfg.direction.mode, cfg.direction.spacing_cm = True, "pair", 100
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
    assert not app.array.calibrating and abs(cfg.direction.offset_ms - lag * 1000) < 0.3
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
    assert c.put("/api/settings", json={"values": {"direction.mode": "triple"}}).status_code == 400
    app.shutdown()


def test_old_estimates_are_flushed(tmp_path):
    from barktracker.array import KEEP_SECONDS
    cfg = Config(data_dir=str(tmp_path))
    cfg.direction.enabled = True
    app = BarkApp(cfg, None, start_detector=False)
    app.detector = BarkDetector(cfg, HeuristicClassifier(), app.store)
    now = time.time()
    for i in range(500):                                  # far more than it keeps
        app.array.events.append({"n": i, "id": i, "ts": now - KEEP_SECONDS - 100 + i})
    app.array.done.update(range(5000))
    app.array.process(now=now)
    assert len(app.array.events) <= 300 and all(e["ts"] >= now - KEEP_SECONDS for e in app.array.events)
    assert app.array.done == set()                         # nothing the detector still holds
    app.shutdown()


def test_directions_are_kept_per_bark_and_per_dog(tmp_path):
    wav = tmp_path / "stereo.wav"
    stereo_scene(wav, [40, 40, 40, 140, 140], 0.5, seed=4)
    cfg = Config(data_dir=str(tmp_path / "data"))
    cfg.direction.enabled, cfg.direction.mode, cfg.direction.spacing_cm = True, "stereo", 50
    t0 = time.time() - 60
    src = FileSource(str(wav), start_ts=t0)
    app = BarkApp(cfg, src, start_detector=False)
    app.detector = BarkDetector(cfg, HeuristicClassifier(), app.store)
    run_source(app.detector, src)
    for _ in range(5):
        app.array.process(now=time.time())
    n = app.store.q1("SELECT COUNT(*) n FROM bark_dirs WHERE valid=1")["n"]
    assert n >= 5
    c = TestClient(create_app(app))
    rex = c.post("/api/dogs", json={"name": "Rex"}).json()
    pip = c.post("/api/dogs", json={"name": "Pip"}).json()
    for e in c.get("/api/episodes?limit=50").json()["items"]:      # label each bark by when it was
        for bk in c.get(f"/api/episodes/{e['id']}").json()["barks"]:
            dog = rex["id"] if bk["ts"] - t0 < 1.5 + 2.5 * 2.5 else pip["id"]
            c.post("/api/barks/label", json={"bark_ids": [bk["id"]], "dog_id": dog})
    dogs = {d["name"]: d for d in c.get("/api/dogs").json()["dogs"]}
    assert abs(dogs["Rex"]["direction"]["bearing"] - 40) < 6 and abs(dogs["Pip"]["direction"]["bearing"] - 140) < 6
    ep = c.get("/api/episodes?limit=1").json()["items"][0]
    barks = c.get(f"/api/episodes/{ep['id']}").json()["barks"]
    assert any(b["bearing"] is not None for b in barks)
    app.shutdown()


def test_direction_table_stays_fast(tmp_path):
    """Directions are kept only for sounds that reached the bark check, old "quiet" rows are cleared
    once, and look-ups use the (valid, ts) index rather than scanning every row per bark."""
    import sqlite3
    from barktracker.array import ensure_schema
    from barktracker.storage import Store
    st = Store(tmp_path / "barktracker.db")
    ensure_schema(st)
    st.set_meta("bark_dirs_v", 1)                              # a database from before v0.16.1
    with st.tx() as c:
        c.executemany("INSERT INTO bark_dirs(ts, kind, bearing, clarity, valid) VALUES (?,?,?,?,1)",
                      [(float(i), "quiet" if i % 10 else "bark", 90.0, 6.0) for i in range(20000)])
    ensure_schema(st)
    assert st.q1("SELECT COUNT(*) n FROM bark_dirs")["n"] == 2000
    plan = " ".join(str(r) for r in sqlite3.connect(tmp_path / "barktracker.db").execute(
        "EXPLAIN QUERY PLAN SELECT bd.bearing FROM barks b JOIN bark_dirs bd ON bd.ts BETWEEN b.ts - 0.3"
        " AND b.ts + 0.3 WHERE bd.valid = 1 AND COALESCE(b.label_dog_id, b.pred_dog_id) > 0"))
    assert "ix_bark_dirs_valid_ts" in plan and "AUTOMATIC" not in plan
