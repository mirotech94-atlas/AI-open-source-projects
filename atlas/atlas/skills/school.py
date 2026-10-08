"""College work — how each course's drafts are presented, and who submits.

ATLAS drafts; Miro writes, types and submits. That line is the whole point
of this skill, and it is also the only honest one: Brightspace sits behind
SSO with MFA, ATLAS has no login path, and a submission would be Tier 2 in
any case.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="College work: drafts he writes himself",
    triggers=("brightspace", "assignment", "homework", "hw", "lab report",
              "webwork", "midterm", "final", "syllabus", "csc 221", "phy 120",
              "phy 121"),
    agents=("school", "scheduler"),
    priority=80,
    body="""
ATLAS drafts, Miro writes, types and submits. Nothing is submitted to
Brightspace by ATLAS — there is no login path, and "done" means the draft
exists and he has what he needs to hand it in himself. Never say an
assignment was submitted.

How each work kind is presented (the course's kind is configuration he
confirmed with school.set_course, never inferred from a title):

- handwritten (PHY 120): numbered steps, one per line, units on every
  quantity, the final answer boxed. No prose — he copies it by hand.
- webwork (MAT): give the exact string to type into WebWork for each
  answer. Note radians or degrees, and the significant figures WebWork
  will check against.
- files (CSC 221): one file per class, a README with how to build and run,
  tests only when the assignment asks for them.
- lab report (PHY 121): data first, as tables, then analysis with the
  uncertainty propagation shown. A measurement he has not given is a blank
  for him to fill in, never an invented number.

Every due date comes from school_upcoming read in this turn, with the time
it was read and whether it came from the calendar feed or an email. If the
feed is unreachable, say so — do not reuse a date from earlier.
"""))
