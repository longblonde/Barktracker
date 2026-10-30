"""Advanced (debug) settings: the numbers that are normally fixed in the code.

Each one is a module-level constant that the code reads while it runs, so changing it here takes
effect straight away. Values you change are stored in the database (meta "advanced") and applied at
start-up; "Reset" puts every one back to the value in the code. These are for experimenting and
diagnosing: the defaults are what BarkTracker was tested with.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

META = "advanced"


@dataclass(frozen=True)
class Tunable:
    key: str            # "module.CONSTANT"
    group: str
    label: str
    unit: str
    lo: float
    hi: float
    help: str = ""
    kind: str = "float"   # float | int | pct (stored 0-1, shown as %)


TUNABLES = [
    Tunable("web.DOUBT_PROB", "Clip review", "“Unreviewed” below this bark score", "%", 0, 1,
            "Clips nobody has checked get the Unreviewed tag when their best bark score is under this.", "pct"),
    Tunable("web.DOUBT_MATCH", "Clip review", "“Unreviewed” below this dog-match confidence", "%", 0, 1,
            "…or when a dog was matched automatically with less confidence than this.", "pct"),
    Tunable("detector.NEAR_DB", "Detection", "Near misses shown on Tuning from", "dB over background", 2, 20,
            "Sounds this loud that didn't wake the bark check are still tagged on the Tuning page."),
    Tunable("tuning.CANDIDATE_RISE_DB", "Detection", "Sounds listed in an “I hear barking” capture from", "dB over background", 2, 20),
    Tunable("refine.MIN_RISE_DB", "Detection", "Second pass: a strong attack is at least", "dB over background", 4, 30,
            "Used when a finished clip is re-scanned to place every bark. Applies to clips re-scanned from now on."),
    Tunable("refine.MIN_DOGNESS", "Detection", "Second pass: dog-like enough at", "%", 0, 1,
            "Inside a confirmed clip, a strong sound this dog-like to the model counts as a bark.", "pct"),
    Tunable("live.NOISY_ABOVE_FLOOR_DB", "Live band", "“Noisy” from", "dB over background", 2, 30),
    Tunable("live.NOISY_ABSOLUTE_DBFS", "Live band", "…or louder than", "dBFS", -80, -10),
    Tunable("live.SETTLE_SECONDS", "Live band", "Wait before switching nap and sit", "s", 0, 60),
    Tunable("live.BARK_LINGER_SECONDS", "Live band", "Barking picture stays after the last bark", "s", 0, 30),
    Tunable("live.HEARD_SECONDS", "Live band", "“Hearing a car” note stays", "s", 1, 60),
    Tunable("web.LIVE_SLOW", "Live band", "Time between live updates while quiet", "s", 0.05, 2),
    Tunable("web.LIVE_FAST", "Live band", "Time between live updates while barking", "s", 0.03, 1),
    Tunable("array.SCAN_MIN_RISE", "Direction", "Bearing chart shows sounds from", "dB over background", 0, 30,
            "Lower shows quieter sounds, but electrical noise on some boards lights every wedge."),
    Tunable("array.SCAN_MIN_CLARITY", "Direction", "Bearing chart needs a clarity of", "", 1, 20),
    Tunable("array.VALID_CLARITY", "Direction", "A direction counts as clear from a clarity of", "", 1, 20,
            "Clear directions are kept per bark and used by the dashboard, the Dogs page and matching."),
    Tunable("array.SCAN_EVERY", "Direction", "Live scan every", "s", 0.05, 2),
    Tunable("identify.MIN_DIR_BARKS", "Direction", "Confirmed barks before a dog's direction counts", "barks", 1, 200, kind="int"),
    Tunable("identify.DIR_FLOOR", "Direction", "Weight left for a dog when a bark comes from elsewhere", "%", 0, 1,
            "Lower trusts direction more when matching.", "pct"),
    Tunable("identify.DIR_UNKNOWN", "Direction", "Weight for dogs whose direction isn't known", "%", 0, 1, kind="pct"),
    Tunable("clockfix.JUMP_FIX", "Clock", "Fix times when the clock jumps more than", "s", 5, 3600,
            "After a boot the clock can be minutes off until the time server answers; a bigger jump than "
            "this shifts everything logged since the boot (and renames its clips)."),
    Tunable("discover.LOOKBACK_DAYS", "Dogs you haven't named", "Look back", "days", 1, 365, kind="int"),
    Tunable("discover.MIN_GROUP", "Dogs you haven't named", "Smallest group", "barks", 3, 500, kind="int"),
    Tunable("discover.MIN_EPISODES", "Dogs you haven't named", "Heard in at least", "clips", 1, 100, kind="int"),
    Tunable("discover.SAMPLES", "Dogs you haven't named", "Examples to listen to", "barks", 1, 20, kind="int"),
    Tunable("discover.MAX_BARKS", "Dogs you haven't named", "Newest unidentified barks considered", "barks", 100, 20000,
            "More is slower on a Pi.", "int"),
]
BY_KEY = {t.key: t for t in TUNABLES}


def _module(key: str):
    mod, name = key.rsplit(".", 1)
    return importlib.import_module(f"barktracker.{mod}"), name


def _get(key: str):
    m, name = _module(key)
    return getattr(m, name)


def _set(key: str, value) -> None:
    m, name = _module(key)
    setattr(m, name, value)


_DEFAULTS: dict[str, float] = {}


def defaults() -> dict:
    if not _DEFAULTS:
        _DEFAULTS.update({t.key: _get(t.key) for t in TUNABLES})
    return dict(_DEFAULTS)


def coerce(key: str, value):
    t = BY_KEY.get(key)
    if t is None:
        raise ValueError(f"{key} isn't adjustable")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{t.label} needs a number") from None
    if not t.lo <= v <= t.hi:
        shown = (lambda x: f"{x * 100:g}%") if t.kind == "pct" else (lambda x: f"{x:g}")
        raise ValueError(f"{t.label} must be between {shown(t.lo)} and {shown(t.hi)}")
    return int(round(v)) if t.kind == "int" else v


def apply_saved(store) -> None:
    defaults()
    for k, v in (store.get_meta(META) or {}).items():
        try:
            _set(k, coerce(k, v))
        except ValueError:
            pass


def view(store) -> dict:
    d = defaults()
    saved = store.get_meta(META) or {}
    return {"items": [{"key": t.key, "group": t.group, "label": t.label, "unit": t.unit, "lo": t.lo, "hi": t.hi,
                       "help": t.help, "kind": t.kind, "value": _get(t.key), "default": d[t.key],
                       "changed": t.key in saved} for t in TUNABLES]}


def update(store, changes: dict) -> dict:
    clean = {k: coerce(k, v) for k, v in changes.items()}        # check everything before changing anything
    d = defaults()
    saved = store.get_meta(META) or {}
    for k, v in clean.items():
        _set(k, v)
        if v == d[k]:
            saved.pop(k, None)
        else:
            saved[k] = v
    store.set_meta(META, saved)
    return view(store)


def reset(store) -> dict:
    for k, v in defaults().items():
        _set(k, v)
    store.set_meta(META, {})
    return view(store)
