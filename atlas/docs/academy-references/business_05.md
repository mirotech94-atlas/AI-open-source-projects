# Study note: business_05

Topic searched: "n8n ai social media post approval" (2026-10-19). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=QovlUE_VlWQ -- Nate Herk | AI Automation, 118,004 views, 9:59, read 2026-10-19. 260 words/min; first on-screen action at 19 s (transcript heuristic); 6 chapters.
- https://www.youtube.com/watch?v=LFO4cP0KMwk -- Ed Hill | AI Automation, 84,670 views, 19:44, read 2026-10-19. 156 words/min; first on-screen action at 637 s (transcript heuristic); 8 chapters.
- https://www.youtube.com/watch?v=Vw6C_ztofBU -- Bo Sar, 7,415 views, 8:29, read 2026-10-19. 200 words/min; first on-screen action at 30 s (transcript heuristic); 6 chapters.

## How they open

All three open on the finished thing. Nate Herk promises posting to many
networks from one workflow and starts on the canvas within seconds. Ed Hill
plays a fast montage of the same video landing on several apps, then a
second run, before any building. Bo Sar flips a status in his own content
base and waits on camera for the reel to appear. None of them shows a
person checking a caption before it goes out.

## The order they explain things

- Herk: a third-party posting service and its two actions (upload media,
  create post), connecting accounts in that service, one live run to two
  networks, then a walk back through the steps: the sheet row marked ready,
  a Drive file ID step, per-network settings, the sheet marked posted.
- Hill: the live demo, then a build from zero: a daily schedule, the sheet
  read with two filters and only the first match, a model step that writes
  one caption per network as JSON, a config node for account IDs, upload,
  one request per network pasted in as curl, a merge, the sheet updated.
  It ends by turning the whole thing into a sub-workflow.
- Sar: a webhook fired by a status change in his database, fetching the
  record, downloading the video, a community posting node, then writing the
  post link back to the record. A bonus on auto-replies to comments.

## Examples and analogies

- Glass-cutting short videos and a banana topic row as the test content
  (Herk, Hill).
- A personal reels base with a stage per video (Sar).
- The sheet as the to-do list for the posting robot: fill rows, it empties
  them (Herk and Hill both frame it this way, without a real analogy).

## Beginner mistakes they address

- Verified community nodes switched off in the admin panel, so the node
  isn't found (Herk).
- Posting every ready row at once instead of one per run: both use the
  sheet's first-match option (Herk, Hill).
- Error handling that only covers one node, said honestly by Herk.
- A model account without credit, so the caption step fails (Hill).
- Invalid JSON when a caption is dropped into a request body, fixed with a
  pasted snippet (Hill).
- The test webhook URL left in place of the production one (Sar).

## Pacing

Herk talks very fast, about 260 words a minute, and shows the result in the
first minute. Hill is slower, about 156, but his first real build step comes
more than ten minutes in, after a long demo. Sar is short and conversational,
around 200, and reaches his canvas in half a minute. Chapters are named by
step in all three.

## What they do badly, and what we do better

- All three post with no human check: the AI caption goes straight out. Our
  lesson is built around the approval step, and nothing posts without a yes.
- Each leans on a paid third-party posting service, a discount code or a
  members' community. We use the nodes the n8n docs describe and sell
  nothing in the lesson.
- Hill pastes long prompts and curl snippets from a document the viewer
  can't see explained. We show the few settings that matter on screen.
- Claims about views on brand-new accounts are anecdotes. We make no reach
  or engagement claim at all.
- None names what happens when the approver never answers. We say it.

## What we take (structure only)

1. Open on the result: one approval message, then the post it allowed.
2. The sheet first, as the plan: date, topic, status, one row per post.
3. Build in the order the data flows: schedule, read, filter to today and
   ready, draft, approve, post, mark the row.
4. One clear failure and its fix where it happens (the approval that never
   resumes).
5. Close on the rule that nothing leaves without a person's yes.
