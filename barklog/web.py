"""FastAPI dashboard + JSON API."""
from __future__ import annotations

import base64
import csv
import io
import secrets
import shutil
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import features as F
from .app import BarkApp
from .audio import read_wav
from .storage import NOT_BARK

STATIC = Path(__file__).parent / "static"


class DogIn(BaseModel):
    name: str | None = None
    color: str | None = None
    notes: str | None = None


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

    app = FastAPI(title="Barklog", dependencies=[Depends(auth)], docs_url="/api/docs")
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
        }

    # ---- stats ----------------------------------------------------------------
    @app.get("/api/summary")
    def summary(days: int = 14):
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
            b["episodes"] += 1
            b["barks"] += e["bark_count"]
            b["seconds"] += dur
            b["longest"] = max(b["longest"], dur)
            s = split.get(e["id"], {})
            tot = sum(s.values())
            for k, n in s.items():
                b["by_dog"][k] += dur * n / tot
            if not tot:
                b["by_dog"]["u"] += dur
        out = [{"date": d.isoformat(), **{k: (round(v, 1) if isinstance(v, float) else v)
                                          for k, v in b.items() if k != "by_dog"},
                "by_dog": {k: round(v, 1) for k, v in b["by_dog"].items()}}
               for d, b in sorted(buckets.items())]
        return {"days": out, "dogs": store.dogs()}

    @app.get("/api/day")
    def day(ds: str | None = Query(None, alias="date")):
        d = date.fromisoformat(ds) if ds else today()
        t0, t1 = day_start(d), day_start(d + timedelta(days=1))
        eps = store.episodes_between(t0, t1)
        split = dog_split(t0, t1)
        barks = store.q("SELECT b.ts, COALESCE(b.label_dog_id, b.pred_dog_id) dog_id FROM barks b"
                        " JOIN episodes e ON e.id=b.episode_id WHERE b.ts>=? AND b.ts<? AND"
                        " e.false_positive=0 AND COALESCE(b.label_dog_id, b.pred_dog_id, -1) != 0",
                        (t0, t1))
        return {"date": d.isoformat(), "start_ts": t0, "end_ts": t1,
                "episodes": [ep_view(e, split.get(e["id"], {})) for e in eps],
                "barks": [[round(b["ts"], 2), b["dog_id"]] for b in barks]}

    @app.get("/api/heatmap")
    def heatmap(days: int = 28):
        d_end = today()
        d0 = d_end - timedelta(days=days - 1)
        t0, t1 = day_start(d0), day_start(d_end + timedelta(days=1))
        grid = [[0.0] * 24 for _ in range(7)]   # [weekday Mon=0][hour] seconds
        for e in store.episodes_between(t0, t1):
            s, end = e["start_ts"], max(e["end_ts"], e["start_ts"] + 0.5)
            while s < end:
                dt = to_dt(s)
                nxt = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
                seg = min(end, nxt) - s
                grid[dt.weekday()][dt.hour] += seg
                s = min(end, nxt)
        return {"days": days, "grid": [[round(v, 1) for v in row] for row in grid]}

    # ---- episodes -------------------------------------------------------------
    @app.get("/api/episodes")
    def episodes(limit: int = 50, offset: int = 0, dog: str | None = None,
                 unreviewed: bool = False, include_fp: bool = False, min_duration: float = 0.0,
                 date_from: str | None = None, date_to: str | None = None):
        where, args = ["1=1"], []
        if not include_fp:
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
            for r in store.q("SELECT COALESCE(label_dog_id, pred_dog_id) d, COUNT(*) n FROM barks"
                             " WHERE episode_id=? GROUP BY d", (e["id"],)):
                if r["d"] == NOT_BARK:
                    continue
                k = "u" if r["d"] is None else str(r["d"])
                s[k] = r["n"]
            out.append(ep_view(e, s))
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
        return {**ep_view(e, s), "barks": barks,
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
    def delete_episode(eid: int):
        get_ep(eid)
        path = store.delete_episode(eid)
        if path:
            Path(path).unlink(missing_ok=True)
        bark.retrain()
        return {"ok": True}

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
        nb = store.q1("SELECT COUNT(*) n FROM barks WHERE label_dog_id=0")["n"]
        return {"dogs": rows, "not_bark_examples": nb, "min_labels": cfg.identify.min_labels_per_dog}

    @app.post("/api/dogs")
    def add_dog(body: DogIn):
        try:
            return store.add_dog(body.name or "", body.color, body.notes or "")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))

    @app.patch("/api/dogs/{dog_id}")
    def edit_dog(dog_id: int, body: DogIn):
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
                        headers={"Content-Disposition": "attachment; filename=barklog.csv"})

    return app
