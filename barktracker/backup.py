"""Backups to a USB drive (or any folder on another disk), and restoring them onto a new install.

Layout on the drive:

    <backup.path>/barktracker-backup/
        manifest.json          what's here, from which install, when
        db/barktracker-YYYYmmdd-HHMMSS.db   consistent snapshots of the log database
                               (episodes, barks, bark features and labels = all training data,
                               dogs, settings, ordinance and contacts). The newest `keep` are kept.
        clips/YYYY-MM-DD/*.wav mirror of the recordings
        media/                 your live-band pictures
        config.yaml            copy of the config file

Safety rules:
  * Never write to a folder on the same disk as the data (an unmounted USB mount point is just an
    empty folder on the SD card) unless backup.allow_same_disk is set.
  * A drive holding another install's backup is never overwritten: restore it, or point this
    install at a different folder.
  * A recording is removed from the backup only when the log no longer references it (the clip
    retention rules deleted it). Recordings that vanished from the SD card but are still in the
    log stay on the drive.

Restoring is staged: the snapshot and recordings are copied into the data folder, then the app
restarts and swaps the restored database in before opening it (the current one is kept in
data/pre-restore-<time>/).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config

log = logging.getLogger(__name__)

DIRNAME = "barktracker-backup"
STATUS_KEY = "backup_status"
INSTALL_KEY = "install_id"
PENDING = "restore-pending.json"
RESTORE_DB = "barktracker.db.restore"
REQUIRED_TABLES = {"dogs", "episodes", "barks", "meta"}


class BackupError(Exception):
    pass


# ---- helpers ------------------------------------------------------------------------------
def install_id(store) -> str:
    v = store.get_meta(INSTALL_KEY)
    if not v:
        v = uuid.uuid4().hex
        store.set_meta(INSTALL_KEY, v)
    return v


def backup_root(path: str | os.PathLike) -> Path:
    """Accept either the drive/folder or the barktracker-backup folder itself."""
    p = Path(path).expanduser()
    return p if p.name == DIRNAME else p / DIRNAME


def _rel_clip(clip_path: str) -> str:
    """clips/<day>/<file>.wav -> '<day>/<file>.wav' (independent of where the data folder was)."""
    p = Path(clip_path)
    return f"{p.parent.name}/{p.name}"


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(src, tmp)
    os.replace(tmp, dst)


def _same(src: Path, dst: Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) < 2.0   # FAT keeps 2 s mtimes


def _check_db(path: Path) -> dict:
    """Open a snapshot read-only, check it, and count what's in it."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise BackupError(f"{path.name} can't be opened: {e}") from None
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES <= tables:
            raise BackupError(f"{path.name} is not a Barktracker database")
        ok = c.execute("PRAGMA quick_check").fetchone()[0]
        if ok != "ok":
            raise BackupError(f"{path.name} is damaged ({ok})")
        one = lambda sql: c.execute(sql).fetchone()[0]   # noqa: E731
        meta = {k: json.loads(v) for k, v in c.execute("SELECT key, value FROM meta")}
        return {
            "episodes": one("SELECT COUNT(*) FROM episodes"),
            "barks": one("SELECT COUNT(*) FROM barks"),
            "dogs": one("SELECT COUNT(*) FROM dogs"),
            "labelled_barks": one("SELECT COUNT(*) FROM barks WHERE label_dog_id IS NOT NULL"),
            "clips": one("SELECT COUNT(*) FROM episodes WHERE clip_path IS NOT NULL"),
            "first_ts": one("SELECT MIN(start_ts) FROM episodes"),
            "last_ts": one("SELECT MAX(end_ts) FROM episodes"),
            "install_id": meta.get(INSTALL_KEY),
        }
    except sqlite3.DatabaseError as e:
        raise BackupError(f"{path.name} is damaged: {e}") from None
    finally:
        c.close()


def drives(cfg: Config) -> list[dict]:
    """Mounted removable/external disks that could hold a backup (Pi OS: /media, /mnt, /run/media)."""
    out, seen = [], set()
    try:
        data_dev = os.stat(cfg.data_path).st_dev
    except OSError:
        data_dev = None
    labels = {}
    for d in (Path("/dev/disk/by-label"),):
        if d.exists():
            for p in d.iterdir():
                try:
                    labels[os.path.realpath(p)] = p.name.replace("\\x20", " ")
                except OSError:
                    pass
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mnt, fs = parts[0], parts[1].replace("\\040", " "), parts[2]
        opts = parts[3].split(",") if len(parts) > 3 else []
        if not dev.startswith("/dev/") or not mnt.startswith(("/media/", "/mnt/", "/run/media/")):
            continue
        if "ro" in opts or fs in ("squashfs", "iso9660", "udf"):      # read-only: can't hold a backup
            continue
        if mnt in seen:
            continue
        seen.add(mnt)
        try:
            du = shutil.disk_usage(mnt)
            same = data_dev is not None and os.stat(mnt).st_dev == data_dev
        except OSError:
            continue
        out.append({"path": mnt, "device": dev, "fs": fs, "label": labels.get(os.path.realpath(dev)),
                    "total_mb": du.total // 2**20, "free_mb": du.free // 2**20, "same_disk": same,
                    "has_backup": (Path(mnt) / DIRNAME / "manifest.json").exists()})
    return out


def _in_fstab(path: Path) -> bool:
    try:
        for line in Path("/etc/fstab").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and not parts[0].startswith("#") and Path(parts[1]) == path:
                return True
    except OSError:
        pass
    return False


def try_mount(path: Path) -> bool:
    """A drive plugged in after boot: mount it if /etc/fstab allows users to (setup-usb-backup.sh does)."""
    import subprocess
    if not _in_fstab(path):
        return False
    try:
        return subprocess.run(["mount", str(path)], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def eject(path: str) -> dict:
    """Unmount the backup drive so it can be unplugged safely."""
    import subprocess
    p = Path(path).expanduser()
    if not os.path.ismount(p):
        return {"ok": True, "reason": "The drive isn't mounted; it's safe to unplug."}
    os.sync()
    try:
        r = subprocess.run(["umount", str(p)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "reason": str(e)}
    if r.returncode:
        return {"ok": False, "reason": (r.stderr or r.stdout).strip() or "umount failed"}
    return {"ok": True, "reason": "Safe to unplug. Plug it back in and the next backup mounts it again."}


def check_target(cfg: Config, path: str | None, mount: bool = False) -> dict:
    """Is this folder usable for backups? Never raises; returns ok + a plain-language reason."""
    if not path:
        return {"ok": False, "reason": "No backup folder set."}
    p = Path(path).expanduser()
    try:
        exists = p.is_dir()
        if exists:
            os.listdir(p)                 # wakes an x-systemd.automount drive
    except OSError as e:
        return {"ok": False, "reason": f"Can't open {p}: {e.strerror or e}"}
    if not exists:
        return {"ok": False, "reason": f"{p} doesn't exist. Is the USB drive plugged in and mounted?"}
    try:
        same = os.stat(p).st_dev == os.stat(cfg.data_path).st_dev
    except OSError:
        same = False
    if same and not cfg.backup.allow_same_disk and mount and try_mount(p):
        same = os.stat(p).st_dev == os.stat(cfg.data_path).st_dev
    if same and not cfg.backup.allow_same_disk:
        if _in_fstab(p):
            return {"ok": False, "same_disk": True, "unmounted": True,
                    "reason": "The backup drive isn't mounted. Plug it in; it's mounted again at the next backup "
                              "(or press Check or Back up now)."}
        return {"ok": False, "same_disk": True,
                "reason": f"{p} is on the same disk as the Pi's data (the SD card). That usually means the USB "
                          "drive isn't mounted there. Plug it in and mount it, or pick its folder from the list."}
    if not os.access(p, os.W_OK):
        return {"ok": False, "reason": f"Barktracker can't write to {p}. See the README for mounting the drive "
                                       "so the barktracker user owns it."}
    du = shutil.disk_usage(p)
    return {"ok": True, "same_disk": same, "free_mb": du.free // 2**20, "total_mb": du.total // 2**20}


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None


def _tree_files(root: Path, pattern: str = "*") -> dict[str, Path]:
    if not root.exists():
        return {}
    return {str(f.relative_to(root)): f for f in root.rglob(pattern)
            if f.is_file() and not f.name.endswith(".part")}


# ---- backing up ---------------------------------------------------------------------------
class Backup:
    def __init__(self, cfg: Config, store, config_file: str | None = None):
        self.cfg, self.store, self.config_file = cfg, store, config_file
        self.lock = threading.Lock()
        self.progress: dict | None = None
        self.restore_progress: dict | None = None

    # status ----------------------------------------------------------------------------
    def status(self) -> dict:
        b = self.cfg.backup
        st = self.store.get_meta(STATUS_KEY) or {}
        target = check_target(self.cfg, b.path) if b.path else {"ok": False, "reason": "No backup folder set."}
        snaps = []
        if b.path and target.get("ok"):
            root = backup_root(b.path)
            snaps = [{"name": p.name, "mb": round(p.stat().st_size / 2**20, 1), "ts": p.stat().st_mtime}
                     for p in sorted((root / "db").glob("barktracker-*.db"), reverse=True)]
        due = None
        if b.enabled and b.path:
            due = (st.get("last_ok") or 0) + b.interval_hours * 3600
        return {"enabled": b.enabled, "path": b.path, "target": target, "last": st, "snapshots": snaps,
                "running": self.progress, "next_due": due, "restore": self.restore_progress,
                "pending_restore": _read_json(self.cfg.data_path / PENDING),
                "last_restore": self.store.get_meta("last_restore"),
                "install_id": install_id(self.store)}

    def due(self) -> bool:
        b = self.cfg.backup
        if not (b.enabled and b.path) or self.lock.locked():
            return False
        st = self.store.get_meta(STATUS_KEY) or {}
        now = time.time()
        if now - (st.get("last_ok") or 0) < b.interval_hours * 3600:
            return False
        # after a failure (drive unplugged, full...) try again hourly rather than every few minutes
        return not (st.get("last_error") and now - (st.get("last_try") or 0) < min(3600, b.interval_hours * 3600))

    def _save_status(self, **kw) -> None:
        st = self.store.get_meta(STATUS_KEY) or {}
        st.update(kw)
        self.store.set_meta(STATUS_KEY, st)

    # run ---------------------------------------------------------------------------------
    def run(self, reason: str = "scheduled") -> dict:
        if not self.lock.acquire(blocking=False):
            raise BackupError("A backup is already running")
        t0 = time.time()
        try:
            self.progress = {"step": "checking the drive", "started": t0}
            out = self._run()
            out["seconds"] = round(time.time() - t0, 1)
            self._save_status(last_ok=time.time(), last_try=time.time(), last_error=None, last_reason=reason,
                              last_result=out)
            log.info("Backup finished: %s", out)
            return out
        except BackupError as e:
            self._save_status(last_try=time.time(), last_error=str(e), last_reason=reason)
            log.warning("Backup failed: %s", e)
            raise
        except Exception as e:  # noqa: BLE001
            self._save_status(last_try=time.time(), last_error=f"{type(e).__name__}: {e}", last_reason=reason)
            log.exception("Backup failed")
            raise BackupError(f"{type(e).__name__}: {e}") from e
        finally:
            self.progress = None
            self.lock.release()

    def _run(self) -> dict:
        cfg, b = self.cfg, self.cfg.backup
        chk = check_target(cfg, b.path, mount=True)
        if not chk["ok"]:
            raise BackupError(chk["reason"])
        root = backup_root(b.path)
        me = install_id(self.store)
        man_path = root / "manifest.json"
        if man_path.exists():
            try:
                other = json.loads(man_path.read_text()).get("install_id")
            except (OSError, ValueError):
                other = None
            if other and other != me:
                raise BackupError(
                    f"{root} holds a backup from another Barktracker install ({json.loads(man_path.read_text()).get('hostname', 'another Pi')}). "
                    "To bring that data here, use Restore. To keep both, choose a different backup folder.")
        root.mkdir(parents=True, exist_ok=True)

        # 1. database snapshot (SQLite online backup: consistent while the detector keeps writing)
        self.progress["step"] = "saving the database"
        dbdir = root / "db"
        dbdir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = dbdir / f"barktracker-{stamp}.db"
        tmp = dbdir / f".barktracker-{stamp}.db.part"
        src = sqlite3.connect(str(cfg.db_path), timeout=30)
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst, pages=1024, sleep=0.01)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
            src.close()
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp, final)
        _fsync_dir(dbdir)
        counts = _check_db(final)
        snaps = sorted(dbdir.glob("barktracker-*.db"), reverse=True)
        for old in snaps[max(1, b.keep):]:
            old.unlink(missing_ok=True)
        for junk in dbdir.glob(".*.part"):
            if junk != tmp:
                junk.unlink(missing_ok=True)

        # 2. recordings
        copied = removed = skipped = 0
        copied_mb = 0.0
        if b.include_clips:
            self.progress["step"] = "copying recordings"
            local = _tree_files(cfg.clips_path, "*.wav")
            remote = _tree_files(root / "clips", "*.wav")
            referenced = {_rel_clip(r["clip_path"]) for r in
                          self.store.q("SELECT clip_path FROM episodes WHERE clip_path IS NOT NULL")}
            todo = [(k, f) for k, f in local.items() if not _same(f, root / "clips" / k)]
            need = sum(f.stat().st_size for _, f in todo)
            free = shutil.disk_usage(root).free
            if need > free - 50 * 2**20:
                raise BackupError(f"Not enough room on the drive: recordings need {need / 2**30:.1f} GB more, "
                                  f"{free / 2**30:.1f} GB free.")
            self.progress.update(total=len(todo), done=0)
            for i, (k, f) in enumerate(todo, 1):
                try:
                    _copy_file(f, root / "clips" / k)
                    copied += 1
                    copied_mb += f.stat().st_size / 2**20
                except FileNotFoundError:
                    skipped += 1          # deleted by retention meanwhile
                self.progress["done"] = i
            for k, f in remote.items():
                if k not in local and k not in referenced:   # retention removed it from the log
                    f.unlink(missing_ok=True)
                    removed += 1
            for d in sorted((root / "clips").glob("*"), reverse=True) if (root / "clips").exists() else []:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()

        # 3. pictures and config
        self.progress["step"] = "copying pictures and settings"
        if cfg.media_path.exists():
            local_m = _tree_files(cfg.media_path)
            for k, f in local_m.items():
                if not _same(f, root / "media" / k):
                    _copy_file(f, root / "media" / k)
            for k, f in _tree_files(root / "media").items():
                if k not in local_m:
                    f.unlink(missing_ok=True)
        if self.config_file and Path(self.config_file).is_file():
            _copy_file(Path(self.config_file), root / "config.yaml")

        clip_files = _tree_files(root / "clips", "*.wav")
        manifest = {
            "app": "barktracker", "format": 1, "version": __version__, "install_id": me,
            "hostname": socket.gethostname(), "created": time.time(),
            "data_dir": str(cfg.data_path), "latest_db": final.name,
            "database": counts,
            "clip_files": len(clip_files),
            "clips_mb": round(sum(f.stat().st_size for f in clip_files.values()) / 2**20, 1),
        }
        tmpm = man_path.with_name("manifest.json.part")
        tmpm.write_text(json.dumps(manifest, indent=2))
        os.replace(tmpm, man_path)
        _fsync_dir(root)
        return {"snapshot": final.name, "copied": copied, "copied_mb": round(copied_mb, 1), "removed": removed,
                "skipped": skipped, "clip_files": manifest["clip_files"], "clips_mb": manifest["clips_mb"],
                "episodes": counts["episodes"], "labelled_barks": counts["labelled_barks"]}

    # ---- restoring -----------------------------------------------------------------------
    def stage_restore(self, src: str, snapshot: str | None = None, include_clips: bool = True) -> dict:
        """Copy a backup into this install's data folder; it takes effect at the next start."""
        if self.lock.locked():
            raise BackupError("A backup is running; try again when it finishes")
        info = inspect(src)
        root = Path(info["root"])
        name = snapshot or info["snapshots"][0]["name"]
        if name not in {s["name"] for s in info["snapshots"]}:
            raise BackupError(f"No snapshot called {name}")
        snap = root / "db" / name
        with self.lock:
            self.restore_progress = {"step": "checking the snapshot", "done": 0, "total": 0}
            try:
                counts = _check_db(snap)
                data = self.cfg.data_path
                data.mkdir(parents=True, exist_ok=True)
                copied = 0
                if include_clips and (root / "clips").exists():
                    files = _tree_files(root / "clips", "*.wav")
                    todo = [(k, f) for k, f in files.items() if not _same(f, self.cfg.clips_path / k)]
                    need = sum(f.stat().st_size for _, f in todo)
                    if need > shutil.disk_usage(data).free - 200 * 2**20:
                        raise BackupError(f"Not enough room on this Pi for the recordings ({need / 2**30:.1f} GB). "
                                          "Free some space or restore without recordings.")
                    self.restore_progress.update(step="copying recordings", total=len(todo))
                    for i, (k, f) in enumerate(todo, 1):
                        _copy_file(f, self.cfg.clips_path / k)
                        copied += 1
                        self.restore_progress["done"] = i
                if (root / "media").exists():
                    self.restore_progress["step"] = "copying pictures"
                    for k, f in _tree_files(root / "media").items():
                        if not _same(f, self.cfg.media_path / k):
                            _copy_file(f, self.cfg.media_path / k)
                self.restore_progress["step"] = "staging the database"
                _copy_file(snap, data / RESTORE_DB)
                (data / PENDING).write_text(json.dumps({
                    "from": str(root), "snapshot": name, "staged": time.time(), "counts": counts,
                    "hostname": info.get("hostname")}))
                self.restore_progress["step"] = "ready: restarting"
                return {"staged": True, "snapshot": name, "clips_copied": copied, **counts}
            except Exception:
                self.restore_progress = None
                raise

    def stage_upload(self, db_bytes: bytes) -> dict:
        """Restore just a database file (log, labels/training data, settings; no recordings)."""
        data = self.cfg.data_path
        tmp = data / (RESTORE_DB + ".upload")
        tmp.write_bytes(db_bytes)
        try:
            counts = _check_db(tmp)
        except BackupError:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, data / RESTORE_DB)
        (data / PENDING).write_text(json.dumps({"from": "upload", "snapshot": "uploaded file",
                                                "staged": time.time(), "counts": counts}))
        return {"staged": True, **counts}


def inspect(src: str) -> dict:
    """What a backup folder holds (for choosing what to restore)."""
    if not src:
        raise BackupError("Choose the backup folder")
    root = backup_root(src)
    if not root.is_dir():
        raise BackupError(f"No Barktracker backup in {src} (looked for {root})")
    man = {}
    if (root / "manifest.json").exists():
        try:
            man = json.loads((root / "manifest.json").read_text())
        except (OSError, ValueError):
            man = {}
    snaps = []
    for p in sorted((root / "db").glob("barktracker-*.db"), reverse=True):
        try:
            snaps.append({"name": p.name, "mb": round(p.stat().st_size / 2**20, 1), "ts": p.stat().st_mtime,
                          **_check_db(p)})
        except BackupError as e:
            snaps.append({"name": p.name, "error": str(e), "ts": p.stat().st_mtime})
    good = [s for s in snaps if "error" not in s]
    if not good:
        raise BackupError(f"{root} has no usable database snapshot")
    snaps = good + [s for s in snaps if "error" in s]
    clips = _tree_files(root / "clips", "*.wav")
    return {"root": str(root), "hostname": man.get("hostname"), "created": man.get("created"),
            "version": man.get("version"), "install_id": man.get("install_id"), "snapshots": snaps,
            "clip_files": len(clips), "clips_mb": round(sum(f.stat().st_size for f in clips.values()) / 2**20, 1)}


def apply_pending_restore(cfg: Config) -> dict | None:
    """At startup, before the database is opened: swap in a staged restore."""
    data = cfg.data_path
    pend, staged = data / PENDING, data / RESTORE_DB
    if not pend.exists() or not staged.exists():
        pend.unlink(missing_ok=True)
        return None
    try:
        info = json.loads(pend.read_text())
        _check_db(staged)
    except (OSError, ValueError, BackupError) as e:
        log.error("Staged restore is unusable, ignoring it: %s", e)
        staged.unlink(missing_ok=True)
        pend.unlink(missing_ok=True)
        return None
    keep = data / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    db = cfg.db_path
    moved = False
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            keep.mkdir(exist_ok=True)
            shutil.move(str(p), keep / p.name)
            moved = True
    os.replace(staged, db)
    # point recordings at this install's clips folder
    c = sqlite3.connect(str(db))
    try:
        rows = c.execute("SELECT id, clip_path FROM episodes WHERE clip_path IS NOT NULL").fetchall()
        for eid, path in rows:
            new = cfg.clips_path / _rel_clip(path)
            if str(new) != path:
                c.execute("UPDATE episodes SET clip_path=? WHERE id=?", (str(new), eid))
        has_missed = c.execute("SELECT 1 FROM sqlite_master WHERE name='missed'").fetchone()
        if has_missed:                                   # "I hear barking" captures under review
            for mid, path in c.execute("SELECT id, clip_path FROM missed WHERE clip_path IS NOT NULL").fetchall():
                p = Path(path)
                new = cfg.clips_path / ("missed" if p.parent.name == "missed" else p.parent.name) / p.name
                c.execute("UPDATE missed SET clip_path=? WHERE id=?", (str(new), mid))
        c.execute("DELETE FROM meta WHERE key=?", (STATUS_KEY,))
        c.commit()
    finally:
        c.close()
    pend.unlink(missing_ok=True)
    info.update(applied=time.time(), previous=str(keep) if moved else None)
    log.warning("Restored database from %s (%s); previous database kept in %s",
                info.get("from"), info.get("snapshot"), keep if moved else "nowhere (there was none)")
    return info
