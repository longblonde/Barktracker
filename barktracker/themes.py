"""Themes: every color and display behavior of the dashboard, live band included, in one small JSON file.

Built-in themes live here and can't be changed; "Duplicate" one to make your own. Your themes are
files in <data_dir>/themes/*.json, so they're backed up with everything else and can be shared:
download one from Settings > Theme, or upload a file someone sent you.

A theme can also carry its own live band pictures (napping, sitting, barking): they live in
<data_dir>/themes/<id>/quiet|active|bark/, and a theme with pictures downloads as a .zip holding
theme.json and those folders. Upload either a .json or such a .zip.

A theme file looks like:
  {"name": "Porch light", "mode": "auto",
   "colors": {"light": {"ink": "#1d2833", ...}, "dark": {"ink": "#e4e9ec", ...}},
   "alert_color": "#e8a800", "radius": 8,
   "behaviors": {"bark_words": true, "motion": "full", "tab_alert": true},
   "live_band": {"day_nap": true, "sound_notes": true, "sound_note_min": 0.8}}
Anything missing is taken from the default theme.
"""
from __future__ import annotations

import copy
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

META = "theme"

COLOR_KEYS = {
    "ink": "Text and lines",
    "surface": "Page background",
    "panel": "Panels and cards",
    "rule": "Borders",
    "muted": "Secondary text",
    "faint": "Tracks, empty cells",
    "accent": "Buttons and links",
    "ok-bark": "Counted barks (tuning)",
    "caution": "Close calls (tuning)",
    "danger": "Problems, “Not a bark”",
    "unknown": "Unidentified dogs",
    "focus": "Keyboard focus ring",
}
BEHAVIORS = {
    "bark_words": ("BARK words over the live band when a bark is detected", True),
    "tab_alert": ("Browser tab turns the alert color and says “Barking Detected”", True),
    "motion": ("Animations", "full"),           # full | reduced
}
# what the live band shows when (the server picks the picture, so these apply to every screen). What
# counts as calm depends on the microphone, so that level is a microphone setting (ui.day_nap_below_dbfs).
LIVE_BAND = {
    "day_nap": ("Show the napping dog in the daytime when it's calm", True),
    "sound_notes": ("Name other sounds the Pi recognizes, like “Hearing a car”", True),
    "sound_note_min": ("Only when the sound model is at least", 0.8),         # 0.3 to 0.99
}
MODES = ("auto", "light", "dark")

DEFAULT = {
    "id": "default", "name": "BarkTracker", "builtin": True,
    "description": "The standard look: light by day, dark when your device is.",
    "mode": "auto",
    "colors": {
        "light": {"ink": "#1d2833", "surface": "#f3f5f4", "panel": "#ffffff", "rule": "#d6dbdd",
                  "muted": "#66727c", "faint": "#e8ecec", "accent": "#1d2833", "ok-bark": "#2e9e5b",
                  "caution": "#e8a800", "danger": "#c2453d", "unknown": "#9aa5ad", "focus": "#2f6fd6"},
        "dark": {"ink": "#e4e9ec", "surface": "#151b20", "panel": "#1d252b", "rule": "#33404a",
                 "muted": "#93a0a9", "faint": "#26313a", "accent": "#e4e9ec", "ok-bark": "#4cc27a",
                 "caution": "#ffc21a", "danger": "#e0605a", "unknown": "#6f7c86", "focus": "#6f9cf0"},
    },
    "alert_color": "#e8a800",
    "radius": 8,
    "behaviors": {"bark_words": True, "tab_alert": True, "motion": "full"},
    "live_band": {k: v[1] for k, v in LIVE_BAND.items()},
}

BUILTIN = [
    DEFAULT,
    {"id": "night-watch", "name": "Night watch", "builtin": True,
     "description": "Always dark and dim, for a screen left on overnight. No splashes.",
     "mode": "dark",
     "colors": {"dark": {"ink": "#c9d1d6", "surface": "#0b0f12", "panel": "#12181d", "rule": "#25303a",
                         "muted": "#7c8a94", "faint": "#1a2229", "accent": "#c9d1d6", "ok-bark": "#3fae6c",
                         "caution": "#d9a520", "danger": "#d0574f", "unknown": "#5f6c76", "focus": "#5f8de0"}},
     "alert_color": "#d98f2b",
     "behaviors": {"bark_words": False, "tab_alert": True, "motion": "reduced"}},
    {"id": "high-contrast", "name": "High contrast", "builtin": True,
     "description": "Black on white with strong colors, for bright rooms and tired eyes.",
     "mode": "light",
     "colors": {"light": {"ink": "#000000", "surface": "#ffffff", "panel": "#ffffff", "rule": "#000000",
                          "muted": "#3a3a3a", "faint": "#e6e6e6", "accent": "#0040c0", "ok-bark": "#00702e",
                          "caution": "#a86b00", "danger": "#b00000", "unknown": "#5a5a5a", "focus": "#0040c0"}},
     "alert_color": "#d40000", "radius": 4,
     "behaviors": {"bark_words": True, "tab_alert": True, "motion": "full"}},
]
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def folder(cfg) -> Path:
    return cfg.data_path / "themes"


def complete(t: dict) -> dict:
    """Fill anything a theme leaves out from the default theme."""
    out = copy.deepcopy(DEFAULT)
    out.update({k: v for k, v in t.items() if k not in ("colors", "behaviors", "live_band")})
    for mode in ("light", "dark"):
        out["colors"][mode].update((t.get("colors") or {}).get(mode) or {})
    # a theme that only defines one scheme uses it for both
    only = [m for m in ("light", "dark") if (t.get("colors") or {}).get(m)]
    if len(only) == 1:
        other = "dark" if only[0] == "light" else "light"
        out["colors"][other] = {**out["colors"][other], **t["colors"][only[0]]}
    out["behaviors"].update(t.get("behaviors") or {})
    out["live_band"].update(t.get("live_band") or {})
    out["builtin"] = bool(t.get("builtin"))
    return out


def validate(t: dict) -> dict:
    if not isinstance(t, dict):
        raise ValueError("A theme is a JSON object")
    name = str(t.get("name") or "").strip()[:40]
    if not name:
        raise ValueError("Give the theme a name")
    out = {"name": name, "description": str(t.get("description") or "").strip()[:200]}
    mode = t.get("mode", "auto")
    if mode not in MODES:
        raise ValueError("mode is auto, light or dark")
    out["mode"] = mode
    out["colors"] = {}
    for scheme, vals in (t.get("colors") or {}).items():
        if scheme not in ("light", "dark") or not isinstance(vals, dict):
            raise ValueError("colors has a light and/or a dark set")
        clean = {}
        for k, v in vals.items():
            if k not in COLOR_KEYS:
                continue                     # ignore colors this version doesn't use
            if not isinstance(v, str) or not HEX.fullmatch(v):
                raise ValueError(f"{k} must look like #1d2833")
            clean[k] = v.lower()
        out["colors"][scheme] = clean
    ac = t.get("alert_color", DEFAULT["alert_color"])
    if not isinstance(ac, str) or not HEX.fullmatch(ac):
        raise ValueError("alert_color must look like #e8a800")
    out["alert_color"] = ac.lower()
    r = t.get("radius", DEFAULT["radius"])
    try:
        out["radius"] = max(0, min(20, int(r)))
    except (TypeError, ValueError):
        raise ValueError("radius is a number of pixels, 0 to 20") from None
    b = t.get("behaviors") or {}
    out["behaviors"] = {"bark_words": bool(b.get("bark_words", True)), "tab_alert": bool(b.get("tab_alert", True)),
                        "motion": "reduced" if b.get("motion") == "reduced" else "full"}
    lb, d = t.get("live_band") or {}, DEFAULT["live_band"]
    try:
        out["live_band"] = {
            "day_nap": bool(lb.get("day_nap", d["day_nap"])),
            "sound_notes": bool(lb.get("sound_notes", d["sound_notes"])),
            "sound_note_min": max(0.3, min(0.99, float(lb.get("sound_note_min", d["sound_note_min"])))),
        }
    except (TypeError, ValueError):
        raise ValueError("live_band sound_note_min must be a number") from None
    return out


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "theme"
    return s


def user_themes(cfg) -> list[dict]:
    out = []
    d = folder(cfg)
    if d.exists():
        for p in sorted(d.glob("*.json")):
            try:
                t = validate(json.loads(p.read_text()))
                t["id"] = p.stem
                out.append(t)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return out


def all_themes(cfg) -> list[dict]:
    out = [complete(t) for t in BUILTIN] + [complete(t) for t in user_themes(cfg)]
    for t in out:
        t["pictures"] = picture_count(cfg, t["id"])
    return out


def find(cfg, tid: str) -> dict | None:
    return next((t for t in all_themes(cfg) if t["id"] == tid), None)


def active(cfg, store) -> dict:
    tid = store.get_meta(META)
    if tid is None:
        tid = _migrate(cfg, store)
    return find(cfg, tid) or complete(DEFAULT)


def set_active(cfg, store, tid: str) -> dict:
    t = find(cfg, tid)
    if t is None:
        raise ValueError("No such theme")
    store.set_meta(META, tid)
    return t


def save(cfg, data: dict, tid: str | None = None) -> dict:
    """Create (tid None) or replace one of your themes. Built-in themes can't be overwritten."""
    t = validate(data)
    if tid and any(b["id"] == tid for b in BUILTIN):
        raise ValueError("Built-in themes can't be changed; duplicate it first")
    d = folder(cfg)
    d.mkdir(parents=True, exist_ok=True)
    if not tid:
        base, n = slug(t["name"]), 1
        tid = base
        taken = {b["id"] for b in BUILTIN} | {p.stem for p in d.glob("*.json")}
        while tid in taken:
            n += 1
            tid = f"{base}-{n}"
    elif not re.fullmatch(r"[a-z0-9-]{1,48}", tid):
        raise ValueError("Bad theme id")
    (d / f"{tid}.json").write_text(json.dumps(t, indent=2))
    return complete({**t, "id": tid})


def delete(cfg, store, tid: str) -> None:
    if any(b["id"] == tid for b in BUILTIN):
        raise ValueError("Built-in themes can't be deleted")
    p = folder(cfg) / f"{tid}.json"
    if not re.fullmatch(r"[a-z0-9-]{1,48}", tid) or not p.exists():
        raise ValueError("No such theme")
    p.unlink()
    clear_pictures(cfg, tid)
    if store.get_meta(META) == tid:
        store.set_meta(META, "default")


def export(t: dict) -> dict:
    return {k: t[k] for k in ("name", "description", "mode", "colors", "alert_color", "radius", "behaviors", "live_band")}


def apply_live_band(cfg, t: dict) -> None:
    """The live band reads these from cfg.ui; the active theme decides them."""
    for k, v in t["live_band"].items():
        setattr(cfg.ui, k, v)


def migrate_live_band(cfg, store) -> None:
    """Before v0.16 the live band settings were plain settings (config.yaml or the dashboard). Once,
    move any you changed into the theme: the active one if it's yours, else a copy named "My theme"."""
    if store.get_meta("live_band_in_theme"):
        return
    t = active(cfg, store)           # first: an install from before themes turns its old settings into one
    saved = store.get_meta("app_settings") or {}
    was = {k: saved.get(f"ui.{k}", getattr(cfg.ui, k)) for k in LIVE_BAND}
    if was != DEFAULT["live_band"]:
        data = {**export(t), "live_band": was}
        if t["builtin"]:
            data["name"] = "My theme"
            set_active(cfg, store, save(cfg, data)["id"])
        else:
            save(cfg, data, t["id"])
    gone = {f"ui.{k}" for k in LIVE_BAND} | {"ui.alert_color", "ui.bark_words"}     # now in the theme
    if gone & saved.keys():
        store.set_meta("app_settings", {k: v for k, v in saved.items() if k not in gone})
    store.set_meta("live_band_in_theme", True)


def css(t: dict) -> str:
    """The theme as CSS custom properties, loaded before the stylesheet's own defaults apply."""
    def block(vals: dict) -> str:
        v = [f"--{k}:{c}" for k, c in vals.items()]
        return ";".join(v + [f"--alert:{t['alert_color']}", f"--radius:{t['radius']}px",
                             f"--radius-sm:{max(0, t['radius'] // 2)}px"])
    light, dark = block(t["colors"]["light"]), block(t["colors"]["dark"])
    if t["mode"] == "light":
        return f":root{{{light};color-scheme:light}}"
    if t["mode"] == "dark":
        return f":root{{{dark};color-scheme:dark}}"
    return (f":root{{{light};color-scheme:light}}"
            f"@media (prefers-color-scheme: dark){{:root{{{dark};color-scheme:dark}}}}")


def _migrate(cfg, store) -> str:
    """Before themes, the alert color and BARK words were plain settings: keep what you chose."""
    saved = store.get_meta("app_settings") or {}
    color, words = saved.get("ui.alert_color"), saved.get("ui.bark_words")
    tid = "default"
    if (color and color != DEFAULT["alert_color"]) or words is False:
        t = export(complete(DEFAULT))
        t["name"] = "My theme"
        if color and HEX.fullmatch(str(color)):
            t["alert_color"] = color
        if words is False:
            t["behaviors"]["bark_words"] = False
        tid = save(cfg, t)["id"]
    store.set_meta(META, tid)
    return tid


# ---- live band pictures in a theme ---------------------------------------------------------------
KINDS = ("quiet", "active", "bark")
PICTURE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".webm"}
MAX_PICTURE = 40 * 2**20
MAX_PACK = 200 * 2**20


def media_dir(cfg, tid: str) -> Path:
    return folder(cfg) / tid


def pictures(cfg, tid: str) -> dict[str, list[Path]]:
    d = media_dir(cfg, tid)
    return {k: sorted(p for p in (d / k).glob("*") if p.is_file() and p.suffix.lower() in PICTURE_TYPES)
            if (d / k).is_dir() else [] for k in KINDS}


def picture_count(cfg, tid: str) -> int:
    if any(b["id"] == tid for b in BUILTIN):
        return 0
    return sum(len(v) for v in pictures(cfg, tid).values())


def copy_pictures(cfg, tid: str, sources: dict[str, list[Path]]) -> int:
    """Put pictures into one of your themes (replacing any it had)."""
    if any(b["id"] == tid for b in BUILTIN):
        raise ValueError("Built-in themes can't hold pictures; duplicate it first")
    clear_pictures(cfg, tid)
    n = 0
    for k, files in sources.items():
        if k not in KINDS:
            continue
        for p in files:
            (media_dir(cfg, tid) / k).mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, media_dir(cfg, tid) / k / p.name)
            n += 1
    return n


def clear_pictures(cfg, tid: str) -> None:
    d = media_dir(cfg, tid)
    if re.fullmatch(r"[a-z0-9-]{1,48}", tid) and d.is_dir():
        shutil.rmtree(d)


def export_zip(cfg, t: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("theme.json", json.dumps(export(t), indent=2))
        for k, files in pictures(cfg, t["id"]).items():
            for p in files:
                z.write(p, f"{k}/{p.name}", compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def import_pack(cfg, data: bytes) -> dict:
    """A theme file someone shared: plain JSON, or a .zip with theme.json and picture folders."""
    if data[:2] != b"PK":
        try:
            return save(cfg, json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("That file isn't a theme (.json or .zip)") from None
    if len(data) > MAX_PACK:
        raise ValueError("Theme packs must be under 200 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That .zip couldn't be opened") from None
    names = z.namelist()
    meta = next((n for n in names if n.rsplit("/", 1)[-1] == "theme.json"), None)
    if not meta:
        raise ValueError("The .zip has no theme.json")
    prefix = meta[: -len("theme.json")]               # packs zipped with or without a top folder
    try:
        t = save(cfg, json.loads(z.read(meta).decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("theme.json in the .zip isn't valid JSON") from None
    for info in z.infolist():
        if info.is_dir() or not info.filename.startswith(prefix):
            continue
        rel = info.filename[len(prefix):].split("/")
        if len(rel) != 2 or rel[0] not in KINDS:
            continue
        name = "".join(c for c in rel[1] if c.isalnum() or c in "._- ").strip()
        if not name or Path(name).suffix.lower() not in PICTURE_TYPES or info.file_size > MAX_PICTURE:
            continue                                  # only pictures, by name only (no paths out of the folder)
        (media_dir(cfg, t["id"]) / rel[0]).mkdir(parents=True, exist_ok=True)
        (media_dir(cfg, t["id"]) / rel[0] / name).write_bytes(z.read(info))
    t["pictures"] = picture_count(cfg, t["id"])
    return t
