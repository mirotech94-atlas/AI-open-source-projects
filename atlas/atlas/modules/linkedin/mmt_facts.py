"""What the public TutorSite site says, as checkable facts. Data only.

Every `quote` is copied word for word from a page of https://tutoring.example
read on 2026-11-11 (plain GET, no login, nothing submitted); the flattened text
is saved under `tests/fixtures/mmt_site/<source>.txt`. A claim goes into a post
only if one of these says it. The internal MMT handoff and the website plan are
never a fact source: a plan is not a product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

SITE = "https://tutoring.example"
READ_AT = datetime(2026, 11, 11, 0, 30, tzinfo=UTC)

# Fixture stem -> the public page it was read from.
SOURCES: dict[str, str] = {
    "home": f"{SITE}/",
    "pricing": f"{SITE}/pricing/",
    "tour": f"{SITE}/tour/",
    "trust": f"{SITE}/trust/",
}


@dataclass(frozen=True, slots=True)
class Fact:
    id: str            # "portals.four"
    quote: str         # verbatim words from the page
    source: str        # fixture file stem: "home", "pricing", "tour", "trust"
    source_url: str    # https://tutoring.example/...
    read_at: datetime  # when the page was read (UTC)


def _f(fid: str, source: str, quote: str) -> tuple[str, Fact]:
    return fid, Fact(fid, quote, source, SOURCES[source], READ_AT)


FACTS: dict[str, Fact] = dict([
    _f("what.workspace", "home", "TutorSite brings parents, tutors, students and "
       "coordinators into one connected workspace"),
    _f("what.focus", "home", "More focus on learning. Less time managing it."),
    _f("problem.messages", "home", "Make student progress visible, not buried in "
       "messages."),
    _f("problem.admin", "home", "Less admin. Better follow-through."),
    _f("usecase.noshow", "home", "A few minutes into the lesson, the parent is told "
       "on WhatsApp that their child has not joined."),
    _f("usecase.reminder", "home", "The parent gets a WhatsApp reminder the day "
       "before each lesson."),
    _f("usecase.payment", "home", "Parents who owe money get a polite private "
       "reminder, and you see what was sent."),
    # The page line also says "as soon as", which the "soon" ban rejects, so the
    # fact is the same use case's own title: word for word, on the page.
    _f("usecase.substitute", "home", "Substitute Tutors: Notify Parents and Cover "
       "Automatically"),
    _f("how.step1", "home", "STEP 01 An event happens A tutor records attendance, "
       "submits progress or requests a timetable change."),
    _f("how.step2", "home", "STEP 02 MMT checks the context The bot checks "
       "authorised class, student, parent and policy information."),
    _f("how.step3", "home", "STEP 03 The right action follows Permitted routine "
       "actions run automatically; exceptions go to the right person."),
    _f("how.step4", "home", "STEP 04 Everyone stays aligned The portal history "
       "updates and relevant people receive the right notification."),
    _f("portals.four", "home", "Four connected portals The right view for every "
       "role."),
    _f("portals.office", "home", "Office Portal Coordinate the complete operation "
       "with role-based controls and oversight."),
    _f("bot.rules", "home", "Your team sets the rules, controls what it can do and "
       "handles anything that needs a human decision."),
    _f("bot.observe", "home", "01 · Observe Track authorised events and changes."),
    _f("bot.understand", "home", "02 · Understand Match people, classes and "
       "context."),
    _f("bot.act", "home", "03 · Act Run permitted routine workflows."),
    _f("bot.explain", "home", "04 · Explain Keep reasons and audit history visible."),
    _f("migration.help", "home", "We help map existing classes, tutors, parents and "
       "students, validate historical exports, and coordinate invitations, step by "
       "step, with safeguards against duplicate communications."),
    _f("trial.fourteen", "home", "Take a guided walkthrough or request a 14-day "
       "free trial for the functions you need."),
    _f("walkthrough.length", "home", "The walkthrough call takes between 30 and 60 "
       "minutes."),
    _f("demo.sample", "home", "Sample data only. Nothing is saved or sent."),
    _f("demo.center", "tour", "Riverside Tuition is a made-up center."),
    _f("price.one", "pricing", "One monthly quote per center, in USD"),
    _f("price.nomodules", "pricing", "Everything is in the one fee. No add-on "
       "modules."),
    _f("price.nologin", "pricing", "Tutors, parents and students all get an "
       "account, and there is no charge per login."),
    _f("price.noshare", "pricing", "When parents pay by card, Stripe charges its own "
       "fee; TutorSite adds nothing on top."),
    _f("trust.backups", "trust", "A copy of the whole database is saved every "
       "night, and the 14 most recent are kept."),
    _f("trust.https", "trust", "All traffic uses HTTPS."),
    _f("trust.log", "trust", "Every change is recorded with who and when."),
    _f("trust.export", "trust", "Reports export to Excel or CSV, or print as PDF."),
])

# Never in a post, whatever the site says today: features that are planned or
# not on the live site, "soon" talk, and competitor names. Group-class wording
# is blocked until the owner confirms what is built (the live knowledge base does
# mention class WhatsApp groups; that stays out of posts by default).
# Confirmed by the MMT platform owner 2026-11-10: none of this may appear in any
# post, intro or fact list. Rule: a term not on tutoring.example today is blocked.
# SAFE: the 1:1 WhatsApp bot, the four portals, the 14-day free trial, walkthrough
# and seat requests. A small copy lives in atlas/modules/mmt/guard.py.
BLOCKLIST_PATTERNS: tuple[str, ...] = (
    r"communit(?:y|ies)",                                 # WhatsApp community
    r"join[\s-]+links?", r"invite[\s-]+links?",
    r"chat\.whatsapp\.com",
    r"groups?[\s-]+(?:billing|pricing|prices?|digests?|mailer|mailing|chats?)",
    r"(?:billing|pricing|digests?|mailer)[\s-]+(?:per|by|for)[\s-]+groups?",
    r"group[\s-]+(?:class(?:es)?|lessons?|sessions?|tuition|bookings?)",
    r"bookable[\s-]+groups?", r"book(?:ing)?[\s-]+(?:a[\s-]+)?groups?",
    r"\bmirror(?:s|ed|ing)?\b", r"whatsapp[\s-]+groups?",
    r"\bgroups?\b[^.]{0,40}\bwhatsapp\b", r"\bwhatsapp\b[^.]{0,40}\bgroups?\b",
)
BLOCKLIST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in BLOCKLIST_PATTERNS)

PLANNED_DENY: tuple[re.Pattern[str], ...] = BLOCKLIST + tuple(
    re.compile(p, re.IGNORECASE) for p in (
    r"group[\s-]+class", r"group[\s-]+tuition", r"group[\s-]+lesson",
    r"per[\s-]+group", r"groups?[\s-]+first", r"whatsapp[\s-]+groups?",
    r"coming[\s-]+soon", r"\bsoon\b", r"\broadmap\b", r"\bplanned\b",
    r"\bupcoming\b", r"tutorhq",
    r"tutorcruncher", r"teachworks", r"tutorbird", r"teach\s*'?n'?\s*go",
))
