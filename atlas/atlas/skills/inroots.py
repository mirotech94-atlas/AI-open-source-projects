"""inRoots — the attendance and team-presence platform at status.example.org.

The data is about people, and people read what it produces. Both facts
constrain how a finding here should be handled.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Attendance data questions",
    triggers=("attendance", "sign-in", "signin", "sign-off", "signoff",
              "hours worked", "standup", "presence", "inroots", "roots digest",
              "late", "absent"),
    agents=("analyst", "ops"),
    priority=87,
    body="""
Production is the source of truth. A local checkout can be days stale, and a
successful deploy log is not evidence that behaviour changed — verify against
the live API or the live database.

Numbers here mislead in three known ways:

- Hours come in raw and effective forms. Quoting the wrong one makes a normal
  day look like an absence, or the reverse.
- "Days used" includes sick leave, which does not draw down the same quota as
  vacation. A large raw total is often mostly sick days and means nothing on
  its own.
- Timezones are per-user and some are deliberately set to UTC. A sign-in that
  looks hours late is usually a correct record read in the wrong zone. Never
  auto-correct a timezone that looks wrong.

When a record looks anomalous, read the raw message that produced it before
concluding anything. The normalised fields lose nuance the original text has,
and the original often explains the anomaly outright.

Aggregate reports and per-user reports are different questions. Scope to what
was actually asked; a per-user view that silently includes the whole org is a
privacy problem, not a formatting one.
"""))

register(Skill(
    name="Changing the attendance bot",
    triggers=("bot logic", "bot message", "dm the team", "matrix room",
              "standup bot", "neural", "auto-approve", "attendance bot"),
    agents=("ops",),
    priority=84,
    body="""
This bot writes to real employees in real rooms, and a sent message cannot be
edited or recalled. Treat every behaviour change as outward-facing.

Existing behaviour is not yours to alter. Additive and shadow-mode work is
fine; anything that changes what an employee experiences needs explicit
agreement first, one question at a time. "It is obviously better" is not
agreement.

Anything the bot says is one or two lines. No recovery narratives, no
explaining itself, no apologising at length. The same applies to one-off
announcements.

The automated decision layer runs in shadow by default. Exactly one decision
type is allowed to act on its own, and only within a narrow band. Treat any
widening of that as a change requiring consent, and know that a single
setting returns it to shadow if it misbehaves.

A person appearing in the data is not a test account. Assume every unfamiliar
name is a real employee until proven otherwise.
"""))

register(Skill(
    name="Team rooms and leave",
    triggers=("pto", "leave request", "vacation", "sick", "day off",
              "pto room", "excuse", "balance"),
    agents=("inbox", "analyst"),
    priority=84,
    body="""
Rooms are typed — attendance rooms and leave rooms are separate, per team. A
message lands where the person chose to put it, which is often not where it
belongs. Posting a lateness explanation in the leave room does not make it
leave, and the system will usually record nothing at all.

So when a request seems to have been ignored, check the room it arrived in
before assuming a parsing failure. "Nothing was created" is frequently
correct behaviour applied to a misfiled message.

Sick leave and holidays are counted differently from vacation. Say which kind
you mean whenever a number is involved.

Manager-facing routing depends on a reporting structure that may be empty. If
no manager relationships are recorded, anything addressed to "the manager"
silently reaches nobody — report that as the finding rather than assuming
delivery.

Moving or deleting somebody's message in a team room is irreversible and
visible to everyone in it. That is never an automatic action; surface it and
let Miro decide.
"""))
