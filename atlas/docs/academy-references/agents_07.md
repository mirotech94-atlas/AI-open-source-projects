# Study note: agents_07

Topic searched: "single agent vs multi-agent AI tutorial" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=bzWI3Dil9Ig -- "My Multi-Agent Team with OpenClaw", Brian Casel, 787,530 views, 14:28, read 2026-11-25. 203 words/min; first on-screen action at 711 s (transcript heuristic); 9 chapters.
- https://www.youtube.com/watch?v=O2gerCxEXvc -- "Generative AI vs AI agents vs Agentic AI", codebasics, 742,470 views, 10:09, read 2026-11-25. 164 words/min; first on-screen action at not detectable; 8 chapters.
- https://www.youtube.com/watch?v=ow1we5PzK-o -- "The Multi-Agent Architecture That Actually Ships" (Luke Alvoeiro, Factory), AI Engineer, 304,909 views, 18:30, read 2026-11-25. 164 words/min; first on-screen action at not detectable; 11 chapters.

## How they open

Brian Casel opens on a desk: a small computer set up only to run a team of
agents with named roles, then a long list of questions he had to settle
(where to run it, cost, chat tool, safety, purpose). Codebasics opens on a
promise to separate three buzzwords in simple language. The AI Engineer
talk opens with a claim that human attention, not model smarts, limits
software work, and promises you can assemble agent teams by the end. None
opens with "start with one agent".

## The order they explain things

- Brian Casel: what the tool is and why always-on helps, where to run it,
  giving each agent its own accounts like a new hire, token cost, which
  chat app, four agents with roles and a model chosen per role, a shared
  workspace, a dashboard he built for tasks and spend, then his use cases.
- Codebasics: a model that only writes text, then the same model with a
  tool to fetch live data, then an agent that decides and acts, then a
  bigger job where one agent calls another (a flight agent calling a visa
  agent), and finally the point that control and limits are still needed.
- Alvoeiro (Factory): five ways agents can work together (hand a job to a
  helper, one builds and another checks, direct talk, negotiation over a
  shared resource, broadcast), then one system with a planner, workers
  with clean context and independent checkers, a "done" contract written
  before any code, structured handoffs, jobs run one after another, and a
  different model chosen per role.

## Examples and analogies

- A new hire who gets their own machine, email and only the access they
  need (Brian Casel).
- A brain that gets a hammer and a screwdriver as tools (codebasics).
- A flight agent that hands the visa question to a visa agent
  (codebasics).
- A builder whose own tests were written after the code, so they confirm
  choices instead of catching bugs (Alvoeiro).
- A fresh reviewer who is not invested in the code, like human code review.

## Beginner mistakes they address

- Giving an agent your personal machine or logins.
- Running up large token bills before measuring.
- Running many agents at once on connected work, so they overwrite each
  other and repeat work (Alvoeiro found running jobs one after another
  safer, with parallel reading only).
- Letting the same agent that wrote the work also approve it.
- Treating the buzzwords as strict; codebasics says the definitions vary.
- Giving a fully autonomous agent sensitive credentials.

## Pacing

Brian Casel is quick, about 203 words a minute, and personal. Codebasics
is calm, about 164, with a diagram built up piece by piece. Alvoeiro runs
about 164 and is dense, for engineers. Two videos drift from our topic:
Casel's is mostly a tour of one product, its machine, costs and chat
setup, and Alvoeiro's describes a coding system built over days, far
beyond a small business's first agent. Neither compares "one agent with
tools" against "several" for a beginner.

## What they do badly, and what we do better

- All three celebrate more agents. Codebasics and Alvoeiro hardly mention
  when one agent is enough. Our script leads with Anthropic's advice to use
  the simplest solution and add complexity only when needed.
- Heavy jargon (gateway, handoff, validators, negotiation). We use
  "helper", "manager" and "workers".
- Costs appear late or in passing. We give each extra agent's cost, extra
  testing, errors carried along and more runs, its own scene.
- No quiz or exercise. We pause on "count the skills your job needs".
- Two of three are tied to a product. Ours names n8n only for the one
  practical step: an agent as a tool of another agent.

## What we take (structure only)

1. Start with the simplest thing that works.
2. One agent with a few tools first.
3. Manager and workers as the first split pattern; checking by a separate
   agent is a worthwhile idea, left out because we have no official page
   we can cite for it.
4. Split on signs: too many tools, very different jobs, independent parts.
5. Count the real cost of each extra agent before adding it.
6. Our script already follows this order; no edit needed.
