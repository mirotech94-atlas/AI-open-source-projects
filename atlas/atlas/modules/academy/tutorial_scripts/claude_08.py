"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/claude_08.md

Claude #8: CLAUDE.md, giving Claude Code a memory of your project.

Drafted 2026-11-24. New topic (the series plan was used up): the series
promise is Claude from zero to advanced; #5 hooks and #7 the four extension
tools already point at CLAUDE.md, and the docs cover it fully. No live demo:
the picture is bullets, steps, one CLAUDE.md on screen, two quizzes and a
pause. Level 3: levels never go down.

Every product fact was read on the official Claude Code docs on 2026-11-24:

  code.claude.com/docs/en/memory
      ("Each Claude Code session begins with a fresh context window. CLAUDE.md
      files and auto memory carry knowledge across sessions"; "CLAUDE.md files
      are markdown files that give Claude persistent instructions ... Claude
      reads them at the start of every session"; locations: User
      instructions ~/.claude/CLAUDE.md "Personal preferences for all
      projects", Project instructions ./CLAUDE.md or ./.claude/CLAUDE.md
      "Team-shared instructions for the project", "Team members via source
      control", Local instructions ./CLAUDE.local.md "Personal project-
      specific preferences; add to .gitignore"; "Run /init to generate a
      starting CLAUDE.md automatically. Claude analyzes your codebase and
      creates a file with build commands, test instructions, and project
      conventions it discovers. If a CLAUDE.md already exists, /init suggests
      improvements rather than overwriting it"; "Claude treats CLAUDE.md files
      as context, not enforced configuration"; "Use 2-space indentation"
      instead of "Format code properly", "Run npm test before committing"
      instead of "Test your changes", "API handlers live in src/api/handlers/"
      instead of "Keep files organized"; "target under 200 lines per CLAUDE.md
      file. Longer files consume more context and reduce adherence"; "group
      related instructions under markdown headers and bullets"; "if two
      instructions contradict each other, Claude may pick one arbitrarily";
      "@path/to/import" syntax, "Imported files are expanded and loaded into
      context alongside the CLAUDE.md that references them", imports "don't
      reduce its context cost"; when to add: "Claude makes the same mistake a
      second time", "A code review catches something Claude should have known
      about this codebase", "You type the same correction or clarification
      into chat that you typed last session", "A new teammate would need the
      same context to be productive"; "To block an action regardless of what
      Claude decides, use a PreToolUse hook instead"; auto memory: "notes
      Claude writes itself based on your corrections and preferences", "Run
      /memory to browse and open memory files from within a session")
  code.claude.com/docs/en/features-overview
      (CLAUDE.md "Persistent context loaded every conversation", "Project
      conventions, 'always do X' rules"; "Keep CLAUDE.md under 200 lines")

The CLAUDE.md on screen is our own example, made only of the docs' three
example instructions and headers.
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    payoff_scene,
    plan,
    scripted,
)

PLANNED = plan(
    8, 3, "claude-md-memory",
    "Claude Code Memory: Write a CLAUDE.md That Claude Actually Follows",
    "Claude\nremembers",
    ("What a CLAUDE.md file is, where it lives, how to write instructions Claude "
     "follows, and when a hook is the better tool."),
    ("claude.md file", "claude code memory", "claude.md best practices",
     "claude code project rules", "claude code init command"),
    ("#claudeai", "#mcp", "#aiagents"),
    ("Why a new session starts blank, and what CLAUDE.md fixes",
     "Where it lives: user, project and local files",
     "Run /init, then edit the file by hand",
     "Write instructions Claude can verify, and keep the file short",
     "Imports with @path, and path-specific rules",
     "A request, not a lock: when to use a hook instead",
     "Recap and a quiz: which file is only for you?"))

SAMPLE = """# Project notes

## Code style
- Use 2-space indentation

## Commands
- Run npm test before committing

## Layout
- API handlers live in src/api/handlers/"""

PICTURE = (
    "A new session\nstarts blank",
    "CLAUDE.md is\nread first",
    "Claude knows\nyour project",
)

EPISODE: Episode = scripted(
    PLANNED,
    result="A memory file",
    title_hook="Claude Code Memory",
    title_benefit="CLAUDE.md Rules",
    links=(("How Claude remembers your project", "https://code.claude.com/docs/en/memory"),
           ("Extend Claude Code", "https://code.claude.com/docs/en/features-overview"),
           ("Hooks guide", "https://code.claude.com/docs/en/hooks-guide")),
    ask="What's the one thing you keep telling Claude twice? Tell me below.",
    topic_said=("Today: CLAUDE.md, the file that gives Claude Code a memory of your "
                "project."),
    topic_shown="CLAUDE.md",
    payoff=payoff_scene(
        ("By the end, you'll have a short project file that Claude reads at the start "
         "of every session."),
        "CLAUDE.md · Run npm test before committing",
        items=PICTURE,
        caption="say it once, in a file"),
    hook=Scene("What you'll learn", "hook", "Say it once", items=PICTURE,
               code=SAMPLE, caption="say it once, in a file", said=(
                   ("[sighing] Claude gets your test command wrong. You correct it. "
                    "Next session, the same mistake."),
                   ("Every session starts fresh, so Claude forgets. A CLAUDE.md file "
                    "fixes that."),
                   ("All you need is Claude Code and a project folder."))),
    needs=("Claude Code", "a project folder"),
    scenes=(
        Scene("What it is", "bullets", "What CLAUDE.md is", items=(
            "A markdown file you write",
            "Read at the start of every session",
            "Context, not a lock",
        ), said=(
            ("A CLAUDE.md is a plain markdown file. You write down what Claude "
             "should know about your project."),
            ("Claude reads it at the start of every session. So a brand new "
             "conversation still begins with your rules."),
            ("[emphasis] One thing to remember. It's context, not enforced "
             "settings. We'll come back to that."))),
        Scene("Where it lives", "bullets", "Where it lives", items=(
            "~/.claude/CLAUDE.md: all your projects",
            "./CLAUDE.md: the project, shared",
            "./CLAUDE.local.md: just you",
        ), said=(
            ("Three places matter. The one in your home folder's dot claude "
             "directory holds personal preferences for every project."),
            ("A CLAUDE.md in the project holds what the whole team needs. Commit "
             "it, and everyone shares it."),
            ("And CLAUDE.local.md is personal and just for this project. Add it to "
             "your dot gitignore so it isn't committed."))),
        Scene("", "quiz", "A note only for you, only in this project. Which file?",
              items=("~/.claude/CLAUDE.md", "./CLAUDE.md", "./CLAUDE.local.md"),
              answer=2, caption="CLAUDE.local.md: personal, and per project.", said=(
                  ("Quick quiz. A note only for you, only in this project. Which "
                   "file? Comment your answer."),
                  "C, CLAUDE.local.md. It's personal, for this project, and out of git.")),
        Scene("A small example", "code", "CLAUDE.md", code=SAMPLE,
              marks=((2, 4), (5, 7), (8, 10)),
              caption="short, specific lines", said=(
                  ("Here's a small one. A line about style: use two-space "
                   "indentation."),
                  "A line about commands: run npm test before committing.",
                  ("And a line about layout: where the API handlers live. Each one "
                   "is concrete, so Claude can check it."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you'd like the rest of these Claude lessons, subscribe.",)),
        Scene("Write it well", "bullets", "Write it so Claude follows it", items=(
            "Concrete: 'Use 2-space indentation'",
            "Short: under 200 lines",
            "Organized: headers and bullets",
            "No contradictions",
        ), said=(
            ("Be concrete. Format code properly is vague. Use two-space "
             "indentation is something Claude can follow."),
            "Keep it short. Aim for under two hundred lines, because long files cost context.",
            "Group related lines under headers and bullets. It's easier to follow than a wall of text.",
            ("[curious] And don't contradict yourself. If two lines disagree, "
             "Claude may pick one at random."))),
        Scene("Start with /init", "steps", "Start with /init", items=(
            "Type /init in your project",
            "Claude writes a first CLAUDE.md",
            "Edit it by hand",
            "@path imports other files",
        ), said=(
            ("You don't start from a blank page. Type slash init, and Claude looks "
             "at your project."),
            ("It writes a first file, with the build commands, test instructions and "
             "conventions it found."),
            ("Then edit it. Add what Claude couldn't guess, and cut what you don't "
             "need."),
            ("You can also pull in another file with an at sign and its path. Just "
             "know that imported files still load, so they cost context too."))),
        Scene("", "pause", "Pause and try this", items=(
            "Run /init in a project",
            "Add one correction you keep repeating",
        ), said=(
            "Pause the video and run slash init in a project you know.",
            "Then add one correction you're tired of repeating, and resume.")),
        Scene("When to add a line", "bullets", "When to add a line", items=(
            "Claude makes the same mistake twice",
            "A review catches something it should've known",
            "A new teammate would need it",
        ), said=(
            ("A good time to add a line? When Claude makes the same mistake a second "
             "time."),
            ("Or when a code review catches something Claude should have known about "
             "your codebase."),
            ("Or when a new teammate would need the same context to get going. "
             "[relaxed] If you'd explain it to a person, write it down."))),
        Scene("A request, not a lock", "bullets", "A request, not a lock", items=(
            "CLAUDE.md is context",
            "Must always hold? Use a hook",
            "/memory: notes Claude takes itself",
        ), said=(
            ("Now, that thing I said we'd come back to. Claude treats CLAUDE.md as "
             "context, not enforced rules."),
            ("So if something must never happen, use a PreToolUse hook. It blocks "
             "the action whatever Claude decides."),
            ("And Claude can take its own notes, called auto memory. Type slash "
             "memory to browse them."))),
        Scene("", "quiz", "A rule must hold every single time. Best fix?",
              items=("A longer CLAUDE.md", "A PreToolUse hook", "Say it louder"),
              answer=1, caption="A hook blocks. A file only asks.", said=(
                  ("Last quiz. A rule has to hold every single time. What's the best "
                   "fix? Comment."),
                  ("B, a PreToolUse hook. It blocks the action. A longer file only "
                   "asks, and a louder one still asks."))),
        Scene("Recap", "checklist", "Recap", items=(
            "CLAUDE.md: read every session",
            "User · project · local files",
            "Concrete, short, no clashes",
            "Must-hold rules → a hook",
        ), said=(
            "Recap. CLAUDE.md is a file Claude reads at the start of every session.",
            "It can be yours, the project's, or yours just for one project.",
            "Keep the lines concrete and short, and never let them clash.",
            ("[happy] And a rule that must always hold belongs in a hook. Say it "
             "once, in the right place."))),
    ),
)
