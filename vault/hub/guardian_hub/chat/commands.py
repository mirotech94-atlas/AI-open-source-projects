"""Short imperatives the assistant acts on, with or without an AI provider.

Miro typed "trust" at an alert about an unverified device and Vault
repeated the advice back at him. The reason is structural: with no AI key
configured the turn falls through to `chat.rules`, which ANSWERS and cannot
act -- every path that does something goes through a model deciding to call
a tool. So the one thing a person is most likely to type at an alert was
the one thing that could not work.

This is the deterministic half. A handful of unambiguous commands are
recognised before any model is consulted, resolved against the tenant's own
rows, and dispatched through the normal tool registry -- so role checks,
tiering, confirmations and the audit trail are exactly the same as if a
model had chosen it. No provider required, and no provider able to bypass
it either.

Deliberately small. Anything ambiguous is left alone and falls through to
the model (or the rules), because a command layer that guesses is worse
than one that does not fire: it acts on the wrong device and nothing on
screen afterwards says what it meant.
"""
from __future__ import annotations

import re

from .. import models as m

#: "trust", "trust it", "trust 192.168.1.203", "mark it trusted".
_TRUST = re.compile(
    r"^\s*(?:please\s+)?(?:mark\s+)?(?:it\s+)?trust(?:ed)?\s*(?:it)?\s*(?P<target>[^\s].*)?$",
    re.I)
#: Recognised so that "distrust it" is never mistaken for "trust it" by the
#: looser pattern above. It is deliberately NOT dispatched here: there is no
#: distrust tool in the registry, and inventing one in a command layer is
#: how a second, quieter way to change a device's state gets built. It falls
#: through to the model, which has `update_device`.
_UNTRUST = re.compile(
    r"^\s*(?:please\s+)?(?:un|dis)trust\b.*$", re.I)


def _unverified(db, tenant) -> list:
    """Devices the unverified alert is actually about: no label, not trusted."""
    return [d for d in db.query(m.Device).filter_by(tenant_id=tenant.id).all()
            if not d.trusted and not (d.label or "").strip()]


def presence_note(d) -> str:
    """What Vault knows about whether this device is answering.

    NOT a live ping. The hub runs on a VPS and cannot reach a 192.168.x
    address at all -- any "I pinged it" here would be invented. What is
    real is the sensor's own ping sweep, which is where `online` and
    `last_seen` come from, so that is what gets quoted and it is labelled
    as what it is.
    """
    # `effective_presence` is the ONE rule for this in the codebase: it
    # keeps the last observation but refuses to present a stale ONLINE as
    # current. Reading `presence` raw here would let a device that answered
    # an hour ago be quoted as answering now, which is precisely the
    # evidence somebody would trust it on.
    from ..payloads._util import effective_presence
    state = effective_presence(d)
    seen = (d.last_seen or "")[:16].replace("T", " ")
    if state == "ONLINE":
        return "It answered Vault's last sweep from the sensor on your network."
    if seen:
        return (f"It did not answer Vault's last sweep from the sensor on your "
                f"network; last seen {seen}.")
    return "It has not answered a sweep from the sensor on your network yet."


def match(db, tenant, text: str):
    """`(tool, args, note)` for a command we are sure about, else None.

    `note` is prepended to the tool's own reply — it is where the evidence
    goes, so a person reading "Trusted aa:bb…" also sees WHY the device was
    reachable enough to be worth trusting.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 120:
        return None

    if _UNTRUST.match(raw):
        return None
    mt = _TRUST.match(raw)
    if mt:
        return _resolve(db, tenant, mt.group("target"), "trust_device")
    return None


def _resolve(db, tenant, target: str | None, tool: str):
    target = (target or "").strip().strip(".").strip()
    # Strip the filler people put round a bare command: "trust that device".
    target = re.sub(r"^(?:that|the|this)\s+(?:device|one|mac|ip)?\s*", "", target, flags=re.I).strip()

    if not target:
        # "trust" on its own. Only safe when there is exactly ONE thing it
        # could mean -- which is the case the alert Miro was reading is
        # about. Two candidates is a question, not a coin flip.
        pending = _unverified(db, tenant)
        if len(pending) != 1:
            return None
        d = pending[0]
        return tool, {"mac": d.mac}, _preface(d, named=False)

    rows = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    needle = target.lower()
    hits = [d for d in rows
            if needle in ((d.mac or "").lower(), (d.ip or "").lower(),
                          (d.label or "").strip().lower(), (d.name or "").strip().lower())]
    if len(hits) != 1:
        return None      # ambiguous or unknown: let the model or rules speak
    return tool, {"mac": hits[0].mac}, _preface(hits[0], named=True)


def _preface(d, *, named: bool) -> str:
    who = d.label or d.name or d.ip or d.mac
    lead = "" if named else f"Taking that to mean {who} — the one device here that is not verified. "
    return f"{lead}{presence_note(d)}"
