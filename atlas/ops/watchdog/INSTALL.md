# Installing the ATLAS watchdog (RA-F4)

**Not installed.** Installing needs Miro's OK and a Slack incoming webhook that
he creates for this purpose. Everything below runs on the **watchdog host**,
the FlyCheap VPS (`root@203.0.113.10`, Debian, Python 3.13, systemd 257).
It never runs on the ATLAS box, because the point is to see ATLAS die.

Design and the heartbeat proposal are in `docs/watchdog.md`.

## What Miro provides

1. A Slack incoming webhook (Slack app → Incoming Webhooks → Add to a channel).
   It is a new URL of the form `https://hooks.slack.com/services/...`, **not**
   ATLAS's bot token. The script refuses `xoxb-`/`xoxp-` values.
2. His OK to add one timer to the FlyCheap VPS. It costs one HTTPS GET and one
   TLS handshake to atlas.example.com every 5 minutes.

## Steps (on the FlyCheap VPS, as root)

```bash
# 1. Script and units (copy from this repo's ops/watchdog/)
install -d -m 755 /opt/atlas-watchdog
install -m 755 atlas_watchdog.py /opt/atlas-watchdog/
install -m 644 INSTALL.md /opt/atlas-watchdog/
install -m 644 atlas-watchdog.service atlas-watchdog.timer /etc/systemd/system/

# 2. The webhook: root-only file. Paste the URL with an editor, not on the
#    command line, so it stays out of shell history.
install -d -m 700 /etc/atlas-watchdog
install -m 600 watchdog.env.example /etc/atlas-watchdog/watchdog.env
${EDITOR:-nano} /etc/atlas-watchdog/watchdog.env      # set ATLAS_WATCHDOG_WEBHOOK=

# 3. One manual run before the timer exists. Expect exit 10 and ONE Slack
#    message saying "heartbeat UNKNOWN" until the heartbeat field ships.
systemctl daemon-reload
systemctl start atlas-watchdog.service
journalctl -u atlas-watchdog.service -n 20 --no-pager
cat /var/lib/atlas-watchdog/state.json

# 4. Only after the message arrived in Slack:
systemctl enable --now atlas-watchdog.timer
systemctl list-timers atlas-watchdog.timer --no-pager
```

## The alternate-route drill (separately authorised)

The plan says an alternate route must be proven, not only configured. With
Miro present, change `ATLAS_WATCHDOG_URL` in the env file to a path that
answers 404 (for example `https://atlas.example.com/healthz-drill`) and run
the service once. It should post one "PROBLEM healthz FAIL: HTTP 404"
message. Restore the URL and run it again. It should post one "recovered"
message. Nothing on the ATLAS host is touched.

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
systemctl disable --now atlas-watchdog.timer
rm /etc/systemd/system/atlas-watchdog.{service,timer}
systemctl daemon-reload
```

Remove `/etc/atlas-watchdog` and `/opt/atlas-watchdog` by hand if they are no
longer wanted. systemd keeps `/var/lib/private/atlas-watchdog` (DynamicUser
state) until you delete it.
