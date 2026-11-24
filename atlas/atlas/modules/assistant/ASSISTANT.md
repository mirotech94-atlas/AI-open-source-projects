# ASSISTANT — ask ATLAS anything

`atlas/modules/assistant` is ATLAS's general-purpose assistant. You ask a
question and get an answer with its sources. It is built the way a
Claude-style assistant is built: a system prompt, your words, a closed set of
tools the model calls by name, a bounded loop, then a final answer. It is
then held to the rules in `CLAUDE.md`, which a chat assistant is not held to.

It is **off** until `ASSISTANT_ENABLED` is set to `1`, `true`, `yes` or `on`.
When it is off, everything below behaves exactly as it did before the module
existed:

- `ask …`, `search …` and `atlas, <question>` take their old paths.
- The channel is not special.
- `assistant.budget` is not on the board.

The hooks read the flag on every message. The board check is registered at
boot, so it appears after a restart.

## How to ask

| Where | How |
|---|---|
| Any Slack channel ATLAS reads | `ask <question>`: the router picks Ask or Search |
| Any Slack channel | `ask! <question>`: Search (the web), forced. In Slack, `search <words>` is ATLAS's search of your own records (`kernel/search.py`, 2026-10-06) and is taken first; the assistant's `search` form only applies outside Slack. An order an agent owns ("search up 50 local businesses … and make them one") goes to the agency either way |
| Any Slack channel | `ask cancel <run id>`: stops that run at its next step |
| Any Slack channel | `atlas, <question>`: reaches the assistant when what follows reads as a question and is not an order, and no earlier Slack path claims it. Chat stays chat, and a general-knowledge question stays with knowledge mode. `atlas, snapshot everything` still goes to the action picker |
| The assistant channel | Every plain message, once `SLACK_ASSISTANT_CHANNEL` names a channel (topic `assistant`). `stop` stops your runs there. Unset, this door is closed |
| Code | `ask(question, mode=None\|"ask"\|"search")` returns an `Answer`. `answer(…)` returns the rendered text. `cancel(run_id)` stops a run. The kernel read tool `assistant_ask` exists for the voice route to call later; it is not wired yet |

The explicit forms are parsed in code by `parse_explicit`, in Slack's own
request path, before anything else reads the message. The `atlas, <question>`
form goes through `kernel.intent.act`'s module `command` hook, the same hook
as `/atlas funnel ...`. It is parsed in code, never by a model, and only after
the channel has proved the words are Miro's: Slack, or a call ATLAS placed,
never an inbound call.

## Ask and Search

| Mode | Tools |
|---|---|
| **Ask** | `calculate`, `convert_units`, `time_now`. No web, no page, no weather, Wikipedia, dictionary or exchange rate |
| **Search** | Every tool below |

The router (`modes.route`) picks Search only when the question needs a fresh
or outside fact:

- current, latest, today, news, a price, the weather or a rate
- money in any currency
- a URL
- a recent year
- a figure about the world ("when did", "how many", "how tall")

That last rule exists because a number the model only remembers is a guess
(invariant 6). "How many seconds in 3 days" is arithmetic on your own
figures, so it stays Ask. Everything else is Ask. "Explain photosynthesis
simply" never touches the network.

Two exceptions narrow this further:

- **Questions about ATLAS itself** (the `atlas` skill) keep `atlas_status`,
  `atlas_module_status` and `atlas_capabilities` in Ask. These read ATLAS's
  own tables, not the outside world. Without them "is flycheap ok" would be
  answered with no fresh read. This deviates from the plan's literal Ask
  list (calculator, units, clock), and it is deliberate.
- **A private question** stays private even under `search`/`ask!`. It runs on
  the local model with local tools only.

## What comes back

The answer comes first. After it come anything that could not be verified,
the sources, and the mode and run id:

```
Lima is the capital of Peru.

Sources:
- https://en.wikipedia.org/wiki/Lima (read 15:02 UTC, 2026-10-06)
- https://example.org/peru (search result excerpt, 15:01 UTC, 2026-10-06; page not opened)

_search · run 3f9a1c2e_
```

The Sources list is built in code from what the tools actually read, and each
entry carries the time it was read:

- **"read"**: a page or API was fetched.
- **"search result excerpt"**: a search engine's snippet of a page nobody
  opened. If `read_page` later opens it, it is listed once, as read.

The model is told not to write URLs. A URL it writes anyway that no tool
returned is cut, along with its sentence.

## The tools (all read-only)

| Tool | What it reads | Source |
|---|---|---|
| `search_web` | Up to 5 hits for a query | Brave (`BRAVE_API_KEY`) or Perplexity through `_perplexity`, with its monthly cap, privacy scrub and name rule. If neither is set: "search unavailable" |
| `read_page` | One public page's title and text. robots.txt is respected; the body is capped at 512 KB and the text at 6,000 characters | The page, through `_netguard`: no loopback, mesh, Docker or metadata address, and every redirect hop is checked |
| `calculate` | Exact arithmetic: `+ - * / // % **`, sqrt, log, trig, round, pi, e. A unit right after a number is carried through (`2500 m / 40 min` = `62.5 m/min`). Adding different units is refused, never converted | A tree walk over the parsed expression; never `eval` |
| `time_now` | The date and time in an IANA timezone | The clock |
| `convert_units` | Length, mass, volume, speed, area, data, time, temperature; with a `display` (`2,500 m`) | A fixed table |
| `convert_currency` | An amount at the latest reference rate, with the rate's date | Frankfurter / ECB (`ASSISTANT_FX_URL`), read fresh every call, never remembered |
| `weather_now` | Current conditions for a place | Open-Meteo geocoding + forecast |
| `wikipedia_summary` | An article's lead | English Wikipedia REST |
| `define_word` | Definitions, senses, examples | dictionaryapi.dev |
| `atlas_status` | ATLAS's own uptime, deploy, errors, checks, proposals | `kernel.selfmodel.status()`: a fresh read; nulls are unknowns |
| `atlas_module_status` | Every check on one module, with its health and read time | ATLAS's own tables (`tools.builtin.module_status`); private modules are refused |
| `atlas_capabilities` | Module and action NAMES with summaries; Tier 3 is never listed | The registry, read-only |

The model sees a one-line description of each tool (`tools.MODEL_DESCRIPTIONS`),
not these docstrings.

## The skills

A skill is a prompt fragment plus the subset of tools it may use. It is
picked by trigger phrase, or by name: `ask(q, skill="compare")`. The skill,
the mode and the privacy rule are intersected, so each can only narrow what
is offered. Registration refuses any tool that is not one of the reads above.

| Skill | Triggers (examples) | Tools |
|---|---|---|
| `answer` (default) | — | all |
| `compare` | compare, vs, pros and cons | all |
| `explain` | explain like I'm new, in simple terms, what is, how does | all |
| `plan` | step by step, how do I, checklist | all |
| `summarise` | summarise, tl;dr, a URL | `read_page`, `time_now`, `calculate` only (always Search) |
| `fact_check` | fact check, is it true, myth | all (always Search) |
| `atlas` | atlas, yourself, what can you do, a module name | the three ATLAS reads, clock, calculator |

## The caps

| Bound | Default | Env |
|---|---|---|
| Web searches a day (rolling 24 h) | 40 | `ASSISTANT_SEARCH_PER_DAY` |
| Other outside reads a day (page, weather, Wikipedia, dictionary, rate) | 80 | `ASSISTANT_FETCH_PER_DAY` |
| Tool calls per question | 6 | `ASSISTANT_MAX_TOOL_CALLS` |
| Model turns per question | tool calls + 2 | — |
| Tokens per question (all turns) | 12,000 | `ASSISTANT_MAX_TOKENS` |
| Prompt per turn | 12,000 characters (~4,000 tokens at 3 chars/token) | — |
| Reply per turn | 700 tokens | — |
| One tool result as the model sees it | 2,500 characters | — |
| Wall clock per question | 90 s | — |

**Why the per-turn prompt cap.** Production runs `ATLAS_FREE_ONLY`. Groq
refuses a request over its 8,000-tokens-a-minute cap with HTTP 413, and
OpenRouter's `:free` models allow 50 requests a day. Before Phase 1, the
worst turn reached 43,782 characters (~14,600 tokens): six long pages piled
up in the history. Now each turn is built like this:

- The newest results are shown, clipped to fit.
- Older results are shown as a one-line note.
- The whole turn stays under 12,000 characters. The measured worst case is
  7,540.

Only what is *shown* shrinks. The grounding check still sees every result
whole.

**A caveat that remains.** Groq's cap is per minute, and a question of
several turns can send them all within one minute. The breaker and the
free-bench fall-through in `kernel/llm.py` handle a 429.

**How the day's count is kept.** The count is the audit log. Every tool call
is written as an `assistant.tool` row before the call is made. The row holds
the tool name and the parameter names, never their values. A count that
cannot be read spends nothing. The check `assistant.budget` shows the counts
against the caps. It is UNKNOWN when the log cannot be counted and DEGRADED
when a cap is spent.

## The run record

Every question is a row in `assistant_runs`:

- id, start time
- the question's SHA-256 (never its text)
- mode, skill, status (`queued`, `running`, `completed`, `failed`,
  `cancelled`)
- tool calls, tokens, provider, model, latency, error

If a record write fails, it is logged, and the answer still goes out.

**Cancel** is a signal, not a kill. It is checked before every model turn
and before every tool call. A run whose caller gives up (Slack's own answer
timeout) is finished as `cancelled`.

## What it will not do, on purpose

- **Act.** It has no actions and every tool is a read. Nothing in the package
  imports the executor. A tool name outside its own set is discarded before
  anything happens (invariants 1 and 2). Asked to send, post, buy or change
  something, it says it cannot, and that you can ask in Slack as
  `atlas, <request>`.
- **Guess a number.** Every figure in the final answer is checked against
  what the tools returned and what the question said
  (`kernel.grounding.unverified_numbers`). "2,500" matches a returned "2500".
  A figure nothing returned gets the model one more turn to read a source or
  drop it. If it still stands, the sentence carrying it is removed and the
  answer says "I could not verify: …" (invariant 6).
- **Remember a figure.** A rate, a temperature or a status is read fresh for
  every question. A question about ATLAS itself is a fresh read of its
  tables, never an earlier answer.
- **Send your private life to a cloud model.** A first-person question about
  your mail, messages, calendar, grades, documents, browsing, health or
  credentials (`privacy.py`) runs on the local model only, with none of the
  outside tools. If there is no local model, it says honestly that the local
  model is down; it never falls through.
- **Claim to be human.** The identity line says it is software Miro runs. It
  never claims feelings, or to have done anything in the world.
- **Follow a page.** Tool results are quoted data. An instruction inside one
  is reported (`injection.suspected` in the audit), not obeyed.

## Phase 1 vs the plan

Miro's *Atlas Implementation Plan* (6 Oct 2026) scopes the first change to
**Phase 1: Ask + Search**. Its handoff says to stop before Research, memory,
artifacts and Act.

**Done in Phase 1**

- Ask and Search modes: `ask!`/`search` force Search, and the router picks
  Search only for fresh or outside facts.
- A closed set of read-only tools with validated parameters, and skills that
  can only narrow it.
- Sources with read times. A search excerpt is never presented as a page
  read.
- The grounding check on every figure. Units are carried through
  `calculate`; the plan's example "2.5 km to metres, then ÷ 40 minutes" gives
  2,500 m and 62.5 m/min.
- The privacy route to the local model.
- Daily caps and per-question caps, and a per-turn prompt budget sized for
  the free tiers.
- The `ASSISTANT_ENABLED` flag, off by default.
- The run record, `assistant_runs`, and cancel (`ask cancel <id>`, `stop`).

**Deferred to later phases**

- **Research**: multi-step, long-running research with a report.
  `research.report` and Perplexity's deep research already exist and are not
  wired here.
- **Memory**: no recall of earlier conversations, and no "I remember you
  asked". Each question stands alone, which is also what invariant 6 wants
  for figures.
- **Artifacts**: no files, tables, charts or documents produced as output.
- **Act**: the assistant never runs or proposes an action. Actions stay with
  `intent.act` and the executor.
- **HTTP run endpoints and streaming**: there is no `/api/assistant/runs`
  (create, get, cancel) and no streamed tokens or tool events. The answer
  arrives whole.
- **Voice**: `assistant_ask` is registered and not wired into
  `api/voice.py`.

## Known limitations

- **Cancel works within one process.** `cancel` marks the row in
  `assistant_runs` and signals a run on this worker. A run on another worker
  sees only the row, and it does not read that row between steps yet.
- **No tokenizer in the image.** The prompt budget is in characters, at a
  conservative 3 characters a token.
- **Groq's per-minute cap spans turns.** One question can spend several turns
  in a minute. The model chain's breaker and fall-through handle the 429; the
  per-turn budget only guarantees that no single request is refused as too
  large.
- **`search_web` depends on its backend.** It uses Perplexity only through
  its MCP endpoint (`https://api.perplexity.ai/mcp`); the legacy
  chat-completions path was removed in Task 50.
  - Whether the MCP tools still answer after Perplexity's Sonar migration has
    not been verified live from this branch.
  - `perplexity.budget` probes `initialize` and `tools/list` only. A server
    that lists its tools but fails every call would still read OK there.
  - With `BRAVE_API_KEY` set, Brave is used instead.
