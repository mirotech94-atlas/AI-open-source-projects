"""One-time, operator-run: make the Twilio Content API quick-reply template that
gives an ATLAS approval card its Approve / Decline buttons, and print its SID.

    TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... python tools/wa_card_template.py

Idempotent: a template with the same friendly name is found and reused, so running
it twice makes one. Put the printed `HX...` value in WHATSAPP_CONTENT_SID_CARD.

This is a setup script a person runs, NOT part of the running system: ATLAS itself
never creates templates (CLAUDE.md invariant 1, the executor is the only writer).
Nothing here is sent to a phone. The template is variable 1 = the card text,
2 = card id, 3 = the card's one-time code; the buttons' payloads are
`approve:{{2}}:{{3}}` and `decline:{{2}}:{{3}}`, which `kernel/wa_approvals.py`
parses on the inbound webhook.

If WhatsApp or the sandbox refuses the template, nothing is lost: the card falls
back to plain "Reply YES <code> or NO <code>" text.
"""
from __future__ import annotations

import os
import re
import sys

import httpx

CONTENT_API = "https://content.twilio.com/v1/Content"
FRIENDLY_NAME = "atlas_card_buttons_v1"
SID = re.compile(r"^HX[0-9a-f]{32}$")


def template_body() -> dict:
    return {
        "friendly_name": FRIENDLY_NAME,
        "language": "en",
        "variables": {"1": "ATLAS card", "2": "1", "3": "ABCDE"},
        "types": {"twilio/quick-reply": {
            "body": "{{1}}",
            "actions": [
                {"title": "✅ Approve", "id": "approve:{{2}}:{{3}}"},
                {"title": "❌ Decline", "id": "decline:{{2}}:{{3}}"},
            ]}},
    }


def find_existing(client: httpx.Client) -> str | None:
    """The SID of a template already called FRIENDLY_NAME, following pages."""
    url: str | None = f"{CONTENT_API}?PageSize=100"
    for _ in range(20):
        if url is None:
            return None
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
        for item in data.get("contents", []):
            if item.get("friendly_name") == FRIENDLY_NAME and SID.match(
                    str(item.get("sid", ""))):
                return str(item["sid"])
        url = (data.get("meta") or {}).get("next_page_url")
    return None


def ensure_template(client: httpx.Client) -> tuple[str, bool]:
    """`(sid, created)`: reuse the template if it exists, else create it once."""
    found = find_existing(client)
    if found:
        return found, False
    r = client.post(CONTENT_API, json=template_body())
    r.raise_for_status()
    sid = str(r.json().get("sid", ""))
    if not SID.match(sid):
        raise RuntimeError("Twilio did not return a Content SID")
    return sid, True


def main() -> int:
    account = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not account or not token:
        print("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN first.", file=sys.stderr)
        return 2
    with httpx.Client(auth=(account, token), timeout=20) as client:
        try:
            sid, created = ensure_template(client)
        except httpx.HTTPStatusError as exc:
            print(f"Twilio refused: HTTP {exc.response.status_code}", file=sys.stderr)
            return 1
    print(f"{'created' if created else 'found'} {FRIENDLY_NAME}")
    print(f"WHATSAPP_CONTENT_SID_CARD={sid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
