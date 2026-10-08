#!/usr/bin/env bash
# Serve Air Glass on http://127.0.0.1:8765 (camera access needs localhost or
# https) and open it. Bound to 127.0.0.1 only: nothing else on the network can
# reach it. Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
URL="http://127.0.0.1:${PORT}/${1:+?$1}"
( sleep 1; open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || echo "Open $URL" ) &
exec python3 -m http.server "$PORT" --bind 127.0.0.1
