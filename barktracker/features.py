"""Pure-numpy audio features: log-mel spectrograms and a compact per-bark 'voiceprint'."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

SR = 16000
N_FFT = 512
HOP = 160          # 10 ms
N_MELS = 40
FMIN, FMAX = 80.0, 7600.0


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10 ** (np.asarray(m) / 2595.0) - 1.0)


@lru_cache(maxsize=4)
def mel_filterbank(n_mels: int = N_MELS, n_fft: int = N_FFT, sr: int = SR,
                   fmin: float = FMIN, fmax: float = FMAX) -> np.ndarray:
    mels = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    hz = _mel_to_hz(mels)
    bins = np.fft.rfftfreq(n_fft, 1.0 / sr)
    fb = np.zeros((n_mels, len(bins)), dtype=np.float32)
    for i in range(n_mels):
        lo, mid, hi = hz[i], hz[i + 1], hz[i + 2]
        up = (bins - lo) / max(mid - lo, 1e-6)
        down = (hi - bins) / max(hi - mid, 1e-6)
        fb[i] = np.maximum(0, np.minimum(up, down))
    fb /= np.maximum(fb.sum(axis=1, keepdims=True), 1e-9)
    return fb


@lru_cache(maxsize=4)
def _window(n: int) -> np.ndarray:
    return np.hanning(n).astype(np.float32)


def power_spectrogram(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """Returns (frames, n_fft//2+1) power spectrum."""
    x = np.asarray(x, dtype=np.float32)
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n_frames = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx] * _window(n_fft)
    return (np.abs(np.fft.rfft(frames, axis=1)) ** 2).astype(np.float32)


def log_mel(x: np.ndarray) -> np.ndarray:
    """(frames, N_MELS) log-mel energies in dB."""
    p = power_spectrogram(x)
    m = p @ mel_filterbank().T
    return 10.0 * np.log10(m + 1e-10)


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)) + 1e-12))
    return 20.0 * np.log10(rms + 1e-12)


FEATURE_DIM = N_MELS * 2 + 8


def bark_features(x: np.ndarray) -> np.ndarray:
    """A fixed-length descriptor of one bark (~0.2-1.0 s of 16 kHz audio).

    Uses only the loud frames so silence around the bark doesn't dominate.
    Captures timbre (mel mean/std), brightness, tonality, pitch and length,
    which is what tends to differ between individual dogs.
    """
    x = np.asarray(x, dtype=np.float32)
    p = power_spectrogram(x)
    mel = 10.0 * np.log10(p @ mel_filterbank().T + 1e-10)
    frame_db = 10.0 * np.log10(p.sum(axis=1) + 1e-10)
    loud = frame_db > (frame_db.max() - 15.0)
    if loud.sum() < 2:
        loud[:] = True
    mel_l, p_l = mel[loud], p[loud]

    # Normalise for loudness so distance from the mic matters less.
    mel_norm = mel_l - mel_l.mean(axis=1, keepdims=True)
    mel_mean = mel_norm.mean(axis=0)
    mel_std = mel_norm.std(axis=0)

    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    psum = p_l.sum(axis=1) + 1e-10
    centroid = (p_l * freqs).sum(axis=1) / psum
    bandwidth = np.sqrt((p_l * (freqs[None, :] - centroid[:, None]) ** 2).sum(axis=1) / psum)
    flatness = np.exp(np.mean(np.log(p_l + 1e-10), axis=1)) / (np.mean(p_l, axis=1) + 1e-10)
    cumsum = np.cumsum(p_l, axis=1)
    rolloff = freqs[np.argmax(cumsum >= 0.85 * cumsum[:, -1:], axis=1)]

    f0, voiced = estimate_pitch(x)
    duration = loud.sum() * HOP / SR

    extra = np.array([
        np.log1p(np.median(centroid)) , np.log1p(np.median(bandwidth)),
        float(np.median(flatness)), np.log1p(np.median(rolloff)),
        np.log1p(f0), voiced, duration, float(np.std(frame_db[loud]) / 10.0),
    ], dtype=np.float32)
    return np.concatenate([mel_mean, mel_std, extra]).astype(np.float32)


def estimate_pitch(x: np.ndarray, fmin: float = 150.0, fmax: float = 1500.0) -> tuple[float, float]:
    """Autocorrelation pitch on the loudest 64 ms. Returns (f0_hz, voicing 0-1)."""
    n = 1024
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    # loudest window
    e = np.convolve(np.square(x), np.ones(n, dtype=np.float32), mode="valid")
    start = int(np.argmax(e))
    seg = x[start:start + n] * _window(n)
    seg = seg - seg.mean()
    spec = np.fft.rfft(seg, 2 * n)
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    lo, hi = int(SR / fmax), int(SR / fmin)
    lag = lo + int(np.argmax(ac[lo:hi]))
    return float(SR / lag), float(max(0.0, ac[lag]))


def resample(x: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    if sr_in == sr_out:
        return x.astype(np.float32, copy=False)
    n_out = int(round(len(x) * sr_out / sr_in))
    if sr_in > sr_out:
        # crude anti-alias: moving average over the decimation factor
        k = max(1, int(round(sr_in / sr_out)))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    t_in = np.arange(len(x)) / sr_in
    t_out = np.arange(n_out) / sr_out
    return np.interp(t_out, t_in, x).astype(np.float32)


def spectrogram_image(x: np.ndarray, width: int = 900, height: int = 96,
                      fmax: float = 6000.0) -> tuple[np.ndarray, float]:
    """Compact uint8 (height, width) spectrogram for the dashboard. Low freq at row 0."""
    p = power_spectrogram(x, n_fft=512, hop=max(80, len(x) // max(width, 1)))
    freqs = np.fft.rfftfreq(512, 1.0 / SR)
    p = p[:, freqs <= fmax]
    db = 10 * np.log10(p + 1e-10)
    # log-ish frequency axis via mel band resampling
    n_bins = p.shape[1]
    mel_edges = _mel_to_hz(np.linspace(_hz_to_mel(60), _hz_to_mel(fmax), height + 1))
    bin_idx = np.clip(np.searchsorted(freqs[:n_bins], mel_edges), 0, n_bins - 1)
    rows = np.stack([db[:, bin_idx[i]:max(bin_idx[i + 1], bin_idx[i] + 1)].max(axis=1)
                     for i in range(height)], axis=0)  # (height, frames)
    # time resample to width
    if rows.shape[1] != width and rows.shape[1] > 1:
        t = np.linspace(0, rows.shape[1] - 1, width)
        i0 = np.floor(t).astype(int)
        rows = rows[:, i0]
    hi = np.percentile(rows, 99.5)
    lo = hi - 70
    img = np.clip((rows - lo) / (hi - lo), 0, 1)
    return (img * 255).astype(np.uint8), len(x) / SR


# ---- live spectrogram column for the tuning page ----------------------------------
SPEC_ROWS = 64


@lru_cache(maxsize=2)
def _col_edges(rows: int = SPEC_ROWS, n_fft: int = 800) -> np.ndarray:
    hz = _mel_to_hz(np.linspace(_hz_to_mel(60.0), _hz_to_mel(6000.0), rows + 1))
    return np.clip(np.round(hz / (SR / n_fft)).astype(int), 1, n_fft // 2)


def spec_column(frame: np.ndarray, rows: int = SPEC_ROWS) -> np.ndarray:
    """One 50 ms frame as a uint8 column (row 0 = low frequency), on a fixed dB scale so columns
    from different moments compare: about -100 dB -> 0, -25 dB -> 255 (relative to full scale)."""
    x = np.asarray(frame, dtype=np.float32)
    n = len(x)
    p = np.abs(np.fft.rfft(x * _window(n))) ** 2
    e = _col_edges(rows, n)
    csum = np.concatenate([[0.0], np.cumsum(p)])
    hi = np.maximum(e[1:], e[:-1] + 1)
    mean = (csum[hi] - csum[e[:-1]]) / (hi - e[:-1])
    db = 10 * np.log10(mean + 1e-12) - 10 * np.log10((n / 4) ** 2)
    return (np.clip((db + 100.0) / 75.0, 0, 1) * 255).astype(np.uint8)


# ---- live EQ bands for the dashboard -------------------------------------------
EQ_BANDS = 20
_EQ_FFT = 800                        # one 50 ms frame at 16 kHz -> 20 Hz bins


@lru_cache(maxsize=2)
def _eq_edges(n_bands: int = EQ_BANDS, n_fft: int = _EQ_FFT) -> tuple[np.ndarray, np.ndarray]:
    edges = np.geomspace(90.0, 7600.0, n_bands + 1)
    bins = np.round(edges / (SR / n_fft)).astype(int)
    lo = bins[:-1]
    hi = np.maximum(bins[1:], lo + 1)
    return lo, hi


# full-scale sine through a Hann window peaks at (N/4)^2 power in one bin
_EQ_REF_DB = 10 * np.log10((_EQ_FFT / 4) ** 2)


def eq_bands(frame: np.ndarray, n_bands: int = EQ_BANDS) -> np.ndarray:
    """Log-spaced band levels of one frame, 0..1 (per-band level, about -84 dB -> 0, -30 dB -> 1)."""
    x = np.asarray(frame, dtype=np.float32)
    if len(x) != _EQ_FFT:
        x = np.resize(x, _EQ_FFT) if len(x) else np.zeros(_EQ_FFT, np.float32)
    p = np.abs(np.fft.rfft(x * _window(_EQ_FFT))) ** 2
    lo, hi = _eq_edges(n_bands)
    csum = np.concatenate([[0.0], np.cumsum(p)])
    peak = np.array([p[a:b].max() for a, b in zip(lo, hi)])
    mean = (csum[hi] - csum[lo]) / (hi - lo)
    db = 10 * np.log10(0.5 * peak + 0.5 * mean + 1e-12) - _EQ_REF_DB
    return np.clip((db + 84.0) / 54.0, 0.0, 1.0).astype(np.float32)


# ---- locating barks precisely in time --------------------------------------------
BARK_LO_HZ, BARK_HI_HZ = 300.0, 3500.0


def band_envelope(x: np.ndarray, lo: float = BARK_LO_HZ, hi: float = BARK_HI_HZ) -> np.ndarray:
    """Energy (dB) in the bark band every 10 ms. Frame k is centred on sample k*HOP + N_FFT/2."""
    p = power_spectrogram(x)
    f = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    return 10.0 * np.log10(p[:, (f >= lo) & (f <= hi)].sum(axis=1) + 1e-10)


def frame_to_sample(k: int) -> int:
    """Sample index of an envelope frame's attack point (a little before the frame centre)."""
    return int(k * HOP + N_FFT // 2 - HOP)


def find_attacks(env: np.ndarray, floor: float | None = None, min_rise: float = 8.0,
                 sep_s: float = 0.15) -> list[dict]:
    """Bark-like events in a band envelope: each is a local peak at least `min_rise` dB above the
    floor, separated by `sep_s`. Returns frames for the attack (onset), peak and end."""
    if len(env) == 0:
        return []
    floor = float(np.percentile(env, 20)) if floor is None else floor
    sep = max(1, int(sep_s * SR / HOP))
    out = []
    n = len(env)
    for i in range(n):
        v = env[i]
        if v < floor + min_rise:
            continue
        lo, hi = max(0, i - sep), min(n, i + sep + 1)
        if v < env[lo:hi].max() or (i > lo and np.any(env[lo:i] == v)):
            continue
        # attack: walk back to where it was 12 dB down. A quiet bark (less than ~16 dB above the
        # floor) never gets 12 dB down before the background noise, so for those stop 4 dB above
        # the floor instead; otherwise the walk runs through the noise into the previous sound.
        down = min(12.0, max(3.0, v - floor - 4.0))
        k = i
        while k > 0 and env[k - 1] >= v - down and i - k < 30:
            k -= 1
        e = i                                      # end: decays 15 dB, nears the floor, or next attack
        while (e + 1 < n and env[e + 1] >= v - 15.0 and env[e + 1] >= floor + 4.0 and e - i < 150
               and not (e > i + 5 and env[e + 1] - env[e] > 6.0)):
            e += 1
        out.append({"onset": k, "peak": i, "end": e + 1, "peak_db": float(v), "floor": floor})
    return out


def isolate(x: np.ndarray, start: int, end: int, pad_before: float = 0.04, pad_after: float = 0.12,
            ramp: float = 0.02) -> np.ndarray:
    """Copy of x with everything outside [start, end] faded to silence, so a classifier judges this
    one sound and not a neighbouring bark that happens to fall in the same window."""
    n = len(x)
    a = max(0, start - int(pad_before * SR))
    b = min(n, end + int(pad_after * SR))
    r = max(1, int(ramp * SR))
    m = np.zeros(n, np.float32)
    m[a:b] = 1.0
    up = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, r))
    lo = max(0, a - r)
    m[lo:a] = up[r - (a - lo):]
    hi = min(n, b + r)
    m[b:hi] = up[::-1][: hi - b]
    return x * m
