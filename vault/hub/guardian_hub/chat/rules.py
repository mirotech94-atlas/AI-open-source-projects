"""`rules.answer` -- the deterministic responder and the last rung of the
provider ladder (`providers.chat`), so Vault chat still answers with no
API key and no credits. It obeys the same strict answer format (verdict
line, up to 6 bullets, `Next:` line) every other provider does, since
`chat/prompt.parse_answer` validates it identically regardless of which
provider produced the text.

It also has to hold a conversation. On an install with no provider key
configured -- which is the common case -- this IS Vault's voice, and
the alerts it most often has to discuss are precisely the ones it cannot
fix: a heartbeat that never checked in, a certificate only the CA can
reissue, an ISP's line. The exchange that prompted this rewrite:

    Vault: ... I cannot fix this one myself: a job that should check
              in with me has not, and it runs outside my reach.
    Owner:    what can i do to help you solve it
    Vault: I can answer about: security score, status/down, alerts, ...

A menu is the right reply to "what can you do?" and to nothing else.
Answering a specific question with a list of topics is a non-answer, and
it is worse here than usual because the owner was offering to help.

So the shape is: resolve what the conversation is ABOUT from the thread's
own turns (`ChatThread.turns`, passed in as `history`), notice that the
new message refers back to it, and answer about that thing -- one sentence
saying what Vault cannot do, the concrete human step, and an offer of
the next thing. The menu is reached only by an opening capability
question, and only with nothing else under discussion.

None of this is reasoning. Every branch is a keyword match over a message
and a lookup of a row Vault already has, so the copy never claims to
have thought about anything -- and a conversational reply says out loud
that a language-model key would make this much better, because it would.
"""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks, remedy as remedy_mod
from ..scans import latest_scan, parse_ts


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp(scan) -> str:
    when = parse_ts(scan.ts) if scan else None
    age_min = int((_now() - when).total_seconds() // 60) if when else None
    return f"(data from {scan.ts[:16].replace('T', ' ') if scan else 'never'}, {age_min if age_min is not None else '?'} min ago)"


#: What this fallback is, in one clause -- appended to a conversational
#: reply's `Next:` line so the honesty is attached to the reply that needs
#: it, rather than repeated on every factual lookup.
NO_MODEL_NOTE = ("I am answering from a fixed rule set rather than a language model, so I can "
                 "follow this only so far -- an AI provider key in Settings would let me talk "
                 "it through properly")

#: An opening "what can you do?" -- and nothing else. These are matched
#: against the WHOLE message (after stripping trailing punctuation), not as
#: substrings, because "what can I do to help you solve it" contains "what
#: can i do" and is the opposite of a capability question.
_CAPABILITY_QUESTIONS = {
    "what can you do", "what can you do for me", "what do you do",
    "what can i ask", "what can i ask you", "what can you help with",
    "what can you help me with", "help", "what are your capabilities",
    "capabilities", "what can you tell me", "what can you answer",
    "options", "menu",
}

#: Openers that only make sense as a reply to something already said.
_FOLLOWUP_OPENERS = (
    "what can i do", "what should i do", "what do i do", "how do i", "how can i",
    "what do you need", "what else do you need", "what do you want",
    "why not", "why", "and then", "then what", "so what", "what now",
    "is that bad", "is it bad", "how bad", "how serious", "does that matter",
    "what happens", "what if", "who", "when", "where", "which one",
    "can you", "could you", "will you", "should i", "anything else",
    "what else", "tell me more", "more", "go on", "ok", "okay", "yes", "no",
)

#: Words that, in a short message, are pointing back at what was just said
#: rather than naming a new subject.
_REFERENTIAL = ("it", "that", "this", "them", "those", "these", "one")

#: Every keyword the factual branches below own, as one set. A message that
#: names one of these is asking a NEW question, even mid-conversation and
#: even with a pronoun in it -- "how is that cert doing" is about
#: certificates, not about whatever was last discussed. Without this gate the
#: referential heuristic swallows the branch that has the right answer and
#: replaces it with the wrong one.
_DOMAIN_WORDS = frozenset((
    "score", "grade", "down", "status", "overview", "summary", "broken",
    "attack", "brute", "fail2ban", "hacked", "intrusion",
    "device", "devices", "wifi", "wi-fi", "lan", "network", "networks",
    "slow", "latency", "speed", "lag", "buffering",
    # "fix" is deliberately NOT here: "how do I fix it" is the commonest
    # follow-up there is, and the fix branch answers it with pending host
    # reboots. The other words of that branch name a specific thing.
    "reboot", "restart", "upgrade", "patch", "remediate",
    "disk", "memory", "ram", "cert", "certs", "certificate", "certificates",
    "ssl", "tls", "alert", "alerts", "warn", "problem", "problems",
))


def _mentions_domain(ql: str) -> bool:
    return bool(_DOMAIN_WORDS & set(_words(ql)))

#: Which question is being asked ABOUT the subject. First match wins, so
#: the more specific phrasings come first.
_INTENTS = (
    ("help", ("what can i do", "how can i help", "what can i do to help", "help you",
              "how do i help", "what do you need", "what do you want", "what should i do",
              "how do i fix", "how can i fix", "how do i solve", "what do i do")),
    ("why", ("why not", "why can", "why cant", "why can't", "why is", "why does", "why")),
    ("severity", ("is that bad", "is it bad", "how bad", "how serious", "does that matter",
                  "does it matter", "what happens", "should i worry", "is that serious")),
    ("more", ("tell me more", "what else", "more detail", "go on", "and then", "then what",
              "what now", "anything else")),
)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9']+", (text or "").lower()) if w]


def _is_capability_question(ql: str) -> bool:
    return ql.strip().strip("?.! ") in _CAPABILITY_QUESTIONS


def _is_followup(ql: str) -> bool:
    """Does this message refer back to what was just said?

    Three signals, any of which is enough: it opens with a phrase that only
    makes sense as a reply, it is short and leans on a pronoun with no
    subject of its own, or it is a bare acknowledgement. Deliberately
    generous -- with a subject under discussion, answering about that
    subject is a better failure than the menu either way.
    """
    q = ql.strip().strip("?.! ")
    if not q:
        return False
    if any(q.startswith(o) for o in _FOLLOWUP_OPENERS):
        return True
    words = _words(q)
    return len(words) <= 10 and any(w in _REFERENTIAL for w in words)


def _intent(ql: str) -> str:
    for name, phrases in _INTENTS:
        if any(p in ql for p in phrases):
            return name
    return "more"


def _subject(db: DbSession, tenant, history) -> "m.Alert | None":
    """The alert this conversation is about, from the thread's own turns.

    The hand-off (`routers/chat.py:alert_handoff`) writes the user side of
    its opening turn as "What can you do about the alert <key>?", so the
    key is in the transcript verbatim -- that is the strongest signal and
    is checked first. Failing that, any open alert whose key or whose
    message text appears in a recent turn counts. Newest turn wins: a
    conversation that moved on to a second alert is about the second one.
    """
    if not history:
        return None
    texts = [str(t.get("text") or "") for t in history if isinstance(t, dict)][-8:]
    if not texts:
        return None
    alerts = db.query(m.Alert).filter_by(tenant_id=tenant.id).filter(
        m.Alert.status.in_(("open", "acked", "snoozed"))).all()
    if not alerts:
        return None
    for text in reversed(texts):
        low = text.lower()
        for a in alerts:
            key = (a.key or "").lower()
            if key and key in low:
                return a
            msg = (a.msg or "").strip()
            # The hand-off opening leads with the alert message verbatim, so
            # a healthy prefix of it is a reliable match without being so
            # short that two alerts collide.
            if len(msg) >= 25 and msg[:60].lower() in low:
                return a
    return None


def _about(db: DbSession, tenant, alert, ql: str, role: str, stamp: str) -> str:
    """Answer the follow-up about `alert`, in the product's own format.

    Vault's honest position on a human-remedy alert is: it cannot act,
    here is the one thing a person does, and here is what it will do once
    that is done. That is three sentences, not a topic list -- and it is
    all read off `remedy.resolve`, the same resolver the inbox row and the
    hand-off opening use, so chat cannot contradict what the alert card
    already says.
    """
    remedy = remedy_mod.resolve(db, tenant, alert, role)
    intent = _intent(ql)
    need = remedy.need or "Tell me what you would like done about it."
    summary = remedy.summary or "I have no remediation of my own for this one."

    if intent == "help":
        verdict = f"Here is what would actually help with {alert.key}: {need}"
    elif intent == "why":
        verdict = summary
    elif intent == "severity":
        weight = {"CRIT": "Yes -- this is the serious kind.",
                  "WARN": "It is worth fixing, but nothing is on fire.",
                  "INFO": "Not urgent; it is a loose end rather than a problem."}
        verdict = f"{weight.get(alert.sev, 'It is worth a look.')} {summary}"
    else:
        verdict = f"Still on {alert.key}. {summary}"

    rows = [f"{alert.key} · {alert.sev} · {alert.msg}"]
    if remedy.need and intent != "help":
        rows.append(f"what would help · {need}")
    if remedy.kind == "approval" and remedy.action:
        rows.append(f"{remedy.host or 'this host'} · I can run {remedy.action.replace('_', ' ')} "
                    f"once you ask me to prepare it")
    if alert.why:
        rows.append(f"why · {alert.why}")

    # The offer is always something Vault can genuinely do next: watch
    # for the change, or show the detail. Never "ask me one of those".
    if remedy.kind == "approval" and remedy.action:
        offer = "ask me *prepare that repair* and I will put it in front of you to confirm"
    elif intent == "help":
        offer = "do that and tell me when it is done -- I will confirm it on the next scan"
    else:
        offer = f"ask me *what can I do* about {alert.key} and I will give you the exact step"
    next_line = f"Next: {offer}. {NO_MODEL_NOTE}."

    parts = [verdict, "\n".join(f"- {r}" for r in rows[:6]), next_line]
    return "\n".join(parts) + f" {stamp}"


def _nothing_under_discussion(open_alerts, stamp: str) -> str:
    """A follow-up arrived but the thread has no subject -- say that in a
    sentence and point at the nearest real thing, rather than at a menu.

    Reaching here after Vault itself said "say yes" is a broken promise, so
    nothing in this module offers a bare "yes" any more: every offer names the
    thing to ask for. A confirmation is a stored token that only a tool call
    mints (`chat/registry._build_proposal`), and this module calls no tools.
    """
    if open_alerts:
        worst = sorted(open_alerts, key=lambda a: {"CRIT": 0, "WARN": 1}.get(a.sev, 2))[0]
        return ("I am not sure which thing you mean -- nothing is under discussion in this "
                "conversation yet.\n"
                f"- {worst.key} · {worst.sev} · {worst.msg}\n"
                f"Next: say *{worst.key}* and I will tell you what can be done about it. "
                f"{NO_MODEL_NOTE}. {stamp}")
    return ("I am not sure which thing you mean, and there are no open alerts to pick up.\n"
            f"Next: ask me about your security score, what is down, or who is on the Wi-Fi. "
            f"{NO_MODEL_NOTE}. {stamp}")


_STATUS_WORDS = re.compile(r"\b(?:down|status|everything ok|all good|overview|summary|broken)\b")


#: Questions that are ABOUT the thing under discussion rather than a new
#: lookup -- with a subject in the thread these win over the keyword
#: branches even when the message also carries a domain word ("why is it
#: down" is about the site that is down, not a request for the status list).
_ABOUT_INTENTS = frozenset(("why", "help", "severity"))

#: HTTP statuses and check errors, in the words a site owner needs.
_CODE_WORDS = {
    502: "502 Bad Gateway: the web server answered but the application behind it did not -- it is "
         "down, still starting, or crashed",
    503: "503 Service Unavailable: the server is up but refusing to serve -- overloaded, in "
         "maintenance, or its backend is not ready",
    504: "504 Gateway Timeout: the web server waited for the application and gave up",
    500: "500 Internal Server Error: the application crashed while handling the request",
    404: "404 Not Found: the URL Vault checks no longer exists on that site",
    403: "403 Forbidden: the site refused the request -- a firewall, auth wall or bot filter",
    401: "401 Unauthorized: the checked page now needs a login",
}


def _is_referential(ql: str) -> bool:
    return any(w in _REFERENTIAL for w in _words(ql))


def _last_guardian_text(history) -> str:
    for t in reversed(history or []):
        if isinstance(t, dict) and t.get("role") == "guardian":
            return str(t.get("text") or "")
    return ""


def _site_subject(db: DbSession, tenant, history):
    """The site the last reply named, when it named one -- the status list
    ("FlyCheap · site · down") puts a site under discussion the way the
    hand-off puts an alert under discussion. A site that is down wins over
    one that merely appeared."""
    last = _last_guardian_text(history).lower()
    if not last:
        return None
    named = [s for s in db.query(m.Site).filter_by(tenant_id=tenant.id).all()
             if s.name and s.name.strip().lower() in last]
    if not named:
        return None
    named.sort(key=lambda s: (0 if not (s.last or {}).get("ok") else 1, s.name))
    return named[0]


def _pick_subject(db: DbSession, tenant, history):
    """What "it" is, when the last reply named both a site and an alert:
    whichever the reply listed FIRST -- the bot's own "ask me about the
    first one" points at that item. Returns ("site", site), ("alert", alert)
    or (None, None)."""
    last = _last_guardian_text(history).lower()
    site = _site_subject(db, tenant, history)
    alert = _subject(db, tenant, history)
    if site is None or alert is None or not last:
        return ("site", site) if site is not None else (("alert", alert) if alert is not None else (None, None))
    site_at = last.find(site.name.strip().lower())
    alert_at = min([i for i in (last.find((alert.key or "").lower()),
                                last.find((alert.msg or "").strip()[:60].lower())) if i >= 0] or [len(last)])
    return ("site", site) if site_at <= alert_at else ("alert", alert)


def _about_site(site, ql: str, open_alerts, stamp: str) -> str:
    """Why a site is down (or how it is doing), read off its last check."""
    last = site.last or {}
    code = last.get("code")
    err = (last.get("error") or "").strip()
    intent = _intent(ql)
    if last.get("ok"):
        verdict = f"{site.name} is up: the last check got HTTP {code} in {last.get('latency_s', '?')}s."
    elif code in _CODE_WORDS:
        verdict = f"{site.name} is down. The last check got {_CODE_WORDS[code]}."
    elif code:
        verdict = f"{site.name} is down. The last check got HTTP {code}, which is not what it expects."
    elif err:
        verdict = f"{site.name} is down. The check could not connect at all: {err}."
    else:
        verdict = f"{site.name} is down. The check got no answer."
    if intent == "severity":
        verdict = ("Yes -- a site that does not answer is the serious kind; everyone who opens it "
                   "sees an error. " + verdict)
    rows = [f"{site.name} · {last.get('url') or site.url}"]
    if last.get("latency_s") is not None:
        rows.append(f"response · {last.get('latency_s')}s")
    for a in open_alerts:
        if a.key.startswith(f"site:{site.name}:"):
            rows.append(f"{a.key} · {a.sev} · {a.msg}")
            break
    if last.get("ok"):
        offer = "nothing needed right now"
    elif code in (502, 503, 504, 500):
        offer = (f"restart the application behind {site.name} on its server (or check its logs); "
                 "I re-check every scan and clear the alert the moment it answers")
    elif err:
        offer = (f"check that {site.name}'s server is up and reachable (DNS, firewall, the web "
                 "server itself); I re-check every scan")
    else:
        offer = f"open {last.get('url') or site.url} yourself to see what it shows; I re-check every scan"
    return "\n".join([verdict, "\n".join(f"- {r}" for r in rows),
                      f"Next: {offer}. {NO_MODEL_NOTE}."]) + f" {stamp}"


def _no_repeat(db: DbSession, tenant, ql: str, history, role: str, stamp: str, open_alerts) -> str:
    """The reply the keyword branches produced is what was just said. Say
    something new about what is under discussion instead of saying it
    again -- a repeated answer reads as not listening."""
    kind, thing = _pick_subject(db, tenant, history)
    if kind == "site":
        return _about_site(thing, ql, open_alerts, stamp)
    if kind == "alert":
        return _about(db, tenant, thing, ql, role, stamp)
    names = [s.name for s in db.query(m.Site).filter_by(tenant_id=tenant.id).all()
             if not (s.last or {}).get("ok")][:3] or [a.key for a in open_alerts[:3]]
    if names:
        return (f"Nothing has changed since I last answered. Ask me about one of these by name "
                f"and I will go into it: {', '.join(names)}.\n"
                f"Next: ask me *why is {names[0]} down* or *what can I do about {names[0]}*. "
                f"{NO_MODEL_NOTE}. {stamp}")
    return (f"Nothing has changed since I last answered: nothing is down and no alert is open, "
            f"so there is nothing to explain. If you are looking at something specific, name it.\n"
            f"Next: ask me about the score, devices, certificates or speed. {stamp}")


_STAMP_RE = re.compile(r"\s*\(data from [^)]*\)\s*$")


def _sans_stamp(text: str) -> str:
    """The reply without its trailing "(data from ..., N min ago)": two
    replies that differ only there are the same reply to the reader."""
    return _STAMP_RE.sub("", text or "").strip()


def answer(db: DbSession, tenant, question: str, history=None, role: str = "viewer") -> str:
    text = _answer(db, tenant, question, history=history, role=role)
    previous = _last_guardian_text(history)
    if previous and _sans_stamp(text) == _sans_stamp(previous):
        scan = latest_scan(db, tenant.id)
        open_alerts = db.query(m.Alert).filter_by(tenant_id=tenant.id, status="open").all()
        text = _no_repeat(db, tenant, (question or "").lower(), history, role, _stamp(scan), open_alerts)
    return text


def _answer(db: DbSession, tenant, question: str, history=None, role: str = "viewer") -> str:
    ql = (question or "").lower()
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    stamp = _stamp(scan)

    sites = db.query(m.Site).filter_by(tenant_id=tenant.id).all()
    hosts = db.query(m.Host).filter_by(tenant_id=tenant.id).all()
    devices = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    open_alerts = db.query(m.Alert).filter_by(tenant_id=tenant.id, status="open").all()

    def bullets(rows: list[str]) -> str:
        return "\n".join(f"- {r}" for r in rows[:6])

    # --- conversation, before the keyword branches -----------------------
    #
    # Order matters and is the whole fix. "how do I fix it" contains "fix"
    # and would otherwise fall into the generic fix branch below, which
    # answers about pending reboots on hosts -- not about the thing the
    # owner is looking at. A message that refers back to what was just said
    # is answered about THAT, and only a message that stands on its own
    # reaches the keyword branches.
    subject = _subject(db, tenant, history)
    if (subject is not None and _is_followup(ql) and not _is_capability_question(ql)
            and not _mentions_domain(ql)):
        return _about(db, tenant, subject, ql, role, stamp)
    # "why is it down", "how bad is that", "what can I do about it" -- a
    # question about the thing under discussion, even though "down" is a
    # domain word. The pronoun is what makes it a follow-up; without one
    # ("why is the score low") the keyword branches are right to answer.
    if _intent(ql) in _ABOUT_INTENTS and _is_referential(ql) and not _is_capability_question(ql):
        kind, thing = _pick_subject(db, tenant, history)
        if kind == "site":
            return _about_site(thing, ql, open_alerts, stamp)
        if kind == "alert":
            return _about(db, tenant, thing, ql, role, stamp)

    if any(w in ql for w in ("score", "grade", "how am i doing", "how are we doing")):
        raw_score = raw.get("score") or {}
        verdict = f"Your security score is {scan.score if scan else '?'} (grade {raw_score.get('grade') or '?'})."
        deds = raw_score.get("deductions") or []
        rows = [f"{d.get('category')} · -{d.get('points')} pts · {d.get('why')}" for d in deds[:6]]
        next_line = "Next: nothing needed right now." if not deds else \
            "Next: ask me *about the top deduction* and I will go through it with you."
        parts = [verdict]
        if rows:
            parts.append(bullets(rows))
        parts.append(next_line)
        return "\n".join(parts) + f" {stamp}"

    # Whole words only: a plain substring check on "down" matched "Download"
    # in a speed alert and "FlyCheap DOWN" in a stale line, steering a
    # "fix it" into this branch.
    if _STATUS_WORDS.search(ql):
        down_sites = [s.name for s in sites if not (s.last or {}).get("ok")]
        down_hosts = [h.name for h in hosts if (h.last or {}).get("reachable") is False]
        # "Everything is up" must never be the answer while a WARN/CRIT is
        # open: nothing being DOWN is not the same as nothing needing a look.
        open_bad = [a for a in open_alerts if a.sev in ("WARN", "CRIT")]
        n_down = len(down_sites) + len(down_hosts)
        if not n_down and not open_bad:
            verdict = "Everything is up."
        elif not n_down:
            verdict = f"Nothing is down, but {len(open_bad)} alert(s) need a look."
        else:
            verdict = f"{n_down} thing(s) are down."
        rows = ([f"{n} · site · down" for n in down_sites]
                + [f"{n} · host · unreachable" for n in down_hosts]
                + [f"{a.msg} · {a.sev}" for a in open_bad[:6]])
        next_line = "Next: nothing needed right now." if not rows else \
            "Next: ask me *about the first one* and I will look into it now."
        parts = [verdict]
        if rows:
            parts.append(bullets(rows))
        parts.append(next_line)
        return "\n".join(parts) + f" {stamp}"

    if any(w in ql for w in ("attack", "brute", "fail2ban", "hacked", "intrusion")):
        failed = sum(int((h.last or {}).get("failed_ssh_24h") or 0) for h in hosts)
        sec_crit = [a for a in open_alerts if a.sev == "CRIT" and a.key.startswith(("sec:", "home:", "lan:"))]
        if sec_crit:
            verdict = f"Active attack sign: {sec_crit[0].msg}"
        elif failed:
            verdict = f"Background noise only: {failed} failed login(s) in 24h, nothing critical."
        else:
            verdict = "No sign of it: 0 failed logins in 24h."
        return f"{verdict}\nNext: nothing needed right now. {stamp}"

    if any(w in ql for w in ("device", "wifi", "wi-fi", "lan", "network", "who is on")):
        online = [d for d in devices if d.presence == "ONLINE"]
        unverified = [d for d in devices if not d.trusted and not d.label]
        verdict = f"{len(online)} of {len(devices)} known devices online, {len(unverified)} not verified."
        rows = [f"{d.label or d.name or d.mac} · {d.ip} · online" for d in online[:6]]
        next_line = ("Next: nothing needed right now." if not unverified else
                     "Next: ask me *which devices are unverified* and I will list them.")
        parts = [verdict]
        if rows:
            parts.append(bullets(rows))
        parts.append(next_line)
        return "\n".join(parts) + f" {stamp}"

    if any(w in ql for w in ("slow", "latency", "speed", "lag", "buffering")):
        sp = raw.get("speed") or {}
        down = sp.get("down_mbps") or sp.get("down")
        up = sp.get("up_mbps") or sp.get("up")
        slow_sites = [s for s in sites if (s.last or {}).get("latency_s") is not None]
        slow_sites.sort(key=lambda s: (s.last or {}).get("latency_s") or 0, reverse=True)
        if down or up:
            verdict = f"Your line last measured {down} Mbps down and {up} Mbps up."
        else:
            verdict = "I have no recent speed measurement to compare against."
        rows = [f"{s.name} · {(s.last or {}).get('latency_s')}s · response time" for s in slow_sites[:6]]
        next_line = ("Next: ask me *run a speed test* and I will start one now."
                     if not rows else "Next: ask me *re-check the slowest site* and I will do it now.")
        parts = [verdict]
        if rows:
            parts.append(bullets(rows))
        parts.append(next_line)
        return "\n".join(parts) + f" {stamp}"

    if any(w in ql for w in ("fix", "reboot", "restart", "upgrade", "patch", "remediate")):
        # Rules mode has no tool calls at all (providers.chat returns none),
        # so this branch must say plainly what it cannot do and who can.
        reboot_needed = [h.name for h in hosts if (h.last or {}).get("reboot_required")]
        pending = [(h.name, len((((h.last or {}).get("sec")) or {}).get("security_updates") or []))
                   for h in hosts]
        verdict = ("I cannot run a fix right now. My AI model is unavailable, so I am answering "
                   "from rules only and cannot act.")
        rows = [f"{n} · host · needs a reboot" for n in reboot_needed]
        rows += [f"{n} · host · {c} pending security update(s)" for n, c in pending if c]
        parts = [verdict]
        if rows:
            parts.append(bullets(rows))
        parts.append("Next: an admin on this site can run it from the dashboard's fix button, "
                     "and I will run it here myself again as soon as my model is back.")
        return "\n".join(parts) + f" {stamp}"

    if any(w in ql for w in ("disk", "memory", "ram")):
        rows = [f"{h.name} · disk {((h.last or {}).get('disk_pct'))}% · mem {((h.last or {}).get('mem_pct'))}%"
                for h in hosts if (h.last or {}).get("reachable", True)]
        verdict = "Here is disk and memory on every reachable host." if rows else "No hosts to report on."
        return "\n".join([verdict, bullets(rows)] if rows else [verdict]) + f"\nNext: nothing needed right now. {stamp}"

    if any(w in ql for w in ("cert", "ssl", "tls")):
        rows = [f"{s.name} · {(s.last or {}).get('cert_days_left')}d left · cert" for s in sites]
        verdict = "Here is certificate expiry for every site." if rows else "No sites to report on."
        return "\n".join([verdict, bullets(rows)] if rows else [verdict]) + f"\nNext: nothing needed right now. {stamp}"

    if any(w in ql for w in ("alert", "warn", "problem")):
        if not open_alerts:
            return f"No open alerts.\nNext: nothing needed right now. {stamp}"
        rows = [f"{a.key} · {a.sev} · {a.msg}" for a in open_alerts[:6]]
        return "\n".join([f"{len(open_alerts)} open alert(s).", bullets(rows)]) + \
            f"\nNext: ask me *about the top one* and I will go through it with you. {stamp}"

    # --- nothing matched ------------------------------------------------
    #
    # The menu is a good answer to "what can you do?" and a bad answer to
    # everything else, so it is the narrow case: an actual capability
    # question, or an opening message in a thread with no history. A
    # question Vault did not understand gets a sentence saying so and a
    # real thing to pick up -- never a list of topics.
    # An explicit "what can you do?" gets the menu whether or not something
    # is under discussion -- it is a question ABOUT the menu, and answering
    # it with the heartbeat would be the same mistake in the other
    # direction.
    if _is_capability_question(ql) or not history:
        return (f"I can answer about: security score, status/down, alerts, devices/Wi-Fi, "
                f"disk/memory, certificates, speed and slowness, or whether anyone is attacking "
                f"you.\n"
                f"Next: ask me one of those. {stamp}")
    if subject is not None:
        return _about(db, tenant, subject, ql, role, stamp)
    return _nothing_under_discussion(open_alerts, stamp)
