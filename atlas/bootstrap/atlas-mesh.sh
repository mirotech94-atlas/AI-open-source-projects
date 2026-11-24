#!/usr/bin/env bash
# Point atlas.storefront.test at the ATLAS box's mesh address, permanently.
#
#   sudo bash atlas-mesh.sh
#
# Run once. After this there is no tunnel, no sudo and no port: the name
# resolves to the mesh address on this Mac, the connection goes over WireGuard,
# and the certificate is the real one because the hostname is unchanged.
#
# It also replaces the temporary entry atlas-tunnel.sh used to add, which
# pointed the name at this laptop and made `ssh atlas.storefront.test` warn about a
# changed host key.
set -euo pipefail

MESH_IP="${ATLAS_MESH_IP:-100.114.123.14}"
NAME="${ATLAS_NAME:-atlas.storefront.test}"
HOSTS=/etc/hosts

[[ $EUID -eq 0 ]] || { echo "run with sudo — editing ${HOSTS} needs root"; exit 1; }

# Refuse to write an entry that cannot work. A hosts line pointing at an
# unreachable address is worse than none: the name stops resolving publicly too.
if ! nc -z -G 5 "$MESH_IP" 443 2>/dev/null; then
  echo "Cannot reach ${MESH_IP}:443 — is NetBird connected on this Mac?"
  echo "  netbird status"
  exit 1
fi

# Drop anything we previously added, then add the real one.
sed -i '' "/# added by atlas-tunnel/d;/# ATLAS over the NetBird mesh/d" "$HOSTS" \
  2>/dev/null || sed -i "/# added by atlas-tunnel/d;/# ATLAS over the NetBird mesh/d" "$HOSTS"
printf '%s %s # ATLAS over the NetBird mesh\n' "$MESH_IP" "$NAME" >> "$HOSTS"

dscacheutil -flushcache 2>/dev/null || true
killall -HUP mDNSResponder 2>/dev/null || true

echo "Done. ${NAME} -> ${MESH_IP} (over the mesh)"
echo
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://${NAME}/api/session" || echo 000)
verify=$(curl -s -o /dev/null -w '%{ssl_verify_result}' --max-time 10 "https://${NAME}/healthz" || echo x)
echo "  https://${NAME}  -> HTTP ${code}, certificate verify=${verify} (0 = trusted)"
echo
echo "Open https://${NAME} — no tunnel, no port."
echo "ssh root@${NAME} now also goes over the mesh to the real server."
