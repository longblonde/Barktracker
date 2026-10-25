"""Configuration loading. Defaults are tuned for a Pi 4/5 with a USB microphone."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AudioConfig:
    device: str | int | None = "auto"    # auto = first USB webcam/mic (Logitech etc.); None = system default;
                                         # or a name substring / index
    samplerate: int = 16000              # capture rate; falls back to the device's native rate if refused
    channels: int = 0                    # 0 = auto (webcams are stereo; mixed to mono)
    block_seconds: float = 0.05          # 50 ms analysis frames
    gain_db: float = 0.0                 # software gain applied before analysis


@dataclass
class DetectionConfig:
    mode: str = "auto"                   # auto | yamnet | heuristic
    model_path: str = "models/yamnet.tflite"
    trigger_db: float = 10.0             # frame must exceed noise floor by this much to wake the classifier
    min_level_dbfs: float = -55.0        # ignore anything quieter than this regardless of floor
    bark_threshold: float = 0.35         # classifier probability needed to count a bark
    min_bark_interval: float = 0.25      # seconds between counted bark onsets
    classify_cooldown: float = 0.30      # min seconds between classifier runs during sustained noise
    episode_gap: float = 8.0             # barks closer than this merge into one episode
    min_episode_barks: int = 1           # episodes with fewer barks are discarded
    noise_floor_adapt: float = 0.02      # EMA rate for noise floor tracking (per frame)
    suppress_learned_false_positives: bool = True


@dataclass
class ClipConfig:
    preroll: float = 2.0
    postroll: float = 2.0
    max_clip_seconds: float = 300.0
    retention_days: int = 60
    max_disk_mb: int = 4000


@dataclass
class IdentifyConfig:
    enabled: bool = True
    k: int = 7
    min_labels_per_dog: int = 3
    min_confidence: float = 0.55


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    username: str | None = None          # set both to enable HTTP basic auth
    password: str | None = None


@dataclass
class NotifyConfig:
    webhook_url: str | None = None       # POST JSON on episode end (ntfy, Home Assistant, etc.)
    min_duration: float = 30.0           # only notify for episodes at least this long


@dataclass
class UiConfig:
    media_dir: str | None = None         # folder holding quiet/, active/ and bark/ pictures; default <data_dir>/media
    bark_words: bool = True              # red BARK words over the live band when a bark is detected


@dataclass
class Config:
    data_dir: str = "data"
    timezone: str | None = None          # e.g. America/Chicago; None = system local time
    audio: AudioConfig = field(default_factory=AudioConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    clips: ClipConfig = field(default_factory=ClipConfig)
    identify: IdentifyConfig = field(default_factory=IdentifyConfig)
    web: WebConfig = field(default_factory=WebConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def clips_path(self) -> Path:
        return self.data_path / "clips"

    @property
    def media_path(self) -> Path:
        return Path(self.ui.media_dir).expanduser().resolve() if self.ui.media_dir else self.data_path / "media"

    @property
    def db_path(self) -> Path:
        return self.data_path / "barktracker.db"


def _merge(dc: Any, values: dict) -> Any:
    known = {f.name: f for f in fields(dc)}
    for key, val in (values or {}).items():
        if key not in known:
            raise ValueError(f"Unknown config key: {key}")
        cur = getattr(dc, key)
        if is_dataclass(cur) and isinstance(val, dict):
            _merge(cur, val)
        else:
            setattr(dc, key, val)
    return dc


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    candidates = [path] if path else [os.environ.get("BARKTRACKER_CONFIG"), "config.yaml"]
    for cand in candidates:
        if cand and Path(cand).exists():
            with open(cand) as fh:
                _merge(cfg, yaml.safe_load(fh) or {})
            base = Path(cand).resolve().parent
            # Relative paths in a config file are relative to that file.
            if not Path(cfg.data_dir).expanduser().is_absolute():
                cfg.data_dir = str(base / cfg.data_dir)
            if not Path(cfg.detection.model_path).expanduser().is_absolute():
                cfg.detection.model_path = str(base / cfg.detection.model_path)
            break
    return cfg


def clone(cfg: Config) -> Config:
    return copy.deepcopy(cfg)
