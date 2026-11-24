"""ATLAS Academy support's knowledge base: the only thing support may answer from.

Miro, 2026-10-11 (Q&A): ATLAS answers product questions itself "from the
product files"; anything it cannot ground goes to him. This module is the
"product files" half: chunks of text, each tagged with the product it is
about, the source it came from and who may see it, and a retrieval that
says how well a question is covered.

Where the chunks come from, and nothing else:

* **The catalog** (`catalog.json`, read at runtime): each product's summary,
  what is inside, who it is for and not for, its FAQ, what you need (the
  "You need" / "Tested on" / "Claude plan" label rows), its refund terms and
  licence, and the price of every offer on sale (`Offer.buy_live`). An
  offer that is not on sale has no price here, so support can never quote one.
* **The store's published pages** -- terms of sale, refunds, privacy -- as
  `site._legal` renders them (read, never edited), so a refund or delivery
  answer is the store's own wording, never a paraphrase invented here.
* **The support policy and FAQ** written in this file from those same facts
  (access page, the `download_link_days` link life, how to get the link
  again, the 2-business-day reply, what support covers, how to report a
  broken workflow, custom builds as a paid ATLAS Agency service with no price).
* **The product files themselves**, from "ATLAS Vault/Products", through an
  allowlist (`SOURCES`): lessons, chapters, workbook parts, kit READMEs.
  Internal notes (BRIEF.md, VERIFY-LOG.md, Sales page.md, Selling on
  Shopify, Roadmap, Products index, FlyCheap, TikTok listings, frontmatter
  and [[wiki links]]) are never read. These are built on Miro's Mac by
  `python -m atlas.modules.academy.support_kb build <Products dir> <out>`
  into `data/academy/support_kb.json` (ACADEMY_SUPPORT_KB) and shipped like
  the product files -- paid lesson text stays out of git. With the file
  missing, support still answers from the catalog and the policy, and hands
  everything else to Miro; it never improvises.

**Scope.** A chunk is `public` (catalog, policy, store pages, a kit's
setup/start-here pages, the free kit) or `buyer` (the paid lessons and
chapters). The store's chat sees public chunks only. An email or form reply
may use a product's buyer chunks only when the address it goes to is the
address on a paid checkout for that product -- the reply goes only there,
so nobody receives text they did not buy.

**Retrieval** is Okapi BM25 over lightly stemmed words, pure Python, no
new dependency. `coverage` -- the share of the question's content words
found in the best chunk -- is the confidence the guards compare against
`ACADEMY_SUPPORT_MIN_COVERAGE`; below it, support says "unsure".
"""
from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path

from .catalog import Catalog, load, money

KB_ENV = "ACADEMY_SUPPORT_KB"
DEFAULT_KB = "data/academy/support_kb.json"
KB_VERSION = 1
CHUNK_CHARS = 1100
REPLY_DAYS = 2                       # Miro, 2026-10-11: "within 2 business days"
REPLY_DAYS_WORD = "two"              # as the pages write it (no stray figures)
REPLY_TZ = "America/New_York"

# ------------------------------------------------------------------ chunks


@dataclass(frozen=True)
class Chunk:
    id: str
    product: str          # catalog slug, or "" for the whole store
    source: str           # where the text was read: a vault path, catalog.json, a page
    title: str
    text: str
    scope: str = "public"  # public | buyer
    kind: str = "doc"      # doc | faq | policy | catalog | terms


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    coverage: float


# Customer-facing product files, relative to "ATLAS Vault/Products". A path
# that matches no pattern is never read. (slug, glob, scope)
SOURCES: tuple[tuple[str, str, str], ...] = (
    ("safe-ai-operator", "Build a Safe AI Operator/Lesson *.md", "buyer"),
    ("safe-ai-operator", "Build a Safe AI Operator/Bonus - From course to client.md",
     "buyer"),
    ("30-business-lessons", "30 Business Lessons.md", "buyer"),
    ("claude-obsidian-vault-kit", "Vault Kit/README.md", "public"),
    ("claude-obsidian-vault-kit", "Vault Kit/CLAUDE.md", "buyer"),
    ("ai-for-every-age", "AI for Every Age/free-kit/*.md", "public"),
    ("ai-for-every-age", "AI for Every Age/adults/*.md", "buyer"),
    ("ai-for-every-age", "AI for Every Age/seniors/*.md", "buyer"),
    ("local-business-automation-pack",
     "Local Business Automation Pack/guide/Chapter 0 - Start here.md", "public"),
    ("local-business-automation-pack",
     "Local Business Automation Pack/guide/Chapter 1 - Set up n8n with Docker.md",
     "public"),
    ("local-business-automation-pack", "Local Business Automation Pack/guide/*.md",
     "buyer"),
    ("local-business-automation-pack",
     "Local Business Automation Pack/workflows/README.md", "buyer"),
    ("local-business-automation-pack",
     "Local Business Automation Pack/workflows/docker-compose.yml", "buyer"),
    ("family-ai-scam-defence-workbook",
     "Family AI-Scam Defense Workbook/workbook/Part 0 - Start here.md", "public"),
    ("family-ai-scam-defence-workbook",
     "Family AI-Scam Defense Workbook/workbook/*.md", "buyer"),
    ("sell-with-ai", "Sell With AI/Lesson 0.1 - *.md", "public"),
    ("sell-with-ai", "Sell With AI/Lesson 0.3 - *.md", "public"),
    ("sell-with-ai", "Sell With AI/Lesson *.md", "buyer"),
)
# Never read, whatever a pattern above would match.
NEVER = ("BRIEF.md", "VERIFY-LOG.md", "Sales page.md", "Selling on Shopify.md",
         "Roadmap.md", "Products index.md", "FlyCheap Pro.md", "README.md.bak")
NEVER_DIRS = ("TikTok Shop listings", "build", "dist", "images", "shopify", "source")

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_WIKI_LINE = re.compile(r"^.*\[\[[^\]]+\]\].*$", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _clean_markdown(text: str) -> str:
    text = _FRONTMATTER.sub("", text.replace("\r\n", "\n"))
    text = _WIKI_LINE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split(text: str, limit: int = CHUNK_CHARS) -> list[tuple[str, str]]:
    """(section heading, text) pieces of at most `limit` characters, cut at
    headings first, then paragraphs."""
    sections: list[tuple[str, str]] = []
    marks = list(_HEADING.finditer(text))
    if not marks or marks[0].start() > 0:
        sections.append(("", text[: marks[0].start()] if marks else text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections.append((m.group(2).strip(), text[m.end():end]))
    out: list[tuple[str, str]] = []
    for heading, body in sections:
        paras = [p.strip() for p in body.split("\n\n") if p.strip()]
        piece = ""
        for para in paras:
            while len(para) > limit:            # one huge paragraph or table
                if piece:
                    out.append((heading, piece))
                    piece = ""
                cut = para.rfind(" ", 0, limit)
                cut = cut if cut > limit // 2 else limit
                out.append((heading, para[:cut].strip()))
                para = para[cut:].strip()
            if piece and len(piece) + len(para) + 2 > limit:
                out.append((heading, piece))
                piece = ""
            piece = f"{piece}\n\n{para}" if piece else para
        if piece:
            out.append((heading, piece))
    return [(h, t) for h, t in out if t.strip()]


def _allowed(rel: str) -> tuple[str, str] | None:
    """(slug, scope) for a vault path on the allowlist, first match wins."""
    parts = rel.split("/")
    if parts[-1] in NEVER or any(p in NEVER_DIRS for p in parts[:-1]):
        return None
    if any(p.startswith(".") for p in parts):
        return None
    for slug, pattern, scope in SOURCES:
        if fnmatch.fnmatchcase(rel, pattern):
            return slug, scope
    return None


def build_from_vault(products_dir: Path) -> list[Chunk]:
    """Every allowlisted file under "ATLAS Vault/Products", chunked. Blocking."""
    chunks: list[Chunk] = []
    for path in sorted(products_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(products_dir).as_posix()
        found = _allowed(rel)
        if found is None:
            continue
        slug, scope = found
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = raw if path.suffix in (".yml", ".yaml") else _clean_markdown(raw)
        name = path.stem
        for n, (heading, piece) in enumerate(_split(text)):
            title = f"{name}: {heading}" if heading and heading != name else name
            chunks.append(Chunk(id=f"vault:{slug}:{rel}#{n}", product=slug,
                                source=f"Products/{rel}", title=title[:160],
                                text=piece, scope=scope, kind="doc"))
    return chunks


def write_kb(chunks: Sequence[Chunk], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": KB_VERSION, "chunks": [asdict(c) for c in chunks]}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)


def kb_path() -> Path:
    return Path(os.getenv(KB_ENV, DEFAULT_KB))


def load_vault_chunks(path: Path | None = None) -> tuple[list[Chunk], str | None]:
    """(chunks, problem). A missing or unreadable file is ([], why): support
    then answers from the catalog and policy only, and says so in its check."""
    path = path or kb_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], f"no product-file KB at {path}"
    except (OSError, ValueError) as exc:
        return [], f"product-file KB at {path} unreadable: {type(exc).__name__}"
    if not isinstance(data, dict) or data.get("version") != KB_VERSION:
        return [], f"product-file KB at {path} has an unknown version"
    out: list[Chunk] = []
    for raw in data.get("chunks") or []:
        try:
            chunk = Chunk(**{k: str(raw[k]) for k in
                             ("id", "product", "source", "title", "text", "scope", "kind")})
        except (KeyError, TypeError):
            continue
        if chunk.scope in ("public", "buyer"):
            out.append(chunk)
    return out, None


# ------------------------------------------------------- catalog and policy

def _html_text(html: str) -> list[tuple[str, str]]:
    """(h2 heading, paragraph text) from one of the store's prose pages."""
    body = html.split('<div class="prose">', 1)[-1]
    body = re.sub(r"<form.*?</form>", " ", body, flags=re.DOTALL)
    body = body.split("</main>", 1)[0]
    out: list[tuple[str, str]] = []
    heading = ""
    for part in re.split(r"(<h2>.*?</h2>)", body, flags=re.DOTALL):
        m = re.fullmatch(r"<h2>(.*?)</h2>", part, flags=re.DOTALL)
        if m:
            heading = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            continue
        text = unescape(re.sub(r"<[^>]+>", " ", part))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append((heading, text))
    return out


def terms_chunks(cat: Catalog) -> list[Chunk]:
    """The store's own terms, refunds and privacy pages, as published."""
    from .site import _legal
    out: list[Chunk] = []
    for path, html in _legal(cat):
        page = path.split("/", 1)[0]
        if page not in ("terms", "refunds", "privacy"):
            continue
        for n, (heading, text) in enumerate(_html_text(html)):
            title = f"Store {page} page" + (f": {heading}" if heading else "")
            out.append(Chunk(id=f"terms:{page}#{n}", product="",
                             source=f"{cat.store_url}/{page}/", title=title, text=text,
                             kind="terms"))
    return out


def _prices(cat: Catalog, slug: str) -> str:
    p = cat.product(slug)
    if p is None:
        return ""
    live = [o for o in p.offers if o.buy_live]
    return "; ".join(f"{o.name}: {money(o.price_usd)}" for o in live)


def catalog_chunks(cat: Catalog) -> list[Chunk]:
    out: list[Chunk] = []
    for p in cat.products:
        src = "catalog.json"
        url = f"{cat.store_url}/{p.slug}/"

        def add(key: str, title: str, text: str, kind: str = "catalog", *,
                p_slug: str = p.slug, p_title: str = p.title, src: str = src) -> None:
            if text.strip():
                out.append(Chunk(id=f"catalog:{p_slug}:{key}", product=p_slug,
                                 source=src, title=f"{p_title}: {title}", text=text,
                                 kind=kind))

        add("about", "what it is", f"{p.title}. {p.summary} {p.intro} Page: {url}")
        inside = "; ".join(f"{t}: " + ", ".join(items) for t, items in p.inside)
        add("inside", "what's inside", inside)
        need = [f"{k}: {v}" for k, v in p.label]
        add("label", "format and what you need", ". ".join(need))
        add("audience", "who it's for", "Who it's for: " + "; ".join(p.audience)
            + (". Not for: " + "; ".join(p.not_for) if p.not_for else "")
            + (f". {p.notice}" if p.notice else ""))
        for n, (q, a) in enumerate(p.faq):
            add(f"faq{n}", q, f"{q} {a}", kind="faq")
        if p.refund:
            add("refund", "refund terms", f"Refund terms for {p.title}: {p.refund}",
                kind="terms")
        if p.licence:
            add("licence", "licence", f"Licence for {p.title}: {p.licence}")
        if prices := _prices(cat, p.slug):
            add("price", "price", f"Price of {p.title} on the store: {prices}. "
                f"Buy on {url}")
    b = cat.bundle
    if b.offer.buy_live:
        names = ", ".join(o.name for o in cat.bundle_offers())
        out.append(Chunk(id=f"catalog:{b.slug}:price", product="", source="catalog.json",
                         title=f"{b.title}: price",
                         text=f"{b.title}: {b.summary} It includes {names}. Price: "
                              f"{money(b.offer.price_usd)}. {b.terms_note} "
                              f"Page: {cat.store_url}/{b.slug}/"))
    return out


DEFAULT_SUPPORT_ADDRESS = "agency.sender@gmail.com"


def support_address() -> str:
    """ACADEMY_SUPPORT_ADDRESS, else the catalog's store.support_email."""
    env = os.getenv("ACADEMY_SUPPORT_ADDRESS", "").strip().lower()
    if env:
        return env
    try:
        from .catalog import load
        return load().support_email.strip().lower() or DEFAULT_SUPPORT_ADDRESS
    except Exception:  # noqa: BLE001
        return DEFAULT_SUPPORT_ADDRESS


def policy_chunks(cat: Catalog) -> list[Chunk]:
    """The support FAQ and policy, written in code from the catalog's facts.
    Every figure here is the catalog's or Miro's decision of 2026-10-11."""
    days = int(cat.store["download_link_days"])
    base = cat.store_url
    help_url = f"{base}/help/"
    contact = cat.contact_email
    support = support_address()
    faq = [
        ("access-page", "Where are my downloads after I pay?",
         ("After payment Stripe sends you to your access page, which lists your "
          "downloads. Keep the page bookmarked: it works for as long as your purchase "
          "is paid and not refunded.")),
        ("link-expired", "My download link expired. How do I get a new one?",
         (f"Each download link works for {days} days. Opening your access page again "
          "makes fresh download links, so an expired link is fixed by reopening the "
          "access page.")),
        ("lost-access", "I lost my access page or never got it.",
         (f"Recover it yourself at {base}/recover/ with the email your "
          "Stripe receipt was sent to and the receipt number on it. No receipt? Write to "
          f"{support} or use the form on {help_url} from the address you paid with; the "
          "access link is sent again to the email on your Stripe checkout only.")),
        ("download-trouble", "A download will not open or a zip will not unzip.",
         ("Download the file again from your access page on a computer, not a phone, "
          "and unzip it with your computer's own unzip tool. If it still fails, write "
          f"to {support} and say which file, which device and the exact error.")),
        ("refund-how", "How do I ask for a refund?",
         (f"Refund terms are set per title and listed on {base}/refunds/. Refund "
          f"requests are handled by Miro himself: email {contact} from the address you "
          "paid with and include your Stripe receipt number if you have it.")),
        ("reply-time", "How fast does support reply?",
         (f"ATLAS Academy support replies within {REPLY_DAYS_WORD} business days (Monday to "
          "Friday, New York time). Most product questions are answered sooner by the AI "
          "support assistant from the product files.")),
        ("ai-assistant", "Am I talking to a person?",
         ("No. This is the ATLAS Academy support assistant, an AI. It answers only from "
          "the product files and the store's published terms. Miro reviews anything it "
          "can't answer.")),
        ("scope", "What does support cover?",
         ("Support covers access and downloads, setting up the kits (Docker, n8n, "
          "Claude, Obsidian), and workflows or files in the kits that don't work as the "
          "guide says. It doesn't cover building something new for you: custom builds "
          "are a paid service.")),
        ("custom-work", "Can you build or set this up for me?",
         ("Custom builds and done-for-you setup are not part of a purchase. They are a "
          "paid service from ATLAS Agency, Miro's studio. Write with what you need and Miro "
          "replies with a quote.")),
        ("report-broken", "How do I report a broken workflow or error?",
         ("Say which product and which workflow or chapter, what you did step by step, "
          "what you expected, and the exact error text or a screenshot. For n8n "
          "workflows include your n8n version and whether it runs in Docker; for Claude "
          "or Obsidian, which app and plan. Never send passwords, API keys or tokens.")),
    ]
    out = [Chunk(id=f"policy:{key}", product="", source="ATLAS Academy support policy",
                 title=q, text=f"{q} {a}", kind="faq")
           for key, q, a in faq]
    return out


def faq_entries(cat: Catalog) -> list[tuple[str, str]]:
    """(question, answer) pairs for the Help page, the same words support uses."""
    out = []
    for c in policy_chunks(cat):
        q = c.title
        out.append((q, c.text[len(q):].strip()))
    return out


def store_chunks(cat: Catalog) -> list[Chunk]:
    return policy_chunks(cat) + catalog_chunks(cat) + terms_chunks(cat)


# ------------------------------------------------------------------ search

_STOP_WORDS = """a about above after again against all am an and any are as at be
because been before being below between both but by can cannot could did do does doing
don down during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other our ours out over own same she should so some such than that the
their them then there these they this those through to too under until up very was we
were what when where which while who whom why will with would you your yours yourself
hi hello hey thanks thank please regards dear miro atlas academy question help know
get got im ive dont cant wont doesnt isnt also still just really need want would like
one way thing things anything something"""
STOP = frozenset(_STOP_WORDS.split())

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def stem(word: str) -> str:
    for suffix in ("ingly", "edly", "ing", "ies", "ied", "es", "ed", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
    return word


def terms(text: str) -> list[str]:
    return [stem(t.replace("'", "")) for t in _TOKEN.findall((text or "").lower())
            if t.replace("'", "") not in STOP and len(t) > 1]


@dataclass
class Index:
    chunks: list[Chunk]
    k1: float = 1.4
    b: float = 0.75
    _docs: list[Counter[str]] = field(default_factory=list, repr=False)
    _df: Counter[str] = field(default_factory=Counter, repr=False)
    _avg: float = 1.0

    def __post_init__(self) -> None:
        for c in self.chunks:
            tf = Counter(terms(f"{c.title} {c.text}"))
            self._docs.append(tf)
            self._df.update(tf.keys())
        lengths = [sum(d.values()) for d in self._docs] or [1]
        self._avg = max(1.0, sum(lengths) / len(lengths))

    def vocabulary(self, chunk_ids: Iterable[str]) -> set[str]:
        wanted = set(chunk_ids)
        out: set[str] = set()
        for c, tf in zip(self.chunks, self._docs, strict=True):
            if c.id in wanted:
                out.update(tf)
        return out

    def search(self, query: str, *, products: Iterable[str] = (),
               buyer_products: Iterable[str] = (), k: int = 5) -> list[Hit]:
        """Best chunks for `query`. Public chunks always; a product's buyer
        chunks only when it is in `buyer_products`. `products`, when given,
        keeps store-wide chunks plus those products' own."""
        q = list(dict.fromkeys(terms(query)))
        if not q:
            return []
        keep = set(products)
        paid = set(buyer_products)
        n = len(self.chunks)
        scored: list[Hit] = []
        for c, tf in zip(self.chunks, self._docs, strict=True):
            if c.scope == "buyer" and c.product not in paid:
                continue
            if keep and c.product and c.product not in keep:
                continue
            length = sum(tf.values())
            score = 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * length / self._avg))
            if score > 0:
                coverage = sum(1 for t in q if t in tf) / len(q)
                scored.append(Hit(c, round(score, 3), round(coverage, 3)))
        scored.sort(key=lambda h: (h.score, h.coverage), reverse=True)
        return scored[:k]


_INDEX: tuple[tuple[str, float], Index, str | None] | None = None


def index(cat: Catalog | None = None) -> tuple[Index, str | None]:
    """The store's chunks plus the product-file KB, cached until the KB file
    or the catalog changes. (index, problem with the product-file KB)."""
    global _INDEX
    cat = cat or load()
    path = kb_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = -1.0
    key = (f"{path}|{id(cat.raw)}|{json.dumps(cat.store, sort_keys=True)}", stamp)
    if _INDEX is not None and _INDEX[0] == key:
        return _INDEX[1], _INDEX[2]
    vault, problem = load_vault_chunks(path)
    built = Index(store_chunks(cat) + vault)
    _INDEX = (key, built, problem)
    return built, problem


def reset_cache() -> None:
    global _INDEX
    _INDEX = None


# ------------------------------------------------------------------ CLI

def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "build":
        print("usage: python -m atlas.modules.academy.support_kb build "
              "<ATLAS Vault/Products dir> <out.json>", file=sys.stderr)
        return 2
    chunks = build_from_vault(Path(argv[2]))
    write_kb(chunks, Path(argv[3]))
    by: Counter[str] = Counter(f"{c.product}:{c.scope}" for c in chunks)
    print(f"wrote {len(chunks)} chunks to {argv[3]}")
    for key, n in sorted(by.items()):
        print(f"  {key}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
