"""Live state for the dashboard band: which picture to show and the EQ levels.

Modes:
  bark - a bark was detected (stays on while the episode is open, and briefly after)
  sit  - medium noise or louder without a bark, or daytime with some background noise
  nap  - quiet during quiet hours (from the ordinance code parameters), or a daytime whose
         background noise is below the daytime-nap level (Settings > Live band)

Switching between nap and sit waits a few seconds so a single car door doesn't flip the picture.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import ordinance as O

NOISY_ABOVE_FLOOR_DB = 8.0     # smoothed level this far above background counts as medium noise
NOISY_ABSOLUTE_DBFS = -38.0    # ...or this loud regardless of background
SETTLE_SECONDS = 4.0           # nap <-> sit only after the condition holds this long
BARK_LINGER_SECONDS = 2.5


class LiveMode:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.tz = ZoneInfo(cfg.timezone) if cfg.timezone else None
        self.mode = None
        self.candidate, self.candidate_since = None, 0.0
        self._quiet_cache = (0.0, None)
        self._lock = threading.Lock()

    def quiet_hours_now(self, now: float | None = None) -> bool:
        now = now or time.time()
        t_cached, th = self._quiet_cache
        if th is None or now - t_cached > 5:
            th = O.get_ordinance(self.store)["thresholds"]
            self._quiet_cache = (now, th)
        dt = datetime.fromtimestamp(now, self.tz) if self.tz else datetime.fromtimestamp(now)
        start, end = O._parse_hhmm(th["quiet_start"]), O._parse_hhmm(th["quiet_end"])
        t = dt.time()
        if start == end:
            return False
        return start <= t < end if start < end else (t >= start or t < end)

    @staticmethod
    def is_barking(det, now: float | None = None) -> bool:
        now = now or time.time()
        return det.ep is not None or bool(det.last_bark_wall and now - det.last_bark_wall < BARK_LINGER_SECONDS)

    @staticmethod
    def smoothed_level(det) -> tuple[float, float]:
        hist = list(det.history)[-20:]                 # ~2 s (history samples every 100 ms)
        if not hist:
            return -90.0, det.floor or -90.0
        return sum(h[1] for h in hist) / len(hist), hist[-1][2]

    def snapshot(self, det, error: str | None = None, now: float | None = None) -> dict:
        now = now or time.time()
        if det is None:
            return {"state": "off", "mode": "nap", "barks": 0, "episode_barks": 0,
                    "level_db": None, "floor_db": None, "bands": [], "quiet_hours": self.quiet_hours_now(now),
                    "error": error}
        ep = det.ep
        barking = self.is_barking(det, now)
        level, floor = self.smoothed_level(det)
        noisy = level - floor >= NOISY_ABOVE_FLOOR_DB or level >= NOISY_ABSOLUTE_DBFS
        quiet_hours = self.quiet_hours_now(now)
        ui = self.cfg.ui
        day_quiet = ui.day_nap and level < ui.day_nap_below_dbfs     # a calm daytime counts as nap too
        want = "bark" if barking else ("sit" if noisy else "nap" if quiet_hours or day_quiet else "sit")
        with self._lock:
            if want == "bark" or self.mode in (None, "bark"):
                self.mode, self.candidate = want, None
            elif want != self.mode:
                if self.candidate != want:
                    self.candidate, self.candidate_since = want, now
                elif now - self.candidate_since >= SETTLE_SECONDS:
                    self.mode, self.candidate = want, None
            else:
                self.candidate = None
            mode = self.mode
        return {
            "state": "barking" if barking else "idle", "mode": mode,
            "barks": det.stats.get("barks", 0), "episode_barks": ep.barks if ep else 0,
            "level_db": round(det.prev_level, 1), "floor_db": round(det.floor or -90, 1),
            "noisy": bool(noisy), "quiet_hours": quiet_hours, "ambient_db": round(level, 1),
            "day_quiet": bool(day_quiet),
            "bands": [round(float(v), 2) for v in det.eq],
            "error": error,
        }
