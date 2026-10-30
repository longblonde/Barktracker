"""Wires the detector, storage, identifier, maintenance and notifications together."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from .classifier import make_classifier
from .clips import enforce_retention
from .config import Config
from .detector import BarkDetector, run_source
from .identify import DogIdentifier, retrain_and_repredict
from . import backup as backup_mod
from . import settings as settings_mod
from .storage import Store

log = logging.getLogger(__name__)


class BarkApp:
    def __init__(self, cfg: Config, source=None, start_detector: bool = True):
        self.cfg = cfg
        cfg.clips_path.mkdir(parents=True, exist_ok=True)
        restored = backup_mod.apply_pending_restore(cfg)          # a restore staged before the restart
        self.store = Store(cfg.db_path)
        if restored:
            self.store.set_meta("last_restore", restored)
        self.defaults = settings_mod.apply_saved(cfg, self.store)   # dashboard overrides
        self._keep_ordinance()
        self.backup = backup_mod.Backup(cfg, self.store, getattr(cfg, "source_file", None))
        from . import tuning
        tuning.ensure_schema(self.store)
        from .array import ensure_schema as array_schema
        array_schema(self.store)
        self._tune_clf = None
        self._tune_lock = threading.Lock()
        self.restart_pending = False
        self.recover()
        ic = cfg.identify
        self.identifier = DogIdentifier(ic.k, ic.min_labels_per_dog, ic.min_confidence) if ic.enabled else None
        if self.identifier:
            retrain_and_repredict(self.store, self.identifier, self._use_direction())
        self.source = source
        if source is not None and hasattr(source, "switch"):      # mic choice / boost saved from the dashboard
            source.device = cfg.audio.device
            source.gain = 10 ** (cfg.audio.gain_db / 20)
        from .array import ArrayMonitor          # experimental direction test (off unless enabled)
        self.array = ArrayMonitor(cfg, self)
        if source is not None and hasattr(source, "tap"):
            source.tap = self.array.on_tap
        if source is not None and hasattr(source, "prefer_native"):
            source.prefer_native = bool(cfg.direction.enabled)     # finer timing for the direction estimate
        self.detector: BarkDetector | None = None
        self.stop = threading.Event()
        self.error: str | None = None
        self.threads: list[threading.Thread] = []
        self.refine_q: queue.Queue = queue.Queue()
        if start_detector and source is not None:
            clf = make_classifier(cfg.detection.mode, cfg.detection.model_path)
            self.detector = BarkDetector(cfg, clf, self.store, self.identifier, self.episode_closed)
            self._spawn(self._detect_loop, "detector")
            if cfg.detection.refine:
                self._spawn(self._refine_loop, "refine")
                self.queue_unrefined()
        if start_detector:          # only the running service prunes clips and backs up (not one-off commands)
            self._spawn(self._maintenance_loop, "maintenance")
            self._spawn(self._backup_loop, "backup")
            self.array.start()

    def _keep_ordinance(self) -> None:
        """Installs from before the generic default (v0.11) ran on the Burnsville rules without saving
        them; pin those so an update doesn't swap a working setup for placeholder text."""
        from . import __version__
        from . import ordinance as O
        if self.store.get_meta("first_version") is not None:
            return                                   # a database this version (or newer) created
        if self.store.get_meta("ordinance") is None and self.store.q1("SELECT 1 FROM episodes LIMIT 1"):
            O.save_ordinance(self.store, {"reset": True, "preset": "burnsville_mn"})
        self.store.set_meta("first_version", __version__)

    def _spawn(self, fn, name):
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    def _detect_loop(self):
        while not self.stop.is_set():
            try:
                run_source(self.detector, self.source, self.stop)
                if not getattr(self.source, "loop", True):
                    log.info("Audio source finished")
                    return
            except Exception as e:  # noqa: BLE001  - mic unplugged etc.: log, wait, retry
                self.error = f"{type(e).__name__}: {e}"
                log.exception("Audio/detector error; retrying in 10s")
                self.stop.wait(10)

    def episode_closed(self, info: dict) -> None:
        if self.cfg.detection.refine:
            self.refine_q.put((0, info["id"]))
        self.notify(info)

    def queue_unrefined(self, limit: int = 2000) -> int:
        from .refine import REFINE_VERSION
        if self.store.get_meta("refine_version", 0) != REFINE_VERSION:
            # the re-scan logic improved since these clips were done: redo them once (labels are kept)
            with self.store.tx() as c:
                c.execute("UPDATE episodes SET refined=0 WHERE clip_path IS NOT NULL")
            self.store.set_meta("refine_version", REFINE_VERSION)
        rows = self.store.q("SELECT id FROM episodes WHERE refined=0 AND clip_seconds IS NOT NULL"
                            " AND clip_path IS NOT NULL ORDER BY start_ts DESC LIMIT ?", (limit,))
        for r in rows:
            self.refine_q.put((1, r["id"]))       # background catch-up after fresh episodes
        return len(rows)

    def _refine_loop(self):
        from .refine import refine_episode
        clf = None
        while not self.stop.is_set():
            try:
                _, eid = self.refine_q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                if clf is None:   # its own model instance: TFLite interpreters aren't thread-safe
                    clf = make_classifier(self.cfg.detection.mode, self.cfg.detection.model_path)
                info = refine_episode(self.store, eid, clf, self.identifier, self.cfg.detection.bark_threshold)
                log.info("Refined episode %d: %s", eid, info)
            except Exception:  # noqa: BLE001
                log.exception("Refining episode %d failed", eid)
            self.stop.wait(0.2)   # stay gentle on the Pi while catching up

    # ---- "I hear barking" captures ------------------------------------------------------------
    def capture_missed(self, seconds: float = 30.0, note: str = "") -> int:
        """Save the last `seconds` of audio for review, then score every sound in it."""
        from . import tuning
        if self.detector is None:
            raise RuntimeError("Not listening, so there's no recent audio to save")
        cap = self.detector.capture(seconds)
        if not len(cap["audio"]):
            raise RuntimeError("No audio yet")
        from datetime import datetime
        stamp = datetime.fromtimestamp(cap["start_ts"]).strftime("%Y%m%d_%H%M%S")
        path = self.cfg.clips_path / "missed" / f"missed_{stamp}.wav"
        tuning.write_wav(path, cap["audio"])
        with self.store.tx() as c:
            mid = c.execute("INSERT INTO missed(created, start_ts, seconds, clip_path, trace, events, note)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (time.time(), cap["start_ts"], len(cap["audio"]) / 16000, str(path),
                             json.dumps(cap["trace"]), json.dumps(cap["events"], default=float), note)).lastrowid
        threading.Thread(target=self.analyze_missed, args=(mid,), name="analyze-missed", daemon=True).start()
        return mid

    def analyze_missed(self, mid: int) -> None:
        from . import tuning
        from .audio import read_wav
        from . import features as F
        r = self.store.q1("SELECT * FROM missed WHERE id=?", (mid,))
        if not r or not r["clip_path"] or not Path(r["clip_path"]).exists():
            return
        try:
            x, sr = read_wav(r["clip_path"])
            x = F.resample(x, sr)
            with self._tune_lock:          # its own model instance: TFLite isn't thread-safe
                if self._tune_clf is None:
                    self._tune_clf = make_classifier(self.cfg.detection.mode, self.cfg.detection.model_path)
                cands = tuning.analyze(x, json.loads(r["trace"] or "[]"), self._tune_clf, self.identifier,
                                       json.loads(r["events"] or "[]"))
            with self.store.tx() as c:
                c.execute("UPDATE missed SET analysis=? WHERE id=?", (json.dumps(cands), mid))
        except Exception:  # noqa: BLE001
            log.exception("Analysing capture %d failed", mid)

    def add_missed(self, mid: int, picks: list[int], dog_id: int | None) -> int:
        """Turn the sounds you marked in a capture into a logged, labelled episode."""
        from . import features as F
        from .audio import read_wav
        from .clips import clip_path_for
        r = self.store.q1("SELECT * FROM missed WHERE id=?", (mid,))
        if not r or not r["analysis"]:
            raise ValueError("That capture isn't ready yet")
        cands = json.loads(r["analysis"])
        # sounds the detector already logged live stay in their own episode
        picks = sorted({int(i) for i in picks if 0 <= int(i) < len(cands) and cands[int(i)].get("live") != "bark"})
        if not picks:
            raise ValueError("Select at least one bark")
        x, sr = read_wav(r["clip_path"])
        x = F.resample(x, sr)
        start = r["start_ts"]
        path = clip_path_for(self.cfg.clips_path, start)
        path.parent.mkdir(parents=True, exist_ok=True)
        Path(r["clip_path"]).replace(path)
        chosen = [cands[i] for i in picks]
        eid = self.store.create_episode(start + chosen[0]["t"], str(path), start)
        ids = []
        for c in chosen:
            on, end = int(c["t"] * F.SR), int(c["end"] * F.SR)
            seg = x[max(0, on - int(0.03 * F.SR)):max(end, on + int(0.2 * F.SR))]
            feats = F.bark_features(seg) if len(seg) >= F.N_FFT else None
            ids.append(self.store.add_bark(eid, start + c["t"], c["end"] - c["t"], c.get("prob") or 0.0,
                                           c.get("peak"), feats))
        if dog_id is not None:
            self.store.label_barks(ids, dog_id)
        self.store.finish_episode(eid, start + chosen[-1]["end"], len(chosen),
                                  max(c.get("peak") or -120 for c in chosen),
                                  max(c.get("prob") or 0 for c in chosen), len(x) / F.SR)
        # these are your marks: don't let the automatic re-scan replace them
        self.store.set_episode(eid, reviewed=1, refined=1,
                               note=(r["note"] + " " if r["note"] else "") + "Added from “I hear barking”.")
        with self.store.tx() as c:
            c.execute("UPDATE missed SET status='added', episode_id=?, marked=?, clip_path=? WHERE id=?",
                      (eid, json.dumps(picks), str(path), mid))
        self.retrain()                     # learn the labels; guess the dog for unlabelled ones
        return eid

    def dismiss_missed(self, mid: int) -> None:
        """No barking in it after all: delete the audio, keep the measurements (they help suggestions)."""
        r = self.store.q1("SELECT clip_path, status FROM missed WHERE id=?", (mid,))
        if not r:
            raise ValueError("No such capture")
        if r["status"] == "added":
            raise ValueError("Already added to the log")
        if r["clip_path"]:
            Path(r["clip_path"]).unlink(missing_ok=True)
        with self.store.tx() as c:
            c.execute("UPDATE missed SET status='dismissed', clip_path=NULL, marked='[]' WHERE id=?", (mid,))

    def _maintenance_loop(self):
        while not self.stop.is_set():
            try:
                from . import tuning
                tuning.prune(self.store, self.cfg.clips.retention_days)
                enforce_retention(self.store, self.cfg.clips_path, self.cfg.clips.retention_days,
                                  self.cfg.clips.max_disk_mb)
            except Exception:  # noqa: BLE001
                log.exception("Retention failed")
            if self._use_direction():           # directions arrive after the live guess: re-match hourly
                try:
                    self.retrain()
                except Exception:  # noqa: BLE001
                    log.exception("Re-matching failed")
            self.stop.wait(3600)

    def _backup_loop(self):
        self.stop.wait(60)                     # let startup (and a restore's re-scan) settle first
        while not self.stop.is_set():
            if self.backup.due():
                try:
                    self.backup.run("scheduled")
                except backup_mod.BackupError:
                    pass                           # recorded in the backup status for the dashboard
            self.stop.wait(300)

    def backup_now(self) -> None:
        def go():
            try:
                self.backup.run("manual")
            except backup_mod.BackupError:
                pass
        threading.Thread(target=go, name="backup-now", daemon=True).start()

    def restart(self) -> bool:
        """Restart the service so a staged restore takes effect. Under systemd (Restart=always) the
        process just exits and comes back in a few seconds; otherwise it asks you to restart."""
        self.restart_pending = True
        if not os.environ.get("INVOCATION_ID"):      # not started by systemd
            log.warning("Restore staged: restart Barktracker to finish")
            return False

        def go():
            time.sleep(1.0)                        # let the HTTP response go out
            self.shutdown()
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()
        return True

    def update_settings(self, changes: dict) -> list[str]:
        changed = settings_mod.update(self.cfg, self.store, changes, self.defaults)
        self._settings_side_effects(changed)
        return changed

    def reset_settings(self) -> None:
        settings_mod.reset(self.cfg, self.store, self.defaults)
        self._settings_side_effects(list(settings_mod.SPECS))

    def _settings_side_effects(self, changed: list[str]) -> None:
        if any(k.startswith("direction.") for k in changed):
            self.array.reconfigure()
            if "direction.enabled" in changed and hasattr(self.source, "prefer_native"):
                self.source.prefer_native = bool(self.cfg.direction.enabled)
                self.source.switch(self.source.device)      # reopen at the device's own rate
        if "audio.device" in changed and hasattr(self.source, "switch"):
            self.source.switch(self.cfg.audio.device)
        if "audio.gain_db" in changed and hasattr(self.source, "gain"):
            self.source.gain = 10 ** (self.cfg.audio.gain_db / 20)
        if any(k.startswith("identify.") for k in changed) and self.identifier:
            self.identifier.min_conf = self.cfg.identify.min_confidence
            self.identifier.min_labels = self.cfg.identify.min_labels_per_dog
            threading.Thread(target=self.retrain, daemon=True).start()
        if any(k in changed for k in ("clips.retention_days", "clips.max_disk_mb")):
            threading.Thread(target=lambda: enforce_retention(
                self.store, self.cfg.clips_path, self.cfg.clips.retention_days,
                self.cfg.clips.max_disk_mb), daemon=True).start()

    def retrain(self) -> dict:
        if not self.identifier:
            return {"trained": False, "reason": "identification disabled"}
        return retrain_and_repredict(self.store, self.identifier, self._use_direction())

    def _use_direction(self) -> bool:
        return bool(self.cfg.identify.use_direction and self.cfg.direction.enabled)

    def recover(self) -> None:
        """Fix up episodes left open by a crash/power cut (and their unfinalised WAV headers)."""
        rows = self.store.q("SELECT id, clip_path FROM episodes WHERE clip_seconds IS NULL")
        for r in rows:
            b = self.store.q1("SELECT COUNT(*) n, MAX(ts + duration) e, MAX(peak_db) p, MAX(prob) m"
                              " FROM barks WHERE episode_id=?", (r["id"],))
            if not b or not b["n"]:
                path = self.store.delete_episode(r["id"])
                if path:
                    Path(path).unlink(missing_ok=True)
                continue
            secs = repair_wav(r["clip_path"]) if r["clip_path"] else None
            self.store.finish_episode(r["id"], b["e"], b["n"], b["p"], b["m"], secs or 0.0)
            log.info("Recovered unfinished episode %d", r["id"])

    def notify(self, ep: dict) -> None:
        """Send the finished episode to the alert destinations whose conditions it meets."""
        from . import alerts
        from . import ordinance as O
        try:
            if not any(i.get("enabled", True) for i in alerts.load(self.store, self.cfg)):
                return
            ctx = O.episode_flags(self.store, self.cfg.timezone, ep["start_ts"], ep["end_ts"])
            alerts.dispatch(self.store, self.cfg, ep, ctx)
        except Exception:  # noqa: BLE001
            log.exception("Alerts failed")

    def shutdown(self):
        self.stop.set()
        self.array.stop()
        if self.detector:
            try:
                self.detector.flush()
            except Exception:  # noqa: BLE001
                pass


def repair_wav(path: str) -> float | None:
    """Rewrite RIFF sizes from the real file length (16-bit mono 16 kHz clips we wrote)."""
    import os
    import struct
    try:
        size = os.path.getsize(path)
        if size < 44:
            return None
        with open(path, "r+b") as f:
            f.seek(4)
            f.write(struct.pack("<I", size - 8))
            f.seek(40)
            f.write(struct.pack("<I", size - 44))
        return (size - 44) / 2 / 16000
    except OSError:
        return None
