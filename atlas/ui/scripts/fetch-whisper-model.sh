#!/usr/bin/env bash
# Fetch whisper-tiny.en (ONNX) for the on-device wake word -- the model the
# browser runs in a Web Worker to hear "atlas" without sending any audio.
#
#   ./ui/scripts/fetch-whisper-model.sh
#
# Then set VOICE_LOCAL_WAKE=true. /api/voice/capabilities reports
# `local_wake` only when the flag is set AND every file the worker needs is
# here (atlas/api/voice.py, WHISPER_FILES).
#
# Not committed: the files are ~75MB and reproducible from a public URL.
# The revision is pinned, so a later push to the model repo cannot change
# what ATLAS runs without this line changing too. The ONNX runtime itself is
# NOT fetched: Vite bundles its wasm from node_modules into dist/assets/.
#
# Files are downloaded into a temporary directory and moved into place only
# when all of them arrived, so an interrupted run never leaves behind a
# directory that looks ready and is not.
#
# As with the hand model, SHA-256s are printed rather than checked against a
# published list, because there is none to check against -- printing them
# lets Miro record the values and compare them later.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_DIR="$(cd "$HERE/.." && pwd)"
DEST="$UI_DIR/public/wake/whisper"
REPO="onnx-community/whisper-tiny.en"
REVISION="2575352d61be1bf7225cf8f8b268a4678025fc58"   # 2024-10-08
BASE="https://huggingface.co/$REPO/resolve/$REVISION"

# What the worker loads. The 8-bit set is what wasm runs (and what the server
# requires); encoder_model.onnx is the full-precision encoder WebGPU uses --
# without it the worker simply falls back to wasm.
FILES=(
  config.json
  generation_config.json
  preprocessor_config.json
  tokenizer.json
  tokenizer_config.json
  special_tokens_map.json
  added_tokens.json
  vocab.json
  merges.txt
  normalizer.json
  onnx/encoder_model_quantized.onnx
  onnx/decoder_model_merged_quantized.onnx
  onnx/encoder_model.onnx
)

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/atlas-whisper.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/onnx"

for file in "${FILES[@]}"; do
  echo "Fetching $file..."
  curl -fL --progress-bar -o "$STAGE/$file" "$BASE/$file"
done

chmod 755 "$STAGE" "$STAGE/onnx"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
mv "$STAGE" "$DEST"
trap - EXIT

echo
echo "Fetched into $DEST:"
for file in "${FILES[@]}"; do
  path="$DEST/$file"
  size="$(du -h "$path" | awk '{print $1}')"
  sum="$(shasum -a 256 "$path" | awk '{print $1}')"
  printf '  %-42s %6s  %s\n' "$file" "$size" "$sum"
done
echo "  total $(du -sh "$DEST" | awk '{print $1}')"
echo
echo "Git-ignored. Re-run after a fresh clone. Set VOICE_LOCAL_WAKE=true to use it."
echo "Run this BEFORE 'npm run build' (or the Docker build): Vite copies"
echo "public/wake/whisper/ into dist/, and the browser loads it from there."
