"""SQLite persistence. One connection per thread; WAL so the detector and web server don't block."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

NOT_BARK = 0  # sentinel dog id meaning "this was not a bark"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#4f8cff',
    notes TEXT DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    bark_count INTEGER NOT NULL DEFAULT 0,
    peak_db REAL,
    max_prob REAL,
    clip_path TEXT,
    clip_start_ts REAL,
    clip_seconds REAL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    false_positive INTEGER NOT NULL DEFAULT 0,
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_episodes_start ON episodes(start_ts);
CREATE TABLE IF NOT EXISTS barks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    duration REAL NOT NULL,
    prob REAL NOT NULL,
    peak_db REAL,
    features BLOB,
    pred_dog_id INTEGER,
    pred_conf REAL,
    label_dog_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_barks_episode ON barks(episode_id);
CREATE INDEX IF NOT EXISTS ix_barks_ts ON barks(ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

PALETTE = ["#e8833a", "#4f8cff", "#3fb68b", "#d65db1", "#c9a227", "#8a6cff", "#e05252", "#2bb3c0"]


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self.tx() as c:
            c.executescript(SCHEMA)

    # -- connection helpers -------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    class _Tx:
        def __init__(self, store):
            self.store = store

        def __enter__(self):
            self.store._write_lock.acquire()
            return self.store.conn

        def __exit__(self, et, ev, tb):
            try:
                if et is None:
                    self.store.conn.commit()
                else:
                    self.store.conn.rollback()
            finally:
                self.store._write_lock.release()

    def tx(self):
        return Store._Tx(self)

    def q(self, sql: str, args: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def q1(self, sql: str, args: Iterable = ()) -> dict | None:
        r = self.conn.execute(sql, tuple(args)).fetchone()
        return dict(r) if r else None

    # -- meta ---------------------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_meta(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # -- dogs ---------------------------------------------------------------
    def dogs(self) -> list[dict]:
        return self.q("SELECT * FROM dogs ORDER BY id")

    def add_dog(self, name: str, color: str | None = None, notes: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("name required")
        with self.tx() as c:
            n = c.execute("SELECT COUNT(*) FROM dogs").fetchone()[0]
            color = color or PALETTE[n % len(PALETTE)]
            cur = c.execute("INSERT INTO dogs(name, color, notes, created) VALUES (?,?,?,?)",
                            (name, color, notes, time.time()))
            did = cur.lastrowid
        return self.q1("SELECT * FROM dogs WHERE id=?", (did,))

    def update_dog(self, dog_id: int, **fields) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in ("name", "color", "notes") and v is not None}
        if allowed:
            with self.tx() as c:
                sets = ", ".join(f"{k}=?" for k in allowed)
                c.execute(f"UPDATE dogs SET {sets} WHERE id=?", (*allowed.values(), dog_id))
        return self.q1("SELECT * FROM dogs WHERE id=?", (dog_id,))

    def delete_dog(self, dog_id: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE barks SET label_dog_id=NULL WHERE label_dog_id=?", (dog_id,))
            c.execute("UPDATE barks SET pred_dog_id=NULL, pred_conf=NULL WHERE pred_dog_id=?", (dog_id,))
            c.execute("DELETE FROM dogs WHERE id=?", (dog_id,))

    # -- episodes & barks ---------------------------------------------------
    def create_episode(self, start_ts: float, clip_path: str | None, clip_start_ts: float | None) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO episodes(start_ts, end_ts, clip_path, clip_start_ts) VALUES (?,?,?,?)",
                (start_ts, start_ts, clip_path, clip_start_ts))
            return cur.lastrowid

    def add_bark(self, episode_id: int, ts: float, duration: float, prob: float, peak_db: float,
                 feats: np.ndarray | None, pred: int | None = None, conf: float | None = None) -> int:
        blob = feats.astype(np.float32).tobytes() if feats is not None else None
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO barks(episode_id, ts, duration, prob, peak_db, features, pred_dog_id, pred_conf)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (episode_id, ts, duration, prob, peak_db, blob, pred, conf))
            return cur.lastrowid

    def finish_episode(self, episode_id: int, end_ts: float, bark_count: int, peak_db: float,
                       max_prob: float, clip_seconds: float | None) -> None:
        with self.tx() as c:
            c.execute("UPDATE episodes SET end_ts=?, bark_count=?, peak_db=?, max_prob=?, clip_seconds=?"
                      " WHERE id=?", (end_ts, bark_count, peak_db, max_prob, clip_seconds, episode_id))

    def delete_episode(self, episode_id: int) -> str | None:
        ep = self.q1("SELECT clip_path FROM episodes WHERE id=?", (episode_id,))
        with self.tx() as c:
            c.execute("DELETE FROM barks WHERE episode_id=?", (episode_id,))
            c.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
        return ep["clip_path"] if ep else None

    def barks_for(self, episode_id: int) -> list[dict]:
        rows = self.q("SELECT id, episode_id, ts, duration, prob, peak_db, pred_dog_id, pred_conf, label_dog_id"
                      " FROM barks WHERE episode_id=? ORDER BY ts", (episode_id,))
        for r in rows:
            r["dog_id"] = r["label_dog_id"] if r["label_dog_id"] is not None else r["pred_dog_id"]
        return rows

    def label_barks(self, bark_ids: list[int], dog_id: int | None) -> None:
        if not bark_ids:
            return
        with self.tx() as c:
            c.executemany("UPDATE barks SET label_dog_id=? WHERE id=?", [(dog_id, b) for b in bark_ids])

    def label_episode(self, episode_id: int, dog_id: int | None) -> None:
        with self.tx() as c:
            c.execute("UPDATE barks SET label_dog_id=? WHERE episode_id=?", (dog_id, episode_id))
            c.execute("UPDATE episodes SET reviewed=1, false_positive=? WHERE id=?",
                      (1 if dog_id == NOT_BARK else 0, episode_id))

    def refresh_episode_flags(self, episode_id: int) -> None:
        """An episode is a false positive if every bark in it is labelled/predicted not-a-bark."""
        rows = self.barks_for(episode_id)
        if not rows:
            return
        fp = all(r["dog_id"] == NOT_BARK for r in rows)
        with self.tx() as c:
            c.execute("UPDATE episodes SET false_positive=? WHERE id=?", (1 if fp else 0, episode_id))

    def set_episode(self, episode_id: int, **fields) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("reviewed", "note", "false_positive")}
        if allowed:
            with self.tx() as c:
                sets = ", ".join(f"{k}=?" for k in allowed)
                c.execute(f"UPDATE episodes SET {sets} WHERE id=?", (*allowed.values(), episode_id))

    def training_set(self) -> tuple[np.ndarray, np.ndarray]:
        rows = self.conn.execute(
            "SELECT features, label_dog_id FROM barks WHERE label_dog_id IS NOT NULL AND features IS NOT NULL"
        ).fetchall()
        if not rows:
            return np.zeros((0, 0), np.float32), np.zeros(0, np.int64)
        X = np.stack([np.frombuffer(r[0], dtype=np.float32) for r in rows])
        y = np.array([r[1] for r in rows], dtype=np.int64)
        return X, y

    def unlabeled_features(self) -> tuple[list[int], np.ndarray]:
        rows = self.conn.execute(
            "SELECT id, features FROM barks WHERE label_dog_id IS NULL AND features IS NOT NULL").fetchall()
        if not rows:
            return [], np.zeros((0, 0), np.float32)
        return [r[0] for r in rows], np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])

    def set_predictions(self, preds: list[tuple[int | None, float | None, int]]) -> None:
        with self.tx() as c:
            c.executemany("UPDATE barks SET pred_dog_id=?, pred_conf=? WHERE id=?", preds)

    def episodes_between(self, t0: float, t1: float, include_fp: bool = False) -> list[dict]:
        sql = "SELECT * FROM episodes WHERE start_ts < ? AND end_ts >= ?"
        if not include_fp:
            sql += " AND false_positive=0"
        return self.q(sql + " ORDER BY start_ts", (t1, t0))

    def bark_dogs_between(self, t0: float, t1: float) -> list[dict]:
        return self.q(
            "SELECT b.episode_id, COALESCE(b.label_dog_id, b.pred_dog_id) AS dog_id, COUNT(*) AS n"
            " FROM barks b JOIN episodes e ON e.id=b.episode_id"
            " WHERE e.start_ts < ? AND e.end_ts >= ? AND e.false_positive=0"
            " GROUP BY b.episode_id, dog_id", (t1, t0))

    def old_clips(self, before_ts: float) -> list[dict]:
        return self.q("SELECT id, clip_path FROM episodes WHERE start_ts < ? AND clip_path IS NOT NULL",
                      (before_ts,))

    def clear_clip(self, episode_id: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE episodes SET clip_path=NULL WHERE id=?", (episode_id,))
