#!/usr/bin/env bash
# Google AIY Voice HAT (Voice Kit V1, the larger black board) as BarkTracker's microphone.
# Its two ICS-43432 MEMS mics sit on the small daughter board, on one I2S clock, so the
# direction test (Settings > System > Developer) can time sounds between them.
#
#   ./scripts/setup-voicehat.sh          enable the sound card driver (then reboot)
#   ./scripts/setup-voicehat.sh check    after rebooting: is the card there, are both mics live?
set -euo pipefail
cd "$(dirname "$0")/.."
CFG=/boot/firmware/config.txt
[[ -f $CFG ]] || CFG=/boot/config.txt

if [[ "${1:-}" == "check" ]]; then
  echo "==> Capture devices"
  arecord -l || true
  CARD=$(arecord -l 2>/dev/null | awk -F'[: ]+' 'tolower($0) ~ /voice/ {print $2; exit}')
  if [[ -z "$CARD" ]]; then
    echo "No Voice HAT sound card found. Is dtoverlay=googlevoicehat-soundcard in $CFG, and did you reboot?"; exit 1
  fi
  echo "==> Recording 3 s from card $CARD (both channels, 48 kHz)"
  T=$(mktemp --suffix=.wav)
  arecord -D "plughw:$CARD,0" -c 2 -r 48000 -f S16_LE -d 3 "$T" 2>/dev/null
  .venv/bin/python - "$T" <<'PY'
import sys, wave, numpy as np
with wave.open(sys.argv[1]) as w:
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels()) / 32768
for c in range(x.shape[1]):
    lv = 20 * np.log10(np.sqrt(np.mean(x[:, c] ** 2)) + 1e-9)
    print(f"   mic {'AB'[c]}: {lv:6.1f} dBFS" + ("   <- silent: check the mic board cable" if lv < -85 else ""))
PY
  rm -f "$T"
  echo
  echo "Next: Settings > System > Microphone > pick the Voice HAT."
  echo "For direction: Settings > System > Developer > Dual-microphone direction test,"
  echo "then on the Direction page choose Stereo and 'AIY Voice HAT: 7.1 cm'."
  exit 0
fi

if grep -q "^dtoverlay=googlevoicehat-soundcard" "$CFG"; then
  echo "==> $CFG already enables the Voice HAT"
else
  sudo cp "$CFG" "$CFG.bak-$(date +%Y%m%d%H%M%S)"
  echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a "$CFG" >/dev/null
  echo "==> Added dtoverlay=googlevoicehat-soundcard to $CFG (a copy of the old one is next to it)"
fi
echo "Reboot, then run:  ./scripts/setup-voicehat.sh check"
