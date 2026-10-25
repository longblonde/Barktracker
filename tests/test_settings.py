import time

import numpy as np
from fastapi.testclient import TestClient

from barktracker import synth
from barktracker.app import BarkApp
from barktracker.classifier import HeuristicClassifier
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.web import create_app


def make(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    return cfg, app, TestClient(create_app(app))


def test_settings_apply_live_and_persist(tmp_path):
    cfg, app, c = make(tmp_path)
    s = c.get("/api/settings").json()
    assert s["values"]["detection.bark_threshold"] == 0.35
    assert s["disk"]["total_mb"] > 0 and "clips_mb" in s["disk"]
    r = c.put("/api/settings", json={"values": {"detection.bark_threshold": 0.6,
                                                 "clips.max_disk_mb": 40960,
                                                 "detection.min_episode_barks": "2"}})
    assert r.status_code == 200
    assert cfg.detection.bark_threshold == 0.6           # live config object updated
    assert cfg.detection.min_episode_barks == 2
    assert set(r.json()["overridden"]) == {"detection.bark_threshold", "clips.max_disk_mb",
                                           "detection.min_episode_barks"}
    # out of range is rejected and changes nothing
    bad = c.put("/api/settings", json={"values": {"detection.bark_threshold": 3, "clips.retention_days": 5}})
    assert bad.status_code == 400 and cfg.clips.retention_days == 60
    assert c.put("/api/settings", json={"values": {"web.password": "x"}}).status_code == 400
    app.shutdown()

    # survives a restart
    cfg2 = Config(data_dir=str(tmp_path))
    app2 = BarkApp(cfg2, None, start_detector=False)
    assert cfg2.detection.bark_threshold == 0.6 and cfg2.clips.max_disk_mb == 40960
    c2 = TestClient(create_app(app2))
    c2.put("/api/settings", json={"reset": True})
    assert cfg2.detection.bark_threshold == 0.35
    assert c2.get("/api/settings").json()["overridden"] == []
    app2.shutdown()


def test_live_state_follows_detector(tmp_path):
    cfg, app, c = make(tmp_path)
    assert c.get("/api/live").json()["state"] == "off"
    det = BarkDetector(cfg, HeuristicClassifier(), app.store)
    app.detector = det
    det.feed(np.zeros(16000, np.float32) + 1e-4, time.time() - 10)
    assert c.get("/api/live").json()["state"] == "idle"
    x = synth.scene(synth.bout(0.5, "Big dog", 3), 3.0, seed=1)
    det.feed(x, time.time() - 3)
    live = c.get("/api/live").json()
    assert live["state"] == "barking" and live["mode"] == "bark" and live["barks"] >= 2
    assert len(live["bands"]) == 20
    # bars rise with a bark and fall back in quiet
    from barktracker import features as F
    b = synth.bark(rng=np.random.default_rng(0)) * 0.5
    peak = F.eq_bands(b[np.argmax(np.abs(b)) - 400:][:800])
    assert peak.max() > 0.6 and F.eq_bands(np.zeros(800, np.float32)).max() == 0
    app.shutdown()


def test_media_folders(tmp_path):
    cfg, app, c = make(tmp_path)
    m = c.get("/api/media").json()
    assert [m[k][0]["name"] for k in ("quiet", "active", "bark")] == ["nap.svg", "sit.svg", "stand.svg"]
    assert not m["quiet"][0]["own"]                       # built-in drawings until you add your own
    assert c.get(m["bark"][0]["url"]).status_code == 200
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = c.put("/api/media/bark/my dog.png", content=png).json()
    assert [i["name"] for i in r["bark"]] == ["my dog.png"] and r["bark"][0]["own"]
    assert c.get("/media/bark/my dog.png").content == png
    assert c.put("/api/media/bark/evil.exe", content=b"x").status_code == 400
    assert c.put("/api/media/other/a.png", content=png).status_code == 404
    assert c.get("/media/bark/..%2F..%2Fbarktracker.db").status_code == 404
    r = c.delete("/api/media/bark/my dog.png").json()
    assert not r["bark"][0]["own"]
    # pictures in the older idle/ folder still count as nap pictures
    (cfg.media_path / "idle").mkdir(parents=True)
    (cfg.media_path / "idle" / "old.png").write_bytes(png)
    q = c.get("/api/media").json()["quiet"]
    assert q[0]["name"] == "old.png" and c.get(q[0]["url"]).status_code == 200
    app.shutdown()


def test_mode_nap_sit_bark(tmp_path):
    from types import SimpleNamespace
    from barktracker.live import LiveMode, SETTLE_SECONDS
    cfg = Config(data_dir=str(tmp_path), timezone="America/Chicago")
    app = BarkApp(cfg, None, start_detector=False)
    lm = LiveMode(cfg, app.store)
    det = SimpleNamespace(ep=None, last_bark_wall=0, stats={"barks": 0}, prev_level=-60, floor=-60,
                          eq=np.zeros(20), history=[(0, -60.0, -60.0)] * 20)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    night = datetime(2026, 10, 25, 23, 30, tzinfo=ZoneInfo("America/Chicago")).timestamp()
    day = datetime(2026, 10, 25, 14, 0, tzinfo=ZoneInfo("America/Chicago")).timestamp()
    assert lm.snapshot(det, now=night)["mode"] == "nap"          # quiet hours, calm
    det.history = [(0, -40.0, -60.0)] * 20                        # medium noise at night
    assert lm.snapshot(det, now=night + 1)["mode"] == "nap"      # waits before switching
    assert lm.snapshot(det, now=night + 2 + SETTLE_SECONDS)["mode"] == "sit"
    det.last_bark_wall = night + 10
    assert lm.snapshot(det, now=night + 10.5)["mode"] == "bark"  # barking switches at once
    det.last_bark_wall, det.history = 0, [(0, -60.0, -60.0)] * 20
    lm2 = LiveMode(cfg, app.store)
    assert lm2.snapshot(det, now=day)["mode"] == "sit"           # quiet daytime
    app.shutdown()


def test_live_stream_sends_events(tmp_path):
    import json
    cfg, app, c = make(tmp_path)
    with c.stream("GET", "/api/live/stream?count=2") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert len(events) == 2 and events[0]["state"] == "off" and events[0]["mode"] == "nap"
    app.shutdown()
