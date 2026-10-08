#!/usr/bin/env bash
# Give Guardian its own hostname on the standard HTTPS port.
#
# Guardian answers on :443 as my.guarder.io. nginx does name-based virtual
# hosting, so a further hostname can take :443 on the same box at the same
# time and the existing one keeps working untouched. This script adds that
# hostname: it writes its own guardian-<hostname>.conf and refuses to touch
# an existing one, so it can never replace the vhost already serving the hub.
#
#   ./deploy/setup-domain.sh app.example.com --dry-run
#   ./deploy/setup-domain.sh app.example.com --redirect-from example.com,www.example.com
#
# The first argument is the hostname Guardian itself answers on. Pick it once
# and never change it: a sensor stores the hub URL it enrolled against, so
# moving the app later re-breaks every deployed sensor. Hostnames passed to
# --redirect-from are 301'd to it, which is how the bare domain can point at
# the dashboard today and become a marketing site later without touching the
# app's URL.
#
# Prerequisite you must do first: an A record for <hostname> pointing at this
# server's public IP. The script refuses to run until that resolves, because
# the certificate authority validates over HTTP on port 80 and a wrong record
# burns issuance attempts against a rate limit.
set -euo pipefail

DOMAIN="${1:-}"
[ -n "$DOMAIN" ] || { echo "usage: $0 <hostname> [--dry-run]" >&2; exit 2; }
shift
DRY=0
REDIRECTS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)       DRY=1 ;;
    --redirect-from) REDIRECTS="${2:-}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
ALL_NAMES="$DOMAIN"
[ -n "$REDIRECTS" ] && ALL_NAMES="$DOMAIN,$REDIRECTS"

HOST="${GUARDIAN_HOST:-root@203.0.113.10}"
DEST="${GUARDIAN_DEST:-/opt/guardian}"
run() { if [ "$DRY" = 1 ]; then echo "DRY  $*"; else ssh "$HOST" "$*"; fi; }
say() { printf '\n== %s\n' "$1"; }

say "checking DNS"
SERVER_IP="$(ssh "$HOST" "curl -s4 --max-time 10 ifconfig.me || hostname -I | awk '{print \$1}'")"
echo "server: ${SERVER_IP:-unknown}"
BAD=0
# Queried against a public resolver, not this machine's. What matters is what
# the certificate authority's resolver sees, and a local resolver that was
# asked before the records existed can hold a negative answer for its whole
# TTL -- which reads here as "no A record" for a name that is already live.
for n in $(echo "$ALL_NAMES" | tr ',' ' '); do
  R="$(dig +short "$n" A @1.1.1.1 2>/dev/null | tail -1)"
  [ -n "$R" ] || R="$(dig +short "$n" A @8.8.8.8 2>/dev/null | tail -1)"
  printf '  %-28s -> %s\n' "$n" "${R:-<no A record>}"
  # Every name goes on one certificate, so a single unresolved name fails the
  # whole issuance -- check them all before spending an attempt against the
  # certificate authority's rate limit.
  [ "$R" = "$SERVER_IP" ] || BAD=1
done
if [ "$BAD" = 1 ]; then
  echo "ERROR: every name above must have an A record pointing at $SERVER_IP." >&2
  echo "       Fresh registrations can take a few hours; re-check with:" >&2
  echo "         dig +short $DOMAIN A @1.1.1.1" >&2
  exit 1
fi

say "port-80 challenge block"
# Templates are pushed and substituted rather than built inline: an nginx
# config is full of $host / $request_uri, and every layer of local string,
# ssh and remote heredoc gets its own chance to eat the dollar signs.
NAMES_SP="$(echo "$ALL_NAMES" | tr ',' ' ')"
push() { if [ "$DRY" = 1 ]; then echo "DRY  scp $1 -> $2"; else scp -q "$1" "$HOST:$2"; fi; }
push deploy/nginx-acme.conf.tmpl /tmp/guardian-acme.tmpl
run "mkdir -p /var/www/acme \
  && sed 's/__NAMES__/$NAMES_SP/' /tmp/guardian-acme.tmpl > /etc/nginx/sites-available/guardian-acme.conf \
  && ln -sf /etc/nginx/sites-available/guardian-acme.conf /etc/nginx/sites-enabled/guardian-acme.conf \
  && nginx -t && systemctl reload nginx"

say "issuing the certificate"
# acme.sh is already installed and is what issued the existing certificate.
# --keylength ec-256 matches it; the install-cert reload hook is what keeps
# renewals live without a second cron entry.
ACME_D="$(for n in $(echo "$ALL_NAMES" | tr ',' ' '); do printf -- '-d %s ' "$n"; done)"
run "/root/.acme.sh/acme.sh --issue $ACME_D -w /var/www/acme --keylength ec-256 \
  && /root/.acme.sh/acme.sh --install-cert -d $DOMAIN --ecc \
       --key-file /etc/nginx/certs/$DOMAIN.key.pem \
       --fullchain-file /etc/nginx/certs/$DOMAIN.fullchain.pem \
       --reloadcmd 'systemctl reload nginx'"

say "installing the :443 server block"
# A copy of the :443 vhost with the hostname and certificate paths swapped.
#
# Written to a per-hostname file, NOT to guardian-443.conf. That file is the
# live vhost for the hub's current hostname; this script used to overwrite it
# whatever domain you passed, so adding a second hostname silently replaced
# the first -- the opposite of what the header promises.
#
# Two things this no longer does, because both belonged to the pre-cutover
# world: rewriting `listen 8443` to `listen 443` (the source is already :443
# since safehome was retired), and stripping a `map $http_upgrade` block out
# of the copy (the source no longer declares one -- conf.d/guardian-ws-map.conf
# is the single declaration per nginx instance).
# The map that vhost's `Connection $connection_upgrade` depends on, installed
# first because `nginx -t` below fails with "unknown variable" without it.
#
# It lives in conf.d (http context, one declaration per nginx instance) rather
# than in the vhost: two maps for one variable is a hard error, so a second
# hostname added later must find it already there rather than declare its own.
# It was retired along with the :8443 vhost that used to carry it, and for a
# week only the hand-placed copy on the live box had it -- a rebuild from this
# repo would have produced an nginx that would not start. `install` is
# idempotent and the file is byte-identical to the live one, so re-running
# this changes nothing on a box that already has it.
run "install -m 644 $DEST/deploy/nginx-ws-map.conf /etc/nginx/conf.d/guardian-ws-map.conf"

VHOST=/etc/nginx/sites-available/guardian-$DOMAIN.conf
run "test ! -e $VHOST || { echo \"FATAL: $VHOST already exists -- refusing to overwrite a live vhost; remove it by hand if you really mean to re-create it\" >&2; exit 1; }
sed -e 's/server_name my.guarder.io;/server_name $DOMAIN;/' \
    -e 's#/etc/nginx/certs/my.guarder.io.fullchain.pem#/etc/nginx/certs/$DOMAIN.fullchain.pem#' \
    -e 's#/etc/nginx/certs/my.guarder.io.key.pem#/etc/nginx/certs/$DOMAIN.key.pem#' \
    $DEST/deploy/nginx-guardian-443.conf > $VHOST
ln -sf $VHOST /etc/nginx/sites-enabled/guardian-$DOMAIN.conf
nginx -t && systemctl reload nginx"

if [ -n "$REDIRECTS" ]; then
  say "installing the redirect block for $REDIRECTS"
  REDIR_SP="$(echo "$REDIRECTS" | tr ',' ' ')"
  push deploy/nginx-redirect.conf.tmpl /tmp/guardian-redirect.tmpl
  run "sed -e 's/__REDIRECTS__/$REDIR_SP/' -e 's/__DOMAIN__/$DOMAIN/g' \
        /tmp/guardian-redirect.tmpl > /etc/nginx/sites-available/guardian-redirect.conf \
    && ln -sf /etc/nginx/sites-available/guardian-redirect.conf /etc/nginx/sites-enabled/guardian-redirect.conf \
    && nginx -t && systemctl reload nginx"
fi

say "pointing the hub at its new public URL"
# Report share links, push-notification action buttons and the sensor enrolment
# command are all built from this value, so they keep pointing at the old
# host:port until it changes. Only this one key is touched, and a timestamped
# backup is taken first -- a previous deploy wiped this file wholesale.
run "cp -a $DEST/hub/.env $DEST/hub/.env.bak-\$(date +%Y%m%d%H%M%S)   && sed -i 's#^GUARDIAN_BASE_URL=.*#GUARDIAN_BASE_URL=https://$DOMAIN#' $DEST/hub/.env   && grep -q '^GUARDIAN_BASE_URL=' $DEST/hub/.env || echo 'GUARDIAN_BASE_URL=https://$DOMAIN' >> $DEST/hub/.env
grep '^GUARDIAN_BASE_URL=' $DEST/hub/.env
systemctl restart guardian-hub"

say "verifying"
run "curl -s --max-time 15 https://$DOMAIN/api/v1/health; echo; systemctl is-active nginx guardian-hub"

cat <<DONE

Guardian is now at https://$DOMAIN (no port).
${REDIRECTS:+Redirecting to it: $REDIRECTS}
Still to do by hand:
  - re-enrol the sensor against the new URL so it stops using the old one
Any hostname already served on :443 was not touched: this wrote its own
guardian-$DOMAIN.conf and left every other vhost alone.
DONE
