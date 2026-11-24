"""ATLAS Academy thumbnails: a family per episode, one real image (2026-10-17).

the owner, 2026-10-17: "create thumbnails for each video and different as well".
Batch 7 of the video system (docs/academy-video-system/production-system.md:
"Thumbnail: 1280x720 PNG, one real screenshot, 2-5 words in `chapter`, the
same outcome as the title").

  Words    the episode's promised result, one string for the title, the
           thumbnail and the opening: `Episode.result` when the catalog
           grows it, else `thumb_title`, else the payoff's caption
           (words_of). Two lines; the second on the blue slab. Plex Sans 600
           at -0.01em, sentence case, 2 to 5 words, never under 104 px on
           the 1280 frame (13.65 px at YouTube's 168x94 list size).
  Image    exactly one, and real: a frame of the episode's own demo
           recording (its last step, cut to that step's box) when it has
           one; otherwise a crisp 1920x1080 frame of its own payoff slide,
           drawn from its scenes with the slide's rail, store and heading
           hidden (view_doc). No 3D still, no glow, no drawn stand-in.
  Families five compositions, not one template with the words swapped:
             result    words left, the image on a card off the right edge
             bleed     the image full-bleed, the words on a navy plate
             split     a seam: words and where it starts | the result
             focal     the result's bubble (or the demo's region) blown up
                       across the lower frame, the words in one row above
             question  the image as a band across the top, the question
                       large under it
           look_for picks one from the episode (a demo, a question, a fix,
           code) and its series' order, so two neighbours never share one;
           the second use of a family in a series is mirrored, and each
           series puts its blue accent rule on its own edge.
  Labels   one tab hanging from the top left: the level and the measured
           minutes in sentence case ("5 min"), 56 px. No "Made with AI" on
           the picture (the owner: the disclosure is YouTube's flag and the
           description).
  Glyphs   single-stroke line glyphs, 2 px on a 24 box, scaled.
"""
from __future__ import annotations

import base64
import functools
import html
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .tutorial_design import KIT_WORST, NON_TEXT, contrast, need, over, rgba
from .video_tokens import (
    BLUE,
    BLUE_TEXT,
    BLUE_TINT,
    ERROR,
    NAVY,
    NAVY_LINE,
    NAVY_RAISED,
    ON_BLUE,
    PAPER,
    PAPER_MUTED,
    SANS_FAMILY,
    fonts_css,
)

TW, TH = 1280, 720
# 3: black + royal blue; 4: the navy video system (2026-10-17) and batch 7's
# families, with the combined bump (tutorial_design.DESIGN_VERSION 8). Bump
# to redraw drafts.
THUMB_VERSION = "4"
NEXT_VERSION = THUMB_VERSION         # what the backfill draws for public videos
# On since the combined bump (2026-10-18): the backfill renders the new
# thumbnail for each public video (tutorials.backfill_thumbnails) and sets
# none, because no catalogued action sets a live video's thumbnail. It
# calls no YouTube API.
BACKFILL = True
ARCHETYPES = ("question", "fix", "command", "build", "versus")
FOCALS = ("flow", "hub", "loop", "code", "terminal", "file", "gpu", "chat", "calendar",
          "bars", "shield", "invoice", "folders", "playlists", "cloud", "agent")
FAMILIES = ("result", "bleed", "split", "focal", "question")
MIN_WORDS, MAX_WORDS = 2, 5
MIN_WORD_PX = 104                    # 13.65 px at YouTube's 168x94 list size
MAX_WORD_PX = 150
LABEL_PX = 56                        # the tab and the split's start line
LIST_W = 168                         # YouTube's list thumbnail on a phone, CSS px
LIST_SCALE = LIST_W / TW
MIN_LIST_PX = 7.0                    # the smallest text, as drawn at list size
MAX_BYTES = 2 * 1024 * 1024          # thumbnails.set's limit (youtube.api)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Thumb:
    """What an episode's thumbnail is about. `words` "" = the episode's
    result words. `data` is the episode's own content: a fix's before and
    after lines, a versus' two sides, node names, code lines."""
    archetype: str
    focal: str
    data: tuple[str, ...] = ()
    words: str = ""
    lit: int = -1                    # which node / line is lit; -1 the last
    extra: dict[str, str] = field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True)
class Look:
    """How one episode's thumbnail is composed: its family, mirrored or not,
    and the edge its series' accent rule sits on (0 left, 1 bottom, 2
    right, 3 top)."""
    family: str
    flip: bool = False
    accent: int = 0


# Curated per episode, by slug (numbers move; slugs do not).
THUMBS: dict[str, Thumb] = {
    "welcome": Thumb("build", "playlists", ("Claude Mastery", "n8n from Zero")),
    "what-is-claude": Thumb("question", "chat",
                            ("Plan my week", "Here's a plan:", "Make it shorter")),
    "mcp-what-it-is": Thumb("question", "hub", ("Claude", "Files", "Database", "Calendar")),
    "best-mcp-servers-claude-code": Thumb(
        "fix", "terminal", ("✕ Failed to connect", "✓ Connected"),
        extra={"cmd": "/mcp"}),
    "claude-skills-first-skill-md": Thumb(
        "command", "file", ("---", "name: reply-style", "description: how we", "  answer leads",
                            "---", "Write warm, short replies."),
        lit=1, extra={"name": "SKILL.md"}),
    "claude-code-subagents": Thumb("build", "hub", ("Claude", "Tests", "Docs", "Review")),
    "claude-code-hooks": Thumb(
        "command", "code", ('"PreToolUse": [{', '  "matcher": "Bash",', '  "hooks": [{',
                            '    "type": "command",', '    "command": "./check.sh"', "  }]"),
        lit=2, extra={"name": "settings.json"}),
    "custom-connector-remote-mcp-oauth": Thumb("build", "flow", ("Claude", "Sign in", "Your API")),
    "skills-mcp-subagents-hooks": Thumb("question", "hub", ("Claude Code", "Skills", "MCP",
                                                         "Subagents", "Hooks")),
    "what-is-n8n": Thumb("question", "flow", ("Trigger", "Action", "Action")),
    "n8n-first-workflow": Thumb("build", "flow", ("Webhook", "HTTP", "Slack")),
    "n8n-triggers-and-nodes": Thumb("build", "flow", ("Schedule", "Webhook", "App event"),
                                    extra={"fan": "1"}),
    "n8n-managing-workflows": Thumb("build", "folders", ("Leads", "Reports", "Invoices")),
    "n8n-error-handling": Thumb("fix", "flow", ("✕ Node failed", "✓ Retry, then alert")),
    "n8n-ai-agent": Thumb("build", "agent", ("Chat model", "Tool", "Memory")),
    "n8n-webhooks-apis": Thumb("command", "terminal",
                               ("curl -X POST \\", "  https://n8n/webhook/in \\",
                                "  -d '{\"city\":\"Lisbon\"}'"), lit=0),
    "self-host-n8n-docker": Thumb("command", "terminal",
                                  ("docker run -it --rm \\", "  -p 5678:5678 \\",
                                   "  -v n8n_data:/home/node/.n8n \\",
                                   "  n8nio/n8n"), lit=0),
    "project-what-we-build": Thumb("question", "flow", ("Form", "n8n", "Claude", "You")),
    "project-plan": Thumb("build", "flow", ("Form", "Claude", "Sheets", "Slack")),
    "project-connect-claude-n8n-mcp": Thumb("build", "hub", ("Claude", "n8n", "Sheets", "Slack")),
    "project-own-mcp-server": Thumb(
        "command", "code", ("@mcp.tool()", "def find_lead(email):", '    """Look up a lead."""',
                            "    return sheet.find(email)"),
        lit=0, extra={"name": "server.py"}),
    "project-claude-skill": Thumb("command", "file",
                                  ("---", "name: reply-style", "---", "Sign every reply",
                                   "with the shop's name."),
                                  lit=3, extra={"name": "SKILL.md"}),
    "project-test-deploy": Thumb("fix", "flow", ("Draft · inactive", "✓ Live · 12 runs")),
    "project-second-channel-email": Thumb("build", "flow", ("Email", "Claude", "You approve",
                                                            "Sent")),
    "business-what-to-automate-first": Thumb("question", "bars", ("Replies", "Booking",
                                                                   "Invoices", "Reports"),
                                             words="What to\nautomate?"),
    "auto-reply-leads": Thumb("build", "chat", ("New lead: Ana", "Hi Ana! Thanks —",
                                                "Saved to Sheets")),
    "booking-reminders": Thumb("fix", "calendar", ("✕ Missed 2pm", "✓ Reminder sent")),
    "invoice-follow-ups": Thumb("build", "invoice", ("Invoice #104", "Paid")),
    "weekly-report-slack": Thumb("build", "bars", ("Mon", "Tue", "Wed", "Thu", "Fri")),
    "social-scheduler-ai-captions": Thumb("build", "chat", ("Write 3 captions",
                                                           "Fresh bread at 7!",
                                                           "Approved · Fri 9:00")),
    "what-an-ai-agent-is": Thumb("question", "loop", ("Look", "Decide", "Act", "Check")),
    "agent-vs-workflow": Thumb("versus", "pair", ("Agent", "workflow"),
                               words="Agent or\nworkflow?"),
    "agent-tools-memory-planning": Thumb("build", "agent", ("Tools", "Memory", "Plan"),
                                         words="What makes\nan agent"),
    "agent-safety-approvals": Thumb("fix", "shield", ("✕ Sends anything", "✓ Asks first")),
    "what-is-local-ai": Thumb("versus", "pair", ("Cloud", "Local"), words="Cloud or\nlocal?"),
    "what-is-hermes-agent": Thumb("question", "loop", ("Model", "Tools", "Memory", "Skills")),
    "dgx-spark-unified-memory": Thumb("build", "gpu", ("128 GB", "unified memory")),
    "hermes-on-spark-vllm": Thumb("command", "terminal",
                                  ("vllm serve <model> \\", "  --port 8000"), lit=0),
    "local-stack-compared": Thumb("build", "folders", ("Playbook 1", "Playbook 2",
                                                       "Playbook 3")),
    "hermes-mcp-tools": Thumb("build", "hub", ("Hermes", "Files", "Database", "APIs")),
    "hermes-claude-desktop-mcp": Thumb("build", "flow", ("Claude", "MCP", "Hermes")),
    "hermes-telegram-slack-bot": Thumb("build", "chat", ("hey, summarise my day",
                                                         "Done — 3 things:", "1. Invoices")),
    "hermes-n8n": Thumb("build", "flow", ("n8n", "HTTP", "Hermes")),
}


# ------------------------------------------------------------- choosing

VERSUS = re.compile(r"\b(or|vs\.?|versus|which one|pick your)\b", re.IGNORECASE)
FIX = re.compile(r"\b(not working|fails?|failing|broken|fix|no more|safe|errors?)\b",
                 re.IGNORECASE)
COMMANDISH = re.compile(r"(\.md|\.json|\.py|docker|hooks?|terminal|install|command|cli)\b",
                        re.IGNORECASE)
FOCAL_WORDS: tuple[tuple[str, str], ...] = (
    (r"\b(gb|gpu|spark|dgx|memory)\b", "gpu"),
    (r"\b(agents?|loop)\b", "loop"),
    (r"\b(mcp|connectors?|plugins?|tools?)\b", "hub"),
    (r"\b(leads?|chat|bot|telegram|replies|email)\b", "chat"),
    (r"\b(booking|calendar|reminders?|no-shows?)\b", "calendar"),
    (r"\b(reports?|weekly|week)\b", "bars"),
    (r"\b(invoices?|paid|payments?)\b", "invoice"),
    (r"\b(safe|approv\w*|captions?)\b", "shield"),
    (r"\b(manage|folders?|versions?)\b", "folders"),
    (r"\b(cloud|local)\b", "cloud"),
)


def _ep_thumb(ep: Any) -> Thumb | None:
    got = getattr(ep, "thumb", None)
    if isinstance(got, Thumb):
        return got
    if isinstance(got, dict) and got.get("archetype"):
        return Thumb(**got)
    return None


def _diagram(ep: Any) -> tuple[str, ...]:
    for sc in getattr(ep, "scenes", ()):
        if sc.kind == "diagram" and sc.items:
            return tuple(i.split("\n")[0] for i in sc.items)[:4]
    return ()


def _code(ep: Any) -> tuple[tuple[str, ...], int]:
    for sc in getattr(ep, "scenes", ()):
        if sc.kind == "code" and sc.code.strip():
            lines = [x for x in sc.code.split("\n") if x.strip()
                     and not x.lstrip().startswith("#")][:6]
            lit = 0
            if sc.marks:
                first = sc.code.split("\n")[sc.marks[0][0]]
                lit = next((i for i, x in enumerate(lines) if x == first), 0)
            return tuple(lines), lit
    return (), -1


def derive(ep: Any) -> Thumb:
    """A spec from the episode's own words, diagram and code, when none was
    written for it."""
    words = getattr(ep, "thumb_title", "")
    flat = words.replace("\n", " ")
    text = f"{flat} {getattr(ep, 'summary', '')}".lower()
    nodes = _diagram(ep)
    lines, lit = _code(ep)
    if VERSUS.search(flat) and " or " in f" {flat.lower()} ":
        a, _, b = flat.partition(" or ")
        return Thumb("versus", "pair", (a.strip(" ?"), b.strip(" ?")), words=words)
    focal = next((f for pat, f in FOCAL_WORDS if re.search(pat, text)), "flow")
    if FIX.search(flat):
        return Thumb("fix", focal if focal in ("calendar", "shield") else "flow",
                     ("✕ It stopped", "✓ It runs again"))
    if COMMANDISH.search(flat) and lines:
        return Thumb("command", "code", lines, lit=lit)
    if COMMANDISH.search(flat):
        return Thumb("command", "terminal", (f"# {flat}",), lit=0)
    if flat.rstrip().endswith("?"):
        return Thumb("question", "flow" if nodes else focal, nodes)
    if nodes:
        return Thumb("build", "flow", nodes)
    return Thumb("build", focal, ())


def spec_for(ep: Any) -> Thumb:
    """The episode's thumbnail spec: its own `thumb`, else THUMBS, else
    derived. A command thumbnail shows the episode's own code once its
    script has some: the table's lines only stand in until then."""
    own = _ep_thumb(ep)
    if own is not None:
        return own
    spec = THUMBS.get(getattr(ep, "slug", ""))
    if spec is None:
        return derive(ep)
    if spec.archetype == "command" and spec.focal in ("code", "file", "terminal"):
        lines, lit = _code(ep)
        if lines:
            return replace(spec, data=lines, lit=lit)
    return spec


def word_lines(spec: Thumb, title: str) -> tuple[str, str]:
    """The two lines of words: the episode's own (`title`, its result)
    first; a spec's `words` only stand in when there are none."""
    return _lines(title or spec.words)


# IBM Plex Sans 600's advance per character at 1 px (Chromium measureText on
# the vendored plex-sans-600.woff2, rounded up), 2026-10-17.
PLEX_600_EM = {
    " ": 0.236, "!": 0.309, "&": 0.713, "'": 0.26, ",": 0.298, "-": 0.402, ".": 0.298,
    "/": 0.437, "0": 0.6, "1": 0.6, "2": 0.6, "3": 0.6, "4": 0.6, "5": 0.6, "6": 0.6,
    "7": 0.6, "8": 0.6, "9": 0.6, ":": 0.318, "?": 0.493, "A": 0.672, "B": 0.663,
    "C": 0.642, "D": 0.689, "E": 0.6, "F": 0.577, "G": 0.712, "H": 0.719, "I": 0.423,
    "J": 0.545, "K": 0.678, "L": 0.521, "M": 0.816, "N": 0.719, "O": 0.712, "P": 0.641,
    "Q": 0.712, "R": 0.664, "S": 0.611, "T": 0.58, "U": 0.69, "V": 0.638, "W": 0.949,
    "X": 0.655, "Y": 0.632, "Z": 0.599, "a": 0.559, "b": 0.6, "c": 0.513, "d": 0.6,
    "e": 0.558, "f": 0.35, "g": 0.545, "h": 0.588, "i": 0.276, "j": 0.276, "k": 0.562,
    "l": 0.294, "m": 0.888, "n": 0.588, "o": 0.563, "p": 0.6, "q": 0.6, "r": 0.393,
    "s": 0.499, "t": 0.374, "u": 0.588, "v": 0.524, "w": 0.819, "x": 0.544, "y": 0.524,
    "z": 0.502,
}
WORD_TRACK = -0.01                   # the chapter style's letter-spacing, in em
KERN_SLACK = 1.02                    # pair kerning runs a measured word up to 0.5% wide


def em(text: str) -> float:
    """Plex Sans 600's advance for `text` at 1 px, set at the chapter
    style's -0.01em; a character not in the table counts as the widest."""
    widest = max(PLEX_600_EM.values())
    return KERN_SLACK * sum(PLEX_600_EM.get(c, widest) + WORD_TRACK for c in text)




# ---------------------------------------------------------------- the words

def words_of(ep: Any) -> str:
    """The thumbnail's words: `thumb_title` (two short lines, sized for the
    168x94 list), else the episode's `result`, else the payoff's caption.
    The result labels the opening; at thumbnail size several run too long."""
    for got in (getattr(ep, "thumb_title", ""), getattr(ep, "result", "")):
        if isinstance(got, str) and got.strip():
            return got.strip()
    payoff = getattr(ep, "payoff", None)
    return (getattr(payoff, "caption", "") or "").strip()


def _lines(words: str) -> tuple[str, str]:
    l1, _, l2 = words.partition("\n")
    if not l2:                                   # one line given: the last word on the slab
        head, _, tail = l1.rpartition(" ")
        l1, l2 = (head, tail) if head else (l1, "")
    return l1.strip(), l2.strip()


# The width each family's words may take on the 1280 frame, and their largest
# size. `focal` sets both lines in one row.
WORD_BOX = {"result": 560, "bleed": 840, "split": 500, "focal": 1150, "question": 1150}
WORD_MAX = {"result": 150, "bleed": 124, "split": 140, "focal": 118, "question": 128}
SLAB_PAD = 48                        # the slab's padding, left + right


def family_px(family: str, l1: str, l2: str) -> int:
    """The words' size in `family`: as large as its box allows."""
    box, most = WORD_BOX[family], WORD_MAX[family]
    slab = SLAB_PAD / most
    if family == "focal":
        wide = em(l1) + 0.26 + em(l2) + slab
    else:
        wide = max(em(l1), em(l2) + slab)
    return int(min(most, box / max(wide, 1.0)))


def _family_of(spec: Thumb) -> str:
    """The family a bare spec (no episode, no series) reads best in."""
    return {"question": "question", "fix": "split", "versus": "split",
            "command": "focal"}.get(spec.archetype, "result")


def legibility_refusal(spec: Thumb, title: str, family: str = "") -> str | None:
    """None when the words read at 168x94; otherwise why not."""
    if spec.archetype not in ARCHETYPES:
        return f"unknown thumbnail layout {spec.archetype!r}"
    l1, l2 = word_lines(spec, title)
    words = f"{l1} {l2}".split()
    if not MIN_WORDS <= len(words) <= MAX_WORDS:
        return f"the thumbnail has {len(words)} words; it takes {MIN_WORDS} to {MAX_WORDS}"
    if not (l1 and l2):
        return "the thumbnail words need two lines, the second on the slab"
    px = family_px(family or _family_of(spec), l1, l2)
    if px < MIN_WORD_PX:
        return (f"the thumbnail words would be {px} px, under {MIN_WORD_PX} px: "
                "unreadable at 168x94")
    return None


# ------------------------------------------------------------- the families

def has_demo(ep: Any) -> bool:
    return any(getattr(sc, "kind", "") == "demo" and getattr(sc, "demo", "")
               for sc in getattr(ep, "scenes", ()))


def _before(ep: Any, spec: Thumb) -> tuple[str, bool]:
    """Where the episode starts, for the split: (text, struck). A fix's own
    "before" line, struck; else the first box of its payoff or diagram."""
    if spec.archetype == "fix" and spec.data:
        return spec.data[0].removeprefix("✕").strip(), True
    for sc in (getattr(ep, "payoff", None), getattr(ep, "hook", None),
               *getattr(ep, "scenes", ())):
        if sc is not None and sc.kind in ("payoff", "hook", "diagram") and sc.items:
            return sc.items[0].split("\n")[0].strip(), False
    return "", False


def ranked(ep: Any) -> tuple[str, ...]:
    """The families this episode suits, best first: a demo leads with its
    recording, a question with its question, a fix with its before and after,
    code with one thing blown up. A family is left out when its words would
    set under MIN_WORD_PX, a question without a question mark, a split with
    nowhere to start from."""
    spec = spec_for(ep)
    l1, l2 = _lines(words_of(ep))
    asks = f"{l1} {l2}".rstrip().endswith("?")
    kinds = {getattr(sc, "kind", "") for sc in getattr(ep, "scenes", ())}
    prefs: list[str] = []
    if has_demo(ep):
        prefs += ["bleed", "focal"]
    if asks:
        prefs.append("question")
    if spec.archetype in ("fix", "versus"):
        prefs.append("split")
    if spec.archetype == "command" or "code" in kinds:
        prefs.append("focal")
    if "diagram" in kinds:
        prefs.append("result")
    prefs += ["result", "bleed", "split", "focal", "question"]
    out: list[str] = []
    for fam in prefs:
        if fam in out or (fam == "question" and not asks):
            continue
        if fam == "split" and not _before(ep, spec)[0]:
            continue
        if family_px(fam, l1, l2) < MIN_WORD_PX:
            continue
        out.append(fam)
    return tuple(out)


def plan(episodes: tuple[Any, ...] | list[Any], series_index: int = 0) -> dict[str, Look]:
    """One series' looks, by slug, in episode order: each episode's best
    family that its neighbour before it did not take, families already used
    in the series pushed down (2 places a use), ties broken from the series'
    own offset. Deterministic: the same catalog, the same thumbnails."""
    out: dict[str, Look] = {}
    uses = dict.fromkeys(FAMILIES, 0)
    prev = ""
    eps = sorted(episodes, key=lambda e: getattr(e, "number", 0))
    fits = [ranked(ep) for ep in eps]
    for i, ep in enumerate(eps):
        # one step ahead: never take the only family the next episode fits
        only = fits[i + 1][0] if i + 1 < len(eps) and len(fits[i + 1]) == 1 else ""
        options = ([f for f in fits[i] if f not in (prev, only)]
                   or [f for f in fits[i] if f != prev] or [f for f in FAMILIES if f != prev])
        rank = {f: i for i, f in enumerate(options)}
        fam = min(options, key=lambda f: (rank[f] + 2 * uses[f],
                                          (FAMILIES.index(f) - series_index) % len(FAMILIES)))
        out[ep.slug] = Look(fam, flip=uses[fam] % 2 == 1, accent=(series_index + i) % 4)
        uses[fam] += 1
        prev = fam
    return out


@functools.lru_cache(maxsize=1)
def _catalog_plans() -> dict[str, Look]:
    from .tutorial_series import SERIES
    out: dict[str, Look] = {}
    for i, s in enumerate(SERIES):
        out.update(plan([e for e in s.episodes if e.scripted], i))
    return out


def look_for(ep: Any) -> Look:
    """This episode's look: its series' plan for a catalog episode; for any
    other, its best family, unmirrored."""
    got = _catalog_plans().get(getattr(ep, "slug", ""))
    if got is not None and getattr(ep, "scripted", True):
        return got
    best = ranked(ep)
    return Look(best[0] if best else "focal")


# ----------------------------------------------------------- the real image

@dataclass(frozen=True)
class Source:
    """Where the one real image comes from: a demo recording's step
    (`key`, `step`), or a slide of the episode's own (`scene`, `view`)."""
    kind: str                        # "demo" | "slide"
    key: str = ""
    step: int = 0
    scene: Any = None
    view: str = "flow"


# Which part of the episode's slide each family shows, and the box it shows
# it in on the 1280 frame (shot at 1.5x, so the picture is crisp and fills
# its box: no crop throws away what the slide drew).
VIEW_OF = {"result": "node", "bleed": "flow", "question": "wide", "split": "bubble-tall",
           "focal": "bubble"}
FRAME_BOX = {"result": (640, 528), "bleed": (TW, TH), "question": (TW, 356),
             "split": (680, TH), "focal": (TW, 404)}
FRAME_SCALE = 1.5


def frame_size(family: str) -> tuple[int, int]:
    w, h = FRAME_BOX.get(family, (TW, TH))
    return round(w * FRAME_SCALE), round(h * FRAME_SCALE)


def source(ep: Any, family: str = "") -> Source:
    """A frame of the episode's own demo recording when it has one (the last
    step of its last demo: the finished thing); else its payoff slide, else
    its hook, a diagram, code or the playlists it shows."""
    for sc in reversed(getattr(ep, "scenes", ())):
        if sc.kind == "demo" and sc.demo:
            from .tutorial_demos import DEMOS
            return Source("demo", key=sc.demo, step=len(DEMOS[sc.demo].steps) - 1)
    view = VIEW_OF.get(family or look_for(ep).family, "flow")
    for sc in (getattr(ep, "payoff", None), getattr(ep, "hook", None)):
        if sc is not None:
            return Source("slide", scene=sc, view=view)
    scenes = getattr(ep, "scenes", ())
    for kind in ("diagram", "playlists", "code"):
        for sc in scenes:
            if sc.kind == kind:
                return Source("slide", scene=sc, view=view)
    return Source("slide", scene=scenes[0] if scenes else None, view=view)


# The slide's own chrome is the same on every episode (and would pull every
# thumbnail towards the others): hidden. What the view shows is then scaled
# to fill the shot, centred, by the page itself once its fonts are in.
CHROME = (".scrim,.rail,.store,.prog,.mark,.main h1,.preview{display:none!important}"
          f"html,body{{background:{BLUE_TINT}!important;width:100%!important;"
          "height:100%!important;overflow:hidden}"
          ".main{position:absolute!important;left:0!important;top:0!important;"
          "right:auto!important;bottom:auto!important;width:1728px;height:1080px}")
VIEWS = {   # view: (what is shown, extra CSS)
    "flow": (".flow,.bubble", ""),
    "wide": (".flow", ".bubble{display:none!important}"),
    "node": (".flow .node:last-of-type",
             (".bubble{display:none!important}"
              ".flow>*:not(.node:last-of-type){visibility:hidden}")),
    "bubble": (".bubble", (".flow{display:none!important}"
                           ".bubble{width:fit-content!important}")),
    "bubble-tall": (".bubble", (".flow{display:none!important}"
                                ".bubble{max-width:620px!important;width:fit-content!important}")),
}
FALLBACK = ".main>*:not(h1)"
MAX_ZOOM = 2.4
FIT_JS = """<script>document.fonts.ready.then(()=>{
const m=document.querySelector('.main');if(!m)return;
let els=[...document.querySelectorAll(%s)].filter(e=>e.getClientRects().length);
if(!els.length)els=[...document.querySelectorAll(%s)].filter(e=>e.getClientRects().length);
if(!els.length)return;
const rs=els.map(e=>e.getBoundingClientRect());
const l=Math.min(...rs.map(r=>r.left)),t=Math.min(...rs.map(r=>r.top));
const w=Math.max(...rs.map(r=>r.right))-l,h=Math.max(...rs.map(r=>r.bottom))-t;
const W=innerWidth,H=innerHeight,s=Math.min(W*.88/w,H*.84/h,%s);
m.style.transformOrigin='0 0';
m.style.transform=`translate(${(W-w*s)/2-l*s}px,${(H-h*s)/2-t*s}px) scale(${s})`;
});</script>"""


def view_doc(scene: Any, view: str = "flow") -> str:
    """The scene's last step as its slide draws it, chrome hidden and the
    viewed part scaled to fill the shot: the picture the thumbnail shows."""
    import json

    from .tutorial_design import slide, steps_of
    doc = slide(scene, max(0, steps_of(scene) - 1), mark="", chapter=0, chapters=1)
    shown, extra = VIEWS[view]
    fit = FIT_JS % (json.dumps(shown), json.dumps(FALLBACK), MAX_ZOOM)
    doc = doc.replace("</head>", f"<style>{CHROME}{extra}</style></head>", 1)
    return doc.replace("</body>", f"{fit}</body>", 1)


async def image(ep: Any, shooter: Any, work: str, family: str = "") -> tuple[str | None,
                                                                              str | None]:
    """(a PNG data URI of the episode's real image, None) or (None, why)."""
    from ..content import ffmpeg
    src = source(ep, family)
    out = os.path.join(work, "thumb-frame.png")
    if src.kind == "demo":
        from . import tutorial_demos as demos
        if (why := demos.missing([src.key])) is not None:
            return None, why
        start, length = demos.window(src.key, src.step)
        step = demos.DEMOS[src.key].steps[src.step]
        try:
            await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                              "-ss", f"{start + max(0.0, length - 0.05):.3f}",
                              "-i", str(demos.video_path(src.key)), "-frames:v", "1",
                              "-vf", demos.crop_filter(step, TW, TH), out], timeout=60)
        except ffmpeg.FfmpegError as exc:
            return None, f"the demo frame could not be cut: {exc}"
    elif src.scene is None:
        return None, "the episode has no scene to show"
    else:
        w, h = frame_size(family or look_for(ep).family)
        await shooter.shoot(view_doc(src.scene, src.view), out, w, h)
    try:
        data = Path(out).read_bytes()
    except OSError as exc:
        return None, f"the frame was not written: {exc}"
    if not data.startswith(PNG_MAGIC):
        return None, "the frame is not a PNG (nothing was drawn)"
    return "data:image/png;base64," + base64.b64encode(data).decode(), None


PALETTE_SQUEEZE = ("split[a][b];[a]palettegen=max_colors=256[p];"
                   "[b][p]paletteuse=dither=sierra2_4a")


async def fit_size(path: str) -> str | None:
    """None when the PNG is 1 byte to MAX_BYTES; a larger one is redrawn on
    a 256-colour palette once, and refused if it is still too big."""
    from ..content import ffmpeg
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size == 0:
        return "the thumbnail is empty"
    if size <= MAX_BYTES:
        return None
    small = path + ".small.png"
    try:
        await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", PALETTE_SQUEEZE, small], timeout=60)
    except ffmpeg.FfmpegError as exc:
        return f"the thumbnail is {size} bytes, over 2 MB, and could not be squeezed: {exc}"
    got = os.path.getsize(small) if os.path.exists(small) else 0
    if not 0 < got <= MAX_BYTES:
        return f"the thumbnail is {got or size} bytes, over YouTube's 2 MB"
    os.replace(small, path)
    return None


# A frame of the lesson's own video (academy/video_frames.py, 2026-10-20) is
# shown large: full-bleed under the plate, or on the result card, or blown up
# under a row of words; the plan's own family first when it is one of these.
FRAME_FAMILIES = ("bleed", "result", "focal")
JPEG_MAGIC = b"\xff\xd8\xff"


def frame_look(ep: Any) -> Look | None:
    """The look for a thumbnail over a video frame: the series plan's family
    when it shows the frame large, else the first of FRAME_FAMILIES whose
    words still read at 168x94; the plan's mirror and accent kept. None when
    no family fits the words."""
    base = look_for(ep)
    l1, l2 = _lines(words_of(ep))
    order = ((base.family,) if base.family in FRAME_FAMILIES else ()) + FRAME_FAMILIES
    for fam in order:
        if family_px(fam, l1, l2) >= MIN_WORD_PX:
            return replace(base, family=fam)
    return None


def frame_uri(path: str) -> tuple[str | None, str | None]:
    """A data URI of a PNG or JPEG frame file, or (None, why). Blocking."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return None, f"the frame could not be read: {type(exc).__name__}"
    if data.startswith(PNG_MAGIC):
        kind = "png"
    elif data.startswith(JPEG_MAGIC):
        kind = "jpeg"
    else:
        return None, "the frame is not a PNG or JPEG"
    return f"data:image/{kind};base64," + base64.b64encode(data).decode(), None


async def render(ep: Any, shooter: Any, out_png: str, *, level: int, badge: str,
                 work: str, frame: str = "") -> str | None:
    """Draw the episode's thumbnail to `out_png` (1280x720 PNG, at most
    2 MB): its real image first, then the page over it. With `frame` (a
    frame of the lesson's own video), that frame is the image, shown large
    (frame_look). None, or why not."""
    import asyncio
    words = words_of(ep)
    if frame:
        got = frame_look(ep)
        if got is None:
            return "the thumbnail words do not fit any family that shows the frame large"
        look = got
    else:
        look = look_for(ep)
    if (why := legibility_refusal(replace(spec_for(ep), words=""), words,
                                  look.family)) is not None:
        return why
    if frame:
        uri, why = await asyncio.to_thread(frame_uri, frame)
    else:
        uri, why = await image(ep, shooter, work, look.family)
    if uri is None:
        return f"the thumbnail has no real picture: {why}"
    doc = (page(ep, level=level, badge=badge, image=uri, look=look) if frame
           else page(ep, level=level, badge=badge, image=uri))
    await shooter.shoot(doc, out_png, TW, TH)
    return await fit_size(out_png)


# ------------------------------------------------------------- the drawing

def _e(s: str) -> str:
    return html.escape(s)


GLYPHS = {"cross": "M6 6l12 12M18 6L6 18", "tick": "M5 12.5l4.5 4.5L19 7",
          "arrow": "M4 12h16M14 6l6 6-6 6"}


def glyph(name: str, colour: str, size: int = LABEL_PX) -> str:
    """A single-stroke line glyph: 2 px on a 24 box, scaled to `size`."""
    return (f'<svg class="t-g" width="{size}" height="{size}" viewBox="0 0 24 24">'
            f'<path d="{GLYPHS[name]}" fill="none" stroke="{colour}" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def badge_shown(badge: str) -> str:
    """The time badge in sentence case: "5 MIN" -> "5 min"."""
    return badge.strip().lower()


def _tab(level: int, badge: str) -> str:
    mins = f'<b class="t-min">{_e(badge_shown(badge))}</b>' if badge else ""
    return f'<div class="t-tab"><b class="t-lvl">Level {level}</b>{mins}</div>'


def _words(l1: str, l2: str) -> str:
    return (f'<div class="t-words"><span class="l1">{_e(l1)}</span>'
            f'<span class="l2">{_e(l2)}</span></div>')


ACCENT = ("left:0;top:0;bottom:0;width:12px", "left:0;right:0;bottom:0;height:12px",
          "right:0;top:0;bottom:0;width:12px", "left:0;right:0;top:0;height:12px")


def _img_css(sel: str, image: str, size: str, pos: str) -> str:
    if not image:                                # no frame (callers without one)
        return f"{sel}{{background:{NAVY_RAISED};border:2px solid {NAVY_LINE}}}"
    return (f'{sel}{{background:{BLUE_TINT} url("{image}") {pos}/{size} no-repeat}}')


def body(spec: Thumb, *, title: str, level: int, badge: str, still: str = "",
         tag: str = "", image: str = "", look: Look | None = None, ep: Any = None,
         demo: bool = False, framed: bool = False) -> tuple[str, str]:
    """(the page's body, its CSS) for one thumbnail. `still` and `tag` are
    no longer drawn (no 3D still, no on-picture AI label); they stay in the
    signature for tutorial_design.thumbnail. `framed`: the image is a whole
    frame of the lesson's video, centred and never zoomed past cover."""
    look = look or Look(_family_of(spec))
    centre = demo or framed
    l1, l2 = word_lines(spec, title)
    fam, flip = look.family, look.flip
    px = family_px(fam, l1, l2)
    near, far = ("right", "left") if flip else ("left", "right")
    css = (f".t-acc{{position:absolute;{ACCENT[look.accent % 4]};background:{BLUE};z-index:6}}"
           f".t-words .l1,.t-words .l2{{font-size:{px}px}}")
    acc = '<div class="t-acc"></div>'
    if fam == "result":
        css += (_img_css(".t-img", image, "cover", "50% 50%" if centre else "0% 50%")
                + f".t-img{{position:absolute;top:96px;bottom:96px;{near}:640px;{far}:0;"
                f"border-{near}:2px solid {NAVY_LINE};border-radius:"
                + ("0 16px 16px 0" if flip else "16px 0 0 16px")
                + f"}}.t-words{{{near}:56px;top:200px}}")
        page = '<div class="t-img"></div>' + _words(l1, l2)
    elif fam == "bleed":
        css += (_img_css(".t-img", image, "cover", "50% 50%")
                + ".t-img{position:absolute;inset:0}"
                # the plate on the right (the tab keeps the top left): under
                # the top edge, or mirrored lower, clear of YouTube's duration
                f".t-plate{{position:absolute;right:48px;top:{290 if flip else 96}px;background:"
                f"{rgba(NAVY, .94)};border-top:8px solid {BLUE};border-radius:0 0 16px 16px;"
                "padding:28px 32px 32px}.t-plate .t-words{position:static}")
        page = f'<div class="t-img"></div><div class="t-plate">{_words(l1, l2)}</div>'
    elif fam == "split":
        before, struck = _before(ep, spec) if ep is not None else (
            (spec.data[0].removeprefix("✕").strip(), True) if spec.data else ("", False))
        mark = glyph("cross", ERROR) if struck else glyph("arrow", BLUE_TEXT)
        cls = "t-from struck" if struck else "t-from"
        css += (_img_css(".t-img", image, "cover", "50% 50%")
                + f".t-img{{position:absolute;top:0;bottom:0;{near}:600px;{far}:0;"
                f"border-{near}:6px solid {BLUE}}}"
                f".t-words{{{near}:56px;top:150px}}"
                f".t-from{{position:absolute;{near}:56px;bottom:64px;width:500px;display:flex;"
                f"gap:16px;align-items:flex-start;font:600 {LABEL_PX}px/1.08 {SANS_FAMILY};"
                f"letter-spacing:-.01em;color:{PAPER_MUTED}}}"
                f".t-from.struck span{{text-decoration:line-through;text-decoration-thickness:"
                f"5px;text-decoration-color:{PAPER_MUTED}}}"
                f".t-ok{{position:absolute;{near}:560px;top:312px;width:96px;height:96px;"
                f"border-radius:50%;background:{NAVY};border:4px solid {BLUE};display:flex;"
                "align-items:center;justify-content:center;z-index:4}")
        page = ('<div class="t-img"></div>' + _words(l1, l2)
                + (f'<div class="{cls}">{mark}<span>{_e(before)}</span></div>' if before else "")
                + f'<div class="t-ok">{glyph("tick", BLUE_TEXT, 64)}</div>')
    elif fam == "focal":
        # mirrored: the blown-up picture on top, the words in a row under it
        zoom, pos = ("170%", "50% 46%") if demo and not framed else ("cover", "50% 50%")
        band = ("top:0;height:404px;border-bottom" if flip
                else "bottom:0;top:316px;border-top")
        css += (_img_css(".t-img", image, zoom, pos)
                + f".t-img{{position:absolute;left:0;right:0;{band}:6px solid {BLUE}}}"
                f".t-words{{{near}:56px;top:{470 if flip else 156}px;display:flex;"
                "align-items:baseline;gap:22px}"
                ".t-words .l1{display:inline}.t-words .l2{margin-top:0}")
        page = '<div class="t-img"></div>' + _words(l1, l2)
    else:                                        # question
        css += (_img_css(".t-img", image, "cover",
                         ("50% 50%" if centre else ("100% 50%" if flip else "0% 50%")))
                + ".t-img{position:absolute;left:0;right:0;top:0;height:356px;"
                f"border-bottom:6px solid {BLUE}}}"
                f".t-words{{{near}:56px;top:392px" + (";text-align:right" if flip else "")
                + "}")
        page = '<div class="t-img"></div>' + _words(l1, l2)
    return _tab(level, badge) + page + acc, css


def page_css(spec: Thumb, title: str) -> str:
    """The CSS every family shares: the field, the tab, the words."""
    return f"""
html,body{{width:{TW}px;height:{TH}px;margin:0;overflow:hidden;background:{NAVY};color:{PAPER}}}
body{{position:relative}}
.t-tab{{position:absolute;left:56px;top:0;display:flex;gap:12px;z-index:7}}
.t-lvl,.t-min{{display:block;font:600 {LABEL_PX}px/1 {SANS_FAMILY};letter-spacing:-.01em;
padding:18px 22px 16px;border-radius:0 0 16px 16px}}
.t-lvl{{background:{PAPER};color:{NAVY}}}
.t-min{{background:{BLUE};color:{ON_BLUE}}}
.t-words{{position:absolute;z-index:5;white-space:nowrap}}
.t-words .l1{{display:block;font-family:{SANS_FAMILY};font-weight:600;line-height:.96;
letter-spacing:-.01em;color:{PAPER}}}
.t-words .l2{{display:inline-block;margin-top:14px;font-family:{SANS_FAMILY};font-weight:600;
line-height:1;letter-spacing:-.01em;color:{ON_BLUE};background:{BLUE};
padding:6px 24px 16px;border-radius:16px}}
.t-g{{flex:none}}
"""


def page(ep: Any, *, level: int, badge: str, image: str, look: Look | None = None) -> str:
    """The episode's whole thumbnail page (1280x720), over its real image.
    `look` set: a video frame's (frame_look), centred as a demo frame is."""
    spec = spec_for(ep)
    framed = look is not None
    look = look or look_for(ep)
    inner, extra = body(spec, title=words_of(ep), level=level, badge=badge, image=image,
                        look=look, ep=ep, demo=has_demo(ep), framed=framed)
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}'
            f'*{{box-sizing:border-box;margin:0;padding:0}}{page_css(spec, words_of(ep))}'
            f'{extra}</style></head><body>{inner}</body></html>')


# ------------------------------------------------------------- contrast

# The brightest a real image can be under the bleed's plate.
IMAGE_WORST = KIT_WORST
# (what, text colour, layers under it, base, px): the threshold is
# tutorial_design.need(px), 4.5:1 under 24px and 3:1 from 24px, 3:1 for a
# state or mark (NON_TEXT). px is the smallest the 1280x720 frame draws it.
TEXT_PAIRS: tuple[tuple[str, str, tuple[tuple[str, float], ...], str, int], ...] = (
    ("words on navy", PAPER, ((NAVY, 1.0),), NAVY, MIN_WORD_PX),
    ("words on the slab", ON_BLUE, ((BLUE, 1.0),), NAVY, MIN_WORD_PX),
    ("words on the bleed plate", PAPER, ((NAVY, .94),), IMAGE_WORST, MIN_WORD_PX),
    ("level tab", NAVY, ((PAPER, 1.0),), NAVY, LABEL_PX),
    ("time badge", ON_BLUE, ((BLUE, 1.0),), NAVY, LABEL_PX),
    ("split, where it starts", PAPER_MUTED, ((NAVY, 1.0),), NAVY, LABEL_PX),
    ("split, the cross", ERROR, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("split, the arrow", BLUE_TEXT, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("split, the tick", BLUE_TEXT, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("split, the tick's ring", BLUE, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("accent rule", BLUE, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("image edge", BLUE, ((NAVY, 1.0),), NAVY, NON_TEXT),
)


def contrast_report() -> list[tuple[str, float, float]]:
    return [(name, round(contrast(over(((fg, 1.0),), "#000000"), over(layers, base)), 2),
             need(px))
            for name, fg, layers, base, px in TEXT_PAIRS]
