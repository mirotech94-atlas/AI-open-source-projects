#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Copy the ATLAS Academy product files from Miro's vault to the ATLAS host's
# private files directory, where the buyer's access page serves them from.
#
#   bootstrap/academy-files.sh            # dry run: says what would be copied
#   bootstrap/academy-files.sh --go       # copies
#
# Why not git: the PDFs and zips are paid products (and *.pdf is gitignored),
# and data/ is excluded from the CD rsync, so they never reach the host with
# a deploy. This script is the one path, run by hand from the Mac.
#
# Where they go: /opt/atlas/data/app/academy/files/ on the host, which is
# /app/data/academy/files in the api container (ACADEMY_FILES_DIR's default).
# NOT under data/app/sites: Caddy serves that directory to the internet.
#
# The names on the left are the vault's; the names on the right are the
# catalog's `path` values (atlas/modules/academy/catalog.json). English only:
# the Arabic edition of 30 Business Lessons waits for Miro's language review.
set -euo pipefail
. "$(dirname "$0")/atlas-host.sh"
HOST="$(atlas_host)"
[ "$HOST" = "local" ] && atlas_local_unsupported "$0" \
    "Locally the files go under data/app/academy/files in this checkout (the api mounts data/app)."

VAULT="${ATLAS_VAULT:-$HOME/Documents/ATLAS Vault}/Products"
DEST="/opt/atlas/data/app/academy/files"
MIN_FREE_GB=10          # memory: keep >= 10 GB free on the host (disk filled once)
GO=0
[ "${1:-}" = "--go" ] && GO=1

# vault file | catalog path
FILES=(
  "Build a Safe AI Operator/Build a Safe AI Operator.pdf|safe-ai-operator/build-a-safe-ai-operator.pdf"
  "Build a Safe AI Operator/safe-operator-starter.zip|safe-ai-operator/safe-operator-starter.zip"
  "dist/30-business-lessons.pdf|30-business-lessons/30-business-lessons.pdf"
  "dist/claude-obsidian-vault-kit.zip|vault-kit/claude-obsidian-vault-kit.zip"
  "AI for Every Age/dist/Everyday-AI-for-Adults.zip|ai-for-every-age/everyday-ai-for-adults.zip"
  "AI for Every Age/dist/Safe and Simple AI 60+.pdf|ai-for-every-age/safe-and-simple-ai-60plus.pdf"
  "Local Business Automation Pack/dist/local-business-automation-pack.zip|local-business-automation-pack/local-business-automation-pack.zip"
  "Family AI-Scam Defense Workbook/dist/Family AI-Scam Defense Workbook.pdf|family-ai-scam-defence-workbook/family-ai-scam-defence-workbook.pdf"
)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
total=0
for row in "${FILES[@]}"; do
  src="$VAULT/${row%%|*}"
  rel="${row##*|}"
  if [ ! -f "$src" ]; then
    echo "missing in the vault: $src" >&2
    exit 1
  fi
  mkdir -p "$STAGE/$(dirname "$rel")"
  cp "$src" "$STAGE/$rel"
  size=$(wc -c < "$src" | tr -d ' ')
  total=$((total + size))
  printf '%10s  %s\n' "$size" "$rel"
done
echo "total: $((total / 1024 / 1024)) MB in ${#FILES[@]} files"

free_kb=$(ssh "$HOST" "df -Pk /opt/atlas | awk 'NR==2 {print \$4}'")
free_gb=$((free_kb / 1024 / 1024))
echo "free on the host: ${free_gb} GB"
if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
  echo "refusing: less than ${MIN_FREE_GB} GB free on the host" >&2
  exit 1
fi

# The support assistant's knowledge base (atlas/modules/academy/support_kb.py):
# the allowlisted, customer-facing product files, chunked. Paid lesson text,
# so never in git; it lands beside the files dir, at ACADEMY_SUPPORT_KB's
# default (/app/data/academy/support_kb.json in the api container).
KB_DEST="/opt/atlas/data/app/academy/support_kb.json"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ATLAS_PYTHON:-$REPO/.venv/bin/python}"
(cd "$REPO" && "$PY" -m atlas.modules.academy.support_kb build "$VAULT" "$STAGE.kb.json")
trap 'rm -rf "$STAGE" "$STAGE.kb.json"' EXIT

if [ "$GO" -ne 1 ]; then
  echo "support KB: $(wc -c < "$STAGE.kb.json" | tr -d ' ') bytes -> $KB_DEST"
  rsync -rt --checksum --dry-run --itemize-changes "$STAGE"/ "$HOST:$DEST/"
  echo "dry run only; re-run with --go to copy"
  exit 0
fi

ssh "$HOST" "install -d -m 750 -o 10001 -g 10001 $DEST"
rsync -rt --checksum --perms --chmod=Du=rwx,Dg=rx,Do=,Fu=rw,Fg=r,Fo= "$STAGE"/ "$HOST:$DEST/"
ssh "$HOST" "chown -R 10001:10001 $DEST"
rsync -t --checksum --chmod=Fu=rw,Fg=r,Fo= "$STAGE.kb.json" "$HOST:$KB_DEST"
ssh "$HOST" "chown 10001:10001 $KB_DEST"
echo "copied. The academy.delivery check reports what is on disk within 6 hours."
