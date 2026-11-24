"""No real brand, no real person, in anything ATLAS asks a video or image
model to make for the AI-stories account.

Miro's rule (Task 55): the stories are fiction, and a prompt that names a
real brand or a real person is refused, not softened. This is deliberately
simple and says what it caught, so a false positive is one glance to
understand and one edit to fix:

  * a short list of brands and public figures a story model is most likely
    to reach for, matched as whole words only when written as a NAME: with
    a capital letter, or followed by ™ / ®. The names that are also
    ordinary words (apple, amazon, coke, drake...) must be capitalised
    somewhere other than the start of a sentence, or carry ™ / ®: "apple
    pie" and "Apple pie was warm." pass, "an Apple watch" does not;
  * a trademark sign (™ ® ©) anywhere;
  * a word with a capital letter inside it after a lower-case one
    (iPhone, PlayStation, McDonald, YouTube) -- the way product names are
    spelled and ordinary story words are not.

It is a guard on the prompt, not a content policy engine: a brand it has
never heard of, spelled like an ordinary word, gets through. The drafting
prompt asks for none in the first place; this is the code half of that.
"""
from __future__ import annotations

import re

from .. import store_origin

BRANDS: tuple[str, ...] = (
    "adidas", "amazon", "apple", "audi", "barbie", "bmw", "bugatti",
    "burger king", "chevrolet", "chevy", "ford", "honda", "hyundai",
    "lamborghini", "maybach", "nissan", "rolls-royce", "rolls royce",
    "chanel", "coca-cola", "coke", "disney", "dior", "facebook", "ferrari",
    "gucci", "google", "gopro", "hello kitty", "ikea", "instagram", "kfc",
    "lego", "louis vuitton", "marvel", "mcdonald's", "mcdonalds",
    "mercedes", "microsoft", "netflix", "nike", "nintendo", "pepsi",
    "pixar", "pokemon", "pokémon", "porsche", "prada", "red bull", "rolex",
    "samsung", "sony", "spotify", "starbucks", "tesla", "tiktok", "toyota",
    "uber", "walmart", "youtube",
)

PEOPLE: tuple[str, ...] = (
    "barack obama", "beyonce", "beyoncé", "bill gates", "cristiano ronaldo",
    "donald trump", "drake", "elon musk", "jeff bezos", "joe biden",
    "kanye west", "kim kardashian", "king charles", "lionel messi",
    "mark zuckerberg", "mrbeast", "oprah", "rihanna", "taylor swift",
)

# Listed names that are also everyday words: capitalised mid-sentence, or
# marked, before they count.
COMMON_WORDS = frozenset({"apple", "amazon", "coke", "drake", "uber", "tesla",
                          "oprah", "dove", "shell", "mercedes"})

def _names_pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"(?<![\w'])(" + "|".join(re.escape(n) for n in sorted(
            names, key=len, reverse=True)) + r")(?![\w])([™®])?",
        re.IGNORECASE)


_LISTED = _names_pattern(BRANDS + PEOPLE)
_BRANDS_ONLY = _names_pattern(BRANDS)
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n\s*|[\"'“‘(]\s*)$")


def _listed(text: str) -> list[str]:
    return _listed_in(text, BRANDS + PEOPLE)


def _listed_in(text: str, names: tuple[str, ...]) -> list[str]:
    pattern = _LISTED if names == BRANDS + PEOPLE else (
        _BRANDS_ONLY if names == BRANDS else _names_pattern(names))
    found = []
    for match in pattern.finditer(text or ""):
        word, marked = match.group(1), bool(match.group(2))
        if marked:
            found.append(match.group(0))
            continue
        # An everyday word ("apple", "amazon") is a brand only when it is
        # capitalised somewhere other than a sentence start.
        everyday = word.lower() in COMMON_WORDS
        if everyday and (not word[:1].isupper()
                         or _SENTENCE_START.search(text[:match.start()])):
            continue
        # Any other listed name is caught in any case: "nike shoes",
        # "taylor swift sang" name the brand and the person all the same.
        found.append(word)
    return found
_TRADEMARK = re.compile(r"\S*[™®©]\S*")
# A lower-case letter followed by a capital inside one word: iPhone,
# PlayStation, McDonald, YouTube. Not "O'Neil" (the apostrophe breaks it),
# not an ALL-CAPS word, not a sentence-initial capital.
_INNER_CAPITAL = re.compile(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z]*\b")
_HASHTAG = re.compile(r"(?<!\w)#\w+")
# Final review M1: a video, a Short or a playlist, or our store -- never any
# YouTube path (a creator's /@Handle is a name).
_LINK = re.compile(
    r"https?://youtu\.be/[\w-]+"
    r"|https?://www\.youtube\.com/(?:watch\?v=[\w-]+|shorts/[\w-]+"
    r"|playlist\?list=[\w-]+)"
    r"|https?://" + re.escape(store_origin.host()) + r"/\S+")


def caught(text: str) -> list[str]:
    """What in `text` looks like a real brand or person. Empty is clean."""
    found: list[str] = []
    # A hashtag is written in CamelCase so it can be read (#SmallBusinessIdeas,
    # #ServiceBusiness): that is a tag's spelling, not a brand's (seen live
    # 2026-10-05: every business lesson failed the gate on its own tags). A
    # LISTED brand or person inside a tag (#Nike) is still caught above.
    # A link to our own video or store is an address, not a name (2026-10-13,
    # growth plan task 7): a teaching Short links its full video, and a
    # YouTube id is mixed case (youtu.be/iRyhxkLk-Yo). Only a video, Short or
    # playlist link on YouTube and our store's links, and only the
    # inner-capital rule skips them; any other link (a channel's /@Handle
    # included) is read as text, and a LISTED brand in a link (nike.com) is
    # still caught above.
    plain = _LINK.sub(" ", _HASHTAG.sub(" ", text or ""))
    hits = (_listed(text) + [m.group(0) for m in _TRADEMARK.finditer(text or "")]
            + [m.group(0) for m in _INNER_CAPITAL.finditer(plain)])
    for word in hits:
        if word.lower() not in (f.lower() for f in found):
            found.append(word)
    return found


def refusal(text: str) -> str | None:
    """A reason naming what was caught, or None when the prompt is clean."""
    hits = caught(text)
    if not hits:
        return None
    return ("the prompt names what looks like a real brand or person ("
            + ", ".join(repr(h) for h in hits[:5])
            + ") — the AI-stories account makes fiction only")



# ------------------------------------------------ studio styles and characters
#
# Relayed from Miro's engine session, 2026-09-27: "Pixar-style 3D" plus "an
# old rusty tow truck with a hook" produced a near-copy of a famous film
# character, and a gold sedan drifted toward another. A public account
# cannot post that. So no prompt names a studio or a film's style, and the
# look is always "stylized 3D animation"; and the one combination known to
# summon an existing character is refused outright. Checked in code at the
# single door every clip goes through (`veo.clip`), not left to wording.

STUDIO_STYLES = re.compile(
    r"\b(pixar|disney|dreamworks|illumination|studio ghibli|ghibli|"
    r"toy story|lightning mcqueen|mcqueen|doc hudson|minions?|shrek|"
    r"cars[- ](?:movie|film|style|franchise)|(?:like|from) (?:the )?cars movie)\b",
    re.IGNORECASE)
MATER = re.compile(r"\bMater\b")
_TOW = re.compile(r"\btow[- ]?truck\b", re.IGNORECASE)
_RUST = re.compile(r"\brust(?:y|ed)?\b", re.IGNORECASE)
_HOOK = re.compile(r"\b(?:hook|buck[- ]?tooth|buck[- ]?teeth)\b", re.IGNORECASE)


def ip_refusal(text: str) -> str | None:
    """Why a video prompt would copy someone's film look or character, or
    None. Say it, don't soften it: the fix is one edit to the prompt."""
    text = text or ""
    if text.startswith(COURSE_PROMPT_HEAD) and (why := screen_refusal(text)):
        return why              # the course lane, at both clip doors
    hit = STUDIO_STYLES.search(text) or MATER.search(text)
    if hit:
        return (f"the prompt names a studio, film or character ({hit.group(0)!r}); "
                f"use 'stylized 3D animation' and original characters")
    if (player := FOOTBALLERS.search(text)) is not None:
        return (f"the prompt names a real footballer ({player.group(0)!r}); a "
                f"video model never depicts a real person")
    if _TOW.search(text) and _RUST.search(text) and _HOOK.search(text):
        return ("a rusty tow truck with a hook reads as an existing film "
                "character; give it an original colour, accessories and eyes")
    return None


# ------------------------------------------------------------- football lane
#
# Miro, 2026-09-29: a football (soccer) lane of stat reels. Real players and
# clubs may be NAMED in the on-screen text, the post caption and a voice-over
# script -- that is what the numbers are about -- but nothing sent to a video
# model may depict a real person, and no club or league crest, badge or logo
# is ever drawn. Two checks, one per side of that line.

# Famous players no video prompt may name, whatever the lane: the single
# door every clip goes through (`veo.clip`) refuses them via `ip_refusal`.
FOOTBALLERS = re.compile(
    r"\b(ronaldo|messi|neymar|mbapp[eé]|haaland|lewandowski|benzema|"
    r"ibrahimovi[cć]|maradona|pel[eé]|zidane|beckham|bellingham|vin[ií]cius|"
    r"lamine yamal|yamal|mohamed salah|de bruyne|modri[cć]|ronaldinho|"
    r"griezmann|harry kane|cristiano|lionel)\b", re.IGNORECASE)
# Words that ask for a crest, a badge, a sponsor mark or a real kit.
CRESTS = re.compile(
    r"\b(crests?|badges?|emblems?|logos?|coat of arms|insignia|kit sponsor|"
    r"sponsor(?:'s)? (?:mark|name)|club colou?rs of|replica (?:kit|shirt|jersey)|"
    r"(?:real|official) (?:kit|shirt|jersey)|trophy (?:of|named)|"
    r"uefa|fifa|premier league|la liga|serie a|bundesliga|ligue 1|"
    r"champions league|world cup|ballon d'or)\b", re.IGNORECASE)
# Anything that would show a face or a real body: the visuals are stadiums,
# balls, light and faceless silhouettes.
LIKENESS = re.compile(
    r"\b(face|faces|facial|portrait|likeness|lookalike|look-alike|celebrity|"
    r"real (?:player|person|footballer)|famous (?:player|footballer)|"
    r"his face|her face|headshot|close-up of (?:a|the) player)\b", re.IGNORECASE)
# Football here is association football -- soccer. Never the American game.
WRONG_SPORT = re.compile(
    r"\b(nfl|touchdowns?|quarterbacks?|super bowl|american football|gridiron|"
    r"end zone|linebackers?|wide receivers?|running backs?|field goals?|"
    r"first down|punts?|helmets? and pads)\b", re.IGNORECASE)


def wrong_sport(text: str) -> str | None:
    hit = WRONG_SPORT.search(text or "")
    if hit:
        return (f"{hit.group(0)!r} is American football; this lane is soccer "
                f"(association football) only")
    return None


def football_caption_refusal(text: str) -> str | None:
    """On-screen text and the post caption of a football reel. Real players,
    clubs and leagues may be named; a brand, a trademark sign or the wrong
    sport may not."""
    text = text or ""
    if (why := wrong_sport(text)) is not None:
        return why
    brands = [w for w in _listed_in(text, BRANDS)]
    marks = [m.group(0) for m in _TRADEMARK.finditer(text)]
    hits = brands + marks
    if hits:
        return ("the football text names a brand or a trademark ("
                + ", ".join(repr(h) for h in hits[:5]) + ")")
    return None


def football_visual_refusal(text: str, names: list[str] | tuple[str, ...] = ()
                            ) -> str | None:
    """What a football reel sends to the video model. Everything the
    ordinary guards refuse, plus: a known player's name; any player, club or
    league named in the fetched facts (`names`, the deny-list built from the
    API's own answer); a crest, badge, logo or real kit; a face or a
    likeness; a digit (numbers are burned in as captions, verified, never
    drawn by the model); and American football."""
    text = text or ""
    if (why := refusal(text) or ip_refusal(text) or wrong_sport(text)) is not None:
        return why
    for name in names:
        for part in {name, *str(name).split()}:
            part = part.strip(" .,'")
            if len(part) >= 3 and re.search(rf"(?<!\w){re.escape(part)}(?!\w)",
                                            text, re.IGNORECASE):
                return (f"the visual names {part!r}, from the fetched facts; a "
                        f"real player, club or league is never depicted")
    for pattern, what in ((CRESTS, ("a crest, badge, logo, real kit or "
                                    "competition mark")),
                          (LIKENESS, "a face or a real person's likeness")):
        hit = pattern.search(text)
        if hit:
            return f"the visual asks for {what} ({hit.group(0)!r})"
    if re.search(r"\d", text):
        return ("the visual carries a digit; numbers are burned in as verified "
                "captions, never drawn by the video model")
    return None


# ------------------------------------------------------------- course lane
#
# Miro, 2026-09-29: two course-promo reels a week on the AI-stories accounts
# (`content.course`), honest by rule. Two rules live here so every door can
# reach them without importing the lane:
#
#   * no fake screens -- a course reel's video prompt never asks for a
#     screen, an interface, a dashboard, a terminal, a phone or a laptop.
#     The product images are the only UI-like visuals, and they are real.
#     Every course prompt starts with COURSE_PROMPT_HEAD, and `ip_refusal`
#     (called by both clip doors, `veo.clip` and `higgsfield.clip`) refuses
#     such a prompt with a screen word in it;
#   * no income claims -- nothing spoken, shown or captioned promises money.

COURSE_PROMPT_HEAD = "Course promo reel, abstract motion."
SCREEN_WORDS = re.compile(
    r"\b(screens?|monitors?|displays?|dashboards?|terminals?|consoles?|"
    r"laptops?|computers?|desktops?|pcs?|phones?|smartphones?|iphones?|"
    r"tablets?|ipads?|keyboards?|mouse|cursors?|user interfaces?|interfaces?|"
    r"ui|ux|hud|apps?|applications?|websites?|web ?pages?|browsers?|"
    r"windows?|tabs?|code|coding|codes|command[- ]line|shell|prompts? window|"
    r"chat ?(?:window|bubbles?|box)|notifications?|spreadsheets?|charts?|"
    r"graphs?|dashboard|typing|typed|texts? on|emails?|inbox)\b",
    re.IGNORECASE)
INCOME_WORDS = re.compile(
    r"(\bmake \$|\bmade \$|\bmaking \$|\bincome\b|\bpassive\b|passive ?income|"
    r"make ?money|money ?online|get ?rich|side ?hustle|"
    r"\bearn(?:s|ed|ing|ings)?\b|\bget(?:ting)? rich\b|\brich quick\b|"
    r"\bsix[- ]figures?\b|\bseven[- ]figures?\b|\bside hustle\b|"
    r"\bfinancial freedom\b|\bquit your job\b|\bmillionaire\b|"
    r"\bprofits?\b|\brevenue\b|\bguaranteed?\b|\bcash ?flow\b|"
    r"\$\s?\d[\d,.]*\s?k?\s*(?:/|a|an|per|every)\s*(?:hour|day|week|month|year)\b)",
    re.IGNORECASE)


def screen_refusal(text: str) -> str | None:
    """A course reel's video prompt may not depict a screen of any kind."""
    hit = SCREEN_WORDS.search(text or "")
    if hit:
        return (f"the course reel's visual asks for a screen or interface "
                f"({hit.group(0)!r}); no fake screens -- the product images "
                f"are the only UI shown, and they are real")
    return None


def income_refusal(text: str) -> str | None:
    """Anything a course reel says, shows or captions: no income claim."""
    hit = INCOME_WORDS.search(text or "")
    if hit:
        return (f"the course reel makes an income or earnings claim "
                f"({hit.group(0).strip()!r}); the course promises safety, "
                f"never money")
    return None
