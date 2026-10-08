"""Client-facing deployment documents — LLD, HLD, installation guides.

These are read by somebody standing in a datacenter holding a cable. A wrong
line does not cause a bug report; it stops the work and escalates.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Deployment documents",
    triggers=("lld", "hld", "installation guide", "install guide", "runbook",
              "rack diagram", "cabling", "bom", "shipment", "racking",
              "deployment doc"),
    agents=("ops", "concierge"),
    priority=82,
    body="""
The inventory sheet is the source of truth; every other document derives from
it. So the classic failure is not a wrong document — it is a RIGHT sheet and a
STALE guide. The field team reads the guide. Check the pair against each
other, and both against what was actually shipped, before believing either.

Documents rot in predictable places. Worth checking every time: hardware model
names that came from a previous site, port numbers that belong to a different
server generation, link speeds that no longer match the fitted cards, counts
that were never updated, and equipment that exists in the sheet but appears
nowhere in the guide's shipment list.

A figure that contradicts the text beside it is worse than either being wrong
alone — it stops a careful reader completely. When text and image disagree,
that is the finding.

Rebuild from the shared copy the team pulls from, never from a local one.
Other people edit those files too, and a rebuild from a private copy silently
reverts their work.

A correction is not finished when the file is fixed. It is finished when it
has replaced the copy in the folder the field team and the client actually
open. A fix that stays in a download folder is why the same escalation
repeats. If the shared location cannot be written to, say so and hand over a
file named exactly like the one it replaces — do not report it as done.
"""))
