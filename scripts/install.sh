#!/usr/bin/env bash
# Barktracker installer for Raspberry Pi OS (Bookworm / Trixie, 64-bit recommended).
# Run from the repo folder:  ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev libportaudio2 alsa-utils curl

echo "==> Python environment (.venv)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> TFLite runtime for the YAMNet bark model"
if .venv/bin/pip install -q ai-edge-litert 2>/dev/null; then
  echo "    installed ai-edge-litert"
elif .venv/bin/pip install -q tflite-runtime 2>/dev/null; then
  echo "    installed tflite-runtime"
else
  echo "    !! No TFLite runtime wheel for this Python/CPU. Barktracker will use the basic detector."
  echo "       (64-bit Raspberry Pi OS is needed for the YAMNet model.)"
fi
./scripts/download_model.sh || true

if [[ ! -f config.yaml ]]; then
  cp config.example.yaml config.yaml
  TZ_NAME="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  [[ -n "$TZ_NAME" ]] && sed -i "s|^timezone: null.*|timezone: $TZ_NAME|" config.yaml
  echo "==> Created config.yaml"
fi
mkdir -p data/media/quiet data/media/active data/media/bark   # your own nap / sit / bark pictures
sudo usermod -aG audio "$USER_NAME" || true

echo "==> systemd service"
sed -e "s|@DIR@|$DIR|g" -e "s|@USER@|$USER_NAME|g" scripts/barktracker.service \
  | sudo tee /etc/systemd/system/barktracker.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now barktracker

PORT=$(grep -E '^\s*port:' config.yaml | awk '{print $2}' | head -1)
IP=$(hostname -I | awk '{print $1}')
echo
echo "Barktracker is running:  http://${IP}:${PORT:-8080}"
echo "Logs:                journalctl -u barktracker -f"
echo "Mic check:           sudo systemctl stop barktracker && .venv/bin/python -m barktracker level"
