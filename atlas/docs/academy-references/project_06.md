# Study note: project_06

Topic searched: "n8n gmail trigger ai email reply" (2026-10-19). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=KIJHRq_Tg6o -- Nate Herk | AI Automation, 129,535 views, 22:37, read 2026-10-19. 230 words/min; first on-screen action at 77 s (transcript heuristic); 10 chapters.
- https://www.youtube.com/watch?v=nEJ3lYGaq70 -- Leon van Zyl, 58,208 views, 21:32, read 2026-10-19. 175 words/min; first on-screen action at 54 s (transcript heuristic); 12 chapters.
- https://www.youtube.com/watch?v=cHFKgOmr6rY -- Ed Hill | AI Automation, 12,140 views, 17:45, read 2026-10-19. 187 words/min; first on-screen action at 184 s (transcript heuristic); 9 chapters.

## How they open

All three open on the working inbox. Nate Herk narrates the whole flow in
one breath, then runs it: an email comes in, gets classed as support, a
draft appears in the same thread, and a chat message says so. Leon van Zyl
names his own pain (too many sponsorship emails) and lists the four things
the flow will do. Ed Hill shows his inbox already sorted into labels, with
a draft waiting, before anything else.

## The order they explain things

- Herk: the Gmail trigger and why he turns Simplify off, pinning the test
  email, a set node for the body and thread, a model that returns a true or
  false support flag, a switch, an agent with a knowledge base tool and a
  create-draft tool, a chat notification, then the not-support branch and
  a live run with the workflow switched on.
- Van Zyl: a Google project with the Gmail API enabled, the consent screen
  and client keys, the trigger with Simplify off, one context field built
  from sender, subject and body, an agent returning structured JSON, an If
  node, a record in a database, a model drafting the reply, then a reply
  sent straight away versus a draft saved in the thread.
- Hill: the trigger, a set node trimming the fields, an agent choosing one
  of nine labels with a structured parser, fetching label IDs, adding the
  label, then an If on the reply label leading to a drafting agent, and a
  second workflow that labels old mail in a loop. It ends on running costs.

## Examples and analogies

- A shop's support inbox with tracking and refund questions, answered from
  a policy document (Herk).
- Sponsorship requests to a channel, with sample rates the presenter says
  are made up (van Zyl).
- A personal inbox sorted into reply, meeting, collaboration and marketing
  labels (Hill).

## Beginner mistakes they address

- Leaving Simplify on, so the email body arrives cut short or missing.
- Forgetting to enable the Gmail API, or to publish the consent screen.
- A draft with no recipient, so Gmail won't send it until the To field is
  set.
- Replying with the wrong email type, so HTML tags show in the message.
- Not tying the draft to the thread, so it lands outside the conversation.
- Running every following node once per item when one item was wanted.

## Pacing

Herk is fast, about 230 words a minute, and his first build step comes a
little over a minute in. Van Zyl is steadier, about 175, and spends three
minutes on Google project setup before any n8n node. Hill is around 187
and the longest on prompts pasted from his own documents. All three are
twenty minutes or more.

## What they do badly, and what we do better

- Van Zyl sends the AI reply straight to the customer before showing the
  draft option. We never send without a person's approval.
- Herk's and Hill's drafts sit in Gmail with nothing that records who
  approved them. We approve in Slack, and log every request in one sheet.
- Each builds a separate email assistant from scratch. We reuse the Claude
  step this series already built, as one shared sub-workflow, so both
  channels keep the same rules.
- Hill's cost figures are his own measurements; we give none.
- All three spend minutes on paste-in prompts. We keep the prompt work in
  the step we already wrote.

## What we take (structure only)

1. Open on the result: one email answered after an approval in Slack.
2. Draw the plan first: two ways in, one shared step, one approval.
3. Build in data order: share the step, add the trigger, approve, reply,
   log.
4. Name the Simplify and Gmail API traps where they happen.
5. Close on the rule that nothing is sent without your yes.
