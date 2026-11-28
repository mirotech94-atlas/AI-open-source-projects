"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/claude_09.md

Claude #9: Claude Code permissions, allow, ask and deny rules and the modes.

Drafted 2026-11-24. New topic (the series plan was used up): the series
promise covers using Claude Code safely, and the docs cover permissions fully.
No live demo: the picture is bullets, steps, one settings file on screen, two
quizzes and a pause. Level 3: levels never go down.

Every product fact was read on the official Claude Code docs on 2026-11-24:

  code.claude.com/docs/en/permissions
      ("Claude Code uses a tiered permission system to balance power and
      safety"; read-only tools such as file reads and Grep need no approval
      within the working directory; Bash commands and file edits ask, except a
      built-in set of read-only commands; the prompt's "Yes, and don't ask
      again" saves a Bash rule "to .claude/settings.local.json"; "You can view
      and manage Claude Code's tool permissions with /permissions"; "Allow
      rules let Claude Code use the specified tool without manual approval",
      "Ask rules prompt for confirmation", "Deny rules prevent Claude Code from
      using the specified tool"; "Rules are evaluated in order: deny, then ask,
      then allow. The first match in that order determines the outcome"; "An
      allow rule can't carve an exception out of a deny rule"; "Permission
      rules are enforced by Claude Code, not by the model. Instructions in your
      prompt or CLAUDE.md shape what Claude tries to do, but they don't change
      these rules"; rule format "Tool or Tool(specifier)"; Bash(npm run build)
      "Matches the exact command npm run build", Read(./.env) "Matches reading
      the .env file in the current directory", WebFetch(domain:example.com);
      the docs' settings example: allow "Bash(npm run *)" and "Bash(git commit
      *)", deny "Bash(git push *)"; "Put the * after the subcommand"; "A push
      written another way, such as git -C . push, isn't matched"; "Bash
      permission patterns that try to constrain command arguments are
      fragile"; modes: default "Prompts before most edits, shell commands, and
      network requests", acceptEdits "Automatically accepts file edits and
      common filesystem commands ... for paths in the working directory", plan
      "Claude reads files and runs read-only shell commands to explore but
      doesn't edit your source files", bypassPermissions "Skips permission
      prompts"; "Only use this mode in isolated environments like containers
      or VMs where Claude Code can't cause damage"; "set defaultMode in your
      settings files"; project settings at .claude/settings.json and user
      settings at ~/.claude/settings.json)

  code.claude.com/docs/en/permission-modes, re-read 2026-11-25 after the study
      note found auto mode missing ("A separate classifier model reviews
      actions before they run and blocks the ones it judges risky"; "Auto mode
      reduces permission prompts but does not guarantee safety")

The settings file on screen is the docs' own example, plus one deny rule for
the .env file built from the Read(./.env) form that page documents.
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
    9, 3, "claude-code-permissions",
    "Claude Code Permissions: Allow, Ask and Deny Rules, and the Safe Modes",
    "Allow, ask,\ndeny",
    ("How Claude Code decides when to ask you, how to write allow, ask and deny rules, "
     "and which permission mode fits which job."),
    ("claude code permissions", "claude code allow deny rules",
     "claude code permission modes", "claude code settings.json",
     "claude code permissions command"),
    ("#claudeai", "#mcp", "#aiagents"),
    ("What Claude Code asks about, and what it doesn't",
     "Allow, ask and deny rules, and the order they are checked in",
     "Writing a rule: Tool(specifier) and the star",
     "The settings file: user, project and local",
     "Modes: default, acceptEdits, plan and bypassPermissions",
     "Rules are enforced by Claude Code, not by the model",
     "Recap and a quiz: where is bypassPermissions safe?"))

SETTINGS = """{
  "permissions": {
    "allow": [
      "Bash(npm run *)",
      "Bash(git commit *)"
    ],
    "deny": [
      "Bash(git push *)",
      "Read(./.env)"
    ]
  }
}"""

PICTURE = (
    "Claude wants\nto act",
    "Your rules\ndecide",
    "Allowed, asked\nor denied",
)

EPISODE: Episode = scripted(
    PLANNED,
    result="Safe permissions",
    title_hook="Claude Code Permissions",
    title_benefit="Safe Rules",
    links=(("Configure permissions", "https://code.claude.com/docs/en/permissions"),
           ("Permission modes", "https://code.claude.com/docs/en/permission-modes"),
           ("Settings", "https://code.claude.com/docs/en/settings")),
    ask="Which command would you never let Claude run alone? Tell me below.",
    topic_said=("Today: Claude Code permissions, and the rules that decide what it "
                "can do without asking."),
    topic_shown="Allow, ask, deny",
    payoff=payoff_scene(
        ("By the end, Claude will run your safe commands without asking, and never "
         "touch the ones you rule out."),
        "deny: git push · allow: npm run *",
        items=PICTURE,
        caption="you decide what needs a yes"),
    hook=Scene("What you'll learn", "hook", "You set the limits", items=PICTURE,
               code=SETTINGS, caption="you decide what needs a yes", said=(
                   ("[curious] Claude Code keeps asking, can I run this? Or worse, "
                    "you click yes without reading."),
                   ("There's a better way. You write the rules once, and Claude Code "
                    "follows them every time."),
                   ("All you need is Claude Code and a project folder."))),
    needs=("Claude Code", "a project folder"),
    scenes=(
        Scene("When Claude asks", "bullets", "When Claude asks", items=(
            "Reading files in your project: no ask",
            "Shell commands and edits: it asks",
            "'Yes, and don't ask again' saves a rule",
        ), said=(
            ("Out of the box, Claude Code reads files in your project without "
             "asking. Reading is low risk."),
            ("Running shell commands and editing files? Those ask first, apart from "
             "a built-in set of read-only commands."),
            ("And when you choose, yes, and don't ask again, Claude Code saves that "
             "as a rule. Type slash permissions any time to see every rule."))),
        Scene("Three kinds of rule", "bullets", "Allow, ask, deny", items=(
            "Allow: no approval needed",
            "Ask: always confirm",
            "Deny: never allowed",
        ), said=(
            ("There are three kinds of rule. Allow lets Claude use a tool without "
             "asking you."),
            ("Ask means always stop and confirm first. Good for things like a deploy "
             "you want to see before it happens."),
            ("And deny means no, ever. [emphasis] When they clash, deny wins, then "
             "ask, then allow. The first match decides."))),
        Scene("", "quiz", "A command matches an allow rule and a deny rule. Result?",
              items=("Allowed", "Denied", "It asks"), answer=1,
              caption="Deny is checked first.", said=(
                  ("Quick quiz. One command matches both an allow rule and a deny "
                   "rule. What happens? Comment your answer."),
                  "B, denied. Deny is checked first, and an allow can't carve out an exception.")),
        Scene("The settings file", "code", ".claude/settings.json", code=SETTINGS,
              marks=((2, 6), (6, 9), (1, 10)),
              caption=".claude/settings.json", said=(
                  ("Here's a settings file. Under allow, npm run anything, and git "
                   "commit, run without asking."),
                  ("Under deny, git push is blocked. So is reading the env file."),
                  ("This one is the project's file, so you can commit it. Your home "
                   "folder has one too, for every project."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you'd like the rest of these Claude lessons, subscribe.",)),
        Scene("Write a rule", "steps", "Write a rule", items=(
            "A tool name, or Tool(specifier)",
            "Bash(npm run build): that exact command",
            "Bash(npm run *): any npm run",
            "Read(./.env): reading that file",
        ), said=(
            ("A rule is a tool name on its own, which covers every use of that tool, or "
             "a tool name with a specifier in brackets, which narrows it."),
            "Bash, npm run build, matches that exact command, and nothing longer.",
            ("Add a star, and Bash, npm run star, matches any npm script. Put the "
             "star after the subcommand."),
            "And Read, dot slash dot env, matches reading that one file.")),
        Scene("Rules have limits", "bullets", "Rules have limits", items=(
            "git push blocked, but git -C . push?",
            "Argument patterns are fragile",
            "Need certainty? A PreToolUse hook",
        ), said=(
            ("[sighing] Be honest about the limits. The git push rule won't catch a "
             "push written another way, like git dash capital C, dot, push."),
            ("Patterns that try to limit a command's arguments are fragile. The docs "
             "warn about that."),
            ("For anything you must be sure of, add a PreToolUse hook as well. A "
             "script can check the whole command, whichever way it's written."))),
        Scene("", "pause", "Pause and try this", items=(
            "Type /permissions, read your rules",
            "Add one deny rule you'd want",
        ), said=(
            "Pause the video and type slash permissions in Claude Code.",
            ("Read what's there first. Then add one deny rule you'd want, for a command "
             "you never want run on its own, and then resume the video."))),
        Scene("Permission modes", "bullets", "Permission modes", items=(
            "default: asks before most things",
            "acceptEdits: file edits go through",
            "plan: reads and explores, no edits",
            "auto: a classifier reviews each action",
            "bypassPermissions: no prompts at all",
        ), said=(
            ("Modes change how much Claude asks. Default asks before most edits, "
             "commands and network requests."),
            ("AcceptEdits approves file edits in your project, plus common file "
             "commands like make a folder, move or copy."),
            ("Plan lets Claude read and explore, but not edit your code. [relaxed] "
             "Good for thinking first."),
            ("Auto has a separate classifier model review actions before they run, "
             "and block the risky ones. Fewer prompts, but no guarantee of safety."),
            ("And bypassPermissions skips the prompts completely. Only in a "
             "container or a virtual machine, where Claude Code can't cause damage."))),
        Scene("", "quiz", "Where is bypassPermissions safe to use?",
              items=("Your main laptop", "A container or VM", "Any project"),
              answer=1, caption="Only where it can't cause damage.", said=(
                  ("Last quiz. Where is bypassPermissions safe to use? Comment your "
                   "answer."),
                  ("B, a container or a virtual machine. Anywhere else, it can "
                   "cause real damage."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Allow · ask · deny; deny wins",
            "Rules: Tool(specifier), with a star",
            "Modes set how much it asks",
            "Rules bind Claude; a file doesn't",
        ), said=(
            ("Recap. Allow, ask and deny are the three kinds of rule, and when they "
             "clash, deny wins."),
            "A rule is a tool, with a specifier, and a star where things vary.",
            ("Modes set how much Claude asks. Plan for thinking, accept edits for "
             "flow, and bypass only for containers."),
            ("[happy] And permission rules are enforced by Claude Code itself, so "
             "they hold even when a file's advice doesn't."))),
    ),
)
