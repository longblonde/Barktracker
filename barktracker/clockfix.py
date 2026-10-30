"""Clock jumps after a boot.

The Pi has no clock battery: after a boot its clock runs from wherever it was last saved until the
time server answers, which can take minutes, and anything logged meanwhile carries the wrong time.
When the clock then jumps by more than JUMP_FIX seconds, everything logged since the boot (or since
the last fix) is shifted by the jump and its clips renamed to match.

"Since the boot" goes by record ids noted at start-up, not by time: a slow clock after a reboot can
overlap the times of records from before it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .clips import clip_path_for

log = logging.getLogger(__name__)

JUMP_FIX = 30.0               # seconds: smaller jumps are left alone
META = "clock_anchor"         # {"boot": boot id, "since": {table: highest id before the boot}}

# table -> (id column, time columns)
TABLES = {
    "episodes": ("id", ("start_ts", "end_ts", "clip_start_ts")),
    "barks": ("id", ("ts",)),
    "bark_dirs": ("rowid", ("ts",)),
    "missed": ("id", ("created", "start_ts")),
}


def boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def _highest(store) -> dict[str, int]:
    out = {}
    for table, (key, _) in TABLES.items():
        try:
            out[table] = (store.q1(f"SELECT MAX({key}) m FROM {table}") or {}).get("m") or 0
        except Exception:  # noqa: BLE001 - table not created (feature never used)
            pass
    return out


def mark_boot(store) -> None:
    """At start-up: on a new boot, remember where its records begin."""
    a = store.get_meta(META) or {}
    bid = boot_id()
    if bid is None or a.get("boot") != bid:
        store.set_meta(META, {"boot": bid, "since": _highest(store)})


def correct(store, delta: float, clips_dir: Path) -> dict[str, int]:
    """Shift what was logged since the boot (or the last fix) by `delta` seconds and rename its
    clips. Returns rows changed per table."""
    since = (store.get_meta(META) or {}).get("since") or {}
    upto = _highest(store)
    counts: dict[str, int] = {}
    with store.tx() as c:
        for table, (key, cols) in TABLES.items():
            lo, hi = since.get(table, 0), upto.get(table, 0)
            if table not in upto or hi <= lo:
                continue
            sets = ", ".join(f"{col}={col}+?" for col in cols)
            cur = c.execute(f"UPDATE {table} SET {sets} WHERE {key} > ? AND {key} <= ?",
                            [delta] * len(cols) + [lo, hi])
            counts[table] = cur.rowcount
    renamed = 0
    if "episodes" in counts:
        for r in store.q("SELECT id, start_ts, clip_path FROM episodes WHERE id > ? AND id <= ?"
                         " AND clip_path IS NOT NULL", (since.get("episodes", 0), upto["episodes"])):
            renamed += _rename(store, "episodes", r["id"], Path(r["clip_path"]), clip_path_for(clips_dir, r["start_ts"]))
    if "missed" in counts:
        from datetime import datetime
        for r in store.q("SELECT id, start_ts, clip_path FROM missed WHERE id > ? AND id <= ?"
                         " AND clip_path IS NOT NULL", (since.get("missed", 0), upto["missed"])):
            old = Path(r["clip_path"])
            new = old.with_name(f"missed_{datetime.fromtimestamp(r['start_ts']):%Y%m%d_%H%M%S}.wav")
            renamed += _rename(store, "missed", r["id"], old, new)
    counts["clips renamed"] = renamed
    store.set_meta(META, {"boot": boot_id(), "since": upto})     # fixed up to here
    return counts


def _rename(store, table: str, row_id: int, old: Path, new: Path) -> int:
    """Move a clip to its corrected name (a clip still being written keeps writing: same file)."""
    if new == old or not old.exists() or new.exists():
        return 0
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except OSError as e:
        log.warning("Could not rename %s after the clock fix: %s", old, e)
        return 0
    with store.tx() as c:
        c.execute(f"UPDATE {table} SET clip_path=? WHERE id=?", (str(new), row_id))
    return 1
