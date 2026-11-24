# Study note: agents_05

Topic searched: "ai agent safety guardrails human in the loop" (2026-10-19). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=L7FF8Zgab3M -- IBM Technology, 262,382 views, 11:34, read 2026-10-19. 162 words/min; first on-screen action at 53 s (transcript heuristic); 5 chapters.
- https://www.youtube.com/watch?v=9iS-YYLIXiw -- IBM Technology, 48,340 views, 10:44, read 2026-10-19. 133 words/min; first on-screen action at 235 s (transcript heuristic); 7 chapters.
- https://www.youtube.com/watch?v=NH0plIdqDMk -- IBM Technology, 30,849 views, 4:59, read 2026-10-19. 165 words/min; first on-screen action at 184 s (transcript heuristic); 0 chapters.

## How they open

All three are whiteboard explainers from one channel, so the openings rhyme.
The first opens on the gap between a chatbot that tells you how to book a
meeting and an agent that books it, and only reaches safety in its last
two minutes. The second opens on a single question (should the AI act
alone, or wait for a person?) and keeps returning to it. The third opens on
a problem statement: developers are asked to ship agents without being
identity specialists. None opens on a working guardrail.

## The order they explain things

- Agent loop video: chatbot versus agent, the loop (gather context, ask the
  model, call a tool, feed the result back, repeat until done), one open
  source agent's architecture (gateway, adapters, memory, tools, skills),
  then a short risk list: exposed instances, prompt injection from emails
  and pages, isolation, reviewing skills, protecting credentials.
- Human-in-the-loop video: a three-point spectrum (waits for approval,
  watched with a kill switch, fully autonomous), one example of each, then
  the three places a person can sit (training, tuning, run time), then run
  time patterns: confidence thresholds, approval gates, escalation queues,
  and the trade-offs of bottlenecks and inconsistent reviewers.
- Runtime security video: an agent's outside connections, why a long-lived
  key is risky, short-lived per-session credentials, the user's own sign-in,
  and a phone prompt for sensitive actions.

## Examples and analogies

- Supervised self-driving as the watched-but-autonomous middle of the
  spectrum; a scan flagged by software but diagnosed by a doctor as the
  approve-first end; high-speed trading as the no-human end.
- An HR agent that can remove employees, asking the user's phone before
  each removal.
- Coding agents that ask before changing files, named as a familiar
  approval gate.

## Beginner mistakes they address

- Giving an agent standing read and write access, so one injected
  instruction can reach everything.
- Hard-coding a permanent key into the agent.
- Treating text from an email or web page as if it were a command.
- Running an agent with full machine access on an exposed network.
- Installing add-ons without reading what they run.

## Pacing

Measured above: about 133 to 165 words a minute, calm and diagram-led. The
first two run ten to twelve minutes with named chapters; the third is five
minutes with none. Each draws its idea on the board as it speaks, so the
picture builds one box at a time.

## What they do badly, and what we do better

- Safety comes last or arrives as a list of warnings. We make it the whole
  lesson and give it as four rules a viewer can apply today.
- The runtime security video is written for enterprise engineers and leans
  on identity jargon. We speak to a small-business owner with one agent.
- None shows where the approval switch actually lives in a tool. We name
  the documented approval for one agent tool in n8n and what the reviewer
  sees.
- None says what undo cannot reach. We say plainly that a sent message
  can't be pulled back, which is why the approval comes first.

## What we take (structure only)

1. Open on the result: an approval message for one risky tool.
2. State why agents need guardrails in one short scene before any rule.
3. Teach the spectrum idea as rules, from least access to approvals.
4. Name outside text as data, with one plain example of an injected line.
5. End on logs and limits, and the honest limit of undo.
