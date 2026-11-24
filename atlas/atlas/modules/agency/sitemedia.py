"""agency.sitemedia -- the self-hosted imagery on every designed client site.

**Why (2026-09-30).** the owner, on the desktop review of the templates: "very
poor design -- make sure it's a frontend marketing website for each one".
The page was type on a white card. A local business's site needs a scene:
a photograph of the trade, a moving background behind the hero, imagery in
the cards. This module says which files each page gets and how they are
written into the markup; nothing here is chosen at run time by a model.

**Where the files come from (`templates/media/`, recorded in
`templates/getlayers.json`).**

* Two GetLayers Backgrounds, materialized at design time with the
  committed Style (`ridgeline-style`): `lilac-silk-waves` behind the beauty
  hero and `sage-room-sunbeam` behind the food hero. Each is a short muted
  H.264 loop (re-encoded from the 4K master to ~1 MB or less) plus its
  poster in AVIF and WebP at two widths. The trades hero (v4, 2026-10-01,
  from the Lovable reference) is the photograph itself, full bleed under a
  CSS gradient, so its loop (`blue-contour-lines`) was retired: a trade's
  page downloads one image and nothing else above the fold.
* Unsplash photographs, under the Unsplash License, downloaded and
  re-encoded here (AVIF + WebP, two widths each): generic scenes of the
  trade -- a panel being tested, a tap being fixed, a salon chair, a set
  table. Every one is captioned as an illustrative photograph in its `alt`
  and is never presented as the business's own premises, van, team or work.

**How they load.** The hero photo is the one eager image (`fetchpriority=
high`); everything below the fold is `loading="lazy"`. The video carries no
`<source>` in the markup: a small inline script attaches it only on a wide
screen that does not ask for reduced motion, so a phone downloads the
poster and nothing else. Every URL is relative to the page (nothing is
hotlinked, no third-party host), and `copy_assets` writes only the files
the page references beside it.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from html import escape
from pathlib import Path

TEMPLATES_DIR = Path(__file__).with_name("templates")
MEDIA_DIR = "media"
FONT_FILE = "fonts/archivo-var.woff2"
FONT_FILES = (FONT_FILE, "fonts/OFL.txt")


@dataclass(frozen=True, slots=True)
class Photo:
    """One self-hosted photograph: its file stem, the widths it was
    encoded at (largest first) and its aspect ratio, and an honest
    description of what it shows."""
    stem: str
    widths: tuple[int, ...]
    ratio: str
    alt: str
    source: str        # where it came from, for the record

    def files(self) -> list[str]:
        return [f"{MEDIA_DIR}/{self.stem}-{w}.{ext}" for w in self.widths
                for ext in ("avif", "webp")]


def _unsplash(stem: str, widths: tuple[int, ...], ratio: str, what: str,
              photo_id: str, author: str) -> Photo:
    return Photo(f"photo-{stem}", widths, ratio, f"Illustrative photo: {what}.",
                 f"Unsplash {photo_id} by {author} (Unsplash License)")


_HERO = (1600, 800)
_CARD = (960, 520)
PHOTOS: dict[str, Photo] = {
    "electric": _unsplash("electric", _HERO, "4 / 3",
                          "an electrical panel being tested with a multimeter",
                          "PkHf7BUWbtk", "Toolmash Expo"),
    "electric-2": _unsplash("electric-2", _CARD, "4 / 3",
                            "a breaker panel on a workbench, wired and labeled",
                            "kXC6XhVL1e4", "Troy Bridges"),
    "plumbing": _unsplash("plumbing", _HERO, "4 / 3",
                          "a plumber working on the pipes under a sink",
                          "kxuz4YrLxSc", "Timur Shakerzianov"),
    "plumbing-2": _unsplash("plumbing-2", _CARD, "16 / 9",
                            "pliers and a shower head on a towel",
                            "Rmu0QRyZFkY", "Tekton"),
    "tools": _unsplash("tools", _HERO, "3 / 2",
                       "hand tools laid out on a wooden bench",
                       "Rf9eElW3Qxo", "Louis Hansel"),
    "salon": _unsplash("salon", (1400, 700), "3 / 2",
                       "a salon chair by a bright window",
                       "T9u-Xr30FY8", "Giorgio Trovato"),
    "salon-2": _unsplash("salon-2", _CARD, "3 / 2",
                         "hair being trimmed with scissors",
                         "iAMGDmQ6bUY", "mostafa meraji"),
    "barber": _unsplash("barber", _HERO, "3 / 2",
                        "barber chairs in front of a brick wall",
                        "EW_rqoSdDes", "Nathon Oski"),
    "barber-2": _unsplash("barber-2", _CARD, "3 / 2",
                          "combs and scissors on a folded towel",
                          "pu20JkUx--A", "Arthur Humeau"),
    "nails": _unsplash("nails", _HERO, "3 / 2",
                       "nail polish being applied",
                       "IYa5Dnj9qWE", "Stefan Lehner"),
    "nails-2": _unsplash("nails-2", _CARD, "4 / 3",
                         "bottles of nail polish in a row",
                         "sp6wzdYJ7QQ", "Maria Lupan"),
    "restaurant": _unsplash("restaurant", _HERO, "3 / 2",
                            "set tables in a restaurant",
                            "toeFdq0WHQk", "Vitalii Kyktov"),
    "restaurant-2": _unsplash("restaurant-2", _CARD, "3 / 2",
                              "glasses and folded napkins on a table",
                              "104bM1xNAZA", "Jordan Mills"),
    "cafe": _unsplash("cafe", _HERO, "3 / 2",
                      "coffee being poured behind a counter",
                      "gYsjG6_cTL4", "Yasamine June"),
    "bakery": _unsplash("bakery", _HERO, "16 / 9",
                        "loaves of bread on a counter",
                        "HJ3tXZpY1Qw", "DDP"),
    "florist": _unsplash("florist", _HERO, "3 / 2",
                         "bouquets on display in a flower shop",
                         "sz7NTRGFDHA", "Sonia Fotograf"),
    "cleaners": _unsplash("cleaners", _HERO, "3 / 2",
                          "pressed shirts on hangers",
                          "cue0DuZ8cUU", "Waldemar Brandt"),
}

# (category pattern, hero photo, card photo or None), first match wins
_BY_CATEGORY: tuple[tuple[str, str, str | None], ...] = (
    (r"electric", "electric", "electric-2"),
    (r"plumb|heating|hvac|air condition|boiler|drain", "plumbing", "plumbing-2"),
    (r"barber", "barber", "barber-2"),
    (r"nail", "nails", "nails-2"),
    (r"hair|salon|beauty|stylist|lash|brow|wax|make-?up|esthetic|aesthetic|massage",
     "salon", "salon-2"),
    (r"bak|cake|patisserie|dessert|bagel", "bakery", None),
    (r"florist|flower", "florist", None),
    (r"dry ?clean|cleaners|laundr|tailor|alteration", "cleaners", None),
    (r"cafe|café|coffee|\btea\b|juice", "cafe", None),
    ((r"restaurant|bistro|diner|eatery|kitchen|pizz|\bbar\b|\bpub\b|deli\b|grill|"
      r"sushi|ramen|noodle|steak|taqueria|cater|food|meal"), "restaurant", "restaurant-2"),
)
_FAMILY_FALLBACK = {"trades": ("tools", None), "beauty": (None, None),
                    "food": (None, None)}


def photos_for(category: str | None, family: str) -> tuple[Photo | None, Photo | None]:
    """(hero photo, card photo) for a business: by its category, else the
    family's fallback (a bench of tools for any trade; nothing for a
    salon or shop the catalogue has no honest picture for)."""
    text = (category or "").replace("_", " ")
    for pattern, hero, card in _BY_CATEGORY:
        if re.search(pattern, text, re.IGNORECASE):
            return PHOTOS[hero], PHOTOS[card] if card else None
    hero, card = _FAMILY_FALLBACK.get(family, (None, None))
    return (PHOTOS[hero] if hero else None), (PHOTOS[card] if card else None)


@dataclass(frozen=True, slots=True)
class Background:
    """A GetLayers Background: the id it was materialized from, its loop
    and its poster, plus how it is dimmed under the copy."""
    family: str
    getlayers_id: str
    preview: str

    @property
    def video(self) -> str:
        return f"{MEDIA_DIR}/bg-{self.family}.mp4"

    def poster(self) -> Photo:
        return Photo(f"bg-{self.family}", (1600, 800), "4 / 3", "", self.getlayers_id)

    def files(self) -> list[str]:
        return [self.video, *self.poster().files()]


BACKGROUNDS: dict[str, Background] = {
    "beauty": Background("beauty", "lilac-silk-waves",
                         "https://www.getlayers.ai/?layer=lilac-silk-waves"),
    "food": Background("food", "sage-room-sunbeam",
                       "https://www.getlayers.ai/?layer=sage-room-sunbeam"),
}


# ------------------------------------------------------------------ markup

def picture(photo: Photo, *, prefix: str = "", sizes: str, eager: bool = False,
            cls: str = "", decorative: bool = False) -> str:
    """A `<picture>` with AVIF then WebP sources at every encoded width and
    a WebP `<img>` fallback. The hero's is eager and high priority; the
    rest lazy. A decorative picture (a background poster) is `alt=""` with
    `role="presentation"`, so launch item 16 reads it as intentional."""
    base = f"{prefix}{MEDIA_DIR}/{photo.stem}"
    sources = "".join(
        f'<source type="image/{ext}" srcset="'
        + ", ".join(f"{base}-{w}.{ext} {w}w" for w in photo.widths)
        + f'" sizes="{escape(sizes)}">' for ext in ("avif", "webp"))
    loading = ('fetchpriority="high" decoding="async"' if eager
               else 'loading="lazy" decoding="async"')
    alt = ('alt="" role="presentation"' if decorative
           else f'alt="{escape(photo.alt)}"')
    class_attr = f' class="{cls}"' if cls else ""
    return (f"<picture{class_attr}>{sources}"
            f'<img src="{base}-{photo.widths[0]}.webp" width="{photo.widths[0]}" '
            f'{alt} {loading} style="aspect-ratio:{photo.ratio}"></picture>')


VIDEO_SCRIPT = (
    '<script data-atlas="scene">(function(){var m=window.matchMedia("(min-width:56rem)'
    ' and (prefers-reduced-motion: no-preference)");if(!m.matches)return;'
    'document.querySelectorAll("video[data-src]").forEach(function(v){'
    'v.src=v.getAttribute("data-src");v.play&&v.play().catch(function(){})})})();'
    "</script>")


def photo_scene(photo: Photo, *, prefix: str = "") -> str:
    """The trades hero (v4): the photograph full bleed behind the copy, the
    page's one eager image, under a gradient drawn in CSS. It keeps its
    honest alt (it is content, not decoration) and carries no loop, so a
    phone and a desktop fetch the same file at their own width."""
    return ('<div class="mf-scene mf-scene--photo" data-atlas="scene">'
            + picture(photo, prefix=prefix, sizes="100vw", eager=True, cls="mf-photo")
            + "</div>")


def scene(background: Background, *, prefix: str = "") -> str:
    """The moving background behind a hero: its poster as a decorative
    picture (what a phone, a paused page and a reduced-motion visitor see)
    and a muted, looping `<video>` whose file is attached by `VIDEO_SCRIPT`
    only where it belongs. Marked `data-atlas="scene"` for the checks."""
    poster = background.poster()
    return ('<div class="mf-scene" aria-hidden="true" data-atlas="scene">'
            + picture(poster, prefix=prefix, sizes="100vw", eager=True, decorative=True)
            + f'<video data-src="{prefix}{background.video}" muted loop playsinline '
              'preload="none" tabindex="-1"></video></div>')


# ------------------------------------------------------------------- files

def files_for(family: str, category: str | None) -> list[str]:
    """Every file a family's home page references, relative to the site."""
    out = list(FONT_FILES)
    background = BACKGROUNDS.get(family)
    if background:
        out += background.files()
    for photo in photos_for(category, family):
        if photo:
            out += photo.files()
    return out


def copy_assets(dest: Path, family: str | None = None, category: str | None = None
                ) -> list[str]:
    """The self-hosted font, and the media the page uses, beside it. With
    no family (a caller from before the media existed) only the font.
    Blocking."""
    wanted = files_for(family, category) if family else list(FONT_FILES)
    written = []
    for rel in wanted:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATES_DIR / rel, target)
        written.append(rel)
    return written
