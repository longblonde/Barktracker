"""Themes: every color and display behavior of the dashboard, in one small JSON file.

Built-in themes live here and can't be changed; "Duplicate" one to make your own. Your themes are
files in <data_dir>/themes/*.json, so they're backed up with everything else and can be shared:
download one from Settings > Theme, or upload a file someone sent you.

A theme file looks like:
  {"name": "Porch light", "mode": "auto",
   "colors": {"light": {"ink": "#1d2833", ...}, "dark": {"ink": "#e4e9ec", ...}},
   "alert_color": "#e8a800", "radius": 8,
   "behaviors": {"bark_words": true, "motion": "full", "tab_alert": true}}
Anything missing is taken from the default theme.
"""
from __future__ import annotations

import copy
import json
import re
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
    out.update({k: v for k, v in t.items() if k not in ("colors", "behaviors")})
    for mode in ("light", "dark"):
        out["colors"][mode].update((t.get("colors") or {}).get(mode) or {})
    # a theme that only defines one scheme uses it for both
    only = [m for m in ("light", "dark") if (t.get("colors") or {}).get(m)]
    if len(only) == 1:
        other = "dark" if only[0] == "light" else "light"
        out["colors"][other] = {**out["colors"][other], **t["colors"][only[0]]}
    out["behaviors"].update(t.get("behaviors") or {})
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
    return [complete(t) for t in BUILTIN] + [complete(t) for t in user_themes(cfg)]


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
    if store.get_meta(META) == tid:
        store.set_meta(META, "default")


def export(t: dict) -> dict:
    return {k: t[k] for k in ("name", "description", "mode", "colors", "alert_color", "radius", "behaviors")}


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
