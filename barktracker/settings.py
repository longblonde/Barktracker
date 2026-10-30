"""Settings that can be changed from the dashboard while running.

They are stored in the database as overrides on top of config.yaml, applied to the live
config objects (the detector and retention job read them on every use), and survive restarts.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config

META_KEY = "app_settings"


@dataclass(frozen=True)
class Spec:
    key: str            # "section.field" in Config
    kind: str           # float | int | bool | str
    lo: float | None = None
    hi: float | None = None


SPECS = {s.key: s for s in [
    Spec("detection.bark_threshold", "float", 0.05, 0.95),
    Spec("detection.trigger_db", "float", 3, 30),
    Spec("detection.min_level_dbfs", "float", -90, -20),
    Spec("detection.episode_gap", "float", 1, 120),
    Spec("detection.min_episode_barks", "int", 1, 50),
    Spec("detection.suppress_learned_false_positives", "bool"),
    Spec("audio.gain_db", "float", -12, 30),
    Spec("clips.preroll", "float", 0, 10),
    Spec("clips.postroll", "float", 0, 30),
    Spec("clips.max_clip_seconds", "float", 10, 3600),
    Spec("clips.retention_days", "int", 0, 3650),
    Spec("clips.max_disk_mb", "int", 0, 2_000_000),
    Spec("identify.min_confidence", "float", 0.3, 0.95),
    Spec("identify.min_labels_per_dog", "int", 1, 100),
    Spec("identify.use_direction", "bool"),
    Spec("ui.day_nap", "bool"),
    Spec("ui.day_nap_below_dbfs", "float", -90, -10),
    Spec("ui.sound_notes", "bool"),
    Spec("ui.sound_note_min", "float", 0.3, 0.99),
    Spec("audio.device", "device"),
    Spec("backup.path", "path"),
    Spec("backup.enabled", "bool"),
    Spec("backup.interval_hours", "float", 1, 720),
    Spec("backup.keep", "int", 1, 90),
    Spec("backup.include_clips", "bool"),
    Spec("direction.enabled", "bool"),
    Spec("direction.mode", "str"),
    Spec("direction.device", "str"),
    Spec("direction.spacing_cm", "float", 0.5, 2000),
    Spec("direction.offset_ms", "float", -1000, 1000),
    Spec("direction.swap", "bool"),
]}

# saved settings from before v0.15 (the direction test lived under "dev")
LEGACY_KEYS = {"dev.array": "direction.enabled", "dev.array_mode": "direction.mode",
               "dev.array_device": "direction.device", "dev.array_spacing_cm": "direction.spacing_cm",
               "dev.array_offset_ms": "direction.offset_ms", "dev.array_swap": "direction.swap"}


def _get(cfg: Config, key: str):
    sec, field = key.split(".")
    return getattr(getattr(cfg, sec), field)


def _set(cfg: Config, key: str, value) -> None:
    sec, field = key.split(".")
    setattr(getattr(cfg, sec), field, value)


def coerce(key: str, value):
    s = SPECS.get(key)
    if s is None:
        raise ValueError(f"{key} can't be changed here")
    if s.kind == "bool":
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if s.kind == "color":
        v = str(value or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("Colors look like #d4252a")
        return v.lower()
    if s.kind == "device":
        v = str(value or "").strip()
        return v or "auto"
    if s.kind == "path":
        v = str(value or "").strip()
        if v and not v.startswith(("/", "~")):
            raise ValueError("The backup folder must be a full path, like /mnt/barktracker-backup")
        return v.rstrip("/") or None
    if s.kind == "str":
        v = (value or "").strip()
        if key == "direction.mode":
            if v not in ("stereo", "pair"):
                raise ValueError("Direction mode is stereo or pair")
            return v
        return v or None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} needs a number") from None
    if s.lo is not None and v < s.lo or s.hi is not None and v > s.hi:
        raise ValueError(f"{key} must be between {s.lo:g} and {s.hi:g}")
    return int(round(v)) if s.kind == "int" else v


def current(cfg: Config) -> dict:
    return {k: _get(cfg, k) for k in SPECS}


def apply_saved(cfg: Config, store) -> dict:
    """At startup: remember config.yaml values as defaults, then apply saved overrides."""
    defaults = current(cfg)
    saved = store.get_meta(META_KEY) or {}
    if any(k in LEGACY_KEYS for k in saved):
        for old, new in LEGACY_KEYS.items():
            if old in saved:
                saved.setdefault(new, saved.pop(old))
        store.set_meta(META_KEY, saved)
    for k, v in saved.items():
        if k in SPECS:
            try:
                _set(cfg, k, coerce(k, v))
            except ValueError:
                pass
    return defaults


def update(cfg: Config, store, changes: dict, defaults: dict) -> list[str]:
    """Validate everything first, then apply. Returns the keys that changed."""
    clean = {k: coerce(k, v) for k, v in changes.items()}
    saved = store.get_meta(META_KEY) or {}
    changed = []
    for k, v in clean.items():
        if _get(cfg, k) != v:
            changed.append(k)
        _set(cfg, k, v)
        if v == defaults.get(k):
            saved.pop(k, None)          # back to the config.yaml value: no override needed
        else:
            saved[k] = v
    store.set_meta(META_KEY, saved)
    return changed


def reset(cfg: Config, store, defaults: dict) -> None:
    for k, v in defaults.items():
        _set(cfg, k, v)
    store.set_meta(META_KEY, {})


def disk_usage(cfg: Config, store) -> dict:
    data = cfg.data_path
    du = shutil.disk_usage(data)
    clips = list(cfg.clips_path.rglob("*.wav")) if cfg.clips_path.exists() else []
    clip_bytes = sum(p.stat().st_size for p in clips)
    db_bytes = sum(os.path.getsize(p) for p in data.glob(cfg.db_path.name + "*") if p.is_file())
    since = time.time() - 14 * 86400
    recent = store.q("SELECT clip_path, start_ts FROM episodes WHERE start_ts >= ? AND clip_path IS NOT NULL",
                     (since,))
    recent_bytes = sum(os.path.getsize(r["clip_path"]) for r in recent if r["clip_path"] and Path(r["clip_path"]).exists())
    first = store.q1("SELECT MIN(start_ts) t FROM episodes")["t"]
    span_days = max(1.0, min(14.0, (time.time() - first) / 86400)) if first else 14.0
    per_day = recent_bytes / span_days
    oldest = store.q1("SELECT MIN(start_ts) t FROM episodes WHERE clip_path IS NOT NULL")["t"]
    cap = cfg.clips.max_disk_mb * 2**20 if cfg.clips.max_disk_mb else None
    room = min(cap - clip_bytes if cap else float("inf"), du.free)
    return {
        "clips_mb": round(clip_bytes / 2**20, 1), "clip_count": len(clips),
        "db_mb": round(db_bytes / 2**20, 1),
        "free_mb": du.free // 2**20, "total_mb": du.total // 2**20, "used_mb": du.used // 2**20,
        "cap_mb": cfg.clips.max_disk_mb, "retention_days": cfg.clips.retention_days,
        "per_day_mb": round(per_day / 2**20, 2),
        "days_until_cap": round(room / per_day, 1) if per_day > 0 and room != float("inf") else None,
        "audio_days_capacity": round((cap or du.free + clip_bytes) / per_day, 1) if per_day > 0 else None,
        "oldest_clip_ts": oldest,
        "path": str(data),
    }


def build_info() -> dict:
    """App version plus the git commit it's running, so you can tell whether a `git pull` took."""
    import subprocess
    from datetime import datetime, timezone
    from . import __version__
    repo = Path(__file__).resolve().parent.parent
    info = {"version": __version__, "commit": None, "commit_date": None}
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%h %ct"],
                             capture_output=True, text=True, timeout=3).stdout.split()
        if len(out) == 2:
            info["commit"] = out[0]
            info["commit_date"] = datetime.fromtimestamp(int(out[1]), timezone.utc).date().isoformat()
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return info
