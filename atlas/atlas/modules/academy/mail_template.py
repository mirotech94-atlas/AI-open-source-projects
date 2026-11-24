"""ATLAS Academy's branded HTML email (Miro, 2026-11-18).

Every mail the store sends (signup confirmation, access link, contact receipt,
support reply) keeps its plain-text body as the text part and gains this HTML
part: black #0A0D14 page, royal blue #5B8CFF accents, the ATLAS mark on top, the
contact line at the bottom. Pure: text in, HTML string out, nothing is sent here.

The text is untrusted-adjacent (a visitor's name can sit in a greeting), so it is
HTML-escaped first; only a line that is a bare https link becomes a button.
"""
from __future__ import annotations

import os
import re
from html import escape

from .. import store_origin

BG, CARD, EDGE = "#0A0D14", "#121826", "#222B3F"
INK, MUTED, BLUE = "#E8ECF5", "#98A2B8", "#5B8CFF"
LOGO = f"{store_origin.DEFAULT}/academy/assets/icon-192.png"
_URL_LINE = re.compile(r"^(https://\S+)$")
_URL = re.compile(r"(https://[^\s<>\"']+)")
FONT = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"


def _para(block: str) -> str:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if len(lines) == 1 and _URL_LINE.match(lines[0]):
        url = escape(lines[0], quote=True)
        label = "Open" if "confirm" not in lines[0] else "Confirm my email"
        if "/access" in lines[0] or "downloads" in lines[0]:
            label = "Open my downloads"
        return (f'<p style="margin:22px 0"><a href="{url}" style="background:{BLUE};'
                f'color:#0A0D14;text-decoration:none;font-weight:700;padding:13px 22px;'
                f'border-radius:10px;display:inline-block">{label}</a></p>'
                f'<p style="margin:0 0 18px;font-size:12px;color:{MUTED};word-break:break-all">'
                f'{url}</p>')
    body = "<br>".join(_linkify(ln) for ln in lines)
    return f'<p style="margin:0 0 16px;line-height:1.6">{body}</p>'


def _linkify(line: str) -> str:
    out, last = [], 0
    for m in _URL.finditer(line):
        out.append(escape(line[last:m.start()]))
        u = m.group(1)
        out.append(f'<a href="{escape(u, quote=True)}" style="color:{BLUE}">{escape(u)}</a>')
        last = m.end()
    out.append(escape(line[last:]))
    return "".join(out)


def render(subject: str, text: str) -> str:
    """The branded HTML for a plain-text mail body."""
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    body = "".join(_para(b) for b in blocks)
    postal = os.getenv("AGENCY_POSTAL_ADDRESS", "").strip()
    foot = " · ".join(x for x in (store_origin.CONTACT_MAIL,
                                  store_origin.CONTACT_PHONE_DISPLAY, postal) if x)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        f'<title>{escape(subject)}</title></head>'
        f'<body style="margin:0;padding:0;background:{BG};color:{INK};font-family:{FONT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{BG}"><tr><td align="center" style="padding:28px 14px">'
        f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;width:100%;background:{CARD};border:1px solid {EDGE};'
        f'border-radius:16px;overflow:hidden">'
        f'<tr><td style="padding:22px 28px;border-bottom:2px solid {BLUE}">'
        f'<img src="{escape(LOGO, quote=True)}" width="32" height="32" alt="" '
        f'style="vertical-align:middle;border-radius:8px;margin-right:10px">'
        f'<span style="font-size:18px;font-weight:800;letter-spacing:.02em;color:{INK};'
        f'vertical-align:middle">ATLAS <span style="color:{BLUE}">Academy</span></span>'
        f'</td></tr>'
        f'<tr><td style="padding:28px;font-size:16px;color:{INK}">{body}</td></tr>'
        f'<tr><td style="padding:18px 28px;border-top:1px solid {EDGE};font-size:12px;'
        f'color:{MUTED};line-height:1.5">ATLAS Academy · {escape(foot)}</td></tr>'
        '</table></td></tr></table></body></html>')
