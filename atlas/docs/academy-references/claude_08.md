# Study note: claude_08

Topic searched: "Claude Code memory CLAUDE.md tutorial" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- "Learn 80% of Claude in Under 20 Minutes", Jeff Su, https://www.youtube.com/watch?v=z9rdrNrkvDY -- 1,632,603 views, 18:54, read 2026-11-25. 185 words/min; first on-screen action at 140 s (transcript heuristic); 9 chapters.
- "Claude Code Tutorial #2 - CLAUDE.md Files & /init", Net Ninja, https://www.youtube.com/watch?v=i_OHQH4-M2Y -- 356,772 views, 12:17, read 2026-11-25. 199 words/min; first on-screen action at 120 s (transcript heuristic); 5 chapters.
- "CLAUDE.md, AGENTS.md and SKILL.md Explained for Beginners", AI School 101, https://www.youtube.com/watch?v=hCL0sWxWJKw -- 311,426 views, 4:06, read 2026-11-25. 148 words/min; first on-screen action at 49 s (transcript heuristic); 12 chapters.

## How they open

Jeff Su opens by naming the viewer (a chat user wary of the terminal) and
promising seven capabilities of a desktop app. Net Ninja picks up mid-course:
Claude is already running, and he says to set up the project file before any
real work. AI School 101 opens on the puzzle of odd file names and answers it
in one line: they are all the same kind of file.

## The order they explain things

- Jeff Su: chat versus the desktop app, settings, local file access, then
  memory as the second capability, shown by asking the app to save its
  preferences into files it keeps for itself; connectors, skills and more.
- Net Ninja: run the init command, watch Claude explore the project, approve
  the file, read what it wrote, edit it by hand, test that Claude obeys it,
  add a note from inside the chat, the three places a memory can live, then
  a command to open the memory files.
- AI School 101: what markdown is, the README for people, CLAUDE.md for
  Claude, AGENTS.md for any agent, then skills as step-by-step jobs, and a
  one-line rule for which goes where.

## Examples and analogies

- A bakery website with rules like keep prices, simple English (AI School 101).
- A cookbook: you read the contents page and open one recipe, not all of
  them, for how skills load (AI School 101).
- Rules of the house versus steps for one job (AI School 101).
- A mini-documentation of your code that Claude consults (Net Ninja).
- A theme-preference hook placed in the right folder because the file said
  where hooks go, and an about page that gets a header link because of a
  saved note (Net Ninja's tests).
- A silly name-greeting preference to show what remembering means (Jeff Su).

## Beginner mistakes they address

- Starting work before Claude knows the project's layout and commands.
- Creating the file once and never updating it when the structure changes.
- Putting personal habits in the file the whole team shares.
- Claude doing more than asked, such as adding extra links; the fix is to
  write down what not to do.
- Cramming every job into one big rules file, so Claude misses things.
- Mixing up which of the odd file names is for people and which for AI.

## Pacing

AI School 101 is slow and gentle, about 148 words a minute, and short
enough to watch twice. Net Ninja is steady at about 199 and shows every
step on screen. Jeff Su is brisk at about 185 across many topics, so
memory gets only a few minutes before he moves on.

## What they do badly, and what we do better

- Jeff Su's video is about the desktop Cowork app, not Claude Code, and he
  defers how the memory files work to a later video. It drifts from our
  topic; we explain the file itself.
- Net Ninja's recording is dated: it uses a hash-key shortcut to save a
  note and a local memory option he says is going away, and his guidance
  to be specific stops at one example. We follow the current docs for the
  three file locations and the memory command.
- AI School 101 is a picture of the idea with no screen demo and no advice
  on writing lines Claude can verify.
- None says plainly that the file is a request, not enforcement, or what
  to use when a rule must hold. We make that the closing point.
- None gives a size guide. We give the docs' length target.

## What we take (structure only)

1. Open on the pain: the same correction every session.
2. What the file is and who reads it, in plain words.
3. Where it lives: yours, the project's, yours for one project.
4. Run the init command, then edit by hand; keep the file current.
5. Concrete, short lines, and when to add one.
6. A request, not a lock: a hook for rules that must always hold.
