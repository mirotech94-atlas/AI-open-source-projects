"""Claude #5: Claude Code hooks, PreToolUse and PostToolUse.

Study note: docs/academy-references/claude_05.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is bullets, steps,
the settings file and the hook script on screen, two quizzes and a pause.

Every product fact was read on the official Claude Code docs on 2026-10-18:

  code.claude.com/docs/en/hooks-guide
      ("Hooks are user-defined shell commands. Claude Code runs them at
      specific points in its lifecycle, which gives you deterministic
      control: certain actions always happen rather than relying on the LLM
      to choose to run them"; "add a hooks block to a settings file";
      locations: ~/.claude/settings.json "All your projects", not
      shareable; .claude/settings.json "Single project", "can be committed
      to the repo"; .claude/settings.local.json "gitignored"; "Type /hooks
      at the Claude Code prompt to open the hooks browser"; PreToolUse
      "Before a tool call executes. Can block it", PostToolUse "After a
      tool call succeeds"; Auto-format: PostToolUse with an Edit|Write
      matcher, "jq -r '.tool_input.file_path' | xargs npx prettier
      --write"; Block edits to protected files: protect-files.sh reads the
      input, ".env" "package-lock.json" ".git/", "exits with code 2 to
      block the edit", "Hook scripts must be executable", chmod +x,
      "$CLAUDE_PROJECT_DIR"/.claude/hooks/protect-files.sh, "Ask Claude to
      add a comment to your .env file. Claude Code blocks the edit before it
      runs and passes the script's Blocked: message to Claude as feedback";
      "Claude Code passes event-specific data as JSON to your script's
      stdin"; tool_name, tool_input; Exit 0 "no objection", "For a
      PreToolUse hook this doesn't approve the tool call: the normal
      permission flow still applies"; Exit 2 "Claude Code blocks the
      action. Write a reason to stderr"; any other exit code: for most
      events "the action proceeds as a non-blocking error"; "The
      Edit|Write matcher fires only when Claude uses the Edit or Write
      tool"; the Bash examples use jq; "PostToolUse hooks can't undo
      actions since the tool has already executed"; Hook not firing: run
      /hooks, "Matchers are case-sensitive"; test a script "manually by
      piping sample JSON"; claude --debug for the full stderr)
  code.claude.com/docs/en/hooks
      (exit code 2 per event: PreToolUse "Blocks the tool call";
      PostToolUse "Shows stderr to Claude; the tool already ran")

The settings and the script on screen are the guide's own two examples,
the script shortened to its checking lines.
"""
from __future__ import annotations

from ..tutorial_series import (
    CLAUDE_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

SETTINGS_JSON = """{
  "hooks": {
    "PreToolUse": [{ "matcher": "Edit|Write", "hooks": [{
      "type": "command",
      "command": "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/protect-files.sh" }] }],
    "PostToolUse": [{ "matcher": "Edit|Write", "hooks": [{
      "type": "command",
      "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write" }] }]
  }
}"""

PROTECT_SH = """#!/bin/bash
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
for pattern in ".env" "package-lock.json" ".git/"; do
  if [[ "$FILE_PATH" == *"$pattern"* ]]; then
    echo "Blocked: $FILE_PATH matches protected pattern '$pattern'" >&2
    exit 2
  fi
done
exit 0"""

PICTURE = (
    "Claude goes\nto edit a file",
    "Your hook runs\nevery single time",
    "Blocked, or\nformatted for you",
)

EPISODE: Episode = scripted(
    next(e for e in CLAUDE_PLANNED if e.number == 5),
    title_hook="Claude Code Hooks",
    title_benefit="Automatic Checks",
    links=(("Automate actions with hooks", "https://code.claude.com/docs/en/hooks-guide"),
           ("Hooks reference", "https://code.claude.com/docs/en/hooks")),
    ask="Which file in your project should Claude never touch? Tell me in the comments.",
    topic_said=("Today: Claude Code hooks, your own commands that run before and after "
                "Claude uses a tool."),
    topic_shown="PreToolUse and PostToolUse",
    payoff=payoff_scene(
        ("By the end, Claude can't edit your secrets file, and every file it writes "
         "gets tidied up by itself."),
        "Edit .env → Blocked: matches protected pattern '.env'",
        items=PICTURE,
        caption="rules that don't depend on memory"),
    hook=Scene("What you'll learn", "hook", "Rules that always run", items=PICTURE,
               code=SETTINGS_JSON, caption="rules that don't depend on memory", said=(
                   ("[sighing] You told Claude not to touch a file. It forgot. That's "
                    "where hooks come in."),
                   ("A hook is your own command, and Claude Code runs it at a fixed "
                    "moment, every time. No judgement involved."),
                   ("All you need is Claude Code, a project folder, and a small tool "
                    "called jq, for reading the hook's input."))),
    needs=("Claude Code", "a project folder", "jq, to read the hook's input"),
    scenes=(
        Scene("What a hook is", "bullets", "What a hook is", items=(
            "Your shell command",
            "Run at a fixed point, every time",
            "Not Claude's choice: it always happens",
        ), said=(
            ("So what's a hook? It's a shell command you write, like a script or a "
             "one-liner."),
            ("Claude Code runs it at a set moment: say, just before Claude edits a "
             "file, or just after."),
            ("[emphasis] And it isn't up to Claude. A skill is advice Claude can "
             "weigh up. A hook simply runs."))),
        Scene("Where hooks live", "bullets", "Where hooks live", items=(
            "~/.claude/settings.json: all your projects",
            ".claude/settings.json: this project, commit it",
            "/hooks: see every hook you have",
        ), said=(
            ("Hooks go in a settings file. The one in your home folder covers every "
             "project on your machine."),
            ("The one inside the project's dot claude folder covers just that "
             "project, and you can commit it for your team."),
            ("And typing slash hooks in Claude Code lists every hook it found, "
             "grouped by the moment it runs. Handy when you've forgotten what you "
             "set up."))),
        Scene("Two moments", "bullets", "Before and after", items=(
            "PreToolUse: before a tool runs, can block it",
            "PostToolUse: after it worked",
            "Matcher Edit|Write: only file edits",
        ), said=(
            ("There are lots of moments to hook into. Today, two. PreToolUse runs "
             "before Claude uses a tool, and it can stop it."),
            ("PostToolUse runs after the tool has worked. Perfect for tidying up."),
            ("And a matcher says which tools count. Edit, bar, Write means only "
             "when Claude edits or writes a file."))),
        Scene("", "quiz", "Stop an edit before it happens. Which event?", items=(
            "PostToolUse", "PreToolUse", "Either one",
        ), answer=1, caption="Only PreToolUse runs before the tool, so it can block.",
              said=(
            ("Quick quiz. You want to stop an edit before it happens. Which event? "
             "Comment your answer."),
            "B, PreToolUse. PostToolUse runs after, when the edit's already done.")),
        Scene("The settings file", "code", ".claude/settings.json", code=SETTINGS_JSON,
              marks=((2, 5), (5, 8)),
              caption=".claude/settings.json", said=(
                  ("Here's the project's settings file. The PreToolUse entry runs our "
                   "protect files script before any edit."),
                  ("The PostToolUse entry runs Prettier, a code formatter, on the file "
                   "Claude just wrote. [confident] Tidy files, without asking."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you'd like the rest of these Claude lessons, subscribe.",)),
        Scene("The protect-files script", "code", "protect-files.sh", code=PROTECT_SH,
              marks=((1, 3), (3, 5), (5, 7), (9, 10)),
              caption=".claude/hooks/protect-files.sh", said=(
                  ("Now the script. Claude Code hands it the details as JSON, and jq "
                   "pulls out the file path."),
                  ("Then it checks the path against a short list: the env file, the "
                   "lock file, and the git folder."),
                  ("If one matches, it writes Blocked, and why, then exits with code "
                   "two. That stops the edit."),
                  ("Otherwise it exits with zero, and nothing changes. One more thing: the "
             "script must be executable, or it can't run at all."))),
        Scene("Exit codes", "bullets", "What Claude sees", items=(
            "0: no objection, normal permissions apply",
            "2: blocked, your message goes to Claude",
            "Anything else: an error, the action goes ahead",
        ), said=(
            ("Exit codes are how a hook answers. Zero means no objection. It doesn't "
             "approve anything, your usual permissions still apply."),
            ("Two means block it. Whatever you wrote goes to Claude, so it can try "
             "another way."),
            ("[break] Any other code is an error, and the action goes ahead. So for "
             "a hard stop, it must be two."))),
        Scene("", "pause", "Pause and try this", items=(
            "Save .claude/hooks/protect-files.sh",
            "chmod +x it, and add it to settings",
        ), said=(
            "Pause the video and save the script in your project's hooks folder.",
            ("Make it executable with chmod plus x, add it to your settings file, "
             "and resume."))),
        Scene("Test it, and fix it", "steps", "Test it, and fix it", items=(
            "Ask Claude: add a comment to .env",
            "Blocked? It works",
            "Never fires? /hooks · check the matcher's case",
            "Errors? Pipe test JSON into the script",
        ), said=(
            "Now test it. Ask Claude to add a comment to your env file.",
            ("[excited] Blocked, with your message. Claude reads it, and leaves the "
             "file alone."),
            ("Hook never fires? Open slash hooks and check it's there. Matchers care "
             "about capitals, so edit, lower case, won't match."),
            ("Seeing an error instead? Pipe a little test JSON into the script "
             "yourself, or start Claude with the debug flag to read the full "
             "message."))),
        Scene("", "quiz", "A PostToolUse hook exits 2. Is the edit undone?", items=(
            "Yes, it's rolled back", "No, the tool already ran",
        ), answer=1, caption="PostToolUse can't undo: the tool has already run.", said=(
            ("Last quiz. A PostToolUse hook exits with two. Is the edit undone? "
             "Comment."),
            ("B, no. The tool already ran. Claude sees your message, but blocking "
             "belongs in PreToolUse."))),
        Scene("Recap", "checklist", "Recap", items=(
            "A hook: your command, run every time",
            "Project hooks: .claude/settings.json",
            "PreToolUse blocks · PostToolUse tidies",
            "Exit 2 blocks and tells Claude why",
        ), said=(
            "Recap. A hook is your own command, and it runs every single time.",
            "Project hooks live in the project's settings file, and travel with it.",
            "PreToolUse can block a tool. PostToolUse cleans up after it.",
            ("[happy] And exit code two is the stop sign, with a reason Claude can "
             "read. Your rules, kept every time."))),
    ),
)
