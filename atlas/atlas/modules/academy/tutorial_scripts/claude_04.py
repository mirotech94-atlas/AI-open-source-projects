"""Claude #4: Claude Code subagents, little specialists that work for you.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is bullets, steps,
one subagent file on screen, a diagram, two quizzes and a pause.

Every product fact was read on the official Claude Code docs on 2026-10-17:

  code.claude.com/docs/en/sub-agents (and its .md form)
      ("Each subagent runs in its own context window with a custom system
      prompt, specific tool access, and independent permissions"; it "sends
      its own requests, which count toward the same usage limits as your
      main conversation"; locations: .claude/agents/ "Current project",
      ~/.claude/agents/ "All your projects", a plugin's agents/ directory;
      "YAML frontmatter between --- markers ... and write its system prompt
      as Markdown after the closing ---. Only name and description are
      required"; tools: "Inherits every tool available to subagents if
      omitted", "a comma-separated string such as Read, Grep, Bash"; model:
      sonnet, opus, haiku ... or inherit; "Claude automatically delegates
      tasks based on the task description in your request, the description
      field in subagent configurations, and current context"; "include
      phrases like 'use proactively'"; natural language: "name the subagent
      in your prompt; Claude decides whether to delegate"; "@-mention:
      guarantees the subagent runs for one task"; "Foreground subagents block
      the main conversation until complete"; "Background subagents run
      concurrently while you continue working"; "For independent
      investigations, spawn multiple subagents to work simultaneously";
      built-in Explore: "search or understand a codebase without making
      changes"; "A name but no description: Claude Code skips the file and
      writes the reason to the debug log"; "To see the debug log, run Claude
      Code with --debug"; edits are detected "within a few seconds ... no
      restart needed", except a new ~/.claude/agents/ folder made during a
      session: "restart Claude Code and try again")

The proofreader on screen is our own example, built only from fields that
page documents (its code-reviewer example uses the same four).
"""
from __future__ import annotations

from ..tutorial_series import (
    CLAUDE_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

AGENT_MD = """---
name: proofreader
description: Checks customer-facing text for typos, tone and promises.
  Use proactively after emails or web copy are edited.
tools: Read, Grep, Glob
model: sonnet
---

You are a careful proofreader. List every typo, every unclear
sentence, and any promise about prices or dates, with a fix for
each. Never edit the files yourself."""

PICTURE = (
    "You ask\n'check the new page'",
    "Proofreader\nits own fresh context",
    "A short report\nback in your chat",
)

EPISODE: Episode = scripted(
    next(e for e in CLAUDE_PLANNED if e.number == 4),
    title_hook="Claude Code Subagents",
    links=(("Claude Code subagents", "https://code.claude.com/docs/en/sub-agents"),),
    ask="What specialist would you hire first: a proofreader, a tester, a researcher?",
    topic_said=("Today: Claude Code subagents, specialists with their own context, "
                "and how to write your first one."),
    topic_shown="Your first subagent",
    payoff=payoff_scene(
        ("By the end, you'll have a proofreader that checks your customer emails and "
         "web pages, in its own space, and only reports back."),
        "Proofreader: 3 typos, 1 unclear line, 0 risky promises",
        items=PICTURE,
        caption="a specialist, one file"),
    hook=Scene("What you'll learn", "hook", "A specialist in one file", items=PICTURE,
               code=AGENT_MD, caption="a specialist, one file", said=(
                   ("Long chats get crowded. A subagent takes one job off to the side, "
                    "and hands you the answer, nothing else."),
                   ("[confident] All you need is Claude Code and a project folder. One "
                    "small file does the rest."))),
    needs=("Claude Code", "a project folder"),
    scenes=(
        Scene("What a subagent is", "bullets", "What a subagent is", items=(
            "Its own context window",
            "Its own instructions and tools",
            "It reports back; your chat stays tidy",
        ), said=(
            ("A subagent is a helper with its own context window. It starts fresh, "
             "away from your main conversation."),
            ("It gets its own instructions, its own tools and its own permissions."
             " So a proofreader can be allowed to read, and nothing else."),
            ("And when it's done, you get its answer, not every file it read. [relaxed] "
             "Your main chat stays nice and tidy."))),
        Scene("Where they live", "bullets", "Where they live", items=(
            "Project: .claude/agents/",
            "Personal: ~/.claude/agents/",
            "Plugins can bring their own",
        ), said=(
            ("Each subagent is one markdown file. Put it in dot claude, agents, inside "
             "a project, and it belongs to that project."),
            ("Put it in the agents folder in your home dot claude folder, and it works "
             "in all your projects."),
            "And plugins can come with subagents of their own.")),
        Scene("The file, line by line", "code", "proofreader.md", code=AGENT_MD,
              marks=((1, 4), (4, 5), (5, 6), (8, 11)),
              caption=".claude/agents/proofreader.md", said=(
                  ("Here's our proofreader. Name and description are the only two "
                   "fields you must have. [emphasis] The description tells Claude when "
                   "to hand a job over."),
                  ("Tools lists what it may use. Leave it out, and it gets every tool. "
                   "Ours can only read and search."),
                  "Model picks which Claude it runs on.",
                  ("Below the dashes is its system prompt: who it is, and what it must "
                   "never do. Ours never edits a file."))),
        Scene("", "quiz", "You leave out tools. The subagent gets…", items=(
            "No tools at all", "Every tool available to subagents", "Only Read",
        ), answer=1, caption="Omit tools and it inherits them all.", said=(
            ("Quick quiz. You write a subagent with no tools line at all. What can it "
             "use? Comment your answer."),
            "B, every tool. That's why we list only the ones it needs.")),
        Scene("", "subscribe", "Still with me?", said=(
            "If you're enjoying these Claude lessons, subscribe.",)),
        Scene("Using it", "steps", "Three ways to use it", items=(
            "Do nothing: Claude picks it from the description",
            "Ask by name: 'use the proofreader'",
            "@-mention it to make sure it runs",
        ), said=(
            ("There are three ways to use it. First, do nothing. Claude reads the "
             "description and hands the job over when it fits."),
            ("Second, ask for it by name, like: use the proofreader on the new page. "
             "Claude still decides."
             " Want it to step in on its own more often? Put use proactively in its description, like we did."),
            ("Third, at-mention it. That guarantees it runs, for that one task."))),
        Scene("", "pause", "Pause and try this", items=(
            "Save proofreader.md in .claude/agents/",
            "Ask Claude to use it on one file",
        ), said=(
            "Pause the video, and save the proofreader file in your project's agents folder.",
            "Then ask Claude to use it on one file. Resume when it reports back.")),
        Scene("Side by side", "diagram", "Several at once", items=(
            "Foreground\nyou wait for it",
            "Background\nyou keep working",
            "Explore\nbuilt in: searches, changes nothing",
        ), said=(
            ("Subagents can run in two ways. In the foreground, your chat waits until "
             "it's done."
             " You can also send a running one to the background with Control B."),
            ("In the background, it works while you carry on. For separate questions, "
             "Claude can run several at the same time. [excited] A little team."),
            ("And some come built in, like Explore, which searches your code without "
             "changing anything. Each one uses your plan's limits, so don't overdo it."))),
        Scene("Not working?", "bullets", "Not working?", items=(
            "No description? The file is skipped",
            "Check why: claude --debug",
            "New personal folder? Restart once",
        ), said=(
            ("[sighing] Subagent not showing up? First, check it has a description. "
             "Without one, the file is skipped."),
            "Start Claude with the debug flag, and the log says why a file was skipped.",
            ("And if you just made the personal agents folder for the first time, "
             "restart Claude Code once. After that, edits are picked up by themselves."))),
        Scene("", "quiz", "You must be sure the proofreader runs. You…", items=(
            "Hope Claude picks it", "@-mention it", "Restart Claude",
        ), answer=1, caption="An @-mention guarantees it runs.", said=(
            ("Last quiz. You need the proofreader to run on this task, for sure. What "
             "do you do? Comment."),
            "B, at-mention it. Asking by name lets Claude decide; a mention doesn't.")),
        Scene("Recap", "checklist", "Recap", items=(
            "A subagent has its own context",
            "One file in .claude/agents/",
            "Name and description required",
            "Picked by description, by name, or @-mention",
        ), said=(
            "Recap. A subagent works in its own context, and reports back.",
            "It's one markdown file in your agents folder.",
            "Only the name and the description are required, but give it just the tools it needs.",
            ("[happy] And Claude uses it from its description, when you name it, or when "
             "you mention it. A specialist, in one file."))),
    ),
)
