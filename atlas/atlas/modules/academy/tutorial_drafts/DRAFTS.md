# Academy draft scripts (2026-11-24)

5 scripts left, one module each in this folder (`<series>_<NN>.py`). Every module here sets
`STATUS = "draft/unapproved"` and carries an `OPENER_TOPIC`. Nothing here is read by
`tutorial_scripts.for_series`, so the runway, lineup, render and upload ignore them.
`tests/test_academy_drafts.py` holds each one to the script shape.

To approve one (Miro): `git mv` it into `../tutorial_scripts/`, delete `STATUS`, add the
`OPENER_TOPICS` line from `OPENER_TOPIC` (new numbers only), run the gate in
`docs/academy-script-writer.md`. "new" = the plan was used up, so the module adds its own
`plan(...)` entry. Resolve every `TODO-VERIFY` comment first. Facts marked read were read on
the official docs on 2026-11-24 (URLs in each docstring).

| Series | # | Title (10 min) | Hook | Note |
|---|---|---|---|---|
| explained | 2 | What is a Language Model? Explained in 10 Minutes | Same question twice, two answers: is it broken? | planned; HOLD |
| security | 1 | Learn Prompt Injection in 10 Minutes | What if the email your agent reads could give it orders? | planned; HOLD |
| security | 2 | Learn API Key Scopes in 10 Minutes | The key that opens everything, or the one that opens one sheet? | planned; HOLD |
| future | 1 | AI Models Explained in 10 Minutes: Why Input Wins | Switched to a newer AI and got the same so-so answer? | planned; HOLD; opinion |
| future | 2 | AI Agents Explained in 10 Minutes: Why Approval Matters | What changes when an AI stops answering and starts doing? | planned; HOLD; opinion |

## Approved 2026-11-24 (Miro authorised the review)

19 drafts moved to `../tutorial_scripts/` (git mv, STATUS removed, OPENER_TOPICS lines
added for the new numbers): claude 7-9, n8n 8-10, project 7-9, business 6-8, agents 6-8,
local 7-9, explained 1. Every `TODO-VERIFY` was resolved against the official docs on
2026-11-24 or the claim was softened: local 9 now names the `n8n-official` entry in
Hermes' MCP catalog (the draft said no built-in link existed), local 7 drops the
"keys stay in Hermes" claim, n8n 8 softens the memory and Calculator outcomes,
business 6 no longer says how the chat model is wired, explained 1 cites the IBM
machine-learning page for the spam-filter and rules-based examples.

## Open items for Miro

* HOLD (explained 2, security 1-2, future 1-2): these are in `tutorial_series_ai.HOLDS`
  (they need a recording or Miro on camera). The drafts use no demo scene and claim no
  demo was shown. Approving the script does not lift the hold.
* security and future have no shipped script yet; #1 and #2 of each follow the ladder order
  (not the "writable" shortcut of #6/#7).
* `TODO-VERIFY` comments left in the remaining drafts (OWASP numbering in security 1) are
  still to resolve before those are approved.
* Study notes: the 18 promoted modules (all but explained 1) carry a "pending study" note in
  `docs/academy-references/`. The human-tutorial study was not run for them; run
  `study.py` for each and replace the note (docs/academy-script-writer.md step 0).
* Closing line: claude 9, n8n 10, project 9, business 8, agents 8, local 9
  now end their series' plan, so their recaps say "That's the whole series." until more
  episodes are planned.
* The per-day cap (7) now equals the number of daily series, since explained posts #1.
* project 8: today's n8n docs say an Error Trigger workflow must be published; the shipped
  project #5 docstring says it need not be, so #5 may be out of date. Check #5.
* n8n 8, 9, 10: lab files (`n8n-agent-lab-starter.json`, `n8n-sub-workflow-lab.json`,
  `n8n-code-last-lab.json`) are in each module's `LAB_FILES`; they only publish after approval.
* claude 7: the title no longer names all four tools, to fit 60 characters.
