"""Claude #3: Claude Code skills, and writing your first SKILL.md.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is bullets, steps,
one SKILL.md on screen, two quizzes and a pause.

Every product fact was read on the official Claude Code docs on 2026-10-17:

  code.claude.com/docs/en/skills
      ("Create a SKILL.md file with instructions, and Claude adds it to its
      toolkit. Claude uses skills when relevant, or you can invoke one
      directly with /skill-name"; where skills live: personal
      ~/.claude/skills/<skill-name>/SKILL.md "All your projects on this
      machine", project .claude/skills/<skill-name>/SKILL.md "Commit it so
      your team gets it too", plugin <plugin>/skills/<skill-name>/SKILL.md;
      "YAML frontmatter between --- markers that tells Claude when to use
      the skill, and markdown content with the instructions"; name
      "Defaults to the directory name"; description "Claude uses this to
      decide when to apply the skill"; "Only description is recommended";
      "Description always in context, full skill loads when invoked";
      "Claude Code reads the frontmatter only when the opening --- is the
      file's first line"; supporting files such as reference.md and
      scripts/ "loaded when needed", "Reference supporting files from
      SKILL.md", "Keep SKILL.md under 500 lines"; testing: ask something
      that matches the description, or invoke it with /skill-name; changes
      are picked up "within the current session, without a restart";
      Troubleshooting, "Skill not triggering": "Check the description
      includes keywords users would naturally say", rephrase, invoke it
      directly, "Run with --debug to see the parse error"; "Skill triggers
      too often": "Make the description more specific", or
      disable-model-invocation: true)

The meeting-notes skill on screen is our own example, built only from the
fields that page documents.
"""
from __future__ import annotations

from ..tutorial_series import (
    CLAUDE_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

SKILL_MD = """---
name: meeting-notes
description: Turns rough meeting notes into a short summary with action items.
  Use when the user pastes meeting notes or asks for minutes.
---

1. Start with a two-line summary.
2. List every action item: who, what, by when.
3. If a date or an owner is missing, say so. Never guess one."""

PICTURE = (
    "SKILL.md\nwhen, and how",
    "Your request\nmatches it",
    "Claude follows it\nevery time",
)

EPISODE: Episode = scripted(
    next(e for e in CLAUDE_PLANNED if e.number == 3),
    title_hook="Claude Code Skills",
    links=(("Claude Code skills", "https://code.claude.com/docs/en/skills"),),
    ask="Which task would you turn into a skill first? Tell me in the comments.",
    topic_said=("Today: Claude Code skills, what they are, where they live, and your "
                "first SKILL.md."),
    topic_shown="Your first SKILL.md",
    payoff=payoff_scene(
        ("By the end, you'll have a skill that turns messy meeting notes into a "
         "summary with action items, the same way every time."),
        "Summary · 3 action items: who, what, by when",
        items=PICTURE,
        caption="one file, used whenever it fits"),
    hook=Scene("What you'll learn", "hook", "One file, one habit", items=PICTURE,
               code=SKILL_MD, caption="one file, used whenever it fits", said=(
                   ("Tired of typing the same long instructions into Claude, again and "
                    "again? A skill writes them down once."),
                   ("[confident] It's one small file. Claude reads its description, and "
                    "uses it when your request fits."),
                   ("All you need is Claude Code and a folder to work in. Nothing else "
                    "to install."))),
    needs=("Claude Code", "a project folder"),
    scenes=(
        Scene("What a skill is", "bullets", "What a skill is", items=(
            "A SKILL.md file with instructions",
            "Claude uses it when it's relevant",
            "Or you run it yourself: /skill-name",
        ), said=(
            ("So what is a skill? It's a file called SKILL.md, with instructions in "
             "it. Claude adds it to its toolkit."),
            ("Claude uses it by itself when it's relevant to what you ask."),
            ("Or you call it yourself, with a slash and the skill's name."))),
        Scene("Where skills live", "bullets", "Where skills live", items=(
            "Personal: ~/.claude/skills/<name>/",
            "Project: .claude/skills/<name>/",
            "Plugin: comes with a plugin",
        ), said=(
            ("Each skill gets its own folder. A personal skill sits in the skills "
             "folder inside your home dot claude folder, and works in all your "
             "projects."),
            ("A project skill sits in dot claude, skills, inside the project. Commit "
             "it, and your team gets it too."),
            "And plugins can bring their own skills with them.")),
        Scene("", "quiz", "Your whole team should get a skill. Where?", items=(
            "Personal folder", "The project's .claude/skills", "Anywhere",
        ), answer=1, caption="Commit a project skill and the team has it.", said=(
            ("Quick quiz. You want everyone on your team to get a skill. Where do you "
             "put it? Comment your answer."),
            "It's B, the project's skills folder, committed with the code.")),
        Scene("SKILL.md, line by line", "code", "SKILL.md, line by line", code=SKILL_MD,
              marks=((0, 5), (1, 2), (2, 4), (5, 8)),
              caption=".claude/skills/meeting-notes/SKILL.md", said=(
                  ("Here's our skill. The top part, between the three dashes, is the "
                   "front matter. The dashes must be the very first line."),
                  ("The name becomes the slash command. Leave it out, and the folder "
                   "name is used."),
                  ("[emphasis] The description is the important bit. It says what the "
                   "skill does, and when to use it."),
                  ("Below that go the instructions, in plain words. Notice the last "
                   "rule: never guess a date or an owner."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you'd like more of these short Claude lessons, subscribe.",)),
        Scene("The description decides", "bullets", "Why the description matters", items=(
            "Always in context: the description",
            "Loaded only when used: the rest",
            "Use the words people would say",
        ), said=(
            ("Why does the description matter so much? Because Claude always sees "
             "it, even when the skill isn't running."),
            ("The rest of the file loads only when the skill is used. So a long skill "
             "doesn't crowd every chat, and you can have lots of them."),
            ("Write it with the words people really say, like meeting notes, or "
             "minutes. [chuckling] Claude can't read your mind, only your "
             "description."))),
        Scene("", "pause", "Pause and try this", items=(
            "Make .claude/skills/meeting-notes/",
            "Save SKILL.md inside it",
        ), said=(
            ("Pause the video and make the folder: dot claude, skills, meeting "
             "notes."),
            ("Save the file inside it as SKILL.md, copying the front matter from the "
             "screen. Resume when it's saved."))),
        Scene("Add files next to it", "bullets", "More than one file", items=(
            "reference.md: details, loaded when needed",
            "scripts/: run, not loaded",
            "Keep SKILL.md under 500 lines",
        ), said=(
            ("A skill can be more than one file. Put long details in a reference "
             "file next to it, and Claude opens it only when needed."),
            ("Scripts can live there too. Claude runs them, rather than reading them "
             "in."),
            ("Mention each file in SKILL.md, so Claude knows it's there. And keep "
             "SKILL.md itself short, under five hundred lines."))),
        Scene("Try it, and fix it", "steps", "Try it, and fix it", items=(
            "Paste notes: 'turn these into minutes'",
            "Or type /meeting-notes",
            "Not used? Check the description's words",
            "Still nothing? claude --debug",
        ), said=(
            ("Now try it. Paste some notes and ask Claude to turn them into minutes. "
             "No restart needed, it picks up new skills right away."),
            ("Or run it yourself, by typing slash meeting notes. That works even when "
             "your request doesn't match the description."),
            ("[sighing] Claude ignores it? Check the description has the words you "
             "used, or rephrase your request."),
            ("If the front matter doesn't parse, start Claude with the debug flag, "
             "and it shows the error. And the opposite problem? If it runs when you "
             "don't want it, make the description more specific."))),
        Scene("", "quiz", "Claude never uses your skill. First check?", items=(
            "Reinstall Claude Code", "The description's words", "Make it longer",
        ), answer=1, caption="The description decides when it's used.", said=(
            ("Last quiz. Claude never picks up your skill. What do you check first? "
             "Comment."),
            "B, the description. That's what Claude reads to decide.")),
        Scene("Recap", "checklist", "Recap", items=(
            "A skill is one SKILL.md in its own folder",
            "Project skills are shared with your team",
            "The description decides when it's used",
            "Test it: ask, or run /skill-name",
        ), said=(
            "Recap. A skill is a SKILL.md file, in its own folder.",
            "Put it in the project, and your whole team gets it.",
            "The description decides when Claude uses it, so write it with care.",
            ("[happy] And test it by asking, or by running it by name. Write instructions "
             "once, and stop repeating yourself."))),
    ),
)
