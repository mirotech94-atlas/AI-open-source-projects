# Study note: agents_08

Topic searched: "AI agent tools function calling tutorial" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=2L_XE6g3atI -- "Agents Tools & Function Calling with Amazon Bedrock (How-to)", AWS Developers, 378,052 views, 19:08, read 2026-11-25. 191 words/min; first on-screen action at 56 s (transcript heuristic); 6 chapters.
- https://www.youtube.com/watch?v=AQZOIr3511U -- "Fix Voice Agent Tool Call Latency With Async Function Tools", LiveKit, 364,103 views, 6:53, read 2026-11-25. 163 words/min; first on-screen action at not detectable; 0 chapters.
- https://www.youtube.com/watch?v=h8gMhXYAv1k -- "What is Tool Calling? Connecting LLMs to Your Data", IBM Technology, 68,989 views, 4:56, read 2026-11-25. 156 words/min; first on-screen action at not detectable; 3 chapters.

## How they open

AWS opens on what a model cannot do alone, such as telling the time or
doing sums reliably, and sends the viewer to a cloud console within the
first minute. IBM opens on a one-sentence definition and a whiteboard of
an app talking to a model. LiveKit opens with a recording of a phone call
where an agent goes silent for many seconds, then plays the fixed version.
Each hooks on a problem a beginner can picture.

## The order they explain things

- AWS: show the model failing at time and maths, create an agent, write
  its instructions, add a group of tools with a name, a short description
  and typed inputs, edit the function that runs behind them, then ask
  questions and read the step-by-step trace.
- IBM: the app sends messages plus a list of tool definitions (name,
  description, inputs); the model recommends a call; the app runs it and
  returns the answer; the model replies or asks for another call. Then a
  second style where a library runs the tools for you.
- LiveKit: the slow-tool problem, why the agent is blocked while a tool
  runs, one fix that lets it speak progress, then cancelling, duplicate
  calls and hand-offs between agents.

## Examples and analogies

- A weather lookup for one city, with the model reading the answer back
  (IBM).
- A "get time" tool and an "add two numbers" tool, with a tip that if a
  parameter's description makes sense to you, the model can probably use
  it too (AWS).
- A refund that takes about fourteen seconds, and the risk of paying twice
  if the model calls it twice (LiveKit).

## Beginner mistakes they address

- Trusting a model's own sums or clock instead of giving it a tool.
- Vague or missing parameter descriptions (AWS stresses they become part
  of the prompt).
- Marking every input as optional when it is required.
- Tool code that returns only a stock success message (AWS flags this as
  sample code to replace).
- Errors the model cannot act on; AWS sends back different messages for
  different failures.
- Long-running tools that leave a caller in silence.
- A write action stopped halfway or run twice (LiveKit).

## Pacing

AWS is the slowest to the point and the longest, about 191 words a minute,
with a pasted code block and a console tour. IBM is short and clean, about
156. LiveKit is tight, about 163, with a demo first. Two videos drift
from our topic: AWS teaches one vendor's console click path, and LiveKit
is about voice calls, speaking while a tool runs, which a text-only shop
agent does not face. Their ideas on checking a tool's trace and not
repeating a risky action are still useful.

## What they do badly, and what we do better

- AWS needs an account and a console; IBM needs the idea of an API. We
  need only a pen and one tool.
- IBM calls the description "extra information" and moves on. We make the
  description the main lesson, with a new-hire test.
- None of the three shows what a good result looks like (plain words, not
  raw identifiers). We do, and we cite Anthropic.
- Only AWS shows a trace, and it is hard to read. We say to watch which
  tool is chosen and the input passed.
- None offers a pen-and-paper exercise. We pause for rewriting one tool.

## What we take (structure only)

1. A tool is a name, a description and inputs; the model reads only that.
2. Describe it so a newcomer would understand, input by input.
3. Few tools, clear names.
4. Results in plain words; errors that say what to try.
5. Test with real tasks and read what the agent actually did.
6. The call-and-return loop (model recommends, your side runs, result
   goes back) is IBM's clearest part. Our script says the model picks a
   tool by its text; the full loop is not spelled out, so it could be a
   later addition. No edit now, as the lesson is about tool design.
