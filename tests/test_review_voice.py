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


def test_dog_filter_review_split_and_marked(tmp_path):
    cfg, app, c = setup(tmp_path, [("Big dog", 5), ("Small dog", 5), ("Big dog", 4)])
    rex = c.post("/api/dogs", json={"name": "Rex", "address": "123 Elm St"}).json()
    pip = c.post("/api/dogs", json={"name": "Pip", "address": "9 Oak Ave"}).json()
    eps = sorted(c.get("/api/episodes").json()["items"], key=lambda e: e["start_ts"])
    assert all(e["marked"] == "auto" for e in eps)
    c.post(f"/api/episodes/{eps[0]['id']}/label", json={"dog_id": rex["id"]})
    c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": pip["id"]})
    # label just one bark of the third: partly manual
    b = c.get(f"/api/episodes/{eps[2]['id']}").json()["barks"]
    c.post("/api/barks/label", json={"bark_ids": [b[0]["id"]], "dog_id": rex["id"]})
    third = c.get(f"/api/episodes/{eps[2]['id']}").json()
    assert third["marked"] == "mixed" and third["labelled"] == 1
    assert c.get(f"/api/episodes/{eps[0]['id']}").json()["marked"] == "manual"

    # review split
    todo = c.get("/api/episodes?review=todo").json()
    done = c.get("/api/episodes?review=done").json()
    assert todo["total"] == 0 and done["total"] == 3
    c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": None})      # back to unlabelled
    c.patch(f"/api/episodes/{eps[1]['id']}", json={"reviewed": False})
    assert [e["id"] for e in c.get("/api/episodes?review=todo").json()["items"]] == [eps[1]["id"]]

    # dog filter on the main views (the rest of the third episode stays unidentified)
    with app.store.tx() as cx:
        cx.execute("UPDATE barks SET pred_dog_id=NULL WHERE episode_id=? AND label_dog_id IS NULL", (eps[2]["id"],))
    only_rex = c.get(f"/api/episodes?dogs={rex['id']}").json()
    assert {e["id"] for e in only_rex["items"]} == {eps[0]["id"], eps[2]["id"]}
    day = c.get(f"/api/day?dogs={rex['id']}").json()
    full = c.get("/api/day").json()
    assert {e["id"] for e in day["episodes"]} == {eps[0]["id"], eps[2]["id"]}
    assert len(day["barks"]) < len(full["barks"])
    part = next(e for e in day["episodes"] if e["id"] == eps[2]["id"])
    assert 0 < part["share"] < 1
    s_all = sum(d["seconds"] for d in c.get("/api/summary?days=2").json()["days"])
    s_rex = sum(d["seconds"] for d in c.get(f"/api/summary?days=2&dogs={rex['id']}").json()["days"])
    s_both = sum(d["seconds"] for d in c.get(f"/api/summary?days=2&dogs={rex['id']},u").json()["days"])
    assert 0 < s_rex < s_both <= s_all + 0.2
    h_rex = sum(map(sum, c.get(f"/api/heatmap?dogs={rex['id']}").json()["grid"]))
    h_all = sum(map(sum, c.get("/api/heatmap").json()["grid"]))
    assert 0 < h_rex < h_all
    assert c.get("/api/day?dogs=1;DROP").status_code == 400
    app.shutdown()


def test_hearing_note(tmp_path):
    from types import SimpleNamespace
    from barktracker.live import LiveMode
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    lm = LiveMode(cfg, app.store)
    now = time.time()
    det = SimpleNamespace(ep=None, last_bark_wall=0, stats={"barks": 0}, prev_level=-40, floor=-60,
                          eq=np.zeros(20), history=[(0, -40.0, -60.0)] * 20, heard=(now - 1, "Car", 0.86))
    assert lm.snapshot(det, now=now)["heard"] == {"label": "Car", "text": "a car", "score": 0.86}
    det.heard = (now - 1, "Car", 0.6)                  # not sure enough
    assert lm.snapshot(det, now=now)["heard"] is None
    det.heard = (now - 1, "Dog", 0.95)                 # the dog itself isn't a note
    assert lm.snapshot(det, now=now)["heard"] is None
    det.heard = (now - 1, "Inside, small room", 0.9)   # nor the room tone
    assert lm.snapshot(det, now=now)["heard"] is None
    det.heard = (now - 30, "Siren", 0.9)               # too long ago
    assert lm.snapshot(det, now=now)["heard"] is None
    det.heard = (now - 1, "Lawn mower", 0.9)
    cfg.ui.sound_notes = False
    assert lm.snapshot(det, now=now)["heard"] is None
    cfg.ui.sound_notes = True
    assert lm.snapshot(det, now=now)["heard"]["text"] == "a lawn mower"
    app.shutdown()


def test_heatmap_cells_and_quiet_hours(tmp_path):
    cfg, app, c = setup(tmp_path, [("Big dog", 5), ("Small dog", 4)])
    rex = c.post("/api/dogs", json={"name": "Rex"}).json()
    eps = sorted(c.get("/api/episodes").json()["items"], key=lambda e: e["start_ts"])
    c.post(f"/api/episodes/{eps[0]['id']}/label", json={"dog_id": rex["id"]})
    h = c.get("/api/heatmap").json()
    assert sum(map(sum, h["barks"])) == sum(e["bark_count"] for e in eps)
    assert sum(map(sum, h["episodes"])) == 2 and sum(h["weekdays"]) == 28
    assert sum(v for row in h["dogs"] for cell in row for k, v in cell.items() if k == str(rex["id"])) == 5
    # default 10 PM - 7 AM: 22..6 fully quiet, 7..21 not
    assert all(h["quiet"][hr] == [[0, 1]] for hr in (22, 23, 0, 3, 6))
    assert all(h["quiet"][hr] == [] for hr in (7, 12, 21))
    c.put("/api/ordinance", json={"thresholds": {"quiet_start": "22:30", "quiet_end": "06:15"}})
    h = c.get("/api/heatmap").json()
    assert h["quiet"][22] == [[0.5, 1]] and h["quiet"][6] == [[0, 0.25]] and h["quiet"][23] == [[0, 1]]
    h2 = c.get(f"/api/heatmap?dogs={rex['id']}").json()
    assert sum(map(sum, h2["barks"])) == 5
    app.shutdown()


def test_unknown_dog_label(tmp_path):
    from barktracker.storage import UNKNOWN
    cfg, app, c = setup(tmp_path, [("Big dog", 5), ("Big dog", 5), ("Small dog", 5)])
    rex = c.post("/api/dogs", json={"name": "Rex"}).json()
    eps = sorted(c.get("/api/episodes").json()["items"], key=lambda e: e["start_ts"])
    c.post(f"/api/episodes/{eps[0]['id']}/label", json={"dog_id": rex["id"]})
    X0, y0 = app.store.training_set()
    r = c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": UNKNOWN})
    assert r.status_code == 200
    # not learned from: training data unchanged
    X1, y1 = app.store.training_set()
    assert X1.shape == X0.shape and UNKNOWN not in set(y1.tolist())
    ep = c.get(f"/api/episodes/{eps[1]['id']}").json()
    assert not ep["false_positive"] and ep["marked"] == "manual"            # still barking, checked by you
    assert all(b["unknown"] and b["dog_id"] is None for b in ep["barks"])
    assert ep["dogs"] == {"u": 5}                                           # credited to nobody
    # counted as barking in stats, not under any dog; stays put after retraining
    c.post("/api/retrain")
    assert c.get(f"/api/episodes/{eps[1]['id']}").json()["dogs"] == {"u": 5}
    assert eps[1]["id"] not in {e["id"] for e in c.get(f"/api/episodes?dogs={rex['id']}").json()["items"]}
    assert eps[1]["id"] in {e["id"] for e in c.get("/api/episodes?dogs=u").json()["items"]}
    assert eps[1]["id"] in {e["id"] for e in c.get("/api/episodes?review=done").json()["items"]}
    rep = c.get(f"/api/report?dog={rex['id']}").json()
    assert all(eps[1]["id"] not in (i.get("episode_ids") or []) for i in rep.get("incidents", []))
    prof = {d["name"]: d for d in c.get("/api/dogs").json()["dogs"]}["Rex"]["profile"]
    assert prof["barks"] == 5                                               # profile only from Rex's own labels
    assert c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": -5}).status_code == 400
    app.shutdown()


def test_day_quiet_hours(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    cfg = Config(data_dir=str(tmp_path), timezone="America/Chicago")
    app = BarkApp(cfg, None, start_detector=False)
    c = TestClient(create_app(app))
    tz = ZoneInfo("America/Chicago")
    d = c.get("/api/day?date=2026-10-27").json()
    ts = lambda h, m=0, day=27: datetime(2026, 10, day, h, m, tzinfo=tz).timestamp()   # noqa: E731
    assert d["quiet"] == [[d["start_ts"], ts(7)], [ts(22), d["end_ts"]]]
    # DST change day (Nov 1, 2026): still midnight-7 AM and 10 PM-midnight local time
    d2 = c.get("/api/day?date=2026-11-01").json()
    assert d2["quiet"][0][1] == datetime(2026, 11, 1, 7, tzinfo=tz).timestamp()
    c.put("/api/ordinance", json={"thresholds": {"quiet_start": "13:00", "quiet_end": "15:30"}})
    assert c.get("/api/day?date=2026-10-27").json()["quiet"] == [[ts(13), ts(15, 30)]]
    app.shutdown()


def test_not_a_bark_navigation_and_list_confidence(tmp_path):
    cfg, app, c = setup(tmp_path, [("Big dog", 5), ("Small dog", 5), ("Big dog", 4), ("Small dog", 4), ("Big dog", 5)])
    eps = sorted(c.get("/api/episodes").json()["items"], key=lambda e: e["start_ts"])
    assert all(e["max_prob"] is not None for e in eps)
    nb = [eps[0]["id"], eps[2]["id"], eps[4]["id"]]
    for i in nb:
        c.post(f"/api/episodes/{i}/label", json={"dog_id": 0})
    mid = c.get(f"/api/episodes/{nb[1]}").json()
    assert (mid["prev_id"], mid["next_id"]) == (nb[0], nb[2])          # skips the barking clips between
    assert mid["position"] == {"index": 2, "of": 3}
    bark = c.get(f"/api/episodes/{eps[1]['id']}").json()
    assert bark["next_id"] == eps[3]["id"] and bark["prev_id"] is None
    # automatic dog matches carry their confidence in the list
    rex = c.post("/api/dogs", json={"name": "Rex"}).json()
    c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": rex["id"]})
    c.post(f"/api/episodes/{eps[3]['id']}/label", json={"dog_id": None})
    with app.store.tx() as cx:
        cx.execute("UPDATE barks SET pred_dog_id=?, pred_conf=0.8 WHERE episode_id=?", (rex["id"], eps[3]["id"]))
    items = {e["id"]: e for e in c.get("/api/episodes").json()["items"]}
    assert items[eps[3]["id"]]["match"] == {str(rex["id"]): 0.8}
    assert items[eps[1]["id"]]["match"] == {}                            # labelled by you: no guess shown
    app.shutdown()
