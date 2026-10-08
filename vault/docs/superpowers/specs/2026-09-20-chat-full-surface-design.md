# Ask Guardian reaches every surface the customer can

2026-09-20

## The ask

> the guardian chatbot should have complete control over write read and
> execute access on all that we are doing — so clients if wanted they can talk
> with the ai bot and help them fix anything advise about anything or report
> anything etc — think of all the things that we are doing that the ai chatbot
> should have on demand for the clients

Read carefully, this is a **coverage** requirement, not a permission one. "Fix
anything, advise about anything, report anything" names three verbs and asks
that each of them reach every part of the product. It does not ask for the
confirmation step to be removed, and this spec does not remove it.

## What is deliberately not changing

`chat/tiers.py` and `remedy.py` keep their three tiers exactly as they are:

- **tier 1** — read, runs immediately;
- **tier 2** — a reversible change, runs immediately for an operator;
- **tier 3** — a single-use, token-bound human *yes* first.

Plus the escalations `effective_tier` already applies: anything on a host
Guardian has never successfully fixed, anything that would firewall off the
hub's own port, anything `KNOWN_FIXES` does not classify, and everything at
all when the platform-wide auto-heal pause is on.

Those exist because chat is the one surface where an LLM chooses the argument.
A wider reach makes the envelope *more* load-bearing, not less: the point of
this work is that a customer can ask for anything, not that Guardian does
anything unasked. Every new tool below is classified into the existing tiers
and takes its `min_role` from the dashboard route it mirrors, read off that
route's `ctx=` guard rather than guessed.

## Coverage matrix

The portal surface on the left, the tool that reaches it on the right. `—`
is the gap this spec closes.

### Read — "advise about anything"

`chat/prompt.py::context_blob` already puts sites, hosts, LAN devices, open
alerts, the score, recommendations, a DNS summary, home/presence, speed and
top domains in front of the model on every turn. Those need no tool. What
follows is what the customer can see in the portal and the bot currently
cannot read at all.

| Surface | Payload | Today | New tool | Tier / role |
|---|---|---|---|---|
| Threats and protection drawer | `payloads/security.threat_detail` | — | `threat_detail` | 1 / viewer |
| Topology | `payloads/topology` | — | `network_map` | 1 / viewer |
| DNS activity, rules, exceptions | `payloads/dnsview` | summary only | `dns_activity` | 1 / viewer |
| Updates and EOL | `payloads/updates` | — | `update_status` | 1 / viewer |
| Cameras / recorders | `payloads/cameras` | — | `camera_status` | 1 / viewer |
| History and trends | `payloads/history` | — | `trend` | 1 / viewer |
| Jobs queue | `payloads/jobs` | — | `work_status` | 1 / viewer |
| Setup progress | `onboarding.progress` | — | `setup_status` | 1 / viewer |
| Security check detail | `GET /security/checks/detail` | — | `check_detail` | 1 / viewer |
| Asset importance | `GET /devices/{mac}/importance` | — | folded into `set_importance` | 1 / viewer |

`threat_detail` is the sharpest of these. It shipped earlier today and
nothing but the drawer can read it, so a bot that can *run* `apply_fix`
cannot answer "what did you find in that brute-force attempt, and how was it
settled" — which is the question the drawer exists to answer.

### Write — "fix anything"

| Route | Today | New tool | Tier / role |
|---|---|---|---|
| `POST /dns/rules` | — | `dns_filtering` | 2 / admin |
| `POST /dns/exceptions` | — | `dns_exception` | 2 / admin |
| `POST /jobs/{qid}/cancel` | — | `cancel_work` | 2 / operator |
| `POST /jobs/{qid}/retry` | — | `retry_work` | 2 / operator |
| `POST /security/checks/{op}` | — | `decide_check` | 2 / operator |
| `POST /devices/{mac}/importance` | — | `set_importance` | 2 / operator |
| `POST /alerts/bulk` | — | `bulk_alerts` | 2 / operator |
| `POST /onboarding/networks/{id}/invite` | — | `send_sensor_invite` | 2 / admin |
| `POST /attacker` | — | `block_attacker` | 2 / admin |
| `POST /reset-lan-baseline` | — | `reset_baseline` | **3** / admin |

`dns_filtering` is the one that matters most and the one that is most
obviously missing. Guardian now shows a standing prompt on the dashboard
telling the customer DNS filtering is off and they should switch it on —
and the assistant sitting next to that prompt could not switch it on. A
product that nags about a feature its own assistant cannot reach is worse
than one that never nagged.

`reset_baseline` is tier 3 although the route is a plain admin POST, and the
reason is the warrant rule rather than the route. The LAN baseline is what
"new device" is measured against; re-anchoring it silently marks every device
currently on the network as known-from-the-start. That is not reversible by
re-running anything, and it is invisible afterwards — exactly the shape the
confirmation tier exists for. `_reversible_for` is given the same answer.

### Report — "report anything"

Two readings of "report", and both shipped.

| Route | Today | New tool | Tier / role |
|---|---|---|---|
| `POST /support/tickets` | — | `raise_ticket` | 2 / **viewer** |
| `POST /support/tickets/{id}/reply` | — | `reply_ticket` | 2 / viewer |

`raise_ticket` is the tool whose whole point is that Guardian cannot fix the
problem itself, and it matters most exactly where the assistant is least able
to help: a dead sensor is `remedy`'s `human` tier, because every fix travels
through the sensor. "I cannot do anything about that" is a worse answer than
the same sentence followed by a ticket. It is `viewer`, mirroring the route —
the person most likely to notice something wrong should not be the one who
cannot tell anybody.

Three more write tools were found by the coverage guard rather than by
reading: `bulk_alerts` (`POST /alerts/bulk`, so "ack everything low" has an
honest shape instead of the model picking one alert), `decide_check`
(`POST /security/checks/{op}`, which refuses a blank reason — an accepted
risk with no recorded reason is indistinguishable later from one nobody
noticed) and `block_attacker` (`POST /attacker`, the standing policy).

`build_report` already exists and already dispatches every preset, including
`threat_report` added earlier today. No new tool. What it lacks is the
ability to say which presets exist, so the model guesses names; the tool's
`schema` gains an enum sourced from `reports/presets.SPECIAL_PRESETS` and
the preset table, so a hallucinated report name is rejected the same way
`KNOWN_FIXES` rejects a hallucinated fix action.

## The rule every new tool follows

**Call the route function; never reimplement it.** `h_remove_network` is the
pattern already in the file:

```python
try:
    out = network_routes.delete_network(row.id, _FakeRequest(ctx.request_ip),
                                        reassign=dest.id, ctx=_chat_ctx(ctx), db=ctx.db)
except HTTPException as e:
    return ToolResult(ok=False, text=str(e.detail))
```

The route owns the audit row, the live-bus publish, the validation and the
role check. A handler that re-derives any of those is a second copy that
drifts, and the drift is silent: chat would keep working while quietly not
writing the audit trail the dashboard writes. The handler's own job is only
to turn a customer's words into the route's arguments, and the route's
`HTTPException` into a sentence.

Three consequences worth stating, because each has already bitten this repo:

- **The warrant rule still holds.** `label` and `trusted` are human warrants.
  `trust_device` and `label_device` are legitimate because a customer saying
  "that printer is ours" *is* a person vouching. No new tool writes either
  field, and `dispatch`'s existing `require_confirm` guard — forced whenever
  the model names a target the user did not — stays the thing that stops text
  inside the DATA blob from producing a silent trust.
- **`KNOWN_FIXES` is a third copy** of the sensor's `fix.FIX_ALERT_KEY`.
  Nothing here touches fix actions, so nothing here touches it; if that
  changes, `hub/tests/test_remedy.py` parses the sensor file and will say so.
- **Schema and mirror ship together.** Any new shape crossing into
  `schemas_dash.py` lands in the same commit as its `web/src/types.ts`
  mirror, or `test_contracts.py` fails — as it did for three commits earlier
  today when they were split.

## Testing

Per tool: one test that it reaches the route (the underlying row changed,
not merely that the handler returned `ok`), and one that a role below its
`min_role` is refused. Beyond that, three tests that guard the matrix itself
rather than any one tool:

- **the coverage test** — `test_chat_coverage.py` walks `routers/` for
  client-facing write routes and fails when one has no tool and is not on a
  short, commented exemption list. This is the test that keeps this document
  true after the document is forgotten. A route added next month without a
  tool fails the suite with the route's own name in the message.
- **the role-parity test** — for every tool that names a route, the tool's
  `min_role` equals that route's guard. A tool handing a viewer a write is
  the worst failure available here and it is a one-character mistake.
- **no wall-clock assertions**, per the house rule. Nothing here is a time
  budget.

## What the customer is asked to confirm

Found while testing, not while designing. `routers/chat.py` forces the
proposal path for *any* tier-2 tool the moment the model names a target the
user did not type, so every write tool here can surface as a confirmation
sentence. The head of that sentence was built as `"<verb> on <target>"` with
`_target_of` reading only `host/mac/target/key/ip/url/section` — none of the
new tools' argument names — so a forced-confirm `dns_filtering` read, exactly:

> Sure, I will dns filtering. Adds or removes one filtering rule …

A confirmation that does not name what it confirms is not a confirmation.
`registry._VERBS` now takes a `{}` slot for the target, `_target_of` reads
`domain/job_id/network/method/subject/ticket_id`, `_bulk_target` counts alert
`keys` the way it counts `macs`, and `dns_filtering`'s head says the
direction — "block casino.example on every device" and "allow … through the
filter" are opposite decisions with identical argument shapes. Five tests in
`test_chat_coverage.py` pin the wording.

## Shipped

48 tools (27 before). `tests/test_chat_coverage.py`: 31 tests, of which two
are the matrix guards. Both were shown to discriminate before being trusted:
un-routing `dns_filtering` fails the coverage guard by path; downgrading its
role to `operator` fails the parity guard by name. The 40-odd routes on the
exemption list each carry their reason in the test file, not here.

## Scope

Not in this spec: giving the bot reach into the *owner* console
(`platform.py`, tenants, billing, audit). That is a different tenant model
and a different blast radius, and the ask was explicitly about clients.
