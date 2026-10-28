import time
from pathlib import Path

import numpy as np
import pytest

from barktracker import features as F
from barktracker import synth
from barktracker.app import BarkApp, repair_wav
from barktracker.audio import read_wav
from barktracker.classifier import HeuristicClassifier, make_classifier
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.identify import DogIdentifier, retrain_and_repredict
from barktracker.storage import NOT_BARK, Store

MODEL = Path(__file__).resolve().parents[1] / "models" / "yamnet.tflite"
T0 = 1_760_000_000.0


@pytest.fixture
def cfg(tmp_path):
    c = Config(data_dir=str(tmp_path))
    c.detection.model_path = str(MODEL)
    c.detection.episode_gap = 5.0
    return c


def run(cfg, audio, clf=None, ident=None, store=None):
    store = store or Store(cfg.db_path)
    det = BarkDetector(cfg, clf or HeuristicClassifier(), store, ident)
    for i in range(0, len(audio), 1234):     # odd chunk size on purpose
        det.feed(audio[i:i + 1234], T0 + i / F.SR)
    det.flush()
    return det, store


def test_counts_barks_and_groups_episodes(cfg):
    rng = np.random.default_rng(3)
    ev = synth.bout(2.0, "Big dog", 6, spacing=(0.7, 1.0), rng=rng) \
        + synth.bout(25.0, "Small dog", 4, spacing=(0.6, 0.9), rng=rng)
    det, store = run(cfg, synth.scene(ev, 40.0, seed=1))
    eps = store.q("SELECT * FROM episodes ORDER BY start_ts")
    assert len(eps) == 2
    assert [e["bark_count"] for e in eps] == [6, 4]
    assert abs(eps[0]["start_ts"] - (T0 + 2.0)) < 0.1
    # duration ~ first onset to end of last bark
    assert 4.0 < eps[0]["end_ts"] - eps[0]["start_ts"] < 7.5
    for e in eps:
        x, sr = read_wav(e["clip_path"])
        assert sr == 16000
        assert abs(len(x) / sr - e["clip_seconds"]) < 0.01
        # preroll + barking + postroll
        assert e["clip_seconds"] > (e["end_ts"] - e["start_ts"]) + 3.0


def test_quiet_room_and_noise_make_no_episodes(cfg):
    rng = np.random.default_rng(0)
    x = rng.standard_normal(20 * F.SR).astype(np.float32) * 0.002
    # a steady hum that switches on halfway: should not count as barking
    t = np.arange(len(x)) / F.SR
    x[len(x) // 2:] += 0.05 * np.sin(2 * np.pi * 120 * t[len(x) // 2:])
    det, store = run(cfg, x)
    assert store.q("SELECT COUNT(*) n FROM episodes")[0]["n"] == 0


def test_identifier_learns_two_dogs(cfg):
    rng = np.random.default_rng(5)
    store = Store(cfg.db_path)
    big = store.add_dog("Big")["id"]
    small = store.add_dog("Small")["id"]
    # training sessions, labelled
    for dog, did, start in (("Big dog", big, 2.0), ("Small dog", small, 30.0)):
        ev = synth.bout(start, dog, 8, rng=rng)
        run(cfg, synth.scene(ev, start + 15, seed=int(start)), store=store)
    for e in store.q("SELECT id FROM episodes"):
        b = store.barks_for(e["id"])[0]
        did = big if b["ts"] < T0 + 20 else small
        store.label_episode(e["id"], did)
    ident = DogIdentifier(k=5, min_labels=3, min_conf=0.55)
    info = retrain_and_repredict(store, ident)
    assert info["trained"]
    # new, unlabelled audio is identified as it is detected
    ev = synth.bout(2.0, "Small dog", 5, rng=rng) + synth.bout(20.0, "Big dog", 5, rng=rng)
    store2 = Store(Path(cfg.data_dir) / "second.db")
    store2.add_dog("Big"), store2.add_dog("Small")
    run(cfg, synth.scene(ev, 32.0, seed=99), ident=ident, store=store2)
    eps = store2.q("SELECT id FROM episodes ORDER BY start_ts")
    assert len(eps) == 2
    first = [b["pred_dog_id"] for b in store2.barks_for(eps[0]["id"])]
    second = [b["pred_dog_id"] for b in store2.barks_for(eps[1]["id"])]
    assert first.count(small) >= 4
    assert second.count(big) >= 4


def test_not_a_bark_suppression(cfg):
    store = Store(cfg.db_path)
    ident = DogIdentifier(k=3, min_labels=3, min_conf=0.5)
    rng = np.random.default_rng(1)
    ev = synth.bout(2.0, "Small dog", 6, rng=rng)
    run(cfg, synth.scene(ev, 12.0, seed=4), store=store)
    eid = store.q("SELECT id FROM episodes")[0]["id"]
    store.label_episode(eid, NOT_BARK)
    assert store.q1("SELECT false_positive FROM episodes WHERE id=?", (eid,))["false_positive"] == 1
    retrain_and_repredict(store, ident)
    det, _ = run(cfg, synth.scene(synth.bout(2.0, "Small dog", 5, rng=rng), 12.0, seed=8),
                 ident=ident, store=store)
    assert det.stats["suppressed"] >= 4
    assert store.q1("SELECT COUNT(*) n FROM episodes WHERE false_positive=0")["n"] == 0


def test_crash_recovery_repairs_open_episode(cfg):
    store = Store(cfg.db_path)
    det = BarkDetector(cfg, HeuristicClassifier(), store)
    x = synth.scene(synth.bout(2.0, "Big dog", 4), 8.0, seed=2)
    det.feed(x, T0)        # no flush: simulates power loss mid-episode
    ep = store.q1("SELECT * FROM episodes")
    assert ep["clip_seconds"] is None
    det.ep.writer._w._file.flush()   # data on disk, header not finalised
    app = BarkApp(cfg, None, start_detector=False)
    ep = app.store.q1("SELECT * FROM episodes")
    assert ep["clip_seconds"] > 0 and ep["bark_count"] == 4
    y, _ = read_wav(ep["clip_path"])
    assert len(y) > 0
    app.shutdown()


def test_features_shape_and_resample():
    b = synth.bark()
    assert F.bark_features(b).shape == (F.FEATURE_DIM,)
    y = F.resample(np.zeros(48000, np.float32), 48000)
    assert len(y) == 16000


@pytest.mark.skipif(not MODEL.exists(), reason="YAMNet model not downloaded")
def test_yamnet_loads_and_scores():
    clf = make_classifier("yamnet", str(MODEL))
    assert clf.name == "yamnet"
    p, d = clf.bark_probability(np.zeros(15600, np.float32))
    assert p < 0.2 and "top" in d


def test_detector_records_decisions_and_capture(tmp_path):
    import time as _t
    from barktracker.app import BarkApp
    from barktracker.classifier import HeuristicClassifier
    from barktracker.config import Config
    from barktracker.detector import BarkDetector
    from barktracker import synth
    import numpy as np
    cfg = Config(data_dir=str(tmp_path))
    app = BarkApp(cfg, None, start_detector=False)
    det = BarkDetector(cfg, HeuristicClassifier(), app.store)
    det.tune_until = _t.time() + 60                       # as if the tuning page were open
    rng = np.random.default_rng(2)
    x = synth.scene(synth.bout(2.0, "Big dog", 4, rng=rng), 14.0, seed=3)
    quiet = synth.bark(rng=rng) * 0.006                    # a faint bark that shouldn't trigger
    x[int(11 * 16000):int(11 * 16000) + len(quiet)] += quiet
    det.feed(x, _t.time() - 14)
    det.flush()
    kinds = [e["kind"] for e in det.events]
    assert kinds.count("bark") == 4
    q = [e for e in det.events if e["kind"] == "quiet"]
    assert q and all(e["rise"] < cfg.detection.trigger_db for e in q if e["reason"] == "below_trigger")
    assert any("prob" in e for e in q)                     # scored while tuning
    cap = det.capture(10)
    assert abs(len(cap["audio"]) / 16000 - 10) < 0.1 and cap["trace"] and cap["events"]
    assert all(0 <= e["t"] <= 10.1 for e in cap["events"])
    feed = det.tune_feed(-1, 0)
    assert len(feed["cols"]) == 64 * len(feed["levels"]) and feed["events"]
    app.shutdown()
