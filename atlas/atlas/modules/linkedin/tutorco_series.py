"""The fixed twelve-post LinkedIn series for the ExampleTutor company page.

Written by hand from `tutorco_facts.FACTS`, never by the model (invariant 6: no
guessed numbers; invariant 4: no ingested text reaches a prompt here). The
sweep's refill takes the next unposted posts from `series_due`; each becomes a
draft that is Miro's card to read, and publishing is a second Tier 2 card.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ...kernel.contracts import Health, Observation
from .tutorco_facts import FACTS, PLANNED_DENY, SOURCES

LINK = "https://exampletutor.com"
DRAFTED_BY = "atlas-tutorco-series"


@dataclass(frozen=True, slots=True)
class Post:
    id: str
    title: str
    text: str
    fact_ids: tuple[str, ...]


def _p(pid: str, title: str, body: str, tags: str, *facts: str) -> Post:
    return Post(pid, title, f"{body}\n\n{LINK}\n{tags}", facts)


SERIES: tuple[Post, ...] = (
    _p("M01", "What ExampleTutor is",
       "ExampleTutor brings parents, tutors, students and coordinators into one "
       "connected workspace.\n\n"
       "The idea is simple: more focus on learning, less time managing it. "
       "Classes, updates and messages are connected in one place.\n\n"
       "We are building it for tutor centers.",
       "#TutoringCenters #EdTech", "what.workspace", "what.focus"),
    _p("M02", "The problem we started from",
       "Running a tutor center often means student progress is buried in "
       "messages.\n\n"
       "Who was absent? What did the tutor note? Which parent was told?\n\n"
       "We want progress to be visible, not buried, and less admin for the "
       "people who run the center.",
       "#TutoringCenters #Operations", "problem.messages", "problem.admin"),
    _p("M03", "Use case: no-show alerts",
       "A student does not join a lesson. What happens next?\n\n"
       "With ExampleTutor, a few minutes into the lesson the parent is told on "
       "WhatsApp that their child has not joined.",
       "#TutoringCenters #WhatsApp", "usecase.noshow"),
    _p("M04", "Use case: class reminders",
       "Families are busy, so a reminder helps.\n\n"
       "The parent gets a WhatsApp reminder the day before each lesson, sent "
       "for the center, so the office is not typing the same message again "
       "and again.",
       "#TutoringCenters #WhatsApp", "usecase.reminder"),
    _p("M05", "Use case: payment reminders",
       "Chasing overdue fees is awkward for everyone.\n\n"
       "Parents who owe money get a polite private reminder, and the center "
       "sees what was sent. No guessing who was already nudged.",
       "#TutoringCenters #SmallBusiness", "usecase.payment"),
    _p("M06", "Use case: substitute tutors",
       "A tutor cannot make it and a substitute steps in.\n\n"
       "Parents and the cover tutor are told privately when the center assigns "
       "the substitute, so nobody turns up to a surprise.",
       "#TutoringCenters #Tutoring", "usecase.substitute"),
    _p("M07", "How it works",
       "How ExampleTutor works, in four steps:\n\n"
       "First, an event happens. A tutor records attendance, submits progress or "
       "requests a timetable change.\n"
       "Second, TUTORCO checks the context: the class, student, parent and policy "
       "information.\n"
       "Third, the right action follows. Permitted routine actions run "
       "automatically; exceptions go to the right person.\n"
       "Fourth, everyone stays aligned. The portal history updates and the right "
       "people are notified.",
       "#TutoringCenters #Automation",
       "how.step1", "how.step2", "how.step3", "how.step4"),
    _p("M08", "Four portals",
       "One platform, four connected portals: Office, Parent, Tutor and "
       "Student.\n\n"
       "Each person gets the right view for their role. The Office portal is "
       "where the center coordinates the whole operation, with role-based "
       "controls and oversight.",
       "#TutoringCenters #EdTech", "portals.four", "portals.office"),
    _p("M09", "TUTORCO Bot and the rules",
       "TUTORCO Bot works in four moves: observe, understand, act and explain.\n\n"
       "It tracks authorised events, matches people, classes and context, runs "
       "permitted routine workflows and keeps its reasons and audit history "
       "visible.\n\n"
       "Your team sets the rules, controls what it can do and handles anything "
       "that needs a human decision.",
       "#TutoringCenters #AI",
       "bot.observe", "bot.understand", "bot.act", "bot.explain", "bot.rules"),
    _p("M10", "What we actually do day to day",
       "What working with us looks like.\n\n"
       "Moving over: we help map your existing classes, tutors, parents and "
       "students, validate historical exports, and coordinate invitations, "
       "with safeguards against duplicate communications.\n\n"
       "Seeing it first: book a walkthrough (the call takes between 30 and 60 "
       "minutes), explore the demo with sample data, or ask for a 14-day free "
       "trial.",
       "#TutoringCenters #EdTech",
       "migration.help", "walkthrough.length", "demo.sample", "trial.fourteen"),
    _p("M11", "How pricing works",
       "How we price ExampleTutor: one monthly quote per center, in USD.\n\n"
       "Everything is in the one fee, with no add-on modules. There is no "
       "charge per login for tutors, parents or students. And when parents pay "
       "by card, Stripe charges its own fee and ExampleTutor adds nothing on "
       "top.",
       "#TutoringCenters #SmallBusiness",
       "price.one", "price.nomodules", "price.nologin", "price.noshare"),
    _p("M12", "Trust and your data",
       "Running a center means holding families' data. What our trust page "
       "says:\n\n"
       "All traffic uses HTTPS. A copy of the whole database is saved every "
       "night, and the 14 most recent are kept. Every change is recorded with "
       "who and when. And reports export to Excel or CSV, or print as PDF.",
       "#TutoringCenters #DataPrivacy",
       "trust.https", "trust.backups", "trust.log", "trust.export"),
)


def series_due(state: dict[str, Any], *, live_ok: dict[str, bool] | None = None,
               ) -> list[dict[str, Any]]:
    """The next unposted series posts, in order, as bank items.

    A post whose series id is already in the bank (any status, so a rejected
    one is not re-offered) is skipped. A post citing a fact that
    `live_ok` marks missing is held: it is not drafted until the site says it
    again. `live_ok=None` means "not checked": nothing is held on that alone.
    """
    from .store import bank

    seen = {str(e.get("series_id")) for e in bank(state, "tutorco")[0]
            if e.get("series_id")}
    out: list[dict[str, Any]] = []
    for post in SERIES:
        if post.id in seen:
            continue
        if live_ok is not None and any(live_ok.get(f) is False for f in post.fact_ids):
            continue
        if any(p.search(post.text) for p in PLANNED_DENY):
            continue                      # a second lock beside the tests
        out.append({"kind": "text", "text": post.text, "series_id": post.id,
                    "fact_ids": list(post.fact_ids), "drafted_by": DRAFTED_BY})
    return out


async def tutorco_facts_fresh() -> Observation:
    """Re-read each source page and check every fact's quote is still there.

    Read only. A page that cannot be read is UNKNOWN, never OK; a missing
    quote is DEGRADED and names the fact ids.
    """
    from .. import _scrape

    check = "tutorco_facts_fresh"
    pages: dict[str, str] = {}
    for stem, url in SOURCES.items():
        got = await _scrape.fetch(url, agent="tutorco")
        if not got.ok:
            return Observation(check, Health.UNKNOWN,
                               detail=f"{url} could not be read "
                                      f"({got.blocked or got.status}); facts not checked")
        pages[stem] = " ".join((got.text or "").split())
    missing = sorted(f.id for f in FACTS.values()
                     if " ".join(f.quote.split()) not in pages[f.source])
    at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if missing:
        return Observation(check, Health.DEGRADED,
                           detail=f"missing from the site at {at}: {', '.join(missing)}",
                           evidence={"missing": missing})
    return Observation(check, Health.OK,
                       detail=f"all {len(FACTS)} facts on the site, read {at}")
