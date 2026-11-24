"""The ATLAS Academy tutorial series: what each YouTube episode shows and says.

Miro, 2026-10-09: "post YouTube videos on demos and how it works and
actually teaching people how to use n8n workflows ... same with Claude: how
to use MCP and what it is", every video with the same intro and ending, the
videos in playlist series, 4-10 minutes, beginner language, and
the store (ACADEMY_PUBLIC_ORIGIN) in every description and on the end card.

Miro, 2026-10-10: "look at other videos and check the tags and hashtags they
use and the titles, improve ours, and the thumbnail and the content as well
-- don't make it boring, make it interactive". And a channel welcome video
(about two minutes) that goes out before either series. And the intro is a
fixed opener ("... My name is Myron, and welcome to ATLAS Academy.") plus a
per-episode topic line said right after it.

Everything said here is code-held copy. Nothing is generated at run time, so
nothing can drift into a number, a price or a claim nobody checked. Every
product fact was read on the official pages on 2026-10-09:

  n8n   docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
        (Test URL vs Production URL; "Registered when you publish the
        workflow"; methods; production runs are not shown in the editor,
        they are in the Executions tab)
        docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/
        (Method, URL, Authentication, Send Query/Headers/Body, Import cURL)
        docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack/
        (Message > Send, Slack credentials: OAuth2 or Access Token)
        docs.n8n.io/code/expressions/  ({{ $json.body.city }})
        docs.n8n.io/workflows/export-import/  (Import from File / URL;
        "Exported workflow JSON files include credential names and IDs")
        github.com/n8n-io/n8n packages/nodes-base: node type names, and the
        parameter names in the example JSON (httpMethod, path, method, url,
        sendQuery, queryParameters, select, channelId, text)
  MCP   modelcontextprotocol.io/docs/getting-started/intro  ("an open-source
        standard for connecting AI applications to external systems", the
        USB-C comparison)
        modelcontextprotocol.io/docs/develop/build-server  (tools, resources,
        prompts; tools are called "with user approval"; `uv add "mcp[cli]"`;
        `from mcp.server import MCPServer`; `@mcp.tool()`;
        `mcp.run(transport="stdio")`; never write to stdout in a stdio
        server; claude_desktop_config.json "mcpServers"; restart Claude for
        Desktop)
        code.claude.com/docs/en/mcp  (`claude mcp add <name> -- <command>`,
        `--transport http`, `/mcp`, `claude mcp list`)
  A second source (Perplexity, relayed 2026-10-09) showed the older
  `from mcp.server.fastmcp import FastMCP` and "activate the workflow"; the
  official pages read the same day say MCPServer and "publish", and those
  are what the scripts say.

What the top videos in the niche do (YouTube Data API, the most-viewed
videos since 2025-10 for "n8n tutorial for beginners" and "claude mcp
tutorial", pulled 2026-10-10, .claude/competitors-2026-10-10.json):
  * titles lead with a benefit or a curiosity hook and carry the year
    ("How I'd Learn n8n if I had to Start Over in 2026", "How Model Context
    Protocol (MCP) actually works", "n8n Tutorial for Beginners 2026: ...");
  * tags repeat the head terms ("n8n", "n8n tutorial", "n8n tutorial for
    beginners", "n8n workflow", "n8n automation", "ai automation", "claude
    ai", "claude mcp") and pad the rest with creators' names, sponsors and
    money phrases -- which ours never carry (no other creator or brand, no
    income claim, no clickbait the video does not deliver);
  * they run 8-19 minutes. Ours stay short on purpose: an "under five
    minutes" topic line is allowed only while the teaching body measures
    under 300 s (tutorials.claim_refusal).

Shapes: a Scene is one chapter, or -- with chapter="" -- a beat inside the
chapter before it (a quiz, a pause, the subscribe reminder: an interrupt,
never its own chapter, since YouTube refuses a chapter under 10 s). `said`
is its narration, one line per build step; for the list kinds step k shows
items[:k+1], so `len(said) == len(items)`. A title, code or hook scene shows
everything from its first step (a code scene's `marks` lights one line range
per step). A quiz says its question, counts down 3-2-1 in silence, then
says the answer (`answer` is the right item; -1 is a poll with no right
answer and no countdown). A pause scene ends with a silent hold so the
viewer can stop the video and try.

Miro, 2026-10-11: "if you are doing a live demo or lab ... switch to a web
browser as the background to do the live demo and explain step by step". A
"demo" scene's picture is a real screen recording (`demo` names it in
tutorial_demos.DEMOS: n8n in a local container, the official MCP
Inspector, a terminal, an editor), one narrated line per recorded step,
`len(said) == len(steps)`. Every other scene sits on a 3D loop picked by
its topic (`backdrop`, or derived from its kind and words), so the picture
changes with what is being said.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from .. import store_origin
from .tutorial_demos import DEMOS

STORE_URL = store_origin.url().rstrip("/")     # https://<host>/academy
SCENE_KINDS = ("title", "bullets", "steps", "diagram", "code", "hook", "payoff", "quiz",
               "pause", "subscribe", "checklist", "playlists", "demo", "lab")
LIST_KINDS = ("bullets", "steps", "diagram", "pause", "checklist", "playlists", "lab")
# Beats that interrupt the lesson and belong to the chapter before them.
BEAT_KINDS = ("quiz", "pause", "subscribe")
COUNTDOWN = 3                  # seconds, said in silence, before a quiz answer
PAUSE_HOLD = 4.0               # seconds of silence after "pause and try this"
UNDER_FIVE = re.compile(r"\bunder (five|5) minutes\b", re.IGNORECASE)
UNDER_FIVE_SECONDS = 300.0

# Miro, 2026-10-12: "the voice should have feelings and laughter and make
# jokes ... don't make him sound like a robot". The narration may carry Fish
# S2 delivery cues in square brackets (docs.fish.audio core-features/emotions,
# read 2026-10-12: "[excited]", "[chuckling]", "[sighing]", "[emphasis]",
# "[break]" a short pause, natural-language cues such as "[laughing softly]").
# Only these, so a bracket that is part of what is said can never be eaten.
# They reach the S2 voice only (tutorials.voiced); plain() takes them out of
# everything shown, counted or timed, and out of a voice that would read
# them aloud (S1 and the preview's `say`).
CUE_WORDS = ("excited", "happy", "delighted", "confident", "curious", "surprised",
             "empathetic", "relaxed", "satisfied", "chuckling", "laughing",
             "laughing softly", "sighing", "emphasis", "break", "long-break",
             "whispering", "soft tone")
CUE = re.compile(r"\s*\[(?:" + "|".join(
    re.escape(w) for w in sorted(CUE_WORDS, key=len, reverse=True)) + r")\]\s*")


def plain(text: str) -> str:
    """`text` without its delivery cues: what is shown and counted."""
    out = CUE.sub(" ", text)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def cues(text: str) -> list[str]:
    """The delivery cues in `text`, in order."""
    return [m.strip().strip("[]") for m in CUE.findall(text)]

# The open and close. Miro, 2026-10-10: the opener word for word. Miro,
# 2026-10-12: "welcome to my new channel intro is only for intro video not
# all the other ones" -- INTRO_SAID opens the channel welcome only. Since
# 2026-10-17 (Miro; the Atlas Academy video system, narration.md) a lesson
# opens on its result, never a welcome: opening_lines().
INTRO_SAID = ("Welcome, guys, to my new channel! My name is Myron, and welcome to "
              "ATLAS Academy.")
INTRO_SHOWN = "Welcome to ATLAS Academy"
INTRO_BYLINE = "Myron · ATLAS Academy"

LEVEL_NAMES = {0: "Basics", 1: "Beginner", 2: "Intermediate", 3: "Advanced"}
LEVEL_TAGS = {0: "beginner", 1: "beginner", 2: "intermediate", 3: "advanced"}


@dataclass(frozen=True)
class Scene:
    chapter: str                 # the chapter title; "" = a beat in the chapter before
    kind: str                    # one of SCENE_KINDS
    heading: str
    said: tuple[str, ...]
    items: tuple[str, ...] = ()
    code: str = ""
    caption: str = ""            # a small line under a code panel / a quiz's answer
    marks: tuple[tuple[int, int], ...] = ()   # code: lines [a, b) lit on step k
    answer: int = -1             # quiz: the right item; -1 a poll
    # 2026-10-11, Miro: "change background depending on what you are talking
    # about". The 3D loop under this scene, by topic (tutorial_design.TOPICS);
    # "" = derived from the kind and the words (tutorial_design.backdrops).
    backdrop: str = ""
    # A "demo" scene: the key of a screen recording (tutorial_demos.DEMOS)
    # that is the picture, one narrated line per recorded step.
    demo: str = ""
    # A "lab" scene: the file (a key of LAB_FILES) the viewer downloads from
    # STORE_URL/free/<resource> to do the exercise.
    resource: str = ""
    # The teaching components (Miro, 2026-10-17; docs/academy-video-system
    # components, .video-gap.md batch 4). Each is drawn only where the script
    # fills it in: the defaults draw nothing, and the renderer never invents
    # one. In repr, so in tutorials.script_key (they were kept out until the
    # combined bump of 2026-10-18): a change to them renders the draft again.
    # StepMap: this scene's node in Episode.steps_map (0-based); -1 = none.
    map_step: int = -1
    # LowerThird: (tool name, one-line role) the first time the tool is on
    # screen, drawn on this scene's first step only; () = none.
    introduces: tuple[str, str] | tuple[()] = ()
    # CheckpointTag: (step, what to look for) -- after that step the picture
    # holds silent for `hold-tag` with "You should now see" and the sentence
    # (`backticks` mark the one code value). () = none.
    checks: tuple[tuple[int, str], ...] = ()
    # ErrorRecovery: the real error, copied, and its cause, fix and check.
    error: ErrorCard | None = None
    # WorkflowTrace (a diagram, or the payoff the opening traces): per node
    # of `items`, the token that travels INTO it ("" none; value-tag, one
    # short key: value) and the real value seen at it ("" none). Copied from
    # the executed workflow, never written for the frame.
    tokens: tuple[str, ...] = ()
    io: tuple[str, ...] = ()
    # True only when the script itself shows this step's result tested on
    # camera: then, and only then, a checkpoint, a traced node or the
    # recovery's last row is `success` with its check. Never inferred.
    verified: bool = False


@dataclass(frozen=True)
class ErrorCard:
    """ErrorRecovery's words: `text` is the error exactly as the app shows it
    (copied, never paraphrased) and `source` where it was copied from (a docs
    URL, or demo key:step); `cause` in one sentence, `fix` naming the exact
    control path, `check` the value seen after the fix."""
    text: str
    source: str
    cause: str
    fix: str
    check: str


# ErrorRecovery's rows after the error itself, one narrated line each.
ERROR_ROWS = ("Cause", "Fix", "Check")
ERROR_ROW_VERIFIED = "Verified"     # the last row's label, only when verified


def error_scene(chapter: str, heading: str, card: ErrorCard,
                said: tuple[str, str, str, str], *, verified: bool = False,
                backdrop: str = "") -> Scene:
    """The error and its recovery: the real error first, then Cause, Fix and
    Check, one narrated line each. A bullets scene to everything else (its
    items are the four lines), drawn as the ErrorRecovery card."""
    return Scene(chapter, "bullets", heading, said,
                 items=(card.text, card.cause, card.fix, card.check), error=card,
                 verified=verified, backdrop=backdrop)


# docs/academy-reference-learnings.md (Miro, 2026-10-11, "learn from them"):
# the lab videos follow every build with a hands-on exercise and a file to
# import; we end every n8n lesson with one, and add what we found nowhere:
# the common error and its fix, and a challenge with no answer shown.
LAB_LABELS = ("Do", "Expect", "Stuck?", "Challenge")
LAB_DIR = "free"                 # the store path the lab files are served from


def lab_url(resource: str) -> str:
    """Where a lab file is downloaded: the store itself, never a third party."""
    return f"{STORE_URL}/{LAB_DIR}/{resource}"


def lab_scene(chapter: str, heading: str, resource: str, *, do: str, expect: str,
              stuck: str, challenge: str, said: tuple[str, str, str, str],
              backdrop: str = "") -> Scene:
    """A lab: the exercise, the result to expect, the common error and its
    fix, and a challenge -- one narrated line each -- with its download."""
    return Scene(chapter, "lab", heading, said, items=(do, expect, stuck, challenge),
                 resource=resource, backdrop=backdrop)


# docs/academy-youtube-plan.md rule 17 (Miro, 2026-10-13): a lesson's first
# scene after the opener is its payoff, in a chapter of this name. (A later
# task cuts a Short from the payoff beat's timings in the manifest.)
PAYOFF_CHAPTER = "Here's what you'll have"
PAYOFF_HEADING = "Here's what you'll have"


def payoff_scene(said: str, code: str, *, items: tuple[str, ...],
                 caption: str = "") -> Scene:
    """The payoff: one spoken line of what the viewer will have at the end,
    the hook's flow boxes, and the result in one line in the bubble."""
    return Scene(PAYOFF_CHAPTER, "payoff", PAYOFF_HEADING, (said,), items=items,
                 code=code, caption=caption)


def demo_scene(chapter: str, heading: str, key: str, said: tuple[str, ...],
               backdrop: str = "") -> Scene:
    """A live-demo scene: the recording `key` is the picture, its steps'
    captions are the items, one narrated line per recorded step."""
    return Scene(chapter, "demo", heading, said,
                 items=tuple(st.caption for st in DEMOS[key].steps), demo=key,
                 backdrop=backdrop)


@dataclass(frozen=True)
class Episode:
    number: int
    slug: str
    title: str                   # the YouTube title (<= 100 characters)
    thumb_title: str             # two short lines, "\n" between; the 2nd is highlighted
    summary: str                 # the description's opening paragraph
    scenes: tuple[Scene, ...]
    # Miro, 2026-10-10: "teach the basics first and then advanced, 0 to
    # advanced". 0 Basics (assumes nothing) · 1 Beginner · 2 Intermediate ·
    # 3 Advanced. Required, never defaulted: a forgotten level must not
    # quietly read as "Basics".
    level: int
    links: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()
    # Up to three, shown above the title by YouTube (support.google.com
    # /youtube/answer/6390658, read 2026-10-10); more than that is noise.
    hashtags: tuple[str, ...] = ()
    # One concrete question for the comments -- the engagement line.
    ask: str = ""
    # Said right after the fixed intro, on its own slide ("" = none).
    topic_said: str = ""
    topic_shown: str = ""
    # The finished result, shown first, right after the topic line.
    hook: Scene | None = None
    # docs/academy-youtube-plan.md rule 17 (Miro, 2026-10-13): "the first
    # seconds are the payoff". One line, said right after the opener and
    # before the hook: what the viewer will have at the end, its result in
    # the bubble from the first frame. None = the channel welcome.
    payoff: Scene | None = None
    min_seconds: float = 240.0   # Miro: 4 to 10 minutes; the welcome 90-180 s
    max_seconds: float = 600.0
    # A planned episode has an outline and no scenes yet: the scheduler skips
    # it ("script not written") and nothing half-made is ever rendered.
    outline: tuple[str, ...] = ()
    # docs/academy-youtube-plan.md (Miro, 2026-10-11): the title is the
    # level's formula filled with this hook (and, at Level 3, the benefit)
    # and the measured minutes -- title_for(). "" keeps `title` as written
    # (the welcome; a planned episode until it is scripted).
    title_hook: str = ""
    title_benefit: str = ""
    # Which level's formula titles it; None = its own level. The plan's
    # rule 2: an episode that explains rather than builds uses the Learn
    # formula (Level 2's) whatever its level -- "Build Your First" is a
    # promise the video must keep.
    title_formula: int | None = None
    # docs/academy-youtube-plan.md rule 10: what you need before you start,
    # said in the hook and written under the summary ("" items = none).
    needs: tuple[str, ...] = ()
    # Lab files (keys of LAB_FILES) linked in the description besides the
    # ones a lab scene names (downloads_of).
    downloads: tuple[str, ...] = ()
    # Miro, 2026-10-12: what OPENER_SAID says "today we'll be learning about"
    # -- a short plain phrase, filled from OPENER_TOPICS by titled(). "" =
    # the channel welcome, which opens with INTRO_SAID instead.
    opener_topic: str = ""
    # The Atlas Academy video system (Miro, 2026-10-17; docs/academy-video-
    # system, ResultTitle and EndCard): the finished result as a noun phrase
    # of RESULT_WORDS_MAX words or fewer, sentence case ("A proofreader
    # subagent"). The opening's badge, "Here is the result: ..." and the end
    # card's "You now have ..." say it; the title and the thumbnail can share
    # it (result_of). Filled from RESULTS by titled(); "" = result_of falls back.
    result: str = ""
    # True only when the script itself shows the result tested on camera:
    # then, and only then, the badge is `success` with its check. Never
    # inferred by the renderer.
    verified: bool = False
    # StepMap (docs/academy-video-system/components/StepMap): the episode's
    # workflow in STEPS_MAP_MAX nodes or fewer, named as the app names them;
    # a scene's map_step lights one. () = no map. In repr, so in the key.
    steps_map: tuple[str, ...] = ()

    @property
    def scripted(self) -> bool:
        return bool(self.scenes)

    @property
    def formula_level(self) -> int:
        """The level whose TITLE_FORMULAS / TITLE_PATTERNS entry titles it."""
        return self.level if self.title_formula is None else self.title_formula

    @property
    def level_label(self) -> str:
        """"Level 0 · Basics" -- data for the frames, the thumbnail and the
        description's first line."""
        return f"Level {self.level} · {LEVEL_NAMES[self.level]}"

    @property
    def level_tag(self) -> str:
        return LEVEL_TAGS[self.level]


STEPS_MAP_MAX = 4                    # StepMap: "keep it to four nodes"


def components_refusal(ep: Episode) -> str | None:
    """Why the episode's teaching-component data cannot be drawn honestly,
    else None: a map of more than four nodes or a scene pointing past it, a
    tool introduced twice, a checkpoint on a step that is never said, a
    token or value with no node, an error with no copied text or source."""
    if len(ep.steps_map) > STEPS_MAP_MAX:
        return f"the step map has {len(ep.steps_map)} nodes; {STEPS_MAP_MAX} at most"
    seen: set[str] = set()
    for sc in ep.scenes:
        where = sc.chapter or sc.heading
        if sc.map_step >= len(ep.steps_map) or sc.map_step < -1:
            return f"{where}: map step {sc.map_step} is not on the step map"
        if sc.introduces:
            name, role = sc.introduces
            if not name.strip() or not role.strip():
                return f"{where}: a lower third needs a name and a role"
            if name.lower() in seen:
                return f"{where}: {name} is introduced twice"
            seen.add(name.lower())
        for step, look in sc.checks:
            if not 0 <= step < len(sc.said) or not look.strip():
                return f"{where}: a checkpoint on step {step} with nothing to look for"
        if len(sc.tokens) > len(sc.items) or len(sc.io) > len(sc.items):
            return f"{where}: a trace token or value with no node"
        if sc.tokens and sc.tokens[0]:
            return f"{where}: nothing travels into the first node"
        if sc.error is not None:
            card = sc.error
            if not all(x.strip() for x in (card.text, card.source, card.cause, card.fix,
                                            card.check)):
                return f"{where}: an error card needs its copied text, source, cause, " \
                       "fix and check"
            if len(sc.said) != 1 + len(ERROR_ROWS):
                return f"{where}: an error card is said in {1 + len(ERROR_ROWS)} lines"
    return None


@dataclass(frozen=True)
class Series:
    key: str
    playlist: str                # "" = no playlist (the channel welcome)
    playlist_description: str
    mark: str                    # the series' short name on frames
    episodes: tuple[Episode, ...] = field(default_factory=tuple)

    def episode(self, number: int) -> Episode | None:
        return next((e for e in self.episodes if e.number == number), None)


# ----------------------------------------------------------- the welcome

WELCOME = Episode(
    number=1,
    slug="welcome",
    level=0,
    title=("Welcome to ATLAS Academy: Free AI Automation Tutorials for Beginners "
           "(n8n, Claude MCP)"),
    thumb_title="Welcome to\nATLAS Academy",
    summary=("Welcome to ATLAS Academy: beginner tutorials on AI automation, in plain "
             "words and step by step, from the very basics to advanced. The first "
             "two series are Claude and n8n, with more to come. Every video is free here on "
             "YouTube, and the workflow JSON and the code are on screen so you can "
             "copy them and try it yourself."),
    tags=("ATLAS Academy", "ai automation", "ai automation for beginners",
          "ai automation tutorial", "learn ai automation", "automation for beginners",
          "n8n", "n8n tutorial for beginners", "n8n workflow", "claude mcp",
          "claude mcp tutorial", "model context protocol", "mcp tutorial",
          "workflow automation", "no code automation", "free ai tutorials",
          "ai tutorials for beginners", "beginner"),
    hashtags=("#AIAutomation", "#n8n", "#ClaudeAI"),
    ask="Which should get episode two first: n8n or Claude and MCP? Tell me below.",
    min_seconds=90.0, max_seconds=180.0,
    scenes=(
        Scene("What this channel makes", "bullets", "What you'll learn here", backdrop="welcome", items=(
            "AI automation, for beginners",
            "From zero to advanced: basics first",
            "Planned: a real project, small business, agents, local AI",
        ), said=(
            ("[excited] This channel is about AI automation, made for beginners. "
             "Never built an automation before? Perfect. You're in the right place."),
            ("Every series goes from zero to advanced. The basics come first, so "
             "nothing is assumed. We start with Claude Mastery, from your first chat "
             "to MCP, skills and connectors, and n8n Workflows from Zero."),
            ("After that, the plan is a real project built with both, automations "
             "for small businesses, AI agents explained simply, and running AI on "
             "your own machine."))),
        Scene("How it works", "steps", "How every video works", backdrop="workflow", items=(
            "Plain words: every new term explained first",
            "Step by step, to one small thing that works",
            "About five minutes of teaching",
        ), said=(
            ("Every video uses plain words. No jargon soup. [chuckling] When a new "
             "term shows up, I explain it before we use it."),
            ("We go step by step, and every video ends with one small thing that "
             "actually works."),
            ("And each lesson is short: about five minutes of teaching, so you can "
             "follow along in one sitting. [break] Coffee optional."))),
        Scene("", "quiz", "Which series do you want first?", items=(
            "Claude + MCP", "n8n workflows",
        ), said=(
            ("Quick question for you. Which series should get its second episode "
             "first? Comment A or B."),
            ("And tell me what you'd like to learn after that. The comments shape "
             "this channel."))),
        Scene("Free and hands-on", "bullets", "Free, and hands-on", items=(
            "Every video is free on YouTube",
            "The workflow JSON and the code, on screen to copy",
            "Pause, try it, then carry on",
        ), said=(
            "Every video is free, right here on YouTube.",
            ("You get the workflow JSON and the code on screen, so you can copy "
             "them and try it yourself."),
            ("[happy] And there are pause moments and quick quizzes along the way, so "
             "you build it with me, instead of just watching."))),
        Scene("Coming up", "playlists", "Coming up first", items=(
            "Claude Mastery: MCP, Skills & Connectors\nEp 0: what Claude is, and how to use it",
            "n8n Workflows from Zero\nEp 0: what automation and n8n are",
        ), said=(
            ("Here's what's coming. Claude Mastery starts at zero: what Claude is, "
             "and how to use it well. Then MCP, connecting Claude to your own tools."),
            ("And n8n Workflows from Zero starts with what automation is and a tour "
             "of the editor, then your first real workflow. If you want to go deeper "
             f"later, there's more at {store_origin.spoken()}."))),
    ),
)

# ------------------------------------------------- Level 0: the basics first
#
# Miro, 2026-10-10: "teach the basics first and then advanced, 0 to
# advanced, for all series". Each series now opens with a Level 0 episode
# that assumes nothing. They are numbered 0 so no episode already drafted
# or posted changes its (series, number) -- that pair keys the posts, the
# review drafts, the Slack cards and the render folders.
#
# Facts read on the official pages on 2026-10-10:
#   Claude  code.claude.com/docs/en/overview  (Claude Code "is an agentic
#           coding tool that reads your codebase, edits files, runs
#           commands, and integrates with your development tools.
#           Available in your terminal, IDE, desktop app, and browser.")
#           support.claude.com/en/articles/9517075-what-are-projects
#           (Projects: "self-contained workspaces with their own chat
#           histories and knowledge bases"; files in the knowledge base;
#           project instructions)
#           support.claude.com/en/articles/8241126-uploading-files-to-claude
#           (the "+" button in the lower left of the chat box, or drag and
#           drop; PDF, DOCX, CSV, TXT and more; JPEG, PNG, GIF, WebP)
#           No price or plan is said: none was read from claude.com/pricing.
#   n8n     docs.n8n.io/glossary/  (workflow: "a collection of nodes that
#           automate a process"; trigger node; execution: "a single run of
#           a workflow"; credentials; the canvas; the editor UI)
#           docs.n8n.io/integrations/builtin/node-types/  (triggers "start a
#           workflow in response to specific events or conditions"; actions
#           "represent specific tasks within a workflow")
#           docs.n8n.io/choose-n8n/  (n8n Cloud: "Fully managed, no setup
#           or maintenance required"; self-hosted: on your own
#           infrastructure, technical expertise needed)
#           docs.n8n.io/try-it-out/quickstart/  ("Add first step", "Execute
#           Workflow", the OUTPUT section)
#   Agents  anthropic.com/engineering/building-effective-agents
#           (workflows: "orchestrated through predefined code paths";
#           agents: LLMs that "dynamically direct their own processes and
#           tool usage"; "LLMs using tools based on environmental feedback
#           in a loop"; pause for human feedback at checkpoints; stopping
#           conditions; start with the simplest solution)

CLAUDE_EP0 = Episode(
    number=0,
    slug="what-is-claude",
    level=0,
    title="What Is Claude and How to Use It: Claude AI Basics for Total Beginners (2026)",
    thumb_title="What is\nClaude?",
    summary=("Claude basics for total beginners: what Claude is, how to start your "
             "first chat at claude.ai, how to ask so you get a useful answer, how to "
             "add files, what Projects are for, and what Claude Code is, in plain "
             "words. Two quick quizzes inside: comment your answers."),
    links=(("Claude", "https://claude.ai"),
           ("Uploading files to Claude",
            "https://support.claude.com/en/articles/8241126-uploading-files-to-claude"),
           ("What are Projects?",
            "https://support.claude.com/en/articles/9517075-what-are-projects"),
           ("Claude Code overview", "https://code.claude.com/docs/en/overview")),
    # Beginner phrases first (Miro's 0-to-advanced ask), then the researched
    # head terms the Claude videos tag ("claude ai", "claude ai tutorial",
    # "how to use claude", "claude code").
    tags=("claude for beginners", "how to use claude", "what is claude ai",
          "claude ai", "claude ai tutorial", "claude tutorial for beginners",
          "how to use claude ai", "claude basics", "claude projects",
          "claude file upload", "what is claude code", "claude code", "anthropic claude",
          "ai for beginners", "ai assistant", "beginner", "ATLAS Academy"),
    hashtags=("#ClaudeAI", "#AIForBeginners", "#Claude"),
    ask=("What's the first thing you'd ask Claude to help you with? Tell me in "
         "the comments."),
    topic_said=("Today we're starting from zero: what Claude is, and how to actually "
                "use it, step by step."),
    topic_shown="What Claude is, and how to use it",
    payoff=payoff_scene(
        ("By the end, you'll start a chat, ask Claude for something useful, and get "
         "a draft back you can actually use."),
        "Claude: Here's a draft you can edit…",
        items=(
            "Chat\nask in plain words",
            "Files\nadd a PDF or a photo",
            "Projects\nkeep work together",
        ),
        caption="claude.ai"),
    hook=Scene("What you'll learn", "hook", "Here's where we're going", items=(
        "Chat\nask in plain words",
        "Files\nadd a PDF or a photo",
        "Projects\nkeep work together",
    ), code=("You: Turn these notes into a short email to my team\n"
             "Claude: Here's a draft you can edit…"),
        caption="claude.ai", said=(
            ("[excited] Then files, Projects, and a first look at Claude Code. No "
             "experience needed. Let's go."),
            ("All you need is a browser and a Claude account, so open claude dot "
             "ai in another tab and try each step with me.")),
    ),
    needs=("a browser", "a Claude account at claude.ai"),
    scenes=(
        Scene("What Claude is", "bullets", "What is Claude?", items=(
            "An AI assistant made by Anthropic",
            "You talk to it in plain words",
            "It writes, explains, summarizes and helps you think",
        ), said=(
            ("Claude is an AI assistant made by a company called Anthropic. Think of "
             "it as a very fast helper you can talk to."),
            ("You don't need special commands. No secret codes, no magic words. "
             "[chuckling] You type, or paste, what you want in normal words, the way "
             "you'd ask a person."),
            ("It can write a first draft, explain something hard in simple terms, "
             "summarize a long document, or help you think through a plan."))),
        Scene("", "quiz", "How do you talk to Claude?", items=(
            "With code only", "In plain words", "With special commands",
        ), answer=1, caption="Plain words: ask the way you'd ask a person.", said=(
            "Quick quiz. How do you talk to Claude? Comment your answer before the timer ends.",
            "It's B, plain words. Ask the way you'd ask a person.")),
        Scene("Your first chat", "steps", "Your first chat", items=(
            "Go to claude.ai and sign in",
            "Type your request in the chat box",
            "Read the answer, then ask a follow-up",
        ), said=(
            ("Open your browser and go to claude dot ai, then sign in. There are "
             "also desktop and phone apps, and they work the same way."),
            ("Type your request in the chat box at the bottom and press enter. "
             "That's a chat."),
            ("Read the answer. If it's not quite right, just reply: make it shorter, "
             "or more friendly. Claude remembers what you said earlier in the same "
             "chat."))),
        Scene("Ask so you get a good answer", "bullets", "Ask well: three parts", backdrop="agent", items=(
            "Context: who it's for, what you have",
            "Goal: what you want back",
            "Format: a list, an email, three options",
        ), said=(
            ("Good answers come from good questions. [sighing] Ever typed one vague "
             "line and got a vague answer back? Here's the fix. First, give context: "
             "who it's for and what you already have."),
            "Second, say the goal: what you want back at the end.",
            ("Third, the format: a short list, an email, a table, or three options to "
             "choose from. [emphasis] Those three parts make a huge difference."))),
        Scene("", "pause", "Pause and try this", items=(
            "Open claude.ai and start a chat",
            "Ask with context, goal and format",
        ), said=(
            "Pause the video and try it now. Open claude dot ai and start a chat,",
            ("then ask for something real, with context, a goal and a format. Resume "
             "when you have your answer."))),
        Scene("Add files", "steps", "Add a file to a chat", backdrop="memory", items=(
            "Click + in the lower left of the chat box",
            "Or drag the file into the chat",
            "PDFs, documents, spreadsheets, photos",
        ), said=(
            ("You can also give Claude a file. Click the plus button in the lower "
             "left of the chat box and pick your file."),
            ("Or just drag the file from your computer into the chat window. Yep, "
             "that's it. [chuckling]"),
            ("It works with PDFs, Word documents, CSV and text files, and images. "
             "Then ask: summarize this, or what are the three main points?"))),
        Scene("", "subscribe", "Finding this useful?", said=(
            ("If this is helping, hit subscribe. This whole channel goes from zero "
             "to advanced, one small step at a time."),)),
        Scene("Projects", "bullets", "Projects keep work together", backdrop="memory", items=(
            "A workspace with its own chats",
            "Add files once, as project knowledge",
            "Project instructions: how Claude should answer",
        ), said=(
            ("Working on the same thing again and again? Use a Project. It's a "
             "workspace with its own chats, all in one place."),
            ("You add your files to the project's knowledge once, and every chat in "
             "that project can use them."),
            ("And you can write project instructions, like: always answer in simple "
             "words, or write like our company does. Claude follows them in every "
             "chat there."))),
        Scene("", "quiz", "Where do you put instructions you reuse?", items=(
            "A new chat every time", "Project instructions", "Your file name",
        ), answer=1, caption="Write them once in the project; every chat uses them.",
            said=(
                ("Last quiz. You want Claude to follow the same instructions every "
                 "time. Where do you put them? Comment your answer."),
                "B, project instructions. Write them once, and every chat there uses them.")),
        Scene("What Claude Code is", "bullets", "And Claude Code?", items=(
            "Claude, for building software",
            "Reads your code, edits files, runs commands",
            "Terminal, code editor, desktop app or browser",
        ), said=(
            ("You'll hear about Claude Code a lot on this channel. It's Claude for "
             "building software and automations."),
            ("Anthropic's docs call it an agentic coding tool: it reads your code, "
             "edits files and runs commands, with you watching and approving."),
            ("It runs in the terminal, in code editors, in the desktop app, and in "
             "the browser. We'll set it up properly in a later episode, step by "
             "step."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Claude: an AI assistant you talk to in plain words",
            "Ask with context, goal and format",
            "Add files with + or drag and drop",
            "Projects for repeat work · Claude Code for building",
        ), said=(
            "Recap. Claude is an AI assistant, and you talk to it in plain words.",
            "Ask with context, a goal and a format, then follow up.",
            "Add files with the plus button, or drag them in.",
            ("[happy] And use Projects for work you repeat. Next in this series: "
             "connecting Claude to your own tools with MCP."))),
    ),
)

# ------------------------------------------------------------ the lab files
#
# Each n8n lesson ends with a lab (docs/academy-youtube-plan.md rule 11), and
# its file is a free download from the store (rule 12): STORE_URL/free/<name>,
# built with the site (site.render), so the file on screen, the file served
# and the file the tests check are this one string. Every lab imports and
# runs with no credential at all: a trigger, the free no-key Open-Meteo city
# search where one is needed, and an Edit Fields node, never a Slack or
# Gmail node. Node types and parameter names read 2026-10-11 in
# github.com/n8n-io/n8n packages/nodes-base: ManualTrigger (manualTrigger,
# v1), ScheduleTrigger (scheduleTrigger, v1-1.4; rule.interval[].field
# "days", triggerAtHour), Set v3.3+ (set; "Fields to Set" = assignments,
# an assignmentCollection, mode "manual" by default), with the Webhook and
# HTTP Request parameters as in N8N_WORKFLOW_JSON below. The Open-Meteo
# answer for Lisbon (results[0]: name, country "Portugal", country_code,
# timezone "Europe/Lisbon") was read the same day.

N8N_LAB0_JSON = """{
  "name": "ATLAS Academy lab: your first run",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab0-message", "name": "message",
            "value": "Hello from my first workflow", "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

N8N_LAB1_JSON = """{
  "name": "ATLAS Academy lab: city info",
  "nodes": [
    { "name": "Webhook", "type": "n8n-nodes-base.webhook",
      "typeVersion": 2, "position": [0, 0],
      "parameters": { "httpMethod": "POST", "path": "city-info" } },
    { "name": "HTTP Request", "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2, "position": [240, 0],
      "parameters": { "method": "GET",
        "url": "https://geocoding-api.open-meteo.com/v1/search",
        "sendQuery": true, "queryParameters": { "parameters": [
          { "name": "name", "value": "={{ $json.body.city }}" } ] } } },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [480, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab1-text", "name": "text",
            "value": "={{ $json.results[0].name }} is in {{ $json.results[0].country }}",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "HTTP Request", "type": "main", "index": 0 }]] },
    "HTTP Request": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

N8N_LAB2_JSON = """{
  "name": "ATLAS Academy lab: every day at nine",
  "nodes": [
    { "name": "Schedule Trigger", "type": "n8n-nodes-base.scheduleTrigger",
      "typeVersion": 1.2, "position": [0, 0],
      "parameters": { "rule": { "interval": [ { "field": "days", "triggerAtHour": 9 } ] } } },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab2-report", "name": "report",
            "value": "=Good morning! Today is {{ $now.toFormat('cccc') }}.",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Schedule Trigger": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

N8N_EP0 = Episode(
    number=0,
    slug="what-is-n8n",
    level=0,
    title="What Is n8n? Automation for Beginners: Triggers, Actions and the Editor (n8n Basics)",
    thumb_title="What is\nn8n?",
    summary=("n8n basics for total beginners: what automation is, what triggers and "
             "actions are, what n8n is, n8n Cloud or self-hosted, and a tour of the "
             "editor so nothing looks scary in the next episode. Two quick quizzes "
             "inside: comment your answers."),
    links=(("n8n glossary", "https://docs.n8n.io/glossary/"),
           ("Choose how to use n8n", "https://docs.n8n.io/choose-n8n/"),
           ("n8n node types", "https://docs.n8n.io/integrations/builtin/node-types/"),
           ("n8n quickstart", "https://docs.n8n.io/try-it-out/quickstart/")),
    tags=("n8n basics", "what is n8n", "automation for beginners", "n8n",
          "n8n tutorial", "n8n tutorial for beginners", "n8n for beginners",
          "what is automation", "n8n explained", "how to use n8n", "learn n8n",
          "n8n cloud vs self hosted", "n8n workflow", "ai automation for beginners",
          "workflow automation", "no code automation", "beginner", "ATLAS Academy"),
    hashtags=("#n8n", "#Automation", "#NoCode"),
    ask=("What's one boring task you'd love to automate first? Tell me in the "
         "comments."),
    topic_said=("Today we're starting from zero: what automation is, what n8n is, and "
                "a quick tour of the editor."),
    topic_shown="What automation is, and what n8n is",
    payoff=payoff_scene(
        ("By the end, you'll know what a trigger, an action and a workflow are, and "
         "you'll find your way around the n8n editor."),
        "New form entry → saved → \"You have a new lead!\"",
        items=(
            "Trigger\na form is filled in",
            "Action\nsave it to a sheet",
            "Action\nmessage you in Slack",
        ),
        caption="it runs by itself, every time"),
    hook=Scene("What you'll learn", "hook", "Here's the idea", items=(
        "Trigger\na form is filled in",
        "Action\nsave it to a sheet",
        "Action\nmessage you in Slack",
    ), code="New form entry → saved → \"You have a new lead!\"",
        caption="it runs by itself, every time", said=(
            ("Here's the idea. Someone fills in a form, and without you touching "
             "anything, it's saved and you get a message."),
            ("[excited] That's automation. And n8n is where you build it, one step "
             "at a time."),
            ("Nothing to install to follow along. And at the end, there's a lab file "
             "to import and run yourself.")),
    ),
    needs=("nothing to install to follow along",
           "for the lab: n8n, Cloud or self-hosted"),
    scenes=(
        Scene("What automation is", "bullets", "What is automation?", items=(
            "A job the computer does for you",
            "The same steps, the same way, every time",
            "You set it up once; it runs by itself",
            "Example: form → spreadsheet → thank-you email",
        ), said=(
            ("Automation means the computer does a job for you, instead of you "
             "clicking through it by hand."),
            ("It follows the same steps, the same way, every time. It doesn't get "
             "bored, and it doesn't forget. [chuckling] Unlike most of us on a "
             "Monday."),
            ("You set it up once, and then it runs by itself, day and night."),
            ("[sighing] Think of a small shop. Every time someone fills in the "
             "contact form, the owner copies their details into a spreadsheet and "
             "sends a thank-you email. Same few steps, again and again. That's the "
             "job automation takes off your hands."))),
        Scene("Triggers and actions", "diagram", "Triggers and actions", items=(
            "Trigger\nsomething happens",
            "Action\none task",
            "Action\nthe next task",
        ), said=(
            ("Every automation has two kinds of parts. A trigger is the thing that "
             "starts it: a form is filled in, an email arrives, or it's nine in the "
             "morning."),
            ("Then actions. An action is one task: save a row, send an email, ask "
             "an AI to write a reply."),
            ("Actions run one after another. Trigger first, then the actions, in "
             "order."))),
        Scene("", "quiz", "\"A new email arrives\" is a…", items=(
            "Trigger", "Action", "Neither",
        ), answer=0, caption="It starts the run: a trigger.", said=(
            ("Quick quiz. A new email arrives. Is that a trigger or an action? "
             "Comment your answer."),
            "It's A, a trigger. It's the thing that starts the run.")),
        Scene("What n8n is", "bullets", "What is n8n?", backdrop="workflow", items=(
            "A tool for building automations",
            "Workflow: a collection of nodes",
            "Node: one step, a trigger or an action",
        ), said=(
            ("n8n is a tool for building these automations, by connecting boxes on a "
             "screen instead of writing a whole program."),
            ("In n8n, an automation is called a workflow. The docs say a workflow is "
             "a collection of nodes that automate a process."),
            ("And a node is one step. Some nodes are triggers, and some are actions "
             "that talk to your apps."))),
        Scene("Cloud or self-hosted", "bullets", "Two ways to use n8n", items=(
            "n8n Cloud: fully managed, no setup",
            "Self-hosted: on your own server, more technical",
            "New? Cloud is the easy start",
        ), said=(
            ("There are two ways to use n8n. n8n Cloud is fully managed: you sign up "
             "and start building, with no setup or maintenance."),
            ("Self-hosted means you run n8n on your own server. You get full "
             "control, but it takes technical skills to install and look after."),
            ("If you're brand new, Cloud is the easy start. We'll cover self-hosting "
             "later in this series."))),
        Scene("", "subscribe", "Is this clicking?", said=(
            ("If this is clicking for you, subscribe. Next episode, we build a real "
             "workflow together."),)),
        # 2026-10-11: the tour is live, in a real n8n (tutorial_demos.N8N_TOUR)
        demo_scene("A tour of the editor", "A tour of the editor", "n8n-tour", said=(
            ("Here's the editor, live. Click Build a workflow, and the big empty area "
             "is the canvas. That's where you place and connect your nodes."),
            ("Click Add first step. A panel opens with every trigger you can choose "
             "from. I'll pick Trigger manually."),
            ("Then I add an Edit Fields node and run the step before it. Open any node: "
             "what came in on the left, its settings in the middle, what comes out in "
             "OUTPUT."),
            ("And Execute workflow runs the whole thing, so you watch each step turn "
             "green. [excited] That green never gets old."))),
        Scene("", "pause", "Pause and try this", items=(
            "Open n8n and create a workflow",
            "Click Add first step and read the triggers",
        ), said=(
            "Pause the video and try it. Open n8n and create a new workflow,",
            ("then click Add first step and scroll through the triggers. Resume when "
             "you've had a look."))),
        Scene("Runs and logins", "bullets", "Two more words", items=(
            "Execution: one run of a workflow",
            "Credentials: your app logins, stored safely",
        ), said=(
            ("Two more words. An execution is one run of a workflow. n8n keeps a "
             "list of them, so you can see what happened."),
            ("And credentials are where n8n keeps your logins for other apps. You "
             "add them once, and nodes use them, so passwords never sit inside a "
             "node."))),
        Scene("", "quiz", "Where do your app logins go?", items=(
            "Inside each node", "Credentials", "The workflow name",
        ), answer=1, caption="Add them once in Credentials; nodes use them.", said=(
            "Last quiz. Where should your app logins go? Comment your answer.",
            "B, Credentials. Add them once, and the nodes use them.")),
        lab_scene("Lab: your first run", "Lab: your first run", "n8n-first-run.json",
                  do="Import n8n-first-run.json into a new workflow",
                  expect="Execute workflow: both nodes green, one message",
                  stuck="No import? Workflow menu → Import from File",
                  challenge="Add a second field with your name; run it again",
                  said=(
                      ("Your lab. Download the lab file from the description, and "
                       "import it into a new workflow."),
                      ("Click Execute workflow. Expect both nodes to turn green, and a "
                       "hello message in the output."),
                      "Stuck? Open the workflow menu and choose Import from File.",
                      ("Your challenge: add a second field with your own name, and run "
                       "it again."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Automation: the same steps, by itself",
            "Trigger starts it · actions do the work",
            "n8n: workflows made of nodes",
            "Canvas, Add first step, Execute Workflow",
        ), said=(
            "Recap. Automation means the computer runs the same steps for you.",
            "A trigger starts it, and actions do the work.",
            "In n8n, a workflow is made of nodes on a canvas.",
            ("[happy] And you know your way around the editor. Next episode: your "
             "first real workflow, step by step."))),
    ),
)

AGENTS_EP1 = Episode(
    number=1,
    slug="what-an-ai-agent-is",
    level=0,
    title="What an AI Agent Actually Is (Explained Simply, No Hype)",
    thumb_title="What's an\nAI agent?",
    summary=("An AI agent in plain words: a model, tools it may use, and a loop that "
             "decides the next step. How it differs from a chatbot and from a fixed "
             "workflow, and where a person stays in charge. Two quick quizzes inside: "
             "comment your answers."),
    links=(("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents"),),
    tags=("what is an ai agent", "ai agents explained", "ai agents", "ai agent tutorial",
          "ai agents for beginners", "ai agent vs chatbot", "ai agent vs workflow",
          "how ai agents work", "ai automation for beginners", "ai for beginners",
          "beginner", "ATLAS Academy"),
    hashtags=("#AIAgents", "#AI", "#Automation"),
    ask=("Name one job you'd trust an AI agent with, and one you never would. Tell "
         "me in the comments."),
    topic_said=("Today we're starting from zero: what an AI agent actually is, with "
                "no hype."),
    topic_shown="What an AI agent actually is",
    payoff=payoff_scene(
        ("You'll finish this able to tell a chatbot, a workflow and an agent apart, "
         "and sort one of your own weekly tasks into the right box."),
        "Agent: checks calendar → drafts invite → asks you to approve",
        items=(
            "Chatbot\nanswers you",
            "Workflow\nfollows fixed steps",
            "Agent\npicks its next step",
        ),
        caption="it asks before anything leaves"),
    hook=Scene("What you'll learn", "hook", "Here's the difference", items=(
        "Chatbot\nanswers you",
        "Workflow\nfollows fixed steps",
        "Agent\npicks its next step",
    ), code=("Goal: find a free time and book a call\n"
             "Agent: checks calendar → drafts invite → asks you to approve"),
        caption="it asks before anything leaves", said=(
            ("Here's the difference in one picture. A chatbot answers you. A "
             "workflow follows fixed steps. An agent picks its own next step."),
            ("That one difference changes what you can hand over, and what you should "
             "keep an eye on."),
            ("You don't need to install anything. Keep one weekly task of yours in "
             "mind: we'll sort it as workflow or agent together."))),
    needs=("nothing to install", "one weekly task of your own to think about"),
    scenes=(
        Scene("A chatbot answers; an agent acts", "bullets", "Chatbot or agent?", backdrop="agent", items=(
            "Chatbot: you ask, it answers",
            "Agent: you give a goal, it takes steps",
            "Steps use tools: search, calendar, email",
        ), said=(
            ("A chatbot is simple: you ask a question, it gives you an answer, and "
             "that's it. If you want something else done, you have to do it yourself, "
             "or ask again. [chuckling] A very polite librarian."),
            ("An agent gets a goal instead, like: find a free time and book a call. "
             "Then it works out the steps by itself."),
            ("To take those steps it uses tools: a web search, your calendar, an "
             "email draft. Tools are how it touches the world."))),
        Scene("The loop", "diagram", "The agent loop", items=(
            "Look\nwhat's the situation?",
            "Decide\npick the next step",
            "Act\nuse a tool",
            "Check\ndid it work?",
        ), said=(
            ("Anthropic describes agents as models using tools based on feedback, "
             "in a loop. First, it looks at the situation."),
            "Then it decides on the next step.",
            "Then it acts, usually by using one tool.",
            ("And it checks the result, then loops again, until the goal is done or "
             "it needs your help. Booking a call might take five turns of this loop: "
             "check the calendar, find a gap, draft the invite, notice a clash, and "
             "try another time. [break] Nobody wrote those five steps in advance. The "
             "agent worked them out as it went, one look at a time."))),
        Scene("", "quiz", "What makes it an agent?", items=(
            "It's very smart", "It picks its own next step", "It talks a lot",
        ), answer=1, caption="An agent directs its own steps and tool use.", said=(
            "Quick quiz. What makes something an agent? Comment your answer.",
            "It's B. An agent picks its own next step and which tool to use.")),
        Scene("Agent or workflow?", "bullets", "Agent or workflow?", items=(
            "Workflow: the steps are written in advance",
            "Agent: the model decides the steps",
            "Start simple; add an agent only when needed",
        ), said=(
            ("A workflow, like the ones we build in n8n, follows steps written in "
             "advance. Same path every time."),
            ("An agent lets the model decide the path. That's more flexible, but "
             "harder to predict, and it usually takes longer, because the model "
             "thinks before every step."),
            ("Anthropic's own advice is to find the simplest solution that works, "
             "and only add complexity when you need it. Often a workflow is "
             "enough."))),
        Scene("", "subscribe", "Finding this useful?", said=(
            ("If this is helping, subscribe. This series goes from the basics to "
             "building agents safely."),)),
        Scene("Three everyday examples", "bullets", "Workflow or agent? Examples", items=(
            "Save every form entry to a sheet: a workflow",
            "Sort your inbox and draft replies: an agent can help",
            "Research a topic across many pages: an agent",
        ), said=(
            ("Let's make it real with three examples. Saving every new form entry to "
             "a spreadsheet is the same every time, so a simple workflow is "
             "perfect."),
            ("[sighing] Sorting your inbox and drafting replies needs judgment: which "
             "email matters, and what to say. An agent can help there, with you "
             "checking the drafts before anything is sent."),
            ("And researching a topic means searching, reading, then deciding what "
             "to look at next. Nobody can write those steps in advance, so that's "
             "agent work."))),
        Scene("", "pause", "Pause and try this", items=(
            "Think of one task you do every week",
            "Fixed steps, or does it need decisions?",
        ), said=(
            "Pause the video and think of one task you do every week.",
            ("Are the steps always the same, or does it need decisions along the "
             "way? Resume when you have your answer."))),
        Scene("A person stays in charge", "bullets", "You stay in charge", backdrop="security", items=(
            "Only the tools it really needs",
            "It asks before anything important",
            "A limit on how many steps it takes",
        ), said=(
            ("Agents can make mistakes, so a person stays in charge. First, give it "
             "only the tools it really needs."),
            ("Second, it pauses and asks you before anything important: sending, "
             "paying, or deleting."),
            ("Third, a stopping rule, like a limit on how many steps it may take, so "
             "it can't loop forever. When it hits the limit, it stops and tells you "
             "what it tried, so you can decide what happens next."))),
        Scene("", "quiz", "An agent wants to send an email. Best setup?", items=(
            "Send it right away", "Ask you first", "Never use email",
        ), answer=1, caption="Approval before anything leaves.", said=(
            "Last quiz. An agent wants to send an email for you. What's the best setup?",
            "B. It asks you first. Approval before anything leaves.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Chatbot answers · agent takes steps",
            "The loop: look, decide, act, check",
            "Workflow first; agent when it's needed",
            "Least tools, approvals, a stopping rule",
        ), said=(
            "Recap. A chatbot answers; an agent takes steps toward a goal.",
            "It works in a loop: look, decide, act, check.",
            "Use a workflow when the steps are fixed, and an agent when they're not.",
            ("[happy] And keep a person in charge. Next: agent or workflow, and how "
             "to choose for your own tasks."))),
    ),
)

# ------------------------------------------------------------------ n8n, ep 1

N8N_WORKFLOW_JSON = """{
  "name": "City info to Slack",
  "nodes": [
    { "name": "Webhook", "type": "n8n-nodes-base.webhook",
      "typeVersion": 2, "position": [0, 0],
      "parameters": { "httpMethod": "POST", "path": "city-info" } },
    { "name": "HTTP Request", "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2, "position": [240, 0],
      "parameters": { "method": "GET",
        "url": "https://geocoding-api.open-meteo.com/v1/search",
        "sendQuery": true, "queryParameters": { "parameters": [
          { "name": "name", "value": "={{ $json.body.city }}" } ] } } },
    { "name": "Slack", "type": "n8n-nodes-base.slack",
      "typeVersion": 2.2, "position": [480, 0],
      "parameters": { "resource": "message", "operation": "post",
        "select": "channel",
        "channelId": { "__rl": true, "mode": "name", "value": "#general" },
        "text": "={{ $json.results[0].name + ' is in ' + $json.results[0].country }}" } }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "HTTP Request", "type": "main", "index": 0 }]] },
    "HTTP Request": { "main": [[{ "node": "Slack", "type": "main", "index": 0 }]] }
  }
}"""

N8N_TEST_CURL = """curl -X POST \\
  https://YOUR-N8N/webhook-test/city-info \\
  -H "Content-Type: application/json" \\
  -d '{"city": "Lisbon"}'"""

N8N_EP1 = Episode(
    number=1,
    slug="n8n-first-workflow",
    level=1,
    # 2026-10-10: the search phrase first (vidIQ and the competitors' tags
    # agree on "n8n tutorial for beginners"), the year as the top titles
    # carry it, then the benefit -- the two things the video delivers.
    title="n8n Tutorial for Beginners 2026: Build and Manage Your First Workflow, Step by Step",
    thumb_title="Your first\nn8n workflow",
    summary=("A beginner n8n tutorial: one real workflow, built and managed step by "
             "step, live in a real n8n. A Webhook starts it, an HTTP Request node asks "
             "a free API (Open-Meteo's city search, no key), and a Slack node posts the "
             "answer. You also get the workflow JSON to import, "
             "how to test it safely, how to publish it, and where to watch its runs. "
             "Two quick quizzes inside: comment your answers."),
    links=(("n8n Webhook node docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/"),
           ("n8n HTTP Request node docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/"),
           ("n8n Slack node docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack/"),
           ("Importing workflows", "https://docs.n8n.io/workflows/export-import/")),
    # Head terms seen in the top n8n videos' tags (2026-10-10: "n8n" x5,
    # "n8n tutorial" x4, "ai automation" x3, "n8n automation" x3, "n8n
    # workflow" x2, "n8n tutorial for beginners" x2, "workflow automation",
    # "ai automation for beginners"), then our own long tail. Their creator
    # names, sponsors and money tags are left out on purpose.
    tags=("n8n", "n8n tutorial", "n8n tutorial for beginners", "n8n for beginners",
          "n8n tutorial 2026", "learn n8n", "how to use n8n", "n8n workflow",
          "n8n workflow tutorial", "n8n automation", "ai automation",
          "ai automation for beginners", "workflow automation", "no code automation",
          "first n8n workflow", "n8n webhook", "n8n http request", "n8n slack",
          "n8n executions", "beginner", "ATLAS Academy"),
    hashtags=("#n8n", "#n8nTutorial", "#Automation"),
    ask=("Which app should we connect this workflow to next: Gmail, Google "
         "Sheets or an AI agent? Tell me in the comments."),
    topic_said=("Today we're looking at exactly how to create an n8n workflow, and "
                "how to manage it, in under five minutes."),
    topic_shown="Create and manage an n8n workflow",
    payoff=payoff_scene(
        ("One web request with a city name goes in, and moments later the answer "
         "lands in Slack, all by itself."),
        "Lisbon is in Portugal",
        items=(
            "Webhook\na city comes in",
            "HTTP Request\nasks a free city API",
            "Slack\nposts the answer",
        ),
        caption="#general"),
    hook=Scene("What you'll build", "hook", "Here's what you'll build", items=(
        "Webhook\na city comes in",
        "HTTP Request\nasks a free city API",
        "Slack\nposts the answer",
    ), code="Lisbon is in Portugal",
        caption="#general", said=(
            ("[excited] Three nodes, wired together on one canvas. All you need is "
             "n8n, Cloud or your own, and no API key. A lab file waits at the end. "
             "Let's build it."),)),
    needs=("n8n, Cloud or self-hosted", "no API key: the city search is free",
           "for the Slack step only: a Slack workspace"),
    # The teaching components (2026-10-18, .video-gap.md batch 4), from this
    # script's own words only: the payoff's three nodes as the StepMap; n8n
    # introduced where it is first on screen; the message the recording's
    # OUTPUT shows, as the checkpoint. Nothing is marked verified (batch 3's
    # rule: no script states a test on camera yet); no error card, since no
    # error text is copied here; no trace token, since no diagram is drawn.
    steps_map=("Webhook", "HTTP Request", "Slack"),
    scenes=(
        Scene("Three words you need", "bullets", "Three words you need", items=(
            "Workflow: the whole automation",
            "Node: one step, one job",
            "Trigger: the node that starts a run",
        ), said=(
            ("Three words first. A workflow is the whole automation: steps connected "
             "in order, like a recipe. [chuckling] One you never cook twice."),
            ("A node is one step that does one job: receive data, call an app, or "
             "send a message."),
            ("And the trigger is the node that starts each run. Ours is a Webhook: "
             "it runs whenever someone calls its web address."))),
        Scene("", "quiz", "Which node starts a workflow?", items=(
            "Any node", "The trigger", "The last node",
        ), answer=1, caption="The trigger: every run starts there.", said=(
            ("Quick quiz. Which node starts a workflow? Comment your answer before "
             "the timer runs out."),
            "It's B, the trigger. Every run starts there.")),
        # 2026-10-11: built live in a real n8n (tutorial_demos.N8N_BUILD and on)
        replace(demo_scene(
            "Step 1: the Webhook node", "Step 1: the Webhook node", "n8n-build", said=(
                ("Let's build it live. Create a workflow, click Add first step, and pick "
                 "the Webhook trigger."),
                ("Set the method to POST and the path to city-info. The path becomes the "
                 "end of its web address."),
                ("Click Listen for test event, and send it a city. [excited] Lisbon "
                 "arrives! The city sits at body, city. Remember that path."))),
            # no checkpoint here: the recorded frame shows the headers, never
            # body.city, so "Lisbon" would be claimed beside an output
            # that does not show it
            map_step=0, introduces=("n8n", "Cloud or self-hosted")),
        Scene("Sending a test request", "code", "The test request",
              code=N8N_TEST_CURL, marks=((0, 2), (3, 4)), map_step=0,
              caption="Replace YOUR-N8N with your n8n address; copy the Test URL from "
                      "the Webhook node.",
              said=(
                  ("That request was this curl command. It posts a city to the Test URL, "
                   "which works while you build."),
                  ("The Production URL takes over once you publish."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add the Webhook node",
            "Send it your own city with the Test URL",
        ), map_step=0, said=(
            "Pause the video and try it now. Add the Webhook node,",
            "then send it your own city. Resume when you see the data arrive.")),
        replace(demo_scene("Step 2: the HTTP Request node", "Step 2: the HTTP Request node",
                           "n8n-api", said=(
                       "Next, add an HTTP Request node after the Webhook. GET is fine.",
                       ("For the URL, we use Open-Meteo's free city search. It needs no "
                        "key, so it's perfect for practice."),
                       ("Turn on Send Query Parameters, call it name, and set the "
                        "value to dollar json dot body dot city. Execute the step, "
                        "and [break] the answer comes back."))), map_step=1),
        # No map_step: Edit Fields stands in for Slack here. The checkpoint
        # sits after step 2, whose recorded OUTPUT shows the message (the
        # payoff's own line); one only, since a second 3.7 s hold takes the
        # cautious estimate past the topic line's "under five minutes".
        replace(demo_scene("Step 3: the message", "Step 3: the message", "n8n-message", said=(
            ("Last, the message. An Edit Fields node builds it from the answer: the "
             "city, and the country it's in. In your workflow, the Slack node goes here."),
            "Execute the step, and the message is ready.",
            ("Then Execute workflow and send one more city. [excited] Every node "
             "turns green. And there it is."))),
            checks=((1, "The message `Lisbon is in Portugal`"),)),
        Scene("Your Slack node", "steps", "Your Slack node", items=(
            "Slack node: Message, Send · pick the channel",
            "Connect Slack credentials once",
        ), map_step=2, said=(
            ("For Slack, add a Slack node: Message, then Send, and pick your channel."),
            "Connect your Slack credentials once.")),
        Scene("", "subscribe", "Finding this useful?", said=(
            ("If this is helping, hit subscribe, so you catch the next episode in "
             "this series."),)),
        Scene("The whole workflow as JSON", "code", "The whole workflow as JSON",
              code=N8N_WORKFLOW_JSON, marks=((2, 17), (18, 22)),
              caption="Import it from the workflow menu: Import from File or Import "
                      "from URL. Then add your own Slack credentials.",
              said=(
                  ("Here's the whole workflow as JSON, with Slack as the last node, "
                   "then the connections."),
                  ("Save it as a file, then choose Import from File in the workflow "
                   "menu, and add your own Slack credentials."))),
        demo_scene("Manage it", "Test, publish, manage", "n8n-manage", said=(
            ("Now manage it. Open any node to see what went in and what came out."),
            ("If a node turns red, [sighing] don't worry, it happens to everyone. "
             "Open it and read the error. Usually it's a URL, a credential, or an "
             "expression pointing at the wrong field."),
            ("When it all works, publish the workflow. That's when the Production URL "
             "starts listening."),
            ("Every real run is saved in the Executions tab, so you can always see "
             "what happened."))),
        Scene("", "quiz", "After you publish, where are the real runs?", items=(
            "The editor canvas", "The Executions tab", "Your Slack channel",
        ), answer=1, caption="Production runs don't show in the editor.", said=(
            ("Last quiz. After you publish, where do you see the real runs? Comment "
             "your answer."),
            "B, the Executions tab. Production runs don't show in the editor.")),
        Scene("Keep it safe", "bullets", "Keep it safe", items=(
            "Keys and tokens live in Credentials",
            "Exported JSON includes credential names and IDs",
        ), said=(
            ("Two safety habits. Keep keys and tokens in Credentials, never in a node "
             "field."),
            ("And exported JSON includes credential names and IDs, so check it before "
             "you share it."))),
        lab_scene("Lab: your own city", "Lab: your own city", "n8n-city-info.json",
                  do="Import n8n-city-info.json · send your own city",
                  expect="Edit Fields: \"<your city> is in <its country>\"",
                  stuck="Nothing arrives? Listen for test event first · POST · city-info",
                  challenge="Add a field with the city's time zone",
                  said=(
                      ("Your lab. Download the lab file from the description, import "
                       "it, and send your own city to the Test URL."),
                      "Expect one line in Edit Fields: your city, and the country it's in.",
                      ("Stuck? Click Listen for test event first, and check the method is "
                       "POST and the path is city-info."),
                      ("Your challenge: add a second field with the city's time zone. "
                       "It's already in the answer. Find it."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Webhook catches the request",
            "HTTP Request asks the API",
            "Slack posts the answer",
            "Test, publish, watch Executions",
        ), said=(
            "Recap. The Webhook catches the request.",
            "HTTP Request asks the API, with an expression passing the city along.",
            "Slack posts the answer.",
            ("And you test, publish, then watch the Executions tab. [happy] That's a "
             "real automation, and you built it."))),
    ),
)

# ------------------------------------------------------------------ MCP, ep 1

MCP_SERVER_PY = '''from mcp.server import MCPServer

mcp = MCPServer("hello")


@mcp.tool()
def greet(name: str) -> str:
    """Say hello to someone by name."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(transport="stdio")'''

MCP_DESKTOP_JSON = """{
  "mcpServers": {
    "hello": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/hello",
               "run", "server.py"]
    }
  }
}"""

MCP_CLAUDE_CODE = """# Claude Code: a local server (everything after -- runs the server)
claude mcp add hello -- uv --directory /ABSOLUTE/PATH/TO/hello run server.py

# a remote server over HTTP
claude mcp add --transport http notion https://mcp.notion.com/mcp

# check what's connected
claude mcp list"""

MCP_EP1 = Episode(
    number=1,
    slug="mcp-what-it-is",
    level=1,
    # 2026-10-10: "claude mcp tutorial" first (vidIQ ~14k/month, rising),
    # "how ... actually works" as Google Cloud Tech's top MCP explainer
    # frames it -- which this video does deliver (host, client, server).
    title="Claude MCP Tutorial 2026: How MCP Actually Works + Your First MCP Server",
    thumb_title="What is\nMCP?",
    summary=("MCP, the Model Context Protocol, explained for beginners: what it is, "
             "how hosts, clients and servers fit together, and the three things a "
             "server can offer (tools, resources and prompts). Then a tiny MCP server "
             "in Python, added to Claude for Desktop and Claude Code. Two quick "
             "quizzes inside: comment your answers."),
    links=(("What is MCP? (official intro)",
            "https://modelcontextprotocol.io/docs/getting-started/intro"),
           ("Build an MCP server (official tutorial)",
            "https://modelcontextprotocol.io/docs/develop/build-server"),
           ("Connect MCP servers to Claude Code", "https://code.claude.com/docs/en/mcp")),
    # vidIQ keyword research, 2026-10-10: "claude mcp" ~91k searches/month,
    # "claude mcp tutorial" ~14k (+160% vs its 30-day baseline), "model
    # context protocol" ~20k (+201%), "claude code mcp" ~29k, "how to use
    # mcp" ~4.9k, "mcp claude desktop" ~4.7k. Plus the head terms the top
    # Claude videos tag (2026-10-10: "claude ai" x3, "claude ai tutorial",
    # "how to use claude", "claude mcp"); their brand and money tags left out.
    tags=("claude mcp", "claude mcp tutorial", "mcp claude", "model context protocol",
          "what is mcp", "mcp explained", "how to use mcp", "mcp server", "mcp tutorial",
          "claude code mcp", "mcp claude desktop", "claude ai", "claude ai tutorial",
          "how to use claude", "claude code", "claude code tutorial", "MCP",
          "python mcp server", "claude mcp 2026", "beginner", "ATLAS Academy"),
    hashtags=("#ClaudeAI", "#MCP", "#ClaudeCode"),
    ask=("What should Claude connect to next through MCP: your email, a "
         "database, or Figma? Tell me in the comments."),
    topic_said=("Today we're looking at what MCP is and how to connect your first "
                "MCP server to Claude, in under five minutes."),
    topic_shown="What MCP is, and your first MCP server",
    payoff=payoff_scene(
        ("You ask Claude to greet someone, Claude asks to use a tool, and your own "
         "little server answers."),
        "Tool: Hello, Ana!",
        items=(
            "Claude\nthe AI app you use",
            "MCP\nthe standard plug",
            "Your server\none greet tool",
        ),
        caption="Claude asks before it uses a tool"),
    hook=Scene("What you'll build", "hook", "Here's what you'll build", items=(
        "Claude\nthe AI app you use",
        "MCP\nthe standard plug",
        "Your server\none greet tool",
    ), code='You: Say hi to Ana\nClaude uses greet(name="Ana")\nTool: Hello, Ana!',
        caption="Claude asks before it uses a tool", said=(
            ("[excited] That tool is a few lines of Python, plugged in through MCP. "
             "Let's build it."),
            ("You'll need uv and a Claude app. The server file is in the "
             "description."))),
    needs=("uv (it installs Python for you)", "Node.js, for the MCP Inspector's npx",
           "Claude for Desktop or Claude Code"),
    downloads=("mcp-hello-server.py",),
    scenes=(
        Scene("What is MCP?", "bullets", "What is MCP?", items=(
            "Model Context Protocol",
            "Like USB-C, for AI apps",
            "Build a server once, use it in any MCP app",
        ), said=(
            ("MCP stands for Model Context Protocol. The official site calls it an "
             "open-source standard for connecting AI applications to external "
             "systems."),
            ("The docs compare it to USB-C: one standard plug, so things just "
             "connect. [chuckling] Goodbye, drawer of cables."),
            ("Build a server once, and any app that speaks MCP, like Claude or "
             "Claude Code, can use it. That's the whole idea: build once, plug in "
             "anywhere."))),
        Scene("A tiny MCP server", "code", "A tiny MCP server in Python", code=MCP_SERVER_PY,
              marks=((0, 3), (5, 9), (11, 13)),
              caption="First: uv init hello, cd hello, uv venv, then uv add \"mcp[cli]\". "
                      "Save this as server.py.",
              said=(
                  ("Here's a complete server in Python, with the official SDK. Set up "
                   "with uv: uv init hello, then uv add, mcp with the cli extra."),
                  ("The mcp dot tool decorator turns a normal function into a tool. Its "
                   "type hints and docstring tell Claude what it does."),
                  ("At the bottom it runs over stdio, with no print anywhere. [break] "
                   "On purpose."))),
        Scene("Hosts, clients and servers", "diagram", "Who talks to whom", items=(
            "Host\nthe AI app: Claude",
            "Client\none per server, inside it",
            "Server\nyour program",
        ), said=(
            "The host is the AI app you use, like Claude for Desktop or Claude Code.",
            ("Inside it, there's one client per server. The host handles that "
             "part for you, so you never write a client yourself."),
            ("And the server is your program. It offers something useful, and it's "
             "the part you build. One host can talk to many servers at once, each "
             "through its own client."))),
        Scene("", "quiz", "Which part do you build?", items=(
            "The host", "The client", "The server",
        ), answer=2, caption="The host and its clients come with the app.", said=(
            "Quick quiz. Which of the three do you build yourself? Comment your answer.",
            "C, the server. The host and its clients come with the app.")),
        Scene("What a server can offer", "bullets", "What a server can offer", items=(
            "Tools: functions the AI can call",
            "Resources: file-like data to read",
            "Prompts: ready-made templates",
        ), said=(
            ("A server can offer three things. Tools are functions the AI can call, "
             "and you approve each use."),
            "Resources are file-like data the app can read, like a file's contents.",
            "And prompts are ready-made templates for common tasks. Ours offers one tool.")),
        Scene("Local or remote", "bullets", "Local or remote", items=(
            "Local: on your computer, over stdio · Remote: over HTTP",
            "A stdio server never prints to stdout",
        ), said=(
            ("A local server runs on your computer and talks over standard input and "
             "output, called stdio. A remote one talks over HTTP."),
            ("One rule for local servers: [emphasis] never print to standard output. "
             "It corrupts the messages. Log to standard error instead."))),
        Scene("", "subscribe", "Is this clicking?", said=(
            ("If this is clicking for you, subscribe, so you catch the next episode "
             "in this series."),)),
        # 2026-10-11: set up, tested and connected live (tutorial_demos.MCP_*)
        demo_scene("Live: set it up", "Set it up, live", "mcp-setup", said=(
            "Let's do it live. uv init hello makes the project, and we step into it.",
            "uv add, mcp with the cli extra, installs the official Python SDK.",
            "Save the code as server.py. Here it is: one tool, greet.",
            ("Now test it before Claude sees it. This starts the official MCP "
             "Inspector in your browser."))),
        Scene("", "pause", "Pause and try this", items=(
            "Install the SDK: uv add \"mcp[cli]\"",
            "Save the code as server.py",
        ), said=(
            "Pause the video and try it. Install the SDK with uv add,",
            "then save the code as server.py. Resume when you're ready.")),
        demo_scene("Test it in the Inspector", "Test it in the MCP Inspector", "mcp-inspector",
                   said=(
                       ("Add the server: the command is uv, and the arguments run "
                        "server.py in hello, next to where the Inspector started."),
                       "Connect. The Inspector starts your server over stdio.",
                       "Open Tools. There's greet, with your docstring as its description.",
                       ("Type a name, Ana, and run it. [excited] Hello, Ana! And "
                        "there it is. Your server works."))),
        demo_scene("Add it to Claude for Desktop", "Add it to Claude for Desktop",
                   "mcp-config", said=(
                  ("For Claude for Desktop, open claude desktop config dot json, and add "
                   "your server under mcpServers: a name, a command, and its "
                   "arguments, with the full path."),
                  ("Save, restart Claude for Desktop, and ask it to greet someone. No "
                   "tool? [sighing] Check the path. It must be absolute."))),
        Scene("Add it to Claude Code", "code", "Add it to Claude Code", code=MCP_CLAUDE_CODE,
              marks=((0, 2), (3, 8)),
              caption="Inside Claude Code, /mcp shows each server's status.",
              said=(
                  ("In Claude Code it's one command: claude mcp add, a name, two dashes, "
                   "then the command that starts your server."),
                  ("For a remote server, add the transport http flag and its URL. Then "
                   "claude mcp list shows what's connected, and inside Claude Code the "
                   "slash mcp command shows each server's status too."))),
        Scene("", "quiz", "What breaks a local stdio server?", items=(
            "A print statement", "A docstring", "A type hint",
        ), answer=0, caption="print() writes to stdout and corrupts the messages.", said=(
            "Last quiz. What can break a local stdio server? Comment your answer.",
            ("A, a print statement. It writes to standard output and corrupts the "
             "messages. [chuckling] Sneaky, right?"))),
        Scene("Recap", "checklist", "Recap", items=(
            "MCP: one standard plug for AI apps",
            "Host, client, server: you build the server",
            "Tools, resources and prompts",
            "Connect it: the config file or claude mcp add",
        ), said=(
            "Recap. MCP is one standard plug between AI apps and your tools.",
            "Host, client, server, and you build the server.",
            "Servers offer tools, resources and prompts.",
            ("[happy] And you connect it with the config file or claude mcp add. In "
             "the next videos, we'll build servers that do real work."))),
    ),
)

# --------------------------------------------- scripted: project #1, business #1
#
# Written 2026-10-10 for days three and four of the schedule. Node names
# from docs.n8n.io read the same day: the n8n Form Trigger (its own Test and
# Production URLs, like the Webhook), Google Sheets (Append Row), Gmail
# (Message > Send), Slack (Message > Send), the Anthropic nodes for Claude.
# Nothing here claims a step this series has not built yet: the approval
# step and error handling are named as what later episodes add.

PROJECT_EP1 = Episode(
    number=1,
    slug="project-plan",
    level=1,
    title="Build a Real AI Assistant with Claude + n8n: Plan the Whole Project (2026)",
    thumb_title="Claude +\nn8n project",
    summary=("The plan for this series' real project: a form comes in, Claude drafts "
             "a reply, the request is logged in Google Sheets and Slack gets an alert, "
             "with a person approving before any reply is sent. One screen per piece, "
             "and the order we'll build it in. Two quick quizzes inside."),
    links=(("n8n Form Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.formtrigger/"),
           ("n8n Google Sheets docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets/"),
           ("What is MCP? (official intro)",
            "https://modelcontextprotocol.io/docs/getting-started/intro")),
    tags=("claude n8n mcp tutorial", "connect claude to n8n with mcp",
          "n8n mcp server setup claude", "n8n tutorial", "n8n tutorial for beginners 2026",
          "claude ai", "claude mcp tutorial", "ai automation", "ai automation project",
          "n8n ai", "n8n google sheets", "n8n slack", "beginner", "ATLAS Academy"),
    hashtags=("#n8n", "#ClaudeAI", "#Automation"),
    ask="What would you want this assistant to answer first? Tell me in the comments.",
    topic_said=("Today we're planning a real AI assistant built with Claude and n8n, "
                "piece by piece, before we write a single node."),
    topic_shown="Plan a real Claude + n8n assistant",
    payoff=payoff_scene(
        ("[excited] Here's the finished assistant. Someone fills in a form, Claude "
         "drafts a reply, the request is logged, and Slack tells you."),
        "Claude's draft is ready for you to approve",
        items=(
            "Form\na request comes in",
            "Claude\ndrafts a reply",
            "Sheets + Slack\nlogged, and you're told",
        ),
        caption="#requests"),
    hook=Scene("What we'll build", "hook", "The finished assistant", items=(
        "Form\na request comes in",
        "Claude\ndrafts a reply",
        "Sheets + Slack\nlogged, and you're told",
    ), code="New request from the website form\nClaude's draft is ready for you to approve",
        caption="#requests", said=(
            "Nothing is sent until you approve it. Today we plan every piece.",)),
    scenes=(
        Scene("The pieces", "diagram", "Four pieces", items=(
            "Form\nn8n Form Trigger",
            "Claude\ndrafts the reply",
            "Sheets\nkeeps the record",
            "Slack\ntells you",
        ), said=(
            ("Four pieces. First, a form. n8n's Form Trigger gives you a form with its "
             "own web address, and each answer starts the workflow."),
            ("Second, Claude. It reads the request and drafts a reply in your style. "
             "It never sends anything by itself."),
            ("Third, Google Sheets. Every request gets a row, so nothing is lost, and "
             "you can look back at every question anyone ever asked."),
            ("And fourth, Slack. You get a message with the request and the draft, "
             "right where you already work, so you never have to go looking."))),
        Scene("", "quiz", "Which piece starts the workflow?", items=(
            "Claude", "The form", "Slack",
        ), answer=1, caption="The Form Trigger starts every run.", said=(
            "Quick quiz. Which piece starts each run? Comment your answer.",
            "B, the form. It's the trigger, so every run starts there.")),
        Scene("Who does what", "bullets", "Who does what", backdrop="project", items=(
            "n8n: moves the data, in fixed steps",
            "Claude: writes the draft",
            "You: approve before anything is sent",
        ), said=(
            ("Who does what? n8n moves the data from step to step, the same way every "
             "time. It never improvises. [chuckling] No jazz solos. That's what makes "
             "it dependable."),
            ("Claude does the one thing that needs language: it reads the question "
             "and writes a draft reply, in a friendly, short style we'll set later "
             "with a skill."),
            ("And you stay in charge. A person approves before any reply leaves. That "
             "rule comes first, and we keep it all series."))),
        Scene("How Claude joins in", "bullets", "How Claude joins in", items=(
            "Simple: n8n calls Claude with its Anthropic nodes",
            "Later: Claude reaches n8n through MCP",
            "Later: our own MCP server, and a Claude skill",
        ), said=(
            ("There are two ways Claude can join. The simple one: n8n calls Claude "
             "directly, with its Anthropic nodes."),
            ("The other way runs the opposite direction. With MCP, Claude can call "
             "your n8n workflow as a tool. That's episode two."),
            ("Then we write our own MCP server, and add a Claude skill so every draft "
             "follows the same rules."))),
        Scene("", "subscribe", "Building along?", said=(
            ("If you want to build this with me, subscribe, so you catch every "
             "episode of the project."),)),
        Scene("The data", "code", "What the form collects", code=(
            '{\n  "name": "Ana",\n  "email": "ana@example.com",\n'
            '  "question": "Do you deliver on weekends?"\n}'),
            marks=((1, 3), (3, 4)),
            caption="example.com is a placeholder address; use your own test data.",
            said=(
                ("Here's the data one request carries: a name and an email, so we "
                 "know who to answer."),
                ("And the question itself. That's what Claude reads. [break] Three "
                 "fields is enough to start."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write down your three form fields",
            "Write one example request",
        ), said=(
            "Pause the video and try it. Write down the three fields your form needs,",
            "and one example request to test with. Resume when you have them.")),
        Scene("One request, start to finish", "steps", "One request, start to finish",
              items=(
                  "Ana sends the form",
                  "n8n saves a row in Sheets",
                  "Claude drafts a reply",
                  "Slack shows you the request and the draft",
              ), said=(
                  ("Let's follow one request all the way through. Ana fills in the form "
                   "and asks if you deliver on weekends. Her answer starts the run."),
                  ("First, n8n saves her request as a new row in Google Sheets. Saving "
                   "first means nothing is lost, even if a later step fails."),
                  ("Then Claude reads her question and drafts a short, friendly reply. "
                   "It only drafts. It doesn't send anything."),
                  ("And Slack shows you Ana's question and Claude's draft side by side, "
                   "so you can check it before anything goes out."))),
        Scene("Safety first", "bullets", "Safety first", backdrop="security", items=(
            "Form answers are untrusted text",
            "Keys live in n8n Credentials",
            "No reply leaves without your approval",
        ), said=(
            ("Three safety rules. Anything typed into a form is untrusted. Claude "
             "treats it as a request to answer, never as instructions."),
            ("Keys and tokens live in n8n Credentials, never in a node field, so they "
             "don't end up in a screenshot or an exported file."),
            "And no reply leaves without your approval.")),
        Scene("", "quiz", "Text from the form is...", items=(
            "Instructions for Claude", "Untrusted data to answer", "A password",
        ), answer=1, caption="Untrusted: answer it, never obey it.", said=(
            "Last quiz. Is text from the form instructions, or something else? Comment.",
            "B. It's untrusted data. Claude answers it, and never obeys it.")),
        Scene("The plan", "checklist", "The build plan", items=(
            "Ep 2: connect Claude to n8n with MCP",
            "Ep 3: our own MCP server",
            "Ep 4: a Claude skill for the replies",
            "Ep 5: test, handle errors, go live",
        ), said=(
            "Here's the build plan. Episode two connects Claude to n8n with MCP.",
            "Episode three writes our own MCP server.",
            "Episode four adds a Claude skill for the replies.",
            ("[happy] And episode five tests it, handles errors, and goes live. If "
             "you build along, tell me in the comments what your form will ask. See "
             "you there."))),
    ),
)

BUSINESS_EP1 = Episode(
    number=1,
    slug="auto-reply-leads",
    level=1,
    title="Auto-Reply to Website Leads with n8n: Never Leave a Lead Waiting (Beginner Tutorial)",
    thumb_title="Answer\nevery lead",
    summary=("A small-business automation in n8n: a website form sends a lead, the "
             "lead gets a friendly first reply by email, the details are saved in "
             "Google Sheets, and you get a Slack message. A fixed, honest template, "
             "no AI guessing, built step by step."),
    links=(("n8n Form Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.formtrigger/"),
           ("n8n Gmail docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/"),
           ("n8n Google Sheets docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets/")),
    tags=("n8n", "n8n tutorial for beginners 2026", "n8n workflow tutorial step by step",
          "ai automation for small business", "small business automation",
          "lead auto reply", "n8n webhook tutorial for beginners", "n8n form trigger",
          "n8n gmail", "n8n google sheets", "n8n slack", "beginner", "ATLAS Academy"),
    hashtags=("#SmallBusiness", "#n8n", "#Automation"),
    ask="What's the first message a new customer should get from you? Tell me below.",
    topic_said=("Today we're making sure every lead from your website gets a fast, "
                "friendly first reply, automatically."),
    topic_shown="Auto-reply to every website lead",
    payoff=payoff_scene(
        ("A lead fills in your website form, gets a friendly reply straight away, and "
         "you get a message in Slack."),
        "First reply sent · row added",
        items=(
            "Form\na lead comes in",
            "Gmail\na friendly first reply",
            "Sheets + Slack\nsaved, and you're told",
        ),
        caption="#leads"),
    hook=Scene("What you'll build", "hook", "Here's what you'll build", items=(
        "Form\na lead comes in",
        "Gmail\na friendly first reply",
        "Sheets + Slack\nsaved, and you're told",
    ), code="New lead: Ana asked about weekend delivery\nFirst reply sent · row added",
        caption="#leads", said=(
            ("Ever had a lead go cold because nobody replied in time? [sighing] "
             "That's what this fixes."),
            "[excited] Four nodes, no lead left waiting. Let's build it.")),
    scenes=(
        Scene("Step 1: the form", "steps", "Step 1: the form", items=(
            "Add an n8n Form Trigger",
            "Fields: name, email, message",
            "Test URL while building · Production URL once published",
        ), said=(
            ("Create a workflow and add an n8n Form Trigger. It gives you a "
             "ready-made form, with its own web address, so you don't have to build a "
             "page. [chuckling] No web designer needed."),
            ("Add three fields: name, email, and message. Make the email field the "
             "email type, so people can't leave it broken."),
            ("Like the Webhook, the form has a test address for building, and a "
             "production address that works once you publish."))),
        Scene("Step 2: save the lead", "steps", "Step 2: save the lead", backdrop="memory", items=(
            "Add Google Sheets: Append Row",
            "Map name, email and message to columns",
        ), said=(
            ("Next, add a Google Sheets node and choose Append Row. Every lead becomes "
             "a row, so nothing is ever lost."),
            ("Map the form's name, email and message to your sheet's columns. Add a "
             "date column too, so you can sort your leads by when they came in."))),
        Scene("", "quiz", "Why save the lead first?", items=(
            "It's faster", "Nothing is lost if a later step fails", "Sheets sends the email",
        ), answer=1, caption="Saved first: a failed email never loses a lead.", said=(
            "Quick quiz. Why do we save the lead before sending anything? Comment.",
            "B. If a later step fails, the lead is still saved.")),
        Scene("Step 3: the first reply", "steps", "Step 3: the first reply", items=(
            "Add Gmail: Message, Send",
            "To: the lead's email, from an expression",
            "A short, honest template",
        ), said=(
            ("Now the reply. Add a Gmail node, with Message and Send, and connect "
             "your Gmail credentials once."),
            ("For To, use an expression that picks the email from the form, so each "
             "reply goes to the right person."),
            ("Keep the message short and honest: thanks, we got your message, and "
             "when you'll really answer. Promise only what you can keep."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write your own first reply",
            "Send a test lead to yourself",
        ), said=(
            "Pause the video and try it. Write your own first reply,",
            "then send a test lead with your own email. Resume when it arrives.")),
        Scene("Step 4: tell yourself", "steps", "Step 4: tell yourself", items=(
            "Add Slack: Message, Send",
            "Name, email and message in the text",
        ), said=(
            "Last step. Add a Slack node and send yourself a message.",
            ("Put the lead's name, email and message in the text with expressions, so "
             "you can answer properly from your phone, wherever you are. [emphasis] "
             "The automatic reply buys you time; your own answer wins the customer."))),
        Scene("", "subscribe", "Saving you time?", said=(
            ("If this saves you time, subscribe. More small-business automations are "
             "coming in this series."),)),
        Scene("A good first reply", "bullets", "A good first reply", items=(
            "Thank them by name",
            "Say what happens next, and when",
            "One way to reach a person",
        ), said=(
            ("What makes a good first reply? Start by thanking them by name, with an "
             "expression that pulls the name from the form."),
            ("Then say what happens next and when, like: we'll answer within one "
             "working day. Only promise a time you can really keep."),
            ("And give one way to reach a person if it's urgent, like your phone "
             "number. A fast, honest answer beats a long one."))),
        Scene("Go live", "steps", "Test, publish, watch", backdrop="workflow", items=(
            "Send a test lead and check every node",
            "Publish the workflow",
            "Share the form's production address",
            "Check Executions if a lead goes missing",
        ), said=(
            "Now send a test lead and click through each node to see what went in and out.",
            ("When it all works, publish the workflow. Until you do, only the test "
             "address listens."),
            ("Then share the form's production address on your website, or link to it "
             "from a button."),
            ("And if a lead ever seems missing, the Executions tab shows every run, "
             "with what each node received and what it sent on."))),
        Scene("", "quiz", "Which address goes on your website?", items=(
            "The test address", "The production address", "Your Gmail address",
        ), answer=1, caption="The production address works once you publish.", said=(
            "Last quiz. Which form address goes on your website? Comment your answer.",
            "B, the production address. It works once the workflow is published.")),
        Scene("Keep it safe", "bullets", "Keep it safe", items=(
            "Gmail and Sheets logins live in Credentials",
            "Only collect what you need",
            "Anything typed in the form is untrusted",
        ), said=(
            ("Three safety habits. Your Gmail and Google Sheets logins live in n8n "
             "Credentials, never typed into a node field."),
            ("Only ask for what you need: a name, an email and a message. Less data "
             "is less to look after."),
            ("And treat anything typed into the form as untrusted text. It's "
             "something to read and answer, never an instruction to follow."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Form Trigger catches the lead",
            "Sheets saves it first",
            "Gmail sends an honest first reply",
            "Slack tells you",
        ), said=(
            "Recap. The Form Trigger catches the lead.",
            "Google Sheets saves it first.",
            "Gmail sends a short, honest first reply.",
            ("[happy] And Slack tells you, so you can answer properly. Every lead, "
             "answered fast."))),
    ),
)


# ------------------------------------------------------------ the roadmap
#
# Miro, 2026-10-10: the channel as a series roadmap. The scripted entries are
# those with scenes; every other entry is PLANNED -- title,
# thumbnail words, summary, tags, hashtags and an outline -- and the
# scheduler skips it with "script not written" until its scenes exist.
# Every outline line is a plan to re-verify against the official docs
# (code.claude.com/docs, docs.n8n.io, modelcontextprotocol.io,
# build.nvidia.com/spark, github.com/nousresearch/hermes-agent) before it is
# scripted; nothing here is said on air until it is.
#
# Search phrases (Perplexity, 2026-10-10: query wording, NOT verified
# volumes; vidIQ's numbers are marked where they exist) are tagged on the
# episode they match.

CLAUDE_HEAD = ("claude code", "claude code tutorial for beginners 2026", "claude ai")
N8N_HEAD = ("n8n", "n8n tutorial", "n8n tutorial for beginners 2026",
            "n8n workflow tutorial step by step")
PROJECT_HEAD = ("claude n8n mcp tutorial", "connect claude to n8n with mcp",
                "n8n mcp server setup claude")
LOCAL_HEAD = ("NVIDIA DGX Spark", "run AI locally", "local LLM tool calling")


def plan(number: int, level: int, slug: str, title: str, thumb: str, summary: str,
         tags: tuple[str, ...], hashtags: tuple[str, ...],
         outline: tuple[str, ...]) -> Episode:
    """A planned episode: everything but the script. Its level's tag
    ("beginner", "intermediate", "advanced") is added once."""
    lvl = () if LEVEL_TAGS[level] in tags else (LEVEL_TAGS[level],)
    return Episode(number=number, slug=slug, title=title, thumb_title=thumb,
                   summary=summary, scenes=(), level=level,
                   tags=(*tags, *lvl, "ATLAS Academy"), hashtags=hashtags,
                   outline=outline)


CLAUDE_PLANNED = (
    plan(2, 1, "best-mcp-servers-claude-code",
         "Best MCP Servers for Claude Code (2026) + How to Fix \"MCP Not Working\"",
         "MCP not\nworking?",
         ("Which MCP servers are worth adding to Claude Code for a beginner, how to "
          "add them at the right scope, and a calm checklist for when one will "
          "not connect."),
         (*CLAUDE_HEAD, "best mcp servers for claude code", "claude code mcp server setup",
          "how to add mcp servers to claude code", "claude code mcp not working",
          "claude mcp tutorial", "model context protocol", "mcp server"),
         ("#ClaudeCode", "#MCP", "#ClaudeAI"),
         ("claude mcp add: local, project and user scope, and what .mcp.json is",
          "stdio servers vs remote HTTP servers, and signing in with /mcp",
          ("A few well-documented servers to start with (picked from the official "
           "lists, verified on the day of scripting)"),
          "claude mcp list and /mcp: reading a server's status",
          ("Not working? the command path, the absolute path, env vars, a server "
           "printing to stdout"),
          "Recap and a quiz: which scope shares a server with your team?")),
    plan(3, 2, "claude-skills-first-skill-md",
         "Claude Skills Explained: Create Your First SKILL.md (Claude Code Tutorial)",
         "Your first\nSKILL.md",
         ("What a Claude Code skill is, where it lives, and how to write your first "
          "SKILL.md so Claude uses it at the right moment."),
         (*CLAUDE_HEAD, "claude code skills tutorial", "how to create claude code skills",
          "claude code skill.md tutorial", "claude skills", "agent skills"),
         ("#ClaudeCode", "#ClaudeSkills", "#ClaudeAI"),
         ("What a skill is: instructions and files Claude loads when they fit the task",
          "Where skills live: personal, project and plugin skills",
          "SKILL.md: the name and description front matter, then the instructions",
          "Why the description decides when the skill is used",
          "Adding a script or a reference file next to SKILL.md",
          "Try it: a small skill, then ask for the task it covers",
          "Recap checklist")),
    plan(4, 2, "claude-code-subagents",
         "Claude Code Subagents Tutorial: Specialists That Work for You",
         "Claude\nsubagents",
         ("What subagents are in Claude Code, why each gets its own context, and "
          "how to define one with its own tools and instructions."),
         (*CLAUDE_HEAD, "claude code subagents tutorial", "claude code agents",
          "claude code subagents"),
         ("#ClaudeCode", "#AIAgents", "#ClaudeAI"),
         ("What a subagent is: a specialist with its own context window",
          "Where they live: .claude/agents/ files with front matter",
          "Choosing a subagent's tools and its description",
          "Asking for one by name, and letting Claude pick one",
          "Several at once: what can run side by side, and what cannot (verify)",
          "Recap and a quiz")),
    plan(5, 3, "claude-code-hooks",
         "Claude Code Hooks Tutorial: Automate Checks with PreToolUse and PostToolUse",
         "Claude Code\nhooks",
         ("Hooks run your own commands at fixed points in Claude Code. Learn "
          "PreToolUse and PostToolUse with one formatter and one safety check."),
         (*CLAUDE_HEAD, "claude code hooks tutorial", "claude code hooks",
          "pretooluse", "posttooluse"),
         ("#ClaudeCode", "#Automation", "#ClaudeAI"),
         ("What a hook is: your command, run at a fixed point, every time",
          "Where hooks are configured: the settings files, and /hooks",
          "PreToolUse: check a tool call before it runs, and block it",
          "PostToolUse: run a formatter after a file is written",
          "Matchers: which tools a hook applies to",
          "Exit codes and what Claude sees",
          "Recap checklist")),
    plan(6, 3, "custom-connector-remote-mcp-oauth",
         "Build a Custom Claude Connector: a Remote MCP Server with OAuth (Beginner Guide)",
         "Your own\nconnector",
         ("How a custom connector reaches Claude: a remote MCP server over HTTP, "
          "how sign-in with OAuth fits in, and how to add it to Claude."),
         (*CLAUDE_HEAD, "claude custom connector", "remote mcp server",
          "mcp oauth", "claude mcp tutorial", "model context protocol"),
         ("#ClaudeAI", "#MCP", "#Connectors"),
         ("Local stdio server vs remote HTTP server, in one diagram",
          "What the remote server must offer: the MCP endpoint over HTTP",
          "Where OAuth comes in: the user signs in, the client gets a token",
          "Adding a custom connector in Claude, and claude mcp add --transport http",
          "Keeping secrets out of the code and the logs",
          "Test, then recap")),
    plan(7, 3, "skills-mcp-subagents-hooks",
         "Claude Skills vs MCP vs Subagents vs Hooks: When to Use Which",
         "Which one\nwhen?",
         ("Four ways to extend Claude Code side by side: what each one is for, one "
          "example of each, and a simple way to choose."),
         (*CLAUDE_HEAD, "claude code skills tutorial", "claude code subagents tutorial",
          "claude code hooks tutorial", "claude mcp tutorial"),
         ("#ClaudeCode", "#MCP", "#ClaudeAI"),
         ("MCP: reach a tool or data outside Claude",
          "Skills: teach Claude a way of doing a task",
          "Subagents: hand a task to a specialist with its own context",
          "Hooks: run something every time, no matter what",
          "One example of each from this series",
          "A quiz: four problems, pick the right one",
          "Recap")),
)

N8N_PLANNED = (
    plan(2, 1, "n8n-triggers-and-nodes",
         "n8n Triggers Explained: Schedule, Webhook and App Triggers (Beginner Tutorial)",
         "n8n\ntriggers",
         ("Every n8n workflow starts with a trigger. Schedule, Webhook and app "
          "triggers side by side, and when to use each."),
         (*N8N_HEAD, "n8n triggers", "n8n schedule trigger", "n8n webhook tutorial for beginners",
          "n8n nodes explained"),
         ("#n8n", "#n8nTutorial", "#Automation"),
         ("What a trigger is, and why a workflow has one to start it",
          "Schedule Trigger: run every hour, every day",
          "Webhook: run when something calls your URL",
          "App triggers: run when something happens in an app",
          "Action nodes and the data that flows between them",
          "Quiz: which trigger for which job?")),
    plan(3, 1, "n8n-managing-workflows",
         "Managing n8n Workflows: Executions, Versions, Publish and Folders",
         "Manage your\nworkflows",
         ("Keep many n8n workflows tidy: read executions, go back to an earlier "
          "version, publish or stop a workflow, and sort them into folders."),
         (*N8N_HEAD, "n8n executions", "n8n workflow history", "n8n folders",
          "n8n tutorial for beginners"),
         ("#n8n", "#n8nTutorial", "#Automation"),
         ("The Executions list: what ran, when, and with what data",
          "Re-running a past execution while debugging",
          "Workflow history: going back to an earlier version (check your plan)",
          "Publishing and unpublishing a workflow",
          "Folders and names that stay readable",
          "Recap checklist")),
    plan(4, 2, "n8n-error-handling",
         "n8n Error Handling Tutorial: Error Trigger, Retry and Continue on Fail",
         "When n8n\nfails",
         ("What happens when a node fails, and three ways to handle it: retries, "
          "carrying on, and an error workflow that tells you."),
         (*N8N_HEAD, "n8n error handling tutorial", "n8n error trigger",
          "n8n retry on fail"),
         ("#n8n", "#n8nTutorial", "#Automation"),
         ("Why workflows fail: bad data, a down API, a missing credential",
          "Retry On Fail on a node",
          "On Error: stop, or continue with the error output",
          "An error workflow that starts with the Error Trigger",
          "Sending yourself the alert in Slack",
          "Quiz and recap")),
    plan(5, 2, "n8n-ai-agent",
         "n8n AI Agent Tutorial for Beginners: Tools and Memory, Step by Step",
         "AI agent\nin n8n",
         ("Build a small AI agent in n8n: the AI Agent node, a chat model, a tool "
          "it can call, and memory so it remembers the conversation."),
         (*N8N_HEAD, "n8n ai agent tutorial", "n8n ai agent", "ai agents",
          "ai automation"),
         ("#n8n", "#AIAgent", "#Automation"),
         ("What an agent adds to a workflow (the finished chat shown first)",
          "What you need: n8n and one chat model credential",
          "The AI Agent node and a chat model",
          "Giving it a tool to call, then memory",
          "Testing it in the chat panel; common error: no chat model connected",
          "Keeping it safe: what it may and may not do",
          ("Lab: import the agent JSON (credentials left empty), ask it one "
           "question, expect a tool call in the log; challenge: add a second tool"),
          "Recap")),
    plan(6, 2, "n8n-webhooks-apis",
         "n8n Webhooks and APIs Deep Dive: Connect Any App",
         "Connect\nany app",
         ("Webhooks in, API calls out: authentication, headers, query parameters, "
          "a JSON body, and Import cURL, so any app with an API can join a "
          "workflow."),
         (*N8N_HEAD, "n8n webhook tutorial for beginners", "n8n http request",
          "n8n api tutorial", "n8n webhook"),
         ("#n8n", "#API", "#Automation"),
         ("Webhook methods, paths and responding to the caller",
          "HTTP Request: method, URL, query, headers, body",
          "Authentication with saved credentials",
          "Import cURL from an API's docs",
          "Reading the response with expressions",
          "Quiz and recap")),
    plan(7, 3, "self-host-n8n-docker",
         "Self Host n8n with Docker for Beginners (Free, Step by Step)",
         "n8n on\nDocker",
         ("Run n8n on your own machine with Docker: one command, a volume so your "
          "work is kept, and opening the editor in your browser."),
         (*N8N_HEAD, "self host n8n tutorial", "n8n docker setup for beginners",
          "n8n docker", "n8n self hosted"),
         ("#n8n", "#Docker", "#SelfHosted"),
         ("Cloud or self-hosted: what changes",
          "Installing Docker",
          "docker run with a volume for n8n's data",
          "Opening the editor on localhost:5678",
          "Updating to a new version without losing work",
          "Recap checklist")),
    # 2026-10-11, docs/academy-reference-learnings.md: "n8n AI agent" is the
    # most repeated build in the top n8n videos (GuaKeDS6UKU, TKnaDGpN7Ns,
    # 5lNy7mOuIlM) and the lab-style videos (ZaPbP9DwBOE) alternate a
    # concept with a hands-on lab. #5 builds the agent; this one is the lab.
    plan(8, 3, "n8n-ai-agent-lab",
         "n8n AI Agent Lab: Tools, Memory and an Approval Step, Hands-On",
         "Agent\nlab",
         ("A hands-on lab: import an n8n agent workflow, run three exercises "
          "(a tool call, memory across two messages, an approval before a "
          "message is sent), read the expected result of each, then take on a "
          "challenge with no answer shown."),
         (*N8N_HEAD, "n8n ai agent tutorial", "n8n ai agent lab", "ai agent lab",
          "n8n human in the loop", "n8n agent tools memory"),
         ("#n8n", "#AIAgents", "#Automation"),
         ("The finished agent, shown first: a question in, a tool call, an approval",
          "What you need: n8n, one chat model credential, the lab JSON",
          "Exercise 1: one tool call; expected result in the execution log",
          "Exercise 2: memory across two messages; expected: it remembers",
          "Exercise 3: an approval step before anything is sent",
          ("Common errors: no model connected, a tool with no description, a loop "
           "that never stops (a max iterations limit)"),
          "Challenge: add a second tool and decide what it must never do",
          "Recap")),
)

PROJECT_LEVEL0 = plan(
    0, 0, "project-what-we-build",
    "Claude + n8n for Beginners: What We'll Build and the Words You Need First",
    "Before we\nbuild",
    ("Before the real project: what an AI assistant built from Claude and n8n "
     "is, the handful of words you need (workflow, trigger, MCP, approval), and "
     "what to have ready. No setup yet, nothing assumed."),
    ("claude n8n tutorial for beginners", "claude and n8n", "ai assistant for beginners",
     "ai automation for beginners", "how to use claude", "n8n basics", "what is mcp",
     "automation for beginners"),
    ("#ClaudeAI", "#n8n", "#AIAutomation"),
    ("The finished assistant, shown first: a form in, a drafted reply out",
     "Claude writes, n8n moves things: who does which job",
     "Five words: workflow, trigger, node, MCP, approval",
     "Why a person approves before anything is sent",
     "What to have ready: a Claude account and an n8n workspace",
     "Quiz: which part sends the email?"))

PROJECT_PLANNED = (
    plan(2, 2, "project-connect-claude-n8n-mcp",
         "Connect Claude to n8n with MCP (MCP Server Trigger, Step by Step)",
         "Claude to\nn8n",
         ("Let Claude call your n8n workflow through MCP: the MCP Server Trigger "
          "node in n8n, and adding its URL to Claude."),
         (*PROJECT_HEAD, "n8n mcp", "claude mcp tutorial", "model context protocol"),
         ("#n8n", "#MCP", "#ClaudeAI"),
         ("n8n as an MCP server: the MCP Server Trigger node",
          "Choosing which tools it exposes",
          "Adding the server to Claude",
          "n8n as an MCP client: the MCP Client Tool node",
          "Testing a call end to end",
          "Recap")),
    plan(3, 2, "project-own-mcp-server",
         "Write Your Own MCP Server and Plug It into an n8n Workflow",
         "Your server\nin n8n",
         ("A small MCP server of our own with one useful tool, then used from the "
          "project's n8n workflow."),
         (*PROJECT_HEAD, "mcp server", "python mcp server", "claude mcp tutorial"),
         ("#MCP", "#n8n", "#ClaudeAI"),
         ("The one tool the project needs",
          "Writing it with the official Python SDK",
          "Running it as a remote server",
          "Calling it from n8n",
          "Recap and quiz")),
    plan(4, 2, "project-claude-skill",
         "Add a Custom Claude Skill to a Real Project",
         "Add a\nskill",
         ("A SKILL.md that teaches Claude the project's reply style, so every draft "
          "follows the same rules."),
         (*PROJECT_HEAD, "claude code skills tutorial", "claude code skill.md tutorial"),
         ("#ClaudeAI", "#ClaudeSkills", "#n8n"),
         ("Why a skill instead of a longer prompt",
          "Writing the project's SKILL.md",
          "Testing that Claude picks it up",
          "Keeping it short and checkable",
          "Recap")),
    plan(5, 3, "project-test-deploy",
         "Test, Handle Errors and Go Live: Claude + n8n Project",
         "Go\nlive",
         ("Test every step with real-looking data, add error handling, publish the "
          "workflow, and watch the first real runs."),
         (*PROJECT_HEAD, "n8n error handling tutorial", "n8n tutorial for beginners 2026"),
         ("#n8n", "#ClaudeAI", "#Automation"),
         ("A test plan: one happy path, three broken ones",
          "Error workflow and alerts",
          "Publishing",
          "Watching the first executions",
          "Checklist before you trust it")),
    plan(6, 3, "project-second-channel-email",
         "Add Email as a Second Channel to Your Claude + n8n Assistant",
         "Add\nemail",
         ("The assistant answers email too: a second trigger, the same Claude step, "
          "and a human approval before anything is sent."),
         (*PROJECT_HEAD, "n8n gmail", "n8n email automation"),
         ("#n8n", "#ClaudeAI", "#Automation"),
         ("A second trigger for email",
          "Reusing the Claude step",
          "Approval before sending",
          "Logging both channels in one sheet",
          "Recap")),
    # 2026-10-18 (coordinator, decision A): one planned entry past the last
    # scripted episode, so its recap names a real next episode.
    plan(7, 3, "project-share-with-team",
         "Share Your Claude + n8n Assistant with Your Team, Safely",
         "Share it\nsafely",
         ("Hand the assistant to the people you work with: who may edit it, who may "
          "only use it, and credentials shared without passing secrets around."),
         (*PROJECT_HEAD, "n8n projects", "n8n credential sharing"),
         ("#n8n", "#ClaudeAI", "#Automation"),
         ("Who needs what: use it, edit it, own it",
          "Sharing the workflow",
          "Sharing a credential without showing the secret",
          "Project skills and settings, committed for the team",
          "Recap checklist")),
)

BUSINESS_LEVEL0 = plan(
    0, 0, "business-what-to-automate-first",
    "Automation for Small Business Beginners: What to Automate First (and What Not To)",
    "Automate\nwhat first?",
    ("Small-business automation from zero: what it means for a shop, a studio or "
     "a freelancer, how to spot a task worth automating, and the jobs a person "
     "should always keep."),
    ("small business automation", "automation for beginners",
     "ai automation for small business", "what is automation",
     "small business automation ideas", "n8n basics", "ai for small business"),
    ("#SmallBusiness", "#Automation", "#AIAutomation"),
    ("What automation is, for a small business, in one picture",
     "A task worth automating: repeated, rule-based, boring",
     "Five common starters: leads, bookings, invoices, reports, posts",
     "What a person keeps: money, refunds, anything that leaves for a customer",
     "Pause: list three tasks you repeat every week",
     "Quiz: automate it or keep it?"))

BUSINESS_PLANNED = (
    plan(2, 1, "booking-reminders",
         "Booking Reminders on Autopilot with n8n (Small Business Automation)",
         "No more\nno-shows",
         ("Send booking confirmations and reminders automatically from your "
          "calendar, with a schedule trigger and one message template."),
         ("n8n", "ai automation for small business", "booking reminder automation",
          "n8n schedule trigger", "small business automation"),
         ("#SmallBusiness", "#n8n", "#Automation"),
         ("Reading tomorrow's bookings",
          "One reminder template",
          "Sending, and logging what was sent",
          "Handling a cancellation",
          "Recap")),
    plan(3, 1, "invoice-follow-ups",
         "Invoice and Payment Link Follow-Ups with n8n (Polite, Automatic)",
         "Follow up\ninvoices",
         ("Remind customers about unpaid invoices with a payment link, politely and "
          "on a schedule, and stop as soon as they pay."),
         ("n8n", "ai automation for small business", "invoice reminder automation",
          "payment link follow up", "small business automation"),
         ("#SmallBusiness", "#n8n", "#Automation"),
         ("Where the unpaid list comes from",
          "A schedule and a polite template",
          "Stopping once paid",
          "Never more than one reminder a week",
          "Recap")),
    plan(4, 2, "weekly-report-slack",
         "Weekly Report to Slack from Google Sheets with n8n",
         "Weekly\nreport",
         ("Every Monday, n8n reads a sheet, sums up the week and posts a short "
          "report in Slack."),
         ("n8n", "n8n google sheets", "n8n slack", "weekly report automation",
          "ai automation for small business"),
         ("#SmallBusiness", "#n8n", "#Slack"),
         ("Schedule Trigger for Monday morning",
          "Reading the sheet",
          "Summing up with a Code or Summarize node",
          "Posting to Slack",
          "Recap")),
    plan(5, 3, "social-scheduler-ai-captions",
         "Social Post Scheduler with AI Captions in n8n (You Approve Every Post)",
         "AI\ncaptions",
         ("Draft captions with AI from a content sheet, approve them, and schedule "
          "the posts, with nothing posted before you say yes."),
         ("n8n", "ai automation for small business", "ai captions",
          "social media automation", "n8n ai agent tutorial"),
         ("#SmallBusiness", "#n8n", "#AIAutomation"),
         ("The content sheet",
          "AI drafts the caption",
          "Your approval step",
          "Scheduling the post",
          "Recap")),
    # 2026-10-18 (coordinator, decision A): the next starter for a small
    # business, one planned entry past the last scripted episode.
    plan(6, 3, "ai-email-triage",
         "AI Email Triage for Small Business with n8n (Drafts You Approve)",
         "Sort the\ninbox",
         ("New emails are sorted by AI into a few simple labels, and the ones that "
          "need an answer get a draft reply you approve before it's sent."),
         ("n8n", "ai automation for small business", "email automation",
          "n8n gmail", "ai email triage"),
         ("#SmallBusiness", "#n8n", "#AIAutomation"),
         ("New email in: the trigger",
          "AI sorts it into a few labels",
          "A draft reply for the ones that need one",
          "Your approval before anything is sent",
          "Recap")),
)

AGENTS_PLANNED = (
    plan(2, 1, "agent-vs-workflow",
         "AI Agent vs Workflow: When You Need Each",
         "Agent or\nworkflow?",
         ("Fixed steps or a model that decides? A simple way to choose between a "
          "workflow and an agent, with examples."),
         ("ai agents", "ai agent vs workflow", "n8n ai agent tutorial", "ai automation"),
         ("#AIAgents", "#n8n", "#Automation"),
         ("Workflow: the same steps every time",
          "Agent: the model picks the steps",
          "Cost, speed and predictability",
          "Mixing both",
          "Quiz: five jobs, pick one")),
    # 2026-10-11, docs/academy-reference-learnings.md: the agentic-AI
    # explainers that draw ("Generative AI vs AI Agents vs Agentic AI" 527k,
    # "AI Model vs Agentic Harness" 169k, "Demo to Production" 491k) make one
    # distinction and then show it running. Ours shows it on a real n8n run.
    plan(3, 2, "agentic-ai-real-workflow",
         "Agentic AI Explained with a Real Workflow: Model, Agent, Agentic System",
         "Agentic AI,\nfor real",
         ("Agentic AI without the buzzwords: a model, an agent and an agentic "
          "system told apart, then one real n8n run where you watch each part "
          "act, and where a person approves."),
         ("agentic ai", "agentic ai explained", "what is agentic ai",
          "ai agents vs agentic ai", "agentic workflow", "n8n ai agent"),
         ("#AgenticAI", "#AIAgents", "#n8n"),
         ("The run shown first: a request in, three steps chosen, one approval",
          "Model, agent, agentic system: one sentence each",
          "Live: the same request through a fixed workflow, then an agent",
          "What made it agentic: it chose the steps and checked its work",
          "Where it went wrong once, and the limit that stopped it",
          "Quiz: model, agent or agentic system?",
          "Recap and the question to ask before you build one")),
    plan(4, 2, "agent-tools-memory-planning",
         "AI Agent Tools, Memory and Planning, with Diagrams",
         "Tools,\nmemory, plans",
         ("The three parts that make an agent useful, each drawn as a diagram: "
          "tools, memory and planning."),
         ("ai agents", "ai agent tools", "ai agent memory", "ai agents explained"),
         ("#AIAgents", "#AI", "#MCP"),
         ("Tools: how a model asks to use one",
          "Memory: short-term and long-term",
          "Planning: breaking a goal into steps",
          "Where MCP fits",
          "Recap")),
    plan(5, 3, "agent-safety-approvals",
         "AI Agent Safety: Approvals and Guardrails for Beginners",
         "Keep agents\nsafe",
         ("How to let an agent help without letting it do harm: approvals, limits "
          "on what it can touch, and logs you can read."),
         ("ai agents", "ai agent safety", "ai guardrails", "human in the loop"),
         ("#AIAgents", "#AISafety", "#Automation"),
         ("Least access: only the tools it needs",
          "Approvals before anything leaves",
          "Untrusted input is never an instruction",
          "Logs and undo",
          "Recap checklist")),
    # 2026-10-18 (coordinator, decision A): the playlist promises how to keep
    # agents safe; testing one before you trust it is the next step.
    plan(6, 3, "test-an-ai-agent",
         "How to Test an AI Agent Before You Trust It (Evaluations for Beginners)",
         "Test your\nagent",
         ("Before an agent works alone: a small set of test cases, what a good "
          "answer looks like, and running the same tests after every change."),
         ("ai agents", "ai agent testing", "ai evaluations", "llm evals"),
         ("#AIAgents", "#AISafety", "#Automation"),
         ("Why test an agent: it can answer differently each time",
          "A small test set from real requests",
          "What good looks like, written down",
          "Run it after every change",
          "Recap checklist")),
)

LOCAL_TAGS = ("hermes agent nous research setup", "hermes agent dgx spark local llm",
              "dgx spark gb10 vllm setup", "dgx spark ollama llama.cpp",
              "dgx spark tensorrt-llm nim", "hermes agent mcp setup",
              "hermes agent n8n integration", "hermes agent claude desktop mcp",
              "hermes agent telegram slack bot")

LOCAL_LEVEL0 = plan(
    0, 0, "what-is-local-ai",
    "Local AI for Beginners: What Running AI on Your Own Computer Means",
    "AI on\nyour machine",
    ("Local AI from zero: what a model is, what running it on your own computer "
     "means compared with a cloud app, why people do it (privacy, control, "
     "offline), and what hardware questions to ask, in plain words."),
    ("local ai for beginners", "run AI locally", "what is a local llm",
     "local llm explained", "run llm locally", "local ai vs cloud ai",
     "ai for beginners", "open source ai models"),
    ("#LocalAI", "#AI", "#OpenSource"),
    ("What a model is: the file that does the thinking",
     "Cloud AI vs local AI: where the model runs",
     "Why run it yourself: privacy, control, offline",
     "What it needs: memory is the first question",
     "Model vs agent: why Hermes Agent comes next",
     "Quiz: cloud or local?"))

LOCAL_PLANNED = (
    plan(1, 1, "what-is-hermes-agent",
         "What Is Hermes Agent? Nous Research's Open-Source Agent, and Why Run AI Locally",
         "Hermes\nAgent?",
         ("Hermes Agent is an open-source agent from Nous Research: it uses tools, "
          "keeps memory and learns skills. It is not a model; it talks to the "
          "model you choose. Why running that model locally can make sense."),
         (*LOCAL_HEAD, "hermes agent", LOCAL_TAGS[0]),
         ("#HermesAgent", "#LocalAI", "#DGXSpark"),
         ("Agent, not model: it connects to any provider or endpoint",
          "Tools, memory and skills it learns from experience",
          "Terminal or a messaging gateway",
          "Why local: privacy, cost, control (and the trade-offs)",
          "What the rest of the series builds")),
    # 2026-10-11, docs/academy-reference-learnings.md: setup is what the
    # Hermes Agent audience watches most ("Full Tutorial & Setup Guide" 508k,
    # tags "hermes agent setup" x9, "setup hermes agent" x8, "hermes agent
    # install" x6). Commands from github.com/NousResearch/hermes-agent's
    # README, read 2026-10-11: the install.sh one-liner (Linux, macOS, WSL2),
    # `hermes setup`, `hermes model`, `hermes tools`, `hermes doctor`;
    # re-read on the day it is scripted.
    plan(2, 1, "hermes-agent-install-first-tool",
         "Install Hermes Agent and Give It Its First Tool (Step by Step)",
         "Install\nHermes",
         ("Hermes Agent from zero: install it with the official script, pick a "
          "model, turn on one tool, run a first task, and check it with the "
          "built-in doctor when something is off."),
         (*LOCAL_HEAD, "hermes agent", "hermes agent install", "hermes agent setup",
          "hermes agent tutorial for beginners", LOCAL_TAGS[0]),
         ("#HermesAgent", "#LocalAI", "#AIAgents"),
         ("The finished first task, shown first",
          ("What you need: a Mac, Linux or WSL2 terminal and a model provider "
           "(a hosted one first; a local model comes later in the series)"),
          "Install with the official one-line script, then reload the shell",
          "hermes setup, then hermes model: choose the model",
          "hermes tools: turn on one tool, and only that one",
          "A first task that uses the tool; expected result on screen",
          ("Common errors: command not found (reload the shell), no model "
           "configured; hermes doctor to diagnose"),
          "Challenge: a second tool, and what it may never do; recap")),
    plan(3, 1, "dgx-spark-unified-memory",
         "NVIDIA DGX Spark Explained: What 128 GB Unified Memory Really Means",
         "128 GB,\nexplained",
         ("The DGX Spark's GB10 and its 128 GB of unified memory in plain words, "
          "and why not all of it is there for model weights."),
         (*LOCAL_HEAD, "dgx spark", "dgx spark gb10"),
         ("#DGXSpark", "#LocalAI", "#NVIDIA"),
         ("What unified memory means: CPU and GPU share one pool",
          "Weights, context and the rest: where memory goes",
          "Why a model's size is only part of the answer",
          "NVIDIA's model-specific recipes as the check (never a guess)",
          "Recap")),
    plan(4, 2, "hermes-on-spark-vllm",
         "Run Hermes Agent on DGX Spark with vLLM (Following NVIDIA's Playbook)",
         "Hermes,\nfully local",
         ("Hermes Agent talking to a local model served by vLLM on a DGX Spark, "
          "following NVIDIA's own Spark playbook step by step."),
         (*LOCAL_HEAD, LOCAL_TAGS[1], LOCAL_TAGS[2], "vllm"),
         ("#HermesAgent", "#DGXSpark", "#LocalAI"),
         ("The playbook at build.nvidia.com/spark",
          "Serving a model with vLLM",
          "Pointing Hermes Agent at the local endpoint",
          "A first task with a tool",
          "Checking it really runs locally",
          "Recap")),
    plan(5, 2, "local-stack-compared",
         "Ollama vs llama.cpp vs vLLM vs NIM vs TensorRT-LLM on DGX Spark",
         "Pick your\nstack",
         ("Five ways to serve a model on a DGX Spark, each from NVIDIA's playbooks: "
          "what each is good at and how to choose."),
         (*LOCAL_HEAD, LOCAL_TAGS[3], LOCAL_TAGS[4], "ollama vs vllm"),
         ("#DGXSpark", "#LocalAI", "#LLM"),
         ("Ollama: the quickest start",
          "llama.cpp: light and flexible",
          "vLLM: serving many requests",
          "NIM and TensorRT-LLM: NVIDIA's optimised paths",
          "Only what a recipe shows fits; no guessed sizes",
          "Quiz: pick a stack for three jobs")),
    plan(6, 2, "hermes-mcp-tools",
         "Give Hermes Agent Tools with MCP: Files, a Database and APIs",
         "Hermes +\nMCP",
         ("Hermes Agent has an MCP client built in. Connect MCP servers so it can "
          "read files, query a database and call APIs."),
         (*LOCAL_HEAD, LOCAL_TAGS[5], "model context protocol", "mcp server"),
         ("#HermesAgent", "#MCP", "#LocalAI"),
         ("Hermes Agent's MCP client (its docs: features/mcp)",
          "A filesystem server",
          "A database server",
          "An API server",
          "Limiting what each tool may touch",
          "Recap")),
    plan(7, 3, "hermes-claude-desktop-mcp",
         "Hermes Agent + Claude Desktop through Hermes' MCP Server Mode",
         "Hermes in\nClaude",
         ("Hermes Agent can also act as an MCP server, so an MCP client such as "
          "Claude Desktop can reach its sessions."),
         (*LOCAL_HEAD, LOCAL_TAGS[7], "claude mcp tutorial", "claude desktop mcp"),
         ("#HermesAgent", "#MCP", "#ClaudeAI"),
         ("Client mode vs server mode",
          "Starting Hermes as an MCP server",
          "Adding it to Claude Desktop's config",
          "What Claude can and cannot see",
          "Recap")),
    plan(8, 3, "hermes-telegram-slack-bot",
         "Hermes Agent as a Telegram or Slack Bot (Messaging Gateway)",
         "Hermes as\na bot",
         ("Run Hermes Agent as a messaging gateway so you can talk to your local "
          "agent from Telegram or Slack."),
         (*LOCAL_HEAD, LOCAL_TAGS[8], "telegram bot", "slack bot"),
         ("#HermesAgent", "#LocalAI", "#Automation"),
         ("The gateway mode",
          "A Telegram bot token, kept secret",
          "A Slack app",
          "Who may talk to it",
          "Recap")),
    plan(9, 3, "hermes-n8n",
         "Hermes Agent + n8n: Connect Them with MCP or HTTP",
         "Hermes +\nn8n",
         ("There is no built-in n8n integration we could confirm, so we connect "
          "Hermes Agent and n8n the honest way: through MCP or plain HTTP."),
         (*LOCAL_HEAD, LOCAL_TAGS[6], "n8n mcp", "n8n tutorial"),
         ("#HermesAgent", "#n8n", "#LocalAI"),
         ("No native integration confirmed: say so",
          "Option 1: MCP between them",
          "Option 2: an HTTP call from n8n",
          "A small automation using both",
          "Recap")),
)

# ------------------------------- scripted 2026-10-11: one a day per series
#
# Miro, 2026-10-11: "we need one video for each series each day". Each
# series' next rung is scripted here, filled into its planned entry (the
# title, thumbnail words, summary, tags and hashtags stay as researched).
# Facts read on the official pages on 2026-10-11:
#   n8n   docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/
#         (MCP Server Trigger: "lets n8n act as a Model Context Protocol
#         server"; Test URL vs Production URL "registered when you publish";
#         SSE and streamable HTTP, no stdio; None / Bearer / Header auth;
#         tools attached with the Custom n8n Workflow Tool node; Claude
#         Desktop through `npx mcp-remote <MCP_URL>`)
#         docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/
#         n8n-nodes-langchain.toolmcp/ (MCP Client Tool: an external MCP
#         server's tools for an n8n agent; Tools to Include: All, Selected,
#         All Except)
#         docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/
#         (intervals Seconds to Months and Custom (Cron); "save and publish
#         the workflow"; the workflow's timezone, else the instance's)
#         docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
#         (DELETE GET HEAD PATCH POST PUT; Respond: Immediately, When Last
#         Node Finishes, Respond to Webhook node, Streaming)
#         docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger/
#         (polls at the chosen Poll Times; event Message Received)
#         docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlecalendar/
#         (Event: Create, Delete, Get, Get Many, Update)
#   Claude Code  code.claude.com/docs/en/mcp (scopes local (default, private,
#         ~/.claude.json), project (.mcp.json, shared through version
#         control, approved before first use), user (all your projects);
#         `claude mcp add --transport http <name> <url>`; `--env`; `--`
#         before a stdio server's command; `claude mcp list / get / remove`;
#         `/mcp` for status and OAuth sign-in; statuses Connected / Needs
#         authentication / Failed to connect; MCP_TIMEOUT; a url entry
#         without "type" is read as stdio)
#   MCP   github.com/modelcontextprotocol/servers (the reference servers:
#         Everything, Fetch, Filesystem, Git, Memory, Sequential Thinking,
#         Time; GitHub, Slack, Postgres and others are archived there)
#   Hermes github.com/NousResearch/hermes-agent README ("The self-improving
#         AI agent built by Nous Research", MIT; tools, memory, skills it
#         creates after complex tasks; a terminal UI and a messaging gateway
#         for Telegram, Discord, Slack, WhatsApp and Signal; Nous Portal,
#         OpenRouter, OpenAI or a custom endpoint; MCP servers)
#   Agents anthropic.com/engineering/building-effective-agents (as above)
# Local AI #0 says no model sizes, no memory figures and no fit claims: the
# hardware numbers wait for the episodes that follow NVIDIA's own pages.


def scripted(planned: Episode, **script: Any) -> Episode:
    """A planned entry with its script: the researched title, thumbnail,
    summary, tags and hashtags stay; the scenes, hook and links arrive."""
    return replace(planned, **script)


def fill(planned: tuple[Episode, ...], *eps: Episode) -> tuple[Episode, ...]:
    """`planned` with each of `eps` in its numbered place."""
    by = {e.number: e for e in eps}
    return tuple(by.get(e.number, e) for e in planned)


def extend(planned: tuple[Episode, ...], *eps: Episode) -> tuple[Episode, ...]:
    """`planned` with `eps` appended after it, in number order."""
    return (*planned, *sorted(eps, key=lambda e: e.number))


def from_scripts(key: str, planned: tuple[Episode, ...], eps: tuple[Episode, ...],
                 inline: tuple[Episode, ...] = ()) -> tuple[Episode, ...]:
    """`planned` with the episodes written in tutorial_scripts/ (Miro,
    2026-10-15: a scheduled writer adds them). Each either lands on a
    planned, still unscripted entry (fill), or -- once the plan is used up
    (2026-10-16) -- is a new entry past the series' last number, planned and
    scripted in the same module (extend). One that collides with an inline
    script (in `planned` or among the series' `inline` episodes before it),
    leaves a gap past the end (fill() would drop it without a word), sits
    inside the numbering with no planned entry, or appears twice is an
    error."""
    slots = {e.number: e for e in planned}
    taken = {e.number for e in inline}
    last = max((*slots, *taken), default=-1)
    seen: set[int] = set()
    for e in eps:
        if e.number in seen:
            raise ValueError(f"{key} #{e.number} is written twice in tutorial_scripts")
        seen.add(e.number)
        if e.number in taken or (e.number in slots and slots[e.number].scripted):
            raise ValueError(f"{key} #{e.number} is already scripted inline")
        if e.number not in slots and e.number <= last:
            raise ValueError(f"{key} #{e.number} has no planned entry")
    beyond = sorted(e.number for e in eps if e.number > last)
    if beyond != list(range(last + 1, last + 1 + len(beyond))):
        raise ValueError(f"{key}: a gap in numbering past #{last}: "
                         f"{', '.join(f'#{n}' for n in beyond)}")
    return extend(fill(planned, *(e for e in eps if e.number <= last)),
                  *(e for e in eps if e.number > last))


PROJECT_LEVEL0 = scripted(
    PROJECT_LEVEL0,
    links=(("What is MCP? (official intro)",
            "https://modelcontextprotocol.io/docs/getting-started/intro"),
           ("Choose how to use n8n", "https://docs.n8n.io/choose-n8n/"),
           ("Claude", "https://claude.ai")),
    ask="What would you want your own assistant to do first? Tell me in the comments.",
    topic_said=("Today, before we build anything: the assistant we're going to make "
                "with Claude and n8n, and the five words you need first."),
    topic_shown="What we'll build, and the words you need",
    payoff=payoff_scene(
        ("Here's where this series ends up. A customer asks a question on a form, "
         "Claude writes a draft reply, and you see it first."),
        "Draft reply ready for you",
        items=(
            "Form\na question comes in",
            "Claude\nwrites a draft reply",
            "You\napprove before it's sent",
        ),
        caption="nothing is sent until you say yes"),
    hook=Scene("What we'll build", "hook", "The finished assistant", items=(
        "Form\na question comes in",
        "Claude\nwrites a draft reply",
        "You\napprove before it's sent",
    ), code="New question: \"Do you deliver on weekends?\"\nDraft reply ready for you",
        caption="nothing is sent until you say yes", said=(
            ("[happy] Nothing is sent until you say yes. Today, no setup at all: just "
             "the big picture, so every later step makes sense."),),
    ),
    scenes=(
        Scene("The big picture", "diagram", "One question, start to finish", items=(
            "Form\nsomeone asks",
            "n8n\nmoves it along",
            "Claude\nwrites a draft",
            "You\napprove",
        ), said=(
            ("Let's follow one question. A customer fills in a form on your website "
             "and asks something simple, like, do you deliver on weekends?"),
            ("n8n picks it up and moves it from step to step. It's the plumbing. "
             "[chuckling] Not glamorous, but you'd miss it. It does the same thing, "
             "the same way, every single time."),
            ("Claude reads the question and writes a short, friendly draft reply. "
             "That's the part that needs language, so that's Claude's job."),
            ("And then you. You read the draft, fix it if you want, and approve it. "
             "Only then does anything go out."))),
        Scene("Who does which job", "bullets", "Who does which job", items=(
            "n8n: fixed steps, every time",
            "Claude: reading and writing",
            "You: the final yes",
        ), said=(
            ("Here's the simple rule we'll use all series. n8n does the fixed steps: "
             "catch the form, save a copy, send a message."),
            ("Claude does the reading and the writing. It never decides on its own to "
             "send an email or change anything."),
            ("And you give the final yes. Keeping those three jobs apart is what makes "
             "this safe, and easy to fix when something goes wrong."))),
        Scene("", "quiz", "Which part writes the draft reply?", items=(
            "n8n", "Claude", "The form",
        ), answer=1, caption="Claude writes; n8n moves things; you approve.", said=(
            "Quick quiz. Which part writes the draft reply? Comment your answer.",
            "It's B, Claude. n8n moves things around, and you approve.")),
        Scene("Five words", "bullets", "Five words you need", items=(
            "Workflow: the whole automation",
            "Trigger: what starts it",
            "Node: one step",
            "MCP: a standard plug for AI apps",
            "Approval: a person says yes first",
        ), said=(
            ("Five words, and you're ready. A workflow is the whole automation, from "
             "start to finish, in n8n."),
            ("A trigger is what starts it. In our project, that's someone sending the "
             "form."),
            ("A node is one step in the workflow, like save a row, or send a message."),
            ("MCP is short for Model Context Protocol. The official site calls it an "
             "open standard for connecting AI apps to other systems. Think of it as "
             "one standard plug, so Claude can use your n8n workflow as a tool."),
            ("And approval means a person says yes before anything leaves. We build "
             "that in from the start, not at the end."))),
        Scene("", "subscribe", "Building along?", said=(
            ("If you want to build this with me, subscribe now, so you don't miss the "
             "next step of the project."),)),
        Scene("Why you approve", "bullets", "Why a person approves", items=(
            "AI can be wrong, and sound sure",
            "A customer sees what you send",
            "One click to check is cheap",
        ), said=(
            ("Why do we keep a person in charge? [sighing] Because AI can be wrong, "
             "and it can sound very sure while it's wrong."),
            ("A customer sees whatever you send, so a wrong answer costs you trust. "
             "Your name is on it, not the AI's."),
            ("Reading a draft and clicking approve takes a few seconds. That's a small "
             "habit that saves you from big mistakes, and over time it shows you "
             "exactly where the drafts need better instructions."))),
        Scene("", "quiz", "When does the reply go out?", items=(
            "As soon as Claude writes it",
            "After you approve it",
            "Never",
        ), answer=1, caption="After you approve it. Never before.", said=(
            ("Last quiz. When does the reply actually go to the customer? Comment your "
             "answer."),
            "B, after you approve it. Never before.")),
        Scene("What to have ready", "checklist", "What to have ready", items=(
            "A Claude account at claude.ai",
            "An n8n workspace: Cloud or self-hosted",
            "One real question your customers ask",
        ), said=(
            ("Before the next episode, have three things ready. First, a Claude "
             "account. You sign up at claude dot ai."),
            ("Second, an n8n workspace. n8n Cloud is the easy start, with nothing to "
             "install. Self-hosting works too, if you already have it."),
            ("And third, one real question your customers ask you a lot. You know the "
             "one. [chuckling] We'll use it to test every step."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write down one question customers ask",
            "Write the reply you'd want to send",
        ), said=(
            "Pause the video and try it. Write down one question your customers ask,",
            ("and the reply you'd want them to get. Resume when you have both. That's "
             "your test case."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Form in, draft out, you approve",
            "n8n moves it · Claude writes",
            "Workflow, trigger, node, MCP, approval",
            "Next: plan every piece",
        ), said=(
            "Recap. A question comes in, Claude drafts a reply, and you approve it.",
            "n8n moves things along, and Claude does the writing.",
            "You know the five words: workflow, trigger, node, MCP and approval.",
            ("[happy] Next episode, we plan every piece of the project, one screen at "
             "a time. See you there."))),
    ),
)

BUSINESS_LEVEL0 = scripted(
    BUSINESS_LEVEL0,
    links=(("n8n quickstart", "https://docs.n8n.io/try-it-out/quickstart/"),
           ("Choose how to use n8n", "https://docs.n8n.io/choose-n8n/")),
    ask="What's one task you repeat every week in your business? Tell me in the comments.",
    topic_said=("Today: what automation means for a small business, how to pick your "
                "first task, and the jobs a person should always keep."),
    topic_shown="What to automate first, and what not to",
    payoff=payoff_scene(
        ("You'll finish this with a three-question test you can run on any task in "
         "your week, and the jobs a person should always keep."),
        "Repeats? Same steps? Boring? → automate it first",
        items=(
            "Does it repeat?\nevery day or week",
            "Same steps?\nno judgment needed",
            "Is it boring?\nthen it's a good start",
        ),
        caption="three yeses: a good first automation"),
    hook=Scene("What you'll learn", "hook", "Three questions", items=(
        "Does it repeat?\nevery day or week",
        "Same steps?\nno judgment needed",
        "Is it boring?\nthen it's a good start",
    ), code="New enquiry → saved → \"Thanks, we'll reply today\"",
        caption="three yeses: a good first automation", said=(
            ("Here's a simple test. Does the task repeat? Are the steps always the "
             "same? Is it boring? [chuckling] Three yeses, and it's a great first "
             "automation."),
            "By the end, you'll have your own list of tasks to start with.")),
    scenes=(
        Scene("What automation is", "bullets", "Automation, for a small business", items=(
            "The computer does a job you repeat",
            "Same steps, same way, every time",
            "You set it up once",
        ), said=(
            ("For a small business, automation means the computer does a job you "
             "keep doing by hand. A shop, a studio, a freelancer, it's the same idea."),
            ("It follows the same steps, the same way, every time. It doesn't forget "
             "on a busy Friday, and it doesn't get tired."),
            ("[happy] You set it up once, and then it just runs. The time you get "
             "back is time for customers, or for a break."))),
        Scene("Worth automating", "bullets", "A task worth automating", items=(
            "Repeated: daily or weekly",
            "Rule-based: the same steps",
            "Boring: nobody misses doing it",
        ), said=(
            ("So which tasks? First, it repeats. If you do it every day or every week, "
             "it's a candidate."),
            ("Second, it follows rules. If you could write the steps on a sticky note, "
             "a computer can follow them too."),
            ("Third, it's boring. [sighing] Copying details from an email into a "
             "spreadsheet is a perfect example. Nobody misses doing that, and it's "
             "exactly where small mistakes creep in when you're tired or in a hurry."))),
        Scene("", "quiz", "Automate it or keep it?", items=(
            "Copying form details into a sheet",
            "Deciding a refund",
            "Neither",
        ), answer=0, caption="Copying is repeated and rule-based: automate it.", said=(
            ("Quick quiz. Which one would you automate first: copying form details "
             "into a sheet, or deciding a refund? Comment your answer."),
            "It's A, copying details. It's repeated and it follows the same steps.")),
        Scene("Five starters", "steps", "Five common starters", items=(
            "Leads: reply to every enquiry fast",
            "Bookings: send reminders",
            "Invoices: polite follow-ups",
            "Reports: a weekly summary",
            "Posts: draft, then you approve",
        ), said=(
            ("Here are five places most small businesses start. One, leads: every "
             "enquiry gets a quick, friendly first reply, even at night."),
            ("Two, bookings: a reminder goes out the day before, so fewer people "
             "forget, and you spend less time waiting for someone who isn't "
             "coming."),
            ("Three, invoices: a polite follow-up when one is still open, so you don't "
             "have to chase by hand."),
            ("Four, reports: a short weekly summary lands in your inbox or in Slack, "
             "so you see how the week went without opening five different apps."),
            ("And five, social posts: AI drafts a caption, and you approve it before "
             "anything is posted. We'll build each of these in this series."))),
        Scene("", "subscribe", "Saving you time already?", said=(
            ("If this is useful, subscribe. Every episode in this series builds one "
             "of these, step by step."),)),
        Scene("What a person keeps", "bullets", "What a person always keeps", items=(
            "Anything about money: refunds, discounts",
            "Anything that makes a promise",
            "Anything you can't undo",
        ), said=(
            ("Now the other side: what not to automate. Anything about money, like a "
             "refund or a discount, stays with a person."),
            ("Anything that makes a promise to a customer gets a person's yes first. "
             "A fixed thank-you note is fine. A promise is not."),
            ("And anything you can't undo, like deleting records. Here's the simple "
             "rule to remember: the computer prepares, and a person decides."))),
        Scene("", "quiz", "Who should approve a refund?", items=(
            "The automation, by itself", "A person", "Nobody",
        ), answer=1, caption="Money decisions stay with a person.", said=(
            "Last quiz. Who should approve a refund? Comment your answer.",
            "B, a person. Anything about money stays with a person.")),
        Scene("Start small", "steps", "How to start", items=(
            "Pick one task",
            "Write its steps down",
            "Automate the boring part only",
        ), said=(
            ("How to start? Pick just one task, the most boring one on your list. "
             "[chuckling] You already know which one. One finished automation beats "
             "five half-built ones."),
            ("Write its steps down in plain words, in order. For example: open the "
             "email, copy the name, paste it in the sheet, send a thank-you. That "
             "list becomes your workflow later."),
            ("Then automate only the boring part, and keep the decisions for "
             "yourself. You can always add more next week."))),
        Scene("", "pause", "Pause and try this", items=(
            "List three tasks you repeat each week",
            "Circle the most boring one",
        ), said=(
            "Pause the video and try it. List three tasks you repeat every week,",
            "then circle the most boring one. Resume when you have it.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Repeated, rule-based, boring: automate",
            "Leads, bookings, invoices, reports, posts",
            "Money and promises stay with a person",
            "Start with one task",
        ), said=(
            "Recap. If a task is repeated, rule-based and boring, automate it.",
            "Leads, bookings, invoices, reports and posts are great places to start.",
            "Money, promises and anything you can't undo stay with a person.",
            ("[happy] And start with one task. Next episode, we build the first one: "
             "a fast reply to every new lead."))),
    ),
)

LOCAL_LEVEL0 = scripted(
    LOCAL_LEVEL0,
    links=(("Hermes Agent (Nous Research)", "https://github.com/NousResearch/hermes-agent"),
           ("NVIDIA DGX Spark playbooks", "https://build.nvidia.com/spark")),
    ask=("Would you rather run AI in the cloud or on your own computer? Tell me why in "
         "the comments."),
    topic_said=("Today we start from zero: what a model is, and what running AI on your "
                "own computer really means."),
    topic_shown="What running AI locally means",
    payoff=payoff_scene(
        ("You'll be able to say what a model is, in plain words, and decide whether "
         "running one at home is worth it for you."),
        "Model (on your computer): Here are the three points…",
        items=(
            "Cloud\nsomeone else's computer",
            "Local\nyour own computer",
            "Same idea\na model answers you",
        ),
        caption="the question never leaves your machine"),
    hook=Scene("What you'll learn", "hook", "Two places AI can run", items=(
        "Cloud\nsomeone else's computer",
        "Local\nyour own computer",
        "Same idea\na model answers you",
    ), code="You: Summarize this note\nModel (on your computer): Here are the three points…",
        caption="the question never leaves your machine", said=(
            ("Here's the idea. Most AI apps run on a company's computers. Local AI "
             "runs on yours, so your question never has to leave your machine."),
            "By the end, you'll know what that means, and when it makes sense.",
            ("You don't need any special hardware to follow this one. It's the "
             "words and the questions to ask, before anything is installed."))),
    needs=("nothing to install: this one explains before anything is set up",),
    scenes=(
        Scene("What a model is", "bullets", "What is a model?", items=(
            "The part of AI that does the thinking",
            "Learned from a huge amount of text",
            "Stored as files on a computer",
        ), said=(
            ("Let's start with one word: model. A model is the part of an AI app that "
             "actually does the thinking. You send it words, and it sends words back."),
            ("It learned by reading a huge amount of text, [chuckling] way more than "
             "any of us will ever read, and it's very good at working out what a "
             "helpful answer looks like."),
            ("And here's the key point. [break] In the end, a model is stored as "
             "files on a computer. Whoever has those files, and a strong enough "
             "computer, can run it."))),
        Scene("Cloud or local", "diagram", "Cloud AI vs local AI", items=(
            "Cloud\nyour words travel to a company",
            "Local\nyour words stay with you",
        ), said=(
            ("So there are two places a model can run. In the cloud, you type into an "
             "app, your words travel over the internet to a company's computers, the "
             "model answers there, and the answer comes back."),
            ("Local means the model runs on a computer you own. Your words go to your "
             "own machine, and the answer is made right there. Nothing has to leave."))),
        Scene("", "quiz", "A chat app in your browser: where's the model?",
              items=("On your computer", "In the cloud", "Nowhere"),
              answer=1, caption="A browser chat app sends your words to the cloud.", said=(
                  ("Quick quiz. You use an AI chat app in your browser. Where does the "
                   "model usually run? Comment your answer."),
                  ("It's B, the cloud. Your words travel to the company's computers "
                   "and the answer comes back."))),
        Scene("Why run it yourself", "bullets", "Why people run AI locally", items=(
            "Privacy: your data stays with you",
            "Control: you choose the model and when it changes",
            "Offline: it can work without internet",
        ), said=(
            ("Why would anyone run AI themselves? First, privacy. Some notes, files and "
             "customer details are things you'd rather not send anywhere."),
            ("Second, control. You choose the model, and it only changes when you "
             "decide to change it."),
            ("And third, it can work offline, once everything is installed. No "
             "internet, still an answer. [chuckling] Handy on a train."))),
        Scene("The trade-offs", "bullets", "The honest trade-offs", items=(
            "You need capable hardware",
            "You set it up and keep it running",
            "The biggest cloud models may not fit",
        ), said=(
            ("[sighing] But it's not free of trade-offs. You need a capable computer, "
             "and running models asks a lot of it."),
            ("You set everything up yourself, and you keep it running and up to date. "
             "Nobody does that for you."),
            ("And the biggest models from the big cloud companies may not run on your "
             "machine at all. Local is a choice, not an automatic upgrade."))),
        Scene("", "subscribe", "Curious about local AI?", said=(
            ("If you want to see this done for real, subscribe. This series builds a "
             "full local setup, step by step."),)),
        Scene("Memory comes first", "bullets", "The first hardware question", items=(
            "A model has to fit in memory to run well",
            "Bigger models need more memory",
            "Check the maker's guide before you buy",
        ), said=(
            ("When people ask what computer they need, the first question is memory. A "
             "model has to fit in memory to run well."),
            ("Bigger models usually need more memory. So the model you want decides "
             "the machine you need, not the other way around."),
            ("Always check the official guides for your hardware before you buy "
             "anything. In this series, we follow NVIDIA's own published guides, "
             "never guesses."))),
        Scene("", "quiz", "What's the first hardware question?", items=(
            "The screen size", "Memory", "The keyboard",
        ), answer=1, caption="A model has to fit in memory to run well.", said=(
            "Last quiz. What's the first hardware question for local AI? Comment.",
            "B, memory. The model has to fit in memory to run well.")),
        Scene("Model vs agent", "bullets", "A model is not an agent", items=(
            "Model: answers what you ask",
            "Agent: uses the model, plus tools, to do tasks",
            "Next: Hermes Agent, an open-source agent",
        ), said=(
            ("One more idea, and it's the reason for the rest of this series. A model "
             "answers what you ask. That's all it does."),
            ("An agent is a program around the model. It can use tools, like reading a "
             "file or searching, and work through a task step by step."),
            ("Next episode, we meet Hermes Agent, an open-source agent from Nous "
             "Research, that can talk to a model you run yourself."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write down one task you'd keep private",
            "Would it need the internet?",
        ), said=(
            "Pause the video and think. Write down one task you'd want to keep private,",
            "and ask yourself, would it need the internet? Resume when you have it.")),
        Scene("Recap", "checklist", "Recap", items=(
            "A model: the part that thinks, stored as files",
            "Cloud: their computers · local: yours",
            "Privacy, control, offline, and real trade-offs",
            "Memory is the first question",
        ), said=(
            "Recap. A model is the part of AI that thinks, and it's stored as files.",
            "Cloud means their computers. Local means yours.",
            "Local gives you privacy, control and offline use, with real trade-offs.",
            ("[happy] And memory is the first hardware question. Next: Hermes Agent. "
             "See you there."))),
    ),
)


# ------------------------- scripted 2026-10-11: two days ahead per series
#
# The rung after today's for every series, so each ladder stays scripted two
# days ahead. Sources as in the block above, read 2026-10-11, and:
#   docs.n8n.io/.../n8n-nodes-base.googlecalendar/event-operations/ (Get Many:
#         Return All, Limit, After, Before)
#   github.com/modelcontextprotocol/servers/tree/main/src/time
#         (`uvx mcp-server-time`; get_current_time, convert_time)

MCP_ADD_EXAMPLES = """# a remote server, just for you, in this project (local scope)
claude mcp add --transport http notion https://mcp.notion.com/mcp

# shared with your team through .mcp.json (project scope)
claude mcp add --transport http shared --scope project https://example.com/mcp

# a local stdio server: everything after -- runs it
claude mcp add --transport stdio time -- uvx mcp-server-time"""

CLAUDE_EP2 = scripted(
    next(e for e in CLAUDE_PLANNED if e.number == 2),
    links=(("Connect Claude Code to tools via MCP", "https://code.claude.com/docs/en/mcp"),
           ("MCP reference servers", "https://github.com/modelcontextprotocol/servers")),
    ask="Which MCP server are you adding first? Tell me in the comments.",
    topic_said=("Today: adding MCP servers to Claude Code the right way, which ones to "
                "start with, and what to check when one won't connect."),
    topic_shown="Add MCP servers, and fix the ones that fail",
    payoff=payoff_scene(
        ("Here's the goal. You add a server, you check it, and you see the word "
         "Connected. And when you don't, you'll know exactly what to check."),
        "time: uvx mcp-server-time - ✔ Connected",
        items=(
            "Add\nclaude mcp add",
            "Check\nclaude mcp list",
            "Fix\na calm checklist",
        ),
        caption="the status you want to see"),
    hook=Scene("What you'll learn", "hook", "From added to connected", items=(
        "Add\nclaude mcp add",
        "Check\nclaude mcp list",
        "Fix\na calm checklist",
    ), code="claude mcp list\ntime: uvx mcp-server-time - ✔ Connected",
        caption="the status you want to see", said=(
            "No guessing. Everything here comes from the official Claude Code docs.",
            "You'll need Claude Code, and uv for the Time server.")),
    needs=("Claude Code, installed", "uv, for the Time server's uvx", "a terminal"),
    scenes=(
        Scene("Three scopes", "bullets", "Where a server is saved", items=(
            "Local (default): this project, only you",
            "Project: .mcp.json, shared with your team",
            "User: every project on your computer",
        ), said=(
            ("When you add a server to Claude Code, you choose a scope. That's where "
             "it's saved, and who can use it. Local is the default: it loads only in "
             "this project, and only for you."),
            ("Project scope saves the server in a file called dot mcp dot json at the "
             "root of your project. You commit it, and your whole team gets it. "
             "Claude Code asks for your approval before using a server from that "
             "file."),
            ("And user scope makes a server available in every project on your "
             "computer. Good for tools you use everywhere."))),
        Scene("Adding one", "code", "claude mcp add", code=MCP_ADD_EXAMPLES,
              marks=((0, 2), (3, 5), (6, 8)),
              caption="From code.claude.com/docs/en/mcp, read 2026-10-11.", said=(
                  ("Here's the command. For a remote server, use transport http, then "
                   "a name, then the address. This one is saved in local scope."),
                  ("Add dash dash scope project, and it goes into dot mcp dot json "
                   "for the whole team instead."),
                  ("A local server runs as a program on your computer, over stdio. "
                   "Everything after the double dash is the command that starts it. "
                   "Here, that's the Time reference server."))),
        Scene("", "quiz", "Which scope shares a server with your team?", items=(
            "Local", "Project", "User",
        ), answer=1, caption="Project scope: .mcp.json, in version control.", said=(
            "Quick quiz. Which scope shares a server with your team? Comment your answer.",
            "It's B, project. It lives in dot mcp dot json, in version control.")),
        Scene("Good first servers", "bullets", "Good first servers", items=(
            "Time: dates and time zones",
            "Fetch: read a web page",
            "Filesystem: files you allow",
            "Git: your repositories",
        ), said=(
            ("Which servers to start with? The MCP project keeps a small set of "
             "reference servers. They're simple, well documented, and great for "
             "learning. Time converts dates and time zones."),
            "Fetch reads a web page and turns it into text the model can use.",
            ("Filesystem works with files, but only in the folders you allow. Give "
             "it the smallest folder that does the job."),
            ("And Git reads and searches your repositories. Add one, try it, and only "
             "then add the next. Five at once is how a whole afternoon disappears. "
             "[chuckling]"))),
        Scene("", "subscribe", "Getting connected?", said=(
            ("If this is helping, subscribe. Next in this series, we write our first "
             "Claude skill."),)),
        Scene("Check the status", "steps", "Is it connected?", items=(
            "claude mcp list: every server and its status",
            "/mcp inside Claude Code: status and sign-in",
            "Connected · Needs authentication · Failed",
        ), said=(
            ("Now check it. In your terminal, claude mcp list shows every server you "
             "added, with its status."),
            ("Inside a Claude Code session, type slash mcp. You see the same status, "
             "and for a remote server that needs a login, this is where you sign in."),
            ("There are three answers to look for: Connected, Needs authentication, "
             "or Failed to connect. [emphasis] Only the first one means you're ready."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add the Time server",
            "Run claude mcp list",
        ), said=(
            "Pause the video and try it. Add the Time server with the command above,",
            "then run claude mcp list and look for Connected. Resume when you see it.")),
        Scene("Not working?", "checklist", "Not working? Check these", items=(
            "Is the command right, and installed?",
            "A remote entry has \"type\": \"http\"",
            "Needs a login? Use /mcp",
            "Slow to start? Raise MCP_TIMEOUT",
        ), said=(
            ("Says failed? [sighing] Don't panic. Check four things. First, the "
             "command. Is it spelled right, and is the program actually installed? "
             "Run it on its own to see."),
            ("Second, in dot mcp dot json, a remote server needs type, http. The docs "
             "warn that an entry with a url but no type is read as a local server."),
            ("Third, if it says needs authentication, open slash mcp and sign in. "
             "Adding a server doesn't check your token; slash mcp does."),
            ("And fourth, a slow server can time out while starting. The docs show "
             "the MCP underscore TIMEOUT setting to give it longer."))),
        Scene("", "quiz", "It says \"Needs authentication\". What now?", items=(
            "Delete Claude Code", "Sign in through /mcp", "Wait a day",
        ), answer=1, caption="/mcp handles the sign-in for remote servers.", said=(
            "Last quiz. A server says needs authentication. What do you do? Comment.",
            "B. Open slash mcp and sign in.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Scopes: local, project, user",
            "claude mcp add, then claude mcp list",
            "Start with a reference server",
            "Failed? command, type, sign-in, timeout",
        ), said=(
            "Recap. A server has a scope: local, project or user.",
            "Add it with claude mcp add, then check it with claude mcp list.",
            "Start with a simple reference server, one at a time.",
            ("[happy] And if it fails, check the command, the type, the sign-in and "
             "the timeout. See you in the next one."))),
    ),
)

N8N_EP2 = scripted(
    next(e for e in N8N_PLANNED if e.number == 2),
    links=(("Schedule Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/"),
           ("Webhook docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/"),
           ("Gmail Trigger docs",
            "https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger/")),
    ask="Which trigger will your next workflow use? Tell me in the comments.",
    topic_said=("Today: the three kinds of trigger in n8n, schedule, webhook and app "
                "triggers, and how to pick the right one."),
    topic_shown="Schedule, Webhook and app triggers",
    payoff=payoff_scene(
        ("You'll end with your own n8n workflow that says good morning every day at "
         "nine, without you touching it."),
        "Every day 9:00 → \"Good morning! Today is …\"",
        items=(
            "Schedule\nat a time you choose",
            "Webhook\nwhen something calls you",
            "App trigger\nwhen something happens",
        ),
        caption="every workflow starts with one trigger"),
    hook=Scene("What you'll learn", "hook", "Three ways to start", items=(
        "Schedule\nat a time you choose",
        "Webhook\nwhen something calls you",
        "App trigger\nwhen something happens",
    ), code="Every Monday 9:00 → build the report\nForm sent → save the lead\nNew email → sort it",
        caption="every workflow starts with one trigger", said=(
            ("Every n8n workflow starts with a trigger. There are three kinds you'll "
             "use all the time: on a schedule, when something calls you, and when "
             "something happens in an app."),
            "By the end, you'll know which one to reach for, every time.",
            ("All you need is n8n, Cloud or your own. And a lab file waits at the "
             "end, so you can try a schedule yourself."))),
    needs=("n8n, Cloud or self-hosted",),
    scenes=(
        Scene("What a trigger does", "bullets", "What a trigger does", items=(
            "It starts the run",
            "It hands the first data to the next node",
            "It only listens once the workflow is published",
        ), said=(
            ("A quick reminder. The trigger is the first node, and it's the thing "
             "that starts each run. Without it, nothing happens. [chuckling] It's the "
             "key in the ignition."),
            ("It also hands over the first piece of data, like the form answers or the "
             "email, to the next node."),
            ("And for real use, the workflow must be published. While you build, you "
             "test it by hand. Once it's published, the trigger listens by itself."))),
        Scene("Schedule Trigger", "steps", "Schedule Trigger", items=(
            "Pick an interval: minutes, hours, days, weeks…",
            "Or a custom cron expression",
            "Time zone: the workflow's, or the instance's",
        ), said=(
            ("The Schedule Trigger runs at times you choose. Pick an interval, like "
             "every hour, every day at nine, or every Monday."),
            ("For anything unusual, there's a custom cron option, a short code for "
             "times. The docs point to a free cron helper site if you need it."),
            ("One thing to check: the time zone. [sighing] A nine o'clock report at "
             "three in the morning? No thanks. n8n uses the workflow's time zone if "
             "you set one, otherwise the instance's. Set it, so nine means your nine."))),
        Scene("", "quiz", "Send a report every Monday. Which trigger?", items=(
            "Schedule", "Webhook", "Gmail Trigger",
        ), answer=0, caption="A fixed time: the Schedule Trigger.", said=(
            "Quick quiz. You want a report every Monday morning. Which trigger? Comment.",
            "It's A, the Schedule Trigger. A fixed time means a schedule.")),
        Scene("Webhook", "steps", "Webhook", items=(
            "n8n gives you a web address",
            "Another app or a form calls it",
            "Test URL while building · Production once published",
        ), said=(
            ("The Webhook node gives your workflow its own web address. When anything "
             "calls that address, the workflow runs, right away."),
            ("That might be a form on your website, a payment app, or another tool "
             "that can send a web request. It supports the usual methods, like GET "
             "and POST. Most apps send POST, with the data inside."),
            ("Remember the two addresses. The test one is for building, and you see "
             "the data in the editor. The production one works once you publish."))),
        Scene("", "subscribe", "Starting to click?", said=(
            ("If triggers are starting to click, subscribe. Next, we manage workflows "
             "once they're running."),)),
        Scene("App triggers", "bullets", "App triggers", items=(
            "Built for one app: Gmail, Slack, Sheets…",
            "Some check every few minutes (polling)",
            "Gmail Trigger: Message Received",
        ), said=(
            ("App triggers are made for one app. Search for the app's name, and if it "
             "has a trigger, you'll see it in the list."),
            ("Some of them check the app on a timer, every few minutes. That's called "
             "polling, so they're not always instant. For most jobs, a few minutes "
             "late is perfectly fine. [chuckling] Your inbox will survive."),
            ("For example, the Gmail Trigger checks for new mail at the poll times "
             "you choose, and starts a run for each message it finds."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add a Schedule Trigger",
            "Set it to every day at 9",
        ), said=(
            "Pause the video and try it. Create a workflow and add a Schedule Trigger,",
            "then set it to every day at nine. Resume when it's set.")),
        Scene("Which one?", "diagram", "Pick the trigger", items=(
            "A time?\nSchedule",
            "Something calls you?\nWebhook",
            "Something in an app?\nApp trigger",
        ), said=(
            ("Here's how to choose. Is the job tied to a time, like every morning or "
             "every Friday? Use a schedule."),
            ("Will another app or form send you the data, the moment something "
             "happens? Use a webhook."),
            ("Does it start when something happens inside an app you use? Look for "
             "that app's trigger. If there isn't one, a schedule that checks the app "
             "works too. And if you're not sure, [emphasis] start with a schedule: "
             "it's the easiest to test."))),
        Scene("", "quiz", "A website form sends you data. Which trigger?", items=(
            "Schedule", "Webhook", "Nothing",
        ), answer=1, caption="Something calls you: a webhook (or the Form Trigger).",
            said=(
                "Last quiz. A website form sends you its data. Which trigger? Comment.",
                ("B, a webhook. Something calls you. n8n's own Form Trigger works the "
                 "same way."))),
        lab_scene("Lab: every day at nine", "Lab: every day at nine",
                  "n8n-every-day-at-nine.json",
                  do="Import n8n-every-day-at-nine.json",
                  expect="Execute workflow: \"Good morning! Today is …\"",
                  stuck="Never runs at nine? Publish it · check the time zone",
                  challenge="Make it run on Mondays only",
                  said=(
                      ("Your lab. Download the lab file from the description and import "
                       "it: a Schedule Trigger, every day at nine, and an Edit Fields "
                       "node."),
                      ("Click Execute workflow to test it now. [excited] Expect a "
                       "good morning line with today's weekday."),
                      ("Stuck? If it never runs at nine by itself, it isn't published "
                       "yet, or the time zone is wrong. Check the workflow settings."),
                      "Your challenge: make it run on Mondays only, then publish it.")),
        Scene("Recap", "checklist", "Recap", items=(
            "A trigger starts every run",
            "Schedule: at a time you choose",
            "Webhook: when something calls your address",
            "App trigger: when something happens in an app",
        ), said=(
            "Recap. The trigger starts every run, and it only listens once published.",
            "A schedule runs at times you choose. Set the time zone.",
            "A webhook runs when something calls your address.",
            ("[happy] And an app trigger runs when something happens in that app. "
             "Tell me in the comments which one you'll use first. Next: managing your "
             "workflows once they're running."))),
    ),
)

PROJECT_EP2 = scripted(
    next(e for e in PROJECT_PLANNED if e.number == 2),
    links=(("n8n MCP Server Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/"),
           ("n8n MCP Client Tool docs",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/"
             "n8n-nodes-langchain.toolmcp/")),
           ("Connect Claude Code to tools via MCP", "https://code.claude.com/docs/en/mcp")),
    ask="What would you let Claude call in your n8n first? Tell me in the comments.",
    topic_said=("Today we connect Claude to n8n with MCP, so Claude can use your n8n "
                "workflow as a tool."),
    topic_shown="Connect Claude to n8n with MCP",
    payoff=payoff_scene(
        ("Here's the goal. You ask Claude to log a request, Claude asks to use a "
         "tool, and that tool is your own n8n workflow."),
        "Claude → n8n tool \"log_request\" → saved ✓",
        items=(
            "Claude\nasks to use a tool",
            "n8n MCP Server Trigger\nlistens",
            "Your workflow\ndoes the work",
        ),
        caption="you approve each tool call"),
    hook=Scene("What we'll build", "hook", "Claude calls your workflow", items=(
        "Claude\nasks to use a tool",
        "n8n MCP Server Trigger\nlistens",
        "Your workflow\ndoes the work",
    ), code="You: Log this request for Ana\nClaude → n8n tool \"log_request\" → saved ✓",
        caption="you approve each tool call", said=(
            "[excited] That connection is MCP. Let's wire it up, step by step.",)),
    scenes=(
        Scene("n8n as an MCP server", "bullets", "n8n as an MCP server", items=(
            "MCP Server Trigger: n8n speaks MCP",
            "It doesn't pass data on: it serves tools",
            "Tools: workflows you attach",
        ), said=(
            ("In n8n, there's a node called the MCP Server Trigger. The docs say it "
             "lets n8n act as an MCP server, so MCP apps like Claude can use your n8n "
             "tools and workflows."),
            ("It's a different kind of trigger. It doesn't pass data to a next node. "
             "It only connects to tool nodes, and runs them when they're called."),
            ("And the tools are what you attach to it. To offer a whole workflow as "
             "a tool, you use the Custom n8n Workflow Tool node."))),
        Scene("Set it up", "steps", "Set up the server", items=(
            "Add an MCP Server Trigger",
            "Attach a Custom n8n Workflow Tool",
            "Name it and describe it clearly",
            "Pick authentication: Bearer or Header",
        ), said=(
            ("Create a new workflow and add the MCP Server Trigger as its first node. "
             "Its path is a random string by default, which is fine to keep."),
            ("Attach a Custom n8n Workflow Tool, and point it at the workflow that "
             "saves a request."),
            ("Give the tool a clear name and description, like: save a customer "
             "request. That description is how Claude knows when to use it, so write "
             "it for a stranger. [chuckling] A stranger who reads very literally."),
            ("Then pick authentication. The node offers none, bearer or header "
             "auth. Use one, and keep the token in n8n's credentials."))),
        Scene("", "quiz", "What tells Claude when to use your tool?", items=(
            "The tool's description", "The node's color", "Nothing",
        ), answer=0, caption="A clear name and description guide the model.", said=(
            "Quick quiz. What tells Claude when to use your tool? Comment your answer.",
            "It's A, the tool's description. Make it short and clear.")),
        Scene("Its address", "bullets", "Two addresses again", items=(
            "Test URL: while you build",
            "Production URL: once published",
            "Streamable HTTP or SSE, never stdio",
        ), said=(
            ("Like the Webhook, the MCP Server Trigger has two addresses. The test "
             "one works while you build, and you can watch the calls in the editor."),
            ("The production one is registered when you publish the workflow. Its "
             "runs show up in the Executions tab."),
            ("It talks over the web, with streamable HTTP or SSE. It does not support "
             "stdio, so Claude connects to it as a remote server."))),
        Scene("", "subscribe", "Building along?", said=(
            ("If you're building along, subscribe. Next episode, we write our own "
             "MCP server and plug it into a workflow."),)),
        Scene("Add it to Claude", "steps", "Add it to Claude Code", items=(
            "Copy the production URL",
            "claude mcp add --transport http …",
            "Add the token as a header",
            "Check with claude mcp list",
        ), said=(
            ("Publish the workflow, then copy the production address from the node. "
             "[emphasis] Not the test one."),
            ("In your terminal, run claude mcp add, transport http, a name like n8n, "
             "then the address."),
            ("Pass your token with the header option, so Claude Code sends it on "
             "every call. Never paste the token into a chat."),
            ("Then run claude mcp list and look for Connected. [excited] There it is. "
             "For Claude Desktop, n8n's docs show a small bridge called mcp-remote "
             "instead."))),
        Scene("", "pause", "Pause and try this", items=(
            "Publish the MCP workflow",
            "Add it with claude mcp add",
        ), said=(
            "Pause the video and try it. Publish your MCP workflow,",
            "then add it with claude mcp add and check for Connected. Resume when it is.")),
        Scene("The other way round", "bullets", "n8n as an MCP client", items=(
            "MCP Client Tool: n8n uses another server",
            "For an n8n AI agent",
            "Tools to include: All, Selected, All Except",
        ), said=(
            ("MCP works the other way too. The MCP Client Tool node lets n8n use the "
             "tools of an outside MCP server."),
            ("You attach it to an AI agent inside n8n, so the agent can call those "
             "tools while it works."),
            ("And you choose which tools it sees: all of them, only the ones you "
             "select, or all except some. Fewer tools is safer, and it also helps the "
             "agent pick the right one."))),
        Scene("", "quiz", "Which node makes n8n an MCP server?", items=(
            "MCP Client Tool", "MCP Server Trigger", "Webhook",
        ), answer=1, caption="Server Trigger: serve tools. Client Tool: use them.", said=(
            "Last quiz. Which node makes n8n an MCP server? Comment your answer.",
            "B, the MCP Server Trigger. The Client Tool uses someone else's.")),
        Scene("Recap", "checklist", "Recap", items=(
            "MCP Server Trigger + a Workflow Tool",
            "Clear tool names and descriptions",
            "Auth on, token in credentials",
            "claude mcp add, then check Connected",
        ), said=(
            "Recap. The MCP Server Trigger, with a workflow tool attached, makes n8n a server.",
            "Give each tool a clear name and description.",
            "Turn authentication on, and keep the token in credentials.",
            ("Then add it with claude mcp add, and check for Connected. Ask Claude to "
             "log a test request, approve the tool call, and [excited] watch the row "
             "appear. Next: our own MCP server."))),
    ),
)

BUSINESS_EP2 = scripted(
    next(e for e in BUSINESS_PLANNED if e.number == 2),
    links=(("Schedule Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/"),
           ("Google Calendar node docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlecalendar/"),
           ("Gmail node docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/")),
    ask="Do your customers prefer reminders by email or by text? Tell me in the comments.",
    topic_said=("Today: booking reminders that send themselves, from your calendar, "
                "with one friendly message and a record of what was sent."),
    topic_shown="Booking reminders on autopilot",
    payoff=payoff_scene(
        ("Here's the result. Every evening, n8n reads tomorrow's bookings from your "
         "calendar, and each customer gets a friendly reminder."),
        "Hi Ana, a reminder: your appointment is tomorrow at 10:00. See you then!",
        items=(
            "Every evening\nthe schedule runs",
            "Tomorrow's bookings\nread from the calendar",
            "One reminder each\nand a log row",
        ),
        caption="one template, sent to each booking"),
    hook=Scene("What we'll build", "hook", "Reminders that send themselves", items=(
        "Every evening\nthe schedule runs",
        "Tomorrow's bookings\nread from the calendar",
        "One reminder each\nand a log row",
    ), code="Hi Ana, a reminder: your appointment is tomorrow at 10:00. See you then!",
        caption="one template, sent to each booking", said=(
            "Every message is logged, so you always know what was sent. Let's build it.",)),
    scenes=(
        Scene("Why reminders", "bullets", "Why send reminders?", items=(
            "People forget, even good customers",
            "A reminder is a small, kind nudge",
            "The same message, every time: perfect to automate",
        ), said=(
            ("Why reminders? Because people forget, even your best customers. "
             "[sighing] Life is busy, and a booking made two weeks ago is easy to "
             "lose track of."),
            ("A short reminder the day before is a kind nudge. It also gives people a "
             "chance to tell you early if they can't make it."),
            ("And it's the same message every time, with only the name and the time "
             "changing. That makes it perfect for automation."))),
        Scene("Step 1: every evening", "steps", "Step 1: every evening", items=(
            "Add a Schedule Trigger",
            "Every day, at 6 in the evening",
            "Set the workflow's time zone",
        ), said=(
            "Create a workflow and add a Schedule Trigger.",
            ("Set it to run every day, at six in the evening. That's late enough to "
             "catch last-minute bookings, and early enough to be polite. [chuckling] "
             "Nobody wants a reminder at midnight."),
            ("Then set the workflow's time zone to yours. Otherwise n8n uses the "
             "instance's time zone, and six might not mean your six."))),
        Scene("Step 2: tomorrow's bookings", "steps", "Step 2: tomorrow's bookings", items=(
            "Add Google Calendar: Event, Get Many",
            "After / Before: tomorrow only",
            "One item per booking",
        ), said=(
            ("Next, add a Google Calendar node. Choose Event, and the Get Many "
             "operation, which gets the events from a calendar."),
            ("Limit it to tomorrow's events only. Set After to the start of tomorrow, "
             "and Before to the end of tomorrow, so nobody gets a reminder for next "
             "month."),
            ("Each booking comes out as its own item, and the next nodes run once "
             "for every item. That's how one template becomes many messages."))),
        Scene("", "quiz", "Why one item per booking?", items=(
            "It looks nicer", "Each booking gets its own message", "It's faster",
        ), answer=1, caption="Nodes run once per item: one message each.", said=(
            "Quick quiz. Why does it matter that each booking is its own item? Comment.",
            "B. The next nodes run once per item, so each booking gets its own message.")),
        Scene("Step 3: one template", "steps", "Step 3: one template", items=(
            "Add Gmail: Message, Send",
            "Name and time from expressions",
            "Short, warm, with a way to reschedule",
        ), said=(
            ("Now the message. Add a Gmail node, with Message and Send, and connect "
             "your Google login once, in credentials."),
            ("Write one template, and drop in the customer's name and the time with "
             "expressions. Where those details live depends on how you write your "
             "bookings, so check one event's data first."),
            ("Keep it short and warm, and tell people how to change the time if they "
             "need to, like: just reply to this email. Read it out loud once. If it "
             "sounds like a robot, rewrite it. [chuckling]"))),
        Scene("", "subscribe", "Fewer no-shows?", said=(
            ("If this saves you a few empty slots, subscribe. Next in this series: "
             "polite invoice follow-ups."),)),
        Scene("Step 4: keep a log", "steps", "Step 4: keep a log", items=(
            "Add Google Sheets: Append Row",
            "Who, which booking, when it was sent",
        ), said=(
            ("Last step. Add a Google Sheets node, Append Row, after the email."),
            ("Write who you reminded, which booking it was, and when it was sent. If "
             "a customer says they never got it, you can check in seconds. And at the "
             "end of the month, you can see how many reminders went out."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add a test booking for tomorrow",
            "Run the workflow once by hand",
        ), said=(
            "Pause the video and try it. Add a test booking for tomorrow with your own email,",
            "then run the workflow once by hand. Resume when the reminder arrives.")),
        Scene("Cancellations", "bullets", "When a booking is cancelled", items=(
            "Delete or cancel it in the calendar",
            "Tomorrow's run won't see it",
            "Changes after 6 pm: tell them yourself",
        ), said=(
            ("What about cancellations? Keep your calendar as the single source of "
             "truth. Cancel the booking there, and that's it."),
            ("Because the workflow reads the calendar fresh each evening, a booking "
             "that's gone simply gets no reminder."),
            ("But a change after the reminder has gone out needs a person. Send that "
             "message yourself. It's kinder, and it's clearer."))),
        Scene("", "quiz", "A booking is cancelled at noon. What happens?", items=(
            "It still gets a reminder",
            "No reminder: the evening run reads the calendar fresh",
            "The workflow breaks",
        ), answer=1, caption="The calendar is the single source of truth.", said=(
            "Last quiz. A booking is cancelled at noon. What happens that evening? Comment.",
            "B. No reminder. The evening run reads the calendar fresh.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Schedule: every evening, your time zone",
            "Calendar: tomorrow's events only",
            "Gmail: one warm template",
            "Sheets: a log of what was sent",
        ), said=(
            "Recap. A Schedule Trigger runs every evening, in your time zone.",
            "Google Calendar gets tomorrow's bookings only.",
            "Gmail sends one warm template to each booking.",
            ("[happy] And Google Sheets keeps a log. Fewer forgotten appointments, "
             "with no extra work. Run it by hand for a week and read every message "
             "before you let it run alone."))),
    ),
)

AGENTS_EP2 = scripted(
    next(e for e in AGENTS_PLANNED if e.number == 2),
    links=(("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents"),),
    ask="Name one job you'd give an agent, and one you'd keep as a workflow. Comment below.",
    topic_said=("Today: workflow or agent? A simple way to choose, with five real jobs "
                "to practise on."),
    topic_shown="Agent or workflow: how to choose",
    payoff=payoff_scene(
        ("You'll finish with five real jobs sorted into workflow or agent, and a rule "
         "of thumb for your own."),
        "5 jobs sorted: workflow or agent",
        items=(
            "Workflow\nthe steps are written down",
            "Agent\nthe model picks the steps",
            "You\nchoose the simplest that works",
        ),
        caption="pick the simplest thing that works"),
    hook=Scene("What you'll learn", "hook", "Two ways to automate", items=(
        "Workflow\nthe steps are written down",
        "Agent\nthe model picks the steps",
        "You\nchoose the simplest that works",
    ), code="Save each form entry → workflow\nResearch a new supplier → agent",
        caption="pick the simplest thing that works", said=(
            ("Two ways to automate a job. In a workflow, the steps are written down. "
             "In an agent, the model picks the steps as it goes."),
            "By the end, you'll know which one your job needs.",
            ("Nothing to install. Bring one real job from your week, and we'll "
             "weigh it on cost, speed and predictability."))),
    needs=("nothing to install", "one real job from your week to test"),
    scenes=(
        Scene("Workflow", "bullets", "A workflow", items=(
            "The same steps, in the same order",
            "Easy to test and to predict",
            "Can still use AI inside one step",
        ), said=(
            ("A workflow runs the same steps, in the same order, every time. Anthropic "
             "describes workflows as following predefined code paths."),
            ("That makes it easy to test, and easy to predict. If it worked today, "
             "it'll work the same way tomorrow. [chuckling] Boring, in the best way. "
             "And when something breaks, you can see exactly which step failed."),
            ("And a workflow can still use AI inside a step, like asking a model to "
             "write a summary. The steps around it stay fixed."))),
        Scene("Agent", "bullets", "An agent", items=(
            "The model decides the next step",
            "It uses tools in a loop",
            "Flexible, but harder to predict",
        ), said=(
            ("An agent is different. The model decides what to do next, based on what "
             "it found so far."),
            ("It uses tools in a loop: it looks, decides, acts, and checks the result, "
             "again and again until the job is done."),
            ("That's flexible, and great when nobody can write the steps in advance. "
             "But it's harder to predict, and harder to test, because two runs of the "
             "same job can take different paths."))),
        Scene("", "quiz", "Who decides the steps in a workflow?", items=(
            "The model", "You, in advance", "Nobody",
        ), answer=1, caption="A workflow's steps are written in advance.", said=(
            "Quick quiz. In a workflow, who decides the steps? Comment your answer.",
            "It's B, you, in advance. That's what makes it predictable.")),
        Scene("Cost, speed, predictability", "diagram", "The trade-offs", items=(
            "Speed\nworkflows are usually quicker",
            "Cost\nagents make more model calls",
            "Predictable\nworkflows win",
        ), said=(
            ("Here are the trade-offs. Speed first. A workflow usually finishes "
             "faster, because there's no thinking between steps."),
            ("Cost. An agent calls the model again and again in its loop, and every "
             "call costs something. [emphasis] A workflow might call it once, or "
             "never."),
            ("And predictability. A workflow does exactly what you wrote. That's "
             "why Anthropic suggests starting with the simplest solution, and adding "
             "complexity only when it clearly helps."))),
        Scene("", "subscribe", "Finding this useful?", said=(
            ("If this is helping, subscribe. Next, we draw an agent's tools, memory "
             "and planning, piece by piece."),)),
        Scene("Mixing both", "bullets", "You can mix them", items=(
            "A workflow around the agent",
            "The agent only does the fuzzy part",
            "A person approves before anything leaves",
        ), said=(
            ("You don't have to choose just one. A common pattern is a workflow "
             "around a small agent, so the agent only works inside clear edges."),
            ("The workflow does the fixed steps, like catching a form and saving it. "
             "The agent only does the fuzzy part, like researching an answer."),
            ("And a person approves the result before anything leaves. You get the "
             "flexibility where you need it, and control everywhere else. In n8n, "
             "that can be a normal workflow with an AI agent node in the middle."))),
        Scene("Five jobs", "steps", "Five jobs: which one?", items=(
            "Save every form entry: workflow",
            "Weekly sales report: workflow",
            "Reminder before a booking: workflow",
            "Research a new supplier: agent",
            "Sort a messy inbox: agent, with approval",
        ), said=(
            ("Let's practise. Saving every form entry to a sheet: same steps every "
             "time. Workflow."),
            ("A weekly report from a spreadsheet: fixed steps on a schedule. "
             "Workflow."),
            ("A reminder the day before a booking: read the calendar, send one "
             "message. Workflow again."),
            ("Researching a new supplier across many web pages: you can't write those "
             "steps in advance. That's agent work."),
            ("And sorting a messy inbox needs judgment, so an agent can help, with "
             "you approving any reply before it goes out. [break] Notice the pattern: "
             "when the steps are known, a workflow wins. When they're not, an agent "
             "is worth it."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one job from your week",
            "Workflow or agent? Why?",
        ), said=(
            "Pause the video and pick one job from your own week.",
            "Is it a workflow or an agent, and why? Resume when you've decided.")),
        Scene("", "quiz", "Fixed steps every time. Which one?", items=(
            "Agent", "Workflow", "Neither",
        ), answer=1, caption="Fixed steps: a workflow.", said=(
            "Last quiz. The steps are the same every time. Agent or workflow? Comment.",
            "B, a workflow. Simple, fast and predictable.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Workflow: steps written in advance",
            "Agent: the model picks the steps",
            "Start simple; add an agent when needed",
            "Mix them, with a person approving",
        ), said=(
            "Recap. A workflow follows steps you wrote in advance.",
            "An agent lets the model pick its steps, in a loop.",
            "Start with the simplest thing that works.",
            ("[happy] And mix them, with a person approving what matters. If you're "
             "unsure, build the workflow first. You can always add an agent later, "
             "once you know exactly where it helps. Next: tools, memory and planning."))),
    ),
)

LOCAL_EP1 = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 1),
    links=(("Hermes Agent (Nous Research)", "https://github.com/NousResearch/hermes-agent"),),
    ask="Where would you talk to your agent: the terminal, or a chat app? Comment below.",
    topic_said=("Today: what Hermes Agent is, what it can do, and why you might run "
                "the model behind it yourself."),
    topic_shown="What Hermes Agent is",
    payoff=payoff_scene(
        ("By the end, you'll know what Hermes Agent does, how it talks to a model, "
         "and whether running that model yourself makes sense for you."),
        "Hermes: Done. Three points…",
        items=(
            "Hermes Agent\nfrom Nous Research",
            "Any model\na provider or your own",
            "Tools, memory, skills\nit gets better with use",
        ),
        caption="open source, MIT licence"),
    hook=Scene("What you'll learn", "hook", "An agent you can run yourself", items=(
        "Hermes Agent\nfrom Nous Research",
        "Any model\na provider or your own",
        "Tools, memory, skills\nit gets better with use",
    ), code="You (in Telegram): Summarize today's notes\nHermes: Done. Three points…",
        caption="open source, MIT licence", said=(
            ("Here's the idea. Hermes Agent is an open-source agent. You talk to it in "
             "your terminal or in a chat app, and it uses tools to get things done."),
            "And the model behind it can be one you run yourself. Let's see how.",
            ("No install today: this is the mental model. The install, step by step "
             "from the official guide, has its own episode."))),
    needs=("nothing to install: the install has its own episode",),
    scenes=(
        Scene("Agent, not model", "bullets", "An agent, not a model", items=(
            "Made by Nous Research, MIT licence",
            "It talks to a model you choose",
            "A provider, or your own endpoint",
        ), said=(
            ("Hermes Agent is made by Nous Research. Its project page calls it the "
             "self-improving AI agent, and it's open source, under the MIT licence."),
            ("Remember last episode: a model answers, an agent acts. Hermes is the "
             "agent part. [break] It doesn't contain the model. It talks to the model "
             "you choose."),
            ("That can be a provider like OpenRouter or OpenAI, or a custom endpoint, "
             "which is how you point it at a model running on your own machine."))),
        Scene("", "quiz", "Is Hermes Agent a model?", items=(
            "Yes, a model", "No, an agent that uses a model", "It's a chat app",
        ), answer=1, caption="It's the agent; you choose the model.", said=(
            "Quick quiz. Is Hermes Agent a model? Comment your answer.",
            "It's B. It's an agent that talks to a model you choose.")),
        Scene("Tools", "bullets", "Tools", items=(
            "Dozens of built-in tools",
            "Runs commands in a place you choose",
            "MCP: connect outside tools",
        ), said=(
            ("An agent is only as useful as its tools. [chuckling] Like a chef with "
             "no knives. Hermes comes with dozens of built-in tools, grouped into "
             "toolsets, so you can switch on only the groups a job needs."),
            ("When it runs commands, you choose where: on your own computer, inside "
             "Docker, or over SSH on another machine, among others. Docker or a "
             "separate machine keeps its commands away from your own files."),
            ("And it supports MCP, so you can connect outside tools, the same standard "
             "plug we use with Claude in this channel."))),
        Scene("Memory and skills", "bullets", "Memory and skills", items=(
            "Memory it keeps up to date",
            "Search over past sessions",
            "Skills it writes after hard tasks",
        ), said=(
            ("Next, memory. Hermes keeps notes it updates over time, so it remembers "
             "things about your work between sessions, instead of starting from "
             "nothing each time you talk to it."),
            ("It can also search your past sessions, so something you solved last "
             "week isn't lost."),
            ("And this is the self-improving part. [excited] After a complex task, it "
             "can write a skill, a saved way of doing that task, and improve it each "
             "time it's used."))),
        Scene("", "subscribe", "Curious about Hermes?", said=(
            ("If you want to see Hermes running for real, subscribe. Later in this "
             "series, we set it up step by step."),)),
        Scene("Where you talk to it", "diagram", "Where you talk to it", items=(
            "Terminal\nits own text interface",
            "Messaging gateway\nTelegram, Discord, Slack…",
        ), said=(
            ("You can talk to Hermes in two main ways. The first is the terminal, "
             "with its own text interface, where you see each tool it uses as it "
             "works. It's the best place to start, because you see everything."),
            ("The second is a messaging gateway. The project lists Telegram, Discord, "
             "Slack, WhatsApp and Signal, so your agent can answer you in a chat app, "
             "even from your phone."))),
        Scene("", "pause", "Pause and look", items=(
            "Open the Hermes Agent page on GitHub",
            "Find the list of chat apps it supports",
        ), said=(
            "Pause the video and open the Hermes Agent page on GitHub, linked below.",
            "Find the list of chat apps it supports. Resume when you've found it.")),
        Scene("Why run the model locally", "bullets", "Why a local model?", items=(
            "Privacy: your messages stay with you",
            "Control: you pick the model",
            "Trade-off: you need the hardware",
        ), said=(
            ("So why point Hermes at a model you run yourself? Privacy first. Your "
             "messages and files go to your own machine, not to a provider."),
            ("Control second. You choose the model, and nobody changes it under you. "
             "If you like how it answers today, it answers the same way next month."),
            ("But the trade-off is real. You need a machine strong enough for the "
             "model, and you look after it. That's what the next episodes are "
             "about."))),
        Scene("", "quiz", "How does Hermes reach a model on your machine?", items=(
            "It can't", "A custom endpoint", "Only through Telegram",
        ), answer=1, caption="Point it at your own endpoint.", said=(
            "Last quiz. How does Hermes reach a model on your own machine? Comment.",
            "B, a custom endpoint. You point it at the model you're running.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Hermes: an open-source agent, not a model",
            "Tools, MCP, memory and skills",
            "Terminal or a chat app",
            "Next: install it, and its first tool",
        ), said=(
            "Recap. Hermes Agent is an open-source agent from Nous Research, not a model.",
            "It has tools, supports MCP, keeps memory and writes its own skills.",
            "You talk to it in the terminal, or in a chat app.",
            ("[happy] And it can use a model you run yourself. Next: we install it "
             "from the official guide, step by step, and give it its first tool."))),
    ),
)

CLAUDE_PLANNED = fill(CLAUDE_PLANNED, CLAUDE_EP2)
N8N_PLANNED = fill(N8N_PLANNED, N8N_EP2)
PROJECT_PLANNED = fill(PROJECT_PLANNED, PROJECT_EP2)
BUSINESS_PLANNED = fill(BUSINESS_PLANNED, BUSINESS_EP2)
AGENTS_PLANNED = fill(AGENTS_PLANNED, AGENTS_EP2)
LOCAL_PLANNED = fill(LOCAL_PLANNED, LOCAL_EP1)

# 2026-10-15: the episodes written one module each (tutorial_scripts/, the
# scheduled writer's, docs/academy-script-writer.md). Imported here, at the
# end of the assembly, so each module can import what it needs from above.
from . import tutorial_scripts, tutorial_series_ai

CLAUDE_PLANNED = from_scripts("claude", CLAUDE_PLANNED,
                              tutorial_scripts.for_series("claude"), (CLAUDE_EP0, MCP_EP1))
N8N_PLANNED = from_scripts("n8n", N8N_PLANNED, tutorial_scripts.for_series("n8n"),
                           (N8N_EP0, N8N_EP1))
PROJECT_PLANNED = from_scripts("project", PROJECT_PLANNED,
                               tutorial_scripts.for_series("project"),
                               (PROJECT_LEVEL0, PROJECT_EP1))
BUSINESS_PLANNED = from_scripts("business", BUSINESS_PLANNED,
                                tutorial_scripts.for_series("business"),
                                (BUSINESS_LEVEL0, BUSINESS_EP1))
AGENTS_PLANNED = from_scripts("agents", AGENTS_PLANNED,
                              tutorial_scripts.for_series("agents"), (AGENTS_EP1,))
LOCAL_PLANNED = from_scripts("local", LOCAL_PLANNED, tutorial_scripts.for_series("local"),
                             (LOCAL_LEVEL0,))
# Miro 2026-10-18, video system pack: three more series, planned in
# tutorial_series_ai (outlines, holds, thumbnail words), written like the rest.
EXPLAINED_PLANNED = from_scripts("explained", tutorial_series_ai.EXPLAINED_PLANNED,
                                 tutorial_scripts.for_series("explained"))
SECURITY_PLANNED = from_scripts("security", tutorial_series_ai.SECURITY_PLANNED,
                                tutorial_scripts.for_series("security"))
FUTURE_PLANNED = from_scripts("future", tutorial_series_ai.FUTURE_PLANNED,
                              tutorial_scripts.for_series("future"))
SCRIPT_SERIES = ("claude", "n8n", "project", "business", "agents", "local",
                 "explained", "security", "future")

# Every downloadable lab file, by the name it is served under at
# STORE_URL/free/<name> (site.render writes them; the tests import each).
LAB_FILES: dict[str, str] = {
    "n8n-first-run.json": N8N_LAB0_JSON + "\n",
    "n8n-city-info.json": N8N_LAB1_JSON + "\n",
    "n8n-every-day-at-nine.json": N8N_LAB2_JSON + "\n",
    "mcp-hello-server.py": MCP_SERVER_PY + "\n",
}


def with_lab_files(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    """`base` and the script modules' lab files; a name used twice is an error."""
    clash = sorted(set(base) & set(extra))
    if clash:
        raise ValueError(f"lab file(s) defined twice: {', '.join(clash)}")
    return {**base, **extra}


for _key in SCRIPT_SERIES:
    LAB_FILES = with_lab_files(LAB_FILES, tutorial_scripts.lab_files(_key))
del _key


def downloads_of(ep: Episode) -> tuple[str, ...]:
    """The lab files an episode's description links: its lab scenes' first,
    then its own extra downloads, each once."""
    names = [sc.resource for sc in ep.scenes if sc.resource] + list(ep.downloads)
    return tuple(dict.fromkeys(names))


# ------------------------------------------------- titles, tags, hashtags
#
# docs/academy-youtube-plan.md, Miro 2026-10-11 (from the YouTube Data API
# read of the top videos in our topics, .claude/competitors-2026-10-11.json):
# every title states its time, and the time is true -- the whole video's
# measured length rounded UP to the next minute, filled in after the render
# (tutorials.final_title); one formula per level; 20-25 tags in a fixed
# order; three hashtags, the product first.

TITLE_FORMULAS = {
    0: "What is {x}? Explained in {min} Minutes (for total beginners)",
    1: "Build Your First {x} in {min} Minutes (step by step)",
    2: "Learn {x} in {min} Minutes",
    3: "{x} Explained in {min} Minutes: {benefit}",
}
# The level 0/1 formulas' closing aside is dropped when the title would run
# past TITLE_SHOWN_MAX (Miro 2026-10-18): the aside is the only part of a
# formula title that carries no search term.
TITLE_ASIDES = {0: " (for total beginners)", 1: " (step by step)"}
TITLE_PATTERNS = {
    0: re.compile(r"^What is (?P<x>.+)\? Explained in (?P<min>\d+) Minutes"
                  r"(?: \(for total beginners\))?$"),
    1: re.compile(r"^Build Your First (?P<x>.+) in (?P<min>\d+) Minutes"
                  r"(?: \(step by step\))?$"),
    2: re.compile(r"^Learn (?P<x>.+) in (?P<min>\d+) Minutes$"),
    3: re.compile(r"^(?P<x>.+) Explained in (?P<min>\d+) Minutes: (?P<benefit>.+)$"),
}
MINUTES_IN_TITLE = re.compile(r"\b(\d+) Minutes\b")

# (series, episode) -> (hook, Level 3 benefit). Only scripted episodes are
# titled by formula; a planned one keeps its working title until scripted.
TITLE_HOOKS: dict[tuple[str, int], tuple[str, str]] = {
    ("claude", 0): ("Claude", ""),
    ("claude", 1): ("MCP Server for Claude", ""),
    ("claude", 2): ("Claude Code MCP Setup", ""),
    ("n8n", 0): ("n8n", ""),
    ("n8n", 1): ("n8n Workflow", ""),
    ("n8n", 2): ("n8n Trigger", ""),
    ("project", 0): ("a Claude + n8n AI Assistant", ""),
    ("project", 1): ("to Plan an AI Assistant with Claude + n8n", ""),
    ("project", 2): ("to Connect Claude to n8n with MCP", ""),
    ("business", 0): ("Small Business Automation", ""),
    ("business", 1): ("n8n Lead Auto-Reply", ""),
    ("business", 2): ("Booking Reminder Automation", ""),
    ("agents", 1): ("an AI Agent", ""),
    ("agents", 2): ("When to Use an AI Agent vs a Workflow", ""),
    ("local", 0): ("Local AI", ""),
    ("local", 1): ("the Hermes Agent Mental Model", ""),
}

# Explainers at a level whose formula promises a build (Level 1's "Build
# Your First ..."): they explain and build nothing, so they take the Learn
# formula (docs/academy-youtube-plan.md rule 2, Miro 2026-10-11).
EXPLAINER_FORMULA = 2
EXPLAINERS: frozenset[tuple[str, int]] = frozenset({
    ("project", 1),   # plans the project; the build starts at #2
    ("agents", 2),    # the agent-or-workflow decision
    ("local", 1),     # what Hermes Agent is and why run AI locally
})


# (series, episode) -> what the opener says "today we'll be learning about"
# (Miro, 2026-10-12). Every episode of every series but the channel welcome,
# planned ones too, in simple words; no numbers (the opener's only number is
# its measured minutes). Each replaces the episode's old "Today ..." topic
# line, which said the same thing: the topic is said once.
OPENER_TOPICS: dict[tuple[str, int], str] = {
    ("claude", 0): "what Claude is, and how to actually use it",
    ("claude", 1): "what MCP is, and how to connect your first MCP server to Claude",
    ("claude", 2): ("adding MCP servers to Claude Code, and what to do when one "
                    "won't connect"),
    ("claude", 3): "Claude skills, and how to write your very first one",
    ("claude", 4): "Claude Code subagents, little specialists that work for you",
    ("claude", 5): "Claude Code hooks, automatic checks that run while Claude works",
    ("claude", 6): "building your own Claude connector, safely",
    ("claude", 7): "skills, MCP, subagents and hooks, and when to use which",
    ("n8n", 0): "what automation is, what n8n is, and a quick tour of the editor",
    ("n8n", 1): "how to build your first n8n workflow, and how to manage it",
    ("n8n", 2): "the three kinds of trigger in n8n, and how to pick the right one",
    ("n8n", 3): "looking after your n8n workflows once they're running",
    ("n8n", 4): "handling errors in n8n, so one failed step doesn't stop everything",
    ("n8n", 5): "building your first AI agent in n8n, with tools and memory",
    ("n8n", 6): "webhooks and APIs in n8n, so you can connect almost any app",
    ("n8n", 7): "running n8n yourself with Docker, for free",
    ("n8n", 8): "an n8n AI agent with tools, memory and a human approval step",
    ("project", 0): ("the AI assistant we'll build with Claude and n8n, and the "
                     "words you need first"),
    ("project", 1): ("planning a real AI assistant with Claude and n8n, before we "
                     "build anything"),
    ("project", 2): ("connecting Claude to n8n with MCP, so Claude can use your "
                     "workflow as a tool"),
    ("project", 3): "writing your own MCP server and plugging it into an n8n workflow",
    ("project", 4): "adding a custom Claude skill to our assistant",
    ("project", 5): "testing our assistant, handling errors, and going live",
    ("project", 6): "adding email as a second way to reach our assistant",
    ("project", 7): "sharing our assistant with your team, safely",
    ("business", 0): ("what automation means for a small business, and what to "
                      "automate first"),
    ("business", 1): "replying to every website lead fast, automatically",
    ("business", 2): "booking reminders that send themselves",
    ("business", 3): "polite, automatic follow-ups for invoices and payment links",
    ("business", 4): "a weekly report sent to Slack from Google Sheets",
    ("business", 5): "scheduling social posts with AI captions that you approve",
    ("business", 6): "sorting your inbox with AI, with replies you approve",
    ("agents", 1): "what an AI agent actually is, with no hype",
    ("agents", 2): "when to use an AI agent, and when a simple workflow is better",
    ("agents", 3): "agentic AI, explained with a real workflow",
    ("agents", 4): "how AI agents use tools, memory and planning",
    ("agents", 5): "keeping AI agents safe, with approvals and guardrails",
    ("agents", 6): "testing an AI agent before you trust it",
    ("local", 0): "what running AI on your own computer really means",
    ("local", 1): "what Hermes Agent is, and why you might run AI yourself",
    ("local", 2): "installing Hermes Agent and giving it its first tool",
    ("local", 3): "NVIDIA's DGX Spark, and what its unified memory really means",
    ("local", 4): "running Hermes Agent on a DGX Spark with vLLM",
    ("local", 5): "the ways to serve a model on a DGX Spark, and which one to pick",
    ("local", 6): "giving Hermes Agent real tools with MCP",
    ("local", 7): "connecting Hermes Agent to Claude Desktop",
    ("local", 8): "turning Hermes Agent into a Telegram or Slack bot",
    ("local", 9): "connecting Hermes Agent to n8n",
    **tutorial_series_ai.OPENER_TOPICS,
}


# (series, episode) -> Episode.result: what the viewer has at the end, taken
# from the episode's own payoff line (2026-10-17, the video system's
# ResultTitle and EndCard). A noun phrase, sentence case, RESULT_WORDS_MAX
# words or fewer; it reads after "Here is the result:" and "You now have".
# None claims a test that the script does not show (Episode.verified).
RESULT_WORDS_MAX = 3
RESULTS: dict[tuple[str, int], str] = {
    ("claude", 0): "A usable draft",
    ("claude", 1): "Your MCP server",
    ("claude", 2): "A connected server",
    ("claude", 3): "A meeting-notes skill",
    ("claude", 4): "A proofreader subagent",
    ("claude", 5): "Two always-on hooks",
    ("claude", 6): "Custom connectors, explained",
    ("n8n", 0): "The n8n basics",
    ("n8n", 1): "Your first workflow",
    ("n8n", 2): "A daily workflow",
    ("n8n", 3): "Workflows under control",
    ("n8n", 4): "Retries and alerts",
    ("n8n", 5): "An AI agent",
    ("n8n", 6): "Your own API",
    ("project", 0): "The big picture",
    ("project", 1): "An assistant plan",
    ("project", 2): "Claude using n8n",
    ("project", 3): "An opening-hours tool",
    ("project", 4): "A house-rules skill",
    ("project", 5): "A live assistant",
    ("business", 0): "A three-question test",
    ("business", 1): "Instant lead replies",
    ("business", 2): "Automatic reminders",
    ("business", 3): "Invoice follow-ups",
    ("business", 4): "A weekly report",
    ("agents", 1): "A clear definition",
    ("agents", 2): "A simple rule",
    ("agents", 3): "Three clear words",
    ("agents", 4): "An agent diagram",
    ("local", 0): "A clear decision",
    ("local", 1): "Hermes, explained",
    ("local", 2): "Hermes, installed",
    ("local", 3): "Unified memory, explained",
    ("local", 4): "Hermes, fully local",
    ("local", 5): "The right engine",
}
# The badge of an episode that names no result: neutral and always true (the
# payoff's own heading), never a hook or a question borrowed as a result.
RESULT_BADGE = "What you'll have"
# First words kept capitalised inside a sentence (names, not articles).
_KEEP_CAPITAL = ("Claude", "Hermes", "Slack", "NVIDIA", "Python", "Docker")


def result_of(ep: Episode) -> str:
    """The one result string the opening, the end card, the title and the
    thumbnail share: Episode.result, else the payoff's result line, else the
    thumbnail's words on one line."""
    if ep.result:
        return ep.result
    if ep.payoff is not None and ep.payoff.code.strip():
        return ep.payoff.code.strip().split("\n")[0]
    return " ".join(ep.thumb_title.split())


def badge_of(ep: Episode) -> str:
    """The ResultTitle badge: the result when the episode names one, else
    RESULT_BADGE."""
    return ep.result or RESULT_BADGE


def lead_lower(text: str) -> str:
    """`text` as it reads mid-sentence: its first letter lowered, unless the
    first word is a name or an acronym ("MCP basics", "Hermes, installed")."""
    first = text.split(" ", 1)[0].rstrip(",")
    if not first or first in _KEEP_CAPITAL or (first[1:] and not first[1:].islower()):
        return text
    return text[0].lower() + text[1:]

# The opener's minutes, said as a word (Miro: "five"). Every lesson runs
# 4-10 minutes (Episode.max_seconds); a number outside this table is
# refused, never said as a guess.
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
                7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
                12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen"}


def number_word(n: int) -> str:
    if n not in NUMBER_WORDS:
        raise ValueError(f"no spoken word for {n} minutes")
    return NUMBER_WORDS[n]


# The spoken opening (Miro, 2026-10-17; docs/academy-video-system
# narration.md "A scripted sample", README "The reusable opening line"):
# "Here is the result: <result>." over the result, before the sting at 6 s;
# then the scope, what you need and where we start, over the scope line.
# No welcome, no "guys", no name, and no spoken minutes: the title carries
# the time (rule 2), measured; the opening claims no number.
RESULT_SAID = "Here is the result"
SCOPE_SAID = "In this lesson"
LAB_SAID = "There's a free lab file at the end, so you can try it yourself."
# Words a lesson's opening never says (tutorials.heard_refusal listens for them).
WELCOME_WORDS = ("welcome", "guys", "new channel")
NO_NEEDS = ("nothing", "no ")


def _and(items: list[str]) -> str:
    """"a, b and c"; with "; " between items that have commas of their own."""
    if len(items) == 1:
        return items[0]
    sep = "; " if any("," in x for x in items) else ", "
    last = "; and " if sep == "; " else " and "
    return f"{sep.join(items[:-1])}{last}{items[-1]}"


def needs_said(needs: tuple[str, ...]) -> str:
    """What you need (Episode.needs, written for the description), as said:
    "Nothing to install ..." as its own sentence, "for the lab: n8n" as "For
    the lab, you'll need n8n.", the rest as "You'll need a, b and c."."""
    out, rest, scoped = [], [], []
    for n in (x.strip() for x in needs):
        if n.lower().startswith(NO_NEEDS):
            out.append(f"{n[:1].upper()}{n[1:]}".replace(": ", ", ") + ".")
        elif n.lower().startswith("for ") and ": " in n:
            scope, _, what = n.partition(": ")
            scoped.append(f"{scope[:1].upper()}{scope[1:]}, you'll need {what}.")
        elif n:
            rest.append(n)
    if rest:
        out.append(f"You'll need {_and(rest)}.")
    return " ".join(out + scoped)


def opening_lines(ep: Episode) -> tuple[str, str]:
    """A lesson's spoken opening: (the line over the result, before the
    sting; the scope line after it). Built from the episode's own fields:
    its result, opener topic, needs, lab and first chapter."""
    first = f"{RESULT_SAID}: {lead_lower(ep.result)}." if ep.result else f"{RESULT_SAID}."
    parts = [f"{SCOPE_SAID}: {ep.opener_topic}."]
    if (needs := needs_said(ep.needs)):
        parts.append(needs)
    if any(sc.kind == "lab" for sc in ep.scenes):
        parts.append(LAB_SAID)
    start = next((sc.chapter for sc in ep.scenes if sc.chapter), "")
    if start:
        parts.append(f"Start with {lead_lower(start.rstrip('?.!'))}.")
    return first, " ".join(parts)


def opener_said(ep: Episode) -> str:
    """Every word the video opens with: the channel welcome's INTRO_SAID, or
    the lesson's opening_lines, in order."""
    if not ep.opener_topic:
        return INTRO_SAID
    return " ".join(opening_lines(ep))


# The opener's delivery (2026-10-12, "feelings"): the welcome excited, a
# lesson's result line confident. The cue is voiced only; opener_said stays
# the words, as shown and gated.
INTRO_CUE = "[excited]"
OPENER_CUE = "[confident]"


def opener_voiced(ep: Episode) -> str:
    """The first narrated line, with its delivery cue, for the voice: the
    welcome's INTRO_SAID, or a lesson's result line."""
    if not ep.opener_topic:
        return f"{INTRO_CUE} {INTRO_SAID}"
    return f"{OPENER_CUE} {opening_lines(ep)[0]}"


def topic_line(ep: Episode) -> str:
    """The narrated line after the opener, on the topic slide: none for a
    lesson since 2026-10-12 (the opener names the topic and its minutes; the
    old "Today ..." line would say the topic twice), the episode's own
    topic_said where it has no opener topic."""
    return "" if ep.opener_topic else ep.topic_said


# Rule 20 (growth plan, 2026-10-13; fix round 1): the recap's last spoken
# sentence names the next episode, by its opener_topic (a spoken phrase; a
# title_hook is a title fragment), or says the series is done. It REPLACES
# the recap's own "Next ..." sentence (or, where there is none, its closing
# sentence), so "next" is said once and the checklist keeps one spoken line
# per item it reveals. No playlist name is spoken.
NEXT_PREFIX = "Next time: "
LAST_SAID = "That's the whole series."
# Fix round 2 (2026-10-13): no subscribe clause (the ending of the time
# asked seconds later). SHORT_NEXT: the episodes whose next line names a shorter topic,
# because with the full one they break the 620-word cap
# (test_every_scripted_episode_has_a_sound_shape; measured: claude #1 625,
# n8n #0 623, n8n #1 622 with its own closing sentence kept, n8n #2 623).
# None = the next topic up to its first comma; a string = the topic to say,
# where the next topic has no comma (n8n #2 says the words of its own old
# line, "Next: managing your workflows once they're running."). None of
# Miro's lines is cut.
SHORT_NEXT: dict[tuple[str, int], str | None] = {
    ("claude", 1): None,
    ("n8n", 0): None,
    ("n8n", 1): None,
    ("n8n", 2): "managing your workflows",
}


def _next_said(later: Episode | None, *, short: bool | str = False) -> str:
    """The one wording of the next line, shared by titled() and next_line().
    `short`: True cuts the next topic at its first comma; a string is said
    in its place."""
    if later is None:
        return LAST_SAID
    if isinstance(short, str):
        topic = short
    else:
        topic = later.opener_topic.split(", ")[0] if short else later.opener_topic
    return f"{NEXT_PREFIX}{topic}."


def _short(series_key: str, number: int) -> bool | str:
    if (series_key, number) not in SHORT_NEXT:
        return False
    return SHORT_NEXT[(series_key, number)] or True


def _later(eps: tuple[Episode, ...], ep: Episode) -> Episode | None:
    """The episode after `ep`, scripted or planned (a planned one has its
    opener topic already): only a true last episode has none."""
    return next((e for e in eps if e.number > ep.number), None)


def next_line(series_key: str, ep: Episode) -> str:
    """Rule 20: what the recap ends on -- the next episode, or the end of the
    series (short where SHORT_NEXT says)."""
    s = series(series_key)
    if s is None:
        raise ValueError(f"no series {series_key!r}")
    return _next_said(_later(s.episodes, ep), short=_short(series_key, ep.number))


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
POINTS_AHEAD = re.compile(r"\bnext\b", re.IGNORECASE)


def _closing(said: str, line: str) -> str:
    """`said` closing on `line`: in place of its own pointing-ahead sentence
    ("Next ...", "See you in the next one.") and anything after it, else
    after its last sentence -- Miro's closing lines stay. Run twice, the same."""
    if plain(said).endswith(line):
        return said
    parts = SENTENCE_END.split(said.strip())
    at = next((i for i, x in enumerate(parts) if POINTS_AHEAD.search(plain(x))),
              len(parts))
    return " ".join([*parts[:at], line])


def _with_next(ep: Episode, line: str) -> Episode:
    """`ep` with its Recap's last spoken line closing on `line`."""
    scenes = []
    for sc in ep.scenes:
        if sc.chapter == "Recap" and sc.said:
            sc = replace(sc, said=(*sc.said[:-1], _closing(sc.said[-1], line)))
        scenes.append(sc)
    return replace(ep, scenes=tuple(scenes))


def titled(series_key: str, eps: tuple[Episode, ...], *,
           playlist: str = "") -> tuple[Episode, ...]:
    out = []
    for e in eps:
        hook = TITLE_HOOKS.get((series_key, e.number))
        if hook:
            e = replace(e, title_hook=hook[0], title_benefit=hook[1],
                        title_formula=(EXPLAINER_FORMULA
                                       if (series_key, e.number) in EXPLAINERS else None))
        e = replace(e, opener_topic=OPENER_TOPICS.get((series_key, e.number), ""),
                    result=e.result or RESULTS.get((series_key, e.number), ""))
        out.append(e)
    # Second pass, once every opener topic is known. SERIES is still being
    # built while this runs, so it reads the episodes it was given, never
    # series(). The last lesson closes the series only when it has a playlist.
    done = tuple(out)
    closed = []
    for e in done:
        later = _later(done, e)
        if e.scripted and (later is not None or playlist):
            e = _with_next(e, _next_said(later, short=_short(series_key, e.number)))
        closed.append(e)
    return tuple(closed)


def title_for(ep: Episode, minutes: int | None) -> str:
    """The episode's title: its level's formula with the measured minutes
    (None before a render: the working title, never a guessed number)."""
    if not ep.title_hook or minutes is None:
        return ep.title
    full = TITLE_FORMULAS[ep.formula_level].format(x=ep.title_hook, min=minutes,
                                                   benefit=ep.title_benefit)
    aside = TITLE_ASIDES.get(ep.formula_level, "")
    if len(full) > TITLE_SHOWN_MAX and aside and full.endswith(aside):
        return full[: -len(aside)]
    return full


# The end card's next lesson (Miro, 2026-10-17; docs/academy-video-system
# EndCard: "the exact title of an existing next video"). A formula title's
# minutes exist only once that episode is rendered and measured, so the card
# shows the title up to its time clause -- the words the viewer will see
# open the real title -- and never a guessed number (invariant 6).
CARD_TITLES = {
    0: "What is {x}?",
    1: "Build Your First {x}",
    2: "Learn {x}",
    3: "{x} Explained",
}


def card_title(ep: Episode) -> str:
    """`ep`'s title as the end card names it: the written title, or its
    level's formula up to the minutes (title_for(ep, n) starts with it)."""
    if not ep.title_hook:
        return ep.title
    return CARD_TITLES[ep.formula_level].format(x=ep.title_hook)


@dataclass(frozen=True)
class NextUp:
    """What the end card points to: the next scripted lesson, or the series
    playlist. Real data only; never an invented title."""
    kind: str                    # "episode" | "playlist"
    title: str
    why: str                     # one line on why it follows


def _first_sentence(text: str) -> str:
    return SENTENCE_END.split(text.strip())[0]


def next_up(series_key: str, ep: Episode) -> NextUp | None:
    """The next episode of the series when it is scripted (it publishes
    before or near this one), else the series playlist. The channel welcome
    points to the catalog's first scripted lesson. None when there is
    nothing real to name."""
    s = series(series_key)
    if s is None:
        raise ValueError(f"no series {series_key!r}")
    if not s.playlist and series_key == "channel":
        later = next((e for x in SERIES if x.playlist for e in x.episodes if e.scripted),
                     None)
    else:
        later = _later(s.episodes, ep)
    if later is not None and later.scripted:
        why = later.opener_topic[:1].upper() + later.opener_topic[1:] + "."
        return NextUp("episode", card_title(later), why)
    if s.playlist:
        return NextUp("playlist", s.playlist, _first_sentence(s.playlist_description))
    return None


# The head terms the top videos share (the plan's list), per series, first.
HEAD_TAGS: dict[str, tuple[str, ...]] = {
    "channel": ("ai", "ai automation", "claude", "n8n", "ai agents", "ai tools"),
    "claude": ("claude", "claude ai", "claude code", "anthropic", "claude code tutorial",
               "mcp", "model context protocol", "ai tools"),
    "n8n": ("n8n", "n8n tutorial", "n8n automation", "ai automation", "automation",
            "workflow", "no code", "n8n ai agent"),
    "project": ("claude", "n8n", "mcp", "model context protocol", "claude ai",
                "ai automation", "n8n tutorial", "claude code"),
    "business": ("automation", "ai automation", "process automation", "n8n", "no code",
                 "workflow", "ai tools", "n8n tutorial"),
    "agents": ("ai agents", "agentic ai", "ai", "llm", "ai automation", "automation",
               "n8n ai agent", "ai tools"),
    "local": ("nvidia", "llm", "ai", "ai agents", "machine learning", "ai tools",
              "open source ai", "local llm"),
}
FILLER_TAGS = ("guide", "walkthrough", "tutorial", "step by step", "explained",
               "ai for beginners", "how to")
LEVEL_WORDS = ("beginner", "intermediate", "advanced")

# Miro 2026-10-18, video system pack (docs/academy-video-system/
# tags-and-hashtags.md; docs/academy-youtube-plan.md rules 27-30): every upload
# carries the five channel tags, its series' set in the spec's exact phrases,
# then 3-5 tags specific to the lesson, under 500 characters as YouTube counts
# them. Supersedes rule 5's 20-25 head-term tags and its three hashtags.
CHANNEL_TAGS = ("atlas academy", "atlasacademyphp", "miro atlas academy",
                "practical ai tutorial", "ai automation for beginners")
_CLAUDE_SET = ("claude ai tutorial", "claude tutorial for beginners", "claude mcp tutorial",
               "mcp server tutorial", "model context protocol explained",
               "claude skills tutorial", "claude connectors", "how to use claude ai",
               "claude code tutorial", "claude desktop mcp setup")
SERIES_TAGS: dict[str, tuple[str, ...]] = {
    "channel": (),
    "claude": _CLAUDE_SET,
    "n8n": ("n8n tutorial for beginners", "n8n for beginners", "n8n beginner tutorial",
            "n8n ai agent tutorial", "n8n tutorial ai agent",
            "how to build ai agents in n8n", "n8n ai automation", "ai automation n8n",
            "n8n workflow tutorial", "n8n automation tutorial", "automation for beginners"),
    # The spec names no set for the Claude + n8n project: it takes the
    # Claude / MCP / Skills set, the one its lessons are built on (Miro to
    # confirm).
    "project": _CLAUDE_SET,
    "business": ("ai for small business", "small business automation",
                 "automate my business with ai", "ai customer service automation",
                 "n8n for small business", "ai booking reminders",
                 "ai lead reply automation", "business automation for beginners"),
    "agents": ("ai agents for beginners", "ai agents explained",
               "ai agents beginner tutorial", "how to build ai agents",
               "ai agent vs workflow", "agentic ai explained", "what is an ai agent",
               "ai agents tutorial", "build an ai agent step by step"),
    "local": ("local ai tutorial", "run ai locally", "hermes agent", "nvidia dgx spark",
              "local llm setup", "private ai assistant", "self hosted ai agent"),
    "explained": ("machine learning explained", "deep learning explained",
                  "generative ai explained", "ai vs machine learning vs deep learning",
                  "what is generative ai", "how do large language models work",
                  "llm explained simply", "ai for beginners",
                  "artificial intelligence explained", "rag vs fine tuning",
                  "what is mcp", "ai hallucination explained"),
    "security": ("ai security for small business", "prompt injection explained",
                 "prompt injection attack", "ai agent security",
                 "how to protect yourself from ai scams", "deepfake scam protection",
                 "voice cloning scam", "ai data privacy", "secure ai workflows n8n",
                 "api key security", "least privilege ai agents"),
    "future": ("future of ai", "future of ai for small business", "what to learn ai 2026",
               "ai skills to learn", "ai career advice", "local ai future",
               "ai agents future"),
}
# 3-5 per description, at its end, the strongest three first (they show above
# the title); a Short adds #shorts fourth, never first. All lower case, as the
# spec writes them. The channel welcome and the project take the closest
# spec sets (Miro to confirm).
SERIES_HASHTAGS: dict[str, tuple[str, ...]] = {
    "channel": ("#aiautomation", "#n8n", "#claudeai"),
    "claude": ("#claudeai", "#mcp", "#aiagents", "#anthropic", "#aitools"),
    "n8n": ("#n8n", "#aiautomation", "#aiagents", "#nocode", "#automation"),
    "project": ("#claudeai", "#mcp", "#n8n"),
    "business": ("#smallbusiness", "#aiautomation", "#n8n"),
    "agents": ("#aiagents", "#agenticai", "#artificialintelligence"),
    "local": ("#localai", "#llm", "#selfhosted"),
    "explained": ("#machinelearning", "#deeplearning", "#generativeai",
                  "#artificialintelligence", "#ai"),
    "security": ("#aisecurity", "#cybersecurity", "#promptinjection", "#deepfake",
                 "#privacy"),
    "future": ("#futureofai", "#artificialintelligence", "#aiagents"),
}
SHORTS_HASHTAG = "#shorts"

# Titles (Miro 2026-10-18, tags-and-hashtags.md): at most 60 characters, the
# tool name first. Read with the level formulas (rule 2), which open "What
# is", "Build Your First" or "Learn": the formula's subject -- the hook {x},
# after an article -- should open with the tool or subject term. Only the
# length holds a title (tutorials.title_refusal); tool_first() is advice.
TITLE_SHOWN_MAX = 60
TOOL_TERMS = ("Claude", "MCP", "n8n", "Hermes", "NVIDIA", "DGX Spark", "Local AI",
              "AI", "Agentic AI", "LLM", "Language Model", "Machine Learning",
              "Deep Learning", "Generative AI", "Tokens", "RAG", "Prompt Injection",
              "API Key", "Small Business")
_LEAD_WORDS = ("a ", "an ", "the ", "your ")


def tool_first(ep: Episode) -> bool:
    """Whether the formula's subject opens with a tool or subject term. An
    episode without a hook (the channel welcome) is not checked."""
    if not ep.title_hook:
        return True
    x = ep.title_hook
    for w in _LEAD_WORDS:
        if x.lower().startswith(w):
            x = x[len(w):]
            break
    return any(x.startswith(t) and (len(x) == len(t) or not x[len(t)].isalnum())
               for t in TOOL_TERMS)


# Chapter names are search terms ("Add the Wait for approval node", never
# "Step 3"): a bare step, part or section number is refused.
GENERIC_CHAPTER = re.compile(r"^\s*(step|part|section|chapter|lesson)\s*\d+\s*[.:]?\s*$",
                             re.IGNORECASE)


def generic_chapters(names: list[str] | tuple[str, ...]) -> list[str]:
    return [n for n in names if GENERIC_CHAPTER.match(n)]
HASHTAGS_MIN, HASHTAGS_MAX = 3, 5
LESSON_TAGS_MIN, LESSON_TAGS_MAX = 3, 5
TAGS_LIMIT = 500
_YEAR = re.compile(r"\b20\d\d\b")
# Lesson tags for scripted episodes whose own tags are all head or series
# terms (2026-10-18). Kept here, not on the Episode, so the script and its
# render key do not change: these are upload metadata only.
LESSON_TAGS: dict[tuple[str, int], tuple[str, ...]] = {
    ("n8n", 5): ("n8n ai agent node", "n8n chat model", "n8n agent tools",
                 "n8n agent memory"),
    ("project", 2): ("n8n mcp server trigger", "claude n8n mcp server", "n8n mcp"),
    ("project", 3): ("python mcp server", "custom mcp server", "call mcp server from n8n"),
    ("project", 4): ("claude code skills tutorial", "claude code skill.md tutorial",
                     "skill.md"),
    ("project", 5): ("n8n pin data", "n8n debug in editor", "n8n publish workflow",
                     "n8n error workflow", "n8n error trigger"),
    ("agents", 2): ("when to use an ai agent", "agent or workflow", "ai workflow cost"),
    ("agents", 4): ("ai agent tools", "ai agent memory", "ai agent planning"),
    ("local", 1): ("what is hermes agent", "hermes agent nous research setup",
                   "nous research", "local llm tool calling"),
    ("local", 3): ("dgx spark", "dgx spark gb10", "unified memory", "dgx spark memory"),
}


def tags_cost(tags: list[str] | tuple[str, ...]) -> int:
    """As YouTube counts against its 500 (YouTube Data API, videos resource,
    snippet.tags[], read 2026-10-18: "commas between items in the list count
    toward the limit", and a tag with a space counts as though wrapped in
    quotation marks, so "Foo Baz" is nine characters)."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def _generic_tags() -> set[str]:
    """Tags that are never lesson-specific: the channel and series sets, the
    head terms of every series, the filler and level words."""
    out = {*CHANNEL_TAGS, *LEVEL_WORDS, *FILLER_TAGS, "atlas academy"}
    for group in (*SERIES_TAGS.values(), *HEAD_TAGS.values(), CLAUDE_HEAD, N8N_HEAD,
                  PROJECT_HEAD, LOCAL_HEAD):
        out |= set(group)
    return {t.lower() for t in out}


def lesson_tags(series_key: str, ep: Episode) -> tuple[str, ...]:
    """The episode's own search phrases -- the tool, node or feature names --
    that fit after the channel and series tags: its most specific (last)
    phrases, up to LESSON_TAGS_MAX, in the episode's order. No year (it goes
    stale) and nothing the channel or series set already says."""
    generic = _generic_tags()
    fixed = [*CHANNEL_TAGS, *SERIES_TAGS.get(series_key, ())]
    seen = {t.lower() for t in fixed}
    own: list[str] = []
    for t in LESSON_TAGS.get((series_key, ep.number), ep.tags):
        low = t.lower()
        if low in generic or low in seen or _YEAR.search(t):
            continue
        seen.add(low)
        own.append(t)
    picked: list[str] = []
    for t in reversed(own):
        if len(picked) == LESSON_TAGS_MAX:
            break
        if tags_cost([*fixed, *picked, t]) <= TAGS_LIMIT:
            picked.append(t)
    return tuple(reversed(picked))


def full_tags(series_key: str, ep: Episode, year: int | None = None) -> tuple[str, ...]:
    """Every tag uploaded: the five channel tags, the series' set, then 3-5
    lesson tags, under 500 characters as YouTube counts them -- never cut
    silently (tags_refusal holds an episode whose lesson tags do not fit).
    `year` is no longer a tag (2026-10-18) and is ignored."""
    return (*CHANNEL_TAGS, *SERIES_TAGS.get(series_key, ()), *lesson_tags(series_key, ep))


def tags_refusal(series_key: str, ep: Episode) -> str | None:
    """None when the upload's tags follow the 2026-10-18 rules; otherwise why
    the episode is held."""
    n = len(lesson_tags(series_key, ep))
    if n < LESSON_TAGS_MIN:
        return (f"only {n} lesson-specific tag(s) fit after the channel and "
                f"{series_key} tags (needs {LESSON_TAGS_MIN}-{LESSON_TAGS_MAX}, under "
                f"{TAGS_LIMIT} characters)")
    if (cost := tags_cost(full_tags(series_key, ep))) > TAGS_LIMIT:
        return f"the tags are {cost} characters as YouTube counts them, over {TAGS_LIMIT}"
    return None


def hashtags_for(series_key: str, ep: Episode, *, short: bool = False
                 ) -> tuple[str, ...]:
    """The description's closing hashtags: the series' 3-5, strongest three
    first. A Short's: the strongest three, then #shorts fourth (never
    first). `ep` is kept for the callers; the set is the series'."""
    tags = SERIES_HASHTAGS.get(series_key, SERIES_HASHTAGS["channel"])
    if short:
        return (*tags[:3], SHORTS_HASHTAG)
    return tags[:HASHTAGS_MAX]


# The daily drafts go one per series (tutorials.lineup), each series up its
# own ladder; the local preview walks every Level 0 first (tutorials.order). Inside a
# series the episodes run 0 to advanced: levels never go down. Series 6,
# the Shorts, is not a playlist in this round: see SHORTS below.
# Each series' playlist title, read by its Series and by titled() (rule 20).
PLAYLISTS: dict[str, str] = {
    "claude": "Claude Mastery: MCP, Skills & Connectors",
    "n8n": "n8n Workflows from Zero",
    "project": "Build a Real Project: Claude + n8n",
    "business": "AI Automation for Small Business",
    "agents": "AI Agents Explained Simply",
    "local": "Local AI: Hermes Agent on NVIDIA DGX Spark",
    "explained": "AI Explained",
    "security": "AI Security",
    "future": "Where AI Is Going",
}
SERIES: tuple[Series, ...] = (
    Series(key="channel", playlist="", playlist_description="",
           mark="ATLAS Academy", episodes=(WELCOME,)),
    Series(key="claude", playlist=PLAYLISTS["claude"],
           playlist_description=("Claude from zero to advanced: the basics first, "
                                 "then MCP, skills, connectors, subagents and hooks, "
                                 f"step by step in plain words. More at {STORE_URL}"),
           mark="Claude Mastery", 
           episodes=titled("claude", (CLAUDE_EP0, MCP_EP1, *CLAUDE_PLANNED),
                           playlist=PLAYLISTS["claude"])),
    Series(key="n8n", playlist=PLAYLISTS["n8n"],
           playlist_description=("Learn n8n from the very beginning: what automation "
                                 "is, then real workflows, built step by step in plain "
                                 f"words. More at {STORE_URL}"),
           mark="n8n Workflows from Zero", 
           episodes=titled("n8n", (N8N_EP0, N8N_EP1, *N8N_PLANNED),
                           playlist=PLAYLISTS["n8n"])),
    Series(key="project", playlist=PLAYLISTS["project"],
           playlist_description=("One real AI assistant built with Claude and n8n, "
                                 f"from plan to live. More at {STORE_URL}"),
           mark="Claude + n8n Project", 
           episodes=titled("project", (PROJECT_LEVEL0, PROJECT_EP1, *PROJECT_PLANNED),
                           playlist=PLAYLISTS["project"])),
    Series(key="business", playlist=PLAYLISTS["business"],
           playlist_description=("Small, honest automations a small business can use, "
                                 f"with a human approving what matters. More at {STORE_URL}"),
           mark="AI for Small Business", 
           episodes=titled("business", (BUSINESS_LEVEL0, BUSINESS_EP1, *BUSINESS_PLANNED),
                           playlist=PLAYLISTS["business"])),
    Series(key="agents", playlist=PLAYLISTS["agents"],
           playlist_description=("What AI agents are, how they work and how to keep "
                                 f"them safe, in plain words. More at {STORE_URL}"),
           mark="AI Agents, Simply", 
           episodes=titled("agents", (AGENTS_EP1, *AGENTS_PLANNED),
                           playlist=PLAYLISTS["agents"])),
    Series(key="local", playlist=PLAYLISTS["local"],
           playlist_description=("Run an open-source agent on your own hardware: "
                                 "Hermes Agent, a DGX Spark and the local serving "
                                 f"stacks, step by step. More at {STORE_URL}"),
           mark="Local AI", 
           episodes=titled("local", (LOCAL_LEVEL0, *LOCAL_PLANNED),
                           playlist=PLAYLISTS["local"])),
    Series(key="explained", playlist=PLAYLISTS["explained"],
           playlist_description=("What AI, machine learning, language models, RAG and "
                                 "MCP really are, each shown on a real example, in "
                                 f"plain words. More at {STORE_URL}"),
           mark="AI Explained",
           episodes=titled("explained", EXPLAINED_PLANNED,
                           playlist=PLAYLISTS["explained"])),
    Series(key="security", playlist=PLAYLISTS["security"],
           playlist_description=("Stay safe with AI: every lesson shows a real risk on "
                                 "our own test setup, then the fix, verified. More at "
                                 f"{STORE_URL}"),
           mark="AI Security",
           episodes=titled("security", SECURITY_PLANNED,
                           playlist=PLAYLISTS["security"])),
    Series(key="future", playlist=PLAYLISTS["future"],
           playlist_description=("Where AI is going and what it means for you. "
                                 "Opinion, labelled as opinion, one a month. More at "
                                 f"{STORE_URL}"),
           mark="Where AI Is Going",
           episodes=titled("future", FUTURE_PLANNED, playlist=PLAYLISTS["future"])),
)


# ------------------------------------------- the one lab / live-build playlist
#
# Miro, 2026-10-22: every hands-on lab and live demo (an MCP server, an n8n
# workflow, an AI agent ...) stays in its own course playlist AND is added to
# one more playlist, grouped by course so a viewer sees which lab is whose.
# The list is reviewed code, never inferred: an explainer never belongs here
# (tests/test_academy_lab_playlist.py holds both). academy.add_to_lab_playlist
# (lab_playlist.py) adds them, in this order.
LAB_PLAYLIST = "Hands-On Labs & Live Builds"
LAB_COURSE_ORDER: tuple[str, ...] = ("claude", "n8n", "project", "business", "local",
                                     "security")
LABS: tuple[tuple[str, int], ...] = (
    ("claude", 1), ("claude", 2), ("claude", 3), ("claude", 4), ("claude", 5),
    ("claude", 6),
    ("n8n", 1), ("n8n", 2), ("n8n", 3), ("n8n", 4), ("n8n", 5), ("n8n", 6), ("n8n", 7),
    ("n8n", 8),
    ("project", 2), ("project", 3), ("project", 4), ("project", 5), ("project", 6),
    ("project", 7),
    ("business", 1), ("business", 2), ("business", 3), ("business", 4),
    ("business", 5), ("business", 6),
    ("local", 2), ("local", 4), ("local", 6), ("local", 7), ("local", 8), ("local", 9),
    ("security", 1),
)
# The short name each lab goes by in the lab playlist's description (the
# catalog titles are search strings, too long for a list).
LAB_SHORT_NAMES: dict[tuple[str, int], str] = {
    ("claude", 1): "Your first MCP server",
    ("claude", 2): "Add MCP servers to Claude Code",
    ("claude", 3): "Write your first skill",
    ("claude", 4): "Subagents",
    ("claude", 5): "Hooks",
    ("claude", 6): "A custom connector with OAuth",
    ("n8n", 1): "Your first workflow",
    ("n8n", 2): "Triggers",
    ("n8n", 3): "Managing workflows",
    ("n8n", 4): "Error handling",
    ("n8n", 5): "Your first AI agent",
    ("n8n", 6): "Webhooks and APIs",
    ("n8n", 7): "Self-host with Docker",
    ("n8n", 8): "AI agent lab with an approval step",
    ("project", 2): "Connect Claude to n8n with MCP",
    ("project", 3): "Write your own MCP server",
    ("project", 4): "Add a custom skill",
    ("project", 5): "Test, handle errors and go live",
    ("project", 6): "Add email as a second channel",
    ("project", 7): "Share it with your team",
    ("business", 1): "Lead auto-reply",
    ("business", 2): "Booking reminders",
    ("business", 3): "Invoice follow-ups",
    ("business", 4): "Weekly report to Slack",
    ("business", 5): "Social posts with AI captions",
    ("business", 6): "AI email triage",
    ("local", 2): "Install Hermes Agent",
    ("local", 4): "Hermes Agent on DGX Spark with vLLM",
    ("local", 6): "Tools with MCP",
    ("local", 7): "Hermes Agent + Claude Desktop",
    ("local", 8): "A Telegram or Slack bot",
    ("local", 9): "Hermes Agent + n8n",
    ("security", 1): "Prompt injection, live",
}
_LAB_SET = frozenset(LABS)


def is_lab(series_key: str, episode: int) -> bool:
    return (series_key, episode) in _LAB_SET


def lab_playlist_description() -> str:
    """One intro line, then one line per course: its playlist title, then
    its labs by number and short name. Under YouTube's 5000 characters."""
    lines = [("Every hands-on lab and live build on ATLAS Academy, grouped by course. "
              "Each video is also in its own course playlist."), ""]
    for key in LAB_COURSE_ORDER:
        eps = ", ".join(f"#{n} {LAB_SHORT_NAMES[(k, n)]}" for k, n in LABS if k == key)
        if eps:
            lines.append(f"{PLAYLISTS[key]} — {eps}")
    lines += ["", f"More at {STORE_URL}"]
    return "\n".join(lines)


# --------------------------------------------------------- Series 6: Shorts
#
# Miro, 2026-10-10: one vertical 9:16 Short (30-60 s) per long episode, a
# single quick tip cut from it, linking to the full video. PLAN ONLY: the
# catalog entry and the cut (tutorials.short_window / short_argv); no action
# posts a Short yet. When one does, it goes through the same Slack review
# card, then the clips route (Postiz / YouTube) the reels already use.

@dataclass(frozen=True)
class Short:
    series: str
    episode: int
    chapter: str                 # the long episode's chapter the tip is cut from
    title: str                   # <= 100 characters, ends with #Shorts


SHORTS: tuple[Short, ...] = (
    Short("claude", 1, "A tiny MCP server",
          "A complete MCP server in a few lines of Python #Shorts"),
    Short("n8n", 1, "Manage it",
          "How to manage an n8n workflow after you build it #Shorts"),
)
# Miro, 2026-10-17 (the video system, episode-structure.md): a Short runs
# 20-40 s, its closing card included (tutorial_short.CUT_MAX).
SHORT_MIN, SHORT_MAX = 20.0, 40.0



def series(key: str) -> Series | None:
    return next((s for s in SERIES if s.key == key), None)


def held(series_key: str, ep: Episode) -> str | None:
    """Why `ep` may not be rendered or posted yet, or None (Miro 2026-10-18,
    video system pack). An episode in tutorial_series_ai.HOLDS needs a
    recording or Miro himself: a RECORDING hold lifts once its script shows a
    demo scene and every demo's recording is on disk; a MIRO hold lifts only
    when the entry is taken out of HOLDS. The writer skips held episodes
    (docs/academy-script-writer.md); render, the lineup and the runway hold
    them. Blocking (a stat per demo)."""
    from .tutorial_demos import video_path
    hold = tutorial_series_ai.HOLDS.get((series_key, ep.number))
    if hold is None:
        return None
    kind, why = hold
    if kind == tutorial_series_ai.RECORDING and ep.scripted:
        keys = [sc.demo for sc in ep.scenes if sc.kind == "demo" and sc.demo]
        if keys and all(video_path(k).is_file() for k in keys):
            return None
    return f"held: {series_key} #{ep.number} {why}"


def words(ep: Episode) -> int:
    """Words in the teaching body (the scenes; not the intro, topic or hook)."""
    return sum(len(plain(line).split()) for sc in ep.scenes for line in sc.said)


def chapters_of(ep: Episode) -> list[int]:
    """For each scene, the index of the chapter it belongs to (a beat joins
    the chapter before it)."""
    out, n = [], -1
    for sc in ep.scenes:
        if sc.chapter or n < 0:
            n += 1
        out.append(n)
    return out


def claim_refusal(ep: Episode, body_seconds: float) -> str | None:
    """Miro, 2026-10-10: a topic line may say "under five minutes" only while
    the episode's teaching body -- everything but the intro, the topic line,
    the hook and the ending -- measures under 300 s. Only a line that is
    said (topic_line): a lesson's topic_said is not spoken since 2026-10-12,
    so it claims nothing (2026-10-18)."""
    if UNDER_FIVE.search(topic_line(ep)) and body_seconds >= UNDER_FIVE_SECONDS:
        return (f"the topic line says \"under five minutes\" but the teaching runs "
                f"{body_seconds:.0f} s (must be under {UNDER_FIVE_SECONDS:.0f} s)")
    return None
