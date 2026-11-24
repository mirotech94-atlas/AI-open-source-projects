"""Slack. The best of the channels for approvals: real buttons, threads,
and the owner is already in it during the workday."""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

from ..kernel.comms import CARD_RULE, Button, Outbound, register
from ..kernel.contracts import Severity

COLOR = {
    Severity.INFO: "#6b7280",
    Severity.NORMAL: "#2563eb",
    Severity.HIGH: "#d97706",
    Severity.CRITICAL: "#dc2626",
}


# A topic whose channel has its own env var, for a channel that exists for
# one purpose. #reels (renamed from #instagram-reels on 2026-09-27; the id
# did not change, so neither did the variable) holds every short-video
# card: the AI-stories account's (`instagram_stories`) and TikTok's
# (`tiktok`). the owner: "both the instagram reels and tiktok one both go to a
# reels channel". Before this, TikTok's upload card had no home and fell to
# the default channel. The main Instagram account (`instagram`) is not a
# reels account and stays where it was. The id is in CONFIGURE.md, not here.
REELS_ENV = "SLACK_INSTAGRAM_REELS_CHANNEL"
# The "ask ATLAS anything" channel (`atlas/modules/assistant`): every plain
# message an approver types there is a question for the assistant. Unset,
# the topic has no home and the assistant answers only to `ask <question>`.
ASSISTANT_ENV = "SLACK_ASSISTANT_CHANNEL"
# ATLAS Academy's YouTube tutorials (the owner, 2026-10-10): every rendered
# video is a draft card in #atlas-academy-videos, watched there before it
# is approved for YouTube (`academy.tutorials.review_tutorial`).
ACADEMY_VIDEOS_ENV = "ACADEMY_TUTORIALS_SLACK_CHANNEL"
# The six agency agents (2026-10-27): approve/decline cards land in
# #agency-decisions; only trouble (and the one 08:00 digest) in #agency-alerts.
AGENCY_DECISIONS_ENV = "SLACK_AGENCY_DECISIONS_CHANNEL"
AGENCY_ALERTS_ENV = "SLACK_AGENCY_ALERTS_CHANNEL"
TOPIC_CHANNEL_ENV: dict[str, str] = {
    "agency_decisions": AGENCY_DECISIONS_ENV,
    "agency_alerts": AGENCY_ALERTS_ENV,
    "instagram_stories": REELS_ENV,
    "tiktok": REELS_ENV,
    "assistant": ASSISTANT_ENV,
    "academy_videos": ACADEMY_VIDEOS_ENV,
}
# Where a topic goes when its variable is unset. #atlas-academy-videos is
# ATLAS's own workspace's channel (the bot created it, so it is a member);
# the id, not the name, because conversations.info and file uploads take
# only ids. CONFIGURE.md says the same.
TOPIC_CHANNEL_DEFAULT: dict[str, str] = {
    "academy_videos": "C0SLACKCH03",
    "agency_decisions": "C0SLACKCH00",
    "agency_alerts": "C0SLACKCH01",
}


# The project a message is about, from its topic, for the "[Project] TYPE:"
# header. An unknown topic is title-cased; no topic is ATLAS itself.
PROJECT: dict[str, str] = {
    "cloudco": "CloudCo", "guardian": "Guardian", "flycheap": "FlyCheap",
    "inroots": "K9", "instagram_stories": "Reels", "tiktok": "Reels",
    "content": "Reels", "instagram": "Instagram", "linkedin": "LinkedIn",
    "exampleco": "ExampleCo", "gitlab": "GitLab", "netbird": "NetBird",
    "mail": "Mail", "calendar": "Calendar", "decisions": "Atlas",
    "brief": "Atlas", "comms": "Atlas", "assistant": "Atlas", "fiverr": "Fiverr",
    "stripe": "Stripe", "youtube": "YouTube", "academy_videos": "Academy",
    "agency_decisions": "Agency", "agency_alerts": "Agency",
}
TYPES = ("INFO", "REVIEW", "ACTION", "INCIDENT", "RESOLVED")
SECTION_LIMIT = 2900        # Slack's per-section text limit is 3000
BLOCK_LIMIT = 45            # of 50 per message; leaves room for the rest


def clip(text: str, limit: int = 150) -> str:
    """Slack caps a header at 150 characters. Cut at a word, and say so."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return (cut if len(cut) > limit // 2 else text[:limit - 1]) + "…"


_SALUTATION = re.compile(r"^(hi|hey|hello|morning|good (morning|afternoon|"
                         r"evening))\b[^.!?\n]{0,20}[,.!]?$", re.IGNORECASE)


def _first_line(text: str) -> str:
    """The first line worth a notification: "Hey the owner," says nothing."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in lines:
        if not _SALUTATION.match(line):
            return line
    return lines[0] if lines else ""


def project(topic: str) -> str:
    t = (topic or "").strip().lower()
    return PROJECT.get(t) or (t.replace("_", " ").title() if t else "Atlas")


def presentation(msg: Outbound) -> str:
    """What the reader needs to do. Distinct from what has happened."""
    given = (getattr(msg, "presentation", "") or "").upper()
    if given in TYPES:
        return given
    values = [str(getattr(b, "value", "")) for b in msg.buttons]
    if any(v.startswith(("approve:", "always:")) for v in values):
        return "ACTION"
    if any(v.startswith("ack:") for v in values) or msg.severity >= Severity.HIGH:
        return "INCIDENT"
    return "INFO"


_BROADCAST = re.compile(r"<!(channel|here|everyone)[^>]*>", re.IGNORECASE)
# A user-group ping, `<!subteam^S0123|@oncall>`: it notifies every member.
_GROUP = re.compile(r"<!subteam\^[^>|]*(?:\|([^>]*))?>", re.IGNORECASE)
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$")


def slack_text(text: str) -> str:
    """Markdown as models and humans write it, made into Slack mrkdwn, and
    made unable to ping everyone. `**bold**` renders as literal asterisks in
    Slack (2026-09-27, the #cloudco server-count answer); `<!channel>` in text
    from a buyer, a web page or a model would notify the whole channel."""
    text = _BROADCAST.sub(lambda m: "@" + m.group(1).lower(), text or "")
    text = _GROUP.sub(lambda m: (m.group(1) or "@group").strip() or "@group", text)
    text = _HEADING.sub(lambda m: f"*{m.group(1)}*", text)
    return _BOLD.sub(lambda m: f"*{m.group(1)}*", text)


def _sections(body: str) -> list[dict[str, Any]]:
    """The whole body, in as many sections as it takes. A card's content is
    what is being approved; cutting it at 2,900 characters hid the end of a
    long caption before approval."""
    out: list[dict[str, Any]] = []
    rest = body
    while rest and len(out) < BLOCK_LIMIT:
        chunk = rest[:SECTION_LIMIT]
        if len(rest) > SECTION_LIMIT:
            cut = chunk.rfind("\n")
            if cut > SECTION_LIMIT // 2:
                chunk = chunk[:cut]
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
        rest = rest[len(chunk):].lstrip("\n")
    if rest:
        out.append({"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"_{len(rest)} more characters are in the full view._"}]})
    return out


def _button(b: Button) -> dict[str, Any]:
    """Slack's `style` is an enum of primary|danger. There is no null member:
    sending "style": null fails block validation and Slack rejects the WHOLE
    message, not just the button. An unstyled button omits the key entirely.

    This matters because a proposal carries a `detail:` button alongside
    approve/decline, so every approval request would have been rejected — and
    `notify()` swallows a channel exception, so it would have failed silently.
    """
    element: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": b.label[:75]},
        "value": b.value[:2000],
        "action_id": f"atlas_{b.value}",
    }
    style = ("primary" if b.value.startswith("approve")
             else "danger" if b.value.startswith("decline") else "")
    if style:
        element["style"] = style
    return element


def _upload_refusal(answer: dict) -> str:
    error = str(answer.get("error") or "unknown error")
    if error == "missing_scope":
        return "the Slack app lacks the files:write scope"
    return f"Slack refused the upload: {error}"


class SlackChannel:
    name = "slack"
    rich = True
    interactive = True

    # Read per call rather than in __init__: register() runs at import time,
    # so a value captured there freezes whatever the environment held before
    # .env was loaded, and the channel stays unavailable forever.
    @property
    def token(self) -> str:
        return os.getenv("SLACK_BOT_TOKEN", "").strip()

    @property
    def channel(self) -> str:
        return os.getenv("SLACK_CHANNEL", "").strip()

    def channel_for(self, topic: str) -> str:
        """Where a message about `topic` belongs.

        SLACK_CHANNELS maps a module to a channel:

            SLACK_CHANNELS=cloudco:C01,flycheap:C02,guardian:C03,mail:C04,calendar:C04

        Two modules can share a channel by naming the same id, which is how
        "everything day-to-day in one place" is expressed -- mail and
        calendar both pointing at C04 -- without inventing a second grouping
        concept that would then need its own mapping.

        A topic in `TOPIC_CHANNEL_ENV` may instead be given its own env var
        (`instagram_stories` -> `SLACK_INSTAGRAM_REELS_CHANNEL`), read only
        when SLACK_CHANNELS does not already name it: the id itself lives in
        the environment and CONFIGURE.md, never in code another install
        could hit.

        Anything unmapped falls back to SLACK_CHANNEL. A topic with no home
        going to the default channel is right: a message that reaches the
        wrong channel is still read, and one that reaches none is lost.
        """
        return self.home_for(topic) or self.channel

    def home_for(self, topic: str) -> str | None:
        """The channel NAMED for this topic -- a SLACK_CHANNELS entry or its
        `TOPIC_CHANNEL_ENV` variable -- or None when it only has the default.
        `kernel.comms.notify` delivers a message whose topic has one to Slack
        ahead of any other channel: a channel set up for one
        purpose is where that purpose is followed."""
        if not topic:
            return None
        raw = os.getenv("SLACK_CHANNELS", "")
        if raw:
            for pair in raw.split(","):
                name, _, target = pair.partition(":")
                if name.strip().lower() == topic.strip().lower():
                    found = target.strip()
                    if found:
                        return found
        env = TOPIC_CHANNEL_ENV.get(topic.strip().lower())
        if env and (found := os.getenv(env, "").strip()):
            return found
        return TOPIC_CHANNEL_DEFAULT.get(topic.strip().lower())

    async def post_blocks(self, *, channel: str, text: str,
                          blocks: list[dict[str, Any]]) -> tuple[str | None, str | None]:
        """Post a ready-made Block Kit message (an image block, say, which
        `send` has no shape for). (ts, None), or (None, Slack's own error
        code -- `not_in_channel`, `channel_not_found`, `invalid_blocks` --
        or the transport failure). Never raises."""
        if not self.token:
            return None, "SLACK_BOT_TOKEN unset"
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post("https://slack.com/api/chat.postMessage",
                                 headers={"Authorization": f"Bearer {self.token}"},
                                 json={"channel": channel, "text": text, "blocks": blocks,
                                       "unfurl_links": False, "unfurl_media": False})
                data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            from ..modules._http import explain
            return None, f"Slack post failed: {explain(exc)}"
        if not data.get("ok"):
            return None, str(data.get("error") or "unknown error")
        return str(data.get("ts") or ""), None

    async def available(self) -> bool:
        return bool(self.token and self.channel)

    async def upload(self, *, channel: str, thread: str | None, filename: str,
                     data: bytes, title: str, comment: str) -> str | None:
        """Put a file in a channel (under `thread` when given) through
        Slack's external-upload flow: `files.getUploadURLExternal`, the
        bytes POSTed to the URL it returns, then
        `files.completeUploadExternal` with `channel_id` and `thread_ts`.
        Returns None on success, else a reason -- never raises. It needs
        the `files:write` scope; without it Slack answers `missing_scope`
        and the reason says which scope to add."""
        if not self.token:
            return "SLACK_BOT_TOKEN unset"
        auth = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post("https://slack.com/api/files.getUploadURLExternal",
                                 headers=auth, data={"filename": filename,
                                                     "length": str(len(data))})
                got = r.json()
                if not got.get("ok"):
                    return _upload_refusal(got)
                sent = await c.post(got["upload_url"], content=data,
                                    headers={"Content-Type": "application/octet-stream"})
                if sent.status_code >= 400:
                    return f"Slack's upload URL answered HTTP {sent.status_code}"
                done = {"files": [{"id": got["file_id"], "title": title}],
                        "channel_id": channel, "initial_comment": comment}
                if thread:
                    done["thread_ts"] = thread
                r = await c.post("https://slack.com/api/files.completeUploadExternal",
                                 headers=auth, json=done)
                finished = r.json()
                if not finished.get("ok"):
                    return _upload_refusal(finished)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            from ..modules._http import explain
            return f"Slack upload failed: {explain(exc)}"
        return None

    @staticmethod
    def _title_repeats_body(msg: Outbound) -> bool:
        """The title is just the start of the body -- the typed-reply case,
        whose title is `body[:150]`. A header then showed every reply twice
        (2026-09-27, #cloudco)."""
        title = " ".join(msg.title.split()).rstrip("…. ")
        return bool(title) and " ".join(msg.body.split()).startswith(title)

    @classmethod
    def headline(cls, msg: Outbound) -> str:
        """`[Project] TYPE: what happened` -- the header and the
        notification text. A reply in a thread leads with its own words; a
        message that names its own header (a proposal card's
        "[Area] #357 · …") has that."""
        if (getattr(msg, "header", "") or "").strip():
            return clip(slack_text(msg.header))
        if cls._title_repeats_body(msg):
            return clip(_first_line(slack_text(msg.body)))
        if msg.kind == "receipt" and not getattr(msg, "presentation", ""):
            return clip(slack_text(msg.title))
        # The title through `slack_text` too: this is the notification text,
        # and a `<!channel>` in a title would ping the channel (plan S04).
        return clip(f"[{project(msg.topic)}] {presentation(msg)}: "
                    f"{slack_text(msg.title)}")

    def _blocks(self, msg: Outbound) -> list[dict[str, Any]]:
        body = slack_text(msg.body)
        blocks: list[dict[str, Any]] = []
        if self._title_repeats_body(msg):
            pass                                # the body says it all, once
        elif msg.kind == "receipt" and not getattr(msg, "presentation", ""):
            # A receipt sits in a thread: its title (often the only place a
            # result lives, "#180 — instagram: published") as a bold first
            # line, not a big header.
            body = f"*{slack_text(msg.title)}*\n{body}"
        else:
            blocks.append({"type": "header", "text": {
                "type": "plain_text", "text": self.headline(msg)}})
        above, rule, below = body.partition(f"\n{CARD_RULE}\n")
        if rule:
            # The decision above, the reason and evidence below a divider.
            blocks += _sections(above)
            blocks.append({"type": "divider"})
            blocks += _sections(below)
        else:
            blocks += _sections(body)
        if msg.buttons:
            blocks.append({"type": "actions",
                           "elements": [_button(b) for b in msg.buttons]})
        return blocks

    async def send(self, msg: Outbound) -> str | None:
        payload: dict[str, Any] = {
            "channel": (getattr(msg, "channel", "") or "").strip()
                       or self.channel_for(msg.topic),
            # Fallback text for notifications and screen readers.
            "text": self.headline(msg),
            "blocks": self._blocks(msg),
            # A colour bar needs content to attach to; an attachment carrying
            # only a colour renders as an empty block.
            "attachments": [{"color": COLOR.get(msg.severity, "#2563eb"),
                             "blocks": [{"type": "context", "elements": [
                                 {"type": "mrkdwn",
                                  "text": f"atlas · {msg.severity.name.lower()}"}]}]}],
        }
        if msg.thread:
            payload["thread_ts"] = msg.thread
            # In a channel, a thread reply is folded away under the message
            # and he never sees it (2026-09-25: ATLAS answered his question
            # in #decisions within 16 s and he read "no response"). A reply
            # in a channel is also shown in the channel; a DM thread is
            # already in front of him.
            if not str(payload["channel"]).startswith("D"):
                payload["reply_broadcast"] = True
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
            )
            data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"slack: {data.get('error')}")
        return data.get("ts")


def escape(text: str) -> str:
    """Outside text (a visitor's, a camera's) made literal in Slack mrkdwn: `&`, `<`
    and `>` are the only characters Slack parses, so a pasted `<@U123>`, `<!channel>`
    or `<https://x|click>` shows as typed and notifies nobody."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notice_problem(topic: str) -> str | None:
    """Why a notice for `topic` cannot be posted, or None. Static configuration only,
    so a health check built on it reads UNKNOWN, never OK, when Slack is not set up."""
    ch = SlackChannel()
    if not ch.token:
        return "SLACK_BOT_TOKEN is not set"
    if not ch.channel_for(topic):
        return f"no Slack channel for {topic!r} (SLACK_CHANNELS or SLACK_CHANNEL)"
    return None


async def post_notice(*, topic: str, title: str, body: str,
                      severity: Severity = Severity.NORMAL) -> tuple[str | None, str | None]:
    """Post one notice straight to the topic's Slack channel: (ts, None) when Slack
    accepted it (`ok: true` is Slack's own confirmation), else (None, a reason).
    Never raises and never puts the token in the reason. The caller is an executor
    action; this is the channel's own writer, not a second path."""
    why = notice_problem(topic)
    if why:
        return None, why
    try:
        ts = await SlackChannel().send(Outbound(
            title=title, body=body, severity=severity, topic=topic, kind="alert"))
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return None, explain(exc)[:200]
    return str(ts or "sent"), None


register(SlackChannel())
