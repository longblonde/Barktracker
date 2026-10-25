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
    assert live["state"] == "barking" and live["barks"] >= 2
    app.shutdown()


def test_media_folders(tmp_path):
    cfg, app, c = make(tmp_path)
    m = c.get("/api/media").json()
    assert m["idle"] and not m["idle"][0]["own"]          # built-in drawings until you add your own
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
    app.shutdown()
