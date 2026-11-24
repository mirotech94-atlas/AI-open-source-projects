# Weekly restore drill (RA-H2 / S05)

The owner approved scheduling it on 2026-09-29. `atlas-restore-drill.timer` runs
`ops/restore_drill.sh` every Sunday at 07:30 UTC, after the 06:20 backup. The
drill restores the newest dump into a throwaway, network-less postgres, checks
it, writes `data/app/backups/restore_drill.json`, and always removes its own
container and volume. It refuses to start with under 5 GB free. The backups
check reads that JSON: a failed drill reads FAILING, and no drill on record
(or a pass older than 8 days) reads DEGRADED, never OK.

Install (root, on the ATLAS host):

    install -m 644 ops/restore-drill/atlas-restore-drill.{service,timer} /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now atlas-restore-drill.timer

Remove: `systemctl disable --now atlas-restore-drill.timer` and delete the two units.
