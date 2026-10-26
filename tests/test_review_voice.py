import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from barktracker import synth
from barktracker.app import BarkApp
from barktracker.classifier import HeuristicClassifier
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.storage import NOT_BARK
from barktracker.web import create_app


def setup(tmp_path, bouts):
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    det = BarkDetector(cfg, HeuristicClassifier(), app.store)
    rng = np.random.default_rng(4)
    t0 = time.time() - 3600
    for i, (dog, n) in enumerate(bouts):
        det.base_ts = t0 + i * 60 - det.received / 16000
        det.feed(synth.scene(synth.bout(2.0, dog, n, rng=rng), 12.0, seed=i), t0 + i * 60)
        det.flush()
    return cfg, app, TestClient(create_app(app))


def test_review_and_delete_not_a_bark(tmp_path):
    cfg, app, c = setup(tmp_path, [("Big dog", 5), ("Small dog", 5), ("Small dog", 4)])
    eps = c.get("/api/episodes").json()["items"]
    assert len(eps) == 3
    a, b = eps[0]["id"], eps[1]["id"]
    for e in (a, b):
        c.post(f"/api/episodes/{e}/label", json={"dog_id": 0})
    nb = c.get("/api/episodes?kind=not_bark").json()
    assert nb["total"] == 2 and c.get("/api/episodes").json()["total"] == 1
    s = c.get("/api/not-bark").json()
    assert s["episodes"] == 2 and s["with_audio"] == 2 and s["training_examples"] >= 8

    # delete only the audio: the file goes, the training examples stay
    path = Path(app.store.q1("SELECT clip_path FROM episodes WHERE id=?", (a,))["clip_path"])
    assert c.delete(f"/api/episodes/{a}?keep_training=true").json()["kept_training"]
    assert not path.exists()
    ep = c.get(f"/api/episodes/{a}").json()
    assert ep["false_positive"] and not ep["has_clip"]
    X, y = app.store.training_set()
    before = int((y == NOT_BARK).sum())
    assert before == s["training_examples"]

    # bulk: remaining not-a-bark audio
    r = c.post("/api/not-bark/delete-audio").json()
    assert r["deleted"] == 1 and r["with_audio"] == 0 and r["training_examples"] == before

    # delete and forget: training examples for that clip are gone
    n_b = len(app.store.barks_for(b))
    c.delete(f"/api/episodes/{b}")
    X, y = app.store.training_set()
    assert int((y == NOT_BARK).sum()) == before - n_b

    # restore: back to barking
    c.post(f"/api/episodes/{a}/label", json={"dog_id": None})
    assert c.get("/api/episodes?kind=not_bark").json()["total"] == 0
    assert c.get("/api/episodes").json()["total"] == 2
    app.shutdown()


def test_voice_profiles_size_and_suggestion(tmp_path):
    cfg, app, c = setup(tmp_path, [("Big dog", 6), ("Small dog", 6), ("Big dog", 5)])
    big = c.post("/api/dogs", json={"name": "Rex", "size": "large"}).json()
    small = c.post("/api/dogs", json={"name": "Pip"}).json()
    assert c.post("/api/dogs", json={"name": "X", "size": "huge"}).status_code == 400
    eps = sorted(c.get("/api/episodes").json()["items"], key=lambda e: e["start_ts"])
    c.post(f"/api/episodes/{eps[0]['id']}/label", json={"dog_id": big["id"]})
    c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": small["id"]})

    dogs = {d["name"]: d for d in c.get("/api/dogs").json()["dogs"]}
    rex, pip = dogs["Rex"]["profile"], dogs["Pip"]["profile"]
    assert rex["pitch_hz"] < pip["pitch_hz"] and rex["brightness_hz"] < pip["brightness_hz"]
    assert rex["sounds_like"] == "large" and pip["sounds_like"] == "small"
    assert len(rex["shape"]) == 40 and max(rex["shape"]) == 0
    assert dogs["Rex"]["separation"]["accuracy"] >= 0.9 and dogs["Pip"]["separation"]["accuracy"] >= 0.9
    assert dogs["Rex"]["size"] == "large"

    # an unlabelled episode reports what it sounds like (the page suggests a dog from this)
    snd = c.get(f"/api/episodes/{eps[2]['id']}").json()["sound"]
    assert snd["sounds_like"] == "large" and snd["pitch_hz"] < 500
    c.patch(f"/api/dogs/{small['id']}", json={"size": "small"})
    assert {d["name"]: d for d in c.get("/api/dogs").json()["dogs"]}["Pip"]["size"] == "small"
    app.shutdown()
