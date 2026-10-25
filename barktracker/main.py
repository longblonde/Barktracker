"""Command line entry point:  python -m barktracker <command>"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta

from .config import load_config


def _setup_logging(verbose: bool):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def cmd_run(args, cfg):
    import uvicorn

    from .app import BarkApp
    from .audio import FileSource, MicSource
    from .web import create_app

    if args.file:
        source = FileSource(args.file, speed=args.speed, loop=args.loop)
    elif args.no_audio:
        source = None
    else:
        a = cfg.audio
        source = MicSource(a.device, a.samplerate, a.channels, a.block_seconds, a.gain_db)
    bark = BarkApp(cfg, source)
    app = create_app(bark)

    def bye(*_):
        bark.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    logging.getLogger("barktracker").info("Dashboard on http://%s:%d", cfg.web.host, cfg.web.port)
    try:
        uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="warning")
    finally:
        bark.shutdown()


def cmd_devices(args, cfg):
    from .audio import list_devices
    print(list_devices())


def cmd_ingest(args, cfg):
    """Process a WAV file into the log (offline), e.g. a recording made while you were out."""
    from .app import BarkApp
    from .audio import FileSource
    from .classifier import make_classifier
    from .detector import BarkDetector, run_source

    start = datetime.fromisoformat(args.start).timestamp() if args.start else time.time()
    bark = BarkApp(cfg, None, start_detector=False)
    det = BarkDetector(cfg, make_classifier(cfg.detection.mode, cfg.detection.model_path),
                       bark.store, bark.identifier)
    run_source(det, FileSource(args.file, start_ts=start))
    print(f"{args.file}: {dict(det.stats)}")


def cmd_refine(args, cfg):
    """Re-scan saved clips so bark marks line up exactly (runs automatically for new episodes)."""
    from .app import BarkApp
    from .classifier import make_classifier
    from .refine import refine_episode
    bark = BarkApp(cfg, None, start_detector=False)
    where = "" if args.all else " AND refined=0"
    rows = bark.store.q(f"SELECT id FROM episodes WHERE clip_seconds IS NOT NULL{where} ORDER BY start_ts")
    clf = make_classifier(cfg.detection.mode, cfg.detection.model_path)
    for i, r in enumerate(rows, 1):
        info = refine_episode(bark.store, r["id"], clf, bark.identifier, cfg.detection.bark_threshold)
        print(f"[{i}/{len(rows)}] episode {r['id']}: {info}")
    print(f"Refined {len(rows)} episodes")


def cmd_retrain(args, cfg):
    from .app import BarkApp
    print(BarkApp(cfg, None, start_detector=False).retrain())


def cmd_level(args, cfg):
    """Live input meter for setting up mic placement and gain."""
    import math

    import numpy as np

    from . import features as F
    from .audio import MicSource
    a = cfg.audio
    src = MicSource(a.device, a.samplerate, a.channels, a.block_seconds, a.gain_db)
    acc = []
    for x, _ in src:
        acc.append(x)
        if sum(len(c) for c in acc) >= F.SR // 5:
            buf = np.concatenate(acc)
            db = F.dbfs(buf)
            pk = 20 * math.log10(float(np.abs(buf).max()) + 1e-9)
            bar = "#" * int(max(0, db + 80) / 1.5)
            print(f"\r{db:6.1f} dBFS  peak {pk:6.1f}  {bar:<54}", end="", flush=True)
            acc = []


def cmd_demo(args, cfg):
    """Fill the database with ~2 weeks of synthetic barking from two dogs, run through the real
    detector, so you can try the dashboard before the Pi has heard anything."""
    import numpy as np

    from . import features as F
    from . import synth
    from .app import BarkApp
    from .classifier import HeuristicClassifier
    from .detector import BarkDetector

    bark = BarkApp(cfg, None, start_detector=False)
    store = bark.store
    names = list(synth.DOG_VOICES)
    existing = {d["name"]: d["id"] for d in store.dogs()}
    homes = {"Big dog": ("J. Smith", "123 Elm St", "Black lab, large"),
             "Small dog": ("R. Lee", "9 Oak Ave", "White terrier mix")}
    ids = {}
    for n in names:
        o, addr, desc = homes.get(n, ("", "", ""))
        ids[n] = existing.get(n) or store.add_dog(n, owner=o, address=addr, description=desc)["id"]
    det = BarkDetector(cfg, HeuristicClassifier(), store, None)
    rng = np.random.default_rng(args.seed)
    now = datetime.now().replace(second=0, microsecond=0)
    truth: list[tuple[int, str]] = []
    hours = [6, 7, 7, 8, 11, 11, 12, 15, 16, 17, 17, 18, 19, 20, 22]
    for day in range(args.days, -1, -1):
        for _ in range(int(rng.integers(2, 7))):
            t = (now - timedelta(days=day)).replace(hour=int(rng.choice(hours)),
                                                     minute=int(rng.integers(0, 60)))
            if t > now:
                continue
            who = rng.choice(["Big dog", "Small dog", "both"], p=[0.45, 0.35, 0.2])
            events, cursor = [], 1.0
            for dog in (names if who == "both" else [who]):
                n = int(rng.integers(3, 25))
                events += synth.bout(cursor + rng.uniform(0, 3), dog, n, rng=rng)
            events.sort()
            total = events[-1][0] + 4
            audio = synth.scene(events, total, seed=int(rng.integers(1e9)))
            before = store.q1("SELECT COALESCE(MAX(id),0) m FROM episodes")["m"]
            det.base_ts = t.timestamp() - det.received / F.SR   # jump the detector's clock
            det.feed(audio, t.timestamp())
            det.flush()
            new = store.q("SELECT id FROM episodes WHERE id>?", (before,))
            for e in new:
                truth.append((e["id"], who))
    # a few long sessions (bursts with short pauses), some late at night
    for day, hour, minute, mins in ((9, 22, 40, 14), (6, 13, 10, 12), (4, 23, 5, 11), (2, 21, 50, 25), (1, 6, 20, 9)):
        t = (now - timedelta(days=day)).replace(hour=hour, minute=minute)
        events, cur = [], 1.0
        while cur < mins * 60:
            events += synth.bout(cur, "Big dog", int(rng.integers(8, 25)), rng=rng)
            cur = events[-1][0] + rng.uniform(10, 45)
        total = events[-1][0] + 4
        audio = synth.scene(events, total, seed=int(rng.integers(1e9)))
        before = store.q1("SELECT COALESCE(MAX(id),0) m FROM episodes")["m"]
        det.base_ts = t.timestamp() - det.received / F.SR
        det.feed(audio, t.timestamp())
        det.flush()
        for e in store.q("SELECT id FROM episodes WHERE id>?", (before,)):
            truth.append((e["id"], "Big dog"))
    # label about a third of the single-dog episodes so the identifier has something to learn from
    labelled = 0
    for eid, who in truth:
        if who != "both" and rng.random() < 0.35:
            store.label_episode(eid, ids[who])
            labelled += 1
    info = bark.retrain()
    print(f"Created {len(truth)} episodes ({labelled} labelled). Identifier: {info}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="barktracker", description="Raspberry Pi bark logger")
    p.add_argument("-c", "--config", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="listen and serve the dashboard (default service command)")
    r.add_argument("--file", help="replay a WAV file instead of the microphone")
    r.add_argument("--speed", type=float, default=1.0, help="replay speed for --file (0 = max)")
    r.add_argument("--loop", action="store_true", help="loop --file forever")
    r.add_argument("--no-audio", action="store_true", help="dashboard only")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("devices", help="list audio input devices").set_defaults(fn=cmd_devices)
    sub.add_parser("level", help="live mic level meter for setup").set_defaults(fn=cmd_level)
    sub.add_parser("retrain", help="retrain dog identification from labels").set_defaults(fn=cmd_retrain)
    rf = sub.add_parser("refine", help="re-scan saved clips so bark marks line up exactly")
    rf.add_argument("--all", action="store_true", help="also redo episodes already refined")
    rf.set_defaults(fn=cmd_refine)

    i = sub.add_parser("ingest", help="process a WAV recording into the log")
    i.add_argument("file")
    i.add_argument("--start", help="recording start time, e.g. 2026-10-24T08:00")
    i.set_defaults(fn=cmd_ingest)

    d = sub.add_parser("demo", help="generate synthetic demo data")
    d.add_argument("--days", type=int, default=14)
    d.add_argument("--seed", type=int, default=7)
    d.set_defaults(fn=cmd_demo)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
