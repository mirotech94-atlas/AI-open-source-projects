"""Guardian — the network watchdog.

Two facts shape everything this business agent says: the API lives at
my.guarder.io, and the credential's scope is the actual control, not this
module's good behaviour.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Reading Guardian's API and token",
    triggers=("guardian", "guarder", "rogue-uplink", "risky-port",
              "device vouch", "trusted device", "guardian token"),
    agents=("guardian",),
    priority=85,
    body="""
The real API is at my.guarder.io. guarder.io is the marketing site and
answers 404 to every route this module or an agent might reach for — a 404
against the bare domain is not evidence Guardian is down.

`label` and `trusted` are human vouches, not display fields: a trusted device
is skipped by the rogue-uplink check and scored lower risk. Only a
write-scope token can set them (`POST /v1/devices/{mac}`), and this module
runs on a read-scope token specifically so that path is closed from here no
matter what code is added later. If a check ever reports the token as
write-scope, that is the finding to lead with — louder than anything else
Guardian might be showing that cycle, because it means the warrant rule is
one call away from being forgeable.

The everyday token proposes nothing. The real write routes
(`/v1/alerts/{key}/{op}`, `/v1/scans`, `/v1/speedtest`) all need the scope
this module is built to argue ATLAS should not hold. The one exception is
incident containment (`guardian_contain`, Tier 2), which uses its own
separately-set write token and is covered by the incident-response skill.
"""))
