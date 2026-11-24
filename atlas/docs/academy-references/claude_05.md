# Study note: claude_05

Topic searched: "claude code hooks tutorial" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=8T0kFSseB58 -- Greg Baugues, 16,488 views, 11:53, read 2026-10-18. 185 words/min; first on-screen action at 522 s (transcript heuristic); 7 chapters.
- https://www.youtube.com/watch?v=wADob457JXk -- TechWhistle, 13,628 views, 6:32, read 2026-10-18. 161 words/min; first on-screen action at not detectable; 9 chapters.
- https://www.youtube.com/watch?v=eFjqogpmNkQ -- Web Dev Cody, 11,163 views, 5:28, read 2026-10-18. 236 words/min; first on-screen action at 101 s (transcript heuristic); 7 chapters.

## How they open

All three open on a sound or a pain, not on a hook blocking anything. Greg
Baugues plays his parallel sessions making cat, beep and spoken noises in
the first half minute, then says the point of the project is to learn when
each event fires. Web Dev Cody kicks off a small edit and waits for a
generated voice line to announce the agent is done. TechWhistle opens on a
scripted pain list (approving every file, formatting by hand, a wrecked
secrets file, a finish nobody noticed) and promises five hooks fix it. Only
TechWhistle mentions guarding a file in the opening; none shows a block.

## The order they explain things

- Greg Baugues: the event list (before a tool, after a tool, notification,
  stop, before compact), which settings file to edit and why he picked the
  committed project one, the hooks block shape, one Python handler for all
  events, logging the JSON that arrives on stdin, then sourcing sounds and
  mapping them per command, then ideas (block a dangerous delete, linters,
  tests before a pull request).
- Web Dev Cody: the finished voice demo, a quick tour of the script he did
  not write, the stop hook in the local settings file, picking a voice,
  then reading the hooks reference page: events, the matcher, tool names.
- TechWhistle: what hooks are, the event list, stdin JSON, exit code two
  as the block, the settings skeleton (event, matcher, command), then five
  hooks: auto-format after edits, a protected-file guard before edits, a
  desktop alert, re-reading key files after compaction, an allow-list.

## Examples and analogies

- Distinct sounds per event and per shell command, used to hear how often
  Claude reads, edits and runs commands (Baugues).
- A spoken done message written by a chat model and voiced by a speech API
  (Web Dev Cody).
- Prettier run on every file Claude edits, fed the path through jq
  (TechWhistle); a guard on the env file, lock file and git folder.
- Hooks compared to CI actions, but for the assistant (TechWhistle).
- Compaction framed as the model losing its rules mid-session, with a hook
  that reloads them (TechWhistle).

## Beginner mistakes they address

- Editing the wrong settings file: the local one is not committed, so the
  hook does not travel with the repo (Baugues).
- Splitting logic across many tiny commands in the settings file; one
  script is easier to debug (Baugues).
- Guessing what a hook receives instead of logging the stdin JSON first
  (Baugues).
- Expecting the stop hook to fire on a manual interrupt; it does not (Web
  Dev Cody, read off the reference page).
- Not knowing what the matcher matches; he finds tool names live (Cody).

## Pacing

Measured above: from about 161 to 236 words a minute. Baugues is calm but
reaches his first real action late (522 s by the heuristic) because the
first half is the sound tour and settings talk. Web Dev Cody is the fastest
and the shortest, and admits he spent minutes on the feature. TechWhistle
has no detectable first action: it is a narrated slide-and-clip piece with
dramatic framing, chapters named by hook, which reads well in a list.

## What they do badly, and what we do better

- Two of three are about notification sounds; the blocking power of a
  before-tool hook is a closing aside (Baugues) or absent (Cody). We make
  the block the centre of the lesson.
- Cody shows a secrets file on screen and cuts the key out in the edit; he
  also says he did not write or read his script. We explain every line we
  run and keep secrets out of frame entirely.
- TechWhistle sells an allow-list hook as god mode that removes prompts;
  that is a security trade-off presented as a free win. We skip it and say
  why a block list is the safer first hook.
- TechWhistle states a precise event count without a source, and nobody
  explains what other exit codes do. We read the exit codes off the docs.
- Nobody tests a hook script by piping sample JSON into it before wiring
  it up. We do that on camera.
- A subscribe push for a next video; our end card asks one question.

## What we take (structure only)

1. Open on the result: Claude tries to edit the env file and is blocked.
2. Name the two events by when they run: before a tool, after a tool.
3. Show the settings block as three parts (event, matcher, command) and say
   which settings file is shared and which is not.
4. Print the stdin JSON once so the viewer sees tool name and input.
5. Build the guard (exit code two blocks, reason to stderr), then the
   after-edit formatter, and prove each live.
6. Close on the two gotchas: matchers are case-sensitive, and an after-tool
   hook cannot undo what already ran.
