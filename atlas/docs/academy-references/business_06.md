# Study note: business_06

Topic searched: "n8n AI email triage automation gmail" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- How I Built an AI Agent to Automate my Emails in n8n (Step by Step, No Code) -- https://www.youtube.com/watch?v=KIJHRq_Tg6o -- Nate Herk | AI Automation, 133,986 views, 22:37, read 2026-11-25. 230 words/min; first on-screen action at 77 s (transcript heuristic); 10 chapters.
- I Built an AI Agent that Automated my Inbox with n8n (No Code) -- https://www.youtube.com/watch?v=JUx2ZfNfD64 -- Nate Herk | AI Automation, 48,722 views, 20:20, read 2026-11-25. 240 words/min; first on-screen action at 227 s (transcript heuristic); 6 chapters.
- I Built an Inbox AI Agent That Handles Your Emails (No-Code, n8n) -- https://www.youtube.com/watch?v=cHFKgOmr6rY -- Ed Hill | AI Automation, 12,491 views, 17:45, read 2026-11-25. 187 words/min; first on-screen action at 184 s (transcript heuristic); 9 chapters.

## How they open

All three start with the finished result before any building. The first
Nate Herk video summarises the flow in one breath (watch the inbox, decide if
it is support, draft a reply in the same thread, ping you in chat) and runs
a live test inside the first minute, then says "step by step" and begins. His
second video opens on the whole canvas and promises you will understand every
branch by the end. Ed Hill opens on the outcome in plain words: nine labels,
and a draft for anything that needs a reply. All three then spend a stretch
on their own community or templates before the build, which we skip.

## The order they explain things

- First Nate video: Gmail trigger, a set-fields step that keeps sender,
  subject, body and thread, a yes/no check for support mail, a support agent
  with a knowledge base, a create-draft tool, the agent prompt, a test, then a
  chat notification and switching the workflow on.
- Second Nate video: the labelling part is covered elsewhere; this one walks
  each label's branch in turn (urgent, support, promotional, billing), each
  with its own demo and its own chat message.
- Ed Hill: connect Google, create the labels in Gmail first, a model that
  returns a label, add the label to the message, then a reply agent for the
  "to respond" label, and finally looping over past emails.

## Examples and analogies

- An urgent email gets only a draft, because a person should stay involved
  (second Nate video).
- A promotional email gets a short summary and no auto-reply, since some of
  those are worth reading.
- A sign-off example teaches the model to name the sender from the email's
  closing line.
- Ed Hill's nine labels, each with a plain instruction on when to use it.

## Beginner mistakes they address

- Leaving the simplified view on, so long emails get cut off and the agent
  lacks context.
- Not keeping the thread id, so the draft lands outside the original
  conversation.
- Forgetting to tick the option that returns structured output, which
  lumps subject and message into one field.
- Naming a node differently from the video, which breaks later references.
- Creating no matching labels in Gmail before asking the AI to apply them.
- Leaving the workflow inactive, so nothing checks the inbox.

## Pacing

Fast. Both Nate videos run around 230 to 240 words a minute and spend the
first minutes on demo and promotion; his first on-screen build step is at
about 77 s in the first video and about 227 s in the second. Ed Hill is
slower, near 187, with short captions but a long community section up
front. Each one runs 17 to 22 minutes because of the vector store, extra
branches and cost breakdown.

## What they do badly, and what we do better

- They build a lot at once: a vector database, chat alerts, four branches or
  nine labels. A beginner cannot finish that in one sitting. We use three
  labels and one reply path.
- The second Nate video lets the AI send support replies on its own. We keep
  every reply a draft you press send on, and say so in a quiz.
- Paid extras (a vector store, a hosted model router) appear without a word
  on cost. We use only a chat model and Gmail.
- Nobody explains what happens to mail that matches no label. We cover the
  Other branch and a weekly look at that pile.
- Ed Hill's drift: much of the second half is looping through old email and
  a cost breakdown, which is beyond our topic.

## What we take (structure only)

1. Show the finished inbox first, then build.
2. Keep the thread attached so the draft sits in the right conversation.
3. Create labels before the AI uses them, and test with your own mail.
4. Urgent or sensitive mail gets a draft only, never an auto-send.
5. Name the on switch: the workflow must be active to watch the inbox.
