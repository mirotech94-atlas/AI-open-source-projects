# Study note: project_05

Topic searched: "n8n error workflow error trigger pin data tutorial" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=erSlRlwpr-g -- Mark Kashef, 37,581 views, 21:53, read 2026-10-18. 215 words/min; first on-screen action at 126 s (transcript heuristic); 30 chapters.
- https://www.youtube.com/watch?v=_UAbMrj7IM4 -- MagicOps AI, 18,380 views, 15:07, read 2026-10-18. 166 words/min; first on-screen action at 36 s (transcript heuristic); 12 chapters.
- https://www.youtube.com/watch?v=bbQ66vsUWPI -- Compass AI, 2,152 views, 4:14, read 2026-10-18. 170 words/min; first on-screen action at 9 s (transcript heuristic); 0 chapters.

## How they open

Topic used after a rerun: the first search ("n8n testing workflows before
going live production") returned three off-topic videos, so the topic was
sharpened to the error workflow and pin data. The rerun is only partly
better. Compass AI is off-topic too: it is a Pinecone RAG demo with no
testing or error handling at all. Mark Kashef is a tour of the new
prompt-to-workflow builder and only touches mock data for a few minutes.
MagicOps AI is a ten-tips list; three of its tips are ours (pinning and
editing output, the Error Trigger, test versus production webhook URLs).

None opens on a result. Mark Kashef opens on two minutes of excitement
about the new builder and the old hacks it replaces before any screen.
MagicOps AI promises ten tips that save hours and teases the two best ones
to keep you watching. Compass AI states its topic in one line and goes
straight to the Pinecone dashboard.

## The order they explain things

- MagicOps AI: an image inside a sticky note as a bonus, then pinning a
  node's output, editing the pinned data to fake a case the real data lacks
  (and typing data into a trigger), several triggers on one flow, disabling
  a node, schedule and cron, node notes, version history and saving, a
  data-extraction function, the Error Trigger workflow wired to a Slack
  message and linked in settings, webhook test and production URLs and
  methods, then keeping binary files across nodes.
- Mark Kashef: why the builder matters, the rollout note, prompting tips,
  a first prompt that builds a lead workflow, clearing credential errors,
  asking the side chat for mock trigger data, pinning it and running the
  workflow, reading which steps worked, then two larger builds.
- Compass AI: a Pinecone account and assistant, a PDF upload, a chat
  trigger with an agent, an HTTP tool imported from a curl command, a test
  question, then token cost and swapping the document for a smaller one.

## Examples and analogies

- A filter that looks for an admin user when the sample data holds only
  staff and managers, fixed by editing the pinned row (MagicOps AI).
- A sub-workflow trigger given hand-typed data so the parent need not run
  (MagicOps AI).
- An error handler that sends the error message, workflow name and link to
  a private Slack message (MagicOps AI).
- A webhook that answers on the test URL and fails once active, because the
  production URL is the one that counts (MagicOps AI).
- Mock booking data for a Calendly trigger, written by the side chat,
  pasted, pinned and run; a missing CRM login makes one step fail (Kashef).
- A form whose own test submission becomes the pinned sample (Kashef).

## Beginner mistakes they address

- Creating fake records in the real CRM just to test one branch, instead of
  editing pinned data.
- Running the parent workflow every time to feed a sub-workflow.
- Calling the test webhook URL after the workflow is live.
- A request method that does not match what the webhook expects.
- Finding out about a broken production flow only by accident.

## Pacing

Measured above: 166 to 215 words a minute. MagicOps AI is the calmest and
shows each tip in the editor within seconds of naming it, but ten unrelated
tips leave no single build to follow. Mark Kashef talks fast over a long
hype opening and his first screen action comes after two minutes; he has
30 chapters, most named by what happens. Compass AI is four minutes with no
chapters and reads like narration over a screen recording.

## What they do badly, and what we do better

- Nobody writes a test plan. They run the happy path once and move on. We
  show four planned runs, three of them broken on purpose, before going
  live.
- No one tests the error workflow honestly: MagicOps AI links it and says
  it will fire. We say the Error Trigger does not fire on manual runs, so
  the check is a real automatic failure.
- One video says n8n does not save on its own; the current docs say it
  auto saves and edits stay in draft until you publish. We read the docs on
  the day and say what publish changes.
- Nothing shows watching the first live runs. We end in the Executions tab.

## What we take (structure only)

1. Show the pinned sample first, then edit it to make the case real data
   does not cover.
2. Keep one workflow on screen and break it in a named, planned way per
   test.
3. Build the error workflow as its own small flow (Error Trigger, then one
   alert) and link it in the workflow's settings.
4. Put the alert's fields on screen: workflow name, failed node, message
   and a link to the run.
5. Name the test versus production URL trap at the moment of publishing.
6. Finish on the live run in the execution list, as proof it works.
