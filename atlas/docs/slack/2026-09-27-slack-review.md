# Slack review, implemented — 2026-09-27

Branch `sdd/slack-plan`, on `phases-12-14` at `f99f888`. Implements Miro's
two documents, *Atlas Slack: Message and Channel Improvement Plan* and
*Atlas Slack: Coding-Agent Implementation Prompt*.

**Not deployed. No real Slack message was sent, edited or deleted, and no
channel was renamed as part of this work.** Every test ran against mocked
Slack and provider transports. Live Slack was only *read* (conversation
history) to confirm the root causes below.

## 1. Outcome and scope

Typed Slack decisions are now deterministic and safe. Every answer stays
in the thread it was asked in and is shown once. Decision replies name the
proposal and its true state. Messages carry one consistent
`[Project] TYPE:` shape. Investigations that stop short become incident
cards. Reel cards lead with their quality and state. Report wording
measures things instead of guessing. The existing conversational behaviour
in every channel is preserved and regression-tested.

## 2. Root causes

**Confirmed in code or in live payloads**

| Symptom | Root cause |
|---|---|
| "what happens if I approve 180?" or "don't approve 180" would approve #180 | `_verdict_for` matched `approve` anywhere in any message of six words or fewer. Questions and negations were never excluded. |
| "do it in 5 minutes" targets #5; "approve 180 181" approves 180 only | `PROPOSAL_REF` read any bare number as an id and took the first one. |
| A pasted "> approve 180" counts | Quoted text (`&gt;` in Slack's escaping) and code were not stripped. |
| The CloudHost answer showed twice | Confirmed from the live payload: a `header` block built from `body[:150]`, then the full body in a `section`. |
| `**9 servers**` showed literal asterisks | Model Markdown was passed straight to Slack mrkdwn. |
| "This week" posted six times into #mail-calender | `weekly.send` saved its record through `brief.store`, which reads `.sections`. `Weekly` has no `sections`, so every save failed and the once-a-week guard never tripped. Fixed earlier in `3bbbf5f`, now deployed. |
| The server-count answer landed in #atlas-decisions | Every typed reply used the `decisions` topic. Fixed earlier in `c6f11b4`, now deployed. |
| Guardian and K9 posts said only "Stopped after the tool-call limit…" | `fixes.sweep` posted whatever the agent returned, with no structure. |
| #cloudhost posts began "(answered by …; … was unavailable …)" | `llm.think` prepends a pin-failure note, and the sweep posted it verbatim. |
| A reel card showed the stills fallback mid-card while every destination read "ready" | `_card_reason` listed render detail after the beats and captions. |
| "the pile means he stopped reading them" | Literal wording in `content.check_drafts`. |
| "Average day score: 0.34" | No definition. The value is `well / (well + wrong)` per reviewed day, from `review.py`. |

**Hypotheses not verified**

- A duplicate Slack event without a retry header has not been observed live.
  The protection is built on Slack's documented behaviour.
- Whether "Approve + suggest always" wording confuses anyone was not examined.
  Its behaviour already records a candidate only.

## 3. Commits and their purpose

| Commit | Purpose |
|---|---|
| `d69d4c3` | Deterministic decision parser (`api/slack_decisions.py`). Question, hold, ack, approve and decline, with quotes, code and mentions stripped. Ids are read as ids, never quantities. A bare yes or no decides only the ATLAS card it replies to. A deferred ask is not an approval. Replies go under the thread root. |
| `3c05bce` | A redelivered Slack event is acted on once. Its `event_id` is kept in the audit log, which survives a restart. Adds an index on `actions_log (event, subject)`. |
| `59227b8` | `Verdict.receipt()` gives messages like "Proposal #180 approved and started. Nothing is confirmed published yet…". Declining something already running says it does not undo it. A decision made elsewhere is noted under the card. |
| `2466e1d` | One message shape: a `[Project] TYPE:` header of INFO, REVIEW, ACTION, INCIDENT or RESOLVED, also used as the notification text. Converts mrkdwn, neutralises `<!channel>`, `<!here>` and `<!everyone>`, and splits long bodies instead of cutting them. |
| `cf42fc9` | The weekly shows its exact window and timezone, and the day score says what it measures. The CloudHost runway states a single basis. |
| `f3ed413` | A receipt keeps its title when the title carries the result, such as a per-platform publish receipt. |
| `6f1e0e8`, `394c47f` | Incident card for an investigation that stopped short. Strips the provider note, never puts agent text in the title, and threads repeats. |
| `15c05a5` | Reel cards lead with "Status: draft only…", plus "Quality exception: still-image fallback" for older media. Drafts are counted, not judged. |
| `d4a1236` | Headers are cut at a word, with an ellipsis. |
| `bad12ea`, `d11c0af` | Fixes from reviewing this branch before handoff. A card is found by its own `approve:`/`decline:` buttons, so the new header wording cannot hide it. A thread parent counts only if ATLAS's own bot posted it. Only a bare verb ("do it") may take the single pending card, so "approve the linkedin drafts" asks instead. In a question only "#7" or "proposal 7" is an id, so "what happened with node 7?" is answered as a question. The duplicate-event lookup runs only for an approver's own message. |

Files touched:

- `api/slack_decisions.py` (new), `api/slack_events.py`, `api/main.py` (Slack button receipt only)
- `channels/slack.py`, `kernel/comms.py` (`Outbound.channel`, `Outbound.presentation`)
- `kernel/approvals.py`, `kernel/fixes.py`, `kernel/weekly.py`, `kernel/reels.py` (card text only)
- `modules/content/__init__.py` (drafts detail only), `modules/cloudhost/__init__.py` (detail wording only)
- `db/schema.sql` (one index), and tests.

## 4. Tests

```
.venv/bin/python -m pytest -q          # 3862 passed, 1 skipped
```

`tests/test_pool.py::test_concurrent_callers_build_exactly_one_pool` fails
in the full run on the base commit `3e759c3` as well, without these
changes. It passes on its own (3/3 runs), so it is a pre-existing
load-sensitive flake and not caused by this branch.

New or extended test files:

- `tests/test_slack_decisions.py`: parser cases.
- `tests/test_slack_events.py`: end-to-end cases through `/webhooks/slack/events`.
- `tests/test_slack_render.py`: payload shape.
- `tests/test_approvals.py`: receipts.
- `tests/test_fixes.py`: the incident card.
- `tests/test_brief.py`, `tests/test_content.py`, `tests/test_reels_gate.py`, `tests/test_cloudhost_balance.py`.

Acceptance tests from the prompt, by number:

| # | Status |
|---|---|
| 1, 2, 3 | covered: answered in its own channel and thread, #atlas-decisions not approval-only |
| 4, 5, 6 | covered |
| 7 | n/a: single-approver workspace; decisions are refused to non-approvers |
| 8, 9, 10, 11 | covered |
| 12 | existing: the atomic claim in `decide()` |
| 13 | covered |
| 14 | existing: expiry is enforced in the claim |
| 15 | covered |
| 16 | existing: the receipt is posted after the status write |
| 17 | covered |
| 18 | existing: UNKNOWN is never OK |
| 19 | partial: threaded while the process lives |
| 20 | covered: weekly |
| 21, 22, 23 | covered |
| 24 | existing: one receipt per leg, no "everywhere" summary |
| 25 | covered |
| 26 | existing |
| 27 | covered: CloudHost wording |
| 28 | gap |
| 29, 30 | covered |

## 5. Rendered messages, before and after

**Before**, as read from live Slack on 2026-09-27:

- *Q&A in #cloudhost*: a `header` block reading "You have \*\*9 servers\*\* running – the …",
  then a `section` repeating the same text in full. Asterisks were literal.
- *Guardian and K9 incident*: a header of "guardian.open_alerts has been degraded since Fri 17:00 UTC",
  and a body that was only "Stopped after the tool-call limit without reaching an answer."
  Some bodies began "(answered by anthropic:claude-sonnet-5; openrouter:… was unavailable: HTTP 404 …)".
- *Reels card*: the format and beats came first, then captions, then "Render: stills fallback (…)",
  then "Per platform: … ready".
- *Decision receipt*: "Posting #180 now; the receipts will be in the thread."

**After**, generated from the real rendering code on this branch:

### Q&A reply in #cloudhost

```json
{
 "text": "You have *9 servers* running – the *cloudhost.instances* check reports “9 of 9 instance(s) running” (read 23:22).",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "You have *9 servers* running – the *cloudhost.instances* check reports “9 of 9 instance(s) running” (read 23:22).\n\nAnything else you need details on?"
   }
  }
 ]
}
```

### Incident: investigation incomplete (Guardian)

```json
{
 "text": "[Guardian] INCIDENT: guardian.open_alerts — investigation incomplete (degraded since Fri 17:00 UTC)",
 "blocks": [
  {
   "type": "header",
   "text": {
    "type": "plain_text",
    "text": "[Guardian] INCIDENT: guardian.open_alerts — investigation incomplete (degraded since Fri 17:00 UTC)"
   }
  },
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "*Observation:* 2 open alert(s)\n*Observed at:* Sun 27 Sep 21:40 UTC\n*Impact:* unknown — the investigation did not establish it\n*Investigation:* stopped before identifying the cause (Stopped after the tool-call limit without reaching an answer.)\n\n*Next step:* check guardian directly, against the observation above, and tell me what you find\n*Owner:* Miro\n*Retry:* not before Mon 28 Sep 21:42 UTC, unless you acknowledge it first\n*Incident:* guardian.open_alerts"
   }
  }
 ]
}
```

### Reels approval card

```json
{
 "text": "[Reels] REVIEW: #180 — Post a rendered AI-stories reel to the stories Instagram account, the Atlas Stories Facebook Page and TikTok (via Postiz)",
 "blocks": [
  {
   "type": "header",
   "text": {
    "type": "plain_text",
    "text": "[Reels] REVIEW: #180 — Post a rendered AI-stories reel to the stories Instagram account, the Atlas Stories Facebook Page and TikTok (via Postiz)"
   }
  },
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "*Quality exception: still-image fallback — Veo quota exceeded.*\nStatus: draft only; nothing is published until you approve.\nToday's AI-stories reel — format: twist (ranking weight 1.4, research)\n1. Opening line\n2. The turn\nCaption: He saved the wrong seat. #aistory\nTikTok caption: He saved the wrong seat. #aistory #fyp #storytime\nRender: stills fallback (Veo quota exceeded): media 212\nPer platform: instagram @atlasstories3: ready; facebook Atlas Stories: ready; tiktok via Postiz: ready\nNothing posts until you approve; the preview is in this card's thread.\n`content.publish_reel` · Tier 2"
   }
  },
  {
   "type": "actions",
   "elements": [
    {
     "type": "button",
     "text": {
      "type": "plain_text",
      "text": "Approve"
     },
     "value": "approve:180",
     "action_id": "atlas_approve:180",
     "style": "primary"
    },
    {
     "type": "button",
     "text": {
      "type": "plain_text",
      "text": "Decline"
     },
     "value": "decline:180",
     "action_id": "atlas_decline:180",
     "style": "danger"
    },
    {
     "type": "button",
     "text": {
      "type": "plain_text",
      "text": "Details"
     },
     "value": "detail:180",
     "action_id": "atlas_detail:180"
    }
   ]
  }
 ]
}
```

### Decision receipt (started)

```json
{
 "text": "Proposal #180 approved and started. Nothing is confirmed published yet; the results will follow in the card's thread.",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "Proposal #180 approved and started. Nothing is confirmed published yet; the results will follow in the card's thread."
   }
  }
 ]
}
```

### Decision receipt (declined)

```json
{
 "text": "Proposal #180 declined. It will not execute.",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "Proposal #180 declined. It will not execute."
   }
  }
 ]
}
```

### Decision receipt (already)

```json
{
 "text": "Proposal #180: #180 is already running; declining does not undo it.",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "Proposal #180: #180 is already running; declining does not undo it."
   }
  }
 ]
}
```

### Partial publishing receipt (instagram)

```json
{
 "text": "#180 — instagram: published",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "*#180 — instagram: published*\nhttps://www.instagram.com/reel/abc/"
   }
  }
 ]
}
```

### Partial publishing receipt (facebook)

```json
{
 "text": "#180 — facebook: published",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "*#180 — facebook: published*\nhttps://facebook.com/.../videos/1"
   }
  }
 ]
}
```

### Partial publishing receipt (tiktok)

```json
{
 "text": "#180 — tiktok: failed",
 "blocks": [
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "*#180 — tiktok: failed*\nPostiz: TikTok Business token expired"
   }
  }
 ]
}
```

### Daily brief header

```json
{
 "text": "[Atlas] INFO: Morning brief — Mon 28 Sep",
 "blocks": [
  {
   "type": "header",
   "text": {
    "type": "plain_text",
    "text": "[Atlas] INFO: Morning brief — Mon 28 Sep"
   }
  },
  {
   "type": "section",
   "text": {
    "type": "mrkdwn",
    "text": "…"
   }
  }
 ]
}
```

## 6. Schema, configuration and compatibility

- **Schema:** one idempotent index, `actions_log_event_subject`. Apply it with
  `python -m atlas.cli migrate`. The code works without it; the index only
  makes the duplicate-event lookup cheaper.
- **Contracts:** `Outbound` gains `channel` and `presentation`, both
  optional with empty defaults. Every other caller is unchanged.
- **Phone:** `phone.APPROVE` and `phone.DECLINE` are untouched, so calls
  stay keypad-only. Slack has its own vocabulary in `api/slack_decisions.py`.
- **Behaviour changes to know about:**
  - A bare "yes", "ok" or "no" at the top level of a channel no longer
    approves or declines the single pending proposal. It asks which one.
  - A verb that names something ("approve the linkedin drafts") no longer
    takes the single pending proposal either. It asks which one.
  - What still works: replying "yes" in the card's thread, "approve 180",
    or a bare "do it" with exactly one card pending.
- **Timezone:** the weekly window prints the server's local time. The
  production container runs in UTC, so it reads "… UTC", not EDT.
- **No configuration changes.** Routing and cadence are as deployed.

## 7. Remaining gaps and staging checklist

Gaps, deliberately not built here:

- **Decision index.** No pending-action index is posted in
  #atlas-decisions with links to each card.
- **Daily brief template.** The brief goes to #atlas-brief, but its body
  is not yet in the "Needs attention / Completed / Awaiting approval /
  Checks unavailable" shape.
- **Versioned approvals.** Proposals have no version field. Params cannot be
  edited in place, so there is no stale version to approve, but a
  refreshed preview link is not identity-checked (acceptance test 28).
- **Threading across restarts.** Card and incident threads rely on the
  in-memory `sent_ref`. After a restart, a decision made elsewhere is not
  echoed under the old card, and a repeated incident starts a new thread.
- **Unchanged incidents.** An unchanged incident is still re-posted once per
  `FIX_RETRY_HOURS`, not folded into the digest.

Staging checklist, before any deploy:

1. Deploy to a box with a test Slack workspace, or read-only against production.
2. In a card thread, say "what happens if I approve?". Expect the card's detail and "Nothing was executed".
3. Say "don't approve <id>". Expect "Not approving #<id>".
4. Say "yes" at the top level with two cards pending. Expect "Which one?" and nothing executed.
5. Say "yes" in one card's thread. Expect that card decided, with the receipt under it.
6. Type "ack" under an alert. Expect the check muted and "No action executed".
7. Ask a question in #cloudhost. Expect one answer, in the thread, shown once, with no literal `**`.
8. Check that the next lasting-check post uses the incident card shape.

## 8. Statement

Nothing on this branch has been deployed, merged into `phases-12-14`, or
sent to a real Slack channel. Deploying is a separate decision for Miro.
