"""Google Voice — read-only, by design.

Google Voice has no official API. Every library that claims otherwise
impersonates the web client, breaks without notice, and risks the account.

So ATLAS does not drive Google Voice. Instead Google Voice is configured to
forward texts and voicemail transcripts to Gmail, which ATLAS already reads.
That gives "you missed a call from a 917 number, here is what they said" with
zero fragile automation and nothing that can get the number suspended.

Sending stays on Twilio. This class exists so the ingestion path is explicit
rather than buried in the mail module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

FROM_ADDRESSES = ("txt.voice.google.com", "contact77@example.com")

SMS_RE = re.compile(r"^(?P<body>.*?)\n+YOUR ACCOUNT", re.DOTALL)
NUMBER_RE = re.compile(r"(\+?1?\s*\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})")


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    kind: str                # "sms" | "voicemail" | "missed"
    counterpart: str
    text: str
    at: datetime


def parse(subject: str, body: str, at: datetime) -> VoiceEvent | None:
    """Turn a Google Voice notification email into a structured event."""
    number = NUMBER_RE.search(subject) or NUMBER_RE.search(body)
    counterpart = number.group(1).strip() if number else "unknown"
    low = subject.lower()

    if "new voicemail" in low:
        kind = "voicemail"
    elif "missed call" in low:
        kind = "missed"
    elif "new text" in low or "sms" in low:
        kind = "sms"
    else:
        return None

    match = SMS_RE.search(body)
    text = (match.group("body") if match else body).strip()[:2000]
    return VoiceEvent(kind=kind, counterpart=counterpart, text=text, at=at)


def is_google_voice(sender: str) -> bool:
    return any(a in (sender or "").lower() for a in FROM_ADDRESSES)
