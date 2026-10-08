#!/usr/bin/env bash
# Turn the bare domain into the public site, and stop it redirecting to the app.
#
# Two products, two hostnames, one server and one certificate:
#
#   my.guarder.io   the Guardian app          guardian-443.conf     UNTOUCHED
#   guarder.io      the public site           guardian-site.conf    <- this script
#   www.guarder.io  the public site           guardian-site.conf    <- this script
#
# deploy/setup-domain.sh put a 301 on the last two (guardian-redirect.conf) and
# said in its own template that a marketing site would replace it. This is that
# replacement: it installs the static site from `site/` and REMOVES the redirect
# block. Removing it is not optional -- two enabled server blocks claiming
# `guarder.io` would have nginx serve whichever it loaded first, which is a
# coin-flip between the site and the old redirect on every reload.
#
#   ./deploy/setup-site.sh --dry-run
#   ./deploy/setup-site.sh guarder.io,www.guarder.io --app my.guarder.io
#
# What it never touches: the app's server block, the certificate (one cert
# already covers all three names -- nothing is issued or renewed here), the
# port-80 ACME block, and hub/.env.
set -euo pipefail

SITE_NAMES="${SITE_NAMES:-guarder.io,www.guarder.io}"
APP_DOMAIN="${APP_DOMAIN:-my.guarder.io}"
DRY=0

# First positional argument, if given, is the site hostname list.
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  SITE_NAMES="$1"; shift
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --app)     APP_DOMAIN="${2:-}"; shift ;;
    *) echo "usage: $0 [site.example,www.site.example] [--app app.example] [--dry-run]" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$APP_DOMAIN" ] || { echo "--app must name the hostname the certificate is filed under" >&2; exit 2; }

HOST="${GUARDIAN_HOST:-root@203.0.113.10}"
DEST="${GUARDIAN_DEST:-/opt/guardian}"
SITE_ROOT="$DEST/site"
NAMES_SP="$(echo "$SITE_NAMES" | tr ',' ' ')"
FIRST_NAME="${SITE_NAMES%%,*}"

cd "$(dirname "$0")/.."

run()  { if [ "$DRY" = 1 ]; then echo "DRY  ssh $*"; else ssh "$HOST" "$*"; fi; }
push() { if [ "$DRY" = 1 ]; then echo "DRY  scp $1 -> $2"; else scp -q "$1" "$HOST:$2"; fi; }
say()  { printf '\n== %s\n' "$1"; }

say "plan"
cat <<PLAN
  site hostnames : $NAMES_SP
  app (untouched): $APP_DOMAIN
  certificate    : /etc/nginx/certs/$APP_DOMAIN.{fullchain,key}.pem  (existing, not reissued)
  static root    : $SITE_ROOT
  removes        : guardian-redirect.conf (the 301 these names serve today)
PLAN

say "fonts"
# The site declares @font-face for the two families the app uses and expects
# the files next to it. They are not in the repo yet (Task 57 vendors them for
# the app under web/public/fonts) -- so this is best-effort on purpose: without
# them the page renders on its system-ui / ui-monospace fallbacks, which is a
# perfectly good page, and never a broken deploy.
if [ "$DRY" = 1 ]; then
  echo "  (skipped -- dry run; this step writes into the working tree)"
elif [ -d web/public/fonts ] && ls web/public/fonts/*.woff2 >/dev/null 2>&1; then
  cp web/public/fonts/*.woff2 site/fonts/ 2>/dev/null || true
  echo "  copied $(ls -1 site/fonts/*.woff2 2>/dev/null | wc -l | tr -d ' ') font file(s) into site/fonts/"
else
  echo "  web/public/fonts is empty or absent -- the site will use its fallback font stacks"
fi

say "checking DNS"
# Skipped on a dry run: this is a real ssh + real queries, and `set -e` would
# abort the rehearsal on a host that is simply not reachable from here.
if [ "$DRY" = 1 ]; then
  echo "  (skipped -- dry run)"
else
  SERVER_IP="$(ssh "$HOST" "curl -s4 --max-time 10 ifconfig.me || hostname -I | awk '{print \$1}'")"
  echo "server: ${SERVER_IP:-unknown}"
  BAD=0
  # Against a public resolver, not this machine's: what matters is what the
  # rest of the world sees, and a local resolver can hold a stale negative
  # answer for its whole TTL.
  for n in $NAMES_SP; do
    R="$(dig +short "$n" A @1.1.1.1 2>/dev/null | tail -1)"
    [ -n "$R" ] || R="$(dig +short "$n" A @8.8.8.8 2>/dev/null | tail -1)"
    printf '  %-28s -> %s\n' "$n" "${R:-<no A record>}"
    [ "$R" = "$SERVER_IP" ] || BAD=1
  done
  if [ "$BAD" = 1 ]; then
    echo "ERROR: every name above must have an A record pointing at $SERVER_IP." >&2
    exit 1
  fi
fi

say "checking the certificate already covers these names"
# Nothing is issued here. If the certificate does not already list them the
# right fix is `deploy/setup-domain.sh <app> --redirect-from <these names>`,
# which is what put them on it in the first place -- not a second certificate.
run "openssl x509 -in /etc/nginx/certs/$APP_DOMAIN.fullchain.pem -noout -text \
  | grep -A1 'Subject Alternative Name' | tail -1"
for n in $NAMES_SP; do
  run "openssl x509 -in /etc/nginx/certs/$APP_DOMAIN.fullchain.pem -noout -text \
    | grep -q 'DNS:$n' || { echo 'ERROR: $n is not on the certificate -- re-run setup-domain.sh with --redirect-from' >&2; exit 1; }"
done

say "pushing site/"
if [ "$DRY" = 1 ]; then
  echo "DRY  rsync site/ -> $HOST:$SITE_ROOT/"
  echo "     $(find site -type f | wc -l | tr -d ' ') file(s), $(du -sh site | cut -f1) total"
else
  ssh "$HOST" "mkdir -p $SITE_ROOT"
  # --delete so a removed page really disappears; the directory holds nothing
  # but what this repo puts there.
  rsync -az --delete site/ "$HOST:$SITE_ROOT/"
fi

say "installing the site server block"
# Template pushed and substituted rather than built inline: an nginx config is
# full of $host / $uri / $remote_addr, and every layer of local string, ssh and
# remote heredoc gets its own chance to eat the dollar signs.
push deploy/nginx-site.conf.tmpl /tmp/guardian-site.tmpl
run "sed -e 's/__SITE_NAMES__/$NAMES_SP/' \
         -e 's/__CERT_DOMAIN__/$APP_DOMAIN/g' \
         -e 's#__SITE_ROOT__#$SITE_ROOT#' \
         /tmp/guardian-site.tmpl > /etc/nginx/sites-available/guardian-site.conf \
  && ln -sf /etc/nginx/sites-available/guardian-site.conf /etc/nginx/sites-enabled/guardian-site.conf"

say "removing the old redirect block"
# The symlink is what nginx reads; sites-available keeps the file so the 301
# can be put back with a single ln -sf if the site has to come down.
run "rm -f /etc/nginx/sites-enabled/guardian-redirect.conf"

say "testing and reloading nginx"
# `nginx -t` before the reload, and the reload is what makes it live -- a
# failed test leaves the running config exactly as it was, so a bad template
# never takes the site (or the app next door) down.
run "nginx -t && systemctl reload nginx"

say "verifying"
run "curl -sS -o /dev/null -w 'https://$FIRST_NAME/ -> %{http_code}\n' --max-time 15 https://$FIRST_NAME/"
run "curl -sS -o /dev/null -w 'the app is still there: https://$APP_DOMAIN/api/v1/health -> %{http_code}\n' --max-time 15 https://$APP_DOMAIN/api/v1/health"
# The form's one proxied path, end to end from the marketing origin. It stores
# a real row, so it uses the honeypot field: the hub answers exactly as it does
# for a genuine submission and writes nothing.
run "curl -sS --max-time 15 -X POST https://$FIRST_NAME/api/v1/public/lead \
  -H 'Content-Type: application/json' \
  -d '{\"name\":\"deploy check\",\"email\":\"check@example.com\",\"website\":\"honeypot\"}'; echo"

cat <<DONE

The public site is live on: $NAMES_SP
The app is untouched at https://$APP_DOMAIN, on the same certificate.

Leads land in the app for the account owner at:
  https://$APP_DOMAIN/settings/leads

To put the 301 back (the site comes down, the names go to the app again):
  ssh $HOST 'ln -sf /etc/nginx/sites-available/guardian-redirect.conf \\
    /etc/nginx/sites-enabled/guardian-redirect.conf \\
    && rm -f /etc/nginx/sites-enabled/guardian-site.conf \\
    && nginx -t && systemctl reload nginx'
DONE
