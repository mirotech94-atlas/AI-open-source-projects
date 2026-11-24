"""Client for the WhatsApp linked-device bridge (`whatsapp-bridge/`, compose service
`wa-bridge`). A dumb transport: this module only asks it to carry text to the owner.

Who calls it: `contact._send_wa`, from executor actions only (invariant 1). The
bridge itself refuses any number but WHATSAPP_OWNER and caps sends per hour; this
side repeats the owner check so a bug here cannot even ask.

Unofficial by nature (a linked device, not the Business API): see
docs/whatsapp-link.md for the ban risk and what keeps it low.

Honesty (invariant 7): `status()` says `reachable`, `connected` and why. A bridge
that cannot be asked, or is not connected, or is linked to the wrong phone is never
reported as working. The shared secret is read from the environment, sent only as a
header, and appears in no log line, return value or exception text.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("atlas.agency.wa_bridge")

URL_ENV = "WA_BRIDGE_URL"
SECRET_ENV = "WA_BRIDGE_SECRET"
DEFAULT_URL = "http://wa-bridge:8090"
SID_PREFIX = "wa:"                      # whatsapp_sid values from the bridge start with this
TIMEOUT = 30.0                          # a send waits for the bridge's human-like delay


@dataclass(frozen=True)
class BridgeStatus:
    configured: bool
    reachable: bool
    connected: bool
    owner_match: bool
    number: str = ""
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.reachable and self.connected and self.owner_match


def configured() -> bool:
    return bool(os.getenv(SECRET_ENV, "").strip())


def _base() -> str:
    return (os.getenv(URL_ENV) or DEFAULT_URL).rstrip("/")


def _headers() -> dict[str, str]:
    return {"X-Bridge-Secret": os.getenv(SECRET_ENV, "").strip()}


async def status() -> BridgeStatus:
    if not configured():
        return BridgeStatus(False, False, False, False, reason="no bridge configured")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{_base()}/status", headers=_headers())
    except Exception as exc:  # noqa: BLE001
        return BridgeStatus(True, False, False, False,
                            reason=f"the WhatsApp bridge is unreachable ({type(exc).__name__})")
    if r.status_code in (401, 403):
        return BridgeStatus(True, True, False, False,
                            reason="the WhatsApp bridge refused the shared secret")
    if r.status_code != 200:
        return BridgeStatus(True, False, False, False,
                            reason=f"the WhatsApp bridge answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        return BridgeStatus(True, False, False, False, reason="the bridge sent unreadable status")
    connected = data.get("connected") is True
    match = data.get("owner_match") is True
    reason = ""
    if not connected:
        reason = "WhatsApp is not linked or not connected (scan the QR on the pairing page)"
    elif not match:
        reason = "the linked WhatsApp account is not the owner number"
    return BridgeStatus(True, True, connected, match, str(data.get("number") or ""), reason)


async def send(to: str, text: str) -> tuple[bool, str]:
    """(True, "wa:<message id>") or (False, reason). `to` must be the owner."""
    from . import contact
    if _digits(to) != _digits(contact.owner_whatsapp()):
        return False, "refused: the bridge only sends to the owner number"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{_base()}/send", headers=_headers(),
                                  json={"to": to, "text": text})
    except Exception as exc:  # noqa: BLE001
        return False, f"the WhatsApp bridge is unreachable ({type(exc).__name__})"
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 200 and data.get("ok") and data.get("id"):
        return True, SID_PREFIX + str(data["id"])
    return False, f"the WhatsApp bridge refused: {str(data.get('error') or r.status_code)[:120]}"


async def message_status(sid: str) -> str:
    """The bridge's ack word for a sent message (pending, server_ack, delivered,
    read, error) or "unknown"."""
    if not sid.startswith(SID_PREFIX):
        return "unknown"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{_base()}/message/{sid[len(SID_PREFIX):]}",
                                 headers=_headers())
        return str(r.json().get("status") or "unknown") if r.status_code == 200 else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())
