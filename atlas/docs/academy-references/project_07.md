# Study note: project_07

Topic searched: "n8n share workflows projects team" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=A0OwvNOLNlw -- "How to Build a WhatsApp Agent with n8n (credential tutorial)", Nate Herk | AI Automation, 426,985 views, 7:03, read 2026-11-25. 273 words/min; first on-screen action at 16 s (transcript heuristic); 4 chapters.
- https://www.youtube.com/watch?v=QovlUE_VlWQ -- "This Workflow Auto-Posts to 9 Different Socials (free template)", Nate Herk | AI Automation, 121,852 views, 9:59, read 2026-11-25. 260 words/min; first on-screen action at 19 s (transcript heuristic); 6 chapters.
- https://www.youtube.com/watch?v=mw1V2GoYHsk -- "How to Deliver Your n8n Project to Clients Like a PRO!", Austin Reed, 59,404 views, 6:30, read 2026-11-25. 196 words/min; first on-screen action at 199 s (transcript heuristic); 6 chapters.

## How they open

Nate Herk opens both videos on the finished result and a promise of speed:
a business number answered by an agent in one, nine social accounts posted
to by one workflow in the other. He is inside the editor within about twenty
seconds. Austin Reed opens on a question instead: everyone talks about
selling automations, nobody about what happens after the sale. His first
screen action comes late, over three minutes in, because his video is talk.
Honest note: none of the three is about sharing workflows inside a team. Two
are credential walk-throughs for an outside service, and one is about handing
a build to a paying client. Only the credential and hand-over ideas carry
over to our topic.

## The order they explain things

- Nate's WhatsApp video: open the trigger, see it wants a credential, go to
  the vendor's developer site, create an app, copy an ID and secret into
  n8n, save and wait for the green test message. Then a second credential
  for the sending node, a test number, a test run, and wiring the agent's
  reply into the send step.
- Nate's posting video: sign up to the posting service, connect the social
  accounts there, copy one key, paste it into a credential, reuse that
  credential on every node, then a live run and a tour of per-platform
  quirks.
- Austin Reed: self-hosted or cloud, who runs the server, how the workflow
  is delivered (installed by you on their instance with their live
  credentials), how keys travel, and a short guarantee and upkeep section.

## Examples and analogies

- One credential reused across ten nodes, so a single paste serves them all
  (Nate, posting video). It is the best picture of why credentials are
  separate from workflows.
- A spare edit node at the top of a workflow that holds a key once, so
  several request nodes read it from one place (Austin Reed).
- Test number first, real number later: build against the safe thing, then
  swap (Nate, WhatsApp video).
- Warning that anyone who can message the agent spends your model credits
  (Nate, WhatsApp video).

## Beginner mistakes they address

- Creating a credential for the trigger and forgetting the sending node
  needs its own.
- Building on test keys and being surprised when live ones behave
  differently.
- Sending keys by email or text; Austin names a one-time-secret style tool
  or a password manager instead.
- Missing community nodes because a setting or version is out of date.
- Leaving a failure path half built; Nate says plainly his template's error
  handling only covers one case.
- Nobody says who owns the instance, so nobody knows who fixes it.

## Pacing

Nate speaks very fast, around 260 to 270 words a minute, with a sales
pitch and a community plug at the end of each video. Austin is calmer, about
196, and almost all talk with little on screen. For a lesson on trust and
access, the slower pace is the one to copy: each rule gets a sentence of its
own and a moment to land.

## What they do badly, and what we do better

- None shows two people in one n8n: who can open, edit, run or delete. We
  show roles and the share dialog step by step.
- Both Nate videos lean on a paid third-party service and a discount code.
  We stay with n8n's own sharing and name no sponsor.
- Credentials are pasted in plain view on screen in the credential
  walk-throughs. We teach that a teammate gets use of a credential without
  ever seeing it.
- Austin treats sharing as handing over the whole build to a client. We
  cover day-to-day teamwork on one assistant, and keep his best idea: keys
  live in one place, never in a shared file or a message.

## What we take (structure only)

1. Open on the problem someone else wants to use it, as Austin does with a
   question, then get on screen sooner than he does.
2. Decide who needs what before touching any button.
3. Share the workflow, then the credential, as two separate steps.
4. Show what a teammate sees when a credential is not shared.
5. Keep secrets out of anything committed; point to each person's own
   settings.
6. A short recap, with no pitch.
