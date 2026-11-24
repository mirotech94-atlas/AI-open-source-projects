# Study note: agents_04

Topic searched: "components of an ai agent tools memory planning" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=hLJTcVHW8_I -- AI Alfie , 613,350 views, 6:52, read 2026-10-18. 181 words/min; first on-screen action at 280 s (transcript heuristic); 11 chapters.
- https://www.youtube.com/watch?v=X4FVEEegCbk -- IBM Technology, 224,571 views, 9:10, read 2026-10-18. 147 words/min; first on-screen action at 132 s (transcript heuristic); 0 chapters.
- https://www.youtube.com/watch?v=BacJ6sEhqMo -- IBM Technology, 152,002 views, 10:40, read 2026-10-18. 138 words/min; first on-screen action at not detectable; 0 chapters.

## How they open

The AI Alfie video opens with the presenter naming himself and his company,
then a general line about agents moving from answering to acting, and goes
straight to a definition; nothing is shown. The IBM skills-versus-MCP video
opens by naming its four items and promising a rule for when to use each,
then sets up one concrete problem (a web page throwing a server error) that
the whole video keeps solving. The IBM memory video opens by saying agents
remember in more than one way and frames the four types through human
memory first. Only the server-error video gives the viewer a problem in the
first minute; none shows an agent doing anything.

## The order they explain things

- AI Alfie: what an agent is (software that works toward a goal rather
  than a fixed list of steps), how it differs from ordinary software, how
  it differs from a bare language model (frozen training data, made-up
  answers), then four components in turn: planning, tools, memory and
  outside knowledge, carrying out actions; then risks and the need for
  human control; a one-line recap.
- IBM, skills vs MCP vs RAG vs memory: the naive fix (pour everything into
  the context window) and why it fails, then a skill as a written procedure
  with judgment about when to escalate, then MCP as the way to reach live
  systems, then retrieval over documents a person stored, then memory as
  what the agent learned itself; a closing rule of thumb that sorts the
  four by where the knowledge comes from.
- IBM, memory types: human memory as the frame, then a research framework
  with four types: working memory (the context window), semantic memory
  (a project instructions file), procedural memory (skills loaded only
  when needed), episodic memory (distilled notes from past sessions, and
  the problem of forgetting); then which agents need which types.

## Examples and analogies

- Booking a meeting with a colleague given as a goal, with the agent
  listing its own steps (Alfie).
- A question about last weekend's sports result that a bare model cannot
  know and may invent an answer to (Alfie).
- A help-desk chat that searches a company's own answers before replying,
  as retrieval (Alfie).
- One checkout page error carried through all four methods: a triage
  procedure, a live metrics call, a documents search, a note from the last
  time it broke (IBM skills video).
- Working memory compared with computer RAM: fast, small, wiped at the end
  (IBM memory video).
- A thermostat, a password-reset bot and a coding agent, ranked by how many
  memory types each needs (IBM memory video).
- The presenter's own story of hours lost debugging the wrong cluster, used
  as the case for remembered experience (IBM memory video).

## Beginner mistakes they address

- Thinking more context is always better; both IBM videos say stuffing the
  window degrades results and the model loses track.
- Mixing up retrieval and memory; the skills video separates them by who
  wrote the knowledge down.
- Assuming a chat app's web search is part of the model itself (Alfie).
- Saving every transcript and calling it memory; the memory video says
  distilled notes are what help.
- Giving every agent every kind of memory when a simple one needs only the
  context window.

## Pacing

Measured above: from about 138 to 181 words a minute. The two IBM videos
are lightboard talks, slow and drawn as they go, with no screen recording.
AI Alfie is quicker and is a talking head with chapter cards for each
component. None of the three shows software running; all are concept
explainers. Alfie names chapters by component; the two IBM videos have no
chapters.

## What they do badly, and what we do better

- Nobody shows an agent using a tool, a memory or a plan. We show one run
  where each part is visible: the plan, the tool call, the stored note.
- Alfie's risk segment uses an extreme science-fiction example instead of a
  real failure. We name the ordinary risks (a wrong tool call, a stale
  memory, an action taken without asking) and where a human approves.
- Alfie quotes a model's training cutoff as a current fact, which dates the
  video. We never state a dated product fact without its date.
- The IBM videos assume terms like context window and vector database. We
  define each in one line before using it.
- No video gives the viewer something to try. We end with a small task.

## What we take (structure only)

1. Carry one concrete problem through every component, as the server-error
   video does, instead of a separate example for each.
2. Start from the naive approach (put everything in the prompt) and show
   why it fails before naming the parts.
3. Sort the parts by one question: where does this knowledge or ability
   come from (written by a person, learned by the agent, a procedure, a
   live system).
4. Close with a short sorting rule the viewer can repeat.
5. Match the parts to the job: show a simple agent that needs little and a
   bigger one that needs all of it.
