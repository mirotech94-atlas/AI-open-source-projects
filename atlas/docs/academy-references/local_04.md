# Study note: local_04

Topic searched: "hermes agent vllm local model setup" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=0ujh7hfutq0 -- IBM Technology, 108,184 views, 10:36, read 2026-10-18. 160 words/min; first on-screen action at 95 s (transcript heuristic); 4 chapters.
- https://www.youtube.com/watch?v=7JRHSo2F-Wk -- Alex Finn, 72,626 views, 24:07, read 2026-10-18. 209 words/min; first on-screen action at 85 s (transcript heuristic); 9 chapters.
- https://www.youtube.com/watch?v=5ctTkkJQpug -- Digital Spaceport, 68,083 views, 17:36, read 2026-10-18. 179 words/min; first on-screen action at 214 s (transcript heuristic); 0 chapters.

## How they open

One of the three is off topic. The IBM Technology video (0ujh7hfutq0) is a
general whiteboard explainer on llama.cpp versus vLLM; it never mentions
Hermes, an agent setup or the DGX Spark. It is kept only for how it frames
vLLM, and it is studied properly in local_05. Of the other two, only Alex
Finn uses a DGX Spark; Digital Spaceport runs Hermes against vLLM on a
four-GPU home server.

Alex Finn opens on the result: the agent already running on the Spark,
then a promise list (setup, local model, use cases), then a sponsor read
for NVIDIA before any step. Digital Spaceport opens on a joke line from the
game the agent built, then goes straight to the vLLM launch flags before
saying what hardware is needed. IBM opens on why people run models locally
(cost, outages, privacy) and promises a way to choose between two engines.

## The order they explain things

- Alex Finn: why local models, why the Spark, plug in headless, then a
  cloud-powered Hermes on the laptop is prompted to set up the Spark, add a
  private network, download a model and load it. A quick chat page proves
  the model answers, then a second Hermes profile is pointed at it, then
  three use cases. The viewer sees prompts, not commands; his agent found a
  llama.cpp server, so the video never actually shows vLLM.
- Digital Spaceport: the server's launch flags one by one, the memory it
  needs, credits to the people whose patches it uses, the agent opening a
  saved project, a long play of the game, a follow-up prompt, then a warning
  about which flag makes the server crash.
- IBM: local benefits, why llama.cpp exists (quantization, one-file
  models, CPU support), then what vLLM adds for many users, then that both
  speak an OpenAI-style API, then how to choose.

## Examples and analogies

- A new agent profile that answers to the wrong name until told (Finn).
- A daily stock-research report on a timer, a video turned into a
  newsletter, and a to-do app built by the agent (Finn's three use cases).
- A browser arcade game about a UFO beaming up farm animals, built and then
  revised by the local agent (Digital Spaceport).
- Rounding pi to two decimals, for quantization (IBM).
- Pancakes coming off a griddle at different times, for continuous
  batching (IBM).

## Beginner mistakes they address

- Starting the agent before the server reports it is ready (Spaceport).
- Not enough system memory beside the GPU memory for a large model.
- Using unmerged patches or speed flags that later crash the server.
- Thinking you must be technical to set up the box (Finn's pep talk).

## Pacing

Measured above: 160, 209 and 179 words a minute. Finn is the fastest and
the longest (24 minutes) but most of it is enthusiasm, not steps; his first
on-screen action comes after the sponsor read. Spaceport takes over three
minutes before anything happens on screen and has no chapters at all, with
several minutes of gameplay. IBM is steady, drawn on a board.

## What they do badly, and what we do better

- The one Spark video hides the setup inside prompts to a cloud agent, so
  a viewer cannot tell which address, port or engine is used. We show the
  endpoint, the curl check and the three config lines on screen.
- It never proves the agent is local: the main agent is a cloud model.
  We give a test (stop vLLM, and Hermes can no longer answer).
- No one mentions keeping the model's port off the network, and Finn adds
  remote access without a word on risk. We say keep port 8000 on the box.
- An investing report is offered as a beginner use case. Our first task is
  a harmless one: read a local text file.
- Spaceport reads out a wall of flags with no reason for each. We point to
  NVIDIA's agent-ready recipe instead of flags we have not checked.
- Neither says what to do when it fails; we end with a stuck section.

## What we take (structure only)

1. Open on the finished state: the agent replying from the local model.
2. Two terminals, two jobs: one serves the model, one talks to it.
3. Wait for the server's ready line before connecting anything.
4. A one-line proof that the wiring works before any real task.
5. Show the agent's tool calls as they happen, then start with one tool.
6. Close with the failure cases a beginner will meet first.
