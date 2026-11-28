# Study note: agents_06

Topic searched: "how to test an AI agent evaluations for beginners" (2026-10-20). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=vuBvf7ZRKTA -- "How to evaluate agents in practice", Google Cloud Tech, 55,293 views, 10:53, read 2026-10-20. 129 words/min; first on-screen action at 219 s (transcript heuristic); 8 chapters.
- https://www.youtube.com/watch?v=XUzUf_HCgvk -- "How to Evaluate and Test Agent Skills", Alejandro AO, 10,387 views, 15:18, read 2026-10-20. 191 words/min; first on-screen action at 58 s (transcript heuristic); 9 chapters.
- https://www.youtube.com/watch?v=lc9q0Fr5648 -- "How to Evaluate Your AI Agent Using Test Cases and Metrics", DigitalOcean in Action, 355 views, 9:12, read 2026-10-20. 151 words/min; first on-screen action at 75 s (transcript heuristic); 0 chapters.

## How they open

Google Cloud Tech opens by recalling the previous episode (why ordinary
software tests fall short for agents) and promises a three-level plan plus
a hands-on run with their agent kit. Alejandro AO opens fast with a
promise that your skills will stop breaking each time you touch them, and
leans on a published article as his spine. DigitalOcean opens on stakes: a
travel agent that people use to plan real trips must be grounded and
accurate. All three start from "why trust needs measuring" before any
tool is shown.

## The order they explain things

- Google: a pyramid of three levels. Small checks on one tool choice and
  its inputs come first (fast, cheap, automatable). Then whole-journey
  checks on the order of the steps and the final answer. Then a human
  reviews the overall experience. After that, a demo with a book-finding
  agent, where the "trajectory" (every step it took) is compared with the
  expected one, and two pass thresholds between zero and one are set.
- Alejandro AO: say what a good result means (the outcome, the process
  followed, the style, the efficiency), keep the work under version
  control, try prompts by hand including ones that should not trigger the
  skill, save them as a table with the expected result, grow it as new
  failures appear, and finally let a second model grade with a rubric.
- DigitalOcean: why evaluations matter, switch on traces first, pick the
  goals to measure, upload a spreadsheet of questions with expected
  answers, run it, read the average score against a pass line, then open
  one low-scoring question to see the reason and fix the instructions.

## Examples and analogies

- A book-finding agent that must try the local library, then the shop,
  then order online (Google): the order of steps is part of the test.
- A skill that trains models, where a full real check takes hours, so
  small prompt checks matter (Alejandro).
- A travel agent that must stay inside its itinerary notes and not add
  facts of its own (DigitalOcean).
- "Evaluate by vibe" versus evaluating like software (Alejandro).

## Beginner mistakes they address

- Judging by one or two happy tries instead of a repeatable set.
- Demanding word-for-word equal answers from something that varies; Google
  sets a looser pass line for wording and a stricter one for steps.
- Never checking prompts that should not trigger the behaviour.
- Changing the instructions with no saved record of what changed.
- Reading only the final answer, not the steps or the reason for a low
  score.
- Letting the agent answer from outside its allowed information.

## Pacing

Google is slow and clear, about 129 words a minute, with a diagram first
and code second. Alejandro AO is quick, about 191, and article-driven.
DigitalOcean is a mid-speed product walkthrough, about 151, and drifts
from our topic: most of it is one vendor's console, with clicks that a
learner cannot copy without that product. Alejandro's video drifts too,
since it tests coding-assistant skills rather than a customer-facing
agent, though the loop of define, try, record, extend carries over.

## What they do badly, and what we do better

- Two of the three need a specific product (a kit, a vendor console) and
  jargon such as trajectory, thresholds and overlap scores. We need only a
  pen and a page, and plain words.
- Google gives the first runnable check at about three and a half
  minutes in. We show the small test table early.
- None tells a newcomer where the first test requests come from. We say:
  copy real ones from your inbox, then add odd ones.
- None says plainly to run the tests in a safe copy, away from real
  customers. We say it, and cite Anthropic's own advice.
- None ends on a short exercise. We pause and ask for three requests.

## What we take (structure only)

1. Why one or two good tries prove little, said before any method.
2. Real requests first, plus a few odd ones.
3. Write what good looks like before running anything.
4. Grade by rule where possible, by a second model otherwise.
5. Re-run every test after each single change, in a safe copy.
6. Our script already matches this order. Google's "check the steps, not
   only the final answer" is a possible later addition; we leave it out
   for now because the lesson has no tools yet to track.
