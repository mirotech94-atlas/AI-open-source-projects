# Reply-time baseline

Status: UNKNOWN. No baseline has been measured yet. Numbers go here only from a
real run of `ops/latency_report.py` on the live local ATLAS after the timing code
has collected at least 100 replies on each channel (WhatsApp and portal), over a
7-day window.

How it is measured:

- `atlas/kernel/reply_timing.py` writes one `reply_timings` row per answered turn
  (channel, route, total ms, stages recall / live_read / model / other, model
  name). No message text is stored.
- `python ops/latency_report.py --days 7` prints p50 and p95 per channel and
  `target = round(0.28 x p50)` (the typical reply, Miro 2026-11-12). It prints
  UNKNOWN and exits 2 when a channel has fewer than 100 replies.
- The baseline is the window before the memory recall change reaches the live
  box, so recall time counts against the target, not for it.
- Portal history from before timing existed (`voice_turns.ms`) is shown as a
  separate series and never mixed in.

Raw numbers and the query used go below after the first real run
(`--write-doc` rewrites this file).
