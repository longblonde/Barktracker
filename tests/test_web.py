import numpy as np
import pytest
from fastapi.testclient import TestClient

from barktracker import synth
from barktracker.app import BarkApp
from barktracker.classifier import HeuristicClassifier
from barktracker.config import Config
from barktracker.detector import BarkDetector


@pytest.fixture
def client(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    det = BarkDetector(cfg, HeuristicClassifier(), app.store)
    import time
    rng = np.random.default_rng(2)
    ev = synth.bout(2.0, "Big dog", 5, rng=rng) + synth.bout(30.0, "Small dog", 5, rng=rng)
    det.feed(synth.scene(ev, 45.0, seed=3), time.time() - 3600)
    det.flush()
    from barktracker.web import create_app
    yield TestClient(create_app(app)), app
    app.shutdown()


def test_dashboard_flow(client):
    c, app = client
    assert "barktracker" in c.get("/").text
    s = c.get("/api/status").json()
    assert s["counts"]["episodes"] == 2
    eps = c.get("/api/episodes").json()
    assert eps["total"] == 2
    eid = eps["items"][0]["id"]
    detail = c.get(f"/api/episodes/{eid}").json()
    assert len(detail["barks"]) == 5 and detail["has_clip"]
    a = c.get(f"/api/episodes/{eid}/audio")
    assert a.status_code == 200 and a.content[:4] == b"RIFF"
    sp = c.get(f"/api/episodes/{eid}/spectrogram").json()
    assert sp["width"] > 0
    assert len(c.get("/api/summary?days=7").json()["days"]) == 7
    assert sum(map(sum, c.get("/api/heatmap").json()["grid"])) > 0
    assert len(c.get("/api/day").json()["episodes"]) >= 1

    dog = c.post("/api/dogs", json={"name": "Rex"}).json()
    r = c.post(f"/api/episodes/{eid}/label", json={"dog_id": dog["id"]}).json()
    assert r["identifier"]["trained"]
    assert r["episode"]["dogs"] == {str(dog["id"]): 5}
    assert c.post(f"/api/episodes/{eid}/label", json={"dog_id": 999}).status_code == 400

    # mark the other as not-a-bark: disappears from stats
    other = eps["items"][1]["id"]
    c.post(f"/api/episodes/{other}/label", json={"dog_id": 0})
    assert c.get("/api/episodes").json()["total"] == 1
    assert "Rex x5" in c.get("/api/export.csv").text

    assert c.delete(f"/api/episodes/{eid}").json()["ok"]
    assert c.get(f"/api/episodes/{eid}").status_code == 404


def test_basic_auth(tmp_path):
    from barktracker.web import create_app
    cfg = Config(data_dir=str(tmp_path))
    cfg.web.username, cfg.web.password = "me", "secret"
    app = BarkApp(cfg, None, start_detector=False)
    c = TestClient(create_app(app))
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", auth=("me", "secret")).status_code == 200
