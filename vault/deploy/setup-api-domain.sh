#!/usr/bin/env bash
# Give the public REST API its own hostname, on the same server and the same
# certificate as the app.
#
#   my.guarder.io   the Guardian app     guardian-443.conf    UNTOUCHED
#   guarder.io      the public site      guardian-site.conf   UNTOUCHED
#   api.guarder.io  the REST API         guardian-api.conf    <- this script
#
#   ./deploy/setup-api-domain.sh --dry-run
#   ./deploy/setup-api-domain.sh api.guarder.io --app my.guarder.io
#
# YOU MUST ADD THE DNS RECORD FIRST:
#
#   api.guarder.io.   A   <this server's public IP>
#
# The script refuses to go any further until that resolves, because the
# certificate authority validates over plain HTTP on port 80 and a wrong record
# burns an issuance attempt against a rate limit.
#
# The certificate is the risky part, so read this before running it for real.
# One certificate covers every Guardian hostname on this box. Adding a name
# means RE-ISSUING that certificate with the full list — and a botched re-issue
# takes the app down with it, not just the API. Three guards, in this order:
#
#   1. The existing certificate's names are read off the installed file. If they
#      cannot be read, the script stops rather than guessing a list.
#   2. If the new name is ALREADY on the certificate, issuance is skipped
#      entirely. Re-running this script is therefore free, and the common case
#      spends no attempt at all.
#   3. The existing primary name stays FIRST in the `-d` list, so acme.sh keeps
#      using the same certificate directory and the installed file paths do not
#      move under the app's own server block.
#
# Idempotent throughout: the port-80 block, the server block, the symlink and
# the certificate step can each be re-applied without harm, and `nginx -t` runs
# before every reload so a bad template never takes the running config down.
set -euo pipefail

API_DOMAIN="${API_DOMAIN:-api.guarder.io}"
APP_DOMAIN="${APP_DOMAIN:-my.guarder.io}"
DRY=0

# First positional argument, if given, is the API hostname.
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  API_DOMAIN="$1"; shift
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --app)     APP_DOMAIN="${2:-}"; shift ;;
    *) echo "usage: $0 [api.example.com] [--app app.example.com] [--dry-run]" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$API_DOMAIN" ] || { echo "the API hostname must not be empty" >&2; exit 2; }
[ -n "$APP_DOMAIN" ] || { echo "--app must name the hostname the certificate is filed under" >&2; exit 2; }

HOST="${GUARDIAN_HOST:-root@203.0.113.10}"
CERT_DIR=/etc/nginx/certs
FULLCHAIN="$CERT_DIR/$APP_DOMAIN.fullchain.pem"

cd "$(dirname "$0")/.."

run()  { if [ "$DRY" = 1 ]; then echo "DRY  ssh $*"; else ssh "$HOST" "$*"; fi; }
push() { if [ "$DRY" = 1 ]; then echo "DRY  scp $1 -> $2"; else scp -q "$1" "$HOST:$2"; fi; }
say()  { printf '\n== %s\n' "$1"; }

say "plan"
cat <<PLAN
  API hostname     : $API_DOMAIN
  app (untouched)  : $APP_DOMAIN
  certificate      : $FULLCHAIN  (re-issued WITH $API_DOMAIN added, unless already covered)
  server block     : /etc/nginx/sites-available/guardian-api.conf
  proxied          : /v1/  ->  127.0.0.1:8800
  refused          : everything else, including /api/, /metrics and the app itself
PLAN

say "checking DNS"
# A dry run must be completely local. `deploy/setup-domain.sh` learns the server
# IP with a direct `ssh` (not through `run`), which means rehearsing a copy of it
# still reaches production; this does not. Set GUARDIAN_SERVER_IP to compare
# against a known address without any ssh at all.
SERVER_IP="${GUARDIAN_SERVER_IP:-}"
if [ "$DRY" = 1 ]; then
  echo "  (dry run -- no ssh)"
  if [ -z "$SERVER_IP" ]; then
    echo "  would ask $HOST for its public IP, then require:"
    echo "    $API_DOMAIN.  A  <that IP>"
  fi
else
  [ -n "$SERVER_IP" ] || SERVER_IP="$(ssh "$HOST" "curl -s4 --max-time 10 ifconfig.me || hostname -I | awk '{print \$1}'")"
  echo "server: ${SERVER_IP:-unknown}"
fi

# Resolved locally against public resolvers, not the server's own: what matters
# is what the certificate authority's resolver sees, and a local resolver asked
# before the record existed can hold a negative answer for its whole TTL --
# which reads as "no A record" for a name that is already live.
RESOLVED="$(dig +short "$API_DOMAIN" A @1.1.1.1 2>/dev/null | tail -1)"
[ -n "$RESOLVED" ] || RESOLVED="$(dig +short "$API_DOMAIN" A @8.8.8.8 2>/dev/null | tail -1)"
printf '  %-28s -> %s\n' "$API_DOMAIN" "${RESOLVED:-<no A record>}"

WANT_IP="$SERVER_IP"
[ -n "$WANT_IP" ] || WANT_IP="<this server's public IP>"

DNS_OK=1
DNS_WHY=""
if [ -z "$RESOLVED" ]; then
  DNS_OK=0
  DNS_WHY="$API_DOMAIN has no A record yet."
elif [ -n "$SERVER_IP" ] && [ "$RESOLVED" != "$SERVER_IP" ]; then
  DNS_OK=0
  DNS_WHY="$API_DOMAIN resolves to $RESOLVED but this server is $SERVER_IP."
fi

if [ "$DNS_OK" = 0 ]; then
  {
    echo
    echo "DNS is not ready: $DNS_WHY"
    echo
    echo "Add this record, then run this script again:"
    echo
    echo "    $API_DOMAIN.   A   $WANT_IP"
    echo
    echo "A fresh record can take a few minutes to a few hours to be visible. Check with:"
    echo "    dig +short $API_DOMAIN A @1.1.1.1"
  } >&2
  if [ "$DRY" = 1 ]; then
    # A dry run changes nothing, so it rehearses the rest anyway -- that is the
    # whole point of rehearsing. A real run stops here: the certificate
    # authority validates over HTTP on port 80, and a failed validation spends
    # an attempt against its rate limit.
    echo "  (dry run -- continuing anyway, since nothing here is applied)" >&2
  else
    exit 1
  fi
fi

say "port-80 challenge block"
# The app's own ACME block (guardian-acme.conf, from setup-domain.sh) already
# owns port 80 for the app and site names. Adding this name to that same block
# rather than declaring a second `listen 80` for it: two blocks both matching a
# name is a coin-flip on every reload.
#
# Read the block's current names, add this one if missing, rewrite from the
# template. Re-running with the name already present changes nothing.
NAMES_SP="$API_DOMAIN"
if [ "$DRY" = 1 ]; then
  echo "DRY  would read server_name from /etc/nginx/sites-available/guardian-acme.conf"
  echo "DRY  would rewrite it with $API_DOMAIN added"
else
  ACME_CONF=/etc/nginx/sites-available/guardian-acme.conf
  HAVE_ACME="$(ssh "$HOST" "test -f $ACME_CONF && echo yes || echo no")"
  EXISTING="$(ssh "$HOST" "grep -h -m1 '^\s*server_name' $ACME_CONF 2>/dev/null \
    | sed -e 's/.*server_name//' -e 's/;.*//' || true")"
  EXISTING="$(echo "$EXISTING" | tr -s ' ' ' ' | sed -e 's/^ *//' -e 's/ *$//')"
  # The block is rewritten from the template, so whatever is not carried over
  # here STOPS being renewable on port 80 -- including the app's own hostname.
  # If the file is there but its names cannot be parsed, stop: adding this name
  # is not worth silently dropping the app's.
  if [ "$HAVE_ACME" = yes ] && [ -z "$EXISTING" ]; then
    echo "ERROR: $ACME_CONF exists but no server_name could be read from it." >&2
    echo "       Refusing to rewrite it with only $API_DOMAIN -- that would take the" >&2
    echo "       app's and the site's names off the port-80 challenge block, and their" >&2
    echo "       next certificate renewal would fail. Inspect it by hand:" >&2
    echo "         ssh $HOST 'cat $ACME_CONF'" >&2
    exit 1
  fi
  NAMES_SP="$(printf '%s %s\n' "$EXISTING" "$API_DOMAIN" | tr -s ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/ $//')"
  echo "  port-80 names: $NAMES_SP"
fi
push deploy/nginx-acme.conf.tmpl /tmp/guardian-acme.tmpl
run "mkdir -p /var/www/acme \
  && sed 's/__NAMES__/$NAMES_SP/' /tmp/guardian-acme.tmpl > /etc/nginx/sites-available/guardian-acme.conf \
  && ln -sf /etc/nginx/sites-available/guardian-acme.conf /etc/nginx/sites-enabled/guardian-acme.conf \
  && nginx -t && systemctl reload nginx"

say "reading the names already on the certificate"
# Read them off the installed file rather than assuming: this list becomes the
# `-d` arguments of a real re-issue, and a name silently dropped here is a name
# that stops working the moment the new certificate is installed.
if [ "$DRY" = 1 ]; then
  echo "DRY  openssl x509 -in $FULLCHAIN -noout -text | (Subject Alternative Name)"
  echo "     would then decide: skip issuance if $API_DOMAIN is already listed,"
  echo "     otherwise re-issue with the existing names PLUS $API_DOMAIN,"
  echo "     keeping $APP_DOMAIN first so the certificate's paths do not move."
  CERT_NAMES=""
else
  CERT_NAMES="$(ssh "$HOST" "openssl x509 -in $FULLCHAIN -noout -text 2>/dev/null \
    | grep -A1 'Subject Alternative Name' | tail -1 \
    | tr -d ' ' | tr ',' '\n' | sed -n 's/^DNS://p' | tr '\n' ' '" || true)"
  CERT_NAMES="$(echo "$CERT_NAMES" | sed -e 's/^ *//' -e 's/ *$//')"
  if [ -z "$CERT_NAMES" ]; then
    echo "ERROR: could not read the names on $FULLCHAIN." >&2
    echo "       Refusing to re-issue against a guessed list -- that is how the app" >&2
    echo "       loses a name it needs. Check the file exists and is a certificate:" >&2
    echo "         ssh $HOST 'openssl x509 -in $FULLCHAIN -noout -text | head -20'" >&2
    exit 1
  fi
  echo "  currently: $CERT_NAMES"
fi

say "certificate"
if [ "$DRY" = 1 ]; then
  echo "DRY  (decision needs the real certificate -- see the previous step)"
elif echo " $CERT_NAMES " | grep -q " $API_DOMAIN "; then
  echo "  $API_DOMAIN is already on the certificate -- nothing to issue."
  echo "  (this is what makes re-running this script free)"
else
  # Order matters: $APP_DOMAIN first. acme.sh names its certificate directory
  # after the first -d, and the app's server block points at
  # $CERT_DIR/$APP_DOMAIN.*.pem -- putting a different name first would create a
  # second directory and leave the app on the old, now-unrenewed, files.
  ACME_D="-d $APP_DOMAIN"
  for n in $CERT_NAMES; do
    [ "$n" = "$APP_DOMAIN" ] && continue
    ACME_D="$ACME_D -d $n"
  done
  ACME_D="$ACME_D -d $API_DOMAIN"
  echo "  re-issuing with: $ACME_D"
  # --keylength ec-256 matches the existing certificate. The install-cert reload
  # hook is what keeps unattended renewals live without a second cron entry.
  # If the issue fails, the old certificate is still installed and still valid:
  # acme.sh writes the new one only on success, and install-cert only runs
  # because of the `&&`.
  ssh "$HOST" "/root/.acme.sh/acme.sh --issue $ACME_D -w /var/www/acme --keylength ec-256 --force \
    && /root/.acme.sh/acme.sh --install-cert -d $APP_DOMAIN --ecc \
         --key-file $CERT_DIR/$APP_DOMAIN.key.pem \
         --fullchain-file $FULLCHAIN \
         --reloadcmd 'nginx -t && systemctl reload nginx'"
  echo "  installed. names now:"
  ssh "$HOST" "openssl x509 -in $FULLCHAIN -noout -text | grep -A1 'Subject Alternative Name' | tail -1"
fi

say "installing the API server block"
# Template pushed and substituted rather than built inline: an nginx config is
# full of $host / $remote_addr / $proxy_add_x_forwarded_for, and every layer of
# local string, ssh and remote heredoc gets its own chance to eat the dollars.
push deploy/nginx-api.conf.tmpl /tmp/guardian-api.tmpl
run "sed -e 's/__API_NAMES__/$API_DOMAIN/' \
         -e 's/__CERT_DOMAIN__/$APP_DOMAIN/g' \
         /tmp/guardian-api.tmpl > /etc/nginx/sites-available/guardian-api.conf \
  && ln -sf /etc/nginx/sites-available/guardian-api.conf /etc/nginx/sites-enabled/guardian-api.conf"

say "testing and reloading nginx"
# `nginx -t` first: a failed test leaves the running config exactly as it was,
# so a bad template never takes the API, the app or the site down.
run "nginx -t && systemctl reload nginx"

say "verifying"
# Four checks, and the last three are the ones that matter: this hostname must
# NOT be able to reach the dashboard, the metrics endpoint, or the app.
run "curl -sS --max-time 15 https://$API_DOMAIN/v1/health; echo"
run "curl -sS -o /dev/null -w 'GET /v1/docs -> %{http_code} (expect 200)\n' --max-time 15 https://$API_DOMAIN/v1/docs"
run "curl -sS -o /dev/null -w 'GET /v1/whoami with no token -> %{http_code} (expect 401)\n' --max-time 15 https://$API_DOMAIN/v1/whoami"
run "curl -sS -o /dev/null -w 'GET /api/v1/me -> %{http_code} (expect 404, the dashboard is not here)\n' --max-time 15 https://$API_DOMAIN/api/v1/me"
run "curl -sS -o /dev/null -w 'GET /metrics -> %{http_code} (expect 404, not proxied from this host)\n' --max-time 15 https://$API_DOMAIN/metrics"
run "curl -sS -o /dev/null -w 'the app is still there: https://$APP_DOMAIN/api/v1/health -> %{http_code}\n' --max-time 15 https://$APP_DOMAIN/api/v1/health"

cat <<DONE

The API is live at https://$API_DOMAIN
  reference       https://$API_DOMAIN/v1/docs
  OpenAPI         https://$API_DOMAIN/v1/openapi.json
  liveness        https://$API_DOMAIN/v1/health

Customers create tokens in the app: https://$APP_DOMAIN/settings/api-tokens

The app and the public site were not touched, and share this certificate.

To take the API hostname down again (the app and site are unaffected):
  ssh $HOST 'rm -f /etc/nginx/sites-enabled/guardian-api.conf \\
    && nginx -t && systemctl reload nginx'
DONE
