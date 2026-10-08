"""Miro's own VPSie tenancy — the ground everything else stands on.

Distinct from `vpsie.py`, which is pod work: Dell sleds and Arista fabric
built for clients. This is the cloud account that runs inRoots, Guardian,
FlyCheap and ATLAS itself. The two share a vendor name and nothing else, and
the pod packs match none of the account's findings.

Learned on 2026-09-22, rebuilding the module against the live API.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Reading the VPSie account",
    # Deliberately not "instance" or "server": those fire on pod work and
    # would steal a slot from the hardware packs, which have better answers
    # for that ground.
    triggers=("virtualmachines", "vpsie api", "clientid", "clientsecret",
              "accesstoken", "api.vpsie", "apps/v2", "is_suspended",
              "dcidentifier", "hostname state"),
    agents=("ops", "sentinel"),
    priority=86,
    body="""
The VPSie API answers in shapes that read as the opposite of what they mean.

`state` on a virtual machine is the DATACENTER — "NY2", "LAX", "VA1". It is
not the power state. Reading it as a status reports every instance as down,
including the one you are running on, and at the severity that wakes someone.
Power lives in `power` and `is_active`. `is_suspended` is a third thing again
and usually means billing rather than a crash, so it deserves its own words.

A rejected credential comes back as HTTP 500 with the vendor's own envelope,
`{"error": true, "message": ...}`. So "the API is down" and "the secret is
wrong" are indistinguishable unless the body is read. Reporting the first
when it is the second sends someone to check a status page instead of a
dashboard. Read the message; never conclude an outage from the status code
alone.

THERE ARE TWO BASE PATHS, and the official Go SDK is the only place that
says so. Authentication is POST /api/v2/auth/from/api. Everything else --
vm, snapshot, backup, billing, monitoring, firewall, domains -- lives under
/apps/v2. A resource call sent to /api/v2 answers 404 "Not found", which
reads like a missing endpoint rather than the wrong base. That cost a
snapshot run: the path was right and the base was wrong.

What the API actually offers, from the SDK, so a question can be answered
without guessing: GET /apps/v2/vm lists instances (cpu, ram, ssd, power,
is_suspended, datacenter as `state`); /vm/{id} for one, /vm/status/{id} for
its state. POST /vm/start, /vm/stop, /vm/restart, /vm/resize {vmIdentifier,
ram, cup -- sic, the SDK misspells it}. Snapshots: GET /snapshot, POST
/snapshot/add {vmIdentifier, name, note}, /snapshot/rollback, and
/snapshot/enable/auto for a policy. Backups: GET /backup, POST /backup/add,
and an auto-backup policy. Billing: /billing/invoices, /billing/estimated/
usages. Monitoring is alert RULES (/monitoring/rules), not utilisation --
VPSie does not expose CPU or memory use over the API, so a resize question
needs Guardian's host readings, not this one.

Balance is nested, not top-level, and two different monthly charges are
reported that can disagree several-fold — a list price and what is actually
billed. Runway computed from the smaller one is wrong in the direction that
lets the account lapse. Prefer the larger when they disagree, and say which
was used.

Two numbers from two endpoints are only comparable when they measure the
same thing. The dashboard's activeResources "Server" count is a lifetime
resource counter -- it read 21 while /vm listed 9, and /vm's own `total`
field also said 9. Comparing the dashboard count to the instance list
produced a confident finding of "12 servers invisible to every check" that
was simply wrong. Check a list against its own endpoint's total; treat a
dashboard aggregate as information, never as a discrepancy.

A 202 from /snapshot/add means ACCEPTED, not done. Nine were accepted in
one burst and two existed twenty minutes later; VPSie evidently drops what
it does not get to. Issue one, poll /snapshot until that hostname appears,
then the next. And VPSie rate-limits the login endpoint ("try again after
an hour") after a burst -- cache the JWT, and read a limit as "blind for an
hour", never as "credentials rejected".

THE THING MOST WORTH REMEMBERING: ATLAS runs inside the account it is
watching. A lapsed balance suspends the instances, and one of them is the
host this observation came from. A restart aimed at the wrong instance takes
out the monitor. That is why the module that reads this account holds no
actions at all, and why a guard by hostname would be weaker than having none.

Credentials live in VPSIE_CLIENT_ID and VPSIE_CLIENT_SECRET in the deployment
environment, never in the repo and never in a prompt. If a report needs to
explain a failure, it says "the credentials were refused" and stops there.
"""))

register(Skill(
    name="Restore points and runway",
    triggers=("snapshot", "backup", "restore point", "restore from",
              "runway", "balance floor", "suspend the account",
              "nothing to restore"),
    agents=("ops", "sentinel", "analyst"),
    priority=85,
    body="""
Two quiet failures on the same estate, both of which only announce themselves
on the day they matter.

A missing restore point is not an alert that fires. Nothing errors, nothing
degrades, and every dashboard stays green right up until an instance is lost
and there is nothing to bring back. The question is never "did the last
backup succeed" — it is "if this were lost tonight, what exists to restore
it from". Ask it about the whole estate rather than per instance: an account
with snapshots on two hosts and none on the other seven is not partly
protected, it is unprotected where it counts.

Zero snapshots and zero backups across every instance is a finding at the top
of the list, above anything currently erroring. An outage ends; this does not.

Balance is the same shape. Payment does not fail loudly — the account simply
reaches zero and the instances suspend, together, including whatever is doing
the monitoring. So express it as time remaining rather than as a number of
dollars: "three weeks of runway" is actionable and "$355" is not, and the
arithmetic needs the real monthly charge rather than the list price.

Neither of these is ATLAS's to fix. Money out is permanently denied and there
is no override; taking a snapshot is a write to the account ATLAS runs in.
Both are reported with the exact thing to do and left to Miro. A report that
says what is missing, what it protects, and how long there is to act is worth
more than one that offers to act.
"""))
