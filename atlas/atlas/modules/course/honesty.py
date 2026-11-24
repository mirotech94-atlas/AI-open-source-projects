"""What a course post may never say, in every language it is posted in, for
every product in the catalog.

Checked when a post is drafted and again when it is published, on the English
text and on every translation:

  * **No income or earnings claims.** The courses promise safety, never money.
    `content.guard.INCOME_WORDS` covers English; Spanish, Portuguese and
    Arabic are listed here, because a translation is exactly where an English
    filter stops seeing.
  * **No fake testimonials, reviews or ratings.** Nobody has been quoted,
    rated or counted, so no post says anyone was: no "students say", no
    stars, no "trusted by", no attributed quote.
  * **No fake scarcity, countdowns or discounts.** The products are downloads;
    they cannot sell out, nothing ends tonight, and nothing is "50% off". The
    only "was" price is the store's own compare-at, read live.
  * **No grades, accuracy or accreditation promises.** No "better grades",
    no "always accurate", no "accredited" or "certified" (the adult course's
    certificate records that you finished it, nothing more).
  * **Never "Claude for kids".** Claude is for adults 18+; no post says or
    implies a child can use it.
  * **For the 60+ guide and the free kit (`no_fear`, `strict_numbers`):** no
    fear words, and no digit anywhere except inside an excerpt copied word
    for word from the product's own lesson files (`data.verbatim`), which the
    post names as its source -- so no scam statistic can be invented.
  * **The price is the one read live.** Any money figure in the text must be
    exactly the words of the live read (`$20, was $49`); the free kit names
    no figure at all, only `catalog.FREE_WORDS`.
  * **The age notice.** When the live product page carries "Sold to adults
    18+", the post carries `catalog.AGE_NOTICE`.
  * **Labelled as his own.** Every post carries its product's fixed label in
    its language (`label_for`), written here, not by a model.

A hit is a refusal naming the phrase. False positives cost one redraft; a
false negative is a public post under the owner's name making a claim he never
made.
"""
from __future__ import annotations

import re

from ..content import guard
from . import catalog, data
from .catalog import OPERATOR

LANGUAGES: dict[str, str] = {"en": "English", "es": "Spanish",
                             "pt": "Portuguese", "ar": "Arabic"}

# The own-course label, one fixed line per language, appended in code after
# any translation. The Spanish, Portuguese and Arabic lines were written for
# this file and reviewed with it; they are not model output.
LABELS: dict[str, str] = {
    "en": "This is my own course. -- the owner",
    "es": "Es mi propio curso. -- the owner",
    "pt": "É o meu próprio curso. -- the owner",
    "ar": "هذه دورتي الخاصة. -- ميرو",
}

_I = re.IGNORECASE

INCOME: dict[str, re.Pattern[str]] = {
    "en": guard.INCOME_WORDS,
    "es": re.compile(
        r"(ganar dinero|gana dinero|ganancias?|ingresos?\b|ingreso pasivo|"
        r"hazte rico|hacerte rico|enriquec|libertad financiera|millonari|"
        r"dinero extra|dinero f[aá]cil|renta pasiva|beneficios econ[oó]micos|"
        r"garantizad)", _I),
    "pt": re.compile(
        r"(ganhar dinheiro|ganhe dinheiro|ganhos\b|renda passiva|renda extra|"
        r"fique rico|ficar rico|enriquec|liberdade financeira|milion[aá]ri|"
        r"dinheiro f[aá]cil|lucros?\b|faturamento|garantid)", _I),
    "ar": re.compile(
        r"(أرباح|ارباح|ربح|اربح|الربح|دخل سلبي|دخل إضافي|دخل اضافي|مصدر دخل|"
        r"ثراء|الثراء|ثروة|مليونير|الحرية المالية|مال سهل|مضمون)"),
}

URGENCY: dict[str, re.Pattern[str]] = {
    "en": re.compile(
        r"(\bhurry\b|limited[- ]time|\bonly \d+ (?:left|spots?|seats?|copies)|"
        r"last chance|today only|ends (?:tonight|today|soon|at midnight)|"
        r"count ?down|\bact now\b|don'?t miss|before it'?s gone|while (?:stocks?|"
        r"supplies) last|(?:offer|sale|price|deal) expires|expires (?:tonight|"
        r"today|soon|at midnight)|selling out|sold out|spots? left|"
        r"seats? left|few left|now or never|flash sale|\bdeadline\b|"
        r"price goes up|last day|final hours?|[⏰⏳⌛])", _I),
    "es": re.compile(
        r"(date prisa|apres[uú]rate|tiempo limitado|[uú]ltima oportunidad|"
        r"solo hoy|s[oó]lo hoy|quedan pocos|plazas limitadas|termina hoy|"
        r"cuenta regresiva|no te lo pierdas|oferta rel[aá]mpago|"
        r"[uú]ltimas horas|se agota|[⏰⏳⌛])", _I),
    "pt": re.compile(
        r"(\bcorra\b|tempo limitado|[uú]ltima chance|[uú]ltima oportunidade|"
        r"s[oó] hoje|apenas hoje|restam poucas|vagas limitadas|termina hoje|"
        r"contagem regressiva|n[aã]o perca|oferta rel[aâ]mpago|"
        r"[uú]ltimas horas|esgotando|[⏰⏳⌛])", _I),
    "ar": re.compile(
        r"(أسرع|اسرع|لفترة محدودة|وقت محدود|آخر فرصة|اخر فرصة|اليوم فقط|"
        r"عدد محدود|ينتهي اليوم|العد التنازلي|لا تفوت|عرض خاطف|الساعات الأخيرة|"
        r"[⏰⏳⌛])"),
}

TESTIMONIAL: dict[str, re.Pattern[str]] = {
    "en": re.compile(
        r"(testimonial|\breviews?\b|\breviewed\b|\d(?:\.\d)? ?stars?\b|"
        r"\bratings?\b|\d(?:\.\d)? ?(?:/|out of) ?(?:5|10)\b|"
        r"five[- ]stars?|\brated\b|\b(?:students|customers|buyers|people|"
        r"readers|users) (?:say|said|love|loved|are saying)|"
        r"\d[\d,]*\+? (?:students|customers|buyers|people|owners) (?:have )?"
        r"(?:joined|enrolled|bought|signed up)|best[- ]?sell|trusted by|"
        r"[★⭐])", _I),
    "es": re.compile(
        r"(testimonio|rese[ñn]a|opiniones de|estrellas|(?:alumnos|estudiantes|"
        r"clientes) (?:dicen|opinan|aman)|m[aá]s vendido|[★⭐])", _I),
    "pt": re.compile(
        r"(depoimento|avalia[çc][aãõ]|estrelas|(?:alunos|estudantes|clientes) "
        r"(?:dizem|amam|adoram)|mais vendido|[★⭐])", _I),
    "ar": re.compile(
        r"(شهادة|شهادات|تقييم|مراجعات|نجوم|يقول الطلاب|يقول العملاء|"
        r"الأكثر مبيعا|الأكثر مبيعًا|[★⭐])"),
}

# English-only lists, applied to every text: a translation is only allowed
# for the Operator, and its Spanish/Portuguese/Arabic lists above cover the
# claims a translation is likeliest to add.
_E = re.IGNORECASE
DISCOUNT = re.compile(
    r"(\d+ ?% off|half[- ]price|\bsave \d|\bsave \$|regular(?:ly)? price|"
    r"\boriginally\b|\bcompare at\b|\bdiscount(?:ed)?\b|\bcoupon\b|"
    r"\bpromo code\b)", _E)
PROMISES = re.compile(
    r"(better grades|(?:improve|raise|boost)s? (?:your |their )?grades|"
    r"higher grades|straight a'?s\b|ace (?:the|your|every) (?:exam|test|class)|"
    r"pass (?:your|the) exams?|100 ?% accura|always (?:right|accurate|correct)|"
    r"never (?:wrong|makes? (?:a )?mistakes?)|perfectly accurate|error[- ]free|"
    r"guaranteed? (?:accura|correct|results?)|no more mistakes)", _E)
ACCREDITED = re.compile(
    r"(accredit|\bcertified\b|\bcertification|\bqualifications?\b|\bdiploma|"
    r"\bCEUs?\b|\bCPD\b|continuing education|officially recogni[sz]ed|"
    r"\bdegree\b)", _E)
KIDS = re.compile(
    r"(claude for (?:kids|children|teens|students)|"
    r"(?:kids|children|teens|your child|a child|your kids) (?:can|may|should) "
    r"use (?:claude|ai|it)|\bfor (?:your )?kids\b|\bfor (?:your )?children\b|"
    r"\bfor teens\b|kid[- ]friendly|child[- ]friendly|family[- ]friendly ai|"
    r"ages? \d+ (?:and|to) up)", _E)
FEAR = re.compile(
    r"(terrif|horrif|frighten|nightmare|epidemic|\bvictims?\b|"
    r"before it'?s too late|lose everything|life savings|"
    r"you(?:'re| are| could be) next|shocking|\bpanic|prey(?:s|ing)? on|"
    r"predators?|devastat|\bscary\b|\bscared\b|\bafraid\b|"
    r"could happen to you|don'?t let (?:this|it) happen|\bsinister\b|"
    r"\bdanger(?:ous)?\b|\bthreats?\b)", _E)

# Every language-blind pattern a card is vetted against before it exists.
BLIND: tuple[re.Pattern[str], ...] = (DISCOUNT, PROMISES, ACCREDITED, KIDS)

_DIGIT = re.compile(r"\d")
_URL = re.compile(r"https?://\S+")
_LESSON_REF = re.compile(r"\b(?:lesson|printable)s? [A-Z]?\d+(?:\.\d+)?", _E)

# An attributed quote: a quoted sentence followed, on the same line, by a dash
# and a name. (A quoted line ending a list, with "- next item" below it, is
# not an attribution.)
_QUOTE = re.compile(r"[\"“«][^\"”»\n]{8,}[\"”»][ \t]*[-–—]{1,2}[ \t]*\w")

# Money in the text, any language: a currency sign or word next to a number.
MONEY = re.compile(
    r"([$€£¥]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:usd|dollars?|"
    r"d[oó]lares?|"
    r"d[oó]lar|دولار|\$))", _I)


def _hit(patterns: dict[str, re.Pattern[str]], language: str, text: str
         ) -> str | None:
    """Every language's list is applied to every text: an English word left
    untranslated in a Spanish post is still that word."""
    for lang in dict.fromkeys([language, *patterns]):
        found = patterns[lang].search(text or "")
        if found:
            return found.group(0).strip()
    return None


def label_for(product: str, language: str) -> str:
    """The fixed own-product label: the Operator's in its four languages,
    every other product's in English (the only language it is posted in)."""
    if product == OPERATOR:
        return LABELS[language]
    found = catalog.get(product)
    return found.label if found else LABELS["en"]


def _without(text: str, spans: list[str]) -> str:
    """`text` with every span removed, longest first, ignoring case."""
    out = text
    for span in sorted({s for s in spans if s}, key=len, reverse=True):
        out = re.sub(re.escape(span), " ", out, flags=re.IGNORECASE)
    return out


def _fixed(product: str, language: str) -> list[str]:
    """The strings code writes into every post: titles, label, age notice."""
    fixed = [catalog.AGE_NOTICE, catalog.FREE_WORDS, label_for(product, language)]
    for p in catalog.CATALOG.values():
        fixed += [p.title, p.short]
    return fixed


def _quoted(product: str) -> list[str]:
    try:
        return [q["text"] for q in data.quotes(product)]
    except KeyError:
        return []


def number_refusal(text: str, product: str, language: str = "en",
                   price_words: str | None = None) -> str | None:
    """For a `strict_numbers` product: a digit outside every verbatim, sourced
    excerpt, the fixed strings, the live price's words, the link and the
    lesson references. Pure. (Whether the price is the live read is the
    money rule's to say.)"""
    rest = _URL.sub(" ", text)
    rest = _without(rest, data.verbatim(product) + _fixed(product, language)
                    + ([price_words] if price_words else []))
    rest = _LESSON_REF.sub(" ", rest)
    if (hit := _DIGIT.search(rest)) is not None:
        around = rest[max(0, hit.start() - 20):hit.end() + 20].strip()
        return (f"it has a number that is not quoted from the lesson files "
                f"({around!r}); every figure must be a verbatim excerpt with its "
                f"source")
    return None


def refusal(text: str, language: str, price_words: str | None,
            product: str = OPERATOR, *, age_notice: bool = False) -> str | None:
    """A reason this post may not go out, or None. `price_words` is the live
    read's wording ("$20, was $49", or the free kit's fixed words), or None
    when the price was not read; `age_notice` is whether the live product
    page carries "Sold to adults 18+"."""
    found = catalog.get(product)
    if found is None:
        return f"no product {product!r} in the catalog"
    if language not in LANGUAGES:
        return f"unknown language {language!r}"
    if language not in found.languages:
        return (f"{found.short} is posted in {', '.join(found.languages)} only, "
                f"not {LANGUAGES[language]}")
    if not (text or "").strip():
        return "the post is empty"
    if (hit := _hit(INCOME, language, text)) is not None:
        return (f"it makes an income or earnings claim ({hit!r}); the course "
                f"promises safety, never money")
    if (hit := _hit(TESTIMONIAL, language, text)) is not None:
        return (f"it reads as a testimonial or review ({hit!r}); nobody has "
                f"been quoted or rated, so no post says anyone was")
    if _QUOTE.search(text):
        return "it carries an attributed quote; there are no testimonials to quote"
    if (hit := _hit(URGENCY, language, text)) is not None:
        return (f"it uses urgency or scarcity ({hit!r}); the course is a "
                f"download that cannot sell out, and nothing is ending")
    if (hit := DISCOUNT.search(text)) is not None:
        return (f"it claims a discount ({hit.group(0).strip()!r}); the only "
                f"\"was\" price is the store's own compare-at, read live")
    if (hit := PROMISES.search(text)) is not None:
        return (f"it promises grades or accuracy ({hit.group(0).strip()!r}); AI "
                f"can be wrong, and every lesson teaches checking it")
    if (hit := ACCREDITED.search(text)) is not None:
        return (f"it claims accreditation ({hit.group(0).strip()!r}); the courses "
                f"are not accredited and are not a qualification")
    if (hit := KIDS.search(text)) is not None:
        return (f"it implies a child can use Claude ({hit.group(0).strip()!r}); "
                f"Claude is for adults 18+")
    if found.no_fear and (hit := FEAR.search(text)) is not None:
        return (f"it uses fear ({hit.group(0).strip()!r}); {found.short} is "
                f"promoted calmly, never by frightening people")
    if found.strict_numbers and (why := number_refusal(
            text, product, language, price_words)):
        return why
    figures = [m.group(0) for m in MONEY.finditer(_without(text, _quoted(product)))]
    if figures:
        if price_words is None:
            return (f"it names a price ({figures[0]!r}) but the price was not "
                    f"read from the store")
        allowed = {m.group(0) for m in MONEY.finditer(price_words)}
        stray = [f for f in figures if f not in allowed]
        if stray or price_words not in text:
            return (f"its price ({(stray or figures)[0]!r}) is not the live "
                    f"read ({price_words!r})")
    if age_notice and catalog.AGE_NOTICE not in text:
        return (f"the product page carries the age notice, and the post does not "
                f"({catalog.AGE_NOTICE!r})")
    if label_for(product, language) not in text:
        return f"it is not labelled as the owner's own {found.noun}"
    return None
