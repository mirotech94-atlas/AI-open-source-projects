#!/bin/bash
# Keeps the Mac's forward to the Spark's vLLM up: 127.0.0.1:18000 -> Spark :8000.
# One tick per launchd run (every 60 s). Repo copy of the `up` path of
# ~/.local/bin/spark-vllm.sh, which stays as is for the Spark tooling.
#
#   - port 18000 unbound            -> dial `ssh -f -N -L 18000:127.0.0.1:8000 spark`
#   - bound, /health answers        -> reset the failure counter
#   - bound, /health fails          -> count; on the 3rd consecutive tick kill ONLY
#                                      the ssh holding that exact -L, then redial.
#                                      One slow tick never kills.
# Other ssh sessions to the Spark (com.miro.spark-tunnel, spark-socks) are never touched.
# No key is read, passed or logged: ssh uses the `spark` host alias.
set -u
PORT=18000
FWD="$PORT:127.0.0.1:8000"
STRIKES=3
STATE_DIR="${ATLAS_TUNNEL_STATE_DIR:-$HOME/Library/Caches/atlas}"
COUNTER="$STATE_DIR/spark-tunnel-fails"

bound() { nc -z 127.0.0.1 "$PORT" 2>/dev/null; }
say() { printf '%s spark-tunnel: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*"; }

dial() {
  ssh -f -N -o BatchMode=yes -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L "$FWD" spark ||
    bound   # lost a race with another run: fine
}

healthy() { curl -s -m 15 -o /dev/null -f "http://127.0.0.1:$PORT/health"; }

fails() {
  local n
  n=$(cat "$COUNTER" 2>/dev/null) || n=0
  [[ "$n" =~ ^[0-9]+$ ]] || n=0
  echo "$n"
}
setfails() { mkdir -p "$STATE_DIR" && printf '%s\n' "$1" > "$COUNTER"; }

kill_stale() {
  # Only an ssh whose command line carries this exact forward; never our own pid.
  local pid i
  for pid in $(pgrep -f -- "^(/usr/bin/)?ssh .*-L ${FWD//./\\.}( |\$)" 2>/dev/null); do
    [[ "$pid" == "$$" ]] && continue
    kill "$pid" 2>/dev/null
  done
  for i in 1 2 3 4 5 6 7 8 9 10; do bound || break; sleep 0.5; done
}

if bound; then
  if healthy; then
    setfails 0
    exit 0
  fi
  n=$(( $(fails) + 1 ))
  if (( n < STRIKES )); then
    setfails "$n"
    say "health failed ($n/$STRIKES), forward kept"
    exit 2
  fi
  say "health failed $n ticks in a row, restarting the :$PORT forward"
  setfails 0
  kill_stale
fi

dial || { say "dial failed"; exit 2; }
healthy || { say "forward up, health not answering yet"; exit 2; }
exit 0
