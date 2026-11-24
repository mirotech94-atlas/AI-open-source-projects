# Installing the ATLAS watchdog (RA-F4, Phase 1)

**Nothing here is installed.** The lead runs these steps. Part A is on the
**Spark** (`ssh spark`, user systemd, **no sudo**). Part B is on the **Mac**
(two launchd jobs). Part C is one Caddyfile change. Design: `docs/watchdog.md`.

The watchdog reads one public URL and posts to its own Slack webhook. It shares
no credential with ATLAS. The webhook is a secret: it lives only in the Spark's
env file (mode 0600), never in this repo, a chat or a log.

## What the owner provides first

1. A Slack **incoming webhook** made for this purpose
   (`https://hooks.slack.com/services/...`). Not ATLAS's bot token: bot-token
   style values are refused.
2. Confirmation that the Caddyfile.public `/healthz` route (part C) is shipped,
   or accept that the watchdog logs `HTTP 404` until it is.

## Part A: the Spark (user units)

Run from the repo checkout on the Mac, then on the Spark. Replace nothing in
the unit files: they use `%h` (the user's home).

```bash
# A1. Copy the script and the units to the Spark
ssh spark 'mkdir -p ~/.local/share/atlas-watchdog ~/.config/systemd/user ~/.config/atlas-watchdog'
scp ops/watchdog/atlas_watchdog.py spark:.local/share/atlas-watchdog/
scp ops/watchdog/atlas-watchdog.user.service spark:.config/systemd/user/atlas-watchdog.service
scp ops/watchdog/atlas-watchdog.user.timer   spark:.config/systemd/user/atlas-watchdog.timer
scp ops/watchdog/watchdog.env.example        spark:.config/atlas-watchdog/watchdog.env

# A2. On the Spark: lock the env file, then put the webhook in with an editor
#     (not on the command line, so it stays out of shell history)
ssh spark
chmod 600 ~/.config/atlas-watchdog/watchdog.env
${EDITOR:-nano} ~/.config/atlas-watchdog/watchdog.env   # set ATLAS_WATCHDOG_WEBHOOK=
#   ATLAS_WATCHDOG_URL=https://agency.example.com/healthz is already set.

# A3. One manual run before the timer exists (still on the Spark)
systemctl --user daemon-reload
systemctl --user start atlas-watchdog.service
journalctl --user -u atlas-watchdog.service -n 20 --no-pager
cat ~/.local/state/atlas-watchdog/seen.json
```

Expect, until the part C route ships: `healthz FAIL: HTTP 404`, exit 10, ONE
Slack message, and a `seen.json` with `"healthz_state": "FAIL"`. After part C:
`healthz OK`, and `heartbeat` UNKNOWN or OK depending on the ATLAS build.

```bash
# A4. Only after the Slack message arrived: start the timer (on the Spark)
systemctl --user enable --now atlas-watchdog.timer
systemctl --user list-timers atlas-watchdog.timer --no-pager
loginctl show-user "$USER" -p Linger        # must say Linger=yes (it does today)
```

## Part B: the Mac host jobs (launchd, read-only)

Two jobs: disk status (every 15 min) and the pull of `seen.json` from the
Spark (every 5 min). Templates use `__WT__` (the repo checkout the stack runs
from) and `__HOME__`.

```bash
WT=$HOME/atlas-agency          # the checkout the compose stack runs from
cd "$WT/ops/watchdog/host"
for j in com.atlas.disk-status com.atlas.watchdog-seen com.atlas.watchdog-proxy com.atlas.watchdog-tunnel; do
  sed -e "s#__WT__#$WT#g" -e "s#__HOME__#$HOME#g" "$j.plist" \
    > "$HOME/Library/LaunchAgents/$j.plist"
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$j.plist"
done
# Check, a few seconds later:
cat "$WT/data/app/backups/disk.json"
cat "$WT/data/app/backups/watchdog_seen.json"
```

The pull job needs `ssh spark` to work without a prompt from launchd's
environment (BatchMode, the Mac's own key). If it cannot, set
`ATLAS_WATCHDOG_SSH_KEY` in the plist's `EnvironmentVariables`. A failed pull
leaves the old file, and `watchdog_seen` reads UNKNOWN within 15 minutes.
Optional env for `disk_status.sh`: `ATLAS_DISK_PATH`, `ATLAS_DISK_ROOTS`,
`ATLAS_DISK_TOP_N`. The ATLAS thresholds are `ATLAS_DISK_FLOOR_GIB` (4) and
`ATLAS_DISK_WARN_GIB` (10) in the api's environment.

The off-box copy target (`ATLAS_OFFBOX_TARGET`, used by `ops/offbox/`) is this
same Spark: `backup-host:atlas-offbox/` through the Mac's `spark` ssh alias. Create the
directory once on the Spark (`ssh spark 'mkdir -p ~/atlas-offbox'`).

## Part C: the public route (one Caddyfile.public change)

The Spark can only reach public names. `Caddyfile.public` now has an exact
`GET /healthz` handle (2026-11-15) that rewrites to the API's `/public/ping`:
it answers only `{"ok":true,"status":"ok","time":...}` and opens no other path. It does
NOT carry the two heartbeat ages; those stay on the mesh-only `/healthz`, so a
route-level liveness check from the Spark proves the public edge and API are up,
not that the cycle is fresh. Deploy (recreating `shop-public`) is the lead's
step; this change does not touch it.

## The alternate-route drill (separately authorised)

With the owner present, set `ATLAS_WATCHDOG_URL` in the Spark's env file to a path
that answers 404 (for example `https://agency.example.com/healthz-drill`) and run
the service once. It should post one "PROBLEM healthz FAIL: HTTP 404" message.
Restore the URL and run again; it should post one "recovered" message.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | all checks OK |
| 10 | a check is not OK; alerted on change (`SuccessExitStatus=10`, so the unit is not marked failed) |
| 2 | no alternate route configured, or the value is not a webhook URL. The unit shows failed on every run until it is fixed |
| 3 | the alert could not be delivered. State is not advanced, so the next run retries |
| 4 (or 1) | the watchdog itself crashed (bad config value, unwritable state). The unit fails; see the journal |

## Removing it

```bash
# On the Spark
systemctl --user disable --now atlas-watchdog.timer
rm ~/.config/systemd/user/atlas-watchdog.{service,timer}
systemctl --user daemon-reload
rm -r ~/.local/share/atlas-watchdog ~/.config/atlas-watchdog ~/.local/state/atlas-watchdog

# On the Mac
for j in com.atlas.disk-status com.atlas.watchdog-seen com.atlas.watchdog-proxy com.atlas.watchdog-tunnel; do
  launchctl bootout "gui/$(id -u)/$j"; rm "$HOME/Library/LaunchAgents/$j.plist"
done
```

After removal `watchdog_seen` and `disk` read UNKNOWN within 15 minutes and
1 hour. That is the intended behaviour: a missing watcher is never "OK".

Part B also installs `com.atlas.watchdog-tunnel` (reverse tunnel, see
`docs/watchdog.md`, "Simplest working design"). On the Spark set
`ATLAS_WATCHDOG_URL=http://127.0.0.1:8080/healthz` in the env file.
