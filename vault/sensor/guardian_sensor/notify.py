"""Local banner only. As of Task 40 the Vault HUB owns alert delivery
(ntfy, Slack, Microsoft Teams, generic webhook) through its own
`guardian_hub.integrations.registry.deliver` — routed per tenant, per
severity, with `/act/<token>/<action>` buttons the hub can serve and the
sensor cannot. This module used to run the full Nexus notification ladder
(ntfy -> macOS banner -> Slack/Teams/webhook -> SMTP fallback); every
network branch was removed here so an enrolled sensor never double-sends an
alert the hub is about to push itself.

An UN-enrolled, standalone sensor (nobody's hub to push through) is not
left silent: `notify()` only goes fully quiet when `config["notify"]
["local_only"]` is explicitly false AND the sensor is enrolled with a hub
(`sync.client_from_disk()` returns a real client). Otherwise it always shows
the macOS banner.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — the
banner-only remainder is still faithful to that original, just stripped of
every network branch.
"""
import subprocess
import urllib.request

from . import config, sync, util


def _mac_banner(title, msg):
    subprocess.run(
        ["osascript", "-e", f'display notification "{msg[:180].replace(chr(34), chr(39))}" with title "{title}"'],
        timeout=5, capture_output=True,
    )


def notify(title, msg, priority="default", actions=None, click=None, sev=None, env=None, cfg=None):
    """Show the local macOS banner (mac only) and return the list of
    channels used -- always `["local"]` or `[]`, never a network channel.

    `env`/`cfg` are accepted (and still read via `util.load_env()` /
    `config.load()` when omitted) only so existing call sites in
    `runner.py` keep working unchanged; neither is used for anything but
    the enrolment / local_only decision below."""
    cfg = cfg if cfg is not None else config.load()
    local_only = bool((cfg.get("notify") or {}).get("local_only"))
    enrolled = bool(sync.client_from_disk())

    if not local_only and enrolled:
        # The hub owns delivery for this sensor; pushing here too would
        # double-send every alert.
        return []

    sent = []
    if util.IS_MAC:
        try:
            _mac_banner(title, msg)
            sent.append("local")
        except Exception:
            pass
    return sent
