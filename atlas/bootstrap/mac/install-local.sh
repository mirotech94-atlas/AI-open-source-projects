#!/bin/zsh
# Install ATLAS's local launchd jobs on Miro's Mac. Run it yourself:
#
#   bootstrap/mac/install-local.sh
#
# What it does, and nothing else:
#   1. writes ~/Library/LaunchAgents/com.atlas.keepawake.plist (13:00 local,
#      bootstrap/mac/atlas-wake.sh) and com.atlas.backup-local.plist (03:30
#      local, bootstrap/mac/atlas-backup-local.sh), com.atlas.restore-drill.plist
#      (Sunday 08:00 local, ops/restore_drill.sh: the weekly restore drill),
#      pointing at THIS checkout,
#      and com.atlas.spark-vllm-tunnel.plist (every 60 s and at login:
#      bootstrap/mac/spark-vllm-tunnel.sh from THIS checkout: the forward to the
#      Spark's model :18000, redialled when dead or stale; survives reboot, sleep
#      and a killed ssh. Roll back with the previous commit's install-local.sh);
#   2. checks each with plutil -lint, then (re)loads it with launchctl;
#   3. PRINTS the one sudo line that schedules the daily wake. It never runs
#      sudo: you paste that line yourself.
# It reads no secret and writes nothing outside ~/Library/LaunchAgents and
# ~/Library/Logs.
set -euo pipefail
here=${0:A:h}
wt=${here:h:h}
domain="gui/$(id -u)"
agents="$HOME/Library/LaunchAgents"
mkdir -p "$agents" "$HOME/Library/Logs"

for label in com.atlas.keepawake com.atlas.backup-local com.atlas.restore-drill com.atlas.spark-vllm-tunnel; do
  plist="$agents/$label.plist"
  tmp="$plist.tmp"
  sed -e "s#__HOME__#$HOME#g" -e "s#__WT__#$wt#g" "$here/$label.plist" > "$tmp"
  plutil -lint "$tmp" >/dev/null
  mv "$tmp" "$plist"
  launchctl bootout "$domain/$label" 2>/dev/null || true
  launchctl bootstrap "$domain" "$plist"
  echo "loaded $plist"
done

if [[ ! -s "$wt/data/backup-age.pub" ]]; then
  echo "note: $wt/data/backup-age.pub is missing; backups fail until it is there" >&2
fi

cat <<'MSG'

One step is yours (it needs sudo; this script never runs it):

    sudo pmset repeat wakeorpoweron MTWRFSU 13:05:00

13:05 local is 17:05 UTC under EDT and 18:05 UTC under EST, after the 17:00 UTC
tutorial hour either way. Check it with: pmset -g sched
MSG
