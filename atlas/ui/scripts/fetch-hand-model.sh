#!/usr/bin/env bash
# Fetch the on-device hand-tracking model that pinch-to-wake needs, plus the
# MediaPipe wasm runtime it loads alongside it.
#
#   ./ui/scripts/fetch-hand-model.sh
#
# Neither is committed: the model is ~7.5MB and the wasm runtime is bundled
# inside @mediapipe/tasks-vision, so both are reproducible from a public URL
# and node_modules rather than carried as binaries in this repo. Run this
# once after `npm install`, and again any time node_modules is reinstalled.
#
# Why the SHA-256 is printed rather than checked: there is no published
# checksum from Google to verify against, so printing it is the honest thing
# to do -- it lets Miro record and later compare a value, rather than this
# script pretending to have verified something it cannot.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_DIR="$(cd "$HERE/.." && pwd)"
WAKE_DIR="$UI_DIR/public/wake"
MODEL_URL="https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_PATH="$WAKE_DIR/hand_landmarker.task"
WASM_SRC="$UI_DIR/node_modules/@mediapipe/tasks-vision/wasm"
WASM_DEST="$WAKE_DIR/wasm"

mkdir -p "$WAKE_DIR"

echo "Fetching hand_landmarker.task..."
curl -fL --progress-bar -o "$MODEL_PATH" "$MODEL_URL"

echo "SHA-256: $(shasum -a 256 "$MODEL_PATH" | awk '{print $1}')"

if [ ! -d "$WASM_SRC" ]; then
    echo "error: $WASM_SRC not found -- run 'npm install' in ui/ first" >&2
    exit 1
fi

echo "Copying the MediaPipe wasm runtime into $WASM_DEST..."
rm -rf "$WASM_DEST"
cp -R "$WASM_SRC" "$WASM_DEST"

echo "Done. $MODEL_PATH and $WASM_DEST are git-ignored -- re-run this script"
echo "after a fresh clone or a node_modules reinstall."
