#!/usr/bin/env bash
# Downloads Google's YAMNet audio classifier (TFLite, Apache 2.0, ~4 MB) into models/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
OUT=models/yamnet.tflite
if [[ -s "$OUT" ]]; then echo "YAMNet already present at $OUT"; exit 0; fi

URLS=(
  "https://storage.googleapis.com/download.tensorflow.org/models/tflite/task_library/audio_classification/android/lite-model_yamnet_classification_tflite_1.tflite"
  "https://raw.githubusercontent.com/tensorflow/tflite-support/master/tensorflow_lite_support/cc/test/testdata/task/audio/yamnet_audio_classifier_with_metadata.tflite"
)
for u in "${URLS[@]}"; do
  echo "Downloading YAMNet from ${u%%/models/*}..."
  if curl -fsSL --retry 3 -o "$OUT.tmp" "$u" && [[ $(stat -c%s "$OUT.tmp") -gt 1000000 ]]; then
    mv "$OUT.tmp" "$OUT"
    echo "Saved $OUT"
    exit 0
  fi
  rm -f "$OUT.tmp"
done
echo "Could not download YAMNet. Barktracker will run with the basic detector." >&2
exit 1
