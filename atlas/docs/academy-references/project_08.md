# Study note: project_08

Topic searched: "n8n executions debugging tutorial" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=Gxe_RfCRH-o -- "n8n Beginner Course (8/9) - Debugging", n8n, 83,845 views, 16:10, read 2026-11-25. 130 words/min; first on-screen action at 31 s (transcript heuristic); 6 chapters.
- https://www.youtube.com/watch?v=de_UK1YEyvY -- "Master AI Automation Workflows Debugging & Error Handling with Execution Logs | n8n Course Tutorial", GenAI Unplugged, 1,560 views, 19:18, read 2026-11-25. 194 words/min; first on-screen action at 122 s (transcript heuristic); 5 chapters.
- https://www.youtube.com/watch?v=E7PDYJkxno0 -- "n8n Tutorial for Beginners #6 | Workflow Execution, Debugging & Pin Data", Assistant Dial AI, 45 views, 7:15, read 2026-11-25. 122 words/min; first on-screen action at 33 s (transcript heuristic); 0 chapters.

## How they open

n8n's own course video recaps the previous lesson on error handling and
defines debugging as fixing what went wrong so it stays fixed, then names
the usual causes: a bad setting, a service that is down, or missing input
data. GenAI Unplugged opens with a story, you build, you test, you deploy,
and then something breaks, and defines a run before touching the screen. The
smallest channel opens on a one-line recap of earlier days and says every
workflow breaks sooner or later. All three talk for a while before the
first click; the n8n video is on screen at about half a minute.

## The order they explain things

- n8n's video: why runs fail, the idea that a run can finish with no error
  yet do nothing useful, then the tools in turn: debug in editor, retry,
  editing a node's output by hand, version history. Two worked cases follow
  in the editor: a missing ID in the incoming request, and a lookup that
  returns nothing and so silently stops the workflow.
- GenAI Unplugged: test run versus live run, the execution list and its
  filters, the storage settings, reading a run node by node, then two real
  failures (a malformed email address, a file where an image was expected),
  a list of common causes, and finally per-node retry and error settings.
- Assistant Dial AI: run nodes one at a time, see what an undefined value
  looks like, read the error details, point a workflow at a second
  workflow to be told about failures, and pin a node's data while building.

## Examples and analogies

- A run that ends green but did nothing, as the case that proves why
  failures must be made loud (n8n's video). It is the most useful example in
  the set.
- Copying the failed run's data back into the editor, so you debug with the
  real input instead of inventing a test (n8n).
- Seeing the exact input and output at each node and noting how much time
  that saves versus recreating the problem (GenAI Unplugged).
- A bad email address and a wrong file type, both found by opening the
  failed node and reading its message (GenAI Unplugged).
- Pinning a node so an AI step does not give a different answer on every
  test (Assistant Dial AI).

## Beginner mistakes they address

- Assuming a green status means everything worked.
- Retrying without checking which node actually failed; n8n's video says a
  retry starts at the failed node, so a mistake earlier needs a different
  route.
- Letting one bad record stop the rest, then fixing it by ignoring errors
  so completely that nobody hears about them (GenAI Unplugged shows both
  sides and then routes failures to a separate branch).
- Not knowing what n8n stores, since storage can be switched off per
  workflow.
- Testing against a fresh answer each time instead of pinned data.
- Misreading a type or undefined-value error as a mystery rather than a
  missing field upstream.

## Pacing

n8n's video is the slowest at about 130 words a minute and works one case
at a time, a good model. GenAI Unplugged runs near 194 and is long, with a
lecture-style first third. The small channel is slow, about 122, but halting:
it fumbles a demo and says so. Our script should keep n8n's calm rhythm and
stay under the length of the longest one.

## What they do badly, and what we do better

- Two are tutorials from a general course and teach a toy webhook or sheet,
  not an assistant someone depends on. We keep one real assistant through
  the whole series.
- The smallest video skips most of what matters, and its demo of failure
  alerts did not show up on screen. We build the alert and show the message
  it sends.
- GenAI Unplugged runs long and leans on a paid plan feature (searching
  inside run data). We stay with what every plan has.
- None puts the duplicate-work risk of a retry up front. We make it the
  warning of the retry step.
- Where videos mix manual test runs with live runs, a newcomer can lose
  track. We keep to live runs and the Executions tab.

## What we take (structure only)

1. Open on the worry: how would you know it broke.
2. Look first: the list, then the failed run, then the node.
3. Retry second, with the duplicate warning.
4. Make a quiet failure loud on purpose, using the run that ends green and
   did nothing as the reason.
5. Say what n8n keeps and for how long you can retry.
6. Pause, quiz, short recap.
7. Possible addition for later: copying a failed run's data into the editor
   before fixing it; check it against n8n's own page first.
