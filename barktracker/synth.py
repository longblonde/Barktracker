"""Synthetic barks for tests and the demo dataset. Not used in normal operation."""
from __future__ import annotations

import numpy as np

from . import features as F

DOG_VOICES = {
    # name: (f0 Hz, brightness, duration s, roughness)
    "Big dog": (330.0, 0.6, 0.30, 0.25),
    "Small dog": (850.0, 1.0, 0.16, 0.10),
}


def bark(f0=400.0, brightness=0.8, duration=0.25, rough=0.2, rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    f0 *= rng.uniform(0.92, 1.08)
    duration *= rng.uniform(0.85, 1.15)
    n = int(duration * F.SR)
    t = np.arange(n) / F.SR
    # pitch rises quickly then falls, like a real bark
    contour = f0 * (1 + 0.35 * np.sin(np.pi * np.clip(t / duration, 0, 1)) - 0.2 * t / duration)
    phase = 2 * np.pi * np.cumsum(contour) / F.SR
    sig = np.zeros(n)
    for h in range(1, 12):
        if h * f0 > 7000:
            break
        sig += (brightness ** (h - 1)) / h ** 0.7 * np.sin(h * phase + rng.uniform(0, 6.28))
    sig += rough * rng.standard_normal(n) * np.abs(sig).mean()
    env = (1 - np.exp(-t / 0.008)) * np.exp(-t / (duration * 0.45))
    sig = sig * env
    return (sig / (np.abs(sig).max() + 1e-9)).astype(np.float32)


def scene(events: list[tuple[float, str]], total: float, noise_db: float = -60.0,
          level: float = 0.5, seed: int = 0) -> np.ndarray:
    """events: [(time_s, dog_name)]; returns a mono 16 kHz mix with room noise."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(total * F.SR)).astype(np.float32) * (10 ** (noise_db / 20))
    for t, dog in events:
        f0, br, dur, rough = DOG_VOICES[dog]
        b = bark(f0, br, dur, rough, rng) * level * rng.uniform(0.7, 1.0)
        i = int(t * F.SR)
        x[i:i + len(b)] += b[: len(x) - i]
    return np.clip(x, -1, 1)


def bout(start: float, dog: str, n: int, spacing=(0.5, 1.2), rng=None) -> list[tuple[float, str]]:
    rng = rng or np.random.default_rng()
    out, t = [], start
    for _ in range(n):
        out.append((t, dog))
        t += rng.uniform(*spacing)
    return out
