"""A course post's words: composed in code in English, translated by a model.

The English text is built here from the product's file in `products/` -- a
lesson's hook and line, the product's title, the live price, the tagged link,
the age notice and the fixed own-product label. No model writes it, so nothing
in it can be a claim nobody made. For the AI for Every Age products every hook
and line is a sentence copied word for word from a lesson file; the Operator's
come from Miro's outline.

A Spanish, Portuguese or Arabic post (the Operator's feed posts only) is the
same English text translated by the model, and the card says so ("translation
drafted by the model -- review it"). The model never sees a price or a link: it
is given the text with `{PRICE}`, `{TITLE}` and `{LESSON}` placeholders, and
code puts the figures back afterwards. A translation that drops or repeats a
placeholder, invents a number, a currency sign, a link or a hashtag is
refused, and so is one the honesty rules refuse (`honesty.refusal`).

Three shapes, by where the post goes (`playbook.md` maps each platform to one):
  `social`   -- a feed post: hook, value line, its source, price or "free",
                link (LinkedIn, Facebook, Threads, Mastodon, Telegram)
  `short`    -- a short-limit feed: hook, title and price, link (X, Bluesky)
  `article`  -- one SEO blog draft per product for the Shopify blog, English
                only: a title (<= 60 characters), a meta description (<= 155)
                and 700-1,200 words made of the product's own lesson excerpts,
                word for word, with the product page linked in the text and in
                one call to action (`article_refusal`).
"""
from __future__ import annotations

import re

from . import catalog, data, honesty
from .catalog import OPERATOR

FORMATS: tuple[str, ...] = ("social", "short", "article")

PLACEHOLDERS: tuple[str, ...] = ("{PRICE}", "{TITLE}", "{LESSON}")
_BRACED = re.compile(r"\{[A-Z_]+\}")
_DIGITS = re.compile(r"\d+")
_FORBIDDEN = re.compile(r"(https?://|www\.|[$€£¥#@]|\.com\b|utm_)", re.IGNORECASE)

TITLE_MAX = 60
META_MAX = 155
WORDS_MIN, WORDS_MAX = 700, 1200
_WORD = re.compile(r"\S+")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def ref_for(product: str, item: dict[str, str]) -> str:
    """How a post names its source: `lesson 6.2, "Voice-clone and ..."`."""
    kind = "printable" if product == "scam_kit" else "lesson"
    return f"{kind} {item['id']}, \"{item['title']}\""


def template(fmt: str, item: dict[str, str], *, has_price: bool,
             product: str = OPERATOR) -> str:
    """The English feed text with placeholders, before the link and label."""
    if fmt == "article":
        raise ValueError("an article is built by `article`, never translated")
    if fmt not in ("social", "short"):
        raise ValueError(f"unknown format {fmt!r}")
    price = " {PRICE}." if has_price else ""
    if product == OPERATOR:
        if fmt == "short":
            if has_price:
                return f"{item['hook']}\nMy course, {{TITLE}}: {{PRICE}}."
            return f"{item['hook']}\nMy course: {{TITLE}}."
        return (f"{item['hook']}\n\n{item['line']}\n\n"
                f"Lesson {{LESSON}} of my course, \"{{TITLE}}\".{price}")
    if fmt == "short":
        return (f"{_cap(item['hook'])}\n{{TITLE}}: {{PRICE}}." if has_price
                else f"{_cap(item['hook'])}\n{{TITLE}}.")
    return (f"{_cap(item['hook'])}\n\n{item['line']}\n\n"
            f"From {ref_for(product, item)}, in {{TITLE}}.{price}")


def title_for(fmt: str, item: dict[str, str], product: str = OPERATOR) -> str:
    if product != OPERATOR or fmt == "short":
        return data.product(product)["short"]
    return data.product(product)["title"]


def fill(text: str, fmt: str, item: dict[str, str], price_words: str | None,
         product: str = OPERATOR) -> str:
    """The placeholders put back, in code."""
    out = text.replace("{TITLE}", title_for(fmt, item, product)).replace(
        "{LESSON}", item["id"])
    if "{PRICE}" in out:
        if price_words is None:
            raise ValueError("a {PRICE} slot with no price read")
        out = out.replace("{PRICE}", price_words)
    return out


def finish(body: str, fmt: str, language: str, link: str,
           product: str = OPERATOR, *, age_notice: bool = False) -> str:
    """The link, the age notice (when the product page carries it), the
    own-product label and (for feeds) the hashtags, appended in code after
    any translation. An article carries its links in its own text."""
    label = honesty.label_for(product, language)
    if age_notice:
        label = f"{catalog.AGE_NOTICE}\n{label}"
    if fmt == "article":
        return f"{body}\n\n{label}"
    if fmt == "short":
        return f"{body}\n{link}\n{label}"
    return f"{body}\n{link}\n\n{label}\n{data.hashtags(product)}"


# ------------------------------------------------------------------ article

def article(item: dict[str, str], price_words: str | None, link: str,
            product: str = OPERATOR) -> str:
    """The Shopify blog draft for `product`, in English, built from its blog
    sections -- the lesson `item`'s own section first when it has one. Every
    paragraph under a lesson heading is an excerpt, word for word, followed
    by the lesson it came from; the rest is the fixed framing written here."""
    p = catalog.get(product)
    if p is None:
        raise ValueError(f"no product {product!r} in the catalog")
    blog = data.blog(product)
    lessons = {x["id"]: x for x in data.lessons(product)}
    sections = sorted(blog["sections"], key=lambda s: s["lesson"] != item["id"])
    ask = "download it" if p.lead_magnet else "decide"
    kind = "printable" if product == "scam_kit" else "lesson"
    parts = [f"Title: {blog['title']}", f"Meta description: {blog['meta']}", "",
             _cap(item["hook"]), "",
             (f"Below are excerpts from my {p.noun}, {p.short}, copied word for "
              f"word from its {kind}s, so you can see how it teaches before you "
              f"{ask}. The {p.noun} is here: {link}")]
    for sec in sections:
        source = lessons.get(sec["lesson"])
        named = ref_for(product, source) if source else f"{kind} {sec['lesson']}"
        parts += ["", f"## {sec['heading']}", "", sec["text"].strip()]
        # A quoted figure goes out whole, with the source it carries, under
        # the excerpt of the file it was copied from.
        parts += [x for q in data.quotes(product) if q["source"] == sec["source"]
                  for x in ("", q["text"])]
        parts += ["", f"(From {named}, in {p.short}.)"]
    if price_words is None:
        cta = f"Get {p.short}: {link}"
    elif p.lead_magnet:
        cta = f"Get the {p.noun}. {price_words}: {link}"
    else:
        cta = f"Get {p.short}. It is {price_words} on my store: {link}"
    parts += ["", f"## Get {p.short}", "", cta]
    return "\n".join(parts)


def article_parts(text: str) -> tuple[str | None, str | None, str]:
    """(title, meta description, body) of an article draft."""
    lines = (text or "").split("\n")
    title = (lines[0][len("Title: "):].strip()
             if lines and lines[0].startswith("Title: ") else None)
    meta = (lines[1][len("Meta description: "):].strip()
            if len(lines) > 1 and lines[1].startswith("Meta description: ") else None)
    return title, meta, "\n".join(lines[2:])


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


def article_refusal(text: str, link: str | None = None) -> str | None:
    """Why an article draft may not go to Miro, or None: the SEO limits and
    the internal links to the product. Pure."""
    title, meta, body = article_parts(text)
    if not title:
        return "the article has no title line"
    if len(title) > TITLE_MAX:
        return f"the title is {len(title)} characters; at most {TITLE_MAX}"
    if not meta:
        return "the article has no meta description"
    if len(meta) > META_MAX:
        return f"the meta description is {len(meta)} characters; at most {META_MAX}"
    n = word_count(body)
    if not WORDS_MIN <= n <= WORDS_MAX:
        return f"the article is {n} words; it must be {WORDS_MIN}-{WORDS_MAX}"
    if link is not None and body.count(link) < 2:
        return ("the article does not link to the product page both in its text "
                "and in its call to action")
    return None


# ------------------------------------------------------------------ translation

def placeholder_refusal(source: str, translated: str) -> str | None:
    """Why a translation cannot be used, or None: every placeholder kept once,
    the same numbers as the source and no others, and nothing the model was
    told not to add."""
    if not translated.strip():
        return "the translation came back empty"
    for ph in PLACEHOLDERS:
        want, got = source.count(ph), translated.count(ph)
        if want != got:
            return (f"the translation has {got} {ph} placeholder(s), the "
                    f"English has {want}")
    stray = set(_BRACED.findall(translated)) - set(PLACEHOLDERS)
    if stray:
        return f"the translation invented placeholder(s) {sorted(stray)}"
    bare = _BRACED.sub(" ", translated)
    if sorted(_DIGITS.findall(bare)) != sorted(_DIGITS.findall(_BRACED.sub(" ", source))):
        return "the translation's numbers are not the English text's numbers"
    if (hit := _FORBIDDEN.search(bare)) is not None:
        return (f"the translation added {hit.group(0)!r}; prices, links and "
                f"hashtags are put in by code")
    return None


SYSTEM = """
You translate one short marketing text for Miro's own online course from
English into {language}. Translate faithfully and naturally; do not add, drop
or strengthen anything.

Rules:
- Keep every placeholder exactly as written ({{PRICE}}, {{TITLE}}, {{LESSON}}),
  each exactly as many times as in the English.
- Keep every number exactly as written, in Western digits (0-9).
- Add nothing: no prices, no currency signs, no links, no hashtags, no emojis,
  no claims about money, results, reviews, other people, or time running out.
- Keep line breaks and list dashes where they are.
- Output only the translation, no notes.
""".strip()


async def translate(source: str, language: str) -> tuple[str | None, str | None]:
    """(translation, None) or (None, reason). The source is ATLAS's own text,
    composed in code; what comes back is checked before anything uses it."""
    from ...kernel.llm import Message, Need, think

    name = honesty.LANGUAGES.get(language)
    if name is None or language == "en":
        return None, f"no translation to {language!r}"
    try:
        completion = await think(Need.DRAFT, SYSTEM.format(language=name),
                                 [Message("user", source)], max_tokens=1800)
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return None, f"the translation model was unreachable: {explain(exc)}"
    text = (completion.text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].strip()
    if (why := placeholder_refusal(source, text)) is not None:
        return None, why
    return text, None


async def compose(fmt: str, item: dict[str, str], language: str,
                  price_words_by_lang: dict[str, str | None], link: str,
                  product: str = OPERATOR, *, age_notice: bool = False
                  ) -> tuple[dict[str, str] | None, str | None]:
    """{"text", "english", "translated"} or (None, reason). `english` is the
    English post, shown on a translation's card for Miro to compare."""
    if fmt not in FORMATS:
        return None, f"unknown format {fmt!r}"
    if language not in honesty.LANGUAGES:
        return None, f"unknown language {language!r}"
    found = catalog.get(product)
    if found is None:
        return None, f"no product {product!r} in the catalog"
    if language not in found.languages:
        return None, (f"{found.short} is posted in {', '.join(found.languages)} "
                      f"only")
    en_words = price_words_by_lang.get("en")
    if fmt == "article":
        if language != "en":
            return None, "the Shopify blog article is drafted in English only"
        text = finish(article(item, en_words, link, product), fmt, "en", link,
                      product, age_notice=age_notice)
        if (why := article_refusal(text, link)) is not None:
            return None, f"the article: {why}"
        if (why := honesty.refusal(text, "en", en_words, product,
                                   age_notice=age_notice)):
            return None, f"the article: {why}"
        return {"text": text, "english": text, "translated": ""}, None
    has_price = en_words is not None
    source = template(fmt, item, has_price=has_price, product=product)
    english = finish(fill(source, fmt, item, en_words, product), fmt, "en", link,
                     product, age_notice=age_notice)
    if (why := honesty.refusal(english, "en", en_words, product,
                               age_notice=age_notice)):
        return None, f"the English text: {why}"
    if language == "en":
        return {"text": english, "english": english, "translated": ""}, None
    translated, why = await translate(source, language)
    if translated is None:
        return None, f"the {honesty.LANGUAGES[language]} translation: {why}"
    words = price_words_by_lang.get(language)
    text = finish(fill(translated, fmt, item, words, product), fmt, language, link,
                  product, age_notice=age_notice)
    if (why := honesty.refusal(text, language, words, product,
                               age_notice=age_notice)) is not None:
        return None, f"the {honesty.LANGUAGES[language]} translation: {why}"
    return {"text": text, "english": english, "translated": "model"}, None
