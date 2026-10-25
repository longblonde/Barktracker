# Barklog

A Raspberry Pi that listens for barking, logs when it happens and for how long, saves a clip of each episode, and learns to tell your dogs apart. Everything runs on the Pi; the dashboard is a web page on your home network.

![Overview](docs/overview.png)

## What it does

- **Automatic detection.** A cheap loudness gate wakes Google's [YAMNet](https://www.kaggle.com/models/google/yamnet) sound classifier only when something loud happens, so the Pi idles in a quiet house and still runs a proper model on every candidate sound.
- **Episodes, not just barks.** Barks less than 8 s apart (configurable) are grouped into one episode with a start time, duration, bark count and peak level.
- **Clips for review.** Each episode is saved as a WAV with 2 s of lead-in and lead-out. The clip page shows a spectrogram with every bark marked; click a bark to hear it alone.
- **Which dog.** Label a few barks per dog in the dashboard and new barks are matched to the closest voice automatically. Mark false alarms as "Not a bark" and similar sounds get ignored from then on.
- **Dashboard.** Plain-language daily summary, a 24-hour timeline coloured by dog, two-week totals, an hour-by-weekday heatmap, clip list with filters, CSV export.
- **Unattended.** systemd service, crash recovery for half-written clips, clip retention and a disk cap, optional login, optional webhook (ntfy, Home Assistant) for long episodes.

![Clip review](docs/clip.png)

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 4 or 5 (2 GB is plenty) | A Pi 3B+ or Zero 2 W works too; YAMNet just takes longer per check. Use **64-bit** Raspberry Pi OS for the model runtime. |
| USB microphone | Any class-compliant USB mic or USB sound card + mic. Omnidirectional conference mics work well. |
| microSD 16 GB+ or USB SSD | About 2 MB per minute of clip audio. |

Put the mic where the dogs bark (near the front window or door), away from fans and the fridge.

## Install

```bash
git clone <this repo> barklog && cd barklog
./scripts/install.sh
```

The installer sets up a Python venv, the TFLite runtime, downloads YAMNet (~4 MB), writes `config.yaml` with your Pi's time zone, and starts the `barklog` service. Open `http://<pi-address>:8080`.

### Check the microphone

```bash
sudo systemctl stop barklog
.venv/bin/python -m barklog devices     # find your mic
.venv/bin/python -m barklog level       # live meter; bark or clap near it
sudo systemctl start barklog
```

A quiet room should read around -60 to -45 dBFS and a bark well above -30. If the mic isn't the default input, set `audio.device` in `config.yaml` to part of its name (e.g. `USB`). The **System** page in the dashboard shows the live level, the learned background noise and the trigger line.

## Using it

1. **Add your dogs** on the Dogs page.
2. **Review clips.** Open a clip, then either label the whole clip ("Who's barking in this clip?") or select individual barks on the spectrogram (shift-select for several) and label those. Clips with two dogs are best labelled bark by bark.
3. After **3+ labelled barks per dog**, new barks are matched automatically and older unlabelled barks are re-matched. Barks it isn't sure about stay "Unidentified". More labels from different days and distances make it better.
4. **Mark false alarms** as "Not a bark". They drop out of the stats immediately, and after a few examples, similar sounds are filtered out before they're logged.

## How detection works

```
mic ─► 50 ms frames ─► noise floor tracker + onset gate
                              │ (only on a sudden loud sound)
                              ▼
               ~1 s window ─► YAMNet ─► P(bark) ≥ 0.35 ? ─► dog matcher ─► episode + clip
```

- The noise floor adapts downward quickly and upward slowly, so a fan switching on raises the threshold over a few minutes without hiding barks.
- YAMNet scores 521 AudioSet classes; Barklog combines *Bark*, *Bow-wow*, *Yip*, *Dog*, *Howl*, *Growling* and *Whimper (dog)* with weights. In testing on real recordings it caught all dog clips and ignored birds, rain and fireworks.
- Without YAMNet (32-bit OS, no runtime) a simple acoustic detector is used. It catches barks but also some claps, door slams and fireworks; the "Not a bark" filter helps.
- Dog matching uses a per-bark voice descriptor (spectral shape, brightness, pitch, length) and a nearest-neighbour vote over your labels. It works well for dogs that sound different (big vs. small, different breeds). Two dogs of the same breed and size will often stay unidentified; that's a limit of the audio, not something more labels fully fix.

## Configuration

Everything is in `config.yaml`, with comments: see [`config.example.yaml`](config.example.yaml). Common tweaks:

| Want | Change |
|---|---|
| Fewer false alarms | `detection.bark_threshold: 0.5`, or `min_episode_barks: 2` |
| Catch distant/quiet barks | lower `trigger_db` to 6–8, add `audio.gain_db: 6` |
| Split episodes more finely | `detection.episode_gap: 4` |
| Keep clips longer | `clips.retention_days`, `clips.max_disk_mb` |
| Require login | `web.username` and `web.password` |
| Phone alert for long barking | `notify.webhook_url: https://ntfy.sh/<your-topic>` |

Restart after editing: `sudo systemctl restart barklog`.

## Command line

```bash
python -m barklog run                  # what the service runs: listen + dashboard
python -m barklog run --file x.wav     # replay a recording through the live pipeline
python -m barklog ingest x.wav --start 2026-10-24T08:00   # log an existing recording
python -m barklog devices | level | retrain
python -m barklog demo                 # two weeks of synthetic barking to try the dashboard
```

## API

The dashboard uses a small JSON API, documented at `/api/docs`. Useful bits: `/api/status`, `/api/summary?days=14`, `/api/episodes`, `/api/episodes/{id}/audio`, `/api/export.csv`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx ai-edge-litert
./scripts/download_model.sh
.venv/bin/python -m pytest
.venv/bin/python -m barklog -c config.example.yaml demo && .venv/bin/python -m barklog -c config.example.yaml run --no-audio
```

No build step: the dashboard is plain HTML/CSS/JS in `barklog/static/` and works without internet (the font is bundled).

## Credits

YAMNet by Google (Apache 2.0). Bricolage Grotesque by Mathieu Triay (SIL OFL 1.1, `barklog/static/fonts/OFL.txt`).
