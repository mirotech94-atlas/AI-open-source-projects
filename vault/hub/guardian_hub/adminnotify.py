"""The operator's own admin channel.

Tenant alerts go to each tenant's integrations. This is the other stream --
the things the person RUNNING Vault wants to see across every tenant:
someone asked for access on the public site, a new tenant was created, a
user signed in for the first time, a sensor enrolled, a chat conversation
handed a job back to a human. One Slack incoming webhook, set once in the
hub's environment (GUARDIAN_ADMIN_SLACK_WEBHOOK); a no-op when unset, and
never raises -- a Slack outage must not fail a sign-in.
"""
import json
import logging
import threading
import urllib.request

from .config import settings

log = logging.getLogger(__name__)
TIMEOUT_S = 6

_ICON = {"lead": ":inbox_tray:", "tenant": ":new:", "signin": ":wave:", "sensor": ":satellite_antenna:",
         "handoff": ":raising_hand:", "info": ":information_source:",
         # The customer-facing stream, mirrored here so the operator sees
         # what his customers are seeing without joining every one of their
         # channels.
         "alert": ":rotating_light:", "resolve": ":white_check_mark:",
         "fix": ":wrench:", "report": ":bar_chart:"}

#: Rank used by the mirror's floor. Matches `ingest._PUSH_RANK`.
_RANK = {"INFO": 0, "WARN": 1, "CRIT": 2}


def enabled() -> bool:
    return bool((settings.admin_slack_webhook or "").startswith("https://hooks.slack.com/")) or _bot_ready()


def _bot_ready() -> bool:
    return bool((settings.slack_bot_token or "").strip() and (settings.admin_slack_channel or "").strip())


def _post_as_bot(text: str) -> None:
    from .integrations import slack as slack_mod

    res = slack_mod.bot_post(settings.admin_slack_channel, text, plain=True)
    if res.ok:
        slack_mod.remember_alert_thread(settings.admin_slack_channel, res.error, text)
    else:
        log.warning("admin notify (bot) failed: %s", res.error)
        if (settings.admin_slack_webhook or "").startswith("https://hooks.slack.com/"):
            _post(settings.admin_slack_webhook, {"text": text})


def _post(url: str, payload: dict) -> None:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "guardian-hub/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            pass
    except Exception as e:  # noqa: BLE001 -- observability only, never a failure
        log.warning("admin notify failed: %s", str(e)[:160])


def notify(kind: str, title: str, body: str = "", *, tenant: str = "") -> bool:
    """Fire-and-forget. Returns False when no admin channel is configured."""
    if not enabled():
        return False
    icon = _ICON.get(kind, _ICON["info"])
    head = f"{icon} *{title}*" + (f"  ·  `{tenant}`" if tenant else "")
    text = head + (f"\n{body}" if body else "")
    if _bot_ready():
        threading.Thread(target=_post_as_bot, args=(text,), daemon=True).start()
        return True
    payload = {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}
    threading.Thread(target=_post, args=(settings.admin_slack_webhook, payload), daemon=True).start()
    return True


def mirror_alert(kind: str, severity: str, title: str, body: str = "", *,
                 tenant: str = "") -> bool:
    """Mirror a TENANT event into the operator's own channel.

    The operator asked to see everything his customers see -- alerts, the
    fixes that ran, the reports that went out -- in one channel, instead of
    joining every customer's Slack. That is what this is: a copy, never the
    delivery itself. A customer's own channels are unaffected by it, and a
    failure here can never stop their alert going out, because `notify` is
    fire-and-forget on a daemon thread and swallows everything.

    The floor is a platform setting, not a constant. Across enough customers
    this becomes the noisiest channel the operator owns, and the fix for that
    has to be a toggle he can turn without a deploy -- `admin_mirror_floor`,
    with "off" to stop it entirely.
    """
    # Imported here, not at module scope: `platform_settings` reads the DB,
    # and this module is imported by request handlers that must stay
    # importable (and fast) with no database at all.
    try:
        from . import platform_settings as ps
        floor = ps.admin_mirror_floor()
    except Exception:  # noqa: BLE001 -- a settings read must never lose an alert
        floor = "INFO"
    if floor == "off":
        return False
    if _RANK.get(severity, 0) < _RANK.get(floor, 0):
        return False
    return notify(kind, title, body, tenant=tenant)
