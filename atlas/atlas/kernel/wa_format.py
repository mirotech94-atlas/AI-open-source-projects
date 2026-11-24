"""The one place an outbound WhatsApp message gets its category tag.

Every message to Miro starts with what it is about ("🏠 Home sensors · ..."), so he
knows at a glance which part of ATLAS is talking. The tag is assigned in code, by
sender or kind (or, for a chat answer, by the agent or module that answered); no model
ever picks it. A message with no known topic is "💬 General", never untagged.

Three places call `ensure` so nothing can go out bare: `tutorco.notify.send_whatsapp`,
`tutorco.notify.send_whatsapp_card` and the chat reply `api.whatsapp.twiml`. Senders tag
their own text too (so the dry run and the stored outbox row show it), and `ensure`
never tags twice. Only wording changes here: no fact, tier or refusal is touched.
"""
from __future__ import annotations

import contextvars
import re
from enum import Enum

MAX_CHARS = 1500                   # Twilio takes 1600; stay clear of it
SEP = " · "
CUT_NOTE = " … (more on the portal)"


class Category(str, Enum):
    HOME_SENSORS = "home_sensors"
    TUTORCO = "tutorco"
    AGENCY = "agency"
    ACADEMY = "academy"
    COMMUNITY = "community"
    MAIL = "mail"
    MONEY = "money"
    SYSTEM = "system"
    APPROVALS = "approvals"
    CONTENT = "content"
    GENERAL = "general"


TAGS: dict[Category, str] = {
    Category.HOME_SENSORS: "\U0001F3E0 Home sensors",
    Category.TUTORCO: "\U0001F4DA TUTORCO",
    Category.AGENCY: "\U0001F3E2 Agency",
    Category.ACADEMY: "\U0001F393 Academy",
    Category.COMMUNITY: "📣 AMSM Community",
    Category.MAIL: "\U0001F4E7 Mail",
    Category.MONEY: "\U0001F4B3 Money/Stripe",
    Category.SYSTEM: "\U0001F6E0️ System health",
    Category.APPROVALS: "✅ Approvals",
    Category.CONTENT: "\U0001F4FA YouTube/content",
    Category.GENERAL: "\U0001F4AC General",
}
_PREFIXES = tuple(t + SEP for t in TAGS.values())

# Roster agent key -> category. Fixed in code; an unknown agent is General.
_AGENT: dict[str, Category] = {
    "tutorco": Category.TUTORCO, "qa": Category.SYSTEM, "content": Category.CONTENT,
    "seo": Category.CONTENT, "sales": Category.AGENCY, "bizops": Category.AGENCY,
    "sites": Category.AGENCY, "research": Category.GENERAL,
}
# Module name -> category. A module not listed is a system matter.
_MODULE: dict[str, Category] = {
    "sensors": Category.HOME_SENSORS, "tutorco": Category.TUTORCO, "tutorseo": Category.TUTORCO,
    "community": Category.COMMUNITY, "academy": Category.ACADEMY, "course": Category.ACADEMY,
    "school": Category.ACADEMY, "mail": Category.MAIL, "stripe_billing": Category.MONEY,
    "youtube": Category.CONTENT, "ytlearn": Category.CONTENT,
    "content": Category.CONTENT, "agency": Category.AGENCY, "sales": Category.AGENCY,
    "bizops": Category.AGENCY, "approvals_board": Category.APPROVALS,
}
# A question's topic, by plain regex (first match wins). Used only to label a chat
# answer when no roster agent owned it.
_TOPICS: tuple[tuple[re.Pattern[str], Category], ...] = tuple(
    (re.compile(p, re.IGNORECASE), c) for p, c in (
        (r"\b(sensors?|cctv|doorbell|driveway|porch|vehicle|clips?)\b",
         Category.HOME_SENSORS),
        (r"\b(e-?mails?|inbox|gmail|unread)\b", Category.MAIL),
        (r"\b(stripe|payouts?|revenue|invoices?|refunds?|balance)\b", Category.MONEY),
        (r"\b(community|amsm|sermon|service|congregation)\b", Category.COMMUNITY),
        (r"\b(academy|course|students?|enrol\w*)\b", Category.ACADEMY),
        (r"\b(tutorco|exampletutor|tutors?)\b", Category.TUTORCO),
        (r"\b(youtube|video|channel|subscribers?|views)\b", Category.CONTENT),
        (r"\b(approvals?|cards?)\b", Category.APPROVALS),
    ))


def category_for_agent(agent: str | None) -> Category:
    return _AGENT.get(str(agent or "").strip().lower(), Category.GENERAL)


def category_for_module(module: str | None) -> Category:
    return _MODULE.get(str(module or "").strip().lower(), Category.SYSTEM)


def category_for_text(text: str | None) -> Category:
    for pattern, category in _TOPICS:
        if pattern.search(text or ""):
            return category
    return Category.GENERAL


def tag(category: Category, text: str) -> str:
    """`<emoji> <label> · <text>`. The label is looked up in code."""
    return TAGS[_coerce(category)] + SEP + (text or "").strip()


def _coerce(category: object) -> Category:
    try:
        return Category(category)
    except ValueError:
        return Category.GENERAL


def _clamp(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS - len(CUT_NOTE)].rstrip() + CUT_NOTE


def ensure(text: str | None, category: Category | None = Category.GENERAL) -> str:
    """The message with exactly one category tag at the front and within the
    WhatsApp length limit. Already tagged text keeps its own tag; an unknown or
    missing category is General, never untagged."""
    body = (text or "").strip()
    if body.startswith(_PREFIXES):
        return _clamp(body)
    return _clamp(tag(_coerce(category), body))


# The category of the chat reply being built, per task. The answer paths set it; the
# TwiML step reads it. It never carries text and defaults to General.
_REPLY: contextvars.ContextVar[Category] = contextvars.ContextVar(
    "wa_reply_category", default=Category.GENERAL)


def set_reply_category(category: Category) -> None:
    _REPLY.set(_coerce(category))


def reset_reply_category() -> None:
    _REPLY.set(Category.GENERAL)


def reply_category() -> Category:
    return _REPLY.get()
