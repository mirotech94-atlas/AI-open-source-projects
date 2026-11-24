"""Cross-cutting discipline for operating machines that are far away, over
links that are not reliable, where a wrong conclusion is expensive.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Unreachable is not faulty",
    triggers=("unreachable", "cannot reach", "can't reach", "no route",
              "vpn", "netbird", "tunnel", "jumphost", "offline", "not responding"),
    agents=("qa",),
    priority=89,
    body="""
Before reporting anything behind a network as broken, establish that the path
to it works. If the routing peer, tunnel or jump host is down, every probe
behind it fails identically and none of those failures are evidence.

The honest output in that case is "not assessable — the access path is down",
naming the single hop at fault. A wall of red for machines that are probably
fine is worse than no report: it burns trust and hides the one real problem.

This is the interface rule applied to reachability. A check that could not run
is UNKNOWN. It is never OK, and it is never a failure of the thing it was
pointed at.

One failed probe is also not an outage. Retry, spaced, before concluding —
and know that many hosts rate-limit rapid reconnection, so a burst of retries
manufactures the failure it was meant to test.
"""))

register(Skill(
    name="Work over an unreliable link",
    triggers=("flapping", "connection drops", "ssh drops", "long running",
              "detached", "timed out mid", "link flap"),
    agents=("qa",),
    priority=80,
    body="""
When the link between here and the machine is unreliable, the shape of the
work matters more than its content.

Anything long belongs on the far side, detached, writing to a log you poll —
so a dropped session loses the view, never the work. Anything with several
steps belongs in ONE session, because a flap between two of them can leave a
system half-changed, which is far worse than not having started.

A transfer that was interrupted looks exactly like a transfer that finished.
Confirm the far side has the whole thing before acting on it.

Report progress from the log, not from the fact that a command was launched.
"""))

register(Skill(
    name="Proving a change landed",
    triggers=("did it work", "verify", "deployed", "is it live", "confirm",
              "rollout", "shipped"),
    agents=("qa", "research"),
    priority=86,
    body="""
An exit code of zero means a command ran. It does not mean the thing you
wanted happened, and the gap between those two is where most false "done"
reports live.

Name the observable proof before starting, then go and read it: the version
string changed, the switch shows the port up, the endpoint returns 200, the
member count is what it should be. If the proof cannot be obtained, say the
change is unverified — which is a different claim from both "done" and
"failed", and the only honest one available.

Be specific about what was NOT checked. A report that quietly implies full
coverage is the one that gets believed and then breaks something.

Do one first and verify it end to end before repeating across a fleet. Ten
machines configured wrong in parallel is ten times the work to undo.
"""))
