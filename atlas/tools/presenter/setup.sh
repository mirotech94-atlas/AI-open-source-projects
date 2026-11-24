#!/bin/sh
# Install the presenter tools on the Mac (once): a venv with onnxruntime and
# numpy at ~/.cache/atlas-presenter/venv, and Robust Video Matting's ONNX
# model next to it, its sha256 checked. Nothing here goes to the server.
#
#     sh tools/presenter/setup.sh
#
# Then: ~/.cache/atlas-presenter/venv/bin/python tools/presenter/process.py ...
set -eu
HOME_DIR="${ATLAS_PRESENTER_HOME:-$HOME/.cache/atlas-presenter}"
PY="${PYTHON:-python3}"
MODEL_URL="https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
MODEL_SHA256="88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"

mkdir -p "$HOME_DIR"
[ -x "$HOME_DIR/venv/bin/python" ] || "$PY" -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q "onnxruntime==1.31.0" "numpy==2.5.4"

MODEL="$HOME_DIR/rvm_mobilenetv3_fp32.onnx"
if [ ! -f "$MODEL" ] || [ "$(shasum -a 256 "$MODEL" | cut -d' ' -f1)" != "$MODEL_SHA256" ]; then
    curl -sSLf -o "$MODEL.part" "$MODEL_URL"
    [ "$(shasum -a 256 "$MODEL.part" | cut -d' ' -f1)" = "$MODEL_SHA256" ] || {
        echo "the downloaded model is not the pinned one" >&2; rm -f "$MODEL.part"; exit 1; }
    mv "$MODEL.part" "$MODEL"
fi
command -v ffmpeg >/dev/null || { echo "ffmpeg is needed on PATH" >&2; exit 1; }
"$HOME_DIR/venv/bin/python" -c "import onnxruntime, numpy; print('presenter tools ready:', onnxruntime.__version__, numpy.__version__)"
