#!/usr/bin/env bash
# Deploy the Guardian hub to the VPS. Run from the repo root on the Mac.
#   deploy/deploy.sh                          # build web, rsync, install, migrate, restart
#   GUARDIAN_SEED_DEMO=1 deploy/deploy.sh     # ...and also refresh the demo tenant
# Requires: root SSH to the VPS (key-based), node on the Mac, python3 on the VPS.
set -euo pipefail
HOST="${GUARDIAN_HOST:-root@203.0.113.10}"
DEST=/opt/guardian
cd "$(dirname "$0")/.."

# --- does the tree about to be shipped actually import? ---------------------
#
# rsync replaces /opt/guardian/hub before anything on the VPS runs, so a hub
# that cannot be imported is on the server already by the time `guardian
# migrate` says so. The service keeps serving only because it is still running
# the old code from memory: the next restart, reboot or crash boots nothing.
# That is a live outage sitting armed behind a deploy that reported failure,
# which is the worst shape a failure can take.
#
# It happened on 2026-09-17: a payloads/security.py change imported
# `effective_presence` from payloads/_util.py, verified against a working tree
# where that helper existed only as uncommitted work. Fine on the Mac, fatal
# everywhere else.
#
# One import of the real entrypoint catches that class of mistake in about a
# second, and catches it HERE, before the VPS has been touched at all. It uses
# the repo's own venv rather than the system python, because that is the
# interpreter the package's dependencies are installed into.
echo "== preflight: hub imports"
if [ -x hub/.venv/bin/python ]; then
  # The `cd hub` is load-bearing, and the reason is easy to miss. `guardian_hub`
  # is installed EDITABLE, so the venv carries a finder that resolves the name
  # to whichever tree it was installed from. Run from the repo root -- where no
  # `guardian_hub/` directory sits in the current directory -- that finder wins,
  # and the check imports some OTHER worktree's code while reporting on this
  # one. Every `Guardian-*` worktree here shares one venv by symlink, so that is
  # the normal case rather than the exotic one.
  #
  # It fails in both directions. On 2026-09-20 a deploy from a clean worktree
  # was refused for a circular import that existed only in a different tree's
  # uncommitted work; the mirror -- shipping a broken tree because a healthy
  # neighbour imported fine -- is the one this check exists to prevent, and is
  # silent. `cd hub` puts the package in the current directory, which takes
  # precedence over the finder, so the check reads the tree being shipped.
  ( cd hub && .venv/bin/python -c "import guardian_hub.main" ) || {
    echo "FATAL: the hub does not import -- refusing to ship it. Nothing was sent." >&2
    exit 1
  }
else
  echo "   (skipped: no hub/.venv here -- create one to get this check)" >&2
fi

# --- is this tree newer than what is already live? -------------------------
#
# rsync ships the tree it is RUN FROM, with `--delete`, and nothing here ever
# asked which branch that was. Production therefore serves whichever branch
# the last person to deploy happened to be standing in, and a deploy from a
# branch that predates the live one is a silent, total revert.
#
# It happened on 2026-09-20 and cost an evening. The owner console at
# admin.guarder.io was a four-page skeleton with an empty front page, and it
# read as bad design. Nothing was broken: prod had been deployed from
# `phase5-closeout`, which never carried the console work at all, while `main`
# sat 44 commits ahead with the whole thing in it. Several sessions work in
# separate worktrees of this repo at once, so any of them can do this to any
# other, and the failure is invisible -- the deploy succeeds, the health check
# passes, and the site quietly goes backwards.
#
# So the server records the commit it was last given, and this refuses to ship
# a tree that does not contain it. Ancestry, not timestamps: a commit date can
# precede a merge that already carries it, and branches here are merged rather
# than rebased.
#
# `GUARDIAN_FORCE_DEPLOY=1` overrides, for the one legitimate case -- a
# deliberate rollback. It is loud on purpose.
echo "== preflight: is this tree newer than production?"
LIVE_SHA="$(ssh "$HOST" "cat $DEST/DEPLOYED_SHA 2>/dev/null || true" | tr -d '[:space:]')"
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
if [ -z "$LIVE_SHA" ]; then
  echo "   (no commit recorded on the server yet -- recording this one)"
elif [ "${GUARDIAN_FORCE_DEPLOY:-0}" = "1" ]; then
  echo "   OVERRIDDEN: shipping ${HEAD_SHA:0:12} over live ${LIVE_SHA:0:12}" >&2
elif [ -z "$HEAD_SHA" ]; then
  echo "FATAL: not a git checkout, so this cannot be checked. Nothing was sent." >&2
  exit 1
elif ! git cat-file -e "$LIVE_SHA^{commit}" 2>/dev/null; then
  echo "FATAL: production is running commit $LIVE_SHA, which does not exist in this" >&2
  echo "       repository. Fetch it before deploying -- it was probably pushed from" >&2
  echo "       another machine. Nothing was sent." >&2
  exit 1
elif ! git merge-base --is-ancestor "$LIVE_SHA" HEAD; then
  echo "FATAL: this tree does NOT contain what is already live." >&2
  echo "       live: $LIVE_SHA  ($(git log -1 --format=%s "$LIVE_SHA" 2>/dev/null))" >&2
  echo "       here: $HEAD_SHA  ($(git log -1 --format=%s HEAD))" >&2
  echo "       Deploying would revert $(git rev-list --count HEAD.."$LIVE_SHA" 2>/dev/null) commit(s)." >&2
  echo "       Merge the live commit into this branch first:" >&2
  echo "           git merge $LIVE_SHA" >&2
  echo "       or, if you really mean to roll production back:" >&2
  echo "           GUARDIAN_FORCE_DEPLOY=1 deploy/deploy.sh" >&2
  echo "       Nothing was sent." >&2
  exit 1
else
  echo "   ok: contains live ${LIVE_SHA:0:12}, ahead by $(git rev-list --count "$LIVE_SHA"..HEAD)"
fi

echo "== build web"
# The deployed commit is baked into the bundle AND written beside it, so a
# page that is already open can ask the server whether it is stale. A
# single-page app never reloads itself; without this a tab opened before a
# deploy runs the old app until somebody happens to reload -- which is how the
# console's redesign shipped and was reported as "still the old design".
( cd web && GUARDIAN_BUILD_SHA="${HEAD_SHA:-dev}" npm ci --silent && GUARDIAN_BUILD_SHA="${HEAD_SHA:-dev}" npm run build --silent )
printf '{"sha":"%s"}\n' "${HEAD_SHA:-dev}" > web/dist/build.json

ssh "$HOST" "mkdir -p $DEST/hub $DEST/sensor $DEST/web/dist $DEST/deploy"
echo "== rsync hub + sensor + web/dist + deploy"
# `.env.*` and `data-sandbox` are the local Stripe-sandbox hub (.claude/launch.json):
# an sk_test_ key and a test database that have no business on the VPS. Both
# were one exact-name exclude away from shipping -- `.env` and `data` matched
# only themselves.
rsync -az --delete --exclude '.env' --exclude '.env.*' --exclude '.venv' --exclude 'data' --exclude 'data-sandbox' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.egg-info' hub/ "$HOST:$DEST/hub/"
rsync -az --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.egg-info' sensor/ "$HOST:$DEST/sensor/"
rsync -az --delete web/dist/ "$HOST:$DEST/web/dist/"
rsync -az deploy/ "$HOST:$DEST/deploy/"
# The marketing site, if it has already been installed by deploy/setup-site.sh.
# Without this line the site is only ever updated by re-running that script, and
# it silently went five commits stale in production -- the pricing page on
# guarder.io was still showing three plans and no monthly/yearly toggle days
# after both shipped. `--delete` is safe here: setup-site.sh owns this whole
# directory and nothing else writes into it.
if ssh "$HOST" "test -d $DEST/site"; then
  echo "== rsync site"
  # `getlayers.json` is GetLayers build state, not a page: design provenance,
  # the reasoning behind Style choices, and paths to internal spec documents.
  # It was being served at https://guarder.io/getlayers.json with a 200.
  # `--delete-excluded`, not just `--delete`: rsync never deletes an EXCLUDED
  # path on the receiver unless told to, so the first version of this line
  # stopped shipping the file and left the old copy serving -- found by
  # checking the URL after the deploy, not by the deploy. CLAUDE.md § Security
  # posture: this product's own output is a disclosure surface, and a
  # marketing host has no reason to serve a build-state file at all.
  rsync -az --delete --delete-excluded --exclude getlayers.json site/ "$HOST:$DEST/site/"
fi

echo "== install on the VPS"
ssh "$HOST" "GUARDIAN_SEED_DEMO=${GUARDIAN_SEED_DEMO:-0} bash -s" <<'REMOTE'
set -euo pipefail
DEST=/opt/guardian
[ -d $DEST/venv ] || python3 -m venv $DEST/venv
$DEST/venv/bin/pip install -q --upgrade pip >/dev/null
$DEST/venv/bin/pip install -q -e $DEST/hub
if [ ! -f $DEST/hub/.env ]; then
  umask 077
  cat > $DEST/hub/.env <<ENV
GUARDIAN_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
GUARDIAN_DATA_DIR=$DEST/data
GUARDIAN_BASE_URL=https://my.guarder.io
GUARDIAN_WEB_DIST=$DEST/web/dist
# Real OTP e-mail: set these. Until they are set, sign-in codes are NOT
# printed to the journal -- GUARDIAN_DEV_CONSOLE_OTP is for a local hub only
# (it puts every customer's sign-in code in the system log), so this script
# never writes it into a production .env.
GUARDIAN_SMTP_HOST=
GUARDIAN_SMTP_PORT=587
GUARDIAN_SMTP_USER=
GUARDIAN_SMTP_PASSWORD=
GUARDIAN_SMTP_FROM="Vault <guardian@guarder.io>"
# Comma-separated: who owner notices (new lead, etc.) go to; empty falls back to every superadmin's email
GUARDIAN_OWNER_EMAIL=
ENV
  echo "wrote new $DEST/hub/.env -- set GUARDIAN_SMTP_* before anyone signs in"
fi
mkdir -p $DEST/data/reports
# A value containing shell metacharacters (an unquoted `<` in an address, say)
# makes this `.` fail, and with it the whole `&&` chain -- so migrations were
# silently skipped while the deploy still reported success. Fail loudly instead.
cd $DEST/hub || exit 1
set -a
. ./.env || { echo "FATAL: $DEST/hub/.env is not valid shell -- quote any value containing < > \$ or spaces" >&2; exit 1; }
set +a
$DEST/venv/bin/guardian migrate || { echo "FATAL: migrations failed" >&2; exit 1; }
# seed-demo is idempotent and never touches the imported demo tenant's
# users (see seed.py's own docstring) -- opt in with GUARDIAN_SEED_DEMO=1
# rather than running it on every deploy by default.
if [ "${GUARDIAN_SEED_DEMO:-0}" = "1" ]; then
  cd $DEST/hub && set -a && . ./.env && set +a && $DEST/venv/bin/guardian seed-demo
  echo "seed-demo: applied"
fi
install -m 644 $DEST/deploy/guardian-hub.service /etc/systemd/system/guardian-hub.service
systemctl daemon-reload
systemctl enable --now guardian-hub >/dev/null
systemctl restart guardian-hub
systemctl is-active guardian-hub
# Poll rather than `sleep 2` then curl once: uvicorn's startup (alembic
# check, scheduler, first DB connection) is sometimes slower than two
# seconds, and a single early probe reported a connection failure while the
# deploy carried on regardless. A hub that is still not answering after 30s
# fails the deploy.
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8800/api/v1/health; then echo; break; fi
  if [ "$attempt" = 30 ]; then echo "hub did not answer /api/v1/health within 30s"; exit 1; fi
  sleep 1
done

# nginx: NOT managed here any more. This used to install the legacy
# safehome.example.com:8443 vhost (deploy/nginx-guardian.conf) on every run,
# but the cutover to my.guarder.io moved the live config into
# guardian-443.conf / guardian-api.conf / guardian-acme.conf /
# guardian-site.conf, owned by deploy/setup-domain.sh and setup-site.sh.
# Re-installing the old vhost re-created a guardian.conf symlink whose
# safehome certs no longer exist, so `nginx -t` failed and the reload was
# skipped on every deploy. The app deploy has no business touching nginx;
# run the setup-*.sh scripts when the served config actually changes.
REMOTE

# Recorded only now, because everything above it can fail: `set -e` plus the
# health-check loop inside the REMOTE block means we reach this line only when
# the hub is actually answering. A SHA written before that would have the next
# deploy refusing to ship over a version that never came up.
#
# Uncommitted changes are not represented here, and cannot be -- the guard's
# question is "does this tree contain what is live", which is about history. A
# dirty tree still ships; it just records the commit it was based on.
if [ -n "${HEAD_SHA:-}" ]; then
  ssh "$HOST" "printf '%s\n' '$HEAD_SHA' > $DEST/DEPLOYED_SHA"
  echo "== recorded ${HEAD_SHA:0:12} as live"
fi
echo "== done: https://my.guarder.io"
