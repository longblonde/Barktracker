"""Alerts to other apps when barking ends: a list of destinations, each with its own conditions.

Stored in the database (meta "alerts"). The single webhook of older versions (notify.webhook_url
in config.yaml or the dashboard) becomes the first destination the first time this runs.

Kinds and what they receive:
  webhook   POST of the full JSON (Home Assistant webhooks, Node-RED, your own script)
  ntfy      POST of the message as plain text, with Title/Tags headers (ntfy.sh or self-hosted)
  discord   POST {"content": message} to a Discord channel webhook
  slack     POST {"text": message} to a Slack incoming webhook
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

META = "alerts"
KINDS = {"webhook": "Webhook (JSON)", "ntfy": "ntfy", "discord": "Discord", "slack": "Slack"}
WHEN = {"episode": "Every episode", "criteria": "Only when it meets a report rule"}


def _blank(**kw) -> dict:
    d = {"id": secrets.token_hex(4), "name": "", "kind": "webhook", "url": "", "enabled": True,
         "when": "episode", "min_seconds": 30.0, "quiet_hours_only": False, "dogs": []}
    d.update(kw)
    return d


def load(store, cfg=None) -> list[dict]:
    items = store.get_meta(META)
    if items is None:                   # first run: carry over the old single webhook
        items = []
        url = None
        saved = store.get_meta("app_settings") or {}
        url = saved.get("notify.webhook_url") or (cfg.notify.webhook_url if cfg else None)
        if url:
            mins = saved.get("notify.min_duration", cfg.notify.min_duration if cfg else 30.0)
            items.append(_blank(name="Webhook", kind="ntfy" if "ntfy" in url else "webhook", url=url,
                                min_seconds=float(mins)))
        store.set_meta(META, items)
    return [{**_blank(), **i} for i in items]


def clean(item: dict) -> dict:
    d = _blank(**{k: v for k, v in item.items() if k in _blank()})
    d["name"] = str(d["name"] or "").strip()[:60] or KINDS.get(d["kind"], "Alert")
    if d["kind"] not in KINDS:
        raise ValueError("Unknown alert type")
    d["url"] = str(d["url"] or "").strip()
    if not d["url"].startswith(("http://", "https://")):
        raise ValueError("The address must start with http:// or https://")
    if d["when"] not in WHEN:
        raise ValueError("Unknown alert condition")
    d["min_seconds"] = max(0.0, float(d["min_seconds"] or 0))
    d["enabled"] = bool(d["enabled"])
    d["quiet_hours_only"] = bool(d["quiet_hours_only"])
    d["dogs"] = [int(x) for x in (d["dogs"] or [])]
    return d


def save(store, items: list[dict]) -> list[dict]:
    out = [clean(i) for i in items]
    store.set_meta(META, out)
    return out


def message(ep: dict, dog_names: dict[str, str]) -> tuple[str, dict]:
    dogs = {dog_names.get(k, "Unidentified" if k in ("u", "None") else "unknown"): v
            for k, v in (ep.get("dogs") or {}).items() if k != "0"}
    mins = ep["duration"] / 60
    length = f"{ep['duration']:.0f} s" if ep["duration"] < 90 else f"{mins:.1f} min"
    text = f"Barking for {length} ({ep['barks']} barks)" + (f": {', '.join(dogs)}" if dogs else "")
    return text, dogs


def matches(item: dict, ep: dict, ctx: dict) -> bool:
    if not item.get("enabled", True):
        return False
    if ep["duration"] < item.get("min_seconds", 0):
        return False
    if item.get("quiet_hours_only") and not ctx.get("quiet"):
        return False
    if item.get("when") == "criteria" and not ctx.get("flags"):
        return False
    want = {str(x) for x in item.get("dogs") or []}
    if want and not want & set((ep.get("dogs") or {}).keys()):
        return False
    return True


def send(item: dict, payload: dict, text: str, title: str = "BarkTracker") -> None:
    kind = item["kind"]
    headers = {"Content-Type": "application/json"}
    if kind == "ntfy":
        data = text.encode()
        headers = {"Content-Type": "text/plain; charset=utf-8", "Title": title, "Tags": "dog"}
    elif kind == "discord":
        data = json.dumps({"content": f"**{title}**: {text}"}).encode()
    elif kind == "slack":
        data = json.dumps({"text": f"*{title}*: {text}"}).encode()
    else:
        data = json.dumps({**payload, "message": text, "title": title}).encode()
    req = urllib.request.Request(item["url"], data=data, headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=10).read()


def dispatch(store, cfg, ep: dict, ctx: dict) -> int:
    """Send this finished episode to every destination whose conditions it meets (in the background)."""
    items = [i for i in load(store, cfg) if matches(i, ep, ctx)]
    if not items:
        return 0
    names = {str(d["id"]): d["name"] for d in store.dogs()}
    text, dogs = message(ep, names)
    payload = {**ep, "dogs": dogs, "flags": ctx.get("flags", []), "quiet_hours": bool(ctx.get("quiet"))}

    def go():
        for i in items:
            try:
                send(i, payload, text)
                _record(store, i["id"], None)
            except Exception as e:  # noqa: BLE001
                log.warning("Alert %s failed: %s", i.get("name"), e)
                _record(store, i["id"], f"{type(e).__name__}: {e}")
    threading.Thread(target=go, name="alerts", daemon=True).start()
    return len(items)


def _record(store, aid: str, error: str | None) -> None:
    st = store.get_meta("alerts_status") or {}
    st[aid] = {"ts": time.time(), "error": error}
    store.set_meta("alerts_status", st)


def status(store) -> dict:
    return store.get_meta("alerts_status") or {}


def test(item: dict) -> None:
    """Send a sample alert right away (raises if it fails)."""
    item = clean(item)
    send(item, {"test": True, "duration": 42, "barks": 17, "dogs": {"Rex": 17}},
         "Test from BarkTracker: barking for 42 s (17 barks): Rex")
