# Barktracker

A Raspberry Pi 4 that listens for barking, logs when it happens and for how long, saves a clip of each episode, and learns to tell dogs apart. When a neighbor's dog is the problem, it turns the log into a clean, printable complaint report (or CSV) that cites your local noise ordinance. Everything runs on the Pi; the dashboard is a web page on your home network.

![Overview](docs/overview.png)

## What it does

- **Automatic detection.** A cheap loudness gate wakes Google's [YAMNet](https://www.kaggle.com/models/google/yamnet) sound classifier only when something loud happens, so the Pi idles in a quiet house and still runs a proper model on every candidate sound.
- **Episodes, not just barks.** Barks less than 8 s apart (configurable) are grouped into one episode with a start time, duration, bark count and peak level.
- **Clips for review.** Each episode is saved as a WAV with 2 s of lead-in and lead-out. The clip page shows a spectrogram with every bark marked; click a bark to hear it alone.
- **Which dog.** Label a few barks per dog in the dashboard and new barks are matched to the closest voice automatically. Mark false alarms as "Not a bark" and similar sounds get ignored from then on.
- **Live band on every page.** A picture that matches what's happening (a napping dog during quiet hours, a sitting dog in the daytime or when there's noise without barking, a barking dog the moment a bark is detected) next to a live EQ of the microphone. Each column's top cell is the bright peak and the stack under it is half strength, so the tops read as a line of sound; bars fall with gravity and peaks hang briefly. It's monotone in the site's colors normally and switches to caution yellow while a dog is barking, with optional red BARK words popping in at random sizes and angles. It updates 6 times a second while quiet and 20 while barking. Use your own photos, GIFs or video loops. Click the picture for a full-screen view you can leave on a spare screen or tablet.
- **Dashboard.** Plain-language daily summary, a 24-hour timeline coloured by dog, two-week totals, an hour-by-weekday heatmap, clip list with filters.
- **Your local ordinance.** Enter the animal-noise rule that applies where you live (Burnsville, MN is the default) and the criteria for incidents worth reporting: sustained barking, barking in quiet hours, frequent days, habitual patterns.
- **Complaint reports.** Per dog or per neighbor's household, for any date range: print or save as PDF, download a clean CSV, or download every clip plus the CSVs as a zip for evidence.
- **Unattended.** systemd service, crash recovery for half-written clips, clip retention and a disk cap, optional login, optional webhook (ntfy, Home Assistant) for long episodes.

![Clip review](docs/clip.png)

![Complaint report](docs/report.png)

![Settings](docs/settings.png)

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 4 (2 GB or more) | The target. Use **64-bit** Raspberry Pi OS (Bookworm or later, Lite is fine) so the YAMNet runtime installs. A YAMNet check takes tens of milliseconds and only runs on loud sounds, so the Pi stays mostly idle. |
| USB webcam with a microphone, e.g. Logitech C920 / C922 / Brio | Picked automatically (`audio.device: auto`). The stereo mics are mixed to mono, and if the webcam won't record at 16 kHz Barktracker uses its native rate (often 32 or 48 kHz) and resamples. Any other USB mic works too. |
| microSD 32 GB+ or USB SSD | About 2 MB per minute of clip audio. |
| Official Pi 4 power supply | Webcams draw extra current; under-powered Pis drop USB audio. |

Point the webcam toward where the barking comes from (a window facing the neighbor's yard works well), away from fans and the fridge. Webcams apply automatic gain, so leave `gain_db` at 0 unless barks read quieter than -35 dBFS in Settings → System.

Timestamps matter for complaints. The Pi 4 has no battery-backed clock, so keep it on the network; Settings → System and every report say whether the clock is synchronized to internet time.

## Install

```bash
git clone <this repo> barktracker && cd barktracker
./scripts/install.sh
```

The installer sets up a Python venv, the TFLite runtime, downloads YAMNet (~4 MB), writes `config.yaml` with your Pi's time zone, and starts the `barktracker` service. Open `http://<pi-address>:8080`.

### Check the microphone

```bash
sudo systemctl stop barktracker
.venv/bin/python -m barktracker devices     # find your mic
.venv/bin/python -m barktracker level       # live meter; bark or clap near it
sudo systemctl start barktracker
```

A quiet room should read around -60 to -45 dBFS and a bark well above -30. With several USB audio devices plugged in, set `audio.device` in `config.yaml` to part of the webcam's name (e.g. `C920`). The **System** section of Settings shows the live level, the learned background noise and the trigger line.

## Using it

1. **Add your dogs** on the Dogs page.
2. **Review clips.** Open a clip, then either label the whole clip ("Who's barking in this clip?") or select individual barks on the spectrogram (shift-select for several) and label those. Clips with two dogs are best labelled bark by bark.
3. After **3+ labelled barks per dog**, new barks are matched automatically and older unlabelled barks are re-matched. Barks it isn't sure about stay "Unidentified". More labels from different days and distances make it better.
4. **Mark false alarms** as "Not a bark". They drop out of the stats immediately, and after a few examples, similar sounds are filtered out before they're logged.

## Settings

Everything adjustable lives on the **Settings** page and takes effect immediately, no restart needed:

- **Storage:** disk usage (clips, log, system, free space), how much audio recent barking adds per day and how many days the cap holds, clip retention, disk cap, clip length, pre and post roll.
- **Detection:** bark confidence, loudness trigger, quiet floor, microphone boost, episode gap, smallest episode, ignoring learned false alarms.
- **Code parameters:** quiet hours, sustained-barking minutes, allowed break, frequent-day total, habitual pattern.
- **Ordinance and contacts:** rule text, code section, source, penalty, where to report, and your details for reports.
- **Live band:** turn the BARK words on or off, and add or remove the nap, sit and bark pictures.
- **Alerts and dog matching:** webhook, alert threshold, matching confidence.
- **System:** live mic level, detector status, clock sync.

Dashboard changes are stored on the Pi as overrides of `config.yaml`; "Restore config.yaml values" undoes them.

## Heartbeat pictures

The band shows one of three pictures:

| Picture | When | Folder |
|---|---|---|
| Napping | Quiet hours (from Code parameters) and the room is calm | `data/media/quiet/` |
| Sitting | Daytime, or noise at least 8 dB above the room's background (or louder than -38 dBFS) without barking | `data/media/active/` |
| Barking | A bark is detected, for as long as the episode lasts | `data/media/bark/` |

Switching between napping and sitting waits 4 seconds so a passing car doesn't flip it; barking switches immediately. Put your own files in those folders or upload them on Settings; if a folder has several, one is picked at random. JPG, PNG, GIF, WebP, SVG, MP4 and WebM all work. Until you add your own, the built-in drawings are used.

## Noise ordinance

The **Code parameters** and **Ordinance and contacts** sections of Settings hold the rule your reports cite and the criteria used to flag incidents. It starts with Burnsville, MN:

- **Rule:** Burnsville City Code [§ 6-2-7](https://burnsville.municipalcodeonline.com/book?type=ordinances#name=6-2-7_Obligation_To_Prevent_Nuisances) makes it a nuisance for an animal to habitually or frequently bark, cry, howl or whimper so it disturbs others. It sets **no minute count and no animal-specific quiet hours**.
- **Contacts:** police non-emergency dispatch for noise complaints (952-322-2323, 24/7), Burnsville Animal Control at American Boarding Kennels (952-894-3647), City Hall (952-895-4400).
- **Documentation criteria (editable):** because the code has no numbers, these are starting points for which incidents to highlight, not legal limits:

| Criterion | Default |
|---|---|
| Sustained | 10 min or more, with no break longer than 60 s |
| Quiet hours | 5 min or more between 10 PM and 7 AM (Burnsville's construction and power-equipment hours, § 7-1-3) |
| Frequent day | 30 min or more of barking in one day |
| Habitual pattern | 3 or more days meeting a criterion within 7 days |

Change any of it for another city on the Settings page: jurisdiction, code section, the rule in plain words, source link, penalty, contacts and criteria are all editable, and "Reset to preset" restores Burnsville. Check the current code before filing; ordinances change.

## Complaint reports

1. On **Dogs**, add each neighbor's dog with its owner, address and a short description. Dogs at the same address form one household.
2. Label some clips so the dog is identified (see above).
3. On **Report**, choose a dog or a neighbor's address and a date range. Options: only incidents meeting your criteria, and only barks you've personally confirmed (leaving out automatic voice matches).
4. **Print or save as PDF** for a signed log, **Download CSV** for a spreadsheet (one clean table, opens directly in Excel or Sheets), **Daily totals CSV**, or **Clips and CSV (.zip)** with every incident's audio named by incident number.

The printed report includes: who and what the complaint is about, the period, your contact details (entered once on Settings), the ordinance text and source, a summary, the criteria with counts, daily totals, a numbered incident log (start, end, length, barks, whether a person confirmed the dog, criteria met, your notes), how the recording was made, where to report, and a signature line.

## How detection works

```
mic ─► 50 ms frames ─► noise floor tracker + onset gate
                              │ (only on a sudden loud sound)
                              ▼
               ~1 s window ─► YAMNet ─► P(bark) ≥ 0.35 ? ─► dog matcher ─► episode + clip
```

- The noise floor adapts downward quickly and upward slowly, so a fan switching on raises the threshold over a few minutes without hiding barks.
- YAMNet scores 521 AudioSet classes; Barktracker combines *Bark*, *Bow-wow*, *Yip*, *Dog*, *Howl*, *Growling* and *Whimper (dog)* with weights. In testing on real recordings it caught all dog clips and ignored birds, rain and fireworks.
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

Restart after editing: `sudo systemctl restart barktracker`.

## Command line

```bash
python -m barktracker run                  # what the service runs: listen + dashboard
python -m barktracker run --file x.wav     # replay a recording through the live pipeline
python -m barktracker ingest x.wav --start 2026-10-24T08:00   # log an existing recording
python -m barktracker devices | level | retrain
python -m barktracker demo                 # two weeks of synthetic barking to try the dashboard
```

## API

The dashboard uses a small JSON API, documented at `/api/docs`. Useful bits: `/api/status`, `/api/summary?days=14`, `/api/episodes`, `/api/episodes/{id}/audio`, `/api/report?dog=1&from=2026-10-01&to=2026-10-31`, `/api/report.csv` (same parameters, plus `kind=daily`), `/api/report/clips.zip`, `/api/ordinance`, `/api/settings`, `/api/live` (or `/api/live/stream` for server-sent events: 6 per second while quiet, 20 while barking), `/api/media`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx ai-edge-litert
./scripts/download_model.sh
.venv/bin/python -m pytest
.venv/bin/python -m barktracker -c config.example.yaml demo && .venv/bin/python -m barktracker -c config.example.yaml run --no-audio
```

No build step: the dashboard is plain HTML/CSS/JS in `barktracker/static/` and works without internet (the font is bundled).

## Credits

YAMNet by Google (Apache 2.0). Bricolage Grotesque by Mathieu Triay (SIL OFL 1.1, `barktracker/static/fonts/OFL.txt`).
