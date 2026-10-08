"""What the course module may market: a closed catalog, defined here in code.

A product that is not in `CATALOG` is never marketed: `course.propose` refuses
any other key, the store reader refuses any other handle, and nothing reads
the store's full product list to find more. Adding a product is a code change
reviewed like any other.

Each entry says, per product:

  * `handle`      -- the Shopify handle; the store URL is built from it.
  * `audience`    -- who it is for, from its sales page (for Nora's choice).
  * `data`        -- the product file in `products/`: lesson hooks and lines,
                     the blog sections and the quotes, copied verbatim from
                     `vault_dir` (the server cannot read the vault).
  * `platforms`   -- the utm_source platforms a post for it may go to.
  * `languages`   -- the languages it may be posted in. Only the Operator has
                     reviewed own-course labels in Spanish, Portuguese and
                     Arabic; the AI for Every Age products are US-focused
                     (FTC, FBI, US Letter) and English only.
  * `claims`      -- what the product may be said to be: its sales page's
                     own line(s), word for word, from its data file (for
                     Nora's choice). A post itself asserts only the verbatim
                     lesson text in the product file, the live price (or
                     "free" for the lead magnet), the age notice and the
                     fixed framing lines in `compose.py`. Nothing else; see
                     `honesty.py` for what is refused.
  * `strict_numbers` / `no_fear` -- the stricter rules for the 60+ guide and
                     the free kit: no digit outside a verbatim, sourced excerpt,
                     and no fear words.
  * `lead_magnet` -- the free kit: its live price must read 0 and is said as
                     `FREE_WORDS`, never "$0"; a priced product reading 0, or
                     the kit reading anything else, is not drafted.
  * `compare_at`  -- whether a live compare-at ("was") price may be shown. The
                     AI for Every Age products have none by Miro's brief
                     (2026-09-29); if one appears on the store, nothing is
                     drafted until he looks. The Operator's "$20, was $49" is
                     the store's own figure and stays.

Price and availability are never here: they are read live at every draft and
every approval (`price.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

STORE = "https://atlasacademy-ai.myshopify.com"
OPERATOR = "operator"
FREE_WORDS = "Free with an email sign-up"
AGE_NOTICE = "Sold to adults 18+."
AGE_MARKER = "Sold to adults 18+"          # what the live product page must carry

# Every Postiz text platform plus the two hand-off channels (channels.py).
_ALL = frozenset({"x", "bluesky", "threads", "mastodon", "linkedin", "facebook",
                  "telegram", "shopify_blog"})


@dataclass(frozen=True, slots=True)
class Product:
    key: str
    handle: str
    title: str
    short: str
    audience: str
    data: str
    vault_dir: str
    platforms: frozenset[str]
    languages: tuple[str, ...] = ("en",)
    label: str = "This is my own course. -- Miro"
    noun: str = "course"
    strict_numbers: bool = False
    no_fear: bool = False
    lead_magnet: bool = False
    compare_at: bool = False

    @property
    def url(self) -> str:
        return f"{STORE}/products/{self.handle}"

    @property
    def claims(self) -> tuple[str, ...]:
        """What may be said of it: the sales-page line(s) in its data file,
        word for word. The posts themselves say only lesson text, the live
        price (or `FREE_WORDS`), the age notice and the fixed framing."""
        from . import data
        return tuple(c["text"] for c in data.claims(self.key))


CATALOG: dict[str, Product] = {p.key: p for p in (
    Product(
        key=OPERATOR,
        handle="build-a-safe-ai-operator-the-course",
        title="The AI That Can't Burn Your Business: Build a Safe AI Operator",
        short="Build a Safe AI Operator",
        audience=("founders, freelancers and small-business owners who can follow "
                  "along with some Python"),
        data="operator.json",
        vault_dir="Products/Build a Safe AI Operator",
        platforms=_ALL,
        languages=("en", "es", "pt", "ar"),
        compare_at=True,
    ),
    Product(
        key="adults",
        handle="everyday-ai-for-adults-use-claude-safely-and-build-your-first-"
               "automation",
        title="Everyday AI for Adults: Use Claude Safely and Build Your First "
              "Automation",
        short="Everyday AI for Adults",
        audience=("adults new to AI, parents who want house rules for AI, and "
                  "small-business owners who want a first automation"),
        data="adults.json",
        vault_dir="Products/AI for Every Age/adults",
        platforms=_ALL,
    ),
    Product(
        key="sixty_plus",
        handle="safe-and-simple-ai-60-large-print-guide-to-claude-and-scam-safety",
        title="Safe and Simple AI 60+: Large-Print Guide to Claude and Scam Safety",
        short="Safe and Simple AI 60+",
        audience=("adults 60 and over, and adult children buying for a parent or "
                  "grandparent"),
        data="sixty_plus.json",
        vault_dir="Products/AI for Every Age/seniors",
        platforms=frozenset({"facebook", "linkedin", "threads", "shopify_blog"}),
        label="This is my own guide. -- Miro",
        noun="guide",
        strict_numbers=True,
        no_fear=True,
    ),
    Product(
        key="bundle",
        handle="ai-for-every-age-bundle-everyday-ai-for-adults-safe-and-simple-ai-60",
        title="AI for Every Age Bundle: Everyday AI for Adults + Safe and Simple "
              "AI 60+",
        short="AI for Every Age Bundle",
        audience=("an adult learner and an older parent in the same household, or "
                  "an adult who wants both editions"),
        data="bundle.json",
        vault_dir="Products/AI for Every Age (adults/ and seniors/)",
        platforms=frozenset({"facebook", "linkedin", "threads", "shopify_blog"}),
        label="These are my own courses. -- Miro",
        noun="bundle",
    ),
    Product(
        key="scam_kit",
        handle="free-family-scam-safety-kit-6-printables-to-keep-by-the-phone",
        title="Free Family Scam-Safety Kit: 6 Printables to Keep by the Phone",
        short="Free Family Scam-Safety Kit",
        audience=("any family that wants a plan before the phone rings; the email "
                  "lead magnet"),
        data="scam_kit.json",
        vault_dir="Products/AI for Every Age/free-kit",
        platforms=frozenset({"facebook", "linkedin", "threads", "x", "bluesky",
                             "mastodon", "shopify_blog"}),
        label="This is my own free kit. -- Miro",
        noun="kit",
        strict_numbers=True,
        no_fear=True,
        lead_magnet=True,
    ),
)}

KEYS: tuple[str, ...] = tuple(CATALOG)


def get(key: str | None) -> Product | None:
    return CATALOG.get(str(key or ""))


def product_refusal(key: str | None) -> str | None:
    """Pure: why `key` is not a product to market, or None."""
    if get(key) is None:
        return (f"no product {key!r} in the catalog; one of {', '.join(KEYS)} -- "
                f"a product not in the catalog is never marketed")
    return None


PLAYBOOK = Path(__file__).parent / "playbook.md"


def playbook() -> str:
    """`playbook.md`, wrapped as quoted data: it was distilled from two web
    pages, so it reaches a prompt the way anything read from the web does
    (invariant 4) -- as evidence, never as instructions."""
    from ...kernel.permissions import wrap_untrusted
    try:
        text = PLAYBOOK.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(the playbook could not be read: {type(exc).__name__})"
    return wrap_untrusted(text, "course playbook (distilled from shopify.com/blog)")


def describe() -> list[dict[str, object]]:
    """The catalog as Nora reads it: no prices (those are read live)."""
    return [{"product": p.key, "title": p.title, "url": p.url,
             "audience": p.audience, "platforms": sorted(p.platforms),
             "languages": list(p.languages), "claims": list(p.claims),
             "lead_magnet": p.lead_magnet} for p in CATALOG.values()]
