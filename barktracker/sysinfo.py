"""Hardware and system status for Settings > System: what the Pi is, how warm and busy it is."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip("\x00\n ")
    except OSError:
        return None


def _throttled() -> dict | None:
    """vcgencmd get_throttled on a Pi: under-voltage and heat throttling, now and since boot."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        v = int(out.strip().split("=")[1], 16)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return {"under_voltage_now": bool(v & 0x1), "throttled_now": bool(v & 0x4),
            "under_voltage_since_boot": bool(v & 0x10000), "throttled_since_boot": bool(v & 0x40000)}


def info(cfg) -> dict:
    mem = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            mem[k] = int(v.split()[0]) // 1024
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    up = _read("/proc/uptime")
    du = shutil.disk_usage(cfg.data_path)
    try:
        load = os.getloadavg()
    except OSError:
        load = None
    return {
        "model": _read("/proc/device-tree/model") or platform.machine(),
        "os": _os_name(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "cpus": os.cpu_count(),
        "load": [round(x, 2) for x in load] if load else None,
        "temp_c": round(int(temp) / 1000, 1) if temp and temp.isdigit() else None,
        "mem_total_mb": mem.get("MemTotal"), "mem_free_mb": mem.get("MemAvailable"),
        "uptime_s": int(float(up.split()[0])) if up else None,
        "disk_free_mb": du.free // 2**20, "disk_total_mb": du.total // 2**20,
        "power": _throttled(),
        "now": time.time(),
    }


def _os_name() -> str:
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return platform.system()
