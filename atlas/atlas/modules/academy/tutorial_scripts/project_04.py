"""Project #4: a custom Claude skill for our assistant's replies.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is bullets, steps,
the project's SKILL.md on screen, a diagram, two quizzes and a pause.

Every product fact was read on the official Claude Code docs on 2026-10-17:

  code.claude.com/docs/en/skills
      (project skills: .claude/skills/<skill-name>/SKILL.md, "Sessions in
      this repository. Commit it so your team gets it too"; frontmatter
      "between --- markers that tells Claude when to use the skill";
      description: "Claude uses this to decide when to apply the skill";
      "Description always in context, full skill loads when invoked";
      "a skill's body loads only when it's used"; supporting files such as
      examples.md "loaded when needed", "Reference supporting files from
      SKILL.md"; "Keep SKILL.md under 500 lines"; test by asking "something
      that matches the description" or with /skill-name; Troubleshooting,
      "Claude stops following a skill": "Claude skipped a rule that must
      hold every time: move the rule into a hook. Claude Code runs a hook
      every time its event occurs"; after compaction Claude Code "can keep
      only the start of an invoked skill, so put the most important
      instructions near the top of SKILL.md")

The tool names in the skill are the project's own: log_request is the n8n
tool from project #2, opening_hours the server from project #3.
"""
from __future__ import annotations

from ..tutorial_series import (
    PROJECT_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

SKILL_MD = """---
name: customer-reply
description: Drafts a reply to a customer message for our shop.
  Use for any customer email, form or lead that needs an answer.
---

1. Hours? Call the opening_hours tool. Never guess.
2. Under 120 words, warm, signed "The team".
3. Never promise refunds, discounts or dates.
4. Log it with the log_request tool, then show me the draft."""

PICTURE = (
    "A customer message\narrives",
    "customer-reply skill\nthe same rules, every time",
    "A draft for you\nto approve",
)

EPISODE: Episode = scripted(
    next(e for e in PROJECT_PLANNED if e.number == 4),
    title_hook="to Add a Claude Skill to Your Project",
    links=(("Claude Code skills", "https://code.claude.com/docs/en/skills"),),
    ask="What's the one rule every reply from your business must follow? Comment it.",
    topic_said=("Today: one skill that teaches Claude how our assistant replies, so "
                "every draft follows the same rules."),
    topic_shown="A skill for the assistant",
    payoff=payoff_scene(
        ("By the end, every reply our assistant drafts checks the real hours, stays "
         "short and kind, and waits for you."),
        "Draft ready · hours checked · under 120 words · logged",
        items=PICTURE,
        caption="house rules, written once"),
    hook=Scene("What we'll build", "hook", "House rules for Claude", items=PICTURE,
               code=SKILL_MD, caption="house rules, written once", said=(
                   ("Our assistant can already log requests and look up hours. But "
                    "does every reply sound like your business? Not yet."),
                   ("[confident] All you need is the project folder from earlier, and "
                    "Claude Code. One file fixes it."))),
    needs=("Claude Code", "the project folder from the earlier episodes"),
    scenes=(
        Scene("Why a skill", "bullets", "Why not a longer prompt?", items=(
            "You'd paste the same rules every time",
            "A skill loads only when it's needed",
            "Commit it: everyone gets the same rules",
        ), said=(
            ("Why a skill, and not just a longer prompt? Because a prompt has to be "
             "pasted every single time. [sighing] And sooner or later, someone forgets."),
            ("A skill is written once. Claude always sees its description, and loads "
             "the rest only when a reply is needed."
             " So it costs almost nothing until it's used."),
            ("And it lives in the project. Commit it, and everyone working on the "
             "assistant gets the same rules."))),
        Scene("The project's SKILL.md", "code", "customer-reply/SKILL.md", code=SKILL_MD,
              marks=((1, 4), (5, 6), (6, 8), (8, 9)),
              caption=".claude/skills/customer-reply/SKILL.md", said=(
                  ("Here it is, in dot claude, skills, customer reply. The name and "
                   "description go at the top. The description covers emails, forms "
                   "and leads."),
                  ("Rule one: for hours, call the opening hours tool from our own "
                   "server. Never guess."),
                  ("Rules two and three: short, warm, signed by the team, and no "
                   "promises about refunds, discounts or dates."),
                  ("Rule four: log it with our n8n tool, then show us the draft. "
                   "Nothing is sent by Claude."
             " Claude only drafts. You decide what goes out."))),
        Scene("", "quiz", "Where must this skill live so your team gets it?", items=(
            "Your home folder", "The project, committed", "A sticky note",
        ), answer=1, caption="Project skills travel with the code.", said=(
            ("Quick quiz. Where must the skill live so your whole team gets it? "
             "Comment your answer."),
            ("B. In the project, committed. It travels with the code."
             " A new teammate gets it on day one."))),
        Scene("", "subscribe", "Building along?", said=(
            "If you're building this assistant with me, subscribe, so you catch every part.",)),
        Scene("Test it", "steps", "Test that Claude picks it up", items=(
            "Paste a customer message: 'reply to this'",
            "Check: hours tool called, under 120 words",
            "Or run it yourself: /customer-reply",
        ), said=(
            ("Now test it. Paste a real-looking customer message, asking if you're "
             "open on Saturday, and say: reply to this."),
            ("Check the draft against the rules. Did Claude call the hours tool? Is it "
             "short? Is it signed by the team?"
             " If it misses a rule, make that line clearer, or move it higher up."),
            ("You can also run it by name, with slash customer reply. [excited] Same "
             "rules, every time."))),
        Scene("", "pause", "Pause and try this", items=(
            "Save customer-reply/SKILL.md",
            "Test it with one made-up message",
        ), said=(
            ("Pause the video and save the skill in your project."
             " Copy the rules from the screen."),
            ("Test it with one made-up customer message. Resume when you have a draft."
             " Try one that asks about Saturday."))),
        Scene("Short and checkable", "bullets", "Keep it short and checkable", items=(
            "One rule per line, easy to check",
            "Most important rules at the top",
            "Examples in a separate examples.md",
        ), said=(
            ("Now, keeping it useful. Write one rule per line, so you can check a "
             "draft against it in seconds."),
            ("Put the most important rules near the top. When a long chat gets "
             "compacted, Claude may keep only the start of a skill."),
            ("And if you want sample replies, put them in an examples file next to "
             "SKILL.md, and mention it. Claude opens it only when needed. Keep SKILL.md "
             "itself well under five hundred lines."))),
        Scene("Rules that must always hold", "diagram", "Skill or hook?", items=(
            "Skill\nhow to write the reply",
            "Hook\na rule that runs every time",
            "You\napprove before anything is sent",
        ), said=(
            ("One more thing. A skill guides Claude, but Claude applies it with "
             "judgement."
             " For tone and wording, that's exactly what you want."),
            ("If a rule must hold every single time, the docs say move it into a "
             "hook, which runs on every event. Hooks get their own episode."),
            ("And for our assistant, the last rule is always you: a person approves "
             "before anything goes to a customer."))),
        Scene("", "quiz", "A rule must hold every time, no exceptions. Use…", items=(
            "A longer skill", "A hook", "Bold text",
        ), answer=1, caption="Hooks run every time their event happens.", said=(
            ("Last quiz. A rule must hold every single time, no exceptions. Skill or "
             "hook? Comment."),
            "B, a hook. It runs on every event, whether or not Claude follows the skill.")),
        Scene("Recap", "checklist", "Recap", items=(
            "One project skill: customer-reply",
            "Rules: real hours, short, no promises, log it",
            "Test it: ask, or /customer-reply",
            "Must-always rules belong in a hook",
        ), said=(
            "Recap. Our assistant now has one project skill, customer reply.",
            "Its rules: real hours, short and warm, no promises, and log every request.",
            "We tested it by asking, and by running it by name.",
            ("[happy] And anything that must hold every time belongs in a hook. Every "
             "draft now sounds like your business."))),
    ),
)
