"""Overview's camera census row (Task 83, item 4 / pattern A5).

The owner's own framing: identify every camera on the network, say whether
they are all accounted for, and flag any of them reaching the internet by a
path Vault does not expect. The part that matters is honesty about scope
-- his cameras sit behind an NVR's own PoE network, invisible to the LAN
sweep (`recorders.py`'s module docstring), so "how many cameras does
Vault know about" and "how many SHOULD be there" are two different
numbers and the census must show both rather than quietly reporting the
smaller one as the truth.

`known` comes from `security.camera_facts`, itself counted off `Device`
rows through `payloads.assets.kind_of` -- the same count the Assets page and
the per-network chips show, so this row can never disagree with them.
`expected` adds what each registered recorder's own `expected_channels` says
should be behind it but has not (yet, or currently) been listed for: exactly
`expected_channels - channel_count`, which is never negative-double-counted
because every channel a recorder HAS listed already became one of the
`Device` rows `known` counts (see `recorders.ingest`). A recorder with no
`expected_channels` set (the default, "don't check the count") contributes
no gap -- the owner has not told Vault a number to hold it to.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from .. import models as m, recorders as recorders_mod
from ..schemas_dash import CameraCensus, RecorderGap
from .security import camera_facts
from ..scans import parse_ts
from ._util import _now


def census(db: DbSession, tenant: m.Tenant, raw: dict) -> CameraCensus:
    facts = camera_facts(db, tenant, raw)
    gaps: list[RecorderGap] = []
    unaccounted = 0
    for r in recorders_mod.listing(db, tenant.id):
        expected = int(r.get("expected_channels") or 0)
        if not expected:
            continue                       # owner gave no number to check against
        known = int(r.get("channel_count") or 0)
        short = expected - known
        if short <= 0:
            continue
        unaccounted += short
        reason = r.get("problem") or (
            f"{short} of {expected} camera(s) behind it have never been listed")
        gaps.append(RecorderGap(name=r.get("name") or r.get("host") or "recorder",
                                 expected=expected, known=known, reason=reason,
                                 network_id=r.get("network_id") or ""))
    observed = parse_ts(raw.get("ts"))
    exposure = (raw.get("home") or {}).get("exposure") or {}
    exposure_known = bool(observed and -60 <= (_now()-observed).total_seconds() <= 1200 and isinstance(exposure.get("open_ports"),list) and not exposure.get("error"))
    return CameraCensus(
        known=facts["total"], online=facts["online"],
        expected=facts["total"] + unaccounted,
        unobserved=facts["unknown"] + unaccounted, offline=len(facts["quiet"]), exposure_known=exposure_known,
        exposed=bool(facts["exposed_ports"]), exposed_ports=list(facts["exposed_ports"]),
        gaps=gaps,
    )
