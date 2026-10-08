#!/usr/bin/env bash
# Set the dashboard password, end to end, in one command.
#
# The password is read from your terminal, piped to the container on stdin, and
# only its argon2 hash is written to .env. The plaintext never appears in a
# command line, in shell history, in a process list or in a log.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/atlas}"
cd "$APP_DIR"

read -rsp "New dashboard password: " PW; echo
[[ ${#PW} -ge 12 ]] || { echo "too short — use at least 12 characters"; exit 1; }
read -rsp "Again: " PW2; echo
[[ "$PW" == "$PW2" ]] || { echo "they did not match"; exit 1; }

HASH=$(printf '%s' "$PW" | docker compose exec -T api python -c \
  'import sys; from argon2 import PasswordHasher; print(PasswordHasher().hash(sys.stdin.read()))')
unset PW PW2

[[ "$HASH" == \$argon2* ]] || { echo "hashing failed: $HASH"; exit 1; }

# Stored base64-encoded as DASHBOARD_PASSWORD_HASH_B64: a raw argon2 hash is
# mostly `$`, which Docker Compose's .env interpolation eats, and the B64 form
# is also the one auth.py reads FIRST -- writing only the raw line left an older
# B64 password in force (2026-09-28: "wrong password" after a reset).
python3 - "$HASH" <<'PY'
import base64, pathlib, sys
h = sys.argv[1].strip()
p = pathlib.Path(".env")
keep = [l for l in p.read_text().split("\n")
        if not l.startswith(("DASHBOARD_PASSWORD_HASH=", "DASHBOARD_PASSWORD_HASH_B64="))]
while keep and keep[-1] == "":
    keep.pop()
keep.append("DASHBOARD_PASSWORD_HASH_B64=" + base64.b64encode(h.encode()).decode())
p.write_text("\n".join(keep) + "\n")
PY
chmod 600 .env
unset HASH

echo "Password set. Restarting the API…"
docker compose up -d api >/dev/null
sleep 6
printf 'configured: '
curl -s http://127.0.0.1:8080/api/session | sed 's/.*"configured":\([a-z]*\).*/\1/'
echo
echo "Reach the dashboard over the mesh at http://<mesh-ip>:8080/"
echo "Until NetBird is up, tunnel from your laptop:"
echo "  ssh -N -L 8080:127.0.0.1:8080 root@${HOSTNAME:-atlas.example.com}"
echo "  then open http://localhost:8080"
