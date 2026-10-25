"""Bark marks must line up with the barks in the clip, even when noise trips the trigger early."""
import numpy as np

from barktracker import features as F
from barktracker import synth
from barktracker.audio import read_wav
from barktracker.classifier import HeuristicClassifier
from barktracker.config import Config
from barktracker.detector import BarkDetector
from barktracker.storage import Store

T0 = 1_760_000_000.0


def noisy_scene(times, total=14.0, seed=4):
    """Barks at `times`, each preceded 0.45 s earlier by a short low rumble that crosses the
    loudness trigger but isn't a bark, over steady room noise (like a webcam's AGC hiss)."""
    rng = np.random.default_rng(seed)
    x = synth.scene([(t, "Big dog") for t in times], total, noise_db=-48, seed=seed)
    for t in times:
        i = int((t - 0.45) * F.SR)
        n = int(0.12 * F.SR)
        rumble = np.convolve(rng.standard_normal(n), np.ones(40) / 40, "same").astype(np.float32)
        x[i:i + n] += rumble * 0.6
    return np.clip(x, -1, 1)


def run(cfg, x):
    store = Store(cfg.db_path)
    det = BarkDetector(cfg, HeuristicClassifier(), store)
    for i in range(0, len(x), 1600):
        det.feed(x[i:i + 1600], T0 + i / F.SR)
    det.flush()
    return store


def offsets(store):
    ep = store.q1("SELECT * FROM episodes")
    return ep, [b["ts"] - ep["clip_start_ts"] for b in store.barks_for(ep["id"])]


def test_live_marks_align_with_barks(tmp_path):
    truth = [2.0, 3.1, 4.4, 5.3, 6.9, 8.0]
    cfg = Config(data_dir=str(tmp_path))
    store = run(cfg, noisy_scene(truth))
    ep, offs = offsets(store)
    clip_start = ep["clip_start_ts"] - T0
    got = [o + clip_start for o in offs]
    assert len(got) == len(truth)
    for g, t in zip(got, truth):
        assert abs(g - t) < 0.05, (got, truth)
    # and the marks match the audio actually saved in the clip
    x, sr = read_wav(ep["clip_path"])
    env = F.band_envelope(x)
    for o in offs:
        k = int(o * F.SR / F.HOP)
        assert env[k:k + 20].max() > np.percentile(env, 50) + 10      # loud bark right at the mark


def test_refine_fixes_existing_clip_and_keeps_labels(tmp_path):
    from barktracker.refine import refine_episode
    truth = [2.0, 3.1, 4.4, 5.3, 6.9, 8.0]
    cfg = Config(data_dir=str(tmp_path))
    store = run(cfg, noisy_scene(truth))
    ep, _ = offsets(store)
    rex = store.add_dog("Rex")["id"]
    barks = store.barks_for(ep["id"])
    # make it look like an old, badly placed episode: marks 0.5 s early and 20 ms long,
    # one bark missing, and one bark labelled by the user
    with store.tx() as c:
        c.executemany("UPDATE barks SET ts=ts-0.5, duration=0.02 WHERE id=?", [(b["id"],) for b in barks])
        c.execute("DELETE FROM barks WHERE id=?", (barks[3]["id"],))
    store.label_barks([barks[1]["id"]], rex)

    info = refine_episode(store, ep["id"], HeuristicClassifier(), None, cfg.detection.bark_threshold)
    assert info["after"] == 6 and info["labels_carried"] == 1
    ep2, offs = offsets(store)
    got = [o + ep2["clip_start_ts"] - T0 for o in offs]
    for g, t in zip(got, truth):
        assert abs(g - t) < 0.05, (got, truth)
    rows = store.barks_for(ep["id"])
    assert all(r["duration"] > 0.1 for r in rows)
    assert [r["label_dog_id"] for r in rows].count(rex) == 1
    assert rows[1]["label_dog_id"] == rex                       # stayed on the same bark
    assert ep2["bark_count"] == 6 and ep2["refined"] == 1
    assert abs(ep2["start_ts"] - (T0 + truth[0])) < 0.05


def test_refine_drops_non_barks_between_barks(tmp_path):
    from barktracker.refine import refine_episode
    truth = [2.0, 3.4, 4.8]
    cfg = Config(data_dir=str(tmp_path))
    store = run(cfg, noisy_scene(truth))       # the rumbles before each bark must not be counted
    ep, _ = offsets(store)
    refine_episode(store, ep["id"], HeuristicClassifier(), None, cfg.detection.bark_threshold)
    assert store.q1("SELECT bark_count n FROM episodes")["n"] == 3
