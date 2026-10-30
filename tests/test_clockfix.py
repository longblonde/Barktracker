"""After a boot the clock can jump when the time server answers: what was logged before the jump
gets the corrected time, and its clip the matching name."""
import time
from pathlib import Path

from barktracker import clockfix
from barktracker import features as F
from barktracker import synth
from barktracker.classifier import HeuristicClassifier
from barktracker.clips import clip_path_for
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.storage import Store

T0 = 1_760_000_000.0
BARKS = [1.0, 3.0, 9.0, 11.0]


def listen(tmp_path, monkeypatch, jump=0.0, gap=0.0, at=6.0):
    """Feed a scene like the mic would; at `at` seconds the wall clock jumps by `jump` (a time
    server) or the audio timestamps skip `gap` seconds with the clock unchanged (a mic stall)."""
    cfg = Config(data_dir=str(tmp_path))
    store = Store(cfg.db_path)
    old = store.create_episode(T0 - 500, None, T0 - 502)        # from before this boot
    store.finish_episode(old, T0 - 490, 5, -20, 0.9, 14)
    monkeypatch.setattr(clockfix, "boot_id", lambda: "boot-1")
    clockfix.mark_boot(store)
    det = BarkDetector(cfg, HeuristicClassifier(), store)
    clock = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: 1000 + clock["t"])
    monkeypatch.setattr(time, "time", lambda: T0 + clock["t"] + (jump if clock["t"] >= at else 0.0))
    x = synth.scene([(t, "Big dog") for t in BARKS], 14.0, noise_db=-48, seed=2)
    for i in range(0, len(x), 800):
        clock["t"] = i / F.SR
        det.feed(x[i:i + 800], time.time() + (gap if clock["t"] >= at else 0.0))
    det.flush()
    return cfg, store, old


def test_time_server_jump_fixes_what_was_logged(tmp_path, monkeypatch):
    cfg, store, old = listen(tmp_path, monkeypatch, jump=180.0)
    eps = store.q("SELECT * FROM episodes WHERE id != ? ORDER BY id", (old,))
    assert eps, "no barking detected"
    barks = [b["ts"] - T0 for b in store.q("SELECT ts FROM barks ORDER BY ts")]
    assert len(barks) == len(BARKS)
    for got, want in zip(barks, BARKS):                       # all on the corrected clock
        assert abs(got - (want + 180)) < 0.5, barks
    ep = eps[0]
    assert abs(ep["start_ts"] - (T0 + 181)) < 0.5 and ep["end_ts"] > ep["start_ts"]
    assert Path(ep["clip_path"]) == clip_path_for(cfg.clips_path, ep["start_ts"])   # renamed to match
    assert Path(ep["clip_path"]).exists()
    assert store.q1("SELECT start_ts FROM episodes WHERE id=?", (old,))["start_ts"] == T0 - 500   # untouched
    # another jump later only moves what was logged after this fix
    assert clockfix.correct(store, 50.0, cfg.clips_path)["barks"] == 2
    again = [b["ts"] - T0 for b in store.q("SELECT ts FROM barks ORDER BY ts")]
    assert again[:2] == barks[:2] and abs(again[2] - barks[2] - 50) < 1e-6


def test_mic_stall_is_not_a_clock_jump(tmp_path, monkeypatch):
    cfg, store, old = listen(tmp_path, monkeypatch, gap=60.0)
    barks = [b["ts"] - T0 for b in store.q("SELECT ts FROM barks ORDER BY ts")]
    assert len(barks) == len(BARKS)
    assert abs(barks[0] - BARKS[0]) < 0.5 and abs(barks[1] - BARKS[1]) < 0.5   # before the stall: as logged
