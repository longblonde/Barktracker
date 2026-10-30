"""Local animal-noise ordinance: settings, evaluation of logged barking against it, and
complaint-report assembly (by dog or by neighbor/household).

Most codes define the offence qualitatively ("habitually or
frequently"), so the numeric thresholds here are *documentation criteria* the user sets
to decide which incidents to highlight. They are not a legal determination.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from .storage import NOT_BARK, Store

THRESHOLDS = {
    "sustained_minutes": 10,     # incident at least this long counts as sustained barking
    "max_pause_seconds": 60,     # pauses up to this long don't break an incident
    "daily_minutes": 30,         # a day with at least this much barking counts as frequent
    "quiet_start": "22:00",
    "quiet_end": "07:00",
    "weekend_quiet": False,      # different quiet hours on Friday and Saturday nights
    "quiet_start_weekend": "23:00",
    "quiet_end_weekend": "09:00",
    "quiet_minutes": 5,          # incident overlapping quiet hours by this much is flagged
    "habitual_days": 3,          # this many qualifying days...
    "habitual_window_days": 7,   # ...within this many days marks a habitual pattern
}
GLOBAL_KEYS = ("quiet_start", "quiet_end", "max_pause_seconds", "weekend_quiet",
               "quiet_start_weekend", "quiet_end_weekend")                # shared by every rule
TIME_KEYS = ("quiet_start", "quiet_end", "quiet_start_weekend", "quiet_end_weekend")

# Rule types you can add on the Report page. "scope" says what a rule flags: one incident, or a day.
RULE_TYPES = {
    "sustained": {"label": "Sustained", "scope": "incident", "params": {"minutes": 10},
                  "help": "One stretch of barking lasting this long"},
    "quiet_hours": {"label": "Quiet hours", "scope": "incident", "params": {"minutes": 5},
                    "help": "This much barking during quiet hours"},
    "intermittent": {"label": "Intermittent", "scope": "incident", "params": {"minutes": 30, "window_hours": 3},
                     "help": "This much barking in total, on and off, within a time window"},
    "frequent": {"label": "Frequent day", "scope": "day", "params": {"minutes": 30},
                 "help": "This much barking in one day"},
    "habitual": {"label": "Habitual pattern", "scope": "day", "params": {"days": 3, "window_days": 7},
                 "help": "Days that meet any other rule, this many times within a window"},
}
# legacy threshold names <-> the default rule they belong to
_LEGACY = {"sustained_minutes": ("sustained", "minutes"), "quiet_minutes": ("quiet_hours", "minutes"),
           "daily_minutes": ("frequent", "minutes"), "habitual_days": ("habitual", "days"),
           "habitual_window_days": ("habitual", "window_days")}


def rules_from_thresholds(th: dict) -> list[dict]:
    out = []
    for rid in ("sustained", "quiet_hours", "frequent", "habitual"):
        t = RULE_TYPES[rid]
        params = dict(t["params"])
        for legacy, (r, k) in _LEGACY.items():
            if r == rid and legacy in th:
                params[k] = th[legacy]
        out.append({"id": rid, "type": rid, "label": t["label"], "enabled": True, "params": params})
    return out


def thresholds_from(rules: list[dict], base: dict) -> dict:
    """The flat thresholds view (older API, dashboard quiet hours) of a rule list."""
    th = {k: base.get(k, THRESHOLDS[k]) for k in THRESHOLDS}
    for legacy, (rid, key) in _LEGACY.items():
        r = next((x for x in rules if x["type"] == rid and x.get("enabled", True)), None)
        if r is not None:
            th[legacy] = r["params"].get(key, th[legacy])
    return th


def clean_rule(r: dict, taken: set[str]) -> dict:
    typ = r.get("type")
    if typ not in RULE_TYPES:
        raise ValueError(f"Unknown rule type: {typ}")
    t = RULE_TYPES[typ]
    params = {}
    for k, dflt in t["params"].items():
        v = float((r.get("params") or {}).get(k, dflt))
        if v < 0:
            raise ValueError(f"{k} must be zero or more")
        params[k] = int(v) if v.is_integer() else v
    rid = str(r.get("id") or "").strip()
    if not rid or rid in taken:
        rid = typ if typ not in taken else next(f"{typ}_{n}" for n in range(2, 99) if f"{typ}_{n}" not in taken)
    taken.add(rid)
    label = str(r.get("label") or t["label"]).strip()[:60] or t["label"]
    return {"id": rid, "type": typ, "label": label, "enabled": bool(r.get("enabled", True)), "params": params}

PRESETS: dict[str, dict] = {
    # Placeholder wording for a new install: pick a city preset or enter your own.
    "example": {
        "preset": "example",
        "jurisdiction": "City of Anytown, USA (example)",
        "code_section": "Anytown Municipal Code § 0-0-0, Animal Noise (replace with your ordinance)",
        "rule_text": ("No person may keep an animal that habitually or frequently barks, howls or makes other "
                      "noise that disturbs the peace and quiet of the neighborhood."),
        "rule_note": ("Example wording. Pick a city preset, or enter your local code section, the rule in "
                      "plain words, a link to the source and where to report. Many cities phrase it as "
                      "barking that is continuous for 10 minutes, or on and off for 30 minutes within "
                      "3 hours; add rules below to match yours."),
        "source_url": "",
        "penalty": "See your city code.",
        "contacts": [
            {"name": "Anytown Police non-emergency line (example)", "phone": "555-555-0100"},
            {"name": "Anytown Animal Control (example)", "phone": "555-555-0101"},
            {"name": "Anytown City Hall, code enforcement (example)", "phone": "555-555-0102"},
        ],
        "thresholds": dict(THRESHOLDS),
    },
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
        "thresholds": dict(THRESHOLDS),
    },
}
PRESET_LABELS = {"example": "Generic placeholder", "burnsville_mn": "Burnsville, MN"}
DEFAULT_PRESET = "example"
REPORTER_FIELDS = ("name", "address", "phone", "email")


def _with_rules(o: dict) -> dict:
    if not o.get("rules"):
        o["rules"] = rules_from_thresholds(o["thresholds"])
    o["thresholds"] = thresholds_from(o["rules"], o["thresholds"])
    return o


def default_ordinance() -> dict:
    return _with_rules(copy.deepcopy(PRESETS[DEFAULT_PRESET]))


def get_ordinance(store: Store) -> dict:
    saved = store.get_meta("ordinance") or {}
    base = copy.deepcopy(PRESETS[DEFAULT_PRESET])
    th = {**base["thresholds"], **(saved.get("thresholds") or {})}
    out = {**base, **saved, "thresholds": th}
    return _with_rules(out)


def save_ordinance(store: Store, data: dict) -> dict:
    if data.get("reset"):
        cur = _with_rules(copy.deepcopy(PRESETS[data.get("preset") or DEFAULT_PRESET]))
        store.set_meta("ordinance", cur)
        return cur
    cur = get_ordinance(store)
    allowed = {"preset", "jurisdiction", "code_section", "rule_text", "rule_note", "source_url",
               "penalty", "contacts"}
    for k in allowed & data.keys():
        cur[k] = data[k]
    if "rules" in data:
        taken: set[str] = set()
        cur["rules"] = [clean_rule(r, taken) for r in data["rules"]]
    if "thresholds" in data:
        th = cur["thresholds"]
        for k, v in data["thresholds"].items():
            if k not in th:
                continue
            if k in TIME_KEYS:
                _parse_hhmm(v)   # validates
                th[k] = v
                continue
            if k == "weekend_quiet":
                th[k] = bool(v)
                continue
            v = float(v)
            if v < 0:
                raise ValueError(f"{k} must be zero or more")
            v = int(v) if float(v).is_integer() else v
            th[k] = v
            if k in _LEGACY:                     # the old names edit the matching default rule
                rid, key = _LEGACY[k]
                for r in cur["rules"]:
                    if r["type"] == rid:
                        r["params"][key] = v
                        break
    cur["thresholds"] = thresholds_from(cur["rules"], cur["thresholds"])
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

    def at(self, d: date, t: dtime) -> float:
        """The timestamp of a wall-clock time on a day (right across daylight-saving changes)."""
        dt = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=self.tz) if self.tz else \
            datetime(d.year, d.month, d.day, t.hour, t.minute)
        return dt.timestamp()

    def tzname(self) -> str:
        return str(self.tz) if self.tz else (datetime.now().astimezone().tzname() or "local time")


def is_weekend_night(d: date, overnight: bool) -> bool:
    """Which windows use the weekend times: Friday and Saturday nights for a window that runs past
    midnight (it's named by the evening it starts), Saturday and Sunday for one within a day."""
    return d.weekday() in ((4, 5) if overnight else (5, 6))


def quiet_window(th: dict, d: date) -> tuple[dtime, dtime]:
    """Start and end of the quiet hours that begin on day d."""
    qs, qe = _parse_hhmm(th["quiet_start"]), _parse_hhmm(th["quiet_end"])
    if th.get("weekend_quiet"):
        ws, we = _parse_hhmm(th["quiet_start_weekend"]), _parse_hhmm(th["quiet_end_weekend"])
        if is_weekend_night(d, ws > we):
            return ws, we
    return qs, qe


def quiet_spans(t0: float, t1: float, th: dict, clock: Clock) -> list[tuple[float, float]]:
    """Every quiet-hours stretch overlapping [t0, t1], as timestamps (handles midnight and DST)."""
    out = []
    d, last = clock.dt(t0).date() - timedelta(days=1), clock.dt(t1).date()
    while d <= last:
        qs, qe = quiet_window(th, d)
        if qs != qe:
            a = clock.at(d, qs)
            b = clock.at(d if qe > qs else d + timedelta(days=1), qe)
            if b > t0 and a < t1:
                out.append((a, b))
        d += timedelta(days=1)
    return out


def in_quiet_hours(ts: float, th: dict, clock: Clock) -> bool:
    return any(a <= ts < b for a, b in quiet_spans(ts, ts + 1, th, clock))


def quiet_week(th: dict) -> list[tuple[int, int]]:
    """Quiet hours as minutes of a week (Monday 00:00 = 0), for the weekday-by-hour heatmap."""
    ref = date(2024, 1, 1)                      # a Monday
    out = []
    for k in range(7):
        qs, qe = quiet_window(th, ref + timedelta(days=k))
        if qs == qe:
            continue
        a = k * 1440 + qs.hour * 60 + qs.minute
        dur = (qe.hour * 60 + qe.minute - qs.hour * 60 - qs.minute) % 1440
        b = a + dur
        if b <= 10080:
            out.append((a, b))
        else:                                    # Sunday night runs into Monday morning
            out += [(a, 10080), (0, b - 10080)]
    return out


def quiet_overlap(start: float, end: float, th: dict, clock: Clock) -> float:
    """Seconds of [start, end] that fall inside quiet hours (window may cross midnight)."""
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in quiet_spans(start, end, th, clock))


def build_incidents(episodes: list[dict], th: dict, clock: Clock, rules: list[dict] | None = None) -> list[dict]:
    """Merge episodes separated by short pauses into incidents and flag them by the incident rules."""
    rules = [r for r in (rules or rules_from_thresholds(th)) if r.get("enabled", True)]
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
        inc["date"] = clock.dt(inc["start_ts"]).date().isoformat()
    for inc in out:
        flags = []
        for r in rules:
            p = r["params"]
            if r["type"] == "sustained" and inc["duration"] >= p["minutes"] * 60:
                flags.append(r["id"])
            elif r["type"] == "quiet_hours" and inc["quiet_seconds"] > 0 and inc["quiet_seconds"] >= p["minutes"] * 60:
                flags.append(r["id"])
            elif r["type"] == "intermittent":
                lo = inc["end_ts"] - p["window_hours"] * 3600
                total = sum(min(o["end_ts"], inc["end_ts"]) - max(o["start_ts"], lo)
                            for o in out if o["end_ts"] > lo and o["start_ts"] <= inc["end_ts"])
                if total >= p["minutes"] * 60 and p["minutes"] > 0:
                    flags.append(r["id"])
        inc["flags"] = flags
    return out


def flag_days(incidents: list[dict], th: dict, d0: date, d1: date,
              rules: list[dict] | None = None) -> dict[str, dict]:
    """Per-day totals plus the day rules' flags (frequent days, habitual patterns)."""
    rules = [r for r in (rules or rules_from_thresholds(th)) if r.get("enabled", True)]
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
        for r in rules:
            if r["type"] == "frequent" and day["seconds"] > 0 and day["seconds"] >= r["params"]["minutes"] * 60:
                day["flags"].append(r["id"])
        day["qualifying"] = bool(day["flags"] or day["flagged_incidents"])
    keys = sorted(days)
    for r in rules:
        if r["type"] != "habitual":
            continue
        win, need = int(r["params"]["window_days"]), int(r["params"]["days"])
        hits = []
        for i, k in enumerate(keys):
            if not days[k]["qualifying"] or need <= 0:
                continue
            # habitual if any window containing this day has >= need qualifying days
            for s in range(max(0, i - win + 1), i + 1):
                window = keys[s:s + win]
                if sum(days[w]["qualifying"] for w in window) >= need:
                    hits.append(k)
                    break
        for k in hits:
            days[k]["flags"].append(r["id"])
    return days


FLAG_LABELS = {
    "sustained": "Sustained",
    "quiet_hours": "Quiet hours",
    "frequent": "Frequent day",
    "habitual": "Habitual pattern",
}


def describe_rule(r: dict, th: dict) -> str:
    p = r["params"]
    return {
        "sustained": lambda: f"barking lasting {p['minutes']} min or more, with no break longer than {th['max_pause_seconds']} s",
        "quiet_hours": lambda: f"at least {p['minutes']} min of barking during quiet hours, {quiet_text(th)}",
        "intermittent": lambda: f"{p['minutes']} min or more of barking, on and off, within {p['window_hours']} hours",
        "frequent": lambda: f"{p['minutes']} min or more of barking in one day",
        "habitual": lambda: f"{p['days']} or more days meeting a rule within {p['window_days']} days",
    }[r["type"]]()


def describe_rules(o: dict) -> dict[str, str]:
    return {r["id"]: describe_rule(r, o["thresholds"]) for r in o["rules"] if r.get("enabled", True)}


def rule_labels(o: dict) -> dict[str, str]:
    return {**FLAG_LABELS, **{r["id"]: r["label"] for r in o["rules"]}}


def describe_thresholds(th: dict) -> dict[str, str]:
    return {
        "sustained": f"barking lasting {th['sustained_minutes']} min or more, "
                     f"with no break longer than {th['max_pause_seconds']} s",
        "quiet_hours": f"at least {th['quiet_minutes']} min of barking between "
                       f"{_fmt12(th['quiet_start'])} and {_fmt12(th['quiet_end'])}"
                       + (f" ({quiet_text(th).split('(', 1)[1]}" if th.get("weekend_quiet") else ""),
        "frequent": f"{th['daily_minutes']} min or more of barking in one day",
        "habitual": f"{th['habitual_days']} or more days meeting a criterion within "
                    f"{th['habitual_window_days']} days",
    }


def quiet_text(th: dict) -> str:
    base = f"{_fmt12(th['quiet_start'])} to {_fmt12(th['quiet_end'])}"
    if not th.get("weekend_quiet"):
        return base
    ws, we = _parse_hhmm(th["quiet_start_weekend"]), _parse_hhmm(th["quiet_end_weekend"])
    which = "Friday and Saturday nights" if ws > we else "Saturdays and Sundays"
    return f"{base} ({_fmt12(th['quiet_start_weekend'])} to {_fmt12(th['quiet_end_weekend'])} on {which})"


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

    rules = [r for r in ordn["rules"] if r.get("enabled", True)]
    inc_rules = [r["id"] for r in rules if RULE_TYPES[r["type"]]["scope"] == "incident"]
    day_rules = [r["id"] for r in rules if RULE_TYPES[r["type"]]["scope"] == "day"]
    incidents = build_incidents(selected, th, clock, rules)
    days = flag_days(incidents, th, d0, d1, rules)
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
        "flag_counts": {f: sum(1 for i in incidents if f in i["flags"]) for f in inc_rules},
        "flagged_incidents": sum(1 for i in incidents if i["flags"]),
        "days_flagged": {f: sum(1 for d in day_list if f in d["flags"]) for f in day_rules},
        "quiet_incidents": sum(1 for i in incidents if i["quiet_seconds"] > 0),
        "longest": {"start_ts": longest["start_ts"], "duration": longest["duration"]} if longest else None,
    }
    return {
        "generated_ts": datetime.now().timestamp(),
        "timezone": clock.tzname(),
        "period": {"from": d0.isoformat(), "to": d1.isoformat()},
        "subject": subject, "confirmed_only": confirmed_only, "flagged_only": flagged_only,
        "ordinance": ordn, "criteria": describe_rules(ordn), "flag_labels": rule_labels(ordn),
        "rule_scopes": {r["id"]: RULE_TYPES[r["type"]]["scope"] for r in rules},
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


def episode_flags(store: Store, tz: str | None, start_ts: float, end_ts: float) -> dict:
    """For alerts: does the barking that just ended meet a rule, and did it touch quiet hours?"""
    clock = Clock(tz)
    o = get_ordinance(store)
    th, rules = o["thresholds"], [r for r in o["rules"] if r.get("enabled", True)]
    look = max([r["params"].get("window_hours", 0) * 3600 for r in rules] + [3600])
    eps = [dict(e, subject_barks=e["bark_count"]) for e in store.episodes_between(end_ts - look, end_ts + 1)
           if not e["false_positive"]]
    incs = build_incidents(eps, th, clock, rules)
    mine = next((i for i in incs if i["start_ts"] <= start_ts <= i["end_ts"] + 1), None)
    return {"flags": mine["flags"] if mine else [],
            "quiet": quiet_overlap(start_ts, max(end_ts, start_ts + 0.5), th, clock) > 0}
