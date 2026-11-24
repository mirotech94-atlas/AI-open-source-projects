"""Concept Shorts: the growth plan's idea bank, explained with motion.

the owner, 2026-10-12 (Q&A on the TikTok + YouTube growth plan): "Concept
Shorts from the 30-idea bank". The lesson Shorts only reach the courses
that have video lessons; the plan's first audience -- local business
owners -- is served by the Automation Pack, which has none. These Shorts
are how ATLAS reaches them without filming a screen it has not got.

What one is: an idea from `concept_scripts.json` -- a hook from the
plan, two or three lines that each say only what one quoted phrase of the
product's catalog entry says (`evidence`, checked by a test), and one
close naming the product and the store -- narrated in the ATLAS voice, in
the brand, with one animated diagram labelled "Concept animation"
(reel_design.concept_panel). No first-person experience (the voice is
synthetic), no screen that pretends to be the product, no send animated
for a flow that only drafts.

Concept Shorts post only when `ACADEMY_CONCEPT_SHORTS` is on: the owner sees
the previews first. When on, the first slot of the day is a concept Short
and the second a lesson Short (reel.daily).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .lesson_short import HASHTAGS_TIKTOK, HASHTAGS_YOUTUBE, STORE, Beat, _guard

SCRIPTS = Path(__file__).with_name("concept_scripts.json")
ENABLE_ENV = "ACADEMY_CONCEPT_SHORTS"
MODEL_ENV = "ACADEMY_SHORTS_FISH_MODEL"
MODEL_DEFAULT = "s2.1-pro"
# the owner, 2026-10-12: "the voice should have feelings and laughter and make
# jokes and have humor when needed, don't make him sound like a robot",
# "make it like a human talking, not an AI". The scripts carry Fish S2
# delivery cues ([excited], [chuckling], [break] ...): sent to the voice,
# never shown (tutorial_series.plain strips them from captions and checks).
# A phrase that reads as machine-written never reaches a script.
AI_PHRASES = ("delve", "seamless", "let's dive in", "game-changer", "game changer",
              "unlock", "elevate", "in today's fast-paced", "revolutionize",
              "supercharge", "harness the power")


def fish_model() -> str:
    """The concept Shorts' Fish model (ACADEMY_SHORTS_FISH_MODEL), default
    s2.1-pro, which reads the cues. The lesson Shorts keep their lessons'
    own recorded voice."""
    return os.getenv(MODEL_ENV, "").strip() or MODEL_DEFAULT


@dataclass(frozen=True)
class Concept:
    id: str
    plan: str
    product: str
    title: str
    motion: str
    beats: tuple[dict, ...]
    close: str
    files: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"concept:{self.id}"


def enabled() -> bool:
    """Off until the owner has seen the previews and says so."""
    return os.getenv(ENABLE_ENV, "off").strip().lower() in ("1", "on", "true", "yes")


def concepts(path: Path | None = None) -> list[Concept]:
    raw = json.loads((path or SCRIPTS).read_text(encoding="utf-8"))
    return [Concept(c["id"], c["plan"], c["product"], c["title"], c["motion"],
                    tuple(c["beats"]), c["close"], tuple(c.get("files") or ()))
            for c in raw["concepts"]]


def find(key: str) -> Concept | None:
    return next((c for c in concepts() if c.key == key), None)


def evidence_refusal(c: Concept, catalog_text: str) -> str | None:
    """Why this concept says something its product's catalog does not, or
    None. Every evidenced line's phrase must be in the product's entry."""
    for b in c.beats:
        ev = b.get("evidence")
        if ev and ev not in catalog_text:
            return f"{c.id}: {ev!r} is not in the {c.product} catalog entry"
    from .tutorial_series import plain
    said = plain(" ".join([b["say"] for b in c.beats] + [c.close]))
    if not _guard(said):
        return f"{c.id}: the narration fails the brand or income guard"
    if hit := next((w for w in AI_PHRASES if w in said.lower()), None):
        return f"{c.id}: {hit!r} reads as machine-written"
    return None


def product_text(slug: str) -> str:
    """The product's catalog entry as one string, for the evidence check."""
    from .catalog import CATALOG_PATH
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entry = next(p for p in raw["products"] if p["slug"] == slug)
    return json.dumps(entry, ensure_ascii=False).replace('\\"', '"')


def build(c: Concept) -> tuple[list[Beat], str, str]:
    """(beats, TikTok caption, YouTube caption). Beats carry no audio path:
    each is said by the Fish voice at render."""
    from ..content.drafts import AI_LABEL
    from .catalog import load
    product = load().product(c.product)
    name = product.title if product else c.product
    beats = [Beat(b.get("kind", "line"), b["say"], "", heading=b.get("label", ""))
             for b in c.beats]
    beats.append(Beat("cta", c.close, ""))
    page = f"{STORE}/{c.product}/"
    caption = (f"{c.title}\n{name}: the files and the guide are at {page}\n"
               f"{HASHTAGS_TIKTOK}\n\n{AI_LABEL}")
    youtube = (f"{c.title}\nA concept animation of how {name} works. "
               f"The whole thing: https://{page}\n{HASHTAGS_YOUTUBE}\n\n{AI_LABEL}")
    return beats, caption, youtube


def next_concept(shown: dict) -> str | None:
    """The concept shown longest ago, never-shown first, in file order --
    but never the same product as the last concept shown while another
    product has one waiting. Five of the seven are the Automation Pack, and
    TikTok flagged two of them in a row as spam (2026-10-12 and 10-13)."""
    from datetime import UTC, datetime
    order = concepts()
    if not order:
        return None
    floor = datetime.min.replace(tzinfo=UTC)
    seen = [(shown[c.key], c.product) for c in order if c.key in shown]
    last = max(seen)[1] if seen else None
    pool = [e for e in enumerate(order) if e[1].product != last] or list(enumerate(order))
    return min(pool, key=lambda e: (shown.get(e[1].key, floor), e[0]))[1].key
