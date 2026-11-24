# Study note: business_04

Topic searched: "n8n google sheets weekly report to slack" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=nWPwQG3oitI -- Fayyaz Ahmed | Shopify Developer, 1,227 views, 9:41, read 2026-10-18. 104 words/min; first on-screen action at 53 s (transcript heuristic); 5 chapters.
- https://www.youtube.com/watch?v=dmW5a5ol9Bg -- Automate & Elevate, 55 views, 5:09, read 2026-10-18. 163 words/min; first on-screen action at not detectable; 9 chapters.
- https://www.youtube.com/watch?v=Wt4M4TAoh24 -- Zakary DeLeo, 14 views, 4:47, read 2026-10-18. 177 words/min; first on-screen action at 221 s (transcript heuristic); 0 chapters.

## How they open

Search results for a weekly sheet-to-Slack report were thin and small (all under 10 minutes); the three kept each cover part of it. Automate & Elevate opens on a familiar meeting pain (everyone hunting through tools for the same numbers) and promises one place for them. Fayyaz Ahmed opens with a general promise of sheet data turned into output by AI. Zakary DeLeo opens by naming the exact metric he will send to Slack.

## The order they explain things

- Automate & Elevate: list every metric and where it lives before any automation, design the destination sheet with two fixed first columns (time and period), then five scheduled or webhook workflows that fill it, the last one posting a summary to Slack, then the dashboard.
- Fayyaz Ahmed: the sample sheet, a manual trigger, an AI agent with a model and memory, the sheet as a tool, then questions answered from the rows, then adding rows.
- DeLeo: API keys per account, a first workflow that pulls messages and skips rows already in the sheet, a second that sums them and posts to Slack, run hourly until stable and then daily.

## Examples and analogies

- A weekly quality meeting where nobody can find the numbers (Automate & Elevate).
- A class roster sheet counted by an agent (Ahmed).
- Inbound versus outbound text messages per sales setter (DeLeo).
- The sheet described as the single source of truth the report reads from (Automate & Elevate).

## Beginner mistakes they address

- Building the automation before deciding which numbers matter and where they live (Automate & Elevate).
- A sheet whose columns change shape, which breaks the report later (Automate & Elevate).
- Writing duplicate rows on every run; check what is already there first (DeLeo).
- Scheduling a new report daily before it has run cleanly many times (DeLeo).

## Pacing

Measured: about 5 to 10 minutes. Automate & Elevate is a tight scripted narration over diagrams; Ahmed is slow with pauses; DeLeo is an unscripted walk through a finished build. Only Automate & Elevate names its chapters by what each workflow does.

## What they do badly, and what we do better

- None builds the plain weekly report step by step; two show finished workflows and one uses an AI agent to count rows, where a sum is exact and free. We sum with a normal node and say why.
- AI answers shown as correct without checking the sheet; any number we show is read from the sheet on screen.
- No date window: they read everything. We read last week's rows only.
- Credentials and API keys shown on screen in passing; we never show a key.

## What we take (structure only)

1. Open on the Slack message with last week's numbers.
2. Decide the numbers and the sheet's columns first, with a date column.
3. A Monday schedule, then the rows from last week only.
4. Sum them with a plain step, no AI.
5. Post to Slack and end on the check that an empty week says so.
