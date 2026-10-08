"""Which servers are running an operating system past its support date.

The MSP spec wants this for two readers at once. The IT contact cares that
an unsupported OS stops receiving security patches; the person who approves
spending cares that it implies a replacement, and getting in front of that
person is how an MSP's contract grows. It is also a recurring question on
cyber-insurance forms, where "any systems past end of support?" is one of
the four questions most likely to move a premium.

Vault already observes the OS and version on every host it can reach.
The end-of-support date is reference data, and reference data has a
property that measurements do not: it goes stale silently.

SO THE DATASET IS DATED, AND UNKNOWN IS NOT SUPPORTED.

`AS_OF` stamps the table, and every answer carries it. A distribution this
table has never heard of resolves to `unknown` with that date attached --
never to "supported", which is the failure mode that matters. A network
running something exotic is exactly the case where a confident green row
is worst, and it is the same rule the trust plan applies to the OUI
database: render "vendor unknown (OUI db 2026-08)" rather than blank,
because the dated version reads as engineering and the blank reads as a
bug.

THESE DATES ARE MAINTAINED BY HAND AND SHOULD BE CHECKED against the
vendors' own pages before the compliance pack is sold on the strength of
them. That is why `controls.asset_lifecycle` is PARTIAL rather than
evidenced: Vault's observation of the version is strong, the date it is
compared against is a table someone has to keep current.
"""
from __future__ import annotations

import datetime as dt
import re

#: When this table was last reviewed. Printed alongside every answer.
AS_OF = "2026-09"

#: Days before end of support at which a system starts being reported as
#: ending soon. A quarter is roughly the shortest notice on which an SMB
#: can actually budget and schedule a replacement, which is the point of
#: saying anything at all.
ENDING_SOON_DAYS = 90

#: `(distro, major version) -> end of standard vendor support`.
#:
#: Standard support, not extended: Ubuntu ESM and RHEL ELS exist and are
#: paid add-ons, and a business that has not bought one is not covered by
#: it. Reporting the ESM date would tell someone they are fine when their
#: machine stopped receiving patches three years ago.
EOL: dict[tuple[str, str], str] = {
    ("ubuntu", "16.04"): "2021-04-30",
    ("ubuntu", "18.04"): "2023-05-31",
    ("ubuntu", "20.04"): "2025-05-31",
    ("ubuntu", "22.04"): "2027-06-01",
    ("ubuntu", "24.04"): "2029-05-31",
    ("debian", "9"): "2020-07-06",
    ("debian", "10"): "2022-09-10",
    ("debian", "11"): "2024-08-14",
    ("debian", "12"): "2026-06-10",
    ("debian", "13"): "2028-06-30",
    ("centos", "7"): "2024-06-30",
    ("centos", "8"): "2021-12-31",
    ("rhel", "7"): "2024-06-30",
    ("rhel", "8"): "2029-05-31",
    ("rhel", "9"): "2032-05-31",
    ("alpine", "3.18"): "2025-05-09",
    ("alpine", "3.19"): "2025-11-01",
    ("alpine", "3.20"): "2026-04-01",
}

_DISTRO_ALIASES = {
    "ubuntu": "ubuntu",
    "debian": "debian",
    "debian gnu/linux": "debian",
    "centos": "centos",
    "centos linux": "centos",
    "centos stream": "centos",
    "red hat enterprise linux": "rhel",
    "rhel": "rhel",
    "rocky linux": "rhel",
    "almalinux": "rhel",
    "alpine linux": "alpine",
    "alpine": "alpine",
}


def parse(os_string: str) -> tuple[str, str] | None:
    """`"Ubuntu 22.04.3 LTS"` -> `("ubuntu", "22.04")`, or None.

    The sensor reports `/etc/os-release`'s PRETTY_NAME, so the shapes are
    the vendors' own: "Ubuntu 22.04.3 LTS", "Debian GNU/Linux 12
    (bookworm)", "Rocky Linux 9.3 (Blue Onyx)".

    Ubuntu is keyed on `major.minor` because that IS its release identity --
    20.04 and 20.10 are different releases with support windows years
    apart, so truncating to "20" would answer the wrong question. Everything
    else keys on the major alone.
    """
    text = (os_string or "").strip().lower()
    if not text:
        return None
    for alias in sorted(_DISTRO_ALIASES, key=len, reverse=True):
        if text.startswith(alias):
            distro = _DISTRO_ALIASES[alias]
            rest = text[len(alias):]
            if distro in ("ubuntu", "alpine"):
                m = re.search(r"(\d+\.\d+)", rest)
            else:
                m = re.search(r"(\d+)", rest)
            return (distro, m.group(1)) if m else None
    return None


def state_of(os_string: str, today: dt.date | None = None) -> dict:
    """What Vault can say about one host's operating system.

    `state` is `supported`, `ending_soon`, `past_eol` or `unknown`. Unknown
    is returned for anything the table has not heard of, and carries
    `as_of` so a reader can see how old the table is rather than assuming
    the silence means safety.
    """
    today = today or dt.date.today()
    parsed = parse(os_string)
    out = {"os": (os_string or "").strip(), "as_of": AS_OF,
           "distro": None, "version": None, "eol": None, "days_left": None,
           "state": "unknown"}
    if parsed is None:
        return out
    distro, version = parsed
    out["distro"], out["version"] = distro, version
    when = EOL.get((distro, version))
    if not when:
        return out
    eol_date = dt.date.fromisoformat(when)
    left = (eol_date - today).days
    out["eol"] = when
    out["days_left"] = left
    out["state"] = ("past_eol" if left < 0
                    else "ending_soon" if left <= ENDING_SOON_DAYS
                    else "supported")
    return out


def survey(hosts: list[dict], today: dt.date | None = None) -> dict:
    """Every host's support state, plus the counts a report prints.

    `hosts` are `Host.last`-shaped dicts. A host with no `os` recorded --
    one Vault has never reached, or one whose collector predates the
    field -- lands in `unknown` rather than being dropped. Dropping it
    would shrink the denominator and quietly improve the headline.
    """
    rows = []
    for host in hosts or []:
        row = state_of((host or {}).get("os") or "", today=today)
        row["host"] = (host or {}).get("name") or (host or {}).get("hostname") or ""
        rows.append(row)
    counts = {"supported": 0, "ending_soon": 0, "past_eol": 0, "unknown": 0}
    for row in rows:
        counts[row["state"]] += 1
    return {"as_of": AS_OF, "hosts": rows, "counts": counts,
            "total": len(rows)}
