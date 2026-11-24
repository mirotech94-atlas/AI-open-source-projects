#!/bin/bash
# Host job (launchd, kept alive) on the Mac: a REVERSE ssh tunnel so the
# independent watchdog on the Spark reads the real ATLAS /healthz.
#
#   Spark 127.0.0.1:8080  ->  Mac 127.0.0.1:18081 (healthz_proxy.py: GET /healthz only, then the api)
#
# The Spark port equals the api's port on purpose: the api only answers a Host
# of 127.0.0.1:8080 / localhost:8080 (DEFAULT_ALLOWED_HOSTS), so the watchdog's
# request to http://127.0.0.1:8080/healthz on the Spark is accepted unchanged.
#
# Both ends are loopback: nothing is published on any interface and no new
# port is opened on the Mac. The Mac uses its OWN ssh key to the Spark; the
# Spark gets no key to the Mac. If the tunnel or ATLAS is down the watchdog's
# /healthz read fails, which is the honest answer (FAIL, never OK).
#
# Environment (defaults in brackets):
#   ATLAS_WATCHDOG_HOST        ssh name of the watchdog box [spark]
#   ATLAS_WATCHDOG_TUNNEL_PORT port on the Spark's loopback [8080]
#   ATLAS_LOCAL_PORT           the healthz-only proxy port on this Mac [18081]
#   ATLAS_WATCHDOG_SSH_KEY     optional ssh key file
set -u
HOST="${ATLAS_WATCHDOG_HOST:-spark}"
RPORT="${ATLAS_WATCHDOG_TUNNEL_PORT:-8080}"
LPORT="${ATLAS_LOCAL_PORT:-18081}"
SSH_BIN="${ATLAS_SSH:-ssh}"
ARGS=(-N -o BatchMode=yes -o ConnectTimeout=15 -o ExitOnForwardFailure=yes
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3
      -R "127.0.0.1:${RPORT}:127.0.0.1:${LPORT}")
[ -n "${ATLAS_WATCHDOG_SSH_KEY:-}" ] && ARGS+=(-i "$ATLAS_WATCHDOG_SSH_KEY")
# exec: launchd supervises ssh itself and restarts it (KeepAlive) when it ends.
exec "$SSH_BIN" "${ARGS[@]}" "$HOST"
