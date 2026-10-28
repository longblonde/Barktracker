"""Local animal-noise ordinance: settings, evaluation of logged barking against it, and
complaint-report assembly (by dog or by neighbor/household).

Most codes (Burnsville's included) define the offence qualitatively ("habitually or
frequently"), so the numeric thresholds here are *documentation criteria* the user sets
to decide which incidents to highlight. They are not a legal determination.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from .storage import NOT_BARK, Store

PRESETS: dict[str, dict] = {
    "burnsville_mn": {
        "preset": "burnsville_mn",
        "jurisdiction": "City of Burnsville, Minnesota",
        "code_section": "Burnsville City Code § 6-2-7, Obligation To Prevent Nuisances",
        "rule_text": ("No person may allow an animal they own or control to disturb the peace and quiet "
                      "of the City or become a public or private nuisance. It is a nuisance for an animal "
                      "to habitually or frequently make animal noises, including barking, crying, howling "
                      "or whimpering."),
        "rule_note": ("Burnsville sets no minimum number of minutes and no animal-specific quiet hours. "
                      "The city also weighs factors such as noise and the nature of the neighborhood "
                      "(§ 6-2-20). The 10 PM to 7 AM window below mirrors the city's quiet hours for "
                      "construction and power equipment (§ 7-1-3)."),
        "source_url": "https://burnsville.municipalcodeonline.com/book?type=ordinances#name=6-2-7_Obligation_To_Prevent_Nuisances",
        "penalty": "See Burnsville City Code Chapter 1-4 (Penalty).",
        "contacts": [
            {"name": "Burnsville Police non-emergency dispatch (noise complaints, 24/7)", "phone": "952-322-2323"},
            {"name": "Burnsville Animal Control (American Boarding Kennels)", "phone": "952-894-3647"},
            {"name": "Burnsville City Hall", "phone": "952-895-4400"},
        ],
        "thresholds": {
            "sustained_minutes": 10,     # incident at least this long counts as sustained barking
            "max_pause_seconds": 60,     # pauses up to this long don't break an incident
            "daily_minutes": 30,         # a day with at least this much barking counts as frequent
            "quiet_start": "22:00",
            "quiet_end": "07:00",
            "quiet_minutes": 5,          # incident overlapping quiet hours by this much is flagged
            "habitual_days": 3,          # this many qualifying days...
            "habitual_window_days": 7,   # ...within this many days marks a habitual pattern
        },
    },
}
PRESET_LABELS = {"burnsville_mn": "Burnsville, MN"}
DEFAULT_PRESET = "burnsville_mn"
REPORTER_FIELDS = ("name", "address", "phone", "email")


def default_ordinance() -> dict:
    return copy.deepcopy(PRESETS[DEFAULT_PRESET])


def get_ordinance(store: Store) -> dict:
    saved = store.get_meta("ordinance") or {}
    base = default_ordinance()
    th = {**base["thresholds"], **(saved.get("thresholds") or {})}
    out = {**base, **saved, "thresholds": th}
    return out


def save_ordinance(store: Store, data: dict) -> dict:
    if data.get("reset"):
        cur = copy.deepcopy(PRESETS[data.get("preset") or DEFAULT_PRESET])
        store.set_meta("ordinance", cur)
        return cur
    cur = get_ordinance(store)
    allowed = {"preset", "jurisdiction", "code_section", "rule_text", "rule_note", "source_url",
               "penalty", "contacts"}
    for k in allowed & data.keys():
        cur[k] = data[k]
    if "thresholds" in data:
        th = cur["thresholds"]
        for k, v in data["thresholds"].items():
            if k not in th:
                continue
            if k in ("quiet_start", "quiet_end"):
                _parse_hhmm(v)   # validates
                th[k] = v
            else:
                v = float(v)
                if v < 0:
                    raise ValueError(f"{k} must be zero or more")
                th[k] = int(v) if float(v).is_integer() else v
    store.set_meta("ordinance", cur)
    return cur


def get_reporter(store: Store) -> dict:
    r = store.get_meta("reporter") or {}
    return {k: r.get(k, "") for k in REPORTER_FIELDS}


def save_reporter(store: Store, data: dict) -> dict:
    r = {k: str(data.get(k, "") or "").strip() for k in REPORTER_FIELDS}
    store.set_meta("reporter", r)
    return r


def _parse_hhmm(s: str) -> dtime:
    hh, mm = str(s).split(":")
    return dtime(int(hh), int(mm))


# --------------------------------------------------------------------------- evaluation
class Clock:
    def __init__(self, tz: str | None):
        self.tz = ZoneInfo(tz) if tz else None

    def dt(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz) if self.tz else datetime.fromtimestamp(ts)

    def day_start(self, d: date) -> float:
        dt = datetime(d.year, d.month, d.day, tzinfo=self.tz) if self.tz else datetime(d.year, d.month, d.day)
        return dt.timestamp()

    def tzname(self) -> str:
        return str(self.tz) if self.tz else (datetime.now().astimezone().tzname() or "local time")


def quiet_overlap(start: float, end: float, th: dict, clock: Clock) -> float:
    """Seconds of [start, end] that fall inside quiet hours (window may cross midnight)."""
    qs, qe = _parse_hhmm(th["quiet_start"]), _parse_hhmm(th["quiet_end"])
    if qs == qe:
        return 0.0
    total, d = 0.0, clock.dt(start).date() - timedelta(days=1)
    last = clock.dt(end).date()
    while d <= last:
        a = clock.day_start(d) + qs.hour * 3600 + qs.minute * 60
        e_day = d if qe > qs else d + timedelta(days=1)
        b = clock.day_start(e_day) + qe.hour * 3600 + qe.minute * 60
        total += max(0.0, min(end, b) - max(start, a))
        d += timedelta(days=1)
    return total


def build_incidents(episodes: list[dict], th: dict, clock: Clock) -> list[dict]:
    """Merge episodes separated by short pauses into incidents and flag them."""
    pause = float(th["max_pause_seconds"])
    out: list[dict] = []
    for e in sorted(episodes, key=lambda x: x["start_ts"]):
        if out and e["start_ts"] - out[-1]["end_ts"] <= pause:
            inc = out[-1]
            inc["end_ts"] = max(inc["end_ts"], e["end_ts"])
            inc["episodes"].append(e)
        else:
            out.append({"start_ts": e["start_ts"], "end_ts": e["end_ts"], "episodes": [e]})
    for i, inc in enumerate(out, 1):
        eps = inc["episodes"]
        inc["duration"] = max(0.0, inc["end_ts"] - inc["start_ts"])
        inc["barks"] = sum(e["bark_count"] for e in eps)
        inc["subject_barks"] = sum(e.get("subject_barks", e["bark_count"]) for e in eps)
        inc["quiet_seconds"] = quiet_overlap(inc["start_ts"], inc["end_ts"], th, clock)
        flags = []
        if inc["duration"] >= th["sustained_minutes"] * 60:
            flags.append("sustained")
        if inc["quiet_seconds"] > 0 and inc["quiet_seconds"] >= th["quiet_minutes"] * 60:
            flags.append("quiet_hours")
        inc["flags"] = flags
        inc["date"] = clock.dt(inc["start_ts"]).date().isoformat()
    return out


def flag_days(incidents: list[dict], th: dict, d0: date, d1: date) -> dict[str, dict]:
    """Per-day totals plus 'frequent' and 'habitual' flags."""
    days: dict[str, dict] = {}
    d = d0
    while d <= d1:
        days[d.isoformat()] = {"date": d.isoformat(), "incidents": 0, "seconds": 0.0, "barks": 0,
                               "quiet_seconds": 0.0, "flags": [], "flagged_incidents": 0}
        d += timedelta(days=1)
    for inc in incidents:
        day = days.get(inc["date"])
        if not day:
            continue
        day["incidents"] += 1
        day["seconds"] += inc["duration"]
        day["barks"] += inc["subject_barks"]
        day["quiet_seconds"] += inc["quiet_seconds"]
        if inc["flags"]:
            day["flagged_incidents"] += 1
    for day in days.values():
        if day["seconds"] >= th["daily_minutes"] * 60 and day["seconds"] > 0:
            day["flags"].append("frequent")
        day["qualifying"] = bool(day["flags"] or day["flagged_incidents"])
    keys = sorted(days)
    win, need = int(th["habitual_window_days"]), int(th["habitual_days"])
    for i, k in enumerate(keys):
        if not days[k]["qualifying"] or need <= 0:
            continue
        # habitual if any window containing this day has >= need qualifying days
        for s in range(max(0, i - win + 1), i + 1):
            window = keys[s:s + win]
            if sum(days[w]["qualifying"] for w in window) >= need:
                days[k]["flags"].append("habitual")
                break
    return days


FLAG_LABELS = {
    "sustained": "Sustained",
    "quiet_hours": "Quiet hours",
    "frequent": "Frequent day",
    "habitual": "Habitual pattern",
}


def describe_thresholds(th: dict) -> dict[str, str]:
    return {
        "sustained": f"barking lasting {th['sustained_minutes']} min or more, "
                     f"with no break longer than {th['max_pause_seconds']} s",
        "quiet_hours": f"at least {th['quiet_minutes']} min of barking between "
                       f"{_fmt12(th['quiet_start'])} and {_fmt12(th['quiet_end'])}",
        "frequent": f"{th['daily_minutes']} min or more of barking in one day",
        "habitual": f"{th['habitual_days']} or more days meeting a criterion within "
                    f"{th['habitual_window_days']} days",
    }


def _fmt12(hhmm: str) -> str:
    t = _parse_hhmm(hhmm)
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


# --------------------------------------------------------------------------- report
def subject_options(store: Store) -> dict:
    dogs = store.dogs()
    households: dict[str, list] = defaultdict(list)
    for d in dogs:
        key = (d.get("address") or "").strip()
        if key:
            households[key].append(d)
    return {"dogs": dogs,
            "neighbors": [{"address": a, "owner": next((d["owner"] for d in ds if d.get("owner")), ""),
                           "dogs": ds} for a, ds in sorted(households.items())]}


def build_report(store: Store, tz: str | None, d0: date, d1: date, dog_id: int | None = None,
                 address: str | None = None, confirmed_only: bool = False,
                 flagged_only: bool = False) -> dict:
    clock = Clock(tz)
    ordn = get_ordinance(store)
    th = ordn["thresholds"]
    t0, t1 = clock.day_start(d0), clock.day_start(d1 + timedelta(days=1))
    dogs = {d["id"]: d for d in store.dogs()}

    if dog_id is not None:
        subject_dogs = [dogs[dog_id]] if dog_id in dogs else []
        subject = {"kind": "dog", "title": subject_dogs[0]["name"] if subject_dogs else "Unknown dog"}
    elif address:
        subject_dogs = [d for d in dogs.values() if (d.get("address") or "").strip() == address.strip()]
        subject = {"kind": "neighbor", "title": address.strip()}
    else:
        subject_dogs = []
        subject = {"kind": "all", "title": "All barking"}
    ids = {d["id"] for d in subject_dogs}
    if subject_dogs:
        subject["owner"] = next((d.get("owner") for d in subject_dogs if d.get("owner")), "")
        subject["address"] = next((d.get("address") for d in subject_dogs if d.get("address")), "")
        subject["dogs"] = [{"name": d["name"], "description": d.get("description") or ""} for d in subject_dogs]

    episodes = store.episodes_between(t0, t1)
    rows = store.q(
        "SELECT b.episode_id, b.label_dog_id, b.pred_dog_id FROM barks b JOIN episodes e ON e.id=b.episode_id"
        " WHERE e.start_ts < ? AND e.end_ts >= ? AND e.false_positive=0", (t1, t0))
    per_ep: dict[int, dict] = defaultdict(lambda: {"confirmed": 0, "auto": 0, "dogs": Counter()})
    for r in rows:
        d = r["label_dog_id"] if r["label_dog_id"] is not None else r["pred_dog_id"]
        if d == -1:              # marked "Unknown dog": a bark, credited to nobody
            d = None
        if d == NOT_BARK:
            continue
        info = per_ep[r["episode_id"]]
        info["dogs"][d] += 1
        if ids and d in ids:
            info["confirmed" if r["label_dog_id"] is not None else "auto"] += 1

    selected = []
    for e in episodes:
        info = per_ep.get(e["id"])
        if info is None or not sum(info["dogs"].values()):
            continue
        if ids:
            n = info["confirmed"] + (0 if confirmed_only else info["auto"])
            if n == 0:
                continue
            e = {**e, "subject_barks": n, "confirmed": info["confirmed"], "auto": info["auto"]}
        else:
            e = {**e, "subject_barks": sum(info["dogs"].values())}
        e["dog_names"] = {(dogs[k]["name"] if k in dogs else "Unidentified"): v
                          for k, v in info["dogs"].most_common()}
        selected.append(e)

    incidents = build_incidents(selected, th, clock)
    days = flag_days(incidents, th, d0, d1)
    for inc in incidents:
        inc["day_flags"] = list(days.get(inc["date"], {}).get("flags", []))
    if flagged_only:   # incidents that themselves meet a criterion (sustained / quiet hours)
        incidents = [i for i in incidents if i["flags"]]
    for n, inc in enumerate(incidents, 1):
        inc["number"] = n

    day_list = [d for d in sorted(days.values(), key=lambda x: x["date"])]
    active = [d for d in day_list if d["incidents"]]
    longest = max(incidents, key=lambda i: i["duration"], default=None)
    summary = {
        "days_in_period": len(day_list),
        "days_with_barking": len(active),
        "incidents": len(incidents),
        "total_seconds": sum(i["duration"] for i in incidents),
        "barks": sum(i["subject_barks"] for i in incidents),
        "flag_counts": {f: sum(1 for i in incidents if f in i["flags"]) for f in ("sustained", "quiet_hours")},
        "flagged_incidents": sum(1 for i in incidents if i["flags"]),
        "days_flagged": {f: sum(1 for d in day_list if f in d["flags"]) for f in ("frequent", "habitual")},
        "quiet_incidents": sum(1 for i in incidents if i["quiet_seconds"] > 0),
        "longest": {"start_ts": longest["start_ts"], "duration": longest["duration"]} if longest else None,
    }
    return {
        "generated_ts": datetime.now().timestamp(),
        "timezone": clock.tzname(),
        "period": {"from": d0.isoformat(), "to": d1.isoformat()},
        "subject": subject, "confirmed_only": confirmed_only, "flagged_only": flagged_only,
        "ordinance": ordn, "criteria": describe_thresholds(th), "flag_labels": FLAG_LABELS,
        "reporter": get_reporter(store),
        "summary": summary, "days": day_list,
        "incidents": [_incident_out(i, clock) for i in incidents],
    }


def _incident_out(inc: dict, clock: Clock) -> dict:
    s, e = clock.dt(inc["start_ts"]), clock.dt(inc["end_ts"])
    eps = inc["episodes"]
    names: Counter = Counter()
    for ep in eps:
        names.update(ep.get("dog_names", {}))
    confirmed = sum(ep.get("confirmed", 0) for ep in eps)
    auto = sum(ep.get("auto", 0) for ep in eps)
    notes = "; ".join(ep["note"] for ep in eps if ep.get("note"))
    return {
        "number": inc["number"], "date": inc["date"], "weekday": s.strftime("%a"),
        "start_ts": inc["start_ts"], "end_ts": inc["end_ts"],
        "start": s.strftime("%I:%M:%S %p").lstrip("0"), "end": e.strftime("%I:%M:%S %p").lstrip("0"),
        "duration": round(inc["duration"], 1), "barks": inc["barks"], "subject_barks": inc["subject_barks"],
        "quiet_seconds": round(inc["quiet_seconds"], 1),
        "flags": inc["flags"], "day_flags": inc["day_flags"], "dogs": dict(names.most_common()),
        "identified": ("confirmed" if confirmed and not auto else
                       "automatic" if auto and not confirmed else
                       "mixed" if confirmed and auto else ""),
        "episode_ids": [ep["id"] for ep in eps],
        "clips": [ep["clip_path"].rsplit("/", 1)[-1] for ep in eps if ep.get("clip_path")],
        "notes": notes,
    }


def fmt_dur(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
