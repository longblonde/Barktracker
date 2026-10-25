"""Bark classifiers.

YamnetClassifier  - Google's YAMNet (AudioSet, 521 classes) via TFLite. Accurate, ~20-40 ms/run on a Pi 4.
HeuristicClassifier - pure numpy fallback when no TFLite runtime/model is available. Works for
                      loud, close barks but will mistake some claps/door slams for barks.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import numpy as np

from . import features as F

log = logging.getLogger(__name__)

WINDOW = 15600  # 0.975 s at 16 kHz, YAMNet's native patch

# Which AudioSet classes count as a bark, with weights (dog-ish but not barks count less).
DOG_LABELS = {
    "Bark": 1.0, "Bow-wow": 1.0, "Yip": 0.9, "Dog": 0.8,
    "Howl": 0.6, "Growling": 0.4, "Whimper (dog)": 0.3,
}
FALLBACK_INDEX = {"Dog": 69, "Bark": 70, "Yip": 71, "Howl": 72, "Bow-wow": 73,
                  "Growling": 74, "Whimper (dog)": 75}


class Classifier:
    name = "base"

    def bark_probability(self, x: np.ndarray) -> tuple[float, dict]:
        """x: 16 kHz mono float32 ~1 s window. Returns (p_bark 0-1, details)."""
        raise NotImplementedError


def _load_interpreter(model_path: str):
    try:
        from ai_edge_litert.interpreter import Interpreter  # type: ignore
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter  # type: ignore
        except ImportError:
            from tensorflow.lite import Interpreter  # type: ignore  # noqa
    return Interpreter(model_path=model_path, num_threads=2)


class YamnetClassifier(Classifier):
    name = "yamnet"

    def __init__(self, model_path: str):
        self.interp = _load_interpreter(model_path)
        self.interp.allocate_tensors()
        inp = self.interp.get_input_details()[0]
        self.in_idx = inp["index"]
        self.in_shape = tuple(inp["shape"])
        self.out_idx = self.interp.get_output_details()[0]["index"]
        labels = self._labels(model_path)
        self.labels = labels
        self.dog_idx = {}
        for name, w in DOG_LABELS.items():
            if labels and name in labels:
                self.dog_idx[labels.index(name)] = (name, w)
            elif name in FALLBACK_INDEX:
                self.dog_idx[FALLBACK_INDEX[name]] = (name, w)
        log.info("YAMNet loaded (%s), dog classes: %s", model_path,
                 sorted(n for n, _ in self.dog_idx.values()))

    @staticmethod
    def _labels(model_path: str) -> list[str] | None:
        # Task-library models carry their label list as a zip appended to the flatbuffer.
        try:
            with zipfile.ZipFile(model_path) as z:
                for n in z.namelist():
                    if n.endswith(".txt"):
                        return z.read(n).decode().splitlines()
        except zipfile.BadZipFile:
            pass
        side = Path(model_path).with_suffix(".labels.txt")
        if side.exists():
            return side.read_text().splitlines()
        return None

    def scores(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if len(x) < WINDOW:
            x = np.pad(x, ((WINDOW - len(x)) // 2, WINDOW - len(x) - (WINDOW - len(x)) // 2))
        x = np.clip(x[:WINDOW], -1.0, 1.0).reshape(self.in_shape)
        self.interp.set_tensor(self.in_idx, x)
        self.interp.invoke()
        return np.asarray(self.interp.get_tensor(self.out_idx)).reshape(-1)

    def bark_probability(self, x):
        s = self.scores(x)
        weighted = {name: float(s[i]) * w for i, (name, w) in self.dog_idx.items()}
        p = max(weighted.values()) if weighted else 0.0
        top = int(np.argmax(s))
        top_label = self.labels[top] if self.labels else str(top)
        return float(min(1.0, p)), {"top": top_label, "top_score": float(s[top]),
                                    **{k: round(v, 3) for k, v in weighted.items()}}


class HeuristicClassifier(Classifier):
    """Scores how bark-like a sound is from simple acoustics:
    sharp onset, short burst, energy concentrated 300-3000 Hz, partly harmonic."""
    name = "heuristic"

    def bark_probability(self, x):
        x = np.asarray(x, dtype=np.float32)
        p = F.power_spectrogram(x)
        freqs = np.fft.rfftfreq(F.N_FFT, 1.0 / F.SR)
        frame_db = 10 * np.log10(p.sum(axis=1) + 1e-10)
        peak = int(np.argmax(frame_db))
        pk = frame_db[peak]
        # burst length: frames within 10 dB of peak around the peak
        loud = frame_db > pk - 10
        l, r = peak, peak
        while l > 0 and loud[l - 1]:
            l -= 1
        while r < len(loud) - 1 and loud[r + 1]:
            r += 1
        burst = (r - l + 1) * F.HOP / F.SR
        # onset sharpness: rise over 30 ms before peak
        pre = frame_db[max(0, l - 3):l + 1]
        rise = pk - (pre.min() if len(pre) else pk)
        seg = p[l:r + 1].sum(axis=0)
        band = seg[(freqs >= 300) & (freqs <= 3000)].sum() / (seg.sum() + 1e-10)
        low = seg[freqs < 150].sum() / (seg.sum() + 1e-10)
        flat = float(np.exp(np.mean(np.log(seg + 1e-10))) / (np.mean(seg) + 1e-10))
        _, voiced = F.estimate_pitch(x)

        def ramp(v, a, b):
            return float(np.clip((v - a) / (b - a), 0, 1))

        s_burst = 1.0 - abs(np.clip(burst, 0.04, 1.2) - 0.25) / 0.95
        s_rise = ramp(rise, 4, 18)
        s_band = ramp(band, 0.35, 0.8)
        s_low = 1.0 - ramp(low, 0.25, 0.6)
        s_tone = 1.0 - ramp(flat, 0.25, 0.6)  # pure noise (claps, hiss) is very flat
        s_voice = ramp(voiced, 0.2, 0.6)
        score = (0.2 * s_burst + 0.15 * s_rise + 0.25 * s_band + 0.1 * s_low
                 + 0.15 * s_tone + 0.15 * s_voice)
        prob = float(np.clip((score - 0.35) / 0.45, 0, 1))
        # hard gates: barks live in the 300-3000 Hz band and aren't dominated by rumble/hum
        prob *= min(1.0, s_band * 2.0) * s_low
        return prob, {"burst_s": round(float(burst), 3), "band": round(float(band), 2),
                      "flatness": round(flat, 2), "voiced": round(float(voiced), 2)}


def make_classifier(mode: str, model_path: str) -> Classifier:
    if mode in ("auto", "yamnet"):
        if Path(model_path).exists():
            try:
                return YamnetClassifier(model_path)
            except Exception as e:  # noqa: BLE001
                if mode == "yamnet":
                    raise
                log.warning("YAMNet unavailable (%s); falling back to heuristic detector", e)
        elif mode == "yamnet":
            raise FileNotFoundError(f"YAMNet model not found at {model_path}; run scripts/download_model.sh")
        else:
            log.warning("No model at %s; using heuristic detector (run scripts/download_model.sh)",
                        model_path)
    return HeuristicClassifier()
