import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from barktracker import backup as BK
from barktracker import synth
from barktracker.app import BarkApp
from barktracker.classifier import HeuristicClassifier
from barktracker.clips import enforce_retention
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.web import create_app


def install(tmp_path, name, bouts=()):
    cfg = Config(data_dir=str(tmp_path / name))
    cfg.backup.allow_same_disk = True           # tests use one disk for "SD card" and "USB drive"
    app = BarkApp(cfg, None, start_detector=False)
    app.restart = lambda: False                 # don't exit the test process
    det = BarkDetector(cfg, HeuristicClassifier(), app.store)
    rng = np.random.default_rng(3)
    t0 = time.time() - 7200
    for i, (dog, n) in enumerate(bouts):
        det.base_ts = t0 + i * 120 - det.received / 16000
        det.feed(synth.scene(synth.bout(2.0, dog, n, rng=rng), 10.0, seed=i), t0 + i * 120)
        det.flush()
    return cfg, app, TestClient(create_app(app))


def wait_backup(c):
    for _ in range(200):
        st = c.get("/api/backup").json()
        if not st["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("backup didn't finish")


def test_backup_then_restore_on_a_new_install(tmp_path):
    usb = tmp_path / "usb"
    usb.mkdir()
    cfg, app, c = install(tmp_path, "pi1", [("Big dog", 5), ("Small dog", 4), ("Big dog", 6)])
    rex = c.post("/api/dogs", json={"name": "Rex", "size": "large", "owner": "J. Smith"}).json()
    eps = c.get("/api/episodes").json()["items"]
    c.post(f"/api/episodes/{eps[0]['id']}/label", json={"dog_id": rex["id"]})
    c.post(f"/api/episodes/{eps[1]['id']}/label", json={"dog_id": 0})            # not a bark
    c.put("/api/settings", json={"values": {"detection.bark_threshold": 0.3}})
    (cfg.media_path / "bark").mkdir(parents=True)
    (cfg.media_path / "bark" / "mine.png").write_bytes(b"\x89PNG....")

    # not set yet / bad folder
    assert c.post("/api/backup/run").status_code == 400
    assert c.put("/api/settings", json={"values": {"backup.path": "relative/dir"}}).status_code == 400
    c.put("/api/settings", json={"values": {"backup.path": str(usb), "backup.keep": 2}})
    st = c.get("/api/backup").json()
    assert st["target"]["ok"] and st["settings"]["backup.path"] == str(usb)

    assert c.post("/api/backup/run").status_code == 200
    st = wait_backup(c)
    assert st["last"]["last_error"] is None, st["last"]
    res = st["last"]["last_result"]
    root = usb / BK.DIRNAME
    clips = sorted(p.relative_to(cfg.clips_path) for p in cfg.clips_path.rglob("*.wav"))
    assert res["clip_files"] == len(clips) == 3
    assert sorted(p.relative_to(root / "clips") for p in (root / "clips").rglob("*.wav")) == clips
    assert (root / "media" / "bark" / "mine.png").exists()
    # snapshots rotate (keep 2)
    for _ in range(2):
        time.sleep(1.05)
        app.backup.run("test")
    assert len(list((root / "db").glob("barktracker-*.db"))) == 2
    X1, y1 = app.store.training_set()
    before = {k: c.get("/api/episodes").json()["total"] for k in ("n",)}

    # a second, unrelated install refuses to write over this backup
    cfg2, app2, c2 = install(tmp_path, "pi2")
    c2.put("/api/settings", json={"values": {"backup.path": str(usb)}})
    with pytest.raises(BK.BackupError, match="another Barktracker install"):
        app2.backup.run("test")

    # ...but can restore it: inspect, stage, then the next start swaps it in
    info = c2.post("/api/backup/inspect", json={"path": str(usb)}).json()
    assert info["clip_files"] == 3 and info["snapshots"][0]["episodes"] == 3
    assert info["snapshots"][0]["labelled_barks"] > 0
    assert c2.post("/api/backup/restore", json={"path": str(usb)}).status_code == 400   # needs confirmation
    c2.post("/api/backup/restore", json={"path": str(usb), "confirm": "RESTORE"})
    for _ in range(200):
        if (cfg2.data_path / BK.PENDING).exists():
            break
        time.sleep(0.05)
    assert (cfg2.data_path / BK.PENDING).exists()
    app2.shutdown()

    cfg3 = Config(data_dir=str(tmp_path / "pi2"))
    cfg3.backup.allow_same_disk = True
    app3 = BarkApp(cfg3, None, start_detector=False)
    c3 = TestClient(create_app(app3))
    assert c3.get("/api/episodes").json()["total"] == before["n"]
    assert cfg3.detection.bark_threshold == 0.3                               # settings came along
    assert [d["name"] for d in c3.get("/api/dogs").json()["dogs"]] == ["Rex"]
    assert c3.get("/api/dogs").json()["dogs"][0]["owner"] == "J. Smith"
    X3, y3 = app3.store.training_set()                                        # training data intact
    assert X3.shape == X1.shape and (y3 == y1).all()
    for e in c3.get("/api/episodes").json()["items"]:                          # clips re-pointed here
        d = c3.get(f"/api/episodes/{e['id']}").json()
        assert d["has_clip"]
        assert c3.get(f"/api/episodes/{e['id']}/audio").status_code == 200
    assert (cfg3.media_path / "bark" / "mine.png").exists()
    assert list(cfg3.data_path.glob("pre-restore-*/barktracker.db"))           # old db kept
    assert c3.get("/api/backup").json()["last_restore"]["snapshot"].startswith("barktracker-")
    # and it carries on backing up to the same drive
    app3.backup.run("test")
    app.shutdown(); app3.shutdown()


def test_backup_mirror_rules(tmp_path):
    usb = tmp_path / "usb"
    usb.mkdir()
    cfg, app, c = install(tmp_path, "pi", [("Big dog", 5), ("Small dog", 4)])
    cfg.backup.path = str(usb)
    app.backup.run("test")
    root = usb / BK.DIRNAME
    assert len(list((root / "clips").rglob("*.wav"))) == 2
    eps = app.store.q("SELECT id, clip_path FROM episodes ORDER BY start_ts")
    # a clip lost from the SD card but still in the log stays on the drive
    Path(eps[0]["clip_path"]).unlink()
    app.backup.run("test")
    assert len(list((root / "clips").rglob("*.wav"))) == 2
    # a clip removed by retention (no longer in the log) is removed from the drive too
    app.store.clear_clip(eps[1]["id"])
    Path(eps[1]["clip_path"]).unlink()
    out = app.backup.run("test")
    assert out["removed"] == 1 and len(list((root / "clips").rglob("*.wav"))) == 1
    app.shutdown()


def test_backup_refuses_sd_card_and_missing_drive(tmp_path):
    cfg, app, c = install(tmp_path, "pi")
    cfg.backup.allow_same_disk = False
    (tmp_path / "mnt").mkdir()
    chk = c.post("/api/backup/check", json={"path": str(tmp_path / "mnt")}).json()
    assert not chk["ok"] and chk["same_disk"]                # empty mount point on the SD card
    chk = c.post("/api/backup/check", json={"path": str(tmp_path / "gone")}).json()
    assert not chk["ok"] and "plugged in" in chk["reason"]
    cfg.backup.path = str(tmp_path / "gone")
    with pytest.raises(BK.BackupError):
        app.backup.run("test")
    st = c.get("/api/backup").json()
    assert st["last"]["last_error"] and not app.backup.due()   # waits before retrying
    app.shutdown()


def test_database_download_and_upload_restore(tmp_path):
    cfg, app, c = install(tmp_path, "pi1", [("Big dog", 5)])
    r = c.get("/api/backup/database.db")
    assert r.status_code == 200 and r.content.startswith(b"SQLite format 3")
    assert not list(cfg.data_path.glob(".download-*"))
    cfg2, app2, c2 = install(tmp_path, "pi2")
    assert c2.put("/api/backup/restore-upload?confirm=RESTORE", content=b"not a db" * 50).status_code == 400
    assert c2.put("/api/backup/restore-upload", content=r.content).status_code == 400
    out = c2.put("/api/backup/restore-upload?confirm=RESTORE", content=r.content).json()
    assert out["staged"] and out["episodes"] == 1
    app2.shutdown()
    app3 = BarkApp(Config(data_dir=str(tmp_path / "pi2")), None, start_detector=False)
    assert app3.store.q1("SELECT COUNT(*) n FROM episodes")["n"] == 1
    app.shutdown(); app3.shutdown()
