import json
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import numpy as np
from fastapi.testclient import TestClient

from barktracker import alerts as AL
from barktracker import discover as DS
from barktracker import ordinance as O
from barktracker.app import BarkApp
from barktracker.config import Config
from barktracker.identify import DogIdentifier, retrain_and_repredict
from barktracker.storage import Store
from barktracker.web import create_app

TZ = "America/Chicago"


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(TZ)).timestamp()


def episode(store, start, dur, barks=10, feats=None, dog=None):
    eid = store.create_episode(start, None, start - 2)
    f = feats if feats is not None else np.zeros(10, np.float32)
    for k in range(barks):
        b = store.add_bark(eid, start + k * dur / max(barks, 1), 0.3, 0.9, -20, f)
        if dog is not None:
            store.label_barks([b], dog)
    store.finish_episode(eid, start + dur, barks, -20, 0.9, dur + 4)
    return eid


def test_custom_rules_from_the_plus_menu(tmp_path):
    app = BarkApp(Config(data_dir=str(tmp_path), timezone=TZ), None, start_detector=False)
    c = TestClient(create_app(app))
    o = c.get("/api/ordinance").json()
    assert [r["id"] for r in o["ordinance"]["rules"]] == ["sustained", "quiet_hours", "frequent", "habitual"]
    assert "intermittent" in o["rule_types"]
    # six 6-minute bouts, 20 minutes apart: never 10 minutes straight, but 36 minutes within 3 hours
    for i in range(6):
        episode(app.store, ts(2026, 10, 20, 13) + i * 1560, 360)
    rules = o["ordinance"]["rules"] + [{"type": "intermittent", "label": "On and off",
                                         "params": {"minutes": 30, "window_hours": 3}}]
    r = c.put("/api/ordinance", json={"rules": rules}).json()
    added = r["ordinance"]["rules"][-1]
    assert added["id"] == "intermittent" and r["labels"]["intermittent"] == "On and off"
    assert "on and off" in r["criteria"]["intermittent"]
    rep = c.get("/api/report?from=2026-10-20&to=2026-10-20").json()
    flags = [i["flags"] for i in rep["incidents"]]
    assert all("sustained" not in f for f in flags)
    assert ["intermittent" in f for f in flags] == [False] * 4 + [True] * 2     # 30 min reached at the 5th
    assert rep["summary"]["flag_counts"]["intermittent"] == 2
    # a second rule of the same type gets its own id; a disabled rule flags nothing
    rules = r["ordinance"]["rules"] + [{"type": "sustained", "params": {"minutes": 5}}]
    rules[0]["enabled"] = False
    r = c.put("/api/ordinance", json={"rules": rules}).json()
    assert r["ordinance"]["rules"][-1]["id"] == "sustained_2"
    rep = c.get("/api/report?from=2026-10-20&to=2026-10-20").json()
    assert all("sustained_2" in i["flags"] and "sustained" not in i["flags"] for i in rep["incidents"])
    assert c.put("/api/ordinance", json={"rules": [{"type": "howling"}]}).status_code == 400
    # the old thresholds view still edits the matching rule
    c.put("/api/ordinance", json={"thresholds": {"daily_minutes": 12}})
    o = c.get("/api/ordinance").json()["ordinance"]
    assert next(x for x in o["rules"] if x["type"] == "frequent")["params"]["minutes"] == 12
    for kind in ("barks.csv", "audio.zip", "everything.zip"):
        assert c.get(f"/api/report/{kind}?from=2026-10-20&to=2026-10-20").status_code == 200
    app.shutdown()


class _Sink(BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        _Sink.got.append((self.path, dict(self.headers), self.rfile.read(n)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_alert_destinations(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    cfg = Config(data_dir=str(tmp_path), timezone=TZ)
    cfg.notify.webhook_url = base + "/old"                     # the single webhook of older versions
    app = BarkApp(cfg, None, start_detector=False)
    c = TestClient(create_app(app))
    items = c.get("/api/alerts").json()["items"]
    assert len(items) == 1 and items[0]["url"].endswith("/old")
    rex = app.store.add_dog("Rex")["id"]
    items += [{"name": "Phone", "kind": "ntfy", "url": base + "/ntfy", "min_seconds": 0},
              {"name": "Rex only", "kind": "discord", "url": base + "/discord", "min_seconds": 0, "dogs": [rex]},
              {"name": "Rules", "kind": "slack", "url": base + "/slack", "min_seconds": 0, "when": "criteria"}]
    assert c.put("/api/alerts", json={"items": items}).status_code == 200
    assert c.put("/api/alerts", json={"items": [{"kind": "ntfy", "url": "ftp://x"}]}).status_code == 400
    _Sink.got.clear()
    app.notify({"id": 1, "start_ts": time.time() - 40, "end_ts": time.time(), "duration": 40.0, "barks": 12,
                "dogs": {str(rex): 12}})
    for _ in range(50):
        if len(_Sink.got) >= 3:
            break
        time.sleep(0.05)
    paths = sorted(p for p, _, _ in _Sink.got)
    assert paths == ["/discord", "/ntfy", "/old"]              # 40 s is no rule's incident, so no Slack
    body = {p: b for p, _, b in _Sink.got}
    assert body["/ntfy"].decode().startswith("Barking for 40 s (12 barks): Rex")
    assert "Rex" in json.loads(body["/discord"])["content"]
    assert json.loads(body["/old"])["dogs"] == {"Rex": 12}
    # someone else's dog: the Rex-only destination stays quiet
    assert not AL.matches(items[2] | {"dogs": [rex]}, {"duration": 40, "dogs": {"9": 3}}, {})
    _Sink.got.clear()
    assert c.post("/api/alerts/test", json={"kind": "slack", "url": base + "/t"}).status_code == 200
    assert json.loads(_Sink.got[0][2])["text"].startswith("*BarkTracker*: Test")
    assert c.post("/api/alerts/test", json={"kind": "slack", "url": "http://127.0.0.1:9/x"}).status_code == 502
    srv.shutdown()
    app.shutdown()


NF = 2 * __import__("barktracker.features", fromlist=["N_MELS"]).N_MELS + 8     # a stored bark descriptor


def _voice(rng, centre):
    return (centre + rng.normal(0, 0.15, NF)).astype(np.float32)


def test_discovered_dog_groups(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    rng = np.random.default_rng(3)
    a, b = np.zeros(NF), np.zeros(NF)
    b[:5] = 3.0
    t = time.time() - 5 * 86400
    for i in range(6):                                          # two unnamed voices over several days
        episode(store, t + i * 40000, 10, 5, None)
        episode(store, t + i * 40000 + 500, 10, 5, None)
    rows = store.q("SELECT id, episode_id FROM barks ORDER BY id")
    eps = sorted({r["episode_id"] for r in rows})
    with store.tx() as c:
        for r in rows:
            v = _voice(rng, a if eps.index(r["episode_id"]) % 2 == 0 else b)
            c.execute("UPDATE barks SET features=? WHERE id=?", (v.tobytes(), r["id"]))
    groups = DS.discovered(store, time.time(), use_direction=False)
    assert len(groups) == 2 and all(g["barks"] == 30 and g["episodes"] == 6 for g in groups)
    assert len(groups[0]["samples"]) == 5 and len({s["episode_id"] for s in groups[0]["samples"]}) == 5
    rh = DS.rhythm(store)
    assert rh == {}                                             # nobody's barks are known yet
    store.label_barks(groups[0]["bark_ids"], store.add_dog("Pip")["id"])
    rh = DS.rhythm(store)
    assert list(rh.values())[0]["pause_s"] == 2.0              # 5 barks over 10 s: one every 2 s


def test_direction_weighs_into_matching(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    from barktracker.array import ensure_schema
    ensure_schema(store)
    rng = np.random.default_rng(5)
    rex, pip = store.add_dog("Rex")["id"], store.add_dog("Pip")["id"]
    t = time.time() - 3600
    voice = np.zeros(NF)                                         # they sound the same...
    for i, (dog, ang) in enumerate([(rex, 40)] * 8 + [(pip, 140)] * 8 + [(None, 135)]):
        eid = store.create_episode(t + i * 30, None, t + i * 30)
        bid = store.add_bark(eid, t + i * 30 + 1, 0.3, 0.9, -20, _voice(rng, voice))
        if dog:
            store.label_barks([bid], dog)
        with store.tx() as c:                                    # ...but bark from different sides
            c.execute("INSERT INTO bark_dirs(ts, kind, bearing, clarity, valid) VALUES (?, 'bark', ?, 9, 1)",
                      (t + i * 30 + 1, ang + rng.normal(0, 3)))
        store.finish_episode(eid, t + i * 30 + 2, 1, -20, 0.9, 4)
    ident = DogIdentifier(k=7, min_labels=3, min_conf=0.6)
    retrain_and_repredict(store, ident, use_direction=False)
    plain = store.q1("SELECT pred_dog_id, pred_conf FROM barks WHERE label_dog_id IS NULL")
    retrain_and_repredict(store, ident, use_direction=True)
    with_dir = store.q1("SELECT pred_dog_id, pred_conf FROM barks WHERE label_dog_id IS NULL")
    assert with_dir["pred_dog_id"] == pip and with_dir["pred_conf"] > 0.75
    assert plain["pred_conf"] < with_dir["pred_conf"]


def test_live_wedge_scan(tmp_path):
    from tests.test_array import stereo_scene
    from barktracker.audio import FileSource
    from barktracker.classifier import HeuristicClassifier
    from barktracker.detector import BarkDetector, run_source
    wav = tmp_path / "s.wav"
    stereo_scene(wav, [45], 0.5)
    cfg = Config(data_dir=str(tmp_path / "d"))
    cfg.direction.enabled, cfg.direction.spacing_cm = True, 50
    t0 = time.time() - 30
    src = FileSource(str(wav), start_ts=t0)
    app = BarkApp(cfg, src, start_detector=False)
    app.detector = BarkDetector(cfg, HeuristicClassifier(), app.store)
    run_source(app.detector, src)
    hits = [app.array.scan(now=t0 + 1.6 + k * 0.05) for k in range(4)]   # during the bark
    hits = [h for h in hits if h]
    assert hits and all(abs(h[1] - 45) < 6 and h[2] > 10 for h in hits)
    assert app.array.scan(now=t0 + 0.6) is None                          # background only
    c = TestClient(create_app(app))
    f = c.get("/api/array/feed").json()
    assert len(f["scans"]) == len(hits) and app.array.watch_until > time.time()
    app.shutdown()


def test_ongoing_and_summary_alerts(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    cfg = Config(data_dir=str(tmp_path), timezone=TZ)
    app = BarkApp(cfg, None, start_detector=False)
    rex = app.store.add_dog("Rex")["id"]
    now = time.time()
    episode(app.store, now - 3 * 3600, 600, 40, dog=rex)
    AL.save(app.store, [{"name": "Live", "kind": "ntfy", "url": base + "/live", "when": "ongoing", "minutes": 5},
                        {"name": "Day", "kind": "slack", "url": base + "/day", "when": "daily",
                         "at": datetime.fromtimestamp(now - 60, ZoneInfo(TZ)).strftime("%H:%M")}])
    _Sink.got.clear()

    class Ep:                                   # an episode that's still going
        id, start_ts, barks, dogs = 99, now - 400, 50, {rex: 50}
    sent: set = set()
    assert AL.check_ongoing(app.store, cfg, Ep, sent, now) == 1
    assert AL.check_ongoing(app.store, cfg, Ep, sent, now + 60) == 0          # once per clip
    Ep.start_ts = now - 60
    assert AL.check_ongoing(app.store, cfg, type("E", (), {"id": 100, "start_ts": now - 60, "barks": 3, "dogs": {}}), sent, now) == 0
    assert AL.check_scheduled(app.store, cfg, now) == 1
    assert AL.check_scheduled(app.store, cfg, now + 30) == 0                   # once a day
    for _ in range(50):
        if len(_Sink.got) >= 2:
            break
        time.sleep(0.05)
    body = {p: b for p, _, b in _Sink.got}
    assert body["/live"].decode().startswith("Still barking: 7 min so far (50 barks): Rex")
    assert "10:00 of barking in 1 clip" in json.loads(body["/day"])["text"]
    text, data = AL.summary(app.store, cfg, now - 86400, now, [rex + 1])
    assert text == "No barking." and data["clips"] == 0
    srv.shutdown()
    app.shutdown()


def test_clip_filter_matches_row_tags_and_advanced(tmp_path):
    app = BarkApp(Config(data_dir=str(tmp_path), timezone=TZ), None, start_detector=False)
    c = TestClient(create_app(app))
    rex = app.store.add_dog("Rex")["id"]
    t = time.time() - 7200
    sure = episode(app.store, t, 5, 3)                       # high bark score, matched with confidence
    with app.store.tx() as db:
        db.execute("UPDATE barks SET pred_dog_id=?, pred_conf=0.95 WHERE episode_id=?", (rex, sure))
    shaky = episode(app.store, t + 100, 5, 3)                # matched, but not very surely
    with app.store.tx() as db:
        db.execute("UPDATE barks SET pred_dog_id=?, pred_conf=0.6 WHERE episode_id=?", (rex, shaky))
    unnamed = episode(app.store, t + 200, 5, 3)              # nobody matched
    labelled = episode(app.store, t + 300, 5, 3, dog=rex)
    rows = {e["id"]: e["tag"] for e in c.get("/api/episodes?limit=50").json()["items"]}
    assert rows == {sure: "auto", shaky: "unreviewed", unnamed: "unreviewed", labelled: "manual"}
    for tag in ("auto", "unreviewed", "manual"):
        got = {e["id"] for e in c.get(f"/api/episodes?tag={tag}&limit=1").json()["items"]}
        total = c.get(f"/api/episodes?tag={tag}").json()["total"]
        assert total == sum(1 for v in rows.values() if v == tag) and got <= {k for k, v in rows.items() if v == tag}
    # the doubt limit is adjustable (Settings > Advanced): at 55% the shaky match counts as sure
    assert c.put("/api/advanced", json={"values": {"web.DOUBT_MATCH": 0.55}}).status_code == 200
    assert {e["id"]: e["tag"] for e in c.get("/api/episodes").json()["items"]}[shaky] == "auto"
    assert c.get("/api/episodes?tag=auto").json()["total"] == 2
    c.put("/api/advanced", json={"reset": True})
    app.shutdown()


def test_dog_page_and_theme_pack(tmp_path):
    import io
    import zipfile
    app = BarkApp(Config(data_dir=str(tmp_path), timezone=TZ), None, start_detector=False)
    c = TestClient(create_app(app))
    rex = c.post("/api/dogs", json={"name": "Rex", "breed": "Beagle"}).json()
    assert rex["breed"] == "Beagle"
    episode(app.store, time.time() - 3600, 20, 8, dog=rex["id"])
    d = c.get(f"/api/dogs/{rex['id']}/detail?days=30").json()
    assert d["totals"]["barks"] == 8 and len(d["daily"]) == 30 and sum(d["hours"]) == 8
    assert sum(map(sum, d["week"])) == 8 and d["rhythm"]["pause_s"] == 2.5
    assert c.get("/api/dogs/999/detail").status_code == 404
    # a theme can carry the live band pictures, and travels as a .zip
    mine = {**c.get("/api/themes/default.json").json(), "name": "Pack"}
    c.post("/api/themes", json=mine)
    assert c.get("/api/media").json()["source"] == "own"
    assert c.post("/api/themes/pack/pictures", json={}).json()["theme"]["pictures"] == 3
    m = c.get("/api/media").json()
    assert m["source"] == "theme" and c.get(m["bark"][0]["url"]).status_code == 200
    z = c.get("/api/themes/pack.zip").content
    assert sorted(zipfile.ZipFile(io.BytesIO(z)).namelist())[0] == "active/sit.svg"
    c.delete("/api/themes/pack")
    assert c.put("/api/theme-pack", content=z).json()["theme"]["pictures"] == 3
    assert c.put("/api/theme-pack", content=b"not a theme").status_code == 400
    app.shutdown()
