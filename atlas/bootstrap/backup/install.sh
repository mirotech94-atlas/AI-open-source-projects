#!/bin/bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Install the nightly backup and weekly housekeeping on the ATLAS host, and
# the backup receiver on the GitLab VM. Idempotent; run as root on the ATLAS
# host after the files are in /opt/atlas/bootstrap/backup.
set -euo pipefail
OFFSITE_HOST="${1:-162.220.52.147}"
KEY=/root/.ssh/atlas_backup_ed25519
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "atlas-backup@$(hostname)" -f "$KEY"
# The receiver: user atlasbak, which may only write under /srv/atlas-backups.
ssh -o BatchMode=yes "root@$OFFSITE_HOST" "
  set -e
  command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync >/dev/null)
  id atlasbak >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh atlasbak
  install -d -o atlasbak -m 700 /srv/atlas-backups /srv/atlas-backups/media
  install -d -o atlasbak -m 700 ~atlasbak/.ssh
  printf 'command=\"rrsync -wo /srv/atlas-backups\",restrict %s\n' '$(cat "$KEY.pub")' > ~atlasbak/.ssh/authorized_keys
  chown atlasbak ~atlasbak/.ssh/authorized_keys && chmod 600 ~atlasbak/.ssh/authorized_keys
  # Off-box copies are kept 30 days; masters 180.
  printf '%s\n' '17 7 * * * atlasbak find /srv/atlas-backups -maxdepth 1 -name \"atlas-*.dump.age\" -mtime +30 -delete' \
                '27 7 * * 0 atlasbak find /srv/atlas-backups/media -name \"*.mp4\" -mtime +180 -delete' > /etc/cron.d/atlas-backups
"
ssh-keyscan -q "$OFFSITE_HOST" >> /root/.ssh/known_hosts 2>/dev/null
sort -u -o /root/.ssh/known_hosts /root/.ssh/known_hosts
install -m 644 atlas-backup.service atlas-backup.timer atlas-housekeeping.service atlas-housekeeping.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now atlas-backup.timer atlas-housekeeping.timer
echo installed
