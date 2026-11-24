"""ATLAS's face and card: `/api/self`, `/api/self/atlas.vcf`,
`/api/self/avatar.png`. Read-only, signed-in, mesh-only like the rest of /api.

the owner: "when he calls me the contact photo should show ATLAS or the humanoid."
ATLAS cannot set a caller-ID image — no carrier or Twilio API does that. What
it can do is hand him a contact card carrying its number(s) and its face;
once saved, the iPhone shows that face whenever the number rings.

The face is `ui/public/atlas-avatar.png`, rasterised in pure Python by
`tools/render_avatar.py` next to the SVG it matches, and committed. Nothing
here renders anything: the API reads a file, so there is no image library on
the box to go missing and no fallback for a phone to choke on.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import pathlib
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..kernel import selfmodel
from . import auth

log = logging.getLogger("atlas.api.self")

router = APIRouter(prefix="/api/self")
SignedIn = Annotated[auth.Session, Depends(auth.require)]

_UI = pathlib.Path(__file__).resolve().parents[2] / "ui"
# `ui/public` in a checkout; the image also ships `ui/dist`, which Vite fills
# from `public/` at build time. Either is the same file.
AVATAR_PATHS = (_UI / "public" / "atlas-avatar.png",
                _UI / "dist" / "atlas-avatar.png")

_PNG: list[bytes] = []                                  # read once, cached


async def _read_avatar() -> bytes | None:
    """The committed PNG, read once off the event loop; None if missing."""
    if not _PNG:
        for path in AVATAR_PATHS:
            if await asyncio.to_thread(path.is_file):
                _PNG.append(await asyncio.to_thread(path.read_bytes))
                break
        else:
            return None
    return _PNG[0]


async def avatar() -> bytes:
    """The committed PNG, or a 404 that says how to make it."""
    png = await _read_avatar()
    if png is None:
        raise HTTPException(
            404, "atlas-avatar.png is missing from ui/public and ui/dist; "
                 "run tools/render_avatar.py")
    return png


def _fold(line: str) -> str:
    """RFC 2426 line folding: 75 octets, continuation lines start with a
    space. ASCII only here, so characters are octets."""
    if len(line) <= 75:
        return line
    out = [line[:75]]
    rest = line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


def vcard(photo: bytes) -> str:
    """ATLAS as a contact: its calling number, the screening line when there
    is one, and its face."""
    lines = ["BEGIN:VCARD", "VERSION:3.0", "N:;ATLAS;;;", "FN:ATLAS",
             "ORG:ATLAS"]
    main = os.getenv("TWILIO_FROM", "").strip()
    screening = os.getenv("SCREENING_FROM", "").strip()
    if main:
        lines.append(f"TEL;TYPE=CELL,VOICE,pref:{main}")
    if screening and screening != main:
        lines.append(f"TEL;TYPE=WORK,VOICE:{screening}")
    if not main:
        lines.append("NOTE:TWILIO_FROM is not set on the server\\, so this "
                     "card carries no number yet.")
    lines.append("PHOTO;ENCODING=b;TYPE=PNG:"
                 + base64.b64encode(photo).decode("ascii"))
    lines.append("END:VCARD")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


@router.get("")
async def self_status(_s: SignedIn) -> dict[str, Any]:
    """The self-model, plus where its face and contact card live."""
    status = await selfmodel.status()
    # A missing face is reported, never allowed to hide the status itself.
    face: Any = "missing"
    if await _read_avatar() is not None:
        face = {"png": True, "url": "/api/self/avatar.png",
                "vcard": "/api/self/atlas.vcf"}
    return {"as_of": datetime.now(UTC).isoformat(), **status, "avatar": face}


@router.get("/avatar.png")
async def avatar_png(_s: SignedIn) -> Response:
    return Response(await avatar(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/atlas.vcf")
async def atlas_vcf(_s: SignedIn) -> Response:
    return Response(
        vcard(await avatar()), media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ATLAS.vcf"'})
