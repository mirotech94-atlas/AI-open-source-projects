# The futures rulebook — 14 paper engines over MNQ/NQ bars

Miro sent a reel (2026-09-29): "We coded 14 Orderflow+SMC+ICT strategies into
a single algorithm that beats prop firms" — NinjaTrader 8, MNQ, VWAP signals,
an equity curve per engine and combined, a live P&L (unverified). This is
the honest version: the same *kinds* of rules, written down so they can be
computed from OHLCV bars alone, measured on real price data, on paper.

Nothing here is advice. ATLAS never trades: no broker, no order path, no
key. Money out is Tier 3, permanently denied.

## Ground rules every engine shares

* **Bars only.** Every rule is a pure function of closed OHLCV bars. A
  signal on bar *N* uses bars 0..N and nothing after. The entry is filled at
  the **open of bar N+1** (never at the close it was seen on).
* **Sessions are CME sessions** in America/New_York: a session opens at
  18:00 and closes at 17:00 the next day; its *session date* is the date of
  the 17:00 close. Sunday 18:00 opens Monday's session.
* **Killzones** (the signal bar's open time, ET): `london` 03:00–05:00,
  `ny_open` 09:30–11:00, `ny_pm` 13:30–15:30. Each engine names the ones it
  trades in; no entry outside them.
* **Flat before the cash close:** any open position exits at the close of
  the bar that ends at or after 16:00 ET (`eod`). No overnight paper
  positions.
* **Stop, target, time stop.** Every entry carries an initial stop
  (ATR-based or structure-based), a target expressed as an R multiple of the
  initial risk (or, for the trailing engine, none), and a time stop in
  minutes. An entry whose risk is under 2 ticks is discarded.
* **Max 2 entries per engine per session, 1 open position per engine.**
* **Both sides.** Every rule below is written long; the short is the exact
  mirror (highs for lows, above for below).
* **Indicators:** ATR14 (Wilder) over all bars; session-anchored VWAP
  (typical price × volume from the 18:00 ET open) and its ±2σ band; 20-bar
  average volume; swing highs/lows as pivots of 3 bars either side,
  usable only once confirmed (3 bars later); previous-session high/low
  (PDH/PDL); the opening range from 09:30 ET.

## The engines

| # | engine | entry (long; short mirrored) | stop | target | time | sessions |
|---|---|---|---|---|---|---|
| 1 | `vwap_reclaim` | the 3 prior bars closed below VWAP, this bar closes above it | lowest low of those 4 bars − 0.25 ATR | 2R | 90 min | ny_open, ny_pm |
| 2 | `vwap_reject` | short: prior 3 bars closed below VWAP, this bar's high touches VWAP and it closes below | high + 0.25 ATR | 2R | 90 min | london, ny_open, ny_pm |
| 3 | `vwap_band_fade` | short: prior bar closed above the +2σ band, this bar closes back inside | higher of the two highs + 0.25 ATR | VWAP itself (structure; skipped if under 1R) | 120 min | ny_open, ny_pm |
| 4 | `orb15` | first close above the 09:30–09:45 range high, before 11:00, first time only | the range low | 2R | 120 min | ny_open |
| 5 | `orb30` | first close above the 09:30–10:00 range high, before 11:00, first time only | the range midpoint | 2R | 120 min | ny_open |
| 6 | `pdh_sweep` | short: high sweeps above PDH, close back below it (liquidity sweep and reversal) | high + 0.25 ATR | 2R | 120 min | london, ny_open, ny_pm |
| 7 | `fvg_fill` | a bullish fair value gap (bar a's high < bar c's low) formed within the last 12 bars; this bar dips into the gap and closes above its top, up | gap bottom − 0.25 ATR | 2R | 120 min | all three |
| 8 | `order_block` | an impulse (close gains ≥ 1.5 ATR over 3 bars) within the last 20 bars; the last down candle before it is the block; this bar retests the block's high and closes above it, up | block low − 0.25 ATR | 2R | 120 min | all three |
| 9 | `bos` | break of structure: the last confirmed swing low is higher than the one before, and this bar is the first close above the last confirmed swing high | last swing low − 0.1 ATR | 2R | 180 min | all three |
| 10 | `choch` | change of character: two descending swing highs and two descending swing lows, then the first close above the last (lower) swing high | last swing low − 0.1 ATR | 2R | 180 min | all three |
| 11 | `eqh_sweep` | short: two confirmed swing highs within 0.05% of each other in the last 40 bars; this bar's high exceeds both and it closes below the lower one | high + 0.25 ATR | 2R | 120 min | all three |
| 12 | `volume_imbalance` | volume ≥ 2× the prior 20-bar average, close above the prior bar's high, close in the top quarter of the bar's range | low − 0.25 ATR | 2R | 90 min | ny_open, ny_pm |
| 13 | `inside_bar` | the prior bar sat inside the one before it; this bar closes above the mother bar's high | mother bar low − 0.1 ATR | 2R | 90 min | all three |
| 14 | `atr_trail` | close crosses above a rising 20-bar SMA (prior close was at or below it) | close − 2 ATR, then trails 2 ATR below the highest close, moved only on closed bars | none — the trail is the exit | 240 min | london, ny_open, ny_pm |

The killzones and the volume proxy are what "order flow" can mean with
bars: there is no tape here, no bid/ask, no footprint. Say so when quoting
the results.

## The backtester's honesty guards

* **Stop first.** A bar that touches both the stop and the target is a
  loss.
* **Gap-through fills at the open.** A bar that opens beyond the stop
  exits at its open (worse than the stop); one that opens beyond the target
  exits at its open too.
* **Costs per side:** 1 tick of slippage on every fill, plus the commission
  in `contracts.json`. MNQ: $2 a point, $0.50 a tick, $0.50 a side. NQ: $20
  a point, $5 a tick, $2 a side.
* **No look-ahead.** Signals use closed bars only; fills happen on the next
  bar; the trailing stop moves on closed bars and binds on the next one.
  `tests/test_futures_engines.py` mutates every bar after the signal bar
  and checks the signal is unchanged.
* **Walk-forward halves.** Nothing is fitted, so nothing is "in-sample" in
  the usual sense; the report still shows the first and second halves of
  the sessions separately, because a rule that only worked in one half is
  a rule that did not work.
* **Survivorship.** One instrument, chosen after the fact because a reel
  named it. There is no universe here to survive, and no claim that the
  result carries to anything else.
* **The data is unofficial.** Yahoo's intraday futures bars have holes
  (the maintenance hour is expected; others are not). Gaps inside CME hours
  are counted and reported. Holidays show up as gaps too.

## The paper forward book

From deploy on, every 15 minutes inside CME hours, the engines run on the
newest closed 5-minute MNQ bars, through the same step function the
backtester uses, so a forward result and a backtest over the same bars are
identical by construction (a test proves it).

$50,000 paper, 1 contract per signal, at most 3 open, a daily loss stop of
−$1,000 (flat, and no entries for the rest of the session), and a breaker
at −$2,000 from the book's peak: flat, and the `futures` module is paused
through `kernel/pause.py` with one line to Miro. Only he resumes it.

## The prop-firm check

`propfirm.json` holds an *illustrative* $50k evaluation: $3,000 target,
$1,000 daily loss limit, $2,000 trailing drawdown, 5 trading days minimum.
Real firms vary. The check is arithmetic over ATLAS's own paper curve:
would it have passed, and if not, on which day and why. ATLAS never signs
up for, pays for, or contacts a prop firm.
