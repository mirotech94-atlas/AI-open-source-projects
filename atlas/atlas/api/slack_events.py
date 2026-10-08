"""Typing "do it" in the decisions channel.

A button click and a slash command both prove the same three things — the
request is signed by Slack, it arrived inside the replay window, and the user
id is one of the approvers. A plain message in the channel proves exactly the
same three, once the Events API delivers it. So "do it", typed, is as good an
approval as the button, and he asked for it to work that way.

What this deliberately does NOT do is treat channel chatter as instructions.
Only a phrase that matches the approval or decline patterns is acted on, and
only from an approver. Everything else in the channel is ignored, including
every message from a bot — ATLAS's own cards would otherwise be read back to
it as commands.

Which proposal? A number in the message wins ("do it 82", "approve #82").
Without one, the single most recent pending proposal is taken. If more than
one is pending and none is named, nothing is approved and he is told which
are waiting: guessing which of two cards he meant is how the wrong thing
runs.

Slack wants a 200 within three seconds and retries otherwise. The decision
runs in a background task after an immediate acknowledgement. A retry
(`X-Slack-Retry-Num`) goes through the same durable event-id check as any
delivery: taken once, whichever copy arrives first -- a first delivery lost
to a restart is not lost for good.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from ..kernel import quota
from ..kernel.background import spawn

log = logging.getLogger("atlas.slack_events")

router = APIRouter(prefix="/webhooks/slack")

def _approvers() -> set[str]:
    return {u for u in os.getenv("SLACK_APPROVER_IDS", "").split(",") if u}


_ME: tuple[str, str] | None = None


async def _me() -> tuple[str, str]:
    """ATLAS's own Slack (user id, bot id), so a thread parent can be proven
    to be a message ATLAS posted -- not another bot's, not an approver's."""
    global _ME
    if _ME is None:
        from ..modules._http import post_json
        data, _ = await post_json(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN', '')}"})
        found = (str((data or {}).get("user_id") or ""),
                 str((data or {}).get("bot_id") or ""))
        if any(found):
            _ME = found
        return found
    return _ME


async def _parent(channel: str, thread_ts: str) -> dict | None:
    """The message a threaded reply hangs under, when ATLAS posted it."""
    from ..modules._http import get_json
    data, reason = await get_json(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN', '')}"},
        params={"channel": channel, "latest": thread_ts, "inclusive": "true",
                "limit": 1})
    if reason or not isinstance(data, dict) or not data.get("ok"):
        return None
    msgs = data.get("messages") or []
    if not msgs or str(msgs[0].get("ts")) != thread_ts:
        return None
    msg = msgs[0]
    user_id, bot_id = await _me()
    ours = ((bot_id and msg.get("bot_id") == bot_id)
            or (user_id and msg.get("user") == user_id))
    return msg if ours else None


_SEEN: dict[str, float] = {}
_SEEN_KEPT = 2000


async def _seen_before(event_id: str) -> bool:
    """Has this exact Slack event already been taken? Durable across a
    restart: the id is kept in the audit log before anything is acted on.

    Slack can deliver one event twice without a retry header. A typed
    request runs through intent.act, which -- unlike an approval's atomic
    claim -- would run the action twice. The in-process set covers two
    copies arriving at the same moment; the audit log covers a restart
    between them. If the log cannot be read, the event is taken (and says
    so): refusing every message because the database blinked is worse."""
    import time

    if not event_id:
        return False
    if event_id in _SEEN:
        return True
    _SEEN[event_id] = time.monotonic()
    while len(_SEEN) > _SEEN_KEPT:
        _SEEN.pop(next(iter(_SEEN)))
    from ..kernel.audit import audit
    from ..kernel.state import fetch_one
    try:
        if await fetch_one(
                """SELECT 1 AS x FROM actions_log
                    WHERE event = 'slack.event' AND subject = $1 LIMIT 1""",
                event_id):
            return True
        await audit("slack.event", event_id, {})
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not check slack event %s for a duplicate (%s); "
                    "taking it", event_id, type(exc).__name__)
    return False


def _ack_key(msg: dict) -> str | None:
    """The check an alert's Acknowledge button names, if it has one."""
    for block in msg.get("blocks") or []:
        for el in block.get("elements") or []:
            value = str(el.get("value") or "")
            if value.startswith("ack:"):
                return value[4:]
    return None


async def _pending() -> list:
    from ..kernel.state import fetch_all
    return await fetch_all(
        """SELECT id, action FROM proposals
            WHERE status = 'pending' AND expires_at > now()
            ORDER BY created_at DESC LIMIT 5""")


def _which(rows: list, lead: str) -> str:
    listed = ", ".join(f"#{r['id']} {r['action']}" for r in rows)
    example = rows[0]["id"] if rows else 180
    return (f"{lead} Pending: {listed}. Say `approve {example}`, or answer in "
            f"the card's thread. Nothing was executed.")


async def _resolve(decision, channel: str, thread_ts: str | None
                   ) -> tuple[int | None, str]:
    """Which proposal he means, or the question to ask him instead.

    An id he named wins; more than one is a question, never the first. In a
    thread under an ATLAS card, that card. Otherwise a strong verb may take
    the single pending proposal; a bare "yes"/"no" never does, and nothing
    ever picks the newest of several."""
    from .slack_decisions import card_id

    if len(decision.ids) > 1:
        named = " and ".join(f"#{i}" for i in decision.ids)
        return None, (f"You named {named}; I decide one at a time. Which one? "
                      f"Nothing was executed.")
    if decision.ids:
        return decision.ids[0], ""
    if thread_ts:
        pid = card_id(await _parent(channel, thread_ts) or {})
        if pid is not None:
            return pid, ""
    rows = await _pending()
    if not rows:
        return None, "Nothing's pending. Nothing was executed."
    if decision.strong and decision.bare and len(rows) == 1:
        return int(rows[0]["id"]), ""
    if len(rows) > 1:
        return None, _which(rows, "Which one?")
    return None, _which(rows, "Which proposal? A bare yes or no only answers "
                              "the card it replies to.")


async def _reply(body: str, channel: str, thread: str) -> None:
    from ..kernel.comms import Outbound, notify
    from ..kernel.contracts import Severity
    from ..kernel.flows import ask
    try:
        used = await notify(Outbound(title=body[:150], body=body,
                                     topic="decisions", severity=Severity.NORMAL,
                                     thread=thread, kind="receipt",
                                     channel=channel))
    except Exception:
        log.exception("could not reply in the channel")
        used = []
    ask.note_reply(used)


async def _act(decision, user: str, channel: str, thread: str,
               in_thread: bool) -> None:
    """Carry out a typed decision: approve, decline, hold or acknowledge."""
    from ..kernel.approvals import decide
    from ..kernel.audit import audit

    if decision.kind == "ack":
        parent = await _parent(channel, thread) if in_thread else None
        key = _ack_key(parent or {})
        if key:
            from ..kernel.acks import ACK_DAYS, ack
            health = await ack(key, via=f"slack:{user}")
            body = (f"Acknowledged {key} ({health}). No action executed. I "
                    f"won't raise it again for {ACK_DAYS} days unless it gets "
                    f"worse, or recovers and breaks again." if health else
                    f"Acknowledged. I don't know a check called {key}, so "
                    f"nothing was muted. No action executed.")
        elif parent is not None:
            await audit("slack.acknowledged", channel, {"by": user, "ts": thread})
            body = "Acknowledged. No action executed."
        else:
            # A "thanks" in conversation: noted, never a decision.
            await audit("slack.acknowledged", channel, {"by": user, "ts": thread})
            body = "Noted."
        await _reply(body, channel, thread)
        return

    pid, why_not = await _resolve(decision, channel, thread if in_thread else None)
    if decision.kind == "hold":
        body = (f"Not approving #{pid}. It stays pending; nothing was executed."
                if pid is not None else "Nothing was approved or executed.")
        await _reply(body, channel, thread)
        return

    if pid is None:
        await _reply(why_not, channel, thread)
        return
    try:
        verdict = await decide(pid, decision.kind, via=f"slack:{user}")
        body = verdict.receipt()
        await _sync_card(pid, body, channel, user)
    except Exception as exc:
        log.exception("typed %s on #%s failed", decision.kind, pid)
        body = f"#{pid}: couldn't do that — {type(exc).__name__}."
    await _reply(body, channel, thread)


async def _sync_card(pid: int, body: str, decided_in: str, user: str) -> None:
    """A decision typed somewhere other than the card's own channel also
    lands under the card, so the card never looks undecided. The card's
    channel is its topic's, exactly as `approvals._receipts` finds it."""
    import json as _json

    from ..channels.slack import SlackChannel
    from ..kernel.approvals import _topic
    from ..kernel.comms import Outbound, notify, sent_ref
    from ..kernel.contracts import Severity
    from ..kernel.registry import REGISTRY
    from ..kernel.state import fetch_one

    card_ts = sent_ref(f"#{pid} — ", "slack")
    if not card_ts:
        return
    try:
        row = await fetch_one("SELECT action, params FROM proposals WHERE id=$1", pid)
        if row is None:
            return
        params = row["params"]
        if isinstance(params, str):
            params = _json.loads(params or "{}")
        spec = REGISTRY.actions.get(row["action"])
        topic = _topic(spec, row["action"], params or {}) if spec else ""
        if SlackChannel().channel_for(topic) == decided_in:
            return                  # decided in the card's own channel already
        await notify(Outbound(
            title=body[:150], body=f"{body}\n_Decided in <#{decided_in}> by <@{user}>._",
            severity=Severity.NORMAL, topic=topic, thread=card_ts, kind="receipt"))
    except Exception:
        log.exception("could not note the decision under card #%s", pid)


# What a named verb can refer to, by the ACTION a pending card carries --
# never by its text, which mentions every product under the sun (a GitLab
# card's body names guardian, linkedin, youtube...).
_NAMED: dict[str, tuple[str, ...]] = {
    "linkedin": ("linkedin.",),
    "draft": ("linkedin.approve_draft", "content.draft"),
    "post": ("linkedin.publish_post", "content.publish_reel", "instagram.publish"),
    "reel": ("content.publish_reel", "instagram.publish", "tiktok."),
    "video": ("content.publish_reel", "tiktok.", "youtube."),
    "tiktok": ("tiktok.",),
    "instagram": ("instagram.",),
    "youtube": ("youtube.",),
    "issue": ("gitlab.",), "gitlab": ("gitlab.",), "ticket": ("gitlab.",),
    "snapshot": ("vpsie.",), "vpsie": ("vpsie.",), "server": ("vpsie.",),
    "invoice": ("stripe.",), "payment": ("stripe.",),
    "reply": ("mail.", "matrix_rooms.", "fiverr.", "instagram.reply"),
    "email": ("mail.",), "calendar": ("calendar.",),
}


def _fits(action: str, prefixes: tuple[str, ...]) -> bool:
    return any(action == p or action.startswith(p) for p in prefixes)


def _named_prefixes(words: str) -> list[tuple[str, tuple[str, ...]]]:
    found = []
    for raw in re.findall(r"[a-z]+", words.lower()):
        word = raw[:-1] if raw.endswith("s") and raw[:-1] in _NAMED else raw
        if word in _NAMED:
            found.append((word, _NAMED[word]))
    return found


async def _named(decision, words: str, channel: str, thread: str) -> bool:
    """"approve the linkedin drafts": answered from what is actually
    pending, never from the model. Returns False when the words name
    nothing ATLAS knows, so the request path takes it."""
    from datetime import UTC, datetime

    from ..kernel.state import fetch_all

    named = _named_prefixes(words)
    if not named:
        return False
    rows = await fetch_all(
        """SELECT id, action, params FROM proposals
            WHERE status = 'pending' AND expires_at > now()
            ORDER BY created_at DESC LIMIT 25""")
    matches = [r for r in rows
               if all(_fits(str(r["action"]), prefixes) for _, prefixes in named)]
    verb = "approve" if decision.kind == "approve" else "decline"
    what = " ".join(w for w, _ in named)
    if matches:
        listed = "\n".join(f"• #{r['id']} `{r['action']}`" for r in matches[:8])
        body = (f"Waiting for your decision ({what}):\n{listed}\n"
                f"Say `{verb} {matches[0]['id']}` for the one you mean, or reply "
                f"in its card's thread. Nothing was executed.")
    else:
        body = f"Nothing matching \"{what}\" is waiting for a decision."
        if any(w == "linkedin" for w, _ in named):
            body += " " + _linkedin_bank_line()
        others = sorted({str(r["action"]).split(".", 1)[0] for r in rows})
        if others:
            body += (f" What is waiting: {len(rows)} card(s) ("
                     f"{', '.join(others)}).")
        body += (f" Nothing was executed. (read {datetime.now(UTC):%H:%M} UTC)")
    await _reply(body, channel, thread)
    return True


def _linkedin_bank_line() -> str:
    """The post bank as it stands, from ATLAS's own LinkedIn state."""
    try:
        from ..modules.linkedin import PAGES
        from ..modules.linkedin.store import bank, load
        state = load()
    except Exception as exc:                            # noqa: BLE001
        return f"The LinkedIn post bank could not be read ({type(exc).__name__})."
    parts = []
    for page, spec in PAGES.items():
        entries, _, _ = bank(state, page)
        approved = [str(e.get("id")) for e in entries if e.get("status") == "approved"]
        drafts = [str(e.get("id")) for e in entries if e.get("status") == "draft"]
        bit = f"{spec['name']}: {len(approved)} approved and queued"
        if approved:
            bit += f" ({', '.join(approved)})"
        if drafts:
            bit += f", {len(drafts)} draft(s) not yet carded ({', '.join(drafts)})"
        parts.append(bit)
    return "LinkedIn post bank — " + "; ".join(parts) + "."


async def _explain(decision, channel: str, thread: str, in_thread: bool) -> None:
    """"What happens if I approve 180?" -- the card's detail, never a run."""
    from .main import _proposal_detail
    from .slack_decisions import card_id

    pid = decision.ids[0] if len(decision.ids) == 1 else None
    if pid is None and in_thread:
        pid = card_id(await _parent(channel, thread) or {})
    if pid is None:
        await _reply("Which proposal? Name it (`what does 180 do?`) or ask in "
                     "the card's thread. Nothing was executed.", channel, thread)
        return
    await _reply(await _proposal_detail(pid) + "\n\n_Nothing was executed._",
                 channel, thread)


_ABOUT_A_DECISION = re.compile(
    r"\b(approv\w*|declin\w*|reject\w*|execut\w*|run|post|publish|happen\w*|"
    r"do(es)?)\b", re.IGNORECASE)


@router.post("/events")
async def events(request: Request,
                 x_slack_request_timestamp: str = Header(""),
                 x_slack_signature: str = Header(""),
                 x_slack_retry_num: str = Header("")) -> dict[str, Any]:
    from .main import _verify_slack

    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON") from None

    # Slack proves it owns the URL before it will send anything. The
    # challenge comes unsigned-verifiable like everything else, so verify
    # first: an unverified echo would let anyone confirm the endpoint.
    if not _verify_slack(raw, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(401, "bad signature")
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # A retry is NOT dropped on sight (RA-B5): Slack retries when the first
    # delivery failed -- a restart, a 502 -- and dropping every retry lost
    # exactly those events. `_seen_before` records the event id durably
    # before anything acts, so a retry of an event already taken is still
    # dropped there, and one whose first delivery never arrived is taken.
    if x_slack_retry_num:
        log.info("slack retry %s for %s", x_slack_retry_num,
                 payload.get("event_id") or "an event without an id")

    event = payload.get("event") or {}
    if event.get("type") != "message" or event.get("subtype"):
        return {"ok": True}
    if event.get("bot_id") or event.get("user") in (None, ""):
        return {"ok": True}

    user = str(event.get("user"))
    allowed = _approvers()
    if not allowed or user not in allowed:
        # Silently. A non-approver typing "do it" gets no reply: replying
        # would confirm both that the phrase works and who is allowed.
        log.info("ignoring a typed command from a non-approver")
        return {"ok": True}
    # Only now, for an approver's own message that could act: ATLAS's own
    # posts, joins and everyone else's chatter never pay for the lookup.
    if await _seen_before(str(payload.get("event_id") or "")):
        return {"ok": True, "ignored": "duplicate"}

    text = str(event.get("text") or "").strip()
    channel = str(event.get("channel") or "")
    ts = str(event.get("ts") or "")
    # Answer where he asked: in his thread when he wrote in one (the thread's
    # root, not his reply's own ts), else a thread rooted at his message.
    parent_ts = str(event.get("thread_ts") or "")
    in_thread = bool(parent_ts) and parent_ts != ts
    thread = parent_ts or ts

    async def go(work, name: str, route: str) -> dict[str, Any]:
        """Hand the message to its handler. With the ask slice on
        (kernel/flows/ask.py) it is recorded as a workflow first, and only
        the worker holding that workflow runs the handler."""
        from ..kernel.flows import ask
        status, taken = await ask.intake(
            str(payload.get("event_id") or ""), text=text, user=user,
            channel=channel, thread=thread)
        if status == ask.DUPLICATE:
            work.close()
            return {"ok": True, "ignored": "duplicate"}
        spawn(ask.handled(taken, work, route) if taken is not None else work, name)
        return {"ok": True}

    from .slack_decisions import classify, own_words, without_quotes
    decision = classify(text)
    if (decision is not None and decision.kind in ("approve", "decline")
            and decision.strong and not decision.bare and not decision.ids
            and not in_thread):
        # "approve the linkedin drafts" names what, not which card. If it
        # names a kind of card ATLAS knows, answer from what is pending; if
        # not, it is a request like any other.
        if _named_prefixes(own_words(text)):
            return await go(_named(decision, own_words(text), channel, thread),
                            "slack named", "named")
        decision = None
    if decision is not None and decision.kind in ("approve", "decline", "hold", "ack"):
        return await go(_act(decision, user, channel, thread, in_thread),
                        "slack decision", decision.kind)
    if (decision is not None and decision.kind == "question"
            and (decision.ids or in_thread)
            and _ABOUT_A_DECISION.search(own_words(text))):
        return await go(_explain(decision, channel, thread, in_thread),
                        "slack question", "explain")

    # Not a verdict: a request. "On Slack too, if I don't want to call him,
    # I should be able to say do something and he does it." Only when it is
    # plainly said TO him -- a DM, one of his own channels, or his name --
    # so a channel's ordinary chatter is not read as instructions.
    if _addressed(text, channel, str(event.get("channel_type") or "")):
        # Only his own words are a request. A quoted "> approve #179" was
        # read by the model as "I see you want to approve #179" (live test,
        # 2026-09-28).
        mine = without_quotes(text)
        if _FILE_IT.match(mine):
            if in_thread:
                return await go(_file_it(user, channel, thread), "slack file it",
                                "file_it")
            # Out of any thread, "it" is nothing: never a request of its
            # own, never a card.
            return await go(_reply("Say \"file it\" in the thread of the "
                                   "request you want filed. Out here I can't "
                                   "tell which one you mean. Nothing was "
                                   "executed.", channel, ts),
                            "slack reply", "file_it")
        if not mine:
            return await go(_reply("That was all quoted text, so I read it as "
                                   "someone else's words. Nothing was executed.",
                                   channel, thread), "slack reply", "quoted")
        return await go(_request(mine, user, channel, thread), "slack request",
                        "request")
    return {"ok": True}


def _addressed(text: str, channel: str, channel_type: str) -> bool:
    """Is this message for ATLAS?

    Miro, 2026-09-27: "all Slack channels, I should be able to talk to them
    and interact normally." The workspace is ATLAS's own and only an
    approver's words ever reach here (`events` drops everyone else), so any
    channel ATLAS is in is a conversation with ATLAS -- unless the message
    tags another person and not ATLAS, in which case it is for them."""
    if channel_type == "im":
        return True
    if re.search(r"\batlas\b", text, re.IGNORECASE):
        return True
    if re.search(r"<@U[A-Z0-9]+>", text):
        return False
    return bool(channel)


_GREETING = re.compile(
    r"^\s*(hi|hey|hello|yo|morning|good (morning|afternoon|evening|night)|"
    r"thanks|thank you|cheers|how('?s| is| are)|what'?s up)\b", re.IGNORECASE)


def _is_conversation(text: str) -> bool:
    """Talk rather than a command: a greeting, or a question that asks for
    nothing to be done. Anything else goes to `intent.act` first, and a
    request nothing can do is talked through there instead of refused."""
    from .voice import _QUESTION_START, is_order
    words = _unaddressed(text)
    if is_order(words):
        return False
    if _is_lookup(words):
        # "look up the capital of Peru": a question, for knowledge.
        return False
    return bool(_GREETING.search(words) or words.rstrip().endswith("?")
                or _QUESTION_START.search(words))


CONVERSE = (
    "You are ATLAS, answering Miro in Slack. Talk like a colleague who "
    "knows his systems: react to what he said, answer it plainly, ask one "
    "short follow-up only if it helps. Short paragraphs, Slack markdown is "
    "fine. Numbers about his own businesses and systems come only from the "
    "live status above, with the time each was read; if one is not there, "
    "say that source is unreachable. Never claim to have done anything. "
    "Start with the answer: no greeting, no sign-off, and never end with an "
    "offer such as 'anything else?' or 'let me know'."
)


async def _converse(text: str) -> str:
    """A conversational answer grounded in the live checks; the agent roster
    when the fast call fails."""
    import asyncio

    from ..kernel.agents import route
    from ..kernel.llm import Message, Need, think
    from ..kernel.permissions import wrap_untrusted
    from .voice import live_context

    # His own records that match his words (kernel/search.py), beside the
    # live checks: "what happened with the irubyana clips" is answered from
    # what ATLAS did, not only from what the board says now.
    try:
        from ..kernel import search as records
        found = await asyncio.wait_for(records.context(text), timeout=8)
    except Exception as exc:                            # noqa: BLE001
        log.warning("records search failed (%s); answering without it",
                    type(exc).__name__)
        found = ""
    try:
        from ..kernel import tasks
        completion = await asyncio.wait_for(think(
            Need.DRAFT, await live_context() + "\n\n" + await tasks.context_text()
            + "\n\n" + found + "\n\n" + CONVERSE,
            [Message("user", wrap_untrusted(text, "slack", principal=True))],
            max_tokens=900), timeout=40)
        answer = (getattr(completion, "text", "") or "").strip()
        if answer and not _looks_cut(answer):
            return answer
        if answer:
            log.warning("slack answer looked cut off; asking the roster instead")
    except Exception as exc:                            # noqa: BLE001
        log.warning("slack conversation fast path failed (%s); routing",
                    type(exc).__name__)
    routed = await route(CONVERSE + "\n\n" + wrap_untrusted(text, "slack",
                                                             principal=True))
    if _looks_cut(routed):
        # A provider that ran out of budget mid-answer (2026-09-28: a
        # rate-limited fallback stopped at "queued (`B5`, `B6`, `B8`, ").
        # Say so rather than post it as if it were whole.
        routed = routed.rstrip() + "\n_(That answer was cut off. Ask again for the rest.)_"
    return routed


def _looks_cut(text: str) -> bool:
    """An answer that stopped mid-sentence: an open code span, or a last
    line ending on a comma, an open bracket or a dash."""
    text = (text or "").rstrip()
    if not text:
        return False
    if text.count("`") % 2 == 1 or text.count("(") > text.count(")"):
        return True
    return text[-1] in ",(-–—:"


_SEARCH = re.compile(
    r"^\s*(atlas\s*[,:]?\s*)?(please\s+)?(search my records for|search for|search"
    r"|what do you have on|what do you know about|history of)\s*[:\-]?\s*(?P<q>.+?)"
    r"[\s?.!]*$", re.IGNORECASE)


def search_terms(text: str) -> str | None:
    """The words to search when the message is a search of his own records,
    else None. "search irubyana drafts", "what do you have on vpsie".
    "search for flights to Rome" is still a search of his records -- it
    finds nothing and says so, never books anything."""
    found = _SEARCH.match(text or "")
    if not found:
        return None
    # "search up 50 local businesses ... and make them one" is an order the
    # agency owns, not a search of his records: an order is never a search.
    from .voice import is_order
    if is_order(_unaddressed(text)):
        return None
    q = found.group("q").strip()
    return q or None


def _unaddressed(text: str) -> str:
    """The message without the "atlas," that addressed it."""
    return re.sub(r"\batlas\b[,:]?\s*", "", text, flags=re.IGNORECASE).strip()


def _is_knowledge_question(text: str) -> bool:
    """The same rule the voice route applies before it answers from
    knowledge: no verb that asks for something to be done, a knowledge cue,
    and no name of his own modules, businesses, agents or checks (those go
    to a specialist, never to general knowledge)."""
    from .voice import is_knowledge, is_order
    stripped = _unaddressed(text)
    return not is_order(stripped) and (is_knowledge(stripped) or _is_lookup(stripped))


def _is_lookup(text: str) -> bool:
    """ "Look up / find / search / get me" something no agent owns: an order
    only in grammar. It is answered from knowledge, never turned into a card
    or a handoff for the Mac."""
    from .voice import ACTION_INTENT, _is_estate_framed, _mentions_a_name, is_order
    # "Get me the flycheap numbers" is about his own estate: that goes to the
    # specialists who measure (invariant 6), never to a web answer.
    return (bool(ACTION_INTENT.search(text)) and not is_order(text)
            and not _mentions_a_name(text) and not _is_estate_framed(text))


# "atlas , file it" (2026-09-28, with the space) missed the old
# `atlas[,:]?` and went down the request path as a request to do ", file
# it" -- which became a card with nothing in it.
_FILE_IT = re.compile(
    r"^\s*(atlas\s*[,:]?\s*)?(please\s+)?(file it|file that|file this|open an issue"
    r"|file an issue|open a ticket)(\s+please)?[\s.!]*$", re.IGNORECASE)


async def _thread_root(channel: str, thread_ts: str) -> dict | None:
    """The message that started a thread."""
    from ..modules._http import get_json
    data, reason = await get_json(
        "https://slack.com/api/conversations.replies",
        headers={"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN', '')}"},
        params={"channel": channel, "ts": thread_ts, "limit": 1})
    if reason or not isinstance(data, dict) or not data.get("ok"):
        return None
    msgs = data.get("messages") or []
    return msgs[0] if msgs and str(msgs[0].get("ts")) == thread_ts else None


async def _file_it(user: str, channel: str, thread: str) -> None:
    """ "file it" in a thread: open an issue about the request that started
    the thread -- the offer ATLAS makes when nothing it has can do a thing.
    Until 2026-09-28 the offer was not wired to anything: "file it" reached
    the model with no idea what "it" was, and it asked for a document."""
    from ..kernel import intent
    from .slack_decisions import without_quotes

    root = await _thread_root(channel, thread)
    asked = without_quotes(str((root or {}).get("text") or ""))
    if not root or root.get("user") != user or not asked:
        await _reply("I can't tell what to file from this thread, because it "
                     "didn't start with a request of yours. Say `file an issue "
                     "about <what>`. Nothing was executed.", channel, thread)
        return
    try:
        outcome = await intent.act(
            "File a GitLab issue recording this request, which I could not do "
            f"directly: {asked}", channel="slack", who=user)
        body = outcome.spoken
    except Exception as exc:
        log.exception("file it failed")
        body = f"Couldn't file it — {type(exc).__name__}. Nothing was executed."
    await _reply(body, channel, thread)


async def _mission(text: str, user: str, ts: str) -> str | None:
    """What the missions path says to this, or None when it is not one: his
    answer to "Which area should I search?", a recipe job, or a job said to
    an agent by name. Slack authenticates him, so a mission may start."""
    from ..kernel import missions
    from .voice import is_order
    words = _unaddressed(text)
    ordered = is_order(words)
    # An order is never taken for his answer to "Which area should I search?".
    got = None if ordered else await missions.answer(words, channel="slack")
    if got is None and ordered:
        got = await missions.take(words, channel="slack", who_=user, thread=ts)
    if got is None and missions.addressed(words) is not None:
        got = await missions.delegate(words, channel="slack", who_=user, thread=ts)
    # Slack is read, not heard: the full account, pacing and switches included.
    return got.full if got is not None else None


def _assistant_channel(channel: str) -> bool:
    """Is this the "ask ATLAS anything" channel (`SLACK_ASSISTANT_CHANNEL`,
    topic `assistant`)? Every plain message there is a question for the
    assistant module. Unset -- or the assistant off (`ASSISTANT_ENABLED`) --
    no channel is."""
    if not channel:
        return False
    from ..modules.assistant import enabled
    if not enabled():
        return False
    from ..channels.slack import SlackChannel
    return SlackChannel().home_for("assistant") == channel


async def _assistant(request, user: str) -> str:
    from ..kernel.flows import ask
    from ..modules.assistant import handle

    return await ask.answer(lambda: handle(request, channel="slack", who=user),
                            "assistant")


@quota.for_miro
async def _request(text: str, user: str, channel: str, ts: str) -> None:
    """A typed request from an approver. Slack is a channel that can
    approve, so the ask is the approval: Tier 1 and 2 run, Tier 3 is
    refused, and either way he gets one line back in the thread.

    He is waiting on it, so it may spend the free allowance kept for him
    (kernel/quota.py). It runs as a spawned task, which starts background;
    the decorator marks it inside."""
    from ..kernel import intent, tasks
    from .voice import is_order

    # "search <words>": his own records, listed, no model in the loop
    # (kernel/search.py). Miro, 2026-10-06: a search engine of his own.
    if (sought := search_terms(text)) is not None:
        from ..kernel import search as records
        try:
            body = records.render(await records.search(sought), sought)
        except Exception as exc:                        # noqa: BLE001
            body = (f"The records could not be read just now "
                    f"({type(exc).__name__}). Nothing was executed.")
        await _reply(body, channel, ts)
        return
    # Every request is on the record (kernel/tasks.py) so he can check on
    # it later; the route and the outcome are written when it is answered.
    task_id = await tasks.open(_unaddressed(text), source="slack", who=user, thread=ts)
    route, ok, cards = "answer", None, ()
    try:
        from ..modules import assistant
        words = _unaddressed(text)
        if _assistant_channel(channel):
            # The assistant's own channel: a question, answered with its
            # sources, never a card and never a run (modules/assistant).
            # `stop` stops his runs; the explicit forms keep their meaning.
            if assistant.STOP.match(words):
                body = await assistant.runs.cancel_mine(user, channel="slack")
            else:
                request = (assistant.parse_explicit(words)
                           or assistant.Request("ask", words))
                body = await _assistant(request, user)
            await _post_reply(body, channel, ts)
            return
        if assistant.enabled() and (request := assistant.parse_explicit(words)):
            # An explicit `ask <question>`, `search <question>` or `ask
            # cancel <id>` anywhere: for the assistant before any other
            # reading of the words. Everything below would take a
            # question-shaped message as chat or knowledge first and the
            # `ask` would never reach intent.act's hook.
            body = await _assistant(request, user)
            await _post_reply(body, channel, ts)
            return
        from ..kernel import improve
        handled = await improve.handle(_unaddressed(text), source="slack")
        mission = None
        if handled is None:
            mission = await _mission(text, user, ts)
        if handled is not None:
            # "Improve X": a development handoff (kernel/improve.py) -- a
            # note for ATLAS itself, a nightshift.run card for FlyCheap/
            # Vault -- never a catalog miss, and never "I can't access it".
            body = handled.spoken
            route = "improve"
            cards = (handled.proposal_id,) if handled.proposal_id is not None else ()
        elif mission is not None:
            # A job for the agent that owns it (kernel/missions.py): the
            # agency's "find 50 businesses with a poor website and make them
            # one", or anything said to an agent by name ("have Ivan ...").
            body = mission
            route, ok = "mission", True
        elif tasks.is_status_question(words) and not is_order(words):
            # "What did you do with my request", "what are you working on":
            # the record itself, no model (kernel/tasks.py).
            body = await tasks.status_text(words)
        elif _is_conversation(text) and not _is_knowledge_question(text):
            # Talk, not a command: "how's it going", "why is guardian slow",
            # "what did you do overnight". Answered like a colleague, over
            # the live checks (the same grounding the voice uses), in the
            # channel it was asked in.
            from ..kernel.flows import ask
            body = await ask.answer(lambda: _converse(_unaddressed(text)),
                                    "live status")
        elif _is_knowledge_question(text):
            # A general-knowledge question -- medicine, technology, history --
            # is answered by the knowledge module (Perplexity first, with its
            # sources and the time they were read, then the model), exactly
            # as it is out loud. It never reaches intent.act: there is no
            # action to run for "what is the difference between a virus and
            # a bacterium", and NO_ACTION was the wrong answer to it.
            from ..kernel import knowledge
            from ..kernel.flows import ask
            body = await ask.answer(lambda: knowledge.answer(_unaddressed(text)),
                                    "knowledge")
        else:
            outcome = await intent.act(text, channel="slack", who=user)
            body = outcome.spoken
            route = "action"
            ok = (outcome.ok if outcome.ran else None) if outcome.action else None
            cards = (outcome.proposal_id,) if outcome.proposal_id is not None else ()
            if not outcome.action and body == intent.NO_ACTION:
                # Nothing in the catalog does it. First the agent that owns
                # the domain (kernel/missions.py); then, when no agent does,
                # not a dead end and not a request for his own `top` output
                # (2026-09-28, the Roots CPU): the specialists measure, and
                # the work is written up for the coding agent as a Tier 2
                # card (kernel/solve.py).
                from ..kernel import missions
                handed = await missions.delegate(_unaddressed(text), channel="slack",
                                                 who_=user, thread=ts)
                if handed is not None:
                    body = handed.full
                    route, ok = "mission", True
                else:
                    from ..kernel.solve import solve
                    body = (await solve(_unaddressed(text), who=user,
                                        channel=channel, thread=ts)
                            + "\n\n_Or say \"atlas, file it\" and I'll open an "
                              "issue instead._")
                    route, ok = "solve", None
    except Exception as exc:
        log.exception("typed request failed")
        body = f"Couldn't do that — {type(exc).__name__}."
        ok = False
    await tasks.close(task_id, route=route, ok=ok, outcome=body, cards=cards)
    await _post_reply(body, channel, ts)


async def _post_reply(body: str, channel: str, ts: str) -> None:
    """One line back in the thread he asked in."""
    from ..kernel.comms import Outbound, notify
    from ..kernel.contracts import Severity
    from ..kernel.flows import ask
    try:
        used = await notify(Outbound(title=body[:150], body=body,
                                     topic="decisions", severity=Severity.NORMAL,
                                     thread=ts, kind="receipt", channel=channel))
    except Exception:
        log.exception("could not reply in the channel")
        used = []
    ask.note_reply(used)
