# Study note: claude_04

Topic searched: "claude code sub agents tutorial create custom agents" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=vDVSGVpB2vc -- Nate Herk | AI Automation, 383,660 views, 16:29, read 2026-10-18. 258 words/min; first on-screen action at 177 s (transcript heuristic); 11 chapters.
- https://www.youtube.com/watch?v=DNGxMX7ym44 -- Developers Digest, 293,294 views, 6:53, read 2026-10-18. 225 words/min; first on-screen action at not detectable; 7 chapters.
- https://www.youtube.com/watch?v=kt5a-TXmWew -- Tech With Tim, 118,809 views, 19:38, read 2026-10-18. 242 words/min; first on-screen action at 128 s (transcript heuristic); 10 chapters.

## How they open

Topic used after a rerun: the first search returned a team interview from
the Claude channel that is not a subagents tutorial, so it was replaced. Of
the three kept, only Developers Digest is a pure subagents tutorial; Nate
Herk covers the newer agent-teams feature and contrasts it with subagents,
and Tech With Tim gives subagents the first of nine sections.

Nate Herk is the only one that opens on a result: a three-agent team builds
a landing page on screen, a QA agent sends work back, then the promise list.
Developers Digest opens on the release news and a one-paragraph definition
(own context, own tools, own prompt), then goes straight to the command.
Tech With Tim lists all nine features, promises a demo for each, and points
to a free written guide before the first one.

## The order they explain things

- Developers Digest: the agents command, project versus machine-wide scope,
  generate with Claude, choose tools (including connected servers), pick a
  colour, read the generated Markdown (name, tools, description, prompt),
  where the folder lives, a second agent for web research, then a build
  that uses the research agent first.
- Nate Herk: a finished team run, how a team differs from subagents (a lead
  plus teammates who message each other versus workers that only report
  back), enabling the experimental flag, saving the docs locally, a prompt
  pattern, dos and don'ts, a live run in the editor, the same run in a
  split terminal, rules, pitfalls, and when not to use teams.
- Tech With Tim: what Claude already does on its own, then defining custom
  agents as files in a folder, checking they loaded with the context
  command, a review request that calls the reviewer agent, and why the main
  thread stays smaller. Then skills, hooks and the rest of the list.

## Examples and analogies

- A Next.js front-end specialist and a web researcher feeding a SaaS landing
  page (Developers Digest).
- A three-role team of front end, back end and QA that loops until QA
  passes (Herk); a researcher, strategist and critic auditing a workspace.
- A reviewer agent and a debugger agent, the reviewer called on a pull
  request (Tech With Tim).
- The team lead described as a project manager handing out tasks (Herk).
- A full context window making the model worse, used as the reason to push
  side work into a separate agent (Tech With Tim).

## Beginner mistakes they address

- Confusing subagents with teams: subagents report back and do not talk to
  each other (Herk).
- Assuming a new agent knows the conversation so far; it only gets the
  prompt it is handed, so the goal must be in it (Herk).
- Letting two agents edit the same file (Herk).
- Too many agents at once, multiplying cost (Herk).
- Giving every agent every tool when a narrower set would do; read-only
  reviewers are the example (Developers Digest, Tech With Tim).
- Making custom agents for work that does not need its own context (Tech
  With Tim).
- Killing a team instead of letting each member save and stop (Herk).

## Pacing

Measured above: from about 225 to 258 words a minute, all fast. Developers
Digest has no detectable first action by the heuristic, though it opens the
command within the first half minute; Herk's first action comes after a
three-minute demo and explanation; Tech With Tim reaches the editor after
the feature list. Herk and Tech With Tim name chapters by topic; Developers
Digest's chapter names misspell the product. Two of the three carry sponsor
reads, one inside the build.

## What they do badly, and what we do better

- Only Developers Digest shows the agent file line by line, and quickly. We
  open the file and read the description and tool list slowly, since those
  decide when and how the agent runs.
- Herk's main topic is teams, a separate experimental feature; a beginner
  could leave thinking it is the same thing. We name the difference in one
  sentence and stay on subagents.
- Nobody shows a subagent being chosen wrongly or not at all, and how to fix
  the description. We do that on camera.
- Very fast speech over moving screens. We keep our pace and hold each file.
- Sponsor reads mid-build; we have none.
- Developers Digest builds before showing what the research agent returned
  on its own. We show the agent's output first, then what the main session
  did with it.

## What we take (structure only)

1. Open on a result: the main session handing one job to a named agent and
   getting a short answer back.
2. Define a subagent in one line by what it gets (its own context, tools and
   prompt) and what it returns.
3. Create one with the agents command, then open the file it wrote and read
   each field.
4. Narrow the tools on camera and explain why (a read-only reviewer).
5. Run it twice: once called by name, once picked by its description.
6. Close on when not to use one, and mention teams only as a separate thing.
