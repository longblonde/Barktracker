# Microphones tested

Starting settings for microphones BarkTracker has been run with, and how to work them out for a new
one. Levels are in dBFS (0 = the loudest the microphone can record). Settings are under
**Settings → Microphone and detection** unless noted.

## Summary

| Microphone | Channels | Microphone boost | Ignore quieter than | Notes |
|---|---|---|---|---|
| [Google AIY Voice HAT v1](#google-aiy-voice-hat-v1) | Stereo, 7.1 cm | **+18 dB** | **-62 dBFS** | No hardware gain: needs the boost. |

Add a row (and a section below) when you test another microphone; see [Adding a microphone](#adding-a-microphone).

## Google AIY Voice HAT v1

Tested 2026-10-30 on a Raspberry Pi 4 (4 GB), 64-bit Raspberry Pi OS, BarkTracker v0.15 (`refactor`).

**Setup:** `dtoverlay=googlevoicehat-soundcard` (see the README's Voice HAT section). It records
2 channels at 48 kHz, 32-bit (`S32_LE`); BarkTracker picks it automatically and resamples to 16 kHz.

**Starting settings**

| Setting | Value | Why |
|---|---|---|
| Channels | Stereo, **AIY Voice HAT: 7.1 cm** | Two MEMS mics on one clock: good for direction. |
| Microphone boost | **+18 dB** | See below. Use +12 dB if dogs bark close to the Pi. |
| Ignore quieter than | **-62 dBFS** | Background is around -58 dBFS after the boost. |
| Bark threshold (Tuning) | 0.35 (default) | This build is trialling 0.15; raise it if there are false alarms. |

**Why it needs a boost.** The board has no analog gain and the driver has no mixer controls
(`amixer -c <card> contents` is empty), so there is nothing to turn up in ALSA. Its digital MEMS
microphones put very loud sounds near full scale and leave everything else low: a dog across a yard
records around -45 dBFS. That's the design, not a fault. The boost is applied to the full 32-bit
capture before clips are saved as 16-bit, so it recovers real detail rather than amplifying noise.

**Measured at 0 dB boost**

| Sound | Level |
|---|---|
| Background (quiet room, window toward the yards) | -76 to -80 dBFS |
| Neighbourhood barks, peak | -32 to -47 dBFS (median -44) |
| Hand clap at arm's length, peak | -6 to -8 dBFS |
| Hand clap at 30 cm, peak | -5.6 dBFS |
| Mic A vs mic B, same sounds | within about 1.5 dB |

At +18 dB, distant barks peak around -14 to -29 dBFS. Sounds right at the board (claps, a door, people
talking next to it) clip; at +12 dB they mostly don't, but quiet barks use fewer bits in the clip.

## Adding a microphone

1. Set the boost to 0 and leave **Ignore quieter than** at its default (-55 dBFS).
2. On the **Detection and tuning** page, note the background level on a quiet day.
3. Wait for real barking (or play some from the distance the dogs usually are) and note the peaks;
   the clip files work too.
4. Clap once at arm's length, then press **I hear barking** to save the last 30 seconds, and note
   the clap's peak. Delete the saved sound afterwards.
5. Choose a boost that puts typical barks peaking around -15 to -30 dBFS, as long as the background
   stays below about -50 dBFS. A webcam with automatic gain usually needs none.
6. Set **Ignore quieter than** to about the background level after the boost, minus 4 dB.
7. Copy the template below, fill it in and add a row to the summary table.

```markdown
## <Make and model>

Tested <date> on <Pi model>, <OS>, BarkTracker <version>.

**Setup:** <driver / overlay / USB; channels and rate it records at>

**Starting settings**

| Setting | Value | Why |
|---|---|---|
| Channels | | |
| Microphone boost | | |
| Ignore quieter than | | |
| Bark threshold (Tuning) | | |

**Measured at 0 dB boost**

| Sound | Level |
|---|---|
| Background | |
| Barks, peak | |
| Hand clap at arm's length, peak | |
```
