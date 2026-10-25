import csv
import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fastapi.testclient import TestClient

from barktracker import ordinance as O
from barktracker.app import BarkApp
from barktracker.config import Config
from barktracker.storage import Store
from barktracker.web import create_app, render_csv

TZ = "America/Chicago"
CT = ZoneInfo(TZ)


def ts(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=CT).timestamp()


def add_episode(store, start, dur, barks, dog=None, label=True):
    eid = store.create_episode(start, None, start - 2)
    feats = np.zeros(10, np.float32)
    for k in range(barks):
        b = store.add_bark(eid, start + k * dur / max(barks, 1), 0.3, 0.9, -20, feats,
                           None if label else dog, 0.9 if not label else None)
        if label and dog is not None:
            store.label_barks([b], dog)
    store.finish_episode(eid, start + dur, barks, -20, 0.9, dur + 4)
    return eid


def test_defaults_are_burnsville():
    o = O.default_ordinance()
    assert "6-2-7" in o["code_section"]
    assert "Burnsville" in o["jurisdiction"]
    assert any("952-322-2323" in c["phone"] for c in o["contacts"])
    assert o["thresholds"]["quiet_start"] == "22:00"


def test_quiet_overlap_crosses_midnight():
    th = O.default_ordinance()["thresholds"]
    clock = O.Clock(TZ)
    # 21:50 -> 22:20 : 20 minutes inside
    assert O.quiet_overlap(ts(2026, 10, 20, 21, 50), ts(2026, 10, 20, 22, 20), th, clock) == pytest.approx(1200)
    # 06:30 -> 07:30 next morning: 30 minutes inside
    assert O.quiet_overlap(ts(2026, 10, 21, 6, 30), ts(2026, 10, 21, 7, 30), th, clock) == pytest.approx(1800)
    # midday: none
    assert O.quiet_overlap(ts(2026, 10, 21, 12), ts(2026, 10, 21, 13), th, clock) == 0


def test_incidents_merge_short_pauses_and_flag():
    th = {**O.default_ordinance()["thresholds"], "sustained_minutes": 10, "max_pause_seconds": 60}
    clock = O.Clock(TZ)
    base = ts(2026, 10, 20, 14)
    eps = [{"id": i, "start_ts": base + i * 200, "end_ts": base + i * 200 + 170, "bark_count": 20,
            "subject_barks": 20} for i in range(4)]          # 30 s gaps -> one incident ~13 min
    eps.append({"id": 9, "start_ts": base + 3600, "end_ts": base + 3660, "bark_count": 5, "subject_barks": 5})
    incs = O.build_incidents(eps, th, clock)
    assert len(incs) == 2
    assert incs[0]["duration"] == pytest.approx(770)
    assert incs[0]["flags"] == ["sustained"]
    assert incs[1]["flags"] == []


def test_report_by_dog_and_neighbor(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    rex = store.add_dog("Rex", owner="J. Smith", address="123 Elm St", description="Black lab")["id"]
    bo = store.add_dog("Bo", owner="J. Smith", address="123 Elm St")["id"]
    other = store.add_dog("Fifi", address="9 Oak Ave")["id"]
    # Rex: 12-minute night incident on the 20th, 40 min daytime on the 21st, 11 min on the 22nd
    add_episode(store, ts(2026, 10, 20, 23, 0), 720, 60, rex)
    add_episode(store, ts(2026, 10, 21, 13, 0), 2400, 200, rex)
    add_episode(store, ts(2026, 10, 22, 9, 0), 660, 40, rex)
    add_episode(store, ts(2026, 10, 22, 15, 0), 60, 8, bo, label=False)     # automatic match only
    add_episode(store, ts(2026, 10, 22, 16, 0), 60, 8, other)

    rep = O.build_report(store, TZ, date(2026, 10, 19), date(2026, 10, 25), dog_id=rex)
    assert rep["subject"]["owner"] == "J. Smith"
    assert rep["summary"]["incidents"] == 3
    first = rep["incidents"][0]
    assert "quiet_hours" in first["flags"] and "sustained" in first["flags"]
    assert first["identified"] == "confirmed"
    assert "frequent" in rep["incidents"][1]["day_flags"]
    # 3 qualifying days within 7 -> habitual on each of them (a day-level flag)
    assert all("habitual" in i["day_flags"] for i in rep["incidents"])
    assert rep["summary"]["days_flagged"] == {"frequent": 1, "habitual": 3}

    hh = O.build_report(store, TZ, date(2026, 10, 19), date(2026, 10, 25), address="123 Elm St")
    assert hh["summary"]["incidents"] == 4                       # Rex x3 + Bo
    assert {d["name"] for d in hh["subject"]["dogs"]} == {"Rex", "Bo"}
    confirmed = O.build_report(store, TZ, date(2026, 10, 19), date(2026, 10, 25),
                               address="123 Elm St", confirmed_only=True)
    assert confirmed["summary"]["incidents"] == 3                # Bo's automatic match excluded
    flagged = O.build_report(store, TZ, date(2026, 10, 19), date(2026, 10, 25),
                             address="123 Elm St", flagged_only=True)
    assert flagged["summary"]["incidents"] == 3                  # Bo's 1-minute incident meets nothing

    rows = list(csv.reader(io.StringIO(render_csv(rep))))
    assert rows[0][:4] == ["Incident", "Date", "Day", f"Start ({TZ})"]
    assert rows[1][1] == "2026-10-20" and rows[1][3] == "11:00:00 PM" and rows[1][5] == "12:00"
    assert "Quiet hours" in rows[1][rows[0].index("Incident meets")]
    assert "Habitual pattern" in rows[1][rows[0].index("Day meets")]
    daily = list(csv.reader(io.StringIO(render_csv(rep, "daily"))))
    assert len(daily) == 1 + 7


def test_ordinance_api_roundtrip(tmp_path):
    cfg = Config(data_dir=str(tmp_path), timezone=TZ)
    app = BarkApp(cfg, None, start_detector=False)
    c = TestClient(create_app(app))
    o = c.get("/api/ordinance").json()
    assert o["ordinance"]["preset"] == "burnsville_mn"
    r = c.put("/api/ordinance", json={"thresholds": {"sustained_minutes": 5, "quiet_start": "21:30"}}).json()
    assert r["ordinance"]["thresholds"]["sustained_minutes"] == 5
    assert "9:30 PM" in r["criteria"]["quiet_hours"]
    assert c.put("/api/ordinance", json={"thresholds": {"quiet_start": "nope"}}).status_code == 400
    r = c.put("/api/ordinance", json={"reset": True, "preset": "burnsville_mn"}).json()
    assert r["ordinance"]["thresholds"]["sustained_minutes"] == 10
    c.put("/api/reporter", json={"name": "Dan", "address": "1 Main"})
    rep = c.get("/api/report?from=2026-10-01&to=2026-10-07").json()
    assert rep["reporter"]["name"] == "Dan" and rep["summary"]["incidents"] == 0
    assert c.get("/api/report.csv?from=2026-10-01&to=2026-10-07").status_code == 200
    z = c.get("/api/report/clips.zip?from=2026-10-01&to=2026-10-07")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    app.shutdown()
