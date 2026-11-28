"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/claude_07.md

Claude #7: Skills vs MCP vs subagents vs hooks, when to use which.

Drafted 2026-11-24. No live demo: the picture is bullets, steps, a diagram,
two quizzes and a pause. Nothing here is built on screen; it is a way to choose.

Every product fact was read on the official Claude Code docs on 2026-11-24:

  code.claude.com/docs/en/features-overview
      ("Understand when to use CLAUDE.md, Skills, subagents, hooks, MCP, and
      plugins"; MCP "Connect to external services", "External data or
      actions"; Skill "Instructions, knowledge, and workflows Claude can use",
      "Reusable content, reference docs, repeatable tasks"; Subagent
      "Isolated execution context that returns summarized results", "Context
      isolation, parallel tasks, specialized workers"; Hook "Automation that
      must run on every matching event"; "You paste the same playbook or
      multi-step procedure into chat for the third time" -> a skill; "A side
      task floods your conversation with output you won't reference again"
      -> a subagent; "You want something to happen every time without
      asking" -> a hook; "You keep copying data from a browser tab Claude
      can't see" -> an MCP server; Hook vs Skill: "Fires on its event without
      Claude deciding" against "Claude interprets the instructions; outcome
      can vary"; "An instruction like 'never edit .env' in CLAUDE.md or a
      skill is a request, not a guarantee. A PreToolUse hook that blocks the
      edit is enforcement"; MCP vs Skill: "MCP gives Claude purpose-built
      tools for an external system", "Skills give Claude knowledge about how
      to use those tools effectively"; Skill + MCP "MCP provides the
      connection; a skill teaches Claude how to use it well"; context cost:
      skills "descriptions every request", hooks "Zero, unless hook returns
      additional context")

The sibling episodes (#3 skills, #4 subagents, #5 hooks, #1 and #6 MCP) carry
the detailed facts and their own docstring citations.
"""
from __future__ import annotations

from ..tutorial_series import (
    CLAUDE_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

PICTURE = (
    "A problem\nin your work",
    "Pick the tool\nthat fits it",
    "Claude does\nthe right thing",
)

EPISODE: Episode = scripted(
    next(e for e in CLAUDE_PLANNED if e.number == 7),
    result="The right tool",
    title_hook="Claude Extensions",
    title_benefit="Which to Use",
    links=(("Extend Claude Code", "https://code.claude.com/docs/en/features-overview"),
           ("Skills", "https://code.claude.com/docs/en/skills"),
           ("Subagents", "https://code.claude.com/docs/en/sub-agents"),
           ("Hooks", "https://code.claude.com/docs/en/hooks-guide"),
           ("MCP", "https://code.claude.com/docs/en/mcp")),
    ask="Which job in your week would you hand to Claude first? Tell me below.",
    topic_said=("Today: four ways to extend Claude Code, and a simple way to pick the "
                "right one."),
    topic_shown="Skills, MCP, subagents, hooks",
    payoff=payoff_scene(
        ("By the end, you'll look at a problem and know which of the four fixes it."),
        "Same playbook, third time → a skill",
        items=PICTURE,
        caption="start from the problem, not the tool"),
    hook=Scene("What you'll learn", "hook", "Which one, when?", items=PICTURE,
               caption="start from the problem, not the tool", said=(
                   ("Skills, MCP, subagents, hooks. [curious] Four ways to extend "
                    "Claude, and they all sound alike."),
                   ("So which one do you pick? Don't start with the tool. Start with "
                    "the problem."),
                   ("All you need today is Claude Code, and a few jobs you repeat."))),
    needs=("Claude Code", "a few jobs you repeat in your work"),
    scenes=(
        Scene("The four", "bullets", "Four ways to extend", items=(
            "MCP: reach things outside Claude",
            "Skill: teach Claude a way of working",
            "Subagent: a separate worker",
            "Hook: runs every time",
        ), said=(
            ("MCP connects Claude to the outside: your database, Slack, a browser. "
             "Data and actions it can't reach alone."),
            ("A skill is instructions and know-how. Claude loads it when the task "
             "fits, or when you type its name."),
            ("A subagent is a helper with its own separate workspace. It does the "
             "job, and sends back just a summary."),
            ("A hook is your own command, tied to an event. It fires every time, "
             "without Claude deciding."))),
        Scene("Spot the trigger", "steps", "Spot the trigger", items=(
            "Copying data from a tab Claude can't see → MCP",
            "Pasting the same playbook, third time → skill",
            "A side task floods the chat → subagent",
            "Must happen every time → hook",
        ), said=(
            ("Here's a trick: look for the trigger, the thing you keep doing by hand. "
             "Copying data from a tab Claude can't see? That's MCP."),
            "Pasting the same playbook into chat for the third time? Save it as a skill.",
            ("A side task that floods your conversation with output you'll never "
             "use again? Give it to a subagent."),
            "And something you want every time, without asking? That's a hook.")),
        Scene("Where each one lives", "bullets", "Where each one lives", items=(
            "MCP: .mcp.json, or claude mcp add",
            "Skill: .claude/skills/<name>/SKILL.md",
            "Subagent: .claude/agents/",
            "Hook: .claude/settings.json",
        ), said=(
            ("Each one lives in a different place. MCP servers go in a dot mcp dot "
             "json file, or you add them with claude mcp add."),
            ("A skill is a folder under dot claude slash skills, with a SKILL dot "
             "md file inside."),
            ("A subagent is one markdown file in dot claude slash agents. Same "
             "idea, project or personal."),
            ("And hooks go in your settings file. The project's copy can be "
             "committed, so your whole team gets the same rules."))),
        Scene("", "quiz", "Your orders live in a web app Claude can't see. Pick…",
              items=("A skill", "MCP", "A hook"), answer=1,
              caption="MCP connects Claude to outside data.", said=(
                  ("Quick quiz. Your orders live in a web app Claude can't see. "
                   "Which one helps? Comment your answer."),
                  "B, MCP. It connects Claude to outside data and actions.")),
        Scene("", "subscribe", "Still with me?", said=(
            "If you'd like the rest of these Claude lessons, subscribe.",)),
        Scene("Request or guarantee", "bullets", "Request or guarantee?", items=(
            "CLAUDE.md or a skill: a request",
            "A hook: enforcement",
            "Skills cost a little; hooks cost nothing",
        ), said=(
            ("[emphasis] Here's the big difference. Write never edit the env file "
             "in CLAUDE.md, or in a skill, and it's a request. Claude usually "
             "follows it."),
            ("A hook that blocks the edit is enforcement. It runs on the event, "
             "whatever Claude decides."),
            ("There's a context cost too. A skill's short description sits in "
             "every request. A hook costs nothing, unless it sends something back."))),
        Scene("Better together", "diagram", "They work together", items=(
            "MCP\nthe connection",
            "Skill\nhow to use it well",
            "Hook\nchecks it every time",
        ), said=(
            ("You don't have to choose just one. Say you want Claude working with "
             "your shop's data. MCP gives it the connection to your database."),
            ("A skill then teaches it your tables, and how you like queries "
             "written."),
            ("And a hook can run a check after every edit. [confident] Each one "
             "does the job it's best at."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write down three jobs you repeat",
            "Match each to MCP, skill, subagent or hook",
        ), said=(
            "Pause the video and write down three jobs you repeat each week.",
            ("Match each one to a tool, using the triggers we just went through. "
             "Then resume the video, and we'll keep going."))),
        Scene("Subagent or skill", "bullets", "Skill or subagent?", items=(
            "Skill: adds to your chat",
            "Subagent: own workspace, summary back",
            "They can combine",
        ), said=(
            ("These two get mixed up most. A skill's content joins your main "
             "conversation, so it adds to the load."),
            ("A subagent works in its own window. It might read dozens of files, "
             "and you only see the summary."),
            ("[relaxed] They combine, too. A subagent can load a skill before it "
             "starts."))),
        Scene("", "quiz", "Never edit .env. It must hold every time. Pick…",
              items=("A skill", "A hook", "A subagent"), answer=1,
              caption="A hook enforces. An instruction only asks.", said=(
                  ("Last quiz. Claude must never edit your env file, every single "
                   "time. Which one? Comment."),
                  "B, a hook. A blocking PreToolUse hook enforces it. An instruction only asks.")),
        Scene("Recap", "checklist", "Recap", items=(
            "MCP: outside data and actions",
            "Skill: reusable know-how",
            "Subagent: separate worker, summary back",
            "Hook: runs every time",
        ), said=(
            "Recap. MCP reaches outside data and actions, like your database or Slack.",
            ("A skill is reusable know-how, a playbook that Claude loads when the task "
             "fits."),
            ("A subagent is a separate worker, in its own window, that sends back "
             "just a summary."),
            ("[happy] And a hook runs every single time. Start from the problem, "
             "and the right tool picks itself."))),
    ),
)
