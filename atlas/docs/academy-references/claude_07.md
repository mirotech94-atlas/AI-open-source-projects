# Study note: claude_07

Topic searched: "Claude Code MCP servers plugins skills extensions" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- "Claude Code is Expensive. This MCP Server Fixes It (Context Mode)", Better Stack, https://www.youtube.com/watch?v=QUHrntlfPo4 -- 162,294 views, 6:09, read 2026-11-25. 158 words/min; first on-screen action at not detectable; 10 chapters.
- "Claude Code: The Advanced Guide (99% of Devs Skip These Features)", Tech With Tim, https://www.youtube.com/watch?v=kt5a-TXmWew -- 153,032 views, 19:38, read 2026-11-25. 242 words/min; first on-screen action at 128 s (transcript heuristic); 10 chapters.
- "Claude MCP Tutorial: Give Claude Superpowers in 30 Seconds", Teacher's Tech, https://www.youtube.com/watch?v=Lxznc91wlTk -- 123,491 views, 12:55, read 2026-11-25. 192 words/min; first on-screen action at 40 s (transcript heuristic); 12 chapters.

## How they open

Better Stack opens on a pain, the context window filling up with tool
output, then names one plugin as the cure. Tech With Tim opens with a
promise of nine lesser-known features, each with a real demo, and a free
community link. Teacher's Tech opens on a wish (see my calendar from inside
the chat), then promises a first connected app in about half a minute.
Only the last one starts from a problem the viewer feels; none of them
offers a way to choose between the tools.

## The order they explain things

- Better Stack: why tool output eats the window, how one plugin keeps big
  outputs in a local index, how it saves a checkpoint across compaction,
  install in two commands, a log-file demo, a token-savings readout.
- Tech With Tim: custom subagents as markdown files, skills and a flag that
  stops Claude loading one on its own, hooks tied to events (format after an
  edit, block a risky command), an MCP server for meeting notes, worktrees,
  headless runs, rewind, resuming and branching sessions.
- Teacher's Tech: the charger analogy, three ways to connect (directory,
  desktop extension, hand-edited config), one connector demo end to end,
  then a list of five slip-ups.

## Examples and analogies

- MCP as the universal charging port of AI: one standard plug instead of a
  drawer of cables (Teacher's Tech). The only real analogy in the set.
- A subagent as a separate worker whose answer alone returns to the main
  chat, so the main thread stays lean (Tech With Tim).
- A save point in a game, for session continuity (Better Stack).
- A formatter that runs after every edit, and a guard that stops a
  dangerous command before it runs (Tech With Tim's hook demos).

## Beginner mistakes they address

- Finishing the sign-in popup too early, so the connection never completes.
- Connected tools left switched off in the tools menu.
- Prompts too vague for Claude to pick the right connector.
- Connecting many apps before understanding one.
- Granting access without reading what the permission screen allows.
- Letting big tool outputs fill the window until Claude forgets earlier work.
- Letting Claude run a skill you wanted to start by hand (the lock flag).

## Pacing

Better Stack is calm, about 158 words a minute, and sticks to one tool.
Tech With Tim is the fastest at about 242, nine features in under twenty
minutes, so each one gets a quick demo and little reasoning. Teacher's Tech
sits near 192 and repeats the point at each step, which suits beginners.

## What they do badly, and what we do better

- Better Stack sells one third-party plugin. It is about saving tokens,
  not about choosing between skills, MCP, subagents and hooks, so it drifts
  from our topic. We stay with the four built-in kinds.
- Tech With Tim lists features side by side but never says how to decide
  among them, and part of the video is a sponsor read plus unrelated session
  tricks. We start from the problem and give a trigger for each tool.
- Teacher's Tech mostly teaches the chat app's connectors, not Claude Code,
  and covers MCP only. We keep to Claude Code and compare all four.
- None contrasts a written instruction (a request) with a hook (enforced).
  We make that the big difference, as the official docs do.

## What we take (structure only)

1. Open on the problem, not the tool; show that the four kinds sound alike.
2. One line each on what the four are, with a plain-language picture for MCP.
3. A trigger for each, then where each one lives on disk.
4. Request versus guarantee, and how they combine.
5. Quizzes and a pause so the viewer matches their own jobs to a tool.
