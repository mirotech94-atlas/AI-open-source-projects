"""The reporting path, watched like anything else.

Every other module answers "is the thing I watch healthy". This one answers
"could I tell you if it weren't". Twenty green checks are worth nothing if the
channel they would report through is dead, and until this module existed
nothing in ATLAS looked at that channel at all.

It exists because `Channel.available()` is not a health check and was never
meant to be one. `SlackChannel.available()` returns True when two environment
variables are non-empty; it has no idea whether the token still works. Revoke
it and the routing table still picks Slack, `send()` raises, `notify()`
catches the exception per channel and logs a warning, and the message is
gone — with the system reading healthy from every angle, including its own.

`available()` is deliberately left as it is. `notify()` calls it once per
channel per send, plus again for the channel a fallback covers for, so a live
probe there would put network round trips in the delivery path — the path that
runs when something is already wrong. The probing belongs here instead, on the
scheduler's cadence, where a failure renders as a tile.

One limitation is stated rather than engineered away: a finding raised by this
module is routed through the same comms layer it is reporting on. If nothing
can deliver, nothing delivers this either. The dashboard tile is the backstop,
and a second channel is the real answer — not a daily "still alive" ping,
which teaches the reader to ignore exactly the message that matters.
"""
from __future__ import annotations

import base64
import os
import re
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import Check, Health, Observation, Severity
from .._http import get_json, post_json, redact

TWILIO_API = "https://api.twilio.com/2010-04-01"

NO_SLACK = ("no SLACK_BOT_TOKEN set, so this measured nothing. Mint one at "
            "api.slack.com -> your app -> OAuth & Permissions (scopes "
            "chat:write, chat:write.public) and install it with "
            "bootstrap/set-secret.sh.")

NO_TWILIO = ("no TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN set, so this measured "
             "nothing. Twilio is the only channel that rings a phone, and "
             "CRITICAL is the only severity that breaks quiet hours.")


# E.164: a leading +, a country code that cannot start with 0, and no more
# than 15 digits in total.
#
# This is checked because a malformed number is invisible from every other
# angle. Twilio wants `To` in E.164 on an outbound call, and a number stored
# as bare digits is rejected — while the account, the credentials and the
# channel all read as healthy. It cost an afternoon once.
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def _shape(value: str) -> str:
    """Describe a number without recording it.

    An Observation lands in Postgres and can reach a reasoner prompt. A
    personal phone number does not need to be in either for this finding to
    be actionable — the length and whether it carries a `+` is the whole
    diagnosis.
    """
    return (f"{len(value)} characters, "
            f"{'starts with +' if value.startswith('+') else 'no leading +'}")


def _definite(reason: str) -> bool:
    """Did the call reach the API and get told no?

    A rejected credential is a measurement: the channel is broken, and that is
    FAILING. A timeout or a DNS failure measured nothing and stays UNKNOWN.
    Collapsing the two would either cry wolf on every blip or — far worse —
    let a genuinely dead token sit under a grey tile nobody reads twice.
    """
    return reason.startswith(("HTTP 401", "HTTP 403"))


# ------------------------------------------------------------------- slack

async def check_slack_auth() -> Observation:
    """Ask Slack whether the bot token still works.

    `auth.test` needs no scopes and is POST-only. It answers HTTP 200 with
    `{"ok": false, "error": "invalid_auth"}` for a dead token, so the status
    code says nothing useful and the body is the whole measurement — which is
    precisely the shape of bug this module was written to catch.
    """
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        return Observation("slack_auth", Health.UNKNOWN, detail=NO_SLACK)

    data, reason = await post_json(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {token}"})
    if reason:
        return Observation(
            "slack_auth",
            Health.FAILING if _definite(reason) else Health.UNKNOWN,
            detail=redact(reason))
    if not isinstance(data, dict):
        return Observation(
            "slack_auth", Health.UNKNOWN,
            detail=f"auth.test returned {type(data).__name__}, expected an "
                   f"object — the assumed shape is wrong")

    if not data.get("ok"):
        return Observation(
            "slack_auth", Health.FAILING, 0.0, "valid",
            f"Slack rejected the bot token: "
            f"{redact(data.get('error') or 'no reason given')}. Every Slack "
            f"notification and every approval button is dead until it is "
            f"replaced.")

    team = str(data.get("team") or "?")
    if not os.getenv("SLACK_CHANNEL", "").strip():
        return Observation(
            "slack_auth", Health.DEGRADED, 1.0, "valid",
            f"the token is valid for {team}, but SLACK_CHANNEL is unset so "
            f"there is nowhere to post it")
    return Observation(
        "slack_auth", Health.OK, 1.0, "valid",
        f"token valid for {team} as {data.get('user') or '?'}",
        evidence={"team": team, "bot": str(data.get("user") or "")})


# ----------------------------------------------------------- slack channels

def _slack_targets() -> dict[str, list[str]]:
    """Every channel id ATLAS posts to, with the topics that land there."""
    from ...channels.slack import TOPIC_CHANNEL_ENV

    out: dict[str, list[str]] = {}
    for pair in os.getenv("SLACK_CHANNELS", "").split(","):
        name, _, cid = pair.partition(":")
        if cid.strip():
            out.setdefault(cid.strip(), []).append(name.strip())
    for topic, env in TOPIC_CHANNEL_ENV.items():
        cid = os.getenv(env, "").strip()
        if cid:
            out.setdefault(cid, []).append(topic)
    default = os.getenv("SLACK_CHANNEL", "").strip()
    if default:
        out.setdefault(default, []).append("default")
    return out


async def check_slack_channels() -> Observation:
    """Is the bot IN every channel it posts to?

    `chat:write.public` lets it post to a public channel it never joined, so
    every card arrives and looks fine. But Slack only sends message events
    for channels the bot is a member of: whatever Miro types there, ATLAS
    never hears. On 2026-09-27 that was #vpsie, #flycheap, #guardian and the
    default channel -- "I should be able to talk with them" in #vpsie went
    nowhere, and nothing anywhere said so.

    Read only: `conversations.info`. Joining is a write, and without the
    `channels:join` scope the bot cannot join anyway; the detail says the
    one line that fixes each channel.
    """
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        return Observation("slack_channels", Health.UNKNOWN, detail=NO_SLACK)
    targets = _slack_targets()
    if not targets:
        return Observation("slack_channels", Health.UNKNOWN,
                           detail="no Slack channel is configured, so there is "
                                  "nothing to be a member of")
    headers = {"Authorization": f"Bearer {token}"}
    me, reason = await post_json("https://slack.com/api/auth.test", headers=headers)
    bot = str((me or {}).get("user") or "Atlas") if not reason else "Atlas"
    deaf: list[str] = []
    unread: list[str] = []
    for cid, topics in targets.items():
        data, reason = await get_json("https://slack.com/api/conversations.info",
                                      headers=headers, params={"channel": cid})
        if reason or not isinstance(data, dict) or not data.get("ok"):
            why = reason or str((data or {}).get("error") or "no answer")
            unread.append(f"{cid} ({redact(why)})")
            continue
        info = data.get("channel") or {}
        if not info.get("is_member"):
            deaf.append(f"#{info.get('name') or cid} ({', '.join(topics)})")
    if deaf:
        return Observation(
            "slack_channels", Health.DEGRADED, float(len(deaf)), "channels",
            f"ATLAS posts to {len(deaf)} channel(s) it is not in, so it cannot "
            f"hear anything typed there: {'; '.join(deaf)}. In each one, type "
            f"/invite @{bot}",
            evidence={"not_member": deaf, "unread": unread})
    if unread:
        return Observation(
            "slack_channels", Health.UNKNOWN,
            detail=f"could not read {len(unread)} channel(s): {'; '.join(unread)}",
            evidence={"unread": unread})
    return Observation(
        "slack_channels", Health.OK, 0.0, "channels",
        f"in all {len(targets)} channel(s) it posts to, so a reply in any of "
        f"them reaches it",
        evidence={"channels": len(targets)})


# ------------------------------------------------------------------ twilio

async def check_twilio_auth() -> Observation:
    """Fetch the account record: the cheapest call that proves the
    credentials work and the account is not suspended.

    Basic auth is built here rather than handed to httpx directly so the call
    still goes through the helper that redacts and explains. The header value
    is a credential and never reaches an Observation — `explain` renders an
    HTTP error as its path alone.
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not (sid and auth):
        return Observation("twilio_auth", Health.UNKNOWN, detail=NO_TWILIO)

    basic = base64.b64encode(f"{sid}:{auth}".encode()).decode()
    data, reason = await get_json(f"{TWILIO_API}/Accounts/{sid}.json",
                                  headers={"Authorization": f"Basic {basic}"})
    if reason:
        return Observation(
            "twilio_auth",
            Health.FAILING if _definite(reason) else Health.UNKNOWN,
            detail=redact(reason))
    if not isinstance(data, dict):
        return Observation(
            "twilio_auth", Health.UNKNOWN,
            detail=f"the account endpoint returned {type(data).__name__}, "
                   f"expected an object")

    status = str(data.get("status") or "").strip().lower()
    if not status:
        return Observation(
            "twilio_auth", Health.UNKNOWN,
            detail="the account record carried no status field, so whether "
                   "this account can still place a call is unmeasured")
    if status != "active":
        return Observation(
            "twilio_auth", Health.FAILING, 0.0, "active",
            f"the Twilio account is {status}: the CRITICAL phone call will "
            f"not go out")

    missing = [n for n in ("TWILIO_FROM", "TWILIO_TO")
               if not os.getenv(n, "").strip()]
    if missing:
        return Observation(
            "twilio_auth", Health.DEGRADED, 1.0, "active",
            f"the credentials work, but {', '.join(missing)} is unset so "
            f"there is no number to send from or to")

    # Present is not the same as usable. Checking only for emptiness is what
    # let a number stored without its +1 read as OK while every inbound reply
    # was refused and every outbound message would have been rejected too.
    malformed = [f"{n} ({_shape(os.getenv(n, '').strip())})"
                 for n in ("TWILIO_FROM", "TWILIO_TO")
                 if not _E164.fullmatch(os.getenv(n, "").strip())]
    if malformed:
        return Observation(
            "twilio_auth", Health.DEGRADED, 1.0, "active",
            f"the credentials work, but {', '.join(malformed)} is not in "
            f"E.164 form. Twilio wants + and the digits, so the CRITICAL "
            f"phone call would be rejected before it rang.")

    return Observation("twilio_auth", Health.OK, 1.0, "active",
                       "account active; the CRITICAL phone call can be placed")


# --------------------------------------------------------------- the router

async def check_reachable() -> Observation:
    """Is anything listening for a routine message?

    Asked against `Severity.NORMAL` deliberately. `voice` is not in that tier
    — routing reaches it at HIGH and CRITICAL — so a Twilio-only setup leaves
    this FAILING, which is the honest reading: the morning brief, every
    routine finding and `test-channels` itself all send at NORMAL and would
    reach nobody.

    Not free, despite touching no API of its own: a channel's `available()`
    may make a live probe, hence the timeout below.
    """
    from ...kernel.comms import CHANNELS, candidates

    if not CHANNELS:
        return Observation(
            "reachable", Health.UNKNOWN,
            detail="no channels are registered at all — atlas.channels was "
                   "never imported, so this measured nothing rather than "
                   "finding nothing")

    ranked = await candidates(Severity.NORMAL)
    if not ranked:
        return Observation(
            "reachable", Health.FAILING, 0.0, "channels",
            "nothing can deliver a normal-severity message: the morning "
            "brief and every routine finding go nowhere. voice does not "
            "count here — routing reaches it at HIGH and CRITICAL, never "
            "for routine traffic — so a Twilio-only setup fails this on "
            "purpose.",
            evidence={"registered": sorted(CHANNELS)})

    return Observation(
        "reachable", Health.OK, float(len(ranked)), "channels",
        f"{ranked[0]} would take the next routine message"
        + (f" ({len(ranked)} available: {', '.join(ranked)})"
           if len(ranked) > 1 else " (the only one available)"),
        evidence={"ranked": ranked, "registered": sorted(CHANNELS)})


async def _last_send_before_restart() -> Observation | None:
    """A deploy restarts the process and empties LAST_SEND, which read as
    UNKNOWN after every deploy (2026-09-28). This check's own last reading
    that names a real send carries `sent_at`; that send is reported as what
    it was, with its time, not as a new measurement. None when there is no
    such reading or it cannot be read -- the caller then says UNKNOWN."""
    try:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """SELECT o.health, o.evidence FROM observations o
                 JOIN checks c ON c.id = o.check_id
                 JOIN properties p ON p.id = c.property_id
                WHERE p.name = 'comms' AND c.name = 'last_send'
                  AND o.evidence ? 'sent_at'
                ORDER BY o.at DESC LIMIT 1""")
    except Exception:                                   # noqa: BLE001
        return None
    if row is None:
        return None
    ev = row["evidence"]
    if isinstance(ev, str):
        import json
        ev = json.loads(ev)
    try:
        sent = datetime.fromisoformat(str(ev["sent_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    used = [str(u) for u in ev.get("used") or []]
    when = f"{sent:%Y-%m-%d %H:%M} UTC"
    if not used:
        return Observation(
            "last_send", Health.FAILING, 0.0, "channels",
            f"nothing sent since the restart; the last message before it "
            f"({when}) reached nobody", evidence={**ev})
    return Observation(
        "last_send", Health.OK, float(len(used)), "channels",
        f"nothing sent since the restart; the last message before it was "
        f"delivered via {', '.join(used)} at {when}", evidence={**ev})


async def check_last_send() -> Observation:
    """Did the last message ATLAS tried to send actually reach anyone?

    Deliberately not a staleness check. When nothing is wrong ATLAS sends
    nothing, so "it has been quiet for six hours" is the healthy case and
    alerting on it would train the reader to ignore this tile. What matters is
    the opposite: a send that was attempted and landed nowhere. `notify` has
    no retry, so that message is simply gone.
    """
    # Read live rather than binding LAST_SEND at import, which would freeze
    # the value at None for the life of the process.
    from ...kernel import comms as router

    last = router.LAST_SEND
    if last is None and (before := await _last_send_before_restart()) is not None:
        return before
    if last is None:
        return Observation(
            "last_send", Health.UNKNOWN,
            detail="nothing has been sent since this process started, so "
                   "whether delivery works is unmeasured. This is not an "
                   "all-clear: run `python -m atlas.cli test-channels` to "
                   "find out.")

    minutes = int((datetime.now(UTC) - last.at).total_seconds() // 60)
    when = f"{minutes} min ago"

    if not last.used:
        return Observation(
            "last_send", Health.FAILING, 0.0, "channels",
            f"the last message ATLAS sent reached nobody ({when}): "
            f"{last.title!r} at {last.severity.name}. notify() has no retry, "
            f"so it is gone.",
            evidence={"title": last.title, "severity": last.severity.name,
                      "used": [], "sent_at": last.at.isoformat()})

    if tuple(last.used) == ("queued",):
        return Observation(
            "last_send", Health.OK, 1.0, "channels",
            f"the last message ({when}) was held by quiet hours for the "
            f"morning brief, which is the designed behaviour",
            evidence={"used": ["queued"], "sent_at": last.at.isoformat()})

    return Observation(
        "last_send", Health.OK, float(len(last.used)), "channels",
        f"the last message was delivered via {', '.join(last.used)} ({when})",
        evidence={"used": list(last.used), "sent_at": last.at.isoformat()})


class CommsModule:
    name = "comms"
    title = "Reporting channel"
    # Never gated on a credential. A module that vanishes from the board when
    # it is unconfigured is useless here: "ATLAS cannot reach you" is the
    # finding, and it is most true exactly when nothing is set.
    requires_env = ()
    # Not required to load; each unlocks one check that is UNKNOWN without it.
    optional_env = ("SLACK_BOT_TOKEN", "SLACK_CHANNEL",
                    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            # CRITICAL on purpose. If routine delivery is dead but sms or
            # voice still work, a CRITICAL finding is the one thing that
            # still reaches a phone — and if they are dead too, nothing is
            # lost by having tried.
            Check("reachable", check_reachable, timedelta(minutes=5),
                  Severity.CRITICAL, "a channel that can reach Miro",
                  baseline_sigma=None, timeout=timedelta(seconds=25)),
            Check("last_send", check_last_send, timedelta(minutes=5),
                  Severity.HIGH, "whether the last message landed",
                  baseline_sigma=None),
            Check("slack_auth", check_slack_auth, timedelta(minutes=15),
                  Severity.HIGH, "the Slack token still works",
                  baseline_sigma=None),
            Check("slack_channels", check_slack_channels, timedelta(hours=1),
                  Severity.HIGH, "the bot is a member of every channel it "
                  "posts to, so it hears replies there",
                  baseline_sigma=None, timeout=timedelta(seconds=60)),
            Check("twilio_auth", check_twilio_auth, timedelta(minutes=15),
                  Severity.HIGH, "the Twilio account still works",
                  baseline_sigma=None),
        )

    def actions(self):
        return ()          # watching the channel never means changing it

    async def signals(self):
        # The scheduler calls this on every module. The Protocol's default
        # does not apply to a class that merely satisfies it, so without
        # this the scheduler logged an AttributeError every cycle.
        return ()

    def brief(self) -> str:
        return "Comms: which channel can reach you, and whether the last message landed"


MODULE = CommsModule()
