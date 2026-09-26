#!/usr/bin/env bash
# Download Pollen's duck detector (ONNX) once, for the room eye on Reachy's camera.
# This is a model file, like the ducks' own policy downloads; after this it is local.
#
#   scripts/get_detector.sh /var/lib/nest/models
set -euo pipefail
dest="${1:-/var/lib/nest/models}"
mkdir -p "$dest"
curl -fL -o "$dest/duck_detect.onnx" \
  "https://huggingface.co/pollen-robotics/microduck-duck-detector/resolve/main/duck_detect.onnx"
echo "saved $dest/duck_detect.onnx"
