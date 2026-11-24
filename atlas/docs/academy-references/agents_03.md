# Study note: agents_03

Topic searched: "agentic ai explained" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=FwOTs4UxQS4 -- Jeff Su, 5,452,594 views, 10:09, read 2026-10-18. 167 words/min; first on-screen action at 139 s (transcript heuristic); 6 chapters.
- https://www.youtube.com/watch?v=qU3fmidNbJE -- Tina Huang, 1,617,925 views, 21:27, read 2026-10-18. 207 words/min; first on-screen action at 124 s (transcript heuristic); 8 chapters.
- https://www.youtube.com/watch?v=EDb37y_MhRw -- IBM Technology, 1,469,634 views, 7:18, read 2026-10-18. 131 words/min; first on-screen action at not detectable; 7 chapters.

## How they open

The Jeff Su video opens with a rapid montage of people repeating the
buzzword, then names its audience (non-technical people who already use
chat tools) and promises a three-step learning path before the first
definition. The Tina Huang video opens on her own credentials (courses,
papers, a long notes file), promises a quiz at the end and reads out the
outline, with a sponsor named inside the first minute. The IBM video opens
with the question in its title and starts answering it at once at a
lightboard. None shows a working agent first; the closest is Su naming the
three levels he will climb.

## The order they explain things

- Jeff Su: level one, a plain chat model (input in, text out), with two
  limits named (no private data, waits to be asked); level two, a workflow
  where a human fixes the path (calendar lookup, then weather, then speech),
  with retrieval presented as one kind of workflow; a real no-code workflow
  in Make; level three, the human decision maker replaced by the model,
  framed as reason plus act plus iterate; a vision demo; a recap diagram of
  all three levels.
- Tina Huang: what is not an agent (one-shot prompting), then the agentic
  workflow as a loop, then full autonomy as a third level; four design
  patterns (reflection, tool use, planning, multi-agent) with a mnemonic;
  the parts of a single agent (task, answer, model, tools); multi-agent
  layouts (sequential, hierarchical, hybrid, parallel, asynchronous); a
  no-code assistant built in n8n; business ideas; a closing quiz.
- IBM: generative AI as reactive (waits for a prompt, makes content), then
  agentic AI as proactive with a perceive, decide, act, learn cycle; the
  shared foundation (a language model as the reasoning engine); use cases
  for each; chain-of-thought as the mechanism; a closing look at systems
  that mix both.

## Examples and analogies

- A question about the next coffee meeting that a chat model cannot answer
  because it has no calendar access (Su).
- A follow-up weather question that breaks a calendar-only workflow, to
  show a fixed path (Su).
- A daily news-to-social-post pipeline built in Make, where the human has
  to rewrite the prompt when the post is weak (Su).
- An essay written in one go compared with outline, research, draft and
  revise (Huang).
- A travel planner agent described by its task, expected answer, model and
  tools (Huang).
- A team of specialists compared with one person juggling everything, as
  the case for several agents (Huang).
- A shopping agent that tracks prices and handles checkout, against a
  creator who reviews every generated draft by hand (IBM).
- An agent planning a conference by talking itself through the steps (IBM).

## Beginner mistakes they address

- Calling any multi-step automation an agent; Su's test is who decides the
  path, and many steps with a human deciding is still a workflow.
- Expecting a chat model to know private or live data it was never given.
- Treating retrieval as something other than a workflow pattern.
- Assuming more agents is always better; Huang warns that complexity adds
  chaos and makes the system harder to steer.
- Thinking the terms (ReAct, RAG, reflection) are harder than they are.

## Pacing

Measured above: from about 131 to 207 words a minute. The IBM lightboard is
the slowest and has no screen action at all; Su is brisk but holds one
running example across all three levels; Huang is the fastest and longest,
covering many patterns in sequence with little time on each. Su and IBM
name chapters by concept; Huang's chapters follow her outline and include
a separate quiz chapter.

## What they do badly, and what we do better

- No video shows an agent's loop on screen with its actual steps. We open
  on a real run log where the model picks a tool, reads the result and
  picks again.
- Huang carries a sponsor read and a shopping list of patterns; much is
  named, little is shown. We teach three ideas and show each one.
- Huang lists saved card details as a tool for a travel agent without a
  word on approval or risk. We always say which actions need a human yes.
- The IBM video presents a shopping agent checking out by itself as a plain
  good. We say what an agent should never do unattended.
- Su's montage hook costs time before any content. Our hook is the
  workflow-versus-agent test itself, in the first sentence.

## What we take (structure only)

1. Climb a ladder: plain model, then a fixed workflow, then an agent, with
   one running example carried through all three.
2. Give one test that separates a workflow from an agent: who decides the
   next step.
3. Break the fixed workflow with a follow-up question it was not built for,
   to show why the agent level exists.
4. Name the loop (reason, act with a tool, observe, repeat) and show one
   pass of it on screen.
5. End on a recap diagram of the three levels and a few check questions.
