# Study note: security_01

Topic searched: "prompt injection explained ai agents" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=jrHRe9lSqqA -- IBM Technology, 301,477 views, 10:57, read 2026-10-18. 189 words/min; first on-screen action at not detectable; 5 chapters.
- https://www.youtube.com/watch?v=Lolub-mJHFk -- Box, 59,410 views, 9:02, read 2026-10-18. 215 words/min; first on-screen action at 35 s (transcript heuristic); 5 chapters.
- https://www.youtube.com/watch?v=5ZA1lTxTH3c -- IBM Technology, 34,695 views, 10:07, read 2026-10-18. 177 words/min; first on-screen action at not detectable; 6 chapters.

## How they open

All three open on a story of an attack, before any definition. IBM's prompt-injection episode starts with a widely reported dealership chatbot talked into agreeing to sell a car for almost nothing. IBM's agent episode opens on two presenters discussing a book-buying agent that overpaid, as a mystery to solve. Box opens with one host giving the other an urgent, no-questions instruction, then reveals that this was the trick being explained.

## The order they explain things

- IBM (injection): the chatbot story, what a prompt is, a security project's ranking of the top risk, social engineering of people compared with a model, jailbreaks and role-play, direct versus indirect injection, what can go wrong, then a layered list of defenses.
- IBM (agents): how a browsing agent is built (model, page reading, computer use, stored preferences and payment details), reading its chain-of-thought log, finding hidden text on the page that steered it, the risks, then defenses and where agents are going.
- Box: prompt injection versus data poisoning (instructions hidden in a document or email the agent reads), why agents fall for it (helpful by default, no firm line between orders and data), where it happens in a business, then guardrails.

## Examples and analogies

- The dealership chatbot and its absurd deal (IBM).
- A shopping agent steered by text hidden on a seller's page (IBM).
- Social engineering a person, used as the model for tricking a model (IBM, Box).
- An email or file carrying instructions an agent later reads (Box).

## Beginner mistakes they address

- Thinking a smart agent can tell its owner's orders from text it reads; it often cannot (Box, IBM).
- Giving an agent payment details and letting it act with no check (IBM agents).
- Relying on one filter; the defenses are layered (IBM).
- Not keeping logs, so nobody can see why the agent acted (IBM agents).

## Pacing

Measured: 9 to 11 minutes. The IBM videos are calm lightboard talks with two short role-play scenes; Box is a two-person conversation. Every chapter is named by its question or idea, which is searchable; none uses numbered steps.

## What they do badly, and what we do better

- The defense comes last, after the whole attack; our rule is to name the defense first and show the approval step stopping the action from the opening seconds.
- Attacks described on third-party systems; ours runs only on our own test inbox and test data.
- Defenses listed, not verified; we verify the fix on camera (the content passed as labelled data, the approval blocking the forward).
- Some framing leans on alarm; we keep a calm tone, no fear music, no red flashing.

## What we take (structure only)

1. Open on the result: the agent about to act on a hidden instruction, and the approval step stopping it.
2. Name the defense (label data as data; approve actions) before the attack is fully shown.
3. Then the attack on our own test inbox, briefly.
4. Read the log to show why the agent was fooled.
5. Apply the fix and verify it on camera, then a short recap. Everything in this episode needs a recording on Miro's own test setup, so it stays held until that exists.
