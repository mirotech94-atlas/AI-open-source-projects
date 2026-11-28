# Study note: local_07

Topic searched: "Hermes Agent Claude Desktop MCP server" (2026-10-26). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=9GpWELm3_XI -- "Hermes Agent Explained In 5 Minutes", CodeHead, 403,637 views, 4:53, read 2026-10-26. 182 words/min; first on-screen action at 35 s (transcript heuristic); 5 chapters.
- https://www.youtube.com/watch?v=kovUM5wssAI -- "Hermes Agent Setup for $8/Month Cost Reduction: No Claude Rate Limits", Moe Lueker, 80,533 views, 11:53, read 2026-10-26. 226 words/min; first on-screen action at 151 s (transcript heuristic); 17 chapters.
- https://www.youtube.com/watch?v=qAss7qQk9W4 -- "How to Set Up Hermes Agent on Your Computer (Desktop App)", Teacher's Tech, 72,481 views, 21:19, read 2026-10-26. 181 words/min; first on-screen action at 355 s (transcript heuristic); 17 chapters.

## How they open

Honest drift first: none of the three videos covers Hermes as an MCP server
for Claude Desktop. The search found general Hermes introductions, so this
note studies how they teach Hermes itself and what a viewer already knows
when they reach our episode. CodeHead opens with a punchy definition (an
agent that lives on a server, answered by text message) and a quick pitch for
subscribing. Moe Lueker opens on a money claim, a very low monthly bill, and
lists everything the video will deliver. Teacher's Tech opens on what you
will have at the end, then states plainly that the software is free and only
the model costs money.

## The order they explain things

- CodeHead: what Hermes is, the two ideas that set it apart (memory across
  sessions, skills it writes for itself), a one-line install, where to host
  it, the skills hub, helper workers for parallel jobs, who it is for.
- Moe Lueker: comparison with other agents, rent a small server in one
  click, a setup wizard, pick a router account and a cheap default model,
  switch models mid-chat, then three copyable jobs (weekly research, a
  morning brief, a memory-backed researcher).
- Teacher's Tech: what it is, the desktop installer, sign in with a free
  account, a first chat, test the limits of the free plan, then what the paid
  plan adds, cheap main model, two safety switches, a messy-folder demo,
  invoices into a reusable skill, a small web page, memory across sessions,
  voice, scheduled jobs, and the catch that a sleeping laptop stops the agent.

## Examples and analogies

- Memory as a notebook that never closes, against a whiteboard wiped after
  every session (CodeHead).
- A fictional coffee roaster as the running demo, so each task looks like
  real work (Teacher's Tech).
- Reading the exact command before approving it, framed as the first habit
  of safe use (Teacher's Tech).
- A live cost screen after a long session, to prove the money claim (Moe).

## Beginner mistakes they address

- Setting the biggest model as the default, which burns credits because the
  whole context is resent every turn.
- Running an always-on agent on your main laptop, which exposes it to
  prompt injection.
- Leaving approvals on automatic in the first week.
- Pointing the agent at real files before watching it work on copies.
- Giving it your logged-in accounts too soon.
- Expecting scheduled jobs to run while the computer sleeps.

## Pacing

CodeHead is about 182 words a minute and finishes in five minutes with
little on screen. Moe Lueker is the fastest at about 226, mostly spent in
menus and wizards, and the first real action lands after two and a half
minutes. Teacher's Tech is about 181 but long, and the first action comes at
nearly six minutes because the explanation comes first.

## What they do badly, and what we do better

- All three skip MCP entirely, so nothing here confirms or contradicts our
  config steps. Our script rests on the official docs and the Claude Desktop
  connection guide, and says what it does not claim.
- Two are partly sponsored hosting pitches, which pulls the lesson toward a
  rented server. We stay on the viewer's own machine.
- Only Teacher's Tech talks about safety, and late. We put the risk of sending
  messages into the lesson, the same way: treat the link like a key.
- None gives a plain fix list when a tool does not show up. We do, in order:
  full restart, run the command by hand, check the JSON.

## What we take (structure only)

1. Say what the viewer will have working by the end, in the first lines.
2. Keep the demo small and concrete, one running example.
3. Name the risk before the viewer enables anything, and say to read before
   approving.
4. Give a pause point with one clear check that it worked.
5. Say plainly which parts the studied videos do not back, as the script's
   header already does.
