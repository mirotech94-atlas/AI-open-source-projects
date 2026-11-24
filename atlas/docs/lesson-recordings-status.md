# Lesson recordings: status (2026-11-15)

Tool: `tools/lesson_recordings/record.py` (Part D5). The plan counted 68 real
command or walk-through segments (operator and sell courses; adults and seniors
are simulated diagrams by design).

## Proven here
- Playwright runs on the Mac with
  `uv run --python 3.12 --no-project --with playwright python -m tools.lesson_recordings.record ...`.
- Chrome channel launches headless (Chrome 157) and renders a page to a PNG.
- `--plan` ran on a one-lesson fixture (a `<pre>` segment and a node
  walk-through both counted, the title skipped, on a 3-segment
  fixture: "real 2, simulated 0"). Only fixture data, no live service.
- `--record` now gets past the Playwright check and stops at the next blocker,
  exit 2: `BLOCKED: recording needs the n8n container and DEMO_HOME scratch folder`.

## What blocks all 68 (checked, not guessed)
1. `_record_all` in `record.py` is a stub that always raises `Blocked`. No
   segment can be recorded until it drives `tools/tutorial_demos/record.py`
   (Capture, encode, n8n_up) for each planned segment.
2. The lesson source `data/lesson-video*/<lesson>/segments.json` is not on this
   Mac or in git (it lives with the server build), so the real 68 could not be
   re-counted here. Copy it into `data/` to run `--plan` for real.
3. The recorder needs the setup in `tools/tutorial_demos/record.py`: Docker
   with the n8n image (`DEMO_N8N_IMAGE`), `DEMO_HOME` scratch folder, uv, and
   Node >= 22.19 with the MCP Inspector installed.

## Not run, needs a person
Any segment whose command signs in to an account, takes a payment or calls a
live service (for example Claude, n8n cloud, Stripe, a hosting login) must not be
recorded by this tool. Which of the 68 these are cannot be listed until the
segments are available (item 2); each needs a fake local stand-in or stays out
of `clips` and goes in `simulated`.
