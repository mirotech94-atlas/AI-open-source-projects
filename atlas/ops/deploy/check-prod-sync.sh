#!/bin/bash
# Read-only: is the CD path on the ATLAS host the one reviewed in git?
# Compares the host's /opt/atlas/bootstrap/deploy/*.sh with ops/deploy/SHA256SUMS.
# Changes nothing on either side. Needs an ordinary root SSH login to the host
# (the CD key cannot do this: it may run only "deploy <sha>").
#   ops/deploy/check-prod-sync.sh [user@host]
set -euo pipefail
HOST="${1:-root@203.0.113.191}"
HERE="$(cd "$(dirname "$0")" && pwd)"
drift=0
while read -r want name; do
  got=$(ssh -n -o BatchMode=yes "$HOST" "sha256sum /opt/atlas/bootstrap/deploy/$name" | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then
    echo "same     $name"
  else
    echo "DRIFTED  $name  host=$got git=$want"; drift=1
  fi
done < "$HERE/SHA256SUMS"
exit "$drift"
