import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from barktracker import synth, tuning
from barktracker.app import BarkApp
from barktracker.classifier import HeuristicClassifier
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.web import create_app


def listening_app(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    cfg.detection.mode = "heuristic"
    app = BarkApp(cfg, None, start_detector=False)
    app.detector = BarkDetector(cfg, HeuristicClassifier(), app.store, app.identifier)
    return cfg, app, TestClient(create_app(app))


def scene_with_faint_barks(seed=3):
    rng = np.random.default_rng(seed)
    x = synth.scene(synth.bout(2.0, "Big dog", 3, rng=rng), 20.0, seed=seed)
    faint = []
    for t in (9.0, 12.0, 15.0):                       # a smaller, quieter dog the trigger misses
        b = synth.bark(rng=rng) * 0.006
        x[int(t * 16000):int(t * 16000) + len(b)] += b
        faint.append(t)
    return x, faint


def wait_analyzed(c, mid):
    for _ in range(200):
        d = c.get(f"/api/missed/{mid}").json()
        if d["analyzed"]:
            return d
        time.sleep(0.05)
    raise AssertionError("analysis didn't finish")


def test_capture_review_add_and_suggest(tmp_path):
    cfg, app, c = listening_app(tmp_path)
    x, faint = scene_with_faint_barks()
    app.detector.feed(x, time.time() - 20)
    assert c.post("/api/missed", json={"seconds": 99}).status_code == 400
    mid = c.post("/api/missed", json={"seconds": 20}).json()["id"]
    d = wait_analyzed(c, mid)
    cands = d["candidates"]
    near = lambda t: [cd for cd in cands if abs(cd["t"] - t) < 0.3]   # noqa: E731
    for t in faint:                                    # each faint bark found, and why it was missed
        cd = near(t)
        assert cd and cd[0]["verdict"]["kind"] == "quiet" and cd[0]["verdict"]["check"] == 1
    loud = [cd for cd in cands if cd.get("live") == "bark"]
    assert len(loud) == 3 and all(cd["verdict"]["kind"] == "bark" for cd in loud)
    # what-if: a lower trigger lets the faint barks through to the model
    rise = min(near(t)[0]["rise"] for t in faint)
    d2 = c.get(f"/api/missed/{mid}?trigger_db={max(3, rise - 1)}").json()
    assert all(near_cd["verdict"]["kind"] != "quiet" for t in faint
               for near_cd in [cd for cd in d2["candidates"] if abs(cd["t"] - t) < 0.3])
    # mark the faint barks as a new dog's: they become a logged, labelled episode
    pip = c.post("/api/dogs", json={"name": "Pip", "size": "small"}).json()
    picks = [near(t)[0]["i"] for t in faint] + [loud[0]["i"]]         # an already-logged one is ignored
    r = c.post(f"/api/missed/{mid}/add", json={"candidates": picks, "dog_id": pip["id"]}).json()
    ep = c.get(f"/api/episodes/{r['episode_id']}").json()
    assert ep["bark_count"] == 3 and ep["dogs"] == {str(pip["id"]): 3} and ep["marked"] == "manual"
    assert ep["has_clip"] and c.get(f"/api/episodes/{r['episode_id']}/audio").status_code == 200
    assert c.get(f"/api/missed/{mid}").json()["status"] == "added"
    # suggestions: the gentlest trigger that would have counted them
    s = c.get("/api/tuning/suggest").json()
    assert s["barks"] == 3 and s["missed_now"] == 3 and s["missed_by_check"]["1"] == 3
    assert "trigger_db" in s["changed"] and s["suggested"]["trigger_db"] < cfg.detection.trigger_db
    assert s["caught"] >= 2
    app.shutdown()


def test_dismiss_and_live_feed(tmp_path):
    cfg, app, c = listening_app(tmp_path)
    x, _ = scene_with_faint_barks(5)
    app.detector.feed(x, time.time() - 20)
    f = c.get("/api/tuning/feed").json()
    assert f["listening"] and f["rows"] == 64 and f["events"] and f["settings"]["trigger_db"] == 10
    f2 = c.get(f"/api/tuning/feed?since_idx={f['last_idx']}&since_event={f['events'][-1]['id']}").json()
    assert f2["events"] == [] and f2["cols"] == ""
    assert app.detector.tuning_active()                  # quiet sounds now get scored too
    mid = c.post("/api/missed", json={"seconds": 15}).json()["id"]
    wait_analyzed(c, mid)
    path = app.store.q1("SELECT clip_path FROM missed WHERE id=?", (mid,))["clip_path"]
    assert Path(path).exists()
    assert c.post(f"/api/missed/{mid}/dismiss").json()["ok"]
    assert not Path(path).exists() and c.get(f"/api/missed/{mid}").json()["status"] == "dismissed"
    assert c.get("/api/missed").json()["counts"] == {"dismissed": 1}
    app.shutdown()


def test_not_listening(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    c = TestClient(create_app(app))
    assert c.post("/api/missed", json={}).status_code == 409
    assert c.get("/api/tuning/feed").json()["listening"] is False
    app.shutdown()


def test_verdict_checks():
    s = {"trigger_db": 10, "min_level": -55, "threshold": 0.35, "suppress": True}
    assert tuning.verdict({"peak": -60, "rise": 15, "prob": 0.9}, s)["check"] == 1
    assert tuning.verdict({"peak": -40, "rise": 6, "prob": 0.9}, s)["check"] == 1
    assert tuning.verdict({"peak": -40, "rise": 12, "prob": 0.2, "other": "Car", "other_score": 0.8}, s)["kind"] == "not_bark"
    assert tuning.verdict({"peak": -40, "rise": 12, "prob": 0.6, "filtered": True}, s)["kind"] == "filtered"
    assert tuning.verdict({"peak": -40, "rise": 12, "prob": 0.6}, s)["kind"] == "bark"
