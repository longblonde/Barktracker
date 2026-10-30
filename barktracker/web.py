"""FastAPI dashboard + JSON API."""
from __future__ import annotations

import asyncio
import base64
import csv
import json
import io
import secrets
import shutil
import threading
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import alerts as AL
from . import backup as BK
from . import discover as DS
from . import sysinfo as SI
from . import themes as TH
from . import features as F
from . import ordinance as O
from . import settings as S
from .live import LiveMode
from . import voice as V
from .app import BarkApp
from .audio import read_wav
from .storage import NOT_BARK

STATIC = Path(__file__).parent / "static"
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm"}
VIDEO = {".mp4", ".webm"}
MEDIA_KINDS = ("quiet", "active", "bark")
LIVE_SLOW, LIVE_FAST = 1 / 6, 1 / 20      # seconds between live updates: quiet / barking   # nap / sit / bark pictures


class DogIn(BaseModel):
    name: str | None = None
    color: str | None = None
    notes: str | None = None
    owner: str | None = None
    address: str | None = None
    description: str | None = None
    size: str | None = None


class LabelIn(BaseModel):
    dog_id: int | None = None          # 0 = not a bark, null = clear label


class BarkLabelIn(BaseModel):
    bark_ids: list[int]
    dog_id: int | None = None


class EpisodePatch(BaseModel):
    reviewed: bool | None = None
    note: str | None = None


def create_app(bark: BarkApp) -> FastAPI:
    cfg, store = bark.cfg, bark.store
    tz = ZoneInfo(cfg.timezone) if cfg.timezone else None
    security = HTTPBasic(auto_error=False)

    def auth(creds: HTTPBasicCredentials | None = Depends(security)):
        if not (cfg.web.username and cfg.web.password):
            return
        ok = creds is not None and secrets.compare_digest(creds.username, cfg.web.username) \
            and secrets.compare_digest(creds.password, cfg.web.password)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    build = S.build_info()          # looked up once at startup
    app = FastAPI(title="Barktracker", version=build["version"], dependencies=[Depends(auth)], docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- time helpers -------------------------------------------------------
    def to_dt(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts)

    def day_start(d: date) -> float:
        dt = datetime(d.year, d.month, d.day, tzinfo=tz) if tz else datetime(d.year, d.month, d.day)
        return dt.timestamp()

    def today() -> date:
        return to_dt(time.time()).date()

    def dog_split(t0: float, t1: float) -> dict[int, dict]:
        """episode_id -> {dog_key: bark_count}; dog_key is dog id, or 'u' for unidentified."""
        out: dict[int, dict] = defaultdict(dict)
        for r in store.bark_dogs_between(t0, t1):
            if r["dog_id"] == NOT_BARK:
                continue
            k = "u" if r["dog_id"] is None else str(r["dog_id"])
            out[r["episode_id"]][k] = out[r["episode_id"]].get(k, 0) + r["n"]
        return out

    def parse_sel(s: str | None) -> set[str] | None:
        """'1,3,u' -> {'1','3','u'} (dog ids, u = unidentified); empty = everyone."""
        if not s:
            return None
        sel = {x.strip() for x in s.split(",") if x.strip()}
        if not all(x == "u" or x.isdigit() for x in sel):
            raise HTTPException(400, "dogs must be dog ids or u, separated by commas")
        return sel or None

    def share(split: dict, sel: set[str] | None) -> float:
        """Fraction of an episode's barking that belongs to the selected dogs (by bark count)."""
        if sel is None:
            return 1.0
        tot = sum(split.values())
        if not tot:
            return 1.0 if "u" in sel else 0.0
        return sum(n for k, n in split.items() if k in sel) / tot

    def marking(n: int, labelled: int, reviewed: bool) -> str:
        """How the dogs in an episode were decided: auto (detector + matching only), manual (you
        labelled every bark, or reviewed it and kept what it found), or mixed."""
        if n and labelled >= n:
            return "manual"
        if labelled:
            return "mixed"
        return "manual" if reviewed else "auto"

    DOUBT_PROB, DOUBT_MATCH = 0.6, 0.75

    def review_tag(e: dict, marked: str, split: dict, match: dict) -> str:
        """Manual (you decided), Auto (found and matched with confidence), or Unreviewed (nobody
        has looked and something about it is uncertain)."""
        if marked in ("manual", "mixed"):
            return "manual"
        if e["false_positive"]:
            return "auto"
        doubt = (e["max_prob"] or 0) < DOUBT_PROB or any(v < DOUBT_MATCH for v in match.values()) \
            or ("u" in split and has_dogs())
        return "unreviewed" if doubt else "auto"

    _dogs_cache = {"t": 0.0, "v": False}

    def has_dogs() -> bool:
        if time.time() - _dogs_cache["t"] > 5:
            _dogs_cache.update(t=time.time(), v=bool(store.q1("SELECT 1 FROM dogs LIMIT 1")))
        return _dogs_cache["v"]

    def ep_view(e: dict, split: dict | None = None) -> dict:
        dogs = split if split is not None else {}
        return {
            "id": e["id"], "start_ts": e["start_ts"], "end_ts": e["end_ts"],
            "duration": round(max(0.0, e["end_ts"] - e["start_ts"]), 2),
            "bark_count": e["bark_count"], "peak_db": e["peak_db"], "max_prob": e["max_prob"],
            "has_clip": bool(e["clip_path"]) and Path(e["clip_path"]).exists(),
            "clip_seconds": e["clip_seconds"], "clip_start_ts": e["clip_start_ts"],
            "reviewed": bool(e["reviewed"]), "false_positive": bool(e["false_positive"]),
            "note": e["note"] or "", "dogs": dogs,
            "live": e["clip_seconds"] is None,
        }

    def get_ep(eid: int) -> dict:
        e = store.q1("SELECT * FROM episodes WHERE id=?", (eid,))
        if not e:
            raise HTTPException(404, "episode not found")
        return e

    # ---- pages ----------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text()

    # ---- status ---------------------------------------------------------------
    @app.get("/api/status")
    def status():
        du = shutil.disk_usage(cfg.data_path)
        clip_bytes = sum(p.stat().st_size for p in cfg.clips_path.rglob("*.wav")) if cfg.clips_path.exists() else 0
        det = bark.detector.status() if bark.detector else None
        return {
            "now": time.time(), "detector": det, "error": bark.error,
            "disk": {"free_mb": du.free // 2**20, "total_mb": du.total // 2**20,
                     "clips_mb": round(clip_bytes / 2**20, 1), "cap_mb": cfg.clips.max_disk_mb},
            "counts": store.q1("SELECT COUNT(*) episodes, COALESCE(SUM(bark_count),0) barks,"
                               " SUM(reviewed=0 AND false_positive=0) unreviewed FROM episodes"),
            "identifier": {"ready": bool(bark.identifier and bark.identifier.ready),
                           "classes": bark.identifier.classes if bark.identifier else {},
                           "min_labels": cfg.identify.min_labels_per_dog},
            "settings": {"episode_gap": cfg.detection.episode_gap,
                         "bark_threshold": cfg.detection.bark_threshold,
                         "retention_days": cfg.clips.retention_days,
                         "timezone": cfg.timezone or "system"},
            "clock": clock_status(),
            "build": build,
            "audio_device": getattr(bark.source, "device_name", None),
        }

    # ---- stats ----------------------------------------------------------------
    @app.get("/api/summary")
    def summary(days: int = 14, dogs: str | None = None):
        sel = parse_sel(dogs)
        days = max(1, min(days, 366))
        d_end = today()
        d0 = d_end - timedelta(days=days - 1)
        t0, t1 = day_start(d0), day_start(d_end + timedelta(days=1))
        eps = store.episodes_between(t0, t1)
        split = dog_split(t0, t1)
        buckets = {d0 + timedelta(days=i): {"episodes": 0, "barks": 0, "seconds": 0.0,
                                             "longest": 0.0, "by_dog": defaultdict(float)}
                   for i in range(days)}
        for e in eps:
            d = to_dt(e["start_ts"]).date()
            if d not in buckets:
                continue
            b = buckets[d]
            dur = max(0.0, e["end_ts"] - e["start_ts"])
            s = split.get(e["id"], {})
            f = share(s, sel)
            if f <= 0:
                continue
            b["episodes"] += 1
            b["barks"] += e["bark_count"] if sel is None else sum(n for k, n in s.items() if k in sel) or e["bark_count"]
            b["seconds"] += dur * f
            b["longest"] = max(b["longest"], dur * f)
            tot = sum(s.values())
            for k, n in s.items():
                if sel is None or k in sel:
                    b["by_dog"][k] += dur * n / tot
            if not tot:
                b["by_dog"]["u"] += dur
        out = [{"date": d.isoformat(), **{k: (round(v, 1) if isinstance(v, float) else v)
                                          for k, v in b.items() if k != "by_dog"},
                "by_dog": {k: round(v, 1) for k, v in b["by_dog"].items()}}
               for d, b in sorted(buckets.items())]
        return {"days": out, "dogs": store.dogs()}

    @app.get("/api/day")
    def day(ds: str | None = Query(None, alias="date"), dogs: str | None = None):
        sel = parse_sel(dogs)
        d = date.fromisoformat(ds) if ds else today()
        t0, t1 = day_start(d), day_start(d + timedelta(days=1))
        split = dog_split(t0, t1)
        eps = [e for e in store.episodes_between(t0, t1) if share(split.get(e["id"], {}), sel) > 0]
        barks = store.q("SELECT b.ts, NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) dog_id FROM barks b"
                        " JOIN episodes e ON e.id=b.episode_id WHERE b.ts>=? AND b.ts<? AND"
                        " e.false_positive=0 AND COALESCE(b.label_dog_id, b.pred_dog_id, -1) != 0",
                        (t0, t1))
        if sel is not None:
            barks = [b for b in barks if ("u" if b["dog_id"] is None else str(b["dog_id"])) in sel]
        ordn = O.get_ordinance(store)
        th = ordn["thresholds"]
        incs = O.build_incidents([{**e, "subject_barks": e["bark_count"]} for e in eps], th, O.Clock(cfg.timezone),
                                 ordn["rules"])
        # quiet hours falling on this day, as timestamps (handles windows across midnight and DST)
        qs, qe = O._parse_hhmm(th["quiet_start"]), O._parse_hhmm(th["quiet_end"])

        def at(day_: date, t) -> float:
            dt = datetime(day_.year, day_.month, day_.day, t.hour, t.minute, tzinfo=tz) if tz else \
                datetime(day_.year, day_.month, day_.day, t.hour, t.minute)
            return dt.timestamp()
        if qs == qe:
            quiet = []
        elif qs < qe:
            quiet = [[at(d, qs), at(d, qe)]]
        else:
            quiet = [[t0, at(d, qe)], [at(d, qs), t1]]
        return {"date": d.isoformat(), "start_ts": t0, "end_ts": t1, "labels": O.rule_labels(ordn),
                "quiet": quiet, "quiet_start": th["quiet_start"], "quiet_end": th["quiet_end"],
                "incidents": [{"start_ts": i["start_ts"], "end_ts": i["end_ts"], "flags": i["flags"],
                               "duration": i["duration"]} for i in incs],
                "episodes": [{**ep_view(e, split.get(e["id"], {})), "share": round(share(split.get(e["id"], {}), sel), 3)}
                             for e in eps],
                "dogs_filter": sorted(sel) if sel else None,
                "barks": [[round(b["ts"], 2), b["dog_id"]] for b in barks]}

    @app.get("/api/heatmap")
    def heatmap(days: int = 28, dogs: str | None = None):
        """Barking by weekday x hour over the past `days` days: seconds (from episode spans, so pauses
        inside an episode count), plus barks, episodes and barks per dog in each cell, the number of
        each weekday in the range, and which part of each hour falls in quiet hours."""
        sel = parse_sel(dogs)
        days = max(1, min(days, 366))
        d_end = today()
        d0 = d_end - timedelta(days=days - 1)
        t0, t1 = day_start(d0), day_start(d_end + timedelta(days=1))
        split = dog_split(t0, t1)
        grid = [[0.0] * 24 for _ in range(7)]   # [weekday Mon=0][hour] seconds
        eps_n = [[0] * 24 for _ in range(7)]
        barks_n = [[0] * 24 for _ in range(7)]
        by_dog = [[defaultdict(int) for _ in range(24)] for _ in range(7)]
        for e in store.episodes_between(t0, t1):
            f = share(split.get(e["id"], {}), sel)
            if f <= 0:
                continue
            s, end = max(e["start_ts"], t0), min(max(e["end_ts"], e["start_ts"] + 0.5), t1)
            first = True
            while s < end:
                dt = to_dt(s)
                nxt = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
                seg = min(end, nxt) - s
                grid[dt.weekday()][dt.hour] += seg * f
                if first:
                    eps_n[dt.weekday()][dt.hour] += 1     # an episode counts in the hour it started
                    first = False
                s = min(end, nxt)
        for r in store.q("SELECT b.ts, NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) d FROM barks b JOIN episodes e"
                         " ON e.id=b.episode_id WHERE b.ts>=? AND b.ts<? AND e.false_positive=0", (t0, t1)):
            if r["d"] == NOT_BARK:
                continue
            k = "u" if r["d"] is None else str(r["d"])
            if sel is not None and k not in sel:
                continue
            dt = to_dt(r["ts"])
            barks_n[dt.weekday()][dt.hour] += 1
            by_dog[dt.weekday()][dt.hour][k] += 1
        weekdays = [0] * 7
        for i in range(days):
            weekdays[(d0 + timedelta(days=i)).weekday()] += 1
        th = O.get_ordinance(store)["thresholds"]
        qs, qe = O._parse_hhmm(th["quiet_start"]), O._parse_hhmm(th["quiet_end"])
        a, b = qs.hour * 60 + qs.minute, qe.hour * 60 + qe.minute
        spans = [] if a == b else ([(a, b)] if a < b else [(a, 1440), (0, b)])
        quiet = []
        for h in range(24):
            parts = []
            for x, y in spans:
                lo, hi = max(x, h * 60), min(y, h * 60 + 60)
                if hi > lo:
                    parts.append([round((lo - h * 60) / 60, 3), round((hi - h * 60) / 60, 3)])
            quiet.append(parts)
        return {"days": days, "grid": [[round(v, 1) for v in row] for row in grid],
                "barks": barks_n, "episodes": eps_n,
                "dogs": [[dict(c) for c in row] for row in by_dog],
                "weekdays": weekdays, "quiet": quiet,
                "quiet_start": th["quiet_start"], "quiet_end": th["quiet_end"]}

    # ---- episodes -------------------------------------------------------------
    @app.get("/api/episodes")
    def episodes(limit: int = 50, offset: int = 0, dog: str | None = None,
                 unreviewed: bool = False, include_fp: bool = False, min_duration: float = 0.0,
                 date_from: str | None = None, date_to: str | None = None, kind: str = "barking",
                 dogs: str | None = None, review: str | None = None, since: float | None = None):
        where, args = ["1=1"], []
        if since:
            where.append("e.start_ts > ?")
            args.append(since)
        labelled_sql = "EXISTS (SELECT 1 FROM barks b WHERE b.episode_id=e.id AND b.label_dog_id IS NOT NULL)"
        if review == "todo":        # nobody has looked at it: not reviewed, no bark labelled
            where.append(f"e.reviewed=0 AND NOT {labelled_sql}")
        elif review == "done":      # reviewed or at least partly labelled by you
            where.append(f"(e.reviewed=1 OR {labelled_sql})")
        sel = parse_sel(dogs)
        if sel:
            conds = []
            ids = [int(x) for x in sel if x != "u"]
            if ids:
                conds.append("NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) IN (%s)" % ",".join("?" * len(ids)))
                args.extend(ids)
            if "u" in sel:
                conds.append("NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) IS NULL")
            where.append("EXISTS (SELECT 1 FROM barks b WHERE b.episode_id=e.id AND (%s))" % " OR ".join(conds))
        if kind == "not_bark":
            where.append("e.false_positive=1")
        elif not include_fp:
            where.append("e.false_positive=0")
        if unreviewed:
            where.append("e.reviewed=0")
        if min_duration:
            where.append("(e.end_ts - e.start_ts) >= ?")
            args.append(min_duration)
        if date_from:
            where.append("e.start_ts >= ?")
            args.append(day_start(date.fromisoformat(date_from)))
        if date_to:
            where.append("e.start_ts < ?")
            args.append(day_start(date.fromisoformat(date_to) + timedelta(days=1)))
        if dog:
            if dog == "u":
                where.append("EXISTS (SELECT 1 FROM barks b WHERE b.episode_id=e.id AND"
                             " NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) IS NULL)")
            else:
                where.append("EXISTS (SELECT 1 FROM barks b WHERE b.episode_id=e.id AND"
                             " NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) = ?)")
                args.append(int(dog))
        w = " AND ".join(where)
        total = store.q1(f"SELECT COUNT(*) n FROM episodes e WHERE {w}", args)["n"]
        rows = store.q(f"SELECT e.* FROM episodes e WHERE {w} ORDER BY e.start_ts DESC LIMIT ? OFFSET ?",
                       (*args, min(limit, 500), offset))
        out = []
        for e in rows:
            s = {}
            n = lab = 0
            match = {}
            for r in store.q("SELECT NULLIF(COALESCE(label_dog_id, pred_dog_id), -1) d, COUNT(*) n,"
                             " SUM(label_dog_id IS NOT NULL) lab,"
                             " AVG(CASE WHEN label_dog_id IS NULL THEN pred_conf END) conf"
                             " FROM barks WHERE episode_id=? GROUP BY d", (e["id"],)):
                n += r["n"]
                lab += r["lab"] or 0
                if r["d"] == NOT_BARK:
                    continue
                k = "u" if r["d"] is None else str(r["d"])
                s[k] = r["n"]
                if k != "u" and r["conf"] is not None and (r["lab"] or 0) < r["n"]:
                    match[k] = round(r["conf"], 2)        # how sure the automatic dog match was
            mk = marking(n, lab, bool(e["reviewed"]))
            out.append({**ep_view(e, s), "marked": mk, "labelled": lab, "match": match,
                        "tag": review_tag(e, mk, s, match)})
        return {"total": total, "items": out}

    @app.get("/api/clips/counts")
    def clip_counts(since: float = 0.0):
        """For the dashboard link: clips nobody has reviewed, and clips newer than your last look."""
        todo = store.q1("SELECT COUNT(*) n FROM episodes e WHERE e.false_positive=0 AND e.reviewed=0 AND NOT EXISTS"
                        " (SELECT 1 FROM barks b WHERE b.episode_id=e.id AND b.label_dog_id IS NOT NULL)")["n"]
        new = store.q1("SELECT COUNT(*) n FROM episodes WHERE false_positive=0 AND start_ts > ?", (since,))["n"]
        total = store.q1("SELECT COUNT(*) n FROM episodes WHERE false_positive=0")["n"]
        return {"to_review": todo, "new": new if since else 0, "total": total}

    @app.get("/api/episodes/{eid}")
    def episode(eid: int, all_kinds: bool = False):
        e = get_ep(eid)
        barks = store.barks_for(eid)
        cs = e["clip_start_ts"] or e["start_ts"]
        from .array import nearest_dirs
        dirs = nearest_dirs(store, [b["ts"] for b in barks])
        for b in barks:
            b["offset"] = round(b["ts"] - cs, 3)
            dr = dirs.get(b["ts"])
            b["bearing"] = dr["bearing"] if dr and dr["valid"] else None
        s = {}
        for b in barks:
            if b["dog_id"] == NOT_BARK:
                continue
            k = "u" if b["dog_id"] is None else str(b["dog_id"])
            s[k] = s.get(k, 0) + 1
        # Earlier/Later stay within the same kind: barking clips, or "Not a bark" clips under review
        fp = 1 if e["false_positive"] else 0
        kind = "1=1" if all_kinds else f"false_positive={fp}"
        prev = store.q1(f"SELECT id FROM episodes WHERE start_ts < ? AND {kind}"
                        " ORDER BY start_ts DESC LIMIT 1", (e["start_ts"],))
        nxt = store.q1(f"SELECT id FROM episodes WHERE start_ts > ? AND {kind}"
                       " ORDER BY start_ts LIMIT 1", (e["start_ts"],))
        pos = store.q1(f"SELECT SUM(start_ts >= ?) i, COUNT(*) n FROM episodes WHERE {kind}",
                       (e["start_ts"],))
        lab = sum(1 for b in barks if b["label_dog_id"] is not None)
        mk = marking(len(barks), lab, bool(e["reviewed"]))
        match = {}
        for b in barks:
            if b["label_dog_id"] is None and b["pred_dog_id"] not in (None, NOT_BARK, -1) and b["pred_conf"] is not None:
                k = str(b["pred_dog_id"])
                match[k] = min(match.get(k, 1.0), b["pred_conf"])
        bs = sorted(x["bearing"] for x in barks if x["bearing"] is not None)
        return {**ep_view(e, s), "marked": mk, "labelled": lab, "tag": review_tag(e, mk, s, match),
                "bearing": bs[len(bs) // 2] if bs else None, "bearings": len(bs),
                "barks": barks, "sound": V.episode_sound(store, eid),
                "position": {"index": pos["i"] or 0, "of": pos["n"] or 0},
                "prev_id": prev["id"] if prev else None, "next_id": nxt["id"] if nxt else None}

    @app.get("/api/episodes/{eid}/audio")
    def episode_audio(eid: int):
        e = get_ep(eid)
        if not e["clip_path"] or not Path(e["clip_path"]).exists():
            raise HTTPException(404, "clip not available")
        return FileResponse(e["clip_path"], media_type="audio/wav",
                            filename=Path(e["clip_path"]).name, content_disposition_type="inline")

    @app.get("/api/episodes/{eid}/spectrogram")
    def episode_spec(eid: int, width: int = 900, height: int = 80):
        e = get_ep(eid)
        if not e["clip_path"] or not Path(e["clip_path"]).exists():
            raise HTTPException(404, "clip not available")
        x, sr = read_wav(e["clip_path"])
        x = F.resample(x, sr)
        img, secs = F.spectrogram_image(x, width=max(100, min(width, 2000)), height=max(32, min(height, 160)))
        return {"width": img.shape[1], "height": img.shape[0], "seconds": secs,
                "data": base64.b64encode(img[::-1].tobytes()).decode()}   # row 0 = top = high freq

    @app.patch("/api/episodes/{eid}")
    def patch_episode(eid: int, body: EpisodePatch):
        get_ep(eid)
        store.set_episode(eid, **{k: (int(v) if isinstance(v, bool) else v)
                                  for k, v in body.model_dump().items() if v is not None})
        return episode(eid)

    @app.post("/api/episodes/{eid}/label")
    def label_episode(eid: int, body: LabelIn):
        get_ep(eid)
        _check_dog(body.dog_id)
        store.label_episode(eid, body.dog_id)
        if body.dog_id is None:
            store.refresh_episode_flags(eid)
        info = bark.retrain()
        return {"episode": episode(eid), "identifier": info}

    @app.post("/api/barks/label")
    def label_barks(body: BarkLabelIn):
        _check_dog(body.dog_id)
        store.label_barks(body.bark_ids, body.dog_id)
        eps = {r["episode_id"] for r in store.q(
            f"SELECT DISTINCT episode_id FROM barks WHERE id IN ({','.join('?' * len(body.bark_ids))})",
            body.bark_ids)} if body.bark_ids else set()
        for e in eps:
            store.refresh_episode_flags(e)
            store.set_episode(e, reviewed=1)
        return {"identifier": bark.retrain()}

    @app.delete("/api/episodes/{eid}")
    def delete_episode(eid: int, keep_training: bool = False):
        """keep_training=true removes only the audio: the episode stays (hidden) and its sounds
        keep teaching the identifier, e.g. a "Not a bark" example the filter learned from."""
        e = get_ep(eid)
        if keep_training:
            if e["clip_path"]:
                Path(e["clip_path"]).unlink(missing_ok=True)
                store.clear_clip(eid)
            return {"ok": True, "kept_training": True}
        path = store.delete_episode(eid)
        if path:
            Path(path).unlink(missing_ok=True)
        bark.retrain()
        return {"ok": True}

    @app.get("/api/not-bark")
    def not_bark_summary():
        rows = store.q("SELECT id, clip_path FROM episodes WHERE false_positive=1")
        with_audio = [r for r in rows if r["clip_path"] and Path(r["clip_path"]).exists()]
        size = sum(Path(r["clip_path"]).stat().st_size for r in with_audio)
        inside = store.q1("SELECT COUNT(*) n FROM barks b JOIN episodes e ON e.id=b.episode_id"
                          " WHERE b.label_dog_id=0 AND e.false_positive=0")["n"]
        examples = store.q1("SELECT COUNT(*) n FROM barks WHERE label_dog_id=0 AND features IS NOT NULL")["n"]
        return {"episodes": len(rows), "with_audio": len(with_audio), "audio_mb": round(size / 2**20, 1),
                "sounds_inside_barking_clips": inside, "training_examples": examples,
                "filter_active": bool(bark.identifier and NOT_BARK in bark.identifier.classes)}

    @app.post("/api/not-bark/delete-audio")
    def not_bark_delete_audio():
        """Free disk space from every "Not a bark" clip while keeping what the filter learned."""
        n, freed = 0, 0
        for r in store.q("SELECT id, clip_path FROM episodes WHERE false_positive=1 AND clip_path IS NOT NULL"):
            p = Path(r["clip_path"])
            if p.exists():
                freed += p.stat().st_size
                p.unlink(missing_ok=True)
            store.clear_clip(r["id"])
            n += 1
        return {"deleted": n, "freed_mb": round(freed / 2**20, 1), **not_bark_summary()}

    def _check_size(size):
        if size not in (None, "", *V.SIZES):
            raise HTTPException(400, "size must be small, medium or large")

    def _check_dog(dog_id):
        if dog_id not in (None, NOT_BARK, -1) and not store.q1("SELECT 1 FROM dogs WHERE id=?", (dog_id,)):
            raise HTTPException(400, "unknown dog")

    # ---- dogs -----------------------------------------------------------------
    @app.get("/api/dogs")
    def dogs():
        rows = store.dogs()
        for d in rows:
            d["labelled_barks"] = store.q1("SELECT COUNT(*) n FROM barks WHERE label_dog_id=?", (d["id"],))["n"]
            d["predicted_barks"] = store.q1("SELECT COUNT(*) n FROM barks WHERE label_dog_id IS NULL"
                                            " AND pred_dog_id=?", (d["id"],))["n"]
        voices = V.dog_profiles(store)
        from .array import dog_directions
        dirs = dog_directions(store)
        rh = DS.rhythm(store)
        for d in rows:
            d.update(voices.get(d["id"], {}))
            d["direction"] = dirs.get(d["id"])
            d["rhythm"] = rh.get(d["id"])
        nb = store.q1("SELECT COUNT(*) n FROM barks WHERE label_dog_id=0")["n"]
        return {"dogs": rows, "not_bark_examples": nb, "min_labels": cfg.identify.min_labels_per_dog,
                "direction_on": cfg.direction.enabled, "use_direction": cfg.identify.use_direction,
                "unidentified": store.q1("SELECT COUNT(*) n FROM barks b JOIN episodes e ON e.id=b.episode_id"
                                         " WHERE e.false_positive=0 AND b.label_dog_id IS NULL AND b.pred_dog_id IS NULL")["n"]}

    _disc = {"t": 0.0, "v": None}

    @app.get("/api/dogs/discovered")
    def dogs_discovered(fresh: bool = False):
        """Groups of unidentified barks that sound alike (and come from the same way): likely dogs
        you haven't added yet. Cached for a minute; it reads a lot of barks."""
        if fresh or _disc["v"] is None or time.time() - _disc["t"] > 60:
            groups = DS.discovered(store, time.time(), cfg.direction.enabled)
            _disc.update(t=time.time(), v=[{k: v for k, v in g.items() if k != "bark_ids"} | {"n_ids": len(g["bark_ids"])} for g in groups])
            _disc["ids"] = {g["key"]: g["bark_ids"] for g in groups}
        return {"groups": _disc["v"]}

    @app.post("/api/dogs/discovered/{key}")
    def adopt_discovered(key: str, body: dict = Body(...)):
        """Name a discovered group: label the example barks you listened to (as a new dog, a dog you
        already have, or not a dog), then re-match so its other barks follow."""
        ids = [int(x) for x in body.get("bark_ids") or []]
        if not ids:
            raise HTTPException(400, "No barks to label")
        dog_id = body.get("dog_id")
        if body.get("new_name"):
            try:
                dog_id = store.add_dog(str(body["new_name"]).strip()[:40], None, "", "", "", "", "")["id"]
            except Exception as e:  # noqa: BLE001
                raise HTTPException(400, "There's already a dog with that name" if "UNIQUE" in str(e) else str(e))
        _check_dog(dog_id)
        if dog_id is None:
            raise HTTPException(400, "Choose a dog")
        store.label_barks(ids, int(dog_id))
        for e in {r["episode_id"] for r in store.q(f"SELECT DISTINCT episode_id FROM barks WHERE id IN ({','.join('?' * len(ids))})", ids)}:
            store.refresh_episode_flags(e)
        _disc["v"] = None
        return {"dog_id": dog_id, "identifier": bark.retrain()}

    @app.get("/api/bearings")
    def bearings(ds: str | None = Query(None, alias="date"), days: int = 1, dogs: str | None = None):
        """Directions of logged barks over a day (or the days up to it), for the dashboard chart."""
        sel = parse_sel(dogs)
        d1 = date.fromisoformat(ds) if ds else today()
        t0, t1 = day_start(d1 - timedelta(days=max(1, min(days, 90)) - 1)), day_start(d1 + timedelta(days=1))
        rows = store.q(
            "SELECT bd.bearing, NULLIF(COALESCE(b.label_dog_id, b.pred_dog_id), -1) d FROM barks b"
            " JOIN episodes e ON e.id=b.episode_id JOIN bark_dirs bd ON bd.ts BETWEEN b.ts - 0.3 AND b.ts + 0.3"
            " WHERE b.ts >= ? AND b.ts < ? AND bd.valid=1 AND e.false_positive=0"
            " AND COALESCE(b.label_dog_id, b.pred_dog_id, -1) != 0", (t0, t1))
        out = []
        for r in rows:
            k = "u" if r["d"] is None else str(r["d"])
            if sel is None or k in sel:
                out.append([round(r["bearing"], 1), None if r["d"] is None else r["d"]])
        return {"on": cfg.direction.enabled, "barks": out,
                "total": store.q1("SELECT COUNT(*) n FROM barks b JOIN episodes e ON e.id=b.episode_id"
                                  " WHERE b.ts >= ? AND b.ts < ? AND e.false_positive=0", (t0, t1))["n"]}

    @app.post("/api/dogs")
    def add_dog(body: DogIn):
        try:
            _check_size(body.size)
            return store.add_dog(body.name or "", body.color, body.notes or "", body.owner or "",
                                 body.address or "", body.description or "", body.size or "")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))

    @app.patch("/api/dogs/{dog_id}")
    def edit_dog(dog_id: int, body: DogIn):
        _check_size(body.size)
        d = store.update_dog(dog_id, **body.model_dump())
        if not d:
            raise HTTPException(404)
        return d

    @app.delete("/api/dogs/{dog_id}")
    def remove_dog(dog_id: int):
        store.delete_dog(dog_id)
        bark.retrain()
        return {"ok": True}

    @app.post("/api/retrain")
    def retrain():
        return bark.retrain()

    # ---- export ---------------------------------------------------------------
    @app.get("/api/export.csv")
    def export_csv(days: int = 365):
        t0 = time.time() - days * 86400
        names = {d["id"]: d["name"] for d in store.dogs()}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["episode_id", "start_local", "end_local", "duration_s", "barks", "dogs", "reviewed", "note"])
        eps = store.episodes_between(t0, time.time() + 1)
        split = dog_split(t0, time.time() + 1)
        for e in eps:
            s = split.get(e["id"], {})
            dogs_s = "; ".join(f"{names.get(int(k), 'unknown') if k != 'u' else 'unknown'} x{n}"
                               for k, n in s.items())
            w.writerow([e["id"], to_dt(e["start_ts"]).isoformat(timespec="seconds"),
                        to_dt(e["end_ts"]).isoformat(timespec="seconds"),
                        round(e["end_ts"] - e["start_ts"], 1), e["bark_count"], dogs_s,
                        e["reviewed"], e["note"] or ""])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=barktracker.csv"})

    # ---- app settings ---------------------------------------------------------
    @app.get("/api/settings")
    def get_settings():
        return {"values": S.current(cfg), "defaults": bark.defaults,
                "overridden": sorted((store.get_meta(S.META_KEY) or {}).keys()),
                "disk": S.disk_usage(cfg, store),
                "info": {"timezone": cfg.timezone or "system", "data_dir": str(cfg.data_path),
                         "media_dir": str(cfg.media_path), "model": bark.detector.classifier.name if bark.detector else None,
                         "audio_device": getattr(bark.source, "device_name", None)},
                "build": build}

    # ---- themes ---------------------------------------------------------------
    @app.get("/theme.css")
    def theme_css():
        return Response(TH.css(TH.active(cfg, store)), media_type="text/css",
                        headers={"Cache-Control": "no-cache"})

    def themes_view():
        act = TH.active(cfg, store)
        return {"active": act["id"], "theme": act, "themes": TH.all_themes(cfg),
                "color_keys": TH.COLOR_KEYS, "behaviors": {k: v[0] for k, v in TH.BEHAVIORS.items()}}

    @app.get("/api/themes")
    def get_themes():
        return themes_view()

    @app.put("/api/themes/active")
    def use_theme(body: dict = Body(...)):
        try:
            TH.set_active(cfg, store, str(body.get("id") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return themes_view()

    @app.post("/api/themes")
    def add_theme(body: dict = Body(...)):
        """A new theme of yours (from the editor, a duplicate, or an uploaded file)."""
        try:
            t = TH.save(cfg, body)
            if body.get("activate", True):
                TH.set_active(cfg, store, t["id"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        return themes_view()

    @app.put("/api/themes/{tid}")
    def edit_theme(tid: str, body: dict = Body(...)):
        try:
            TH.save(cfg, body, tid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return themes_view()

    @app.delete("/api/themes/{tid}")
    def remove_theme(tid: str):
        try:
            TH.delete(cfg, store, tid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return themes_view()

    @app.get("/api/themes/{tid}.json")
    def download_theme(tid: str):
        t = TH.find(cfg, tid)
        if not t:
            raise HTTPException(404)
        return JSONResponse(TH.export(t), headers={"Content-Disposition": f'attachment; filename="barktracker-theme-{tid}.json"'})

    # ---- alerts to other apps ----------------------------------------------------
    @app.get("/api/alerts")
    def get_alerts():
        return {"items": AL.load(store, cfg), "kinds": AL.KINDS, "when": AL.WHEN, "status": AL.status(store)}

    @app.put("/api/alerts")
    def put_alerts(body: dict = Body(...)):
        try:
            AL.save(store, body.get("items") or [])
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        return get_alerts()

    @app.post("/api/alerts/test")
    def test_alert(body: dict = Body(...)):
        try:
            AL.test(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"It didn't go through: {type(e).__name__}: {e}")
        return {"ok": True}

    # ---- system --------------------------------------------------------------------
    @app.get("/api/system")
    def system():
        det = bark.detector.status() if bark.detector else None
        return {"hardware": SI.info(cfg), "clock": clock_status(), "build": build,
                "timezone": cfg.timezone or "system", "data_dir": str(cfg.data_path),
                "audio_device": getattr(bark.source, "device_name", None),
                "model": bark.detector.classifier.name if bark.detector else None,
                "listening": det is not None, "error": bark.error,
                "uptime_s": det["uptime_s"] if det else None,
                "stats": det["stats"] if det else None,
                "identifier": {"ready": bool(bark.identifier and bark.identifier.ready),
                               "min_labels": cfg.identify.min_labels_per_dog},
                "counts": store.q1("SELECT COUNT(*) episodes, COALESCE(SUM(bark_count),0) barks FROM episodes"
                                   " WHERE false_positive=0"),
                "direction": bark.array.status()}

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        try:
            if body.get("reset"):
                bark.reset_settings()
            else:
                bark.update_settings(body.get("values", {}))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return get_settings()

    # ---- backup to a USB drive and restore -------------------------------------------
    def backup_status():
        st = bark.backup.status()
        st["drives"] = BK.drives(cfg)
        st["restart_pending"] = bark.restart_pending
        st["settings"] = {k: v for k, v in S.current(cfg).items() if k.startswith("backup.")}
        return st

    @app.get("/api/backup")
    def get_backup():
        return backup_status()

    @app.post("/api/backup/check")
    def check_backup_path(body: dict = Body(...)):
        return BK.check_target(cfg, (body.get("path") or "").strip() or None, mount=True)

    @app.post("/api/backup/run")
    def run_backup():
        if bark.backup.lock.locked():
            raise HTTPException(409, "A backup is already running")
        chk = BK.check_target(cfg, cfg.backup.path, mount=True)
        if not chk["ok"]:
            raise HTTPException(400, chk["reason"])
        bark.backup_now()
        time.sleep(0.2)
        return backup_status()

    @app.post("/api/backup/eject")
    def eject_backup():
        if bark.backup.lock.locked():
            raise HTTPException(409, "A backup is running; wait for it to finish")
        if not cfg.backup.path:
            raise HTTPException(400, "No backup folder set")
        return BK.eject(cfg.backup.path)

    @app.get("/api/backup/database.db")
    def download_database():
        """A consistent copy of the log database (labels, training data, settings; no recordings)."""
        import sqlite3
        from starlette.background import BackgroundTask
        tmp = cfg.data_path / f".download-{secrets.token_hex(4)}.db"
        src, dst = sqlite3.connect(str(cfg.db_path), timeout=30), sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close(); src.close()
        name = f"barktracker-{datetime.now().strftime('%Y%m%d-%H%M')}.db"
        return FileResponse(tmp, media_type="application/vnd.sqlite3", filename=name,
                            background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))

    @app.post("/api/backup/inspect")
    def inspect_backup(body: dict = Body(...)):
        try:
            return BK.inspect((body.get("path") or "").strip())
        except BK.BackupError as e:
            raise HTTPException(400, str(e))

    def _staged(fn):
        def go():
            try:
                fn()
                bark.restart()
            except Exception as e:  # noqa: BLE001
                bark.backup.restore_progress = {"error": str(e)}
        return go

    @app.post("/api/backup/restore")
    def restore_backup(body: dict = Body(...)):
        path = (body.get("path") or "").strip()
        try:
            info = BK.inspect(path)          # fail fast on a wrong folder
        except BK.BackupError as e:
            raise HTTPException(400, str(e))
        if body.get("confirm") != "RESTORE":
            raise HTTPException(400, "Type RESTORE to confirm")
        bark.backup.restore_progress = {"step": "starting", "done": 0, "total": 0}
        threading.Thread(target=_staged(lambda: bark.backup.stage_restore(
            info["root"], body.get("snapshot") or None, bool(body.get("include_clips", True)))),
            daemon=True).start()
        time.sleep(0.2)
        return backup_status()

    @app.put("/api/backup/restore-upload")
    async def restore_upload(request: Request, confirm: str = ""):
        if confirm != "RESTORE":
            raise HTTPException(400, "Type RESTORE to confirm")
        data = await request.body()
        if len(data) < 100 or not data.startswith(b"SQLite format 3"):
            raise HTTPException(400, "That isn't a Barktracker database file (.db)")
        try:
            out = bark.backup.stage_upload(data)
        except BK.BackupError as e:
            raise HTTPException(400, str(e))
        out["restarting"] = bark.restart()
        return out

    # ---- tuning: live decisions, "I hear barking" captures, suggestions -----------------
    from . import tuning as T

    def what_if(trigger_db: float | None, min_level: float | None, threshold: float | None) -> dict:
        return T.settings_from(cfg, trigger_db, min_level, threshold)

    @app.get("/api/tuning/feed")
    def tuning_feed(since_idx: int = -1, since_event: int = 0):
        det = bark.detector
        if det is None:
            return {"listening": False, "settings": T.current_settings(cfg)}
        f = det.tune_feed(since_idx, since_event)
        f["cols"] = base64.b64encode(f["cols"]).decode()
        return {"listening": True, **f, "rows": F.SPEC_ROWS,
                "pending_review": store.q1("SELECT COUNT(*) n FROM missed WHERE status='new'")["n"]}

    @app.post("/api/missed")
    def capture_missed(body: dict = Body(default={})):
        seconds = float(body.get("seconds") or 30)
        if not 5 <= seconds <= 60:
            raise HTTPException(400, "Choose between 5 and 60 seconds")
        try:
            mid = bark.capture_missed(seconds, str(body.get("note") or "")[:500])
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"id": mid}

    @app.get("/api/missed")
    def list_missed(status: str | None = None):
        sql, args = "SELECT * FROM missed", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        rows = store.q(sql + " ORDER BY created DESC LIMIT 200", args)
        s = T.current_settings(cfg)
        return {"items": [{k: v for k, v in T.row_view(r, s).items() if k != "candidates"} for r in rows],
                "counts": {r["status"]: r["n"] for r in store.q("SELECT status, COUNT(*) n FROM missed GROUP BY status")}}

    def get_missed(mid: int) -> dict:
        r = store.q1("SELECT * FROM missed WHERE id=?", (mid,))
        if not r:
            raise HTTPException(404, "capture not found")
        return r

    @app.get("/api/missed/{mid}")
    def missed_detail(mid: int, trigger_db: float | None = None, min_level: float | None = None,
                      threshold: float | None = None):
        r = get_missed(mid)
        s = what_if(trigger_db, min_level, threshold)
        out = T.row_view(r, s)
        out["trace"] = json.loads(r["trace"] or "[]")
        out["settings"], out["current"] = s, T.current_settings(cfg)
        nxt = store.q1("SELECT id FROM missed WHERE status='new' AND id!=? ORDER BY created DESC LIMIT 1", (mid,))
        out["next_new"] = nxt["id"] if nxt else None
        return out

    @app.get("/api/missed/{mid}/audio")
    def missed_audio(mid: int):
        r = get_missed(mid)
        if not r["clip_path"] or not Path(r["clip_path"]).exists():
            raise HTTPException(404, "audio not available")
        return FileResponse(r["clip_path"], media_type="audio/wav", content_disposition_type="inline")

    @app.get("/api/missed/{mid}/spectrogram")
    def missed_spec(mid: int, width: int = 900, height: int = 80):
        r = get_missed(mid)
        if not r["clip_path"] or not Path(r["clip_path"]).exists():
            raise HTTPException(404, "audio not available")
        x, sr = read_wav(r["clip_path"])
        x = F.resample(x, sr)
        img, secs = F.spectrogram_image(x, width=max(100, min(width, 2000)), height=max(32, min(height, 160)))
        return {"width": img.shape[1], "height": img.shape[0], "seconds": secs,
                "data": base64.b64encode(img[::-1].tobytes()).decode()}

    @app.post("/api/missed/{mid}/add")
    def add_missed(mid: int, body: dict = Body(...)):
        get_missed(mid)
        dog_id = body.get("dog_id")
        _check_dog(dog_id)
        try:
            eid = bark.add_missed(mid, body.get("candidates") or [], dog_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"episode_id": eid}

    @app.post("/api/missed/{mid}/dismiss")
    def dismiss_missed(mid: int):
        get_missed(mid)
        try:
            bark.dismiss_missed(mid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.delete("/api/missed/{mid}")
    def delete_missed(mid: int):
        r = get_missed(mid)
        if r["status"] != "added" and r["clip_path"]:
            Path(r["clip_path"]).unlink(missing_ok=True)
        with store.tx() as c:
            c.execute("DELETE FROM missed WHERE id=?", (mid,))
        return {"ok": True}

    # ---- EXPERIMENTAL: dual-microphone direction test ---------------------------------
    @app.get("/api/array/feed")
    def array_feed(since: int = 0, scans_since: float = 0.0):
        arr = bark.array
        arr.watch()                          # someone's looking: scan the input live for a few seconds
        evs = [dict(e) for e in list(arr.events) if e["n"] > since]
        # the dog each bark belongs to now (your labels, or the latest match), not just the live guess
        if evs:
            rows = store.q("SELECT ts, NULLIF(COALESCE(label_dog_id, pred_dog_id), -1) d FROM barks"
                           " WHERE ts BETWEEN ? AND ?", (evs[0]["ts"] - 1, evs[-1]["ts"] + 1))
            for e in evs:
                near = min(rows, key=lambda r: abs(r["ts"] - e["ts"]), default=None)
                if near is not None and abs(near["ts"] - e["ts"]) <= 0.3 and near["d"] not in (None, 0):
                    e["dog"] = near["d"]
        return {"status": arr.status(), "events": evs,
                "scans": [x for x in list(arr.scans) if x[0] > scans_since],
                "devices": None if since else _input_names()}

    def _input_names():
        try:
            from .audio import list_inputs
            info = list_inputs()
            return [d["short"] for d in info["devices"]] + list(info["unlisted"])
        except Exception:  # noqa: BLE001
            return []

    @app.post("/api/array/calibrate")
    def array_calibrate(body: dict = Body(default={})):
        arr = bark.array
        if not cfg.direction.enabled:
            raise HTTPException(400, "Choose Stereo or Two microphones under Settings > Microphone first")
        if body.get("clear"):
            bark.update_settings({"direction.offset_ms": 0})
            arr.calibrating = False
        else:
            arr.calibrating = True
        return arr.status()

    @app.get("/api/tuning/suggest")
    def tuning_suggest():
        return T.suggest(store, cfg)

    # ---- heartbeat: live state + idle/bark pictures -------------------------------
    livemode = LiveMode(cfg, store)

    @app.get("/api/live")
    def live():
        return livemode.snapshot(bark.detector, bark.error)

    @app.get("/api/audio/devices")
    def audio_devices():
        from .audio import list_inputs, short_name
        info = list_inputs()
        current = getattr(bark.source, "device_name", None)
        for d in info["devices"]:
            d["active"] = bool(current) and short_name(d["name"]) == short_name(current)
        return {**info, "current": current, "configured": cfg.audio.device,
                "listening": bark.detector is not None}

    @app.get("/api/live/stream")
    async def live_stream(request: Request, count: int = 0):
        """Server-sent events with the picture mode, bark state and EQ bands.
        6 updates a second while it's quiet, 20 a second while a bark is detected. The EQ sent is
        the loudest seen since the last update, so slow updates don't miss short sounds.
        count > 0 ends the stream after that many events (for scripts and tests)."""
        async def gen():
            yield "retry: 2000\n\n"
            sent, last_send, peak = 0, 0.0, None
            while not await request.is_disconnected() and (count <= 0 or sent < count):
                det = bark.detector
                if det is not None:
                    peak = det.eq.copy() if peak is None else np.maximum(peak, det.eq)
                barking = det is not None and livemode.is_barking(det)
                now = time.monotonic()
                if now - last_send >= (LIVE_FAST if barking else LIVE_SLOW) or sent == 0:
                    snap = livemode.snapshot(det, bark.error)
                    if peak is not None and snap["bands"]:
                        snap["bands"] = [round(float(v), 2) for v in peak]
                    snap["interval"] = LIVE_FAST if snap["mode"] == "bark" else LIVE_SLOW
                    yield f"data: {json.dumps(snap, separators=(',', ':'))}\n\n"
                    sent, last_send, peak = sent + 1, now, None
                await asyncio.sleep(0.05)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def media_dir(kind: str) -> Path:
        if kind not in MEDIA_KINDS:
            raise HTTPException(404)
        return cfg.media_path / kind

    def list_media(kind: str) -> list[dict]:
        def own_files(k):
            d = cfg.media_path / k
            return [p for p in sorted(d.glob("*")) if p.suffix.lower() in MEDIA_TYPES and p.is_file()] if d.exists() else []
        own = own_files(kind) + (own_files("idle") if kind == "quiet" else [])   # older installs used idle/
        if own:
            return [{"url": f"/media/{p.parent.name}/{p.name}", "name": p.name, "own": True, "folder": p.parent.name,
                     "video": p.suffix.lower() in VIDEO} for p in own]
        builtin = sorted((STATIC / "media" / kind).glob("*"))
        return [{"url": f"/static/media/{kind}/{p.name}", "name": p.name, "own": False, "folder": kind,
                 "video": p.suffix.lower() in VIDEO} for p in builtin if p.suffix.lower() in MEDIA_TYPES]

    @app.get("/api/media")
    def media():
        return {**{k: list_media(k) for k in ("quiet", "active", "bark")}, "folder": str(cfg.media_path)}

    @app.get("/media/{kind}/{name}")
    def media_file(kind: str, name: str):
        base = (cfg.media_path / "idle") if kind == "idle" else media_dir(kind)
        p = (base / name).resolve()
        if p.parent != base.resolve() or not p.is_file() or p.suffix.lower() not in MEDIA_TYPES:
            raise HTTPException(404)
        return FileResponse(p, media_type=MEDIA_TYPES[p.suffix.lower()])

    @app.put("/api/media/{kind}/{name}")
    async def upload_media(kind: str, name: str, request: Request):
        safe = "".join(c for c in Path(name).name if c.isalnum() or c in "._- ").strip()
        if not safe or Path(safe).suffix.lower() not in MEDIA_TYPES:
            raise HTTPException(400, "Use a JPG, PNG, GIF, WebP, SVG, MP4 or WebM file")
        body = await request.body()
        if len(body) > 40 * 2**20:
            raise HTTPException(413, "Files must be under 40 MB")
        d = media_dir(kind)
        d.mkdir(parents=True, exist_ok=True)
        (d / safe).write_bytes(body)
        return media()

    @app.delete("/api/media/{kind}/{name}")
    def delete_media(kind: str, name: str):
        base = (cfg.media_path / "idle") if kind == "idle" else media_dir(kind)
        p = (base / name).resolve()
        if p.parent != base.resolve() or not p.is_file():
            raise HTTPException(404)
        p.unlink()
        return media()

    # ---- ordinance & complaint reports ----------------------------------------
    @app.get("/api/ordinance")
    def ordinance():
        o = O.get_ordinance(store)
        return {"ordinance": o, "criteria": {**O.describe_thresholds(o["thresholds"]), **O.describe_rules(o)},
                "rule_types": O.RULE_TYPES, "labels": O.rule_labels(o),
                "presets": [{"id": k, "label": v} for k, v in O.PRESET_LABELS.items()],
                "reporter": O.get_reporter(store)}

    @app.put("/api/ordinance")
    def put_ordinance(body: dict = Body(...)):
        try:
            O.save_ordinance(store, body)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, f"Invalid value: {e}")
        return ordinance()

    @app.put("/api/reporter")
    def put_reporter(body: dict = Body(...)):
        return O.save_reporter(store, body)

    @app.get("/api/subjects")
    def subjects():
        return O.subject_options(store)

    def _report(dog, address, d_from, d_to, confirmed, flagged):
        d1 = date.fromisoformat(d_to) if d_to else today()
        d0 = date.fromisoformat(d_from) if d_from else d1 - timedelta(days=29)
        if d0 > d1:
            raise HTTPException(400, "The start date is after the end date")
        if (d1 - d0).days > 366:
            raise HTTPException(400, "Reports can cover at most one year")
        return O.build_report(store, cfg.timezone, d0, d1, dog_id=dog, address=address,
                              confirmed_only=confirmed, flagged_only=flagged)

    @app.get("/api/report")
    def report(dog: int | None = None, address: str | None = None,
               d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
               confirmed: bool = False, flagged: bool = False):
        return _report(dog, address, d_from, d_to, confirmed, flagged)

    def _slug(rep):
        t = rep["subject"]["title"] if rep["subject"]["kind"] != "all" else "all-dogs"
        t = "".join(c if c.isalnum() else "-" for c in t.lower()).strip("-")
        while "--" in t:
            t = t.replace("--", "-")
        return f"barktracker-{t}-{rep['period']['from']}-to-{rep['period']['to']}"

    @app.get("/api/report.csv")
    def report_csv(dog: int | None = None, address: str | None = None,
                   d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
                   confirmed: bool = False, flagged: bool = False, kind: str = "incidents"):
        rep = _report(dog, address, d_from, d_to, confirmed, flagged)
        body = render_csv(rep, kind)
        name = f"{_slug(rep)}-{'daily' if kind == 'daily' else 'incidents'}.csv"
        return Response(body.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def _bark_rows(rep) -> str:
        """Every bark in the report's incidents: when, which dog, how it was decided, direction."""
        ids = [eid for i in rep["incidents"] for eid in i["episode_ids"]]
        names = {d["id"]: d["name"] for d in store.dogs()}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Incident", "Clip", f"Time ({rep['timezone']})", "Seconds into clip", "Dog", "Identified",
                    "Match confidence", "Bark confidence", "Loudness (dBFS)", "Direction (deg)"])
        inc_of = {eid: i["number"] for i in rep["incidents"] for eid in i["episode_ids"]}
        if ids:
            rows = store.q("SELECT b.*, e.clip_start_ts, e.start_ts est, e.clip_path FROM barks b JOIN episodes e"
                           f" ON e.id=b.episode_id WHERE b.episode_id IN ({','.join('?' * len(ids))}) ORDER BY b.ts", ids)
            from .array import nearest_dirs
            dirs = nearest_dirs(store, [r["ts"] for r in rows])
            for r in rows:
                d = r["label_dog_id"] if r["label_dog_id"] is not None else r["pred_dog_id"]
                if d == NOT_BARK:
                    continue
                who = "Unknown dog" if d == -1 else names.get(d, "Unidentified") if d is not None else "Unidentified"
                how = "Confirmed" if r["label_dog_id"] is not None else "Automatic" if r["pred_dog_id"] is not None else ""
                dr = dirs.get(r["ts"])
                w.writerow([inc_of.get(r["episode_id"], ""), Path(r["clip_path"]).name if r["clip_path"] else "",
                            to_dt(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"),
                            round(r["ts"] - (r["clip_start_ts"] or r["est"]), 2), who, how,
                            f"{round(r['pred_conf'] * 100)}%" if r["pred_conf"] is not None and r["label_dog_id"] is None else "",
                            f"{round(r['prob'] * 100)}%", round(r["peak_db"]) if r["peak_db"] is not None else "",
                            round(dr["bearing"]) if dr and dr["valid"] else ""])
        return buf.getvalue()

    @app.get("/api/report/barks.csv")
    def report_barks(dog: int | None = None, address: str | None = None,
                     d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
                     confirmed: bool = False, flagged: bool = False):
        rep = _report(dog, address, d_from, d_to, confirmed, flagged)
        return Response(_bark_rows(rep).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{_slug(rep)}-barks.csv"'})

    def _ordinance_txt(rep) -> str:
        o, rp = rep["ordinance"], rep["reporter"]
        lines = [f"{o['code_section']}", o["jurisdiction"], "", o["rule_text"], ""]
        if o.get("rule_note"):
            lines += [o["rule_note"], ""]
        lines += ["Rules used in this report:"] + [f"- {rep['flag_labels'].get(k, k)}: {v}" for k, v in rep["criteria"].items()]
        if o.get("source_url"):
            lines += ["", f"Source: {o['source_url']}"]
        if o.get("contacts"):
            lines += ["", "Where to report:"] + [f"- {c['name']}: {c['phone']}" for c in o["contacts"]]
        if rp.get("name"):
            lines += ["", "Prepared by: " + ", ".join(x for x in (rp["name"], rp["address"], rp["phone"], rp["email"]) if x)]
        sm = rep["summary"]
        lines += ["", f"Period: {rep['period']['from']} to {rep['period']['to']} ({rep['timezone']})",
                  f"Incidents: {sm['incidents']}, total barking {O.fmt_dur(sm['total_seconds'])}, "
                  f"{sm['days_with_barking']} of {sm['days_in_period']} days"]
        return "\n".join(lines) + "\n"

    def _zip(rep, what: str) -> Response:
        """what: clips (incident clips + the incident and daily tables, as before), audio (just the
        recordings), or all (recordings, every table, the rule text)."""
        slug = _slug(rep)
        buf = io.BytesIO()
        ids = [eid for i in rep["incidents"] for eid in i["episode_ids"]]
        paths = {r["id"]: r["clip_path"] for r in store.q(
            f"SELECT id, clip_path FROM episodes WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            if what in ("clips", "all"):
                z.writestr(f"{slug}/incidents.csv", render_csv(rep, "incidents").encode("utf-8-sig"))
                z.writestr(f"{slug}/daily.csv", render_csv(rep, "daily").encode("utf-8-sig"))
            if what == "all":
                z.writestr(f"{slug}/barks.csv", _bark_rows(rep).encode("utf-8-sig"))
                z.writestr(f"{slug}/ordinance-and-summary.txt", _ordinance_txt(rep))
                z.writestr(f"{slug}/report.json", json.dumps(rep, indent=1, default=str))
            for inc in rep["incidents"]:
                for eid in inc["episode_ids"]:
                    p = paths.get(eid)
                    if p and Path(p).exists():
                        z.write(p, f"{slug}/{'clips/' if what != 'audio' else ''}incident-{inc['number']:03d}_{Path(p).name}",
                                compress_type=zipfile.ZIP_STORED)        # WAV barely compresses; don't make the Pi try
        suffix = {"clips": "", "audio": "-audio", "all": "-everything"}[what]
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{slug}{suffix}.zip"'})

    @app.get("/api/report/clips.zip")
    def report_zip(dog: int | None = None, address: str | None = None,
                   d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
                   confirmed: bool = False, flagged: bool = False):
        return _zip(_report(dog, address, d_from, d_to, confirmed, flagged), "clips")

    @app.get("/api/report/audio.zip")
    def report_audio(dog: int | None = None, address: str | None = None,
                     d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
                     confirmed: bool = False, flagged: bool = False):
        return _zip(_report(dog, address, d_from, d_to, confirmed, flagged), "audio")

    @app.get("/api/report/everything.zip")
    def report_all(dog: int | None = None, address: str | None = None,
                   d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
                   confirmed: bool = False, flagged: bool = False):
        return _zip(_report(dog, address, d_from, d_to, confirmed, flagged), "all")

    return app


def render_csv(rep: dict, kind: str = "incidents") -> str:
    """One clean table, header row first, no preamble, so it opens directly in Excel/Sheets."""
    tz = rep["timezone"]
    labels = rep["flag_labels"]
    buf = io.StringIO()
    w = csv.writer(buf)
    subject = rep["subject"]["kind"] != "all"
    if kind == "daily":
        w.writerow(["Date", "Day", "Incidents", "Total barking (m:ss)", "Total barking (seconds)",
                    "Barks", "Minutes during quiet hours", "Meets criteria"])
        for d in rep["days"]:
            dt = date.fromisoformat(d["date"])
            flags = list(d["flags"]) + (["Incident criteria"] if d["flagged_incidents"] else [])
            w.writerow([d["date"], dt.strftime("%a"), d["incidents"], O.fmt_dur(d["seconds"]),
                        round(d["seconds"]), d["barks"], round(d["quiet_seconds"] / 60, 1),
                        "; ".join(labels.get(f, f) for f in flags)])
        return buf.getvalue()
    head = ["Incident", "Date", "Day", f"Start ({tz})", f"End ({tz})", "Duration (m:ss)",
            "Duration (seconds)", "Total barks"]
    if subject:
        head += ["Barks by " + rep["subject"]["title"], "Identification"]
    head += ["Dogs heard", "Minutes during quiet hours", "Incident meets", "Day meets", "Clip files", "Notes"]
    w.writerow(head)
    for i in rep["incidents"]:
        row = [i["number"], i["date"], i["weekday"], i["start"], i["end"], O.fmt_dur(i["duration"]),
               round(i["duration"]), i["barks"]]
        if subject:
            row += [i["subject_barks"], {"confirmed": "Confirmed by reviewer", "automatic": "Automatic match",
                                         "mixed": "Partly confirmed"}.get(i["identified"], "")]
        row += ["; ".join(f"{k} x{v}" for k, v in i["dogs"].items()), round(i["quiet_seconds"] / 60, 1),
                "; ".join(labels.get(f, f) for f in i["flags"]), "; ".join(labels.get(f, f) for f in i["day_flags"]),
                "; ".join(i["clips"]), i["notes"]]
        w.writerow(row)
    return buf.getvalue()


def clock_status() -> dict:
    """Is the system clock NTP-synchronised? Matters for the credibility of timestamps."""
    import subprocess
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        return {"ntp_synced": out == "yes" if out else None}
    except (OSError, subprocess.SubprocessError):
        return {"ntp_synced": None}
