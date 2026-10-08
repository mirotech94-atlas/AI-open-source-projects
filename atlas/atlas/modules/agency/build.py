"""agency.build_site — the mockup becomes a deployable static site (AGENCY.md §2.6).

Static where possible: a local business site is a brochure, not an
application. Two kinds:

* `kind="brochure"` (the default): the lead's designed template
  (`sitetemplate`, one of three trade families, designed with GetLayers
  and checked in) rendered to `data/agency/sites/<slug>/index.html` from
  the validated copy `agency.mockup` saved beside its page
  (`<id>.copy.json`), re-validated here, with the self-hosted font beside
  it. Since 2026-09-30 nothing is copied from model output: a mockup from
  before the templates has no copy record and is refused ("run
  agency.mockup again"), and a rendered page that `pagegate` does not pass
  -- not a whole document, or visible text that reads like tool output --
  is never written.

* `kind="store"`: a one-product store page, built here directly from a
  product photo and a description rather than from a prior `agency.mockup`
  run — GetLayers layers (`_mcp.McpClient`, the same three tools
  `mockup.py` calls, gated by the same `AGENCY_GETLAYERS_LICENCE_OK` this
  is client work too) supply the design material, and `think()` writes the
  copy for the sections that are safe to leave to a model: hero, bundles,
  upsells, FAQ. Two things are never trusted just because a prompt asked
  nicely (invariant 6, twice over): `think()`'s JSON is untrusted output,
  not trusted markup, so every value it returns is HTML-escaped at the
  point `_render_store_html` inserts it -- a headline that happened to
  contain `<form>` lands on the page as text, not as a live form; and its
  copy is filtered for fabricated social proof (`_has_fabricated_figure`,
  `_parse_store_copy`) -- a rating, a customer count, or a discount the
  model invented and that does not appear in `product_description` is
  dropped before it ever reaches the page, not merely asked not to be
  written. `product_photo` and `reviews_path` are confined to
  `AGENCY_UPLOADS_DIR/<lead_id>/` (`_resolve_upload`): a model-supplied
  path on a Tier AUTO action is exactly the kind of open argument
  invariant 2 warns a closed catalog can still have, so both are resolved
  (symlinks followed, not trusted) and checked against an allow-listed
  suffix before this ever reads or copies them. The reviews section and
  the "how to order" section are assembled here, not by the model, on
  purpose (see `_render_store_html` and `_how_to_order_html`): there is no
  reviews section at all until the client supplies real ones (a
  `reviews_path` file or `client.json`), and how-to-order is built straight
  from the lead's own
  phone and/or an email address the caller supplies -- a store with
  neither refuses to build at all, since a page that cannot be ordered
  from has no reason to exist. The product photo is copied next to
  `index.html` under a fixed name rather than referenced by its path on
  the ATLAS box, so the page still shows it once `deploy.py` ships only
  `site_dir` somewhere else. **There is no checkout on a store page and
  nothing here can add one.** ATLAS finds leads, builds pitches and pages
  — it never takes a customer's card. A store page ends at "call or email
  to order"; payment is arranged between the client and their own buyer,
  on whatever terms and processor the client already uses, never through
  ATLAS.

**Variants (Phase 20).** `kind="store"` takes `variants` (1 by default,
clamped to `STORE_VARIANTS_MAX`): that many deliberately different pages,
each its own GetLayers composition and its own headline angle (`ANGLES`:
benefit, proof, offer, story, urgency-free), all materialized under ONE
Style -- the first `styleId` GetLayers' search result names, passed to every
materialize call (the argument name is GetLayers' documented flow, assumed,
not verified against a live server; with no style named, none is sent and
the record says `style_id: null` rather than inventing one). Every variant
is scored by `kernel/review.py`'s page rubric, including its two hard store
checks ("no fabricated reviews", "no payment form"); a rejected variant is
never the primary and is not written anywhere. The best-scoring survivor
becomes `index.html` in the slug directory -- the only thing `deploy.py`
ships -- and the others are attached BESIDE it in `.<slug>.variants/`,
never inside it, so an alternate can never go public by riding along with
an rsync. The ranking is recorded in `agency_sites.variants`, and the
deploy preview names it. Variants are proposals, not a traffic-splitting
engine: ATLAS does not host the client's traffic, so nothing here routes a
visitor to one version or claims one converts better. Measuring a live
variant belongs to Task 30's experiment framework and needs a metric read
from the client's own site (PageSpeed is not conversion); nothing here
files an experiment, and until such a source exists a variant is
unmeasured, never "winning".
The product photo is the hero image; `think()` takes no images, so the
product description is always the client's own text, never drafted from
the photo, and the result says so.

**The 20 launch items (2026-09-29).** Both kinds then go through
`launch.finish_site`: the home page gets what it lacks (CTA above the fold,
share tags, JSON-LD, sticky mobile CTA, the client-data sections) and the
site gets contact, privacy, terms, 404, robots, sitemap, share image and --
behind a form -- a thank-you page. Reviews, case studies, the team photo,
hours and the response time come only from `AGENCY_UPLOADS_DIR/<lead_id>/
client.json` (`client.py`), a file a person puts there and never an action
argument a model could fill. `launchcheck.launch_audit` then reads the
built files back and its report is written beside the site as
`<slug>.LAUNCH_CHECK.md`; `deploy.py` refuses a site whose check has any
ISSUE.

**The AI chat assistant (2026-09-29).** When `client.json` says the client
bought the $500 or $300 package, every page carries `mapfront-chat.js` and
`sitechat.write_facts` writes the facts it answers from BESIDE the site
(`.<slug>.chat.json`, never deployed); any other package removes both, so
the public endpoint 404s that slug. The launch check's item 21 reads them
back. A rollback restores the facts file with the rest (`sitechat.py`).

Tier AUTO: nothing here leaves the box, `build_site` only rearranges files
already on disk and a row already in Postgres. A rebuild snapshots
whatever was already at the slug's directory before overwriting it, so
`build_site_rollback` can put it back — the same "undo this execution's
write" contract `audit.py` and `score.py` use, not a multi-generation
release history (that discipline lives in `deploy.py`, which is Tier ASK
because ITS mistake reaches a stranger's production domain). One rollback
path serves both kinds: it only ever restores or removes whatever is on
disk at the slug's directory, and neither kind writes anything else.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from dataclasses import replace
from html import escape
from pathlib import Path

from ...kernel import review
from ...kernel.contracts import ActionResult
from ...kernel.llm import Message, Need, think
from ...kernel.permissions import wrap_untrusted
from ..research.report import uncited_numbers
from . import _store, pagegate, sitechat, sitetemplate
from ._mcp import McpClient
from .client import (
    ClientData,
    ClientReview,
    Faq,
    client_data,
    http_url,
    parse_reviews,
    read_client_file,
    resolve_in,
)
from .launch import finish_site
from .launchcheck import (
    LaunchReport,
    launch_audit,
    provenance_path,
    report_path,
    write_report,
)
from .mockup import (
    _LICENCE_MISSING,
    COMPOSITIONS_TOOL,
    MATERIALIZE_TOOL,
    SEARCH_TOOL,
    _find_tools,
    _licence_ok,
)
from .sitekit import analytics_from_env, faq_list

SITES_DIR = Path(os.getenv("AGENCY_SITES_DIR", "data/agency/sites"))
UPLOADS_DIR = Path(os.getenv("AGENCY_UPLOADS_DIR", "data/agency/uploads"))

_SLUG_UNSAFE = re.compile(r"[^a-z0-9-]+")

KINDS = ("brochure", "store")

STORE_VARIANTS_MAX = 5

# One headline angle per variant, in the order variants are built -- a
# one-variant build is the benefit version. Each is a way into the same
# facts, never a licence to add new ones: "proof" means what the client's
# description demonstrates, and "urgency-free" exists because pressure copy
# ("today only", "3 left") is exactly what the rubric marks down.
ANGLES = ("benefit", "proof", "offer", "story", "urgency-free")
_ANGLE_BRIEFS = {
    "benefit": "lead with what the product does for the person who buys it.",
    "proof": "lead with facts the product description itself states -- "
             "materials, size, how it is made. Proof here never means a "
             "testimonial, a rating, a customer count or an award.",
    "offer": "lead with a bundle or an add-on described in plain terms; a "
             "price or a discount only if the product description states it.",
    "story": "lead with where the product comes from or how it is made, "
             "drawn only from the product description.",
    "urgency-free": "calm and plain: no countdown, no scarcity, no 'today "
                    "only', no pressure of any kind.",
}
assert tuple(_ANGLE_BRIEFS) == ANGLES

# The first style id a GetLayers result names, in either spelling. Read,
# never guessed: no match means no Style is sent.
_STYLE_ID = re.compile(r"""["']?style_?id["']?\s*[:=]\s*["']?([A-Za-z0-9_.:-]+)""",
                       re.IGNORECASE)

# What `product_photo` / `reviews_path` are allowed to point at (I-3): only
# these suffixes, and only inside this lead's own uploads folder.
_PHOTO_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_REVIEWS_SUFFIXES = (".json",)

STORE_SYSTEM = (
    "You write marketing copy for a one-product online store, as a JSON "
    "object and nothing else -- no markdown fences, no explanation. Keys, "
    "all optional but prefer to fill every one: \"headline\" (a short "
    "hero headline), \"subheadline\" (one supporting sentence), "
    "\"bundles\" (a list of 2-3 short bundle-offer strings), \"upsells\" "
    "(a list of 1-2 short upsell strings), \"faq\" (a list of 3-5 "
    "objects with \"q\" and \"a\"). Base all of it on the product "
    "description and the layout material a design tool supplied below. "
    "Never write a reviews section, a testimonial, a quote attributed to "
    "a customer, a star rating or review score, a customer or sales "
    "count, an award or accolade, or any price, discount or percentage "
    "that is not explicitly stated in the product description -- none of "
    "those are yours to invent. A bundle or upsell described in plain "
    "terms (\"2-pack\", \"buy one, get one\") is fine; attaching an "
    "invented number to it (\"buy one, get one, rated 4.9 stars by "
    "10,000 happy customers\") is not. A payment form or a checkout "
    "button is also not yours to write; the page around your copy "
    "handles reviews and ordering on its own."
)

# Figures that read as social proof or an invented offer: a percentage, a
# currency amount, a star rating ("4.8/5", "4.9 stars"), or a count
# attributed to customers/sales/reviews/orders. A bare small number ("2",
# "2-pack") never matches this, which is what lets "Buy 2, get 1" survive
# the filter below while "Save 20%" and "rated 4.9 stars" do not.
_SUSPICIOUS_FIGURE = re.compile(
    r"%|[$€£]|\b\d+(?:\.\d+)?\s*(?:/\s*5\b|-?\s*stars?\b)|"
    r"\b[\d,]+\+?\s*(?:customers?|clients?|buyers?|reviews?|ratings?|"
    r"sales|sold|users?|orders?)\b",
    re.IGNORECASE)


def clamp_variants(value: object) -> tuple[int | None, str | None]:
    """`variants` is a model-supplied argument on a Tier AUTO action
    (invariant 2: a closed catalog with open arguments is not closed), so
    it is checked here rather than trusted: a whole number, given as an int
    or a string of digits, clamped to `1..STORE_VARIANTS_MAX`. Anything
    else is refused by name rather than quietly read as 1."""
    if isinstance(value, bool):
        return None, f"variants must be a whole number from 1 to {STORE_VARIANTS_MAX}"
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        value = int(value)
    if not isinstance(value, int):
        return None, (f"variants must be a whole number from 1 to "
                      f"{STORE_VARIANTS_MAX}, not {value!r}")
    return max(1, min(STORE_VARIANTS_MAX, value)), None


def _check_variants(kind: str, variants: object) -> tuple[int | None, str | None]:
    """Shared by `build_site` and `build_site_dry` so a dry run can never
    approve what the real run would refuse."""
    n, reason = clamp_variants(variants)
    if reason is not None:
        return None, reason
    if kind != "store" and n != 1:
        return None, "variants apply to a store page only (kind='store')"
    return n, None


def _style_id_from(result: object) -> str | None:
    """A JSON `null` (or a stray boolean) is GetLayers naming no Style, not
    a Style called "null" -- never sent on as if it were one."""
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    match = _STYLE_ID.search(text or "")
    if not match or match.group(1).lower() in ("null", "none", "true", "false"):
        return None
    return match.group(1)


def _variants_dir(slug: str) -> Path:
    """Where the alternates live: BESIDE the slug directory, never in it,
    because `deploy.py` publishes the slug directory whole."""
    return SITES_DIR / f".{slug}.variants"


def _has_fabricated_figure(line: str, product_description: str) -> bool:
    """I-4(b): the prompt (I-4a, above) asks the model not to invent
    testimonials, ratings or discounts, but a prompt is not enforcement --
    invariant 6 exists precisely because a model asked nicely still
    invents numbers. This is the code-side backstop: a copy string is
    fabricated when it carries a suspicious figure (see
    `_SUSPICIOUS_FIGURE`) that does not appear anywhere in the client's
    own product description. Reuses `research.report.uncited_numbers` for
    the "does this figure appear in the source text" check, the same
    machinery invariant 6 already trusts, narrowed here to fire only on
    the shapes that read as fake social proof or an invented price --
    an ordinary small quantity ("2-pack", "buy 2, get 1") never trips
    `_SUSPICIOUS_FIGURE` and so is never checked or dropped. Catches only
    numeric fake claims; a non-numeric one ("customers love it") is left
    to the prompt in (a) alone, which the docstring above says plainly."""
    if not _SUSPICIOUS_FIGURE.search(line):
        return False
    return bool(uncited_numbers(line, product_description))


def slugify(name: str, lead_id: int) -> str:
    base = _SLUG_UNSAFE.sub("-", (name or "").strip().lower()).strip("-")
    return f"{base or 'lead'}-{lead_id}"


def _write_build(html: str, site_dir: Path, is_rebuild: bool,
                 family: str | None = None, category: str | None = None) -> str | None:
    """Blocking (`shutil` + `Path`); always run through `asyncio.to_thread`
    — "async throughout" (CLAUDE.md) rules out a synchronous filesystem
    call on the event loop. Snapshots whatever was already at `site_dir`
    before overwriting it, returning the backup path, or None when this is
    a fresh build with nothing to snapshot. Writes the rendered home page
    and the template's self-hosted font and media beside it (`deploy.py`
    ships only `site_dir`)."""
    backup_dir: str | None = None
    if is_rebuild and site_dir.exists():
        backup_dir = str(site_dir.parent / f".{site_dir.name}.rollback")
        if Path(backup_dir).exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(site_dir, backup_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(html, encoding="utf-8")
    sitetemplate.copy_assets(site_dir, family, category)
    return backup_dir


_STALE_MOCKUP = ("the mockup for lead {lead_id} predates the designed templates (it has "
                 "no validated copy beside it); run agency.mockup again")


def _brochure_copy(mockup_path: str | None) -> dict | None:
    """The validated copy `agency.mockup` saved beside its page, or None when
    there is no mockup or it was made before the designed templates (the
    raw-model pages the 2026-09-30 fix retired). Blocking."""
    if not mockup_path:
        return None
    return sitetemplate.read_copy_record(
        sitetemplate.copy_record_path(Path(mockup_path)))


def render_brochure(lead: dict, client: ClientData, record: dict,
                    form_action: str | None
                    ) -> tuple[ClientData, str, list[Faq], str | None]:
    """The home page, rendered here from the designed template and the
    copy record -- never copied from model output. The copy is validated
    again against the lead's current details (it is model output at rest).
    Returns (themed client, html, faqs, why it may not be published)."""
    family = record.get("family")
    if family not in sitetemplate.FAMILIES:
        family = sitetemplate.family_for(lead.get("category"))
    client = sitetemplate.themed(client, str(family))
    copy, _notes = sitetemplate.parse_copy(record["copy"], client, str(family))
    faqs = faq_list(client, list(copy.faqs), form=bool(form_action))
    html = sitetemplate.render_home(client, copy, str(family), faqs,
                                    form_action=form_action)
    return client, html, faqs, pagegate.page_problem(html)


def _write_store_html(html: str, product_photo: str, photo_name: str,
                      site_dir: Path, is_rebuild: bool) -> str | None:
    """The store-page equivalent of `_write_build`: same snapshot-before-
    overwrite contract, but the content is a string this process wrote
    rather than a file already on disk. Also copies the product photo into
    `site_dir` under `photo_name` -- `deploy.py` only ships `site_dir`
    itself, so a page that referenced the photo by its original path on
    the ATLAS box would show a broken image everywhere else. Blocking;
    always run through `asyncio.to_thread`."""
    backup_dir: str | None = None
    if is_rebuild and site_dir.exists():
        backup_dir = str(site_dir.parent / f".{site_dir.name}.rollback")
        if Path(backup_dir).exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(site_dir, backup_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(product_photo, site_dir / photo_name)
    (site_dir / "index.html").write_text(html)
    return backup_dir


def index_sha(site_dir: Path) -> str | None:
    """The first 12 hex chars of the sha256 of `site_dir/index.html`, or None
    when it cannot be read. Blocking; always run through `asyncio.to_thread`.
    `agency.build_site` records it; its read-back recomputes it."""
    try:
        data = (Path(site_dir) / "index.html").read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:12]


def _remove_site_dir(site_dir: Path) -> None:
    """Blocking; always run through `asyncio.to_thread`."""
    shutil.rmtree(site_dir, ignore_errors=True)


def _restore_backup(site_dir: Path, backup_dir: str) -> None:
    """Blocking; always run through `asyncio.to_thread`."""
    shutil.rmtree(site_dir, ignore_errors=True)
    shutil.move(backup_dir, str(site_dir))


def _write_alternates(variants_dir: Path, alternates: list[tuple[str, str]],
                      product_photo: str | None, photo_name: str | None) -> str | None:
    """Replace the alternates beside a slug directory with `alternates`
    (`(angle, html)` pairs), snapshotting whatever was there first so a
    rollback can put it back; returns the snapshot path, or None when there
    was nothing to snapshot. An empty `alternates` just clears the old set
    (a one-variant rebuild, or a brochure over what was a store). The photo
    is copied in once so an alternate opens with its hero intact. Blocking;
    always run through `asyncio.to_thread`."""
    backup_dir: str | None = None
    if variants_dir.exists():
        backup_dir = str(variants_dir.parent / f"{variants_dir.name}.rollback")
        if Path(backup_dir).exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(variants_dir), backup_dir)
    if alternates:
        variants_dir.mkdir(parents=True, exist_ok=True)
        if product_photo and photo_name:
            shutil.copyfile(product_photo, variants_dir / photo_name)
        for angle, html in alternates:
            (variants_dir / f"{angle}.html").write_text(html)
    return backup_dir


def _restore_alternates(variants_dir: Path, backup_dir: str | None) -> None:
    """Blocking; always run through `asyncio.to_thread`."""
    shutil.rmtree(variants_dir, ignore_errors=True)
    if backup_dir and Path(backup_dir).exists():
        shutil.move(backup_dir, str(variants_dir))


def _resolve_upload(lead_id: int, raw_path: str, allowed_suffixes: tuple[str, ...],
                    what: str) -> tuple[Path | None, str | None]:
    """I-3: confines `raw_path` to `AGENCY_UPLOADS_DIR/<lead_id>/` before
    this process ever reads or copies it.

    `product_photo` and `reviews_path` are parameters on a Tier AUTO
    action's closed catalog (invariant 2 says the catalog is closed but
    its arguments are the model's own words) -- without this, a value
    like `/app/.env` would pass a bare `exists()` check and get copied
    straight into a directory `deploy.py` publishes, and a JSON file
    anywhere on the box (a stored buyer message, say) could be read as
    "real reviews". A relative `raw_path` is resolved against this lead's
    own uploads folder; an absolute one is accepted only if it already
    lives there. `Path.resolve()` follows symlinks, so a symlink placed
    inside the uploads folder that points outside it is caught by the
    confinement check below rather than by the suffix check, which runs
    on the name the caller supplied -- the check that actually matters is
    where the bytes really come from.
    """
    # M-9: a synchronous stat on the event loop, same pre-existing pattern as
    # the brochure path's `Path(mockup_path).exists()` below -- one
    # `resolve()` on a validation path, not a loop over a large or
    # untrusted-size tree. The confinement itself lives in
    # `client.resolve_in`, shared with `client.json`'s own file references.
    return resolve_in(UPLOADS_DIR / str(lead_id), raw_path, allowed_suffixes, what)


def _read_reviews_file(path: str) -> list[dict] | None:
    """Real reviews come from a file the client supplies -- a JSON list of
    `{"author", "text", "rating"?, "source_url"?}` objects -- never invented.
    An entry without an author is dropped rather than credited to "a
    customer" (an anonymous quote is not a review anyone can check).
    Returns None (not an empty list) on anything that is not that shape, so
    the caller renders no reviews section at all; blocking, always run
    through `asyncio.to_thread`."""
    try:
        raw = Path(path).read_text()
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out = [_review_dict(r) for r in parse_reviews(data, [])]
    return out or None


def _review_dict(review: ClientReview) -> dict:
    return {"author": review.author, "text": review.text, "rating": review.rating,
            "source_url": review.source_url}


def _review_footer(review: dict) -> str:
    """Author, rating and source, all inside the quote's <footer> -- the
    page rubric reads a star or a score there as the reviewer's own, and
    anywhere else in the section as a rating nobody gave."""
    meta = [f"— {escape(str(review.get('author') or ''))}"]
    rating = review.get("rating")
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        meta.append(f"rated {int(rating) if float(rating).is_integer() else rating}/5")
    source = http_url(review.get("source_url"))
    if source:
        meta.append(f'<a href="{escape(source)}" rel="noopener">source</a>')
    return f"<footer>{', '.join(meta)}</footer>"


def _parse_store_copy(text: str, product_description: str) -> dict:
    """Never trust the model's JSON to be well-formed or complete -- a
    parse failure or a missing key falls back to an empty value that
    `_render_store_html` already knows how to render around, rather than
    raising and failing the whole build over copy that didn't parse.

    Also where I-4(b) is enforced: every string the model wrote (headline,
    subheadline, each bundle/upsell, each FAQ question and answer) is
    dropped if `_has_fabricated_figure` flags it against
    `product_description`. A dropped headline/subheadline becomes "" (so
    `_render_store_html`'s own fallback text takes over); a dropped
    bundle/upsell/FAQ entry is removed from its list -- if a list ends up
    empty, the placeholder text in `_render_store_html` is the intended
    result, not a bug."""
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    def _clean(value: str) -> str:
        value = str(value or "").strip()
        return "" if _has_fabricated_figure(value, product_description) else value

    def _clean_list(raw: object) -> list[str]:
        out = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, (str, int, float)):
                continue
            cleaned = _clean(str(item))
            if cleaned:
                out.append(cleaned)
        return out

    faq_in = data.get("faq")
    faq = []
    if isinstance(faq_in, list):
        for item in faq_in:
            if not (isinstance(item, dict) and item.get("q") and item.get("a")):
                continue
            q, a = _clean(str(item["q"])), _clean(str(item["a"]))
            if q and a:
                faq.append({"q": q, "a": a})

    return {
        "headline": _clean(data.get("headline") or ""),
        "subheadline": _clean(data.get("subheadline") or ""),
        "bundles": _clean_list(data.get("bundles")),
        "upsells": _clean_list(data.get("upsells")),
        "faq": faq,
    }


def _how_to_order_html(phone: str | None, email: str | None) -> str:
    """The entire "how to order" section, built here rather than by the
    model: it is where the payments-are-the-client's rule (Tier 3 money
    out stays permanently denied; ATLAS never holds a customer's card)
    becomes a fact about a web page, not just a Tier check -- there is no
    code path through which a payment field could end up in this string,
    because nothing that writes it ever asks for a price or a card.
    `phone`/`email` are escaped like any other value that lands in the
    page -- a lead's own contact fields are still untrusted input, not a
    value ATLAS wrote."""
    ways = []
    if phone:
        ways.append(f"call {escape(str(phone))}")
    if email:
        ways.append(f"email {escape(str(email))}")
    line = " or ".join(ways) if ways else "contact us directly"
    line = line[:1].upper() + line[1:]         # capitalize() lowercases the rest
    return (
        '<section id="how-to-order"><h2>How to order</h2>'
        f"<p>{line} to place your order. We'll confirm details and "
        "arrange payment directly with you -- nothing is charged on this "
        "page.</p></section>"
    )


def _render_store_html(lead: dict, copy: dict, reviews: list[dict] | None,
                       photo_name: str, how_to_order_html: str) -> str:
    """Assembles the five model-agnostic sections plus how-to-order.
    Nothing here ever emits a `<form>`, an `<input>` for a card number, or
    a "buy now"/checkout button -- the sections that touch money
    (reviews' honesty, how-to-order's no-payment rule) are template
    strings this function controls, not text the model wrote. Every value
    that came from the model, a reviews file, or the lead row is escaped
    at the point it is inserted: `think()`'s JSON is untrusted the same as
    any other model output, and a headline of `<form>...` must land on the
    page as literal text, not as markup -- otherwise "no payment form" is
    only a prompt instruction, not something this code guarantees."""
    headline = copy["headline"] or lead.get("name") or "Shop now"
    subheadline = copy["subheadline"]
    bundles_html = "".join(f"<li>{escape(b)}</li>" for b in copy["bundles"]) or (
        "<li>Ask about bundle pricing.</li>")
    upsells_html = "".join(f"<li>{escape(u)}</li>" for u in copy["upsells"]) or (
        "<li>Ask about add-ons.</li>")
    faq_html = "".join(f"<dt>{escape(item['q'])}</dt><dd>{escape(item['a'])}</dd>"
                       for item in copy["faq"])
    # No reviews, no section (2026-09-29): not even a neutral "on their way"
    # line. M-6 still holds -- no internal note on a public page -- and the
    # launch check lists reviews as NEEDS_CLIENT instead.
    reviews_html = ""
    if reviews:
        reviews_html = ('<section id="reviews"><h2>Reviews</h2>' + "".join(
            f"<blockquote>{escape(r['text'])}{_review_footer(r)}</blockquote>"
            for r in reviews) + "</section>")
    safe_headline = escape(headline)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{safe_headline}</title></head><body>"
        f'<section id="hero"><img src="{escape(photo_name)}" alt="{safe_headline}">'
        f"<h1>{safe_headline}</h1><p>{escape(subheadline)}</p></section>"
        f'<section id="bundles"><h2>Bundles</h2><ul>{bundles_html}</ul></section>'
        f'<section id="upsells"><h2>You might also like</h2><ul>{upsells_html}</ul></section>'
        f"{reviews_html}"
        f'<section id="faq"><h2>FAQ</h2><dl>{faq_html}</dl></section>'
        f"{how_to_order_html}"
        "</body></html>"
    )


async def _make_store_variants(
    lead: dict, photo_path: Path, product_description: str,
    profile: ClientData, count: int, form_action: str | None = None,
) -> tuple[list[dict], str | None, str | None, str | None]:
    """GetLayers for the design material, `think()` for the copy -- the same
    two-step `mockup.py` uses for a brochure, aimed at a product instead of
    a business's existing site, run once per variant. Returns
    `(variants, style_id, photo_name, None)` or `([], None, None, reason)`;
    never raises on a GetLayers failure.

    One search, one Style: the search runs once and the style id it names
    goes to every variant's materialize call, so the variants differ in
    composition and angle but read as one brand. Each variant then asks for
    its own composition (the angle is in the query) and gets its own copy.
    A variant whose GetLayers call fails is recorded with that reason and
    the rest carry on; only when every one fails does the build fail.

    Every variant is scored with `review.score_page(..., store=True)`: the
    client's reviews, when there is a file, are the only testimonials it
    accepts. `photo_name` is the name the photo will be copied under, next
    to `index.html`, so the page never references a path that only exists
    on the ATLAS box. `photo_path` and `reviews_path` are already confined
    and validated by `_resolve_upload` (I-3) before this is ever called.
    The photo's real path never reaches the `think()` prompt, and the photo
    itself does not either: `think()` takes no images, so the product
    description is the client's own text, never one drafted from the
    photo."""
    photo_name = "product" + photo_path.suffix
    client = McpClient()
    _init, reason = await client.initialize()
    if reason is not None:
        return [], None, None, reason

    _ok, reason = await _find_tools(client)
    if reason is not None:
        return [], None, None, reason

    query = (f"{lead.get('category') or 'product'} store for "
            f"{lead.get('name') or 'this product'}: {product_description}")

    search_result, reason = await client.call(SEARCH_TOOL, {"query": query})
    if reason is not None:
        return [], None, None, reason
    style_id = _style_id_from(search_result)

    # The client's reviews (client.json and/or the reviews_path file, merged
    # in `build_site`) are the only ones a page may carry.
    reviews = [_review_dict(r) for r in profile.reviews] or None
    client_reviews = [r["text"] for r in reviews] if reviews else None
    how_to_order = _how_to_order_html(profile.phone, profile.email)

    variants: list[dict] = []
    for angle in ANGLES[:count]:
        entry: dict = {"angle": angle, "style_id": style_id}
        angled = f"{query} -- {angle} angle"
        compositions_result, reason = await client.call(
            COMPOSITIONS_TOOL, {"query": angled})
        if reason is None:
            arguments = {"query": angled, "compositions": compositions_result}
            if style_id:
                arguments["styleId"] = style_id
            materialize_result, reason = await client.call(MATERIALIZE_TOOL, arguments)
        if reason is not None:
            entry["error"] = reason
            variants.append(entry)
            continue

        material = (
            f"A product photo is supplied separately and already placed in "
            f"the hero section -- do not describe or reference it by name.\n"
            f"Description: {product_description}\n\n"
            f"GetLayers search result:\n{search_result}\n\n"
            f"GetLayers compositions:\n{compositions_result}\n\n"
            f"GetLayers materialized layer:\n{materialize_result}\n"
        )
        wrapped = wrap_untrusted(material, "getlayers")
        system = f"{STORE_SYSTEM}\n\nAngle: {angle} -- {_ANGLE_BRIEFS[angle]}"
        completion = await think(Need.HARD, system, [Message("user", wrapped)],
                                 max_tokens=2000)
        copy = _parse_store_copy(completion.text, product_description)
        # The FAQ the page shows is the one its FAQPage JSON-LD will carry:
        # the client's own questions, then this variant's filtered copy,
        # then questions answered from facts on record (`faq_list`).
        copy["faq"] = [{"q": f.q, "a": f.a} for f in faq_list(
            profile, [Faq(x["q"], x["a"]) for x in copy["faq"]], form=bool(form_action))]
        entry["faqs"] = copy["faq"]
        html = _render_store_html(lead, copy, reviews, photo_name, how_to_order)
        scored = review.score_page(html, store=True, client_reviews=client_reviews)
        entry.update(html=html, score=scored.score, rejected=scored.rejected,
                     failed=scored.failed)
        variants.append(entry)

    if all("error" in v for v in variants):
        return [], None, None, variants[0]["error"]
    return variants, style_id, photo_name, None


def _rank(variants: list[dict]) -> list[dict]:
    """Survivors first, best score first; a tie keeps build order, so the
    benefit version wins a dead heat. Rejected and failed variants follow,
    unranked."""
    ok = [v for v in variants if "error" not in v and not v["rejected"]]
    ok.sort(key=lambda v: -(v["score"] or 0.0))           # stable: ties keep order
    kept = {id(v) for v in ok}
    rest = [v for v in variants if id(v) not in kept]
    for i, v in enumerate(ok, start=1):
        v["rank"] = i
    for v in rest:
        v["rank"] = None
    return ok + rest


def _variants_record(ranked: list[dict], style_id: str | None, site_dir: Path,
                     variants_dir: Path) -> dict:
    """What `agency_sites.variants` keeps, and what the deploy preview
    reads: the ranking and why, never the HTML itself (the files hold
    that). A rejected or failed variant keeps its reason and no path --
    it was never written."""
    out = []
    for v in ranked:
        row: dict = {"rank": v["rank"], "angle": v["angle"], "style_id": v["style_id"]}
        if "error" in v:
            row.update(error=v["error"], path=None)
        else:
            score = v["score"]
            row.update(score=None if score is None else round(score, 3),
                       rejected=v["rejected"], failed=v["failed"])
            if v["rank"] == 1:
                row["path"] = str(site_dir / "index.html")
            elif v["rank"] is not None:
                row["path"] = str(variants_dir / f"{v['angle']}.html")
            else:
                row["path"] = None
        out.append(row)
    return {"style_id": style_id, "primary": ranked[0]["angle"],
            "dir": str(variants_dir), "variants": out}


def _validate_store_inputs(
    lead_id: int, lead: dict, product_photo: str | None,
    product_description: str | None, contact_email: str | None,
    reviews_path: str | None,
) -> tuple[Path | None, Path | None, str | None]:
    """Every check `kind="store"` needs before touching GetLayers or the
    filesystem, shared between `build_site` and `build_site_dry` so a dry
    run can never approve what the real run would refuse. Returns
    `(photo_path, reviews_file_path, None)` -- both already resolved and
    confined to this lead's uploads folder (I-3) -- or `(None, None,
    reason)`. `reviews_file_path` is `None` when `reviews_path` was not
    given at all, which is allowed (the placeholder renders instead)."""
    if not _licence_ok():
        return None, None, _LICENCE_MISSING
    if not product_photo:
        return None, None, "a store page needs product_photo (a path to one product photo)"
    if not product_description:
        return None, None, "a store page needs product_description"
    if not lead.get("phone") and not contact_email:
        # M-5: a page with no phone and no email has no way to complete
        # the one thing it exists to do -- take an order.
        return None, None, "a store needs a way to order"
    photo_path, reason = _resolve_upload(
        lead_id, product_photo, _PHOTO_SUFFIXES, "product_photo")
    if reason is not None:
        return None, None, reason
    reviews_file: Path | None = None
    if reviews_path:
        reviews_file, reason = _resolve_upload(
            lead_id, reviews_path, _REVIEWS_SUFFIXES, "reviews_path")
        if reason is not None:
            return None, None, reason
    return photo_path, reviews_file, None


async def _client_for(lead_id: int, lead: dict, raw_client: dict | None,
                      contact_email: str | None, reviews_file: Path | None,
                      ) -> tuple[ClientData, str | None]:
    """The lead row overlaid with the client's own `client.json`, plus a
    store's `reviews_path` file; and the form endpoint, the client's own
    (`form_action`) before the agency-wide `AGENCY_FORM_ACTION`. No form
    endpoint means no form: phone and email carry the enquiry instead. A
    preview gets no endpoint at all."""
    extra: tuple[ClientReview, ...] = ()
    if reviews_file is not None:
        rows = await asyncio.to_thread(_read_reviews_file, str(reviews_file)) or []
        extra = tuple(ClientReview(r["author"], r["text"], r["rating"], r["source_url"])
                      for r in rows)
    client = await asyncio.to_thread(
        client_data, lead, raw_client, UPLOADS_DIR / str(lead_id),
        contact_email=contact_email, extra_reviews=extra)
    # An unsold preview collects nobody's details: its enquiry and booking
    # forms would land in an inbox for a business that hasn't bought the site.
    if sitetemplate.is_preview(client):
        return client, None
    form_action = client.form_action or http_url(os.getenv("AGENCY_FORM_ACTION"))
    return client, form_action


def _launch_line(check: LaunchReport) -> str:
    verdict = ("ready to deploy" if check.ready
               else "NOT ready to deploy until the ISSUEs are fixed")
    return f"{check.summary()} -- {verdict}"


def _remove_side_files(site_dir: Path) -> None:
    """The launch report, provenance record and chat facts beside a site.
    Blocking."""
    for path in (report_path(site_dir), provenance_path(site_dir),
                 sitechat.facts_path(site_dir)):
        path.unlink(missing_ok=True)


def _snapshot_side(source: Path) -> str | None:
    """Copy a record beside the site aside before a rebuild rewrites it, so
    a rollback restores the one that matches the restored files. None when
    there was none. Blocking."""
    if not source.exists():
        return None
    backup = source.with_name(source.name + ".rollback")
    shutil.copyfile(source, backup)
    return str(backup)


def _restore_side(target: Path, backup: str | None) -> None:
    """Blocking."""
    if backup and Path(backup).exists():
        shutil.move(backup, str(target))
    else:
        target.unlink(missing_ok=True)


def _snapshot_provenance(site_dir: Path) -> str | None:
    """The provenance record, before a rebuild rewrites it (without it, the
    restored site's genuine client reviews would read as invented, and every
    launch page as someone else's). Blocking."""
    return _snapshot_side(provenance_path(site_dir))


def _restore_provenance(site_dir: Path, backup: str | None) -> None:
    """Blocking."""
    _restore_side(provenance_path(site_dir), backup)


def _refresh_report(site_dir: Path) -> None:
    """Re-run the launch check on whatever is at `site_dir` now and rewrite
    its report, so the report never describes a build that was undone.
    Blocking."""
    if site_dir.is_dir():
        write_report(site_dir, launch_audit(site_dir))
    else:
        report_path(site_dir).unlink(missing_ok=True)


async def build_site(
    lead_id: int,
    kind: str = "brochure",
    *,
    product_photo: str | None = None,
    product_description: str | None = None,
    contact_email: str | None = None,
    reviews_path: str | None = None,
    variants: int = 1,
) -> ActionResult:
    if kind not in KINDS:
        return ActionResult(False, f"unknown site kind {kind!r}; expected "
                            f"one of {', '.join(KINDS)}")
    count, reason = _check_variants(kind, variants)
    if reason is not None:
        return ActionResult(False, reason)

    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    raw_client, reason = await asyncio.to_thread(
        read_client_file, UPLOADS_DIR / str(lead_id))
    if reason is not None:
        return ActionResult(False, reason)

    photo_path: Path | None = None
    reviews_file: Path | None = None
    copy_record: dict | None = None
    if kind == "brochure":
        mockup_path = await _store.latest_mockup_path(lead_id)
        if not mockup_path or not Path(mockup_path).exists():
            return ActionResult(
                False, f"no mockup on disk for lead {lead_id}; run agency.mockup first")
        copy_record = await asyncio.to_thread(_brochure_copy, mockup_path)
        if copy_record is None:
            return ActionResult(False, _STALE_MOCKUP.format(lead_id=lead_id))
    else:
        photo_path, reviews_file, reason = _validate_store_inputs(
            lead_id, lead, product_photo, product_description, contact_email,
            reviews_path)
        if reason is not None:
            return ActionResult(False, reason)

    client, form_action = await _client_for(
        lead_id, lead, raw_client, contact_email if kind == "store" else None,
        reviews_file)

    slug = slugify(lead.get("name") or "", lead_id)
    site_dir = SITES_DIR / slug
    variants_dir = _variants_dir(slug)
    existing = await _store.get_site(slug)
    is_rebuild = existing is not None
    previous_variants = (_store.parse_json(existing.get("variants"), None)
                         if existing else None)

    record: dict | None = None
    faqs: list[Faq] | None = None
    colours_given = bool(raw_client and "colours" in raw_client)
    if kind == "brochure" and copy_record is not None:
        if not colours_given:
            # The share image takes the template family's own colour.
            brand = sitetemplate.FAMILY_BRAND.get(str(copy_record.get("family")))
            if brand:
                client = replace(client, colours={**client.colours, "primary": brand})
                colours_given = True
        client, html, faqs, problem = render_brochure(lead, client, copy_record,
                                                      form_action)
        if problem is not None:
            # The hard gate (pagegate): nothing that is not a whole page about
            # the business is ever written into a site directory.
            return ActionResult(False, f"refused to build lead {lead_id}: the rendered "
                                       f"page is {problem}; nothing was written")
        backup_dir = await asyncio.to_thread(_write_build, html, site_dir, is_rebuild,
                                             client.theme, client.category)
        # A brochure over what was a store: the old alternates go (kept for
        # the rollback), so the deploy preview never names a stale ranking.
        variants_backup = await asyncio.to_thread(
            _write_alternates, variants_dir, [], None, None)
        detail = (f"built brochure site for lead {lead_id} at {site_dir} from the "
                  f"{client.theme} template ({sitetemplate.STYLE_ID}); copy: "
                  f"{copy_record.get('source', 'unknown')}")
    else:
        built, style_id, photo_name, reason = await _make_store_variants(
            lead, photo_path, product_description, client, count, form_action)
        if reason is not None:
            return ActionResult(False, reason)
        ranked = _rank(built)
        primary = ranked[0]
        if primary["rank"] is None:
            why = "; ".join(
                f"{v['angle']}: {v.get('error') or ', '.join(v['failed'])}"
                for v in ranked)
            return ActionResult(
                False, f"every store variant was rejected by the page rubric ({why}); "
                       f"nothing was written")
        alternates = [(v["angle"], v["html"]) for v in ranked[1:] if v["rank"] is not None]
        backup_dir = await asyncio.to_thread(
            _write_store_html, primary["html"], str(photo_path), photo_name,
            site_dir, is_rebuild)
        variants_backup = await asyncio.to_thread(
            _write_alternates, variants_dir, alternates, str(photo_path), photo_name)
        record = _variants_record(ranked, style_id, site_dir, variants_dir)
        faqs = [Faq(x["q"], x["a"]) for x in primary.get("faqs") or []]
        detail = (f"built store site for lead {lead_id} at {site_dir}; primary: "
                  f"{primary['angle']} (rubric {primary['score']:.2f}) of "
                  f"{len(ranked)} variant{'s' if len(ranked) != 1 else ''}")
        if alternates:
            detail += (f", {len(alternates)} attached beside it in {variants_dir} "
                       f"(not deployed)")
        rejected = [v["angle"] for v in ranked if v["rank"] is None]
        if rejected:
            detail += f"; rejected or failed: {', '.join(rejected)}"
        detail += ("; the product description is the client's own text -- "
                   "think() cannot read the photo")

    # The 20 launch items (sitekit / launch.finish_site): the home page's
    # additions, the other pages and the share image, inside site_dir so a
    # rollback removes or restores them with the rest; then the launch check
    # reads the result back and its report goes BESIDE the site.
    provenance_backup = (await asyncio.to_thread(_snapshot_provenance, site_dir)
                         if is_rebuild else None)
    chat_backup = (await asyncio.to_thread(
        _snapshot_side, sitechat.facts_path(site_dir)) if is_rebuild else None)
    finished = await asyncio.to_thread(
        finish_site, site_dir, client, analytics=analytics_from_env(), legacy_lead=lead,
        form_action=form_action, faqs=faqs, colours_given=colours_given)
    # The AI chat assistant's facts ($500/$300 packages), beside the site
    # like the provenance record; removed for any other package, so the
    # endpoint stops answering for a site that no longer carries it.
    await asyncio.to_thread(sitechat.write_facts, site_dir, client)
    if client.chat:
        detail += f"; AI chat assistant on ({client.package} package)"
    check = await asyncio.to_thread(launch_audit, site_dir)
    await asyncio.to_thread(write_report, site_dir, check)
    detail += "; " + _launch_line(check)
    if finished.kept:
        detail += (f"; kept as they are (edited or not ATLAS's): "
                   f"{', '.join(finished.kept)}")
    if client.problems:
        detail += "; client.json: " + "; ".join(client.problems[:5])

    created = existing is None
    if created:
        site_id = await _store.insert_site(lead_id, slug, None, str(site_dir),
                                           variants=record)
        if site_id is None:
            # Lost a race with another build for the same slug: nothing of
            # ours to roll back, and the other run's row is not ours to touch.
            return ActionResult(False, f"a site already exists for slug {slug!r}")
    elif record is not None or previous_variants is not None:
        await _store.set_site_variants(slug, record)

    # M-7: a build that isn't the lead's first (already `contacted`, say)
    # must not silently rewind its status forever -- capture what it was
    # so a rollback can put it back rather than leaving it at "mocked".
    previous_status = lead.get("status")
    await _store.set_lead_status(lead_id, "mocked")

    token = json.dumps({"slug": slug, "created": created, "backup_dir": backup_dir,
                        "provenance_touched": True,
                        "provenance_backup": provenance_backup,
                        "chat_touched": True, "chat_backup": chat_backup,
                        "lead_id": lead_id, "previous_status": previous_status,
                        "variants_touched": True, "variants_backup": variants_backup,
                        "previous_variants": previous_variants})
    evidence: dict = {"slug": slug, "path": str(site_dir), "kind": kind,
                      "index_sha": await asyncio.to_thread(index_sha, site_dir),
                      "launch_check": check.as_dict(),
                      "launch_report": str(report_path(site_dir))}
    if record is not None:
        evidence["variants"] = record
    return ActionResult(True, detail, evidence=evidence, rollback_token=token)


async def build_site_dry(
    lead_id: int,
    kind: str = "brochure",
    *,
    product_photo: str | None = None,
    product_description: str | None = None,
    contact_email: str | None = None,
    reviews_path: str | None = None,
    variants: int = 1,
) -> ActionResult:
    if kind not in KINDS:
        return ActionResult(False, f"unknown site kind {kind!r}; expected "
                            f"one of {', '.join(KINDS)}")
    count, reason = _check_variants(kind, variants)
    if reason is not None:
        return ActionResult(False, reason)
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    _raw, reason = await asyncio.to_thread(read_client_file, UPLOADS_DIR / str(lead_id))
    if reason is not None:
        return ActionResult(False, reason)
    slug = slugify(lead.get("name") or "", lead_id)
    if kind == "brochure":
        mockup_path = await _store.latest_mockup_path(lead_id)
        if not mockup_path:
            return ActionResult(
                False, f"no mockup on disk for lead {lead_id}; run agency.mockup first")
        if await asyncio.to_thread(_brochure_copy, mockup_path) is None:
            return ActionResult(False, _STALE_MOCKUP.format(lead_id=lead_id))
        return ActionResult(True, f"would render the designed template for lead "
                            f"{lead_id} at {SITES_DIR / slug} with the 20 launch items, "
                            f"then run the launch check")
    _photo_path, _reviews_file, reason = _validate_store_inputs(
        lead_id, lead, product_photo, product_description, contact_email, reviews_path)
    if reason is not None:
        return ActionResult(False, reason)
    plural = "variant" if count == 1 else "variants"
    return ActionResult(
        True, f"would assemble a static store page for lead {lead_id} at "
              f"{SITES_DIR / slug} ({count} {plural} under one Style, ranked by "
              f"the page rubric; GetLayers for layout, think() for copy, "
              f"no checkout)")


async def build_site_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written for this run; nothing to roll back")
    try:
        data = json.loads(token)
    except ValueError:
        return ActionResult(False, "rollback token is not valid JSON")

    slug = data["slug"]
    site_dir = SITES_DIR / slug
    lead_id = data.get("lead_id")
    previous_status = data.get("previous_status")

    async def _restore_status() -> None:
        # M-7: put the lead's status back to what it was before this
        # build, not just "whatever build_site happened to set" -- a
        # rebuild of a lead already past "mocked" (e.g. "contacted")
        # must not get stuck back at "mocked" once its build is undone.
        if lead_id is not None and previous_status is not None:
            await _store.set_lead_status(lead_id, previous_status)

    async def _restore_variants() -> None:
        # Phase 20: the alternates beside the slug directory, and the
        # ranking in `agency_sites.variants`, go back to what they were.
        # A token from before Phase 20 never touched either.
        if not data.get("variants_touched"):
            return
        await asyncio.to_thread(
            _restore_alternates, _variants_dir(slug), data.get("variants_backup"))
        if not data.get("created"):
            await _store.set_site_variants(slug, data.get("previous_variants"))

    if data.get("created"):
        # The launch report and provenance describe the build being undone.
        await asyncio.to_thread(_remove_side_files, site_dir)
        await asyncio.to_thread(_remove_site_dir, site_dir)
        await _restore_variants()
        await _store.delete_site(slug)
        await _restore_status()
        return ActionResult(True, f"removed the built site {slug!r}")

    await _restore_variants()
    if data.get("provenance_touched"):
        # The record that matches the files about to come back, so their
        # client reviews still read as the client's and ATLAS's own launch
        # pages stay refreshable on the next rebuild.
        await asyncio.to_thread(
            _restore_provenance, site_dir, data.get("provenance_backup"))
    if data.get("chat_touched"):
        # The chat answers from the facts of the build that is live again,
        # or not at all when that build had no chat.
        await asyncio.to_thread(
            _restore_side, sitechat.facts_path(site_dir), data.get("chat_backup"))
    backup_dir = data.get("backup_dir")
    if backup_dir and Path(backup_dir).exists():
        await asyncio.to_thread(_restore_backup, site_dir, backup_dir)
        await asyncio.to_thread(_refresh_report, site_dir)
        await _restore_status()
        return ActionResult(True, f"restored the previous build for {slug!r}")

    await asyncio.to_thread(_refresh_report, site_dir)
    await _restore_status()
    return ActionResult(True, f"nothing to restore for {slug!r}")
