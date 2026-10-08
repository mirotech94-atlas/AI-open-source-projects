#!/bin/sh
# Guardian sensor container entrypoint.
#
#   RUN_DNS=1                start the DNS filter alongside the collector
#   ENROL_CODE + HUB_URL     enrol on first boot if this sensor has no hub.json
#
# Requires --network host: the collector sweeps the LAN it is on and the DNS
# filter binds :53.
set -e
: "${GUARDIAN_DATA:=/var/lib/guardian}"
export GUARDIAN_DATA
mkdir -p "$GUARDIAN_DATA"

if [ -n "$ENROL_CODE" ] && [ -n "$HUB_URL" ] && [ ! -f "$GUARDIAN_DATA/hub.json" ]; then
  echo "entrypoint: enrolling with $HUB_URL"
  guardian-sensor enrol "$ENROL_CODE" --hub "$HUB_URL" || echo "entrypoint: enrolment failed, continuing unenrolled"
fi

if [ "$RUN_DNS" = "1" ]; then
  echo "entrypoint: starting the DNS filter"
  guardian-sensor dns &
  DNS_PID=$!
  trap 'kill "$DNS_PID" 2>/dev/null || true' TERM INT
fi

exec guardian-sensor "$@"
