"""Colours, page sizes and the seven products of the Academy PDF template.

Miro, 2026-11-14: every PDF in a dark (screen) and a white (print) version.
The store look is black + royal blue (tools/academy_covers.py values); the
white version keeps the royal blue as its accent on white paper and keeps a
dark cover. WCAG AA is checked in tests/test_academy_pdf_template.py.
"""
from __future__ import annotations

MODES = {
    "dark": {
        "bg": "#0A0D14", "surface": "#0F1420", "raised": "#131A28", "lift": "#1B2334",
        "line": "#2B3650", "text": "#EEF2FA", "text2": "#C9D2E3", "muted": "#8E99AE",
        "accent": "#5B8CFF", "accent_text": "#8FB0FF", "on_accent": "#070A12",
        "warn": "#FFB454", "code_bg": "#0F1420",
    },
    "print": {
        "bg": "#FFFFFF", "surface": "#F3F6FC", "raised": "#E9EFFB", "lift": "#DCE5F8",
        "line": "#BFCBE3", "text": "#0B1220", "text2": "#26324A", "muted": "#4D5A75",
        "accent": "#1F4FD8", "accent_text": "#1A44B8", "on_accent": "#FFFFFF",
        "warn": "#8A4B00", "code_bg": "#F3F6FC",
    },
}
# The cover is dark in both modes (the store's black and royal blue).
COVER = {"bg": "#0A0D14", "text": "#EEF2FA", "text2": "#C9D2E3", "accent": "#5B8CFF",
         "accent_text": "#8FB0FF", "on_accent": "#070A12"}

# width x height in mm
PAGE_MM = {"A4": (210, 297), "A5": (148, 210), "Letter": (215.9, 279.4)}

CALLOUT_LABELS = ("Rule", "Try this", "Checkpoint", "Warning", "Example", "Takeaway")


def _lin(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def contrast_pairs(mode: str) -> list[tuple[str, str, str, float]]:
    """(name, foreground, background, minimum ratio) for every text pairing used."""
    m = MODES[mode]
    pairs = [
        ("text on page", m["text"], m["bg"], 7.0),
        ("secondary on page", m["text2"], m["bg"], 4.5),
        ("muted on page", m["muted"], m["bg"], 4.5),
        ("accent text on page", m["accent_text"], m["bg"], 4.5),
        ("text on surface", m["text"], m["surface"], 7.0),
        ("muted on surface", m["muted"], m["surface"], 4.5),
        ("accent text on surface", m["accent_text"], m["surface"], 4.5),
        ("text on raised", m["text"], m["raised"], 7.0),
        ("accent text on raised", m["accent_text"], m["raised"], 4.5),
        ("label on accent", m["on_accent"], m["accent"], 4.5),
        ("warning label on surface", m["warn"], m["surface"], 4.5),
    ]
    pairs += [("cover text", COVER["text"], COVER["bg"], 7.0),
              ("cover accent text", COVER["accent_text"], COVER["bg"], 4.5),
              ("cover tag", COVER["on_accent"], COVER["accent"], 4.5)]
    return pairs


# Page size per product (catalog inclusions.detail and the file today, pdfinfo
# 2026-11-14); body size in pt. `file` is today's file stem.
PRODUCTS: dict[str, dict] = {
    "safe-ai-operator": {
        "file": "build-a-safe-ai-operator", "title": "Build a Safe AI Operator",
        "series": "ATLAS Academy · written course",
        "promise": "Build your own AI operator: an agent that watches your business and does real "
                   "work, without it ever being able to burn you.",
        "foot": "28 lessons plus a bonus. Starter code is in the download.",
        "page": "A4", "body_pt": 10.5, "author": "by Miro",
    },
    "30-business-lessons": {
        "file": "30-business-lessons", "title": "30 Business Lessons",
        "series": "A practical guide",
        "promise": "Starting a small AI-assisted service business, one honest step at a time.",
        "foot": "Educational content, not an income guarantee. Examples and figures are "
                "labeled hypotheticals.",
        "page": "A5", "body_pt": 10, "author": "By Miro (Myron Mekhael)",
    },
    "everyday-ai-for-adults": {
        "file": "everyday-ai-for-adults", "title": "Everyday AI for Adults",
        "series": "AI for Every Age",
        "promise": "Learn to question AI, use it safely for everyday tasks, and build your first "
                   "useful automation.",
        "foot": "Sold to adults 18+. The Claude lessons are for adults; this course never asks a "
                "child to use Claude.",
        "page": "Letter", "body_pt": 10.5,
    },
    "safe-and-simple-ai-60plus": {
        "file": "safe-and-simple-ai-60plus", "title": "Safe and Simple AI 60+",
        "series": "AI for Every Age · Large print",
        "promise": "Stay safe from scams, set up Claude on your phone, and use it for everyday "
                   "letters, lists and questions. One step at a time.",
        "foot": "Sold to adults 18+. Written for adults 60 and over.",
        "page": "Letter", "body_pt": 17,
    },
    "family-ai-scam-defence-workbook": {
        "file": "family-ai-scam-defence-workbook",
        "title": "The Family AI-Scam Defense Workbook",
        "series": "ATLAS Academy · printable workbook",
        "promise": "A household plan you make in one sitting: a family safe word, a callback rule, "
                   "a never-paste list, practice drills, and the pages you keep by the phone.",
        "foot": "For adults 18+. United States reporting details, checked 5 Oct 2026. General "
                "information, not legal or financial advice.",
        "page": "Letter", "body_pt": 12.5,
    },
    "sell-with-ai": {
        "file": "sell-with-ai", "title": "Sell With AI",
        "series": "ATLAS Academy",
        "promise": "Clips, digital products and an online store: three ways one person sells with "
                   "AI doing the repetitive work.",
        "foot": "Educational content, not an income guarantee.",
        "page": "Letter", "body_pt": 10.5, "author": "Miro (Myron Mekhael)",
    },
    "local-business-automation-pack": {
        "file": "local-business-automation-pack", "title": "Local Business Automation Pack",
        "series": "ATLAS Academy · n8n workflow pack",
        "promise": "Four n8n automations that draft, never send: site health, lead replies, "
                   "missed calls, review requests.",
        "foot": "Use made-up data and a separate test account while you learn.",
        "page": "Letter", "body_pt": 10.5,
    },
}
