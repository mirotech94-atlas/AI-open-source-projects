"""The caption a campaign clip posts with, built and checked in code.

Both live campaigns spell out the same FTC layout, and `build_caption`
writes exactly it:

    <the post text>
    <blank line>
    #Ad                      <- alone on its own line, the first thing after the text
    #Tag #Tag #Tag           <- at most three, each one the campaign's own
                                (a campaign may require some of its own and
                                raise its count: Irubyana's four, every time)
    @handle                  <- the tag the platform requires

A YouTube Short carries the same caption as its description, and a title:
the post text word for word, then the disclosure, then the YouTube handle
the campaign requires (`youtube_title`) -- the title is what a Short shows
-- refused, never cut short, when that passes YouTube's 100 characters.

Pure: no I/O, no clock, no model. The post text is one of the campaign's
approved samples (chosen by key, never written by a model -- COD forbids
made-up claims), and every rule the campaign states about captions is
checked here, so a caption that would get the post rejected is refused
before a file is ever handed to Miro.

**Hooks.** A campaign whose `caption_rules["wording"]` is `"free"` (its doc
says exact wording is not required) may open the approved text with one of
`MOG_HOOKS`, picked by key, never written by a model. Every other campaign
-- a missing `wording` reads as `"samples"` -- gets none, and the word "mog"
is refused in its captions outright. "Mog" is about the play: it is refused
beside an @mention or a named private person, whatever the mode. The hooked
text is checked like any other (the campaign's require/forbid run on it),
and #Ad, the tags and the handle are laid out exactly as before.
"""
from __future__ import annotations

import re

from .campaigns import Campaign

MAX_HASHTAGS = 3
# A campaign whose rules ask for more may raise its own ceiling to this
# (`caption_rules["max_hashtags"]`: Irubyana's four), never past it.
MAX_HASHTAGS_CEILING = 5
CAPTION_MAX = 2200            # Instagram and TikTok both stop here
TITLE_MAX = 100               # YouTube's title limit
_HASHTAG = re.compile(r"#\w+")
_MOG = re.compile(r"\bmog\w*", re.IGNORECASE)
_MENTION = re.compile(r"(?<![\w.])@\w")

WORDING_MODES = ("samples", "free")
# The hooks a free-wording campaign's caption may open with. About the play
# or the streamer on camera, never about another player or a person.
MOG_HOOKS: dict[str, str] = {
    "mog": "Pure mog.",
    "clutch": "Clutch mog.",
    "play": "This play mogs.",
}


class CaptionError(ValueError):
    """The caption breaks one of the campaign's rules. The message says which."""


def sample(campaign: Campaign, key: str) -> str:
    samples = (campaign.caption_rules or {}).get("samples") or {}
    if key not in samples:
        raise CaptionError(f"{campaign.name} has no approved caption {key!r} "
                           f"(it has: {', '.join(samples) or 'none'})")
    return str(samples[key])


def wording(campaign: Campaign) -> str:
    """"free" when the campaign's rules let the caption's words vary, else
    "samples" (the default: approved text only)."""
    mode = str((campaign.caption_rules or {}).get("wording") or "samples")
    return mode if mode in WORDING_MODES else "samples"


def hook_prefixes(campaign: Campaign) -> dict[str, str]:
    """{key: hook} this campaign's captions may open with; {} unless its
    caption rules permit free wording."""
    return dict(MOG_HOOKS) if wording(campaign) == "free" else {}


def with_hook(campaign: Campaign, text: str, hook: str = "",
              private_names: tuple[str, ...] | list[str] = ()) -> str:
    """`text` opened with the hook `hook` names, or `text` itself when no
    hook is asked for. CaptionError when the campaign allows no hooks, the
    key is unknown, or the text names one of `private_names` (a chat
    member, a bystander: people who are not the brand or the streamer)."""
    if not hook:
        return text
    hooks = hook_prefixes(campaign)
    if not hooks:
        raise CaptionError(f"{campaign.name}'s captions are its approved samples "
                           f"only, so no hook")
    if hook not in hooks:
        raise CaptionError(f"no hook {hook!r} (there are: {', '.join(hooks)})")
    for name in private_names:
        if name.strip() and re.search(rf"\b{re.escape(name.strip())}\b", text,
                                      re.IGNORECASE):
            raise CaptionError(f"a hook never runs beside a private person's "
                               f"name ({name.strip()!r})")
    hooked = f"{hooks[hook]} {' '.join(text.split())}"
    check_text(campaign, hooked)
    return hooked

# Niche tags a clip may add after its campaign's required ones (Miro,
# 2026-09-29), per campaign slug and caption key ("*" for every key). Closed
# and in code; each is still refused by `build_caption` unless it is in the
# campaign's own hashtag pool, and never past MAX_HASHTAGS. ATLUS asks that
# a clip's tags match the game shown, so only a caption that names one game
# gets that game's tag.
NICHE: dict[str, dict[str, tuple[str, ...]]] = {
    "atlus-persona-metaphor": {"persona3": ("#Persona3Reload",),
                               "persona5": ("#Persona5Royal",),
                               "metaphor": ("#MetaphorReFantazio",)},
    "cod-mw4-warzone-operator": {"*": ("#CODWarzone",)},
    "irubyana-twitch": {"*": ("#twitchlive",)},
}


def max_hashtags(campaign: Campaign) -> int:
    """How many hashtags besides the disclosure this campaign's captions
    carry: MAX_HASHTAGS unless its rules raise it (`max_hashtags`), and
    never past MAX_HASHTAGS_CEILING. Pure."""
    try:
        own = int((campaign.caption_rules or {}).get("max_hashtags") or MAX_HASHTAGS)
    except (TypeError, ValueError):
        own = MAX_HASHTAGS
    return max(MAX_HASHTAGS, min(own, MAX_HASHTAGS_CEILING))


def required_hashtags(campaign: Campaign) -> list[str]:
    """The hashtags every caption of this campaign must carry
    (`caption_rules["required_hashtags"]`), in its own spelling. Pure."""
    return [str(t).strip() for t in
            (campaign.caption_rules or {}).get("required_hashtags") or ()
            if str(t).strip()]


def niche_tags(campaign: Campaign, key: str, tags: list[str]) -> list[str]:
    """The niche tags to append to `tags` (the campaign's required or
    default ones, which stay first): only from the campaign's own pool --
    a campaign with no pool allows no extra hashtag -- never repeated, and
    only while the total stays within the campaign's hashtag count. Pure."""
    pool = {t.lower() for t in (campaign.caption_rules or {}).get("hashtags") or ()}
    if not pool:
        return []
    table = NICHE.get(campaign.slug) or {}
    have = {t.lower() for t in tags}
    out: list[str] = []
    for tag in (*table.get(key, ()), *table.get("*", ())):
        if len(tags) + len(out) >= max_hashtags(campaign):
            break
        if tag.lower() in pool and tag.lower() not in have:
            out.append(tag)
            have.add(tag.lower())
    return out


def check_text(campaign: Campaign, text: str) -> None:
    """The post text against the campaign's own caption rules."""
    body = " ".join((text or "").split())
    if not body:
        raise CaptionError("the post text is empty")
    if (hit := _MOG.search(body)) is not None:
        if wording(campaign) != "free":
            raise CaptionError(f"{campaign.name}'s captions are its approved "
                               f"samples only; {hit.group(0)!r} is not one")
        if _MENTION.search(body):
            raise CaptionError(f"{hit.group(0)!r} never runs beside a tagged "
                               f"person")
    if _HASHTAG.search(body):
        # "#Ad must be the first hashtag after the post text" -- so the
        # text itself carries none.
        raise CaptionError("the post text may carry no hashtag: the disclosure "
                           "must be the first one")
    rules = campaign.caption_rules or {}
    for pattern in rules.get("require") or ():
        if not re.search(pattern, body, re.IGNORECASE):
            raise CaptionError(f"{campaign.name} captions must match {pattern!r}")
    for pattern in rules.get("forbid") or ():
        if (hit := re.search(pattern, body, re.IGNORECASE)) is not None:
            raise CaptionError(f"{campaign.name} captions may not say "
                               f"{hit.group(0)!r}")


def build_caption(campaign: Campaign, text: str, platform: str,
                  hashtags: tuple[str, ...] | list[str] = ()) -> str:
    """The whole caption, or CaptionError naming the rule it breaks."""
    if campaign.rules_pending:
        raise CaptionError(f"{campaign.name}'s rules are pending")
    if platform not in campaign.platforms:
        raise CaptionError(f"{campaign.name} does not pay for {platform} "
                           f"(only {', '.join(campaign.platforms)})")
    handle = (campaign.tag_handles or {}).get(platform, "").strip()
    if not handle.startswith("@") or len(handle) < 2:
        raise CaptionError(f"{campaign.name} names no {platform} handle to tag")
    disclosure = (campaign.disclosure or "").strip()
    if disclosure not in ("#Ad", "#Advertisement", "#Sponsored"):
        raise CaptionError(f"{disclosure!r} is not an FTC disclosure the "
                           f"campaign accepts")
    check_text(campaign, text)
    tags = [t.strip() for t in hashtags if t.strip()]
    limit = max_hashtags(campaign)
    if len(tags) > limit:
        raise CaptionError(f"at most {limit} hashtags besides {disclosure}")
    pool = {t.lower() for t in (campaign.caption_rules or {}).get("hashtags") or ()}
    for tag in tags:
        if not _HASHTAG.fullmatch(tag):
            raise CaptionError(f"{tag!r} is not a hashtag")
        if tag.lower() not in pool:
            raise CaptionError(f"{tag} is not one of {campaign.name}'s related "
                               f"hashtags")
        if tag.lower() == disclosure.lower():
            raise CaptionError(f"{disclosure} appears once, on its own line")
    if len({t.lower() for t in tags}) != len(tags):
        raise CaptionError("a hashtag is repeated")
    have = {t.lower() for t in tags}
    for must in required_hashtags(campaign):
        if must.lower() not in have:
            raise CaptionError(f"{campaign.name} captions must carry {must}")
    lines = [" ".join(text.split()), "", disclosure]
    if tags:
        lines.append(" ".join(tags))
    lines.append(handle)
    caption = "\n".join(lines)
    if len(caption) > CAPTION_MAX:
        raise CaptionError(f"the caption is {len(caption)} characters; the "
                           f"platforms stop at {CAPTION_MAX}")
    return caption


def validate(campaign: Campaign, caption: str, platform: str) -> None:
    """A finished caption re-read against the layout: the text, a blank
    line, the disclosure alone, then tags and the handle. CaptionError if
    anything is out of place."""
    lines = caption.split("\n")
    if len(lines) < 4 or lines[1] != "":
        raise CaptionError("the text must be followed by one blank line")
    if lines[2] != campaign.disclosure:
        raise CaptionError(f"{campaign.disclosure} must stand alone on the line "
                           f"after the blank line")
    rest = lines[3:]
    tags = rest[0].split() if len(rest) == 2 else []
    rebuilt = build_caption(campaign, lines[0], platform, tags)
    if rebuilt != caption:
        raise CaptionError("the caption is not in the campaign's layout")


def youtube_title(campaign: Campaign, text: str) -> str:
    """A Short's title: the approved post text, whole, then the disclosure
    (so `#Ad` is its first hashtag, as on every other platform), then the
    campaign's YouTube handle. CaptionError when the text breaks a rule,
    there is no handle, or the title would not fit: a caption is never
    truncated into a different claim."""
    check_text(campaign, text)
    disclosure = (campaign.disclosure or "").strip()
    handle = (campaign.tag_handles or {}).get("youtube", "").strip()
    if not handle.startswith("@") or len(handle) < 2:
        raise CaptionError(f"{campaign.name} names no youtube handle to tag")
    title = f"{' '.join(text.split())} {disclosure} {handle}"
    if len(title) > TITLE_MAX:
        raise CaptionError(f"as a YouTube title it is {len(title)} characters; "
                           f"YouTube stops at {TITLE_MAX} and a caption is never "
                           f"cut short -- pick a shorter approved caption")
    if "<" in title or ">" in title:
        raise CaptionError("a YouTube title may not contain < or >")
    return title


def onscreen_refusal(campaign: Campaign, text: str) -> str | None:
    """Why this on-screen text (burned captions or a title line) breaks the
    campaign's on-screen rules, or None."""
    rules = campaign.onscreen_rules or {}
    for pattern in rules.get("forbid") or ():
        if (hit := re.search(pattern, text or "", re.IGNORECASE)) is not None:
            return f"{campaign.name} forbids {hit.group(0)!r} on screen"
    return None


def onscreen_line(campaign: Campaign, index: int) -> str:
    """The on-screen title for a clip: one of the campaign's approved lines
    word for word, picked by index; "" when the campaign asks for none."""
    rules = campaign.onscreen_rules or {}
    if rules.get("mode") != "approved_lines":
        return ""
    lines = [str(line) for line in rules.get("lines") or ()]
    if not lines:
        raise CaptionError(f"{campaign.name} requires on-screen text but lists "
                           f"no approved line")
    return lines[index % len(lines)]
