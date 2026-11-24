# Study note: business_03

Topic searched: "n8n invoice payment reminder automation" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=iUEu9dnCcQo -- Zubair Trabzada | AI Workshop, 16,482 views, 22:47, read 2026-10-18. 198 words/min; first on-screen action at 63 s (transcript heuristic); 11 chapters.
- https://www.youtube.com/watch?v=s1hgeDf_xnY -- Pratik Joshi, 7,156 views, 15:55, read 2026-10-18. 222 words/min; first on-screen action at 206 s (transcript heuristic); 0 chapters.
- https://www.youtube.com/watch?v=hL7KUdZPLBs -- Grey Matterz, 4,596 views, 12:55, read 2026-10-18. 96 words/min; first on-screen action at 79 s (transcript heuristic); 10 chapters.

## How they open

The search found no human tutorial on polite follow-ups for unpaid invoices; the most-viewed n8n invoice videos are about reading incoming invoices. The note uses them for how they frame a small-business money workflow and where they put a person in the loop. Zubair Trabzada opens on a live demo: an invoice photo sent from a phone, processed, then a question back to the phone before it continues. Pratik Joshi opens with the problem (many invoices a day keyed into accounting software by hand). Grey Matterz opens with a long generic promise about saving time before any screen.

## The order they explain things

- Trabzada: the demo first, then the workflow tour: route by file type, extract the fields, parse with an AI service, store in a sheet, ask the person to confirm, mark it paid, file the original in Drive.
- Joshi: the scenario, the whole workflow at a glance, then node by node: a form upload, a text-extraction call, validation, building the import file, emailing it as the output.
- Grey Matterz: a messaging-app trigger and its developer credentials, downloading the file, an OCR call, a code step to pull fields, a sheet row, a Drive archive, an AI reply, a test.

## Examples and analogies

- An electronics shop invoice found online, used as the test document (Trabzada).
- An accountant receiving dozens of supplier invoices a day (Joshi).
- A small business owner sending invoices from her phone by chat (Grey Matterz).
- A wait loop that keeps checking a parsing job until it reports done (Trabzada).

## Beginner mistakes they address

- Letting an automation post money records with no human check: Trabzada pauses for a confirmation on the phone before the next step.
- Skipping validation of extracted fields before they reach the books (Joshi).
- Forgetting that a messaging trigger needs its own developer app and test numbers (Grey Matterz).

## Pacing

Measured: about 13 to 23 minutes. Trabzada is demo-first and then thorough; Joshi is a steady talk-through of a finished workflow; Grey Matterz is mostly setup screens with music under them. Trabzada's chapters name each stage; Joshi's video has none.

## What they do badly, and what we do better

- None covers the job our lesson does: reminding a customer, politely, about an open invoice. We teach that and say plainly the videos studied did the inbound side.
- Long credential setup in the middle of the lesson; we keep credentials to one short chapter.
- Generic hype opening (Grey Matterz); we open on the reminder email that went out.
- No log of what was sent; we log every reminder so nobody is chased twice.

## What we take (structure only)

1. Open on the finished result: the reminder sent, and the log row it left.
2. Then the list it reads from, and the rule for which invoices qualify.
3. A weekly schedule, then the filter to open invoices only.
4. One polite email from a template, read before it goes.
5. Log it, and end on the check that a paid invoice gets nothing.
