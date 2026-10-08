"""Config drift detail (Task 83, item 5 / pattern A7): what the alert
`sec:drift:<host>:<what>` can honestly say beyond "something changed".

**What the sensor actually keeps, checked before writing any of this.** The
host probe (`sensor/guardian_sensor/hosts.py`) fingerprints five files with a
truncated md5 (`HSSH`, `HKEY`, `HCRON`, `HUFW`, `HCMP` -> `drift=...`) and
sends only that fingerprint. It never sends file content, and the sensor's
own alert builder (`sensor/guardian_sensor/alerts.py`, the `sec:drift:`
branch) fires from comparing the new fingerprint to the OLD one it holds
locally -- but that old fingerprint is never part of the alert or pushed to
the hub; only the CURRENT one reaches `Host.last["drift"]`. So neither a
prior file content nor even a prior fingerprint is available at the hub. A
line diff is not an engineering gap here, it is data Vault does not have
-- inventing one would be exactly the dishonesty this task is guarding
against.

**What IS honest to show.** The file (named, not the internal token), the
host, the current fingerprint (so someone who wants to check by hand has
something to compare against), when Vault first saw this fingerprint
(`Alert.first_seen` on the `sec:drift:` alert, since that alert opens the
instant the mismatch is detected), and a plain statement of the limit above
-- so nobody reads a blank diff panel as "nothing changed".
"""

from __future__ import annotations

from . import models as m

#: `what` token (from the alert key) -> the file(s) it actually is. Mirrors
#: `sensor/guardian_sensor/hosts.py`'s own fingerprint list one-for-one.
FILE_LABELS: dict[str, str] = {
    "sshd_config": "SSH server config (/etc/ssh/sshd_config)",
    "authorized_keys": "root's authorized_keys",
    "cron": "crontab and /etc/cron.d",
    "ufw_rules": "firewall rules (ufw)",
    "docker_compose": "docker-compose files under /opt",
}

NOTE = ("Vault keeps only a fingerprint of this file, not its contents, so it "
        "cannot show what changed inside it -- only that it did, and when.")

#: Item 26: what "config drift" MEANS, per file, in one sentence a person
#: who does not administer Linux can act on.
#:
#: Naming the file was never the hard part -- "sshd_config changed" tells an
#: owner nothing about whether to care. These five files are the ones an
#: attacker edits to keep a foothold after they are already in, which is
#: precisely why the sensor fingerprints these five and not others. Saying
#: that out loud is the difference between a log line and a warning.
WHY_IT_MATTERS: dict[str, str] = {
    "sshd_config": "This file decides who may log in to the server remotely and how. "
                   "A change here can quietly permit password logins, or let root log "
                   "in directly -- two of the first things an intruder turns back on.",
    "authorized_keys": "This file lists the keys that can log in as root WITHOUT a "
                       "password. Adding a line to it is the most common way an "
                       "intruder keeps a way back in after you have closed the hole "
                       "they came through.",
    "cron": "These are the commands the server runs on a schedule, by itself. A new "
            "entry here is how something malicious gets re-launched every few minutes, "
            "even after you kill it.",
    "ufw_rules": "These are the firewall rules -- what the server accepts from the "
                 "internet. A change can expose a database or an admin port that was "
                 "closed a moment ago.",
    "docker_compose": "These files define the containers this server runs, including "
                      "what ports they publish and what secrets they receive. A change "
                      "can expose a service or hand credentials to a new container.",
}

#: What to say when the drift is on a file the map above does not name yet.
WHY_FALLBACK = ("This is one of the files Vault fingerprints because changing it is "
                "a common way to keep access to a machine. Confirm the change was "
                "yours.")

_PREFIX = "sec:drift:"


def parse_key(key: str) -> tuple[str, str] | None:
    """`(host, what)` out of `sec:drift:<host>:<what>`, or None for any other
    key. Split from the right: a host name may itself contain no `:`
    (`hosts.py` derives it from `ssh.name`, alphanumeric/dash), but splitting
    from the right is the one direction that is safe either way."""
    if not key.startswith(_PREFIX):
        return None
    rest = key[len(_PREFIX):]
    if ":" not in rest:
        return None
    host, _, what = rest.rpartition(":")
    return (host, what) if host and what else None


def detail(db_hosts: dict[str, "m.Host"], key: str, first_seen: str) -> dict | None:
    """The honest detail for a `sec:drift:` alert row, or None for any other
    alert. `db_hosts` is `{Host.name: Host}` for the tenant, built once per
    inbox build so this never runs a query per row."""
    parsed = parse_key(key)
    if parsed is None:
        return None
    host_name, what = parsed
    host = db_hosts.get(host_name)
    fingerprint = str((host.last or {}).get("drift", {}).get(what) or "") if host else ""
    return {
        "host": host_name,
        "file": FILE_LABELS.get(what, what),
        "changed_at": first_seen,
        "fingerprint": fingerprint,
        "note": NOTE,
        "why": WHY_IT_MATTERS.get(what, WHY_FALLBACK),
    }
