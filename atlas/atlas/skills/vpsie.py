"""VPSie pod operations — Dell sleds, Arista fabric, Proxmox, the platform.

Hard-won on the T07 build. Every line here is a discriminator that cost
hours to learn the first time.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Pod hardware signals",
    triggers=("idrac", "mellanox", "connectx", "qsfp", "sfp+", "nic card",
              "sled", "c6420", "c6400", "no-carrier", "link down", "40g",
              "bond2", "bond1", "firmware", "dimm", "racadm"),
    agents=("ops", "sentinel"),
    priority=85,
    body="""
Hardware reports lie in specific, knowable ways. Read the signal before
believing the conclusion.

- A Mellanox card missing from the iDRAC NIC inventory is almost never a
  missing card. It is InfiniBand mode: the port has no ethernet personality,
  so the OS shows no netdev and the iDRAC lists nothing. Confirm with the
  PCIe inventory, which still sees it.
- Identify NICs by DRIVER, never by interface order. `mlx4_en` is the 40G
  management pair; `i40e` is the 10G storage pair. The nicN numbering differs
  between sleds of the same model.
- On a 4-node chassis sled the data ports are 1-4 only: 1 and 2 are 40G
  (management), 3 and 4 are 10G (storage). Ports 5 and 6 belong to an older
  rackmount generation. Guidance naming them is guidance for different metal.
- Identical model, identical cable, identical switch port, one link dead:
  suspect card firmware before hardware. One known-bad version advertises
  auto-negotiation that never completes against a forced switch port.
- A stale iDRAC inventory (a disk or card that is physically present but
  absent from the listing) is usually a controller refresh away, not a
  failure. Treat "absent from one inventory" and "dead" as different findings.

Never propose a blind firmware write to a card whose current firmware cannot
be read cleanly. That is how a remote node becomes a site visit. The safe
paths are matching a known-good twin or replacing the part.

The link is not up because the node says so. It is up when the switch reports
the port connected at the expected speed and the port-channel at the expected
aggregate.
"""))

register(Skill(
    name="Proxmox cluster state",
    triggers=("proxmox", "corosync", "pmxcfs", "quorum", "pvecm",
              "cluster node", "ring0", "ring1", "cfs-lock", "px node"),
    agents=("ops",),
    priority=85,
    body="""
Quorum arithmetic first: expected votes versus total votes tells you how many
members are actually talking. A node that is powered on and reachable can
still be outside the membership.

Three corosync rings carry the cluster — two heartbeat networks and the
management network. A node can look healthy on management while a heartbeat
ring is silently dead, usually from a VLAN missing off the bridge's allowed
list. Verify every ring independently before concluding the cluster is fine.

Joins are serial by nature: two nodes joining at once collide on the config
lock and one fails with a lock timeout. That failure is a collision, not
corruption, and it is expected under parallel work.

A node whose cluster filesystem is mounted but not serving will refuse web
logins with an authentication-key lock error while SSH still works perfectly.
Read it as "the cluster filesystem is not online", not "bad password" — the
credential is not the problem and rotating it fixes nothing.

Version skew of a patch release across members is not a fault and not worth
an alert.
"""))

register(Skill(
    name="Pod power event",
    triggers=("power event", "power loss", "site came back", "after reboot",
              "galera", "wsrep", "xtradb", "crashloop", "bootstrap"),
    agents=("ops", "sentinel"),
    priority=88,
    body="""
After a site-wide power loss the application errors are the symptom and the
database is the disease. Diagnose in this order or you will chase ghosts:

1. The clustered database. A Galera/PXC cluster does not self-recover from a
   simultaneous full stop — every node comes back refusing to start, each one
   unwilling to assume it holds the newest data. Until it is deliberately
   recovered, every application that depends on it crashloops, and each of
   those crashloops looks like its own bug.
2. The virtual machines. Guests without a start-on-boot flag stay down after
   a host reboot, which silently takes the whole platform with them.
3. Only then the application pods.

The tell that you are in this situation rather than an application bug: many
unrelated services failing at once with connection-lost errors at startup,
all dated to the same minute.

Report it as one incident with a cause, not as N separate service outages.
"""))

register(Skill(
    name="Platform gateway errors",
    triggers=("503", "envoy", "gateway", "httproute", "ingress", "argocd",
              "upstream", "pod not ready", "imagepullbackoff"),
    agents=("ops",),
    priority=86,
    body="""
For a 503 behind an API gateway, the access log is the oracle — it names the
cause and saves an hour of guessing. Two response flags carry most of it:

- Connection refused / no healthy upstream: nothing is listening. The
  application crashed at startup, usually on a missing required environment
  variable. Read the top of its log, not the tail.
- Connection termination immediately after connect: a protocol mismatch. The
  gateway is speaking plaintext to something serving TLS, or the reverse.
  That is a routing-configuration finding, not an application fault.

A Ready pod is not proof of a working service — several charts define no
readiness probe at all, so a crashed process can report Ready indefinitely.
Verify by what is listening, not by the status column.

When two routes claim the same hostname, the older one wins. A "fix" that
adds a corrected route while the stale one still exists changes nothing, and
looks inexplicable until you list them.

There are two paths to every hosted service — the internal gateway and the
public load balancer. Testing one and reporting on the other is how a real
outage gets missed. State which path you measured.
"""))
