"""Channel routing.

ATLAS talks to Miro over whichever channel suits the moment, and listens on all
of them. A Channel is a plugin like anything else: implement the protocol, drop
it in `atlas/channels/`, and it joins the routing table.

Routing is by severity, not preference, because the failure mode of this whole
system is Miro muting it in week three.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from typing import Literal, Protocol

from .contracts import Severity

log = logging.getLogger("atlas.comms")


@dataclass(frozen=True, slots=True)
class Button:
    label: str
    value: str          # "approve:41", "decline:41", "detail:41"


# What a message IS, as opposed to how urgent it is. Only an "alert" may be
# shortened when he is out (see `brevity`): a digest (the morning brief, the
# weekly, the nightly review, a list of what expired) and a receipt (a
# decision he made, a call that was taken) are the content itself, and
# cutting one loses exactly the part he needed.
MessageKind = Literal["alert", "digest", "receipt"]


@dataclass(frozen=True, slots=True)
class Outbound:
    title: str
    body: str
    severity: Severity = Severity.NORMAL
    buttons: Sequence[Button] = ()
    thread: str | None = None
    # Which part of the estate this is about -- a module name, usually.
    # Channels that can separate their traffic use it to pick where a
    # message lands; channels that cannot ignore it. Empty means "no
    # particular topic", which is a real answer for a brief that spans
    # everything, not a missing one.
    topic: str = ""
    kind: MessageKind = "alert"
    # An explicit, stable id for "this specific ongoing thing" -- the
    # scheduler's check-keyed pages pass `<module>.<check>` (`finding.check`).
    # Only `escalation.may_call`'s G3 re-ring rule reads it, to tell two
    # CRITICAL pages apart with more confidence than a title comparison
    # alone; a caller with nothing better than a title leaves this unset and
    # `escalation` falls back to title + its own rolling window.
    incident_key: str | None = None
    # A Slack channel id to answer IN, overriding the topic's channel. Set
    # only for a reply to something he typed: the answer belongs where the
    # question was asked, under it. Before this every typed reply went to
    # the `decisions` topic carrying a thread ts from another channel, so
    # "how many servers do I have?" asked in #cloudhost was answered in
    # #atlas-decisions, detached from the question (2026-09-27). Channels
    # other than Slack ignore it.
    channel: str = ""
    # What the reader needs to do, shown as the header's TYPE: INFO, REVIEW
    # (approve content: a post, a reel), ACTION (approve an action),
    # INCIDENT, RESOLVED. Empty lets the channel derive it; it is never the
    # execution status, which the body states.
    presentation: str = ""
    # The Slack header, word for word, when the "[Project] TYPE: title" form
    # is not the right one: a proposal card's "[Area] #357 · what approving
    # does" (kernel/board.py). The title stays the card's key ("#357 — …"):
    # `sent_ref`, the receipts and the spoken opening all read it.
    header: str = ""


# A line by itself in a body: what is above it is the decision, what is below
# it the reason and evidence. Slack draws it as a divider; a channel that
# shows text shows the line.
CARD_RULE = "────────"


@dataclass(frozen=True, slots=True)
class Delivery:
    """What became of the last message ATLAS tried to send.

    Held in memory on purpose. The notification path must not acquire a
    database write — `notify()` is what runs when things are already going
    wrong — and a fresh process that has sent nothing yet genuinely knows
    nothing, which the `comms` module reports as UNKNOWN rather than
    inventing an all-clear.
    """

    at: datetime
    title: str
    severity: Severity
    used: tuple[str, ...]


LAST_SEND: Delivery | None = None

# Where recent messages landed, by title: {channel name: that channel's own
# reference for the message (Slack's `ts`)}. In memory and bounded, like
# LAST_SEND, and for the same reason: the notification path acquires no
# database write. It lets a caller put something UNDER a card it just
# raised -- a reel's preview in the card's thread -- without `notify`
# changing what it returns. Keyed by title, not "the last send", so a
# concurrent message cannot be mistaken for the card.
REFS_KEPT = 64
_REFS: dict[str, dict[str, str]] = {}


def _remember(title: str, channel: str, ref: str) -> None:
    _REFS.setdefault(title, {})[channel] = ref
    while len(_REFS) > REFS_KEPT:
        _REFS.pop(next(iter(_REFS)))


def sent_ref(title_prefix: str, channel: str) -> str | None:
    """The newest recorded reference, on `channel`, for a message whose
    title starts with `title_prefix` (a card's "#41 — "). None when it did
    not go there, or was never sent."""
    for title in reversed(list(_REFS)):
        if title.startswith(title_prefix) and channel in _REFS[title]:
            return _REFS[title][channel]
    return None


async def _read_file(path: str) -> bytes:
    def read() -> bytes:
        with open(path, "rb") as fh:
            return fh.read()
    return await asyncio.to_thread(read)


async def attach_to_card(pid: int, *, topic: str, path: str, filename: str,
                         title: str, note: str) -> str:
    """Put a local file where card `pid`'s decision is being made, the same
    way `kernel.reels.preview_under_card` has always uploaded a reel's
    preview: into the topic's own Slack channel (a file in a thread is
    invisible from the channel), with a comment naming the card. Never
    raises -- a caller's own signal must stand whatever becomes of its
    attachment. Returns "uploaded" on success, else the reason."""
    slack = CHANNELS.get("slack")
    if slack is None or not hasattr(slack, "upload"):
        return "Slack is not configured"
    try:
        data = await _read_file(path)
    except OSError as exc:
        return f"could not read {path}: {exc}"
    try:
        why = await slack.upload(
            channel=slack.channel_for(topic), thread=None, filename=filename,
            data=data, title=title, comment=f"#{pid} — {note}")
    except Exception as exc:  # noqa: BLE001
        from ..modules._http import explain
        return f"Slack upload failed: {explain(exc)}"
    return why or "uploaded"


@dataclass(frozen=True, slots=True)
class Inbound:
    """Something Miro said, from any channel, normalised."""

    channel: str
    sender: str
    text: str
    thread: str | None = None
    at: datetime = None                                  # type: ignore[assignment]


class Channel(Protocol):
    name: str
    rich: bool          # supports buttons
    interactive: bool   # Miro can reply through it

    async def send(self, msg: Outbound) -> str | None: ...
    async def available(self) -> bool: ...


CHANNELS: dict[str, Channel] = {}


def register(channel: Channel) -> None:
    CHANNELS[channel.name] = channel


def _quiet_now() -> bool:
    window = os.getenv("QUIET_HOURS", "")
    if "-" not in window:
        return False
    start_s, end_s = window.split("-", 1)
    now = datetime.now().astimezone().time()
    start = time.fromisoformat(start_s.strip())
    end = time.fromisoformat(end_s.strip())
    return (start <= now or now < end) if start > end else (start <= now < end)


# Severity -> ordered channel preference. First available one wins, except
# CRITICAL, which fans out to every channel listed.
# SMS is deliberately absent. A US 10DLC number cannot send without an
# approved A2P campaign, and the campaign cannot pass vetting without a
# publicly hosted privacy policy and terms page — which would mean widening
# a public surface that is deliberately limited to /webhooks/* and /healthz,
# to unblock a channel the router never actually reached: Slack already sits
# ahead of it at HIGH and wins. Messages go over Slack; the phone is for
# CRITICAL only. Twilio remains, for voice.
ROUTING: dict[Severity, tuple[str, ...]] = {
    Severity.INFO: ("slack", "matrix"),
    Severity.NORMAL: ("slack", "matrix"),
    # 2026-09-22, his words: "atlas only calls me when there are major
    # updates or emergencies -- other than that nothing else." HIGH is a
    # finding he should see today, not one that cannot wait for him to open
    # Slack. Only CRITICAL rings the phone, and CRITICAL is exempt from the
    # call budget in `escalation` by definition.
    Severity.HIGH: ("slack", "matrix"),
    Severity.CRITICAL: ("voice", "slack", "matrix"),
}


async def candidates(severity: Severity, *, home: bool | None = None,
                     title: str | None = None,
                     incident_key: str | None = None) -> list[str]:
    """The channels that would take a message of this severity, in order.

    Split out of `notify` so the `comms` module's `reachable` check can ask
    the router the same question the router answers, instead of
    reimplementing the preference order and drifting from it. It reads
    availability and nothing about the message, so it cannot tell you whether
    a *particular* message would be delivered — only whether anything is
    listening at that severity.

    `home`, `title` and `incident_key` are forwarded to `escalation.may_call`
    (G3): together they only ever turn an already-answered CRITICAL re-ring,
    for that exact page, into a text once he's home, so a caller with nothing
    to report about presence or the page's identity can simply leave them
    out.
    """
    ranked: list[str] = []
    for name in ROUTING.get(severity, ("slack", "matrix")):
        channel = CHANNELS.get(name)
        if channel is None or not await channel.available():
            continue
        # The call budget. A channel that is reachable is not the same as a
        # channel that should be used right now: ringing him a fourth time
        # today is how the phone stops being answered at all, and then none
        # of this works. CRITICAL is exempt by definition.
        if name == "voice":
            from .escalation import may_call
            allowed, why = may_call(severity, home=home, title=title,
                                    incident_key=incident_key)
            if not allowed:
                log.info("not calling: %s", why)
                continue
        ranked.append(name)
    return ranked


def _record(msg: Outbound, used: Sequence[str]) -> None:
    global LAST_SEND
    LAST_SEND = Delivery(datetime.now(UTC), msg.title, msg.severity,
                         tuple(used))


async def _audit_undelivered(msg: Outbound) -> None:
    """A message that reached nobody is the one event this system cannot
    afford to lose, and a log line is not a record.

    Best effort on purpose: delivery has already failed once here, and a
    database that is also unhappy must not turn that into an exception
    escaping `notify` into whatever was trying to raise the alarm.
    """
    try:
        from .audit import audit
        await audit("comms.undelivered", msg.title,
                    {"severity": msg.severity.name,
                     "routed_to": list(ROUTING.get(msg.severity, ()))})
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record the undelivered message: %s", exc)


# ---------------------------------------------------------------- brevity
#
# Miro, 2026-09-24: "ATLAS can call me anytime in an emergency; he should
# realise I'm not home when I'm not on my internet and be quick and straight
# to the point." Calling him anytime is already the CRITICAL path. What this
# adds is the second half: when `rhythm.presence()` says he is off his own
# network (not merely off the mesh — see that module), anything below
# CRITICAL is cut to two sentences and goes as a text, never a call.
# CRITICAL is never trimmed and never even asks where he is — an emergency is
# said in full, wherever he is. An approval card is never trimmed either: it
# is what he reads before he decides, and half of it is worse than none.
# Presence that cannot be read changes nothing (invariant 6): an unreachable
# mesh is not evidence that he is out.

BRIEF_SENTENCES = 2
PRESENCE_TIMEOUT_S = 4.0
TRIMMED = " — more on the dashboard"

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


async def presence() -> dict:
    """`rhythm.presence()`, imported late: rhythm pulls in memory and the
    database layer, and the notification path must not depend on either
    having imported cleanly."""
    from .rhythm import presence as read
    return await read()


def shorten(text: str, sentences: int = BRIEF_SENTENCES) -> str:
    """The first `sentences` sentences of `text`, or `text` unchanged."""
    flat = " ".join((text or "").split())
    parts = [p for p in _SENTENCE.split(flat) if p]
    if len(parts) <= sentences:
        return text
    return " ".join(parts[:sentences])


# CRITICAL is the only severity that ever reaches `channels.twilio`
# (`ROUTING`), so this is not "brevity for the phone call" as a second tier
# below CRITICAL -- it is the one call that always rings, made shorter and
# more useful when he is away from his own network. G2 of the reel audit:
# "Jarvis calls you mid-workout: 30 seconds, one decision."
CALL_AWAY_LINE = (
    "Miro is out (not on his own network). Open with the single decision "
    "you need in one sentence, keep this call under 30 seconds, offer to "
    "text the details, and do not call again about this once he is home."
)


async def call_brevity_line() -> str | None:
    """The away-brevity instruction for a call ATLAS places when he is out,
    or `None` when nothing should change. `channels.twilio` marks the call
    away when this is set, and `api.phone` adds it to every turn's prompt on
    that call (and says, out loud, that it will be quick).

    Reads presence with the same short timeout `brevity()` uses, and an
    unreadable mesh or `home` being `True`/unknown changes nothing at all
    (invariant 6: never guess). This never suppresses the call -- CRITICAL
    always rings under `ROUTING` regardless of what this returns; it only
    shortens what is said once the call connects.
    """
    try:
        seen = await asyncio.wait_for(presence(), PRESENCE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        log.info("call_brevity_line: presence unreadable, no change: %s", exc)
        return None
    if (seen or {}).get("home") is False:
        return CALL_AWAY_LINE
    return None


async def brevity(msg: Outbound) -> tuple[Outbound, bool]:
    """(the message to send, whether it may ring the phone).

    "He should realise I'm not home when I'm not on my internet and be
    quick" — Miro.

    Unchanged and free to ring unless he is known to be out — then an
    alert's body is cut to two sentences and says so (`TRIMMED`), and the
    phone is off the table for anything below CRITICAL. Only `kind="alert"`
    is ever cut: digests and receipts go whole, and so does anything with
    buttons.

    What this trims is texts, never a call worth making: only CRITICAL rings
    the phone under `ROUTING`, and CRITICAL is never trimmed, so an
    emergency call is always said in full. Being out shortens the ordinary
    messages and guarantees that none of them turns into a call, whatever
    `ROUTING` says later.
    """
    if msg.severity >= Severity.CRITICAL:
        return msg, True
    try:
        seen = await asyncio.wait_for(presence(), PRESENCE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        log.info("brevity: presence unreadable, sending in full: %s", exc)
        return msg, True
    if (seen or {}).get("home") is not False:
        return msg, True
    if msg.buttons or msg.kind != "alert":
        return msg, False
    short = shorten(msg.body)
    if short == msg.body:
        return msg, False
    return replace(msg, body=short + TRIMMED), False


async def _dedicated_slack(topic: str) -> bool:
    """Whether this topic has a Slack channel named for it, and Slack is up."""
    if not topic:
        return False
    slack = CHANNELS.get("slack")
    home_for = getattr(slack, "home_for", None)
    if slack is None or home_for is None or not home_for(topic):
        return False
    return await slack.available()


def _is_card_or_preview(msg: Outbound) -> bool:
    """A proposal card (it carries the approve/decline/details buttons) or
    its preview receipt (`kernel.reels.preview_under_card`'s fallback link,
    `kind='receipt'`) -- as opposed to a plain alert like the 17:00 nudge,
    which shares the same topic and dedicated channel but is neither."""
    return bool(msg.buttons) or msg.kind == "receipt"


async def notify(msg: Outbound) -> list[str]:
    """Send. Returns the channels actually used."""
    # Plan S01: a credential in a provider or log payload never reaches a
    # card. Every current secret VALUE and credential query parameter is
    # masked here, before the quiet-hours branch, so the copy queued for
    # the morning brief is clean too. `redact_stored`, not `redact`: the
    # Bearer/Basic heuristic would mangle prose ("basic health check").
    from ..modules._http import redact_stored
    msg = replace(msg, title=redact_stored(msg.title) if msg.title else msg.title,
                  body=redact_stored(msg.body) if msg.body else msg.body)
    # A card (or its preview) routed to a Slack channel NAMED for its topic
    # -- #instagram-reels via SLACK_INSTAGRAM_REELS_CHANNEL, an acked
    # module's own channel -- reaches that channel immediately, even during
    # quiet hours: a topic channel is not a page, it is where the thing is
    # already followed, and it does not wake him. This adds no phone
    # delivery -- only Slack, and only Slack, is tried. A plain
    # alert on the same topic (the 17:00 nudge) is not a card and still
    # waits for the morning brief like everything else below CRITICAL.
    if (_quiet_now() and msg.severity < Severity.CRITICAL
            and _is_card_or_preview(msg) and await _dedicated_slack(msg.topic)):
        slack = CHANNELS.get("slack")
        try:
            ref = await slack.send(msg)
            if ref:
                _remember(msg.title, "slack", str(ref))
        except Exception as exc:  # noqa: BLE001
            log.warning("channel slack failed during quiet hours: %s", exc)
            await _audit_undelivered(msg)
            _record(msg, [])
            return []
        log.info("quiet hours: %r went straight to its Slack channel "
                 "(topic %r) -- nothing else", msg.title, msg.topic)
        _record(msg, ["slack"])
        return ["slack"]

    if _quiet_now() and msg.severity < Severity.CRITICAL:
        from .state import queue_for_brief
        await queue_for_brief(msg)
        log.info("quiet hours: queued %r for the morning brief", msg.title)
        _record(msg, ["queued"])
        return ["queued"]

    # After the quiet-hours branch, so what waits for the brief waits whole.
    msg, may_ring = await brevity(msg)
    used: list[str] = []

    # G3: presence for the escalation re-ring rule. Only read for CRITICAL --
    # `brevity()` above already covers everything below it, and reading the
    # mesh for a call that will never happen has no reason to exist. An
    # unreadable mesh leaves `home` at `None`, which `may_call` treats the
    # same as "don't know" (invariant 6): it never turns a ring into a text
    # on a guess. Not logged here: `call_brevity_line` (`channels.twilio`)
    # reads the same presence for the same underlying page and already logs
    # on this exact failure -- logging it again here would double a warning
    # about one root cause rather than add information.
    home = None
    if msg.severity >= Severity.CRITICAL:
        try:
            seen = await asyncio.wait_for(presence(), PRESENCE_TIMEOUT_S)
            home = (seen or {}).get("home")
        except Exception:  # noqa: BLE001, S110
            pass

    ranked = await candidates(msg.severity, home=home, title=msg.title,
                              incident_key=msg.incident_key)
    # A topic with a Slack channel NAMED for it (#instagram-reels, an acked
    # module's own channel) is delivered there first: that channel is where
    # the thing is followed, answered and acknowledged. Only BELOW CRITICAL:
    # a CRITICAL page rings first, always -- the named channel is added after
    # everything else so its Acknowledge button still lands there, without
    # making the call wait behind a Slack post.
    dedicated = await _dedicated_slack(msg.topic)
    if dedicated and msg.severity < Severity.CRITICAL:
        ranked = ["slack"] + [n for n in ranked if n != "slack"]
    elif dedicated and "slack" not in ranked:
        ranked = [*ranked, "slack"]
    for name in ranked:
        channel = CHANNELS.get(name)
        if channel is None:
            continue
        # Dropped here and not in `candidates()`: the comms module's
        # `reachable` check asks `candidates()` whether anything is
        # listening, and he being out does not make the phone unreachable.
        if name == "voice" and not may_ring:
            log.info("not calling about %r: he is out, texting instead",
                     msg.title)
            continue
        # Don't send a buttonless copy to a channel that can't render them
        # unless it's the only one we have. CRITICAL is exempt: it is defined
        # as fanning out to every channel, and the buttons degrade to a
        # numbered reply list rather than vanishing. Without this exemption a
        # CRITICAL proposal (which always carries approve/decline/detail
        # buttons) reached sms and slack only -- the phone call, the one
        # channel that works when Miro is away from a screen, never fired.
        if (msg.buttons and not channel.rich and used
                and msg.severity < Severity.CRITICAL):
            continue
        try:
            ref = await channel.send(msg)
            used.append(name)
            if ref:
                _remember(msg.title, name, str(ref))
            if name == "voice":
                # Spend the budget only when the phone actually rang. A call
                # that failed to place must not count against him. `home`,
                # `title` and `incident_key` are attached so a later
                # `may_call` can tell whether this exact page was placed
                # while he was away (G3).
                from .escalation import record
                record(msg.severity, home=home, title=msg.title,
                      incident_key=msg.incident_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("channel %s failed: %s", name, exc)
            continue
        if msg.severity < Severity.CRITICAL:
            break        # first working channel is enough

    if not used:
        log.error("no channel could deliver %r", msg.title)
        await _audit_undelivered(msg)
    _record(msg, used)
    return used
