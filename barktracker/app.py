"""Wires the detector, storage, identifier, maintenance and notifications together."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.request
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
        self.backup = backup_mod.Backup(cfg, self.store, getattr(cfg, "source_file", None))
        self.restart_pending = False
        self.recover()
        ic = cfg.identify
        self.identifier = DogIdentifier(ic.k, ic.min_labels_per_dog, ic.min_confidence) if ic.enabled else None
        if self.identifier:
            retrain_and_repredict(self.store, self.identifier)
        self.source = source
        if source is not None and hasattr(source, "switch"):      # mic choice / boost saved from the dashboard
            source.device = cfg.audio.device
            source.gain = 10 ** (cfg.audio.gain_db / 20)
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

    def _maintenance_loop(self):
        while not self.stop.is_set():
            try:
                enforce_retention(self.store, self.cfg.clips_path, self.cfg.clips.retention_days,
                                  self.cfg.clips.max_disk_mb)
            except Exception:  # noqa: BLE001
                log.exception("Retention failed")
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
        return retrain_and_repredict(self.store, self.identifier)

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
        n = self.cfg.notify
        if not n.webhook_url or ep["duration"] < n.min_duration:
            return
        names = {str(d["id"]): d["name"] for d in self.store.dogs()}
        dogs = {names.get(k, "unknown"): v for k, v in ep["dogs"].items() if k != "0"}
        payload = {**ep, "dogs": dogs,
                   "message": f"Barking for {ep['duration']:.0f}s ({ep['barks']} barks)"
                              + (f": {', '.join(dogs)}" if dogs else "")}

        def send():
            try:
                req = urllib.request.Request(n.webhook_url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # noqa: BLE001
                log.warning("Webhook failed: %s", e)
        threading.Thread(target=send, daemon=True).start()

    def shutdown(self):
        self.stop.set()
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
