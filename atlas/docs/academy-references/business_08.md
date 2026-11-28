# Study note: business_08

Topic searched: "n8n monthly summary report automation" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- Automate Reports in Excel using Office Scripts - Time Saving Tip to Auto Format Reports -- https://www.youtube.com/watch?v=z-62JPTqgiw -- Sharon Smith, 249,074 views, 4:43, read 2026-11-25. 200 words/min; first on-screen action at 38 s (transcript heuristic); 5 chapters.
- n8n for Beginners: Automate Financial News with AI & RSS (Part 1) -- https://www.youtube.com/watch?v=1PeSb8liPkg -- Piotr Jankowski, 920 views, 9:02, read 2026-11-25. 119 words/min; first on-screen action at 199 s (transcript heuristic); 0 chapters.
- Create Professional SEO Reports in Minutes with n8n Automation -- https://www.youtube.com/watch?v=Dxidg2AOfn8 -- Marvomatic | AI Automations, 886 views, 19:22, read 2026-11-25. 188 words/min; first on-screen action at 31 s (transcript heuristic); 11 chapters.

## How they open

Sharon Smith opens with the benefit (time saved) and tells you where to find
the Automate tab, with a note that an IT admin may have switched it off.
Piotr Jankowski welcomes viewers, promises a beginner-friendly build, and
lays out a three-part project with free tools before touching n8n. Marvomatic
starts from the finished product: a polished slide report, its contents page
and its comparison against the previous period, then shows the form that
produces it. Only Marvomatic and Piotr use n8n at all.

## The order they explain things

- Sharon: record your formatting once on a real report (fit the columns,
  style the header, a formula, a number format), read the generated script,
  save it, then run it on a new report with one click.
- Piotr: a scheduled trigger at a set hour, an RSS read, a filter that keeps
  only the last day, copy the same pieces for a second feed, connect a free
  language-model account, a prompt that handles one article at a time, then a
  Discord post through a webhook.
- Marvomatic: a form that takes the site, dates and period, variables for
  current and previous dates, requests to the search and analytics services
  for both periods, a merge and calculations, then writing the results into
  slides and a linked sheet through readable names on each chart.

## Examples and analogies

- Recording your clicks once and replaying them (Sharon).
- A morning news digest with a one-sentence summary per article (Piotr).
- A report that always sets this period beside the one before it, so a
  reader sees direction, not just totals (Marvomatic).
- Nothing hard-coded: the same flow can run weekly, monthly or ad hoc by
  changing the dates.

## Beginner mistakes they address

- Hiding a menu tab by company policy, so the feature seems missing.
- Letting the AI see many articles at once; Piotr forces it to take one.
- Free-plan request limits on a chart service, handled with a pause between
  calls.
- Hard-coding a property or date, so the flow breaks on the next site or
  month.
- Missing the paging option on large data pulls (Marvomatic).

## Pacing

Sharon is the shortest, under five minutes at about 200 words a minute, and
shows her first action within 40 seconds. Piotr is slow, about 119 words a
minute, and spends over three minutes introducing a three-part plan before
the first step. Marvomatic runs close to 20 minutes at about 188, with 11
chapters, and reaches the form quickly but turns technical fast. Sharon's
video is about Excel, not n8n, and Marvomatic's goal is a visual slide
report, so each drifts from a plain monthly summary message.

## What they do badly, and what we do better

- Sharon's lesson teaches formatting, not a written summary, and it needs a
  work account with the right tab enabled.
- Piotr's is a daily news digest and a first part of a series; it never
  reaches a monthly figure or a count.
- Marvomatic's build is a dozen requests and a slide API, far past a first
  lesson, with no word on what to check before trusting the numbers.
- None says which part does the arithmetic and which part writes the words.
  We say it plainly: n8n counts, the AI only phrases.
- None covers what happens in a month with no rows. We make that a quiz.

## What we take (structure only)

1. Show the finished note first, then build it.
2. Pull the dates into the flow, so the same build runs for any month.
3. One input at a time for the model, with the numbers already worked out.
4. Compare with the previous period when the reader needs direction.
5. Send the result somewhere the reader already looks, then check it by hand
   for the first few months.
