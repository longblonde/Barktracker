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
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import backup as BK
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
        barks = store.q("SELECT b.ts, COALESCE(b.label_dog_id, b.pred_dog_id) dog_id FROM barks b"
                        " JOIN episodes e ON e.id=b.episode_id WHERE b.ts>=? AND b.ts<? AND"
                        " e.false_positive=0 AND COALESCE(b.label_dog_id, b.pred_dog_id, -1) != 0",
                        (t0, t1))
        if sel is not None:
            barks = [b for b in barks if ("u" if b["dog_id"] is None else str(b["dog_id"])) in sel]
        th = O.get_ordinance(store)["thresholds"]
        incs = O.build_incidents([{**e, "subject_barks": e["bark_count"]} for e in eps], th, O.Clock(cfg.timezone))
        return {"date": d.isoformat(), "start_ts": t0, "end_ts": t1,
                "incidents": [{"start_ts": i["start_ts"], "end_ts": i["end_ts"], "flags": i["flags"],
                               "duration": i["duration"]} for i in incs],
                "episodes": [{**ep_view(e, split.get(e["id"], {})), "share": round(share(split.get(e["id"], {}), sel), 3)}
                             for e in eps],
                "dogs_filter": sorted(sel) if sel else None,
                "barks": [[round(b["ts"], 2), b["dog_id"]] for b in barks]}

    @app.get("/api/heatmap")
    def heatmap(days: int = 28, dogs: str | None = None):
        sel = parse_sel(dogs)
        d_end = today()
        d0 = d_end - timedelta(days=days - 1)
        t0, t1 = day_start(d0), day_start(d_end + timedelta(days=1))
        split = dog_split(t0, t1) if sel else {}
        grid = [[0.0] * 24 for _ in range(7)]   # [weekday Mon=0][hour] seconds
        for e in store.episodes_between(t0, t1):
            f = share(split.get(e["id"], {}), sel)
            if f <= 0:
                continue
            s, end = e["start_ts"], max(e["end_ts"], e["start_ts"] + 0.5)
            while s < end:
                dt = to_dt(s)
                nxt = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
                seg = min(end, nxt) - s
                grid[dt.weekday()][dt.hour] += seg * f
                s = min(end, nxt)
        return {"days": days, "grid": [[round(v, 1) for v in row] for row in grid]}

    # ---- episodes -------------------------------------------------------------
    @app.get("/api/episodes")
    def episodes(limit: int = 50, offset: int = 0, dog: str | None = None,
                 unreviewed: bool = False, include_fp: bool = False, min_duration: float = 0.0,
                 date_from: str | None = None, date_to: str | None = None, kind: str = "barking",
                 dogs: str | None = None, review: str | None = None):
        where, args = ["1=1"], []
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
                conds.append("COALESCE(b.label_dog_id, b.pred_dog_id) IN (%s)" % ",".join("?" * len(ids)))
                args.extend(ids)
            if "u" in sel:
                conds.append("COALESCE(b.label_dog_id, b.pred_dog_id) IS NULL")
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
                             " COALESCE(b.label_dog_id, b.pred_dog_id) IS NULL)")
            else:
                where.append("EXISTS (SELECT 1 FROM barks b WHERE b.episode_id=e.id AND"
                             " COALESCE(b.label_dog_id, b.pred_dog_id) = ?)")
                args.append(int(dog))
        w = " AND ".join(where)
        total = store.q1(f"SELECT COUNT(*) n FROM episodes e WHERE {w}", args)["n"]
        rows = store.q(f"SELECT e.* FROM episodes e WHERE {w} ORDER BY e.start_ts DESC LIMIT ? OFFSET ?",
                       (*args, min(limit, 500), offset))
        out = []
        for e in rows:
            s = {}
            n = lab = 0
            for r in store.q("SELECT COALESCE(label_dog_id, pred_dog_id) d, COUNT(*) n,"
                             " SUM(label_dog_id IS NOT NULL) lab FROM barks WHERE episode_id=? GROUP BY d", (e["id"],)):
                n += r["n"]
                lab += r["lab"] or 0
                if r["d"] == NOT_BARK:
                    continue
                k = "u" if r["d"] is None else str(r["d"])
                s[k] = r["n"]
            out.append({**ep_view(e, s), "marked": marking(n, lab, bool(e["reviewed"])), "labelled": lab})
        return {"total": total, "items": out}

    @app.get("/api/episodes/{eid}")
    def episode(eid: int):
        e = get_ep(eid)
        barks = store.barks_for(eid)
        cs = e["clip_start_ts"] or e["start_ts"]
        for b in barks:
            b["offset"] = round(b["ts"] - cs, 3)
        s = {}
        for b in barks:
            if b["dog_id"] == NOT_BARK:
                continue
            k = "u" if b["dog_id"] is None else str(b["dog_id"])
            s[k] = s.get(k, 0) + 1
        prev = store.q1("SELECT id FROM episodes WHERE start_ts < ? AND false_positive=0"
                        " ORDER BY start_ts DESC LIMIT 1", (e["start_ts"],))
        nxt = store.q1("SELECT id FROM episodes WHERE start_ts > ? AND false_positive=0"
                       " ORDER BY start_ts LIMIT 1", (e["start_ts"],))
        lab = sum(1 for b in barks if b["label_dog_id"] is not None)
        return {**ep_view(e, s), "marked": marking(len(barks), lab, bool(e["reviewed"])), "labelled": lab,
                "barks": barks, "sound": V.episode_sound(store, eid),
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
        if dog_id not in (None, NOT_BARK) and not store.q1("SELECT 1 FROM dogs WHERE id=?", (dog_id,)):
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
        for d in rows:
            d.update(voices.get(d["id"], {}))
        nb = store.q1("SELECT COUNT(*) n FROM barks WHERE label_dog_id=0")["n"]
        return {"dogs": rows, "not_bark_examples": nb, "min_labels": cfg.identify.min_labels_per_dog}

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
        return {"ordinance": o, "criteria": O.describe_thresholds(o["thresholds"]),
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

    @app.get("/api/report/clips.zip")
    def report_zip(dog: int | None = None, address: str | None = None,
                   d_from: str | None = Query(None, alias="from"), d_to: str | None = Query(None, alias="to"),
                   confirmed: bool = False, flagged: bool = False):
        rep = _report(dog, address, d_from, d_to, confirmed, flagged)
        slug = _slug(rep)
        buf = io.BytesIO()
        ids = [eid for i in rep["incidents"] for eid in i["episode_ids"]]
        paths = {r["id"]: r["clip_path"] for r in store.q(
            f"SELECT id, clip_path FROM episodes WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{slug}/incidents.csv", render_csv(rep, "incidents").encode("utf-8-sig"))
            z.writestr(f"{slug}/daily.csv", render_csv(rep, "daily").encode("utf-8-sig"))
            for inc in rep["incidents"]:
                for eid in inc["episode_ids"]:
                    p = paths.get(eid)
                    if p and Path(p).exists():
                        z.write(p, f"{slug}/clips/incident-{inc['number']:03d}_{Path(p).name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})

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
