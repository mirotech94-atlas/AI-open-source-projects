# Module audit — which interfaces are real

Written 2026-09-22, after four modules turned out to have been built against
APIs that do not exist. Each entry says what was **verified against ground
truth** and what is still **assumed**, so no credential is chased for a
module that would 404 with it.

The pattern, four times over: a module names plausible endpoints, declares
`requires_env`, and is disabled before it can prove them wrong. The
credential never arrives, so nothing ever 404s, so the module looks like it
is merely waiting. `requires_env` turned "these paths are invented" into
"this is pending configuration" for months.

## Verified and rebuilt

| Module | Ground truth | Result |
|---|---|---|
| **flycheap** | repo routes enumerated from `src/app/api/**`; `/api/health` probed live | 11 checks, **no credential** — `/api/health` is public by design. `FLYCHEAP_API_TOKEN` named nothing and is deleted. |
| **guardian** | `hub/guardian_hub/routers/api_v1.py`; `my.vault.example/api/v1/health` probed | 3 live, 4 waiting on a **read-scope** token. Base was `vault.example`, the marketing site, which 404s the API. |
| **guardian** (write) | `hub/guardian_hub/routers/api_v1.py`, `write.py`, `schemas_api.py`, `schemas_dash.py`, `networks.py`, read at Guardian `bb6c439` (2026-09-25) — the one commit `guardian/__init__.py` and `guardian/incident.py` both now cite for this work. `write.py` changed substantially between `bb6c439` and the earlier `cbf6ec0` Task 37 cited for the read-only routes (the `/fix` and `/rec/decide` decision-ledger work), but `apply_alert_op`, `apply_device_update`, `queue_and_audit`'s `/scans`/`/speedtest` callers, `ALERT_OPS`, `DeviceWrite`, `Device`, `DeviceCard`/`DeviceDetail` and `NetworkRef` are byte-identical at both, so the routes below hold at either commit | **VERIFIED**, four real routes, each write-scope: `POST /v1/alerts/{key}/{op}` (`op` ∈ ack/snooze/resolve/reopen, `ALERT_OPS` in `write.py`); `POST /v1/devices/{mac}?network=<id\|slug>` (`DeviceWrite`, a partial update via `model_dump(exclude_unset=True)` — sending only `{"trusted": false}` never touches `label`/`always_on`/`owner`); `POST /v1/scans` (`trigger_scan` takes **no body and no `network` parameter at all** — it queues one sensor sweep for the whole tenant, not a chosen network); `POST /v1/speedtest` (same shape, not wired to an action). `guardian.alert_op`, `guardian.mark_untrusted` and `guardian.rescan` call exactly these, gated on `GUARDIAN_WRITE_TOKEN`. Also verified: the hub's `Alert` schema carries a real `network: NetworkRef \| None` field, but the hand-built `alert` dict `alert_evidence()` returns does not pass it through — Guardian's incident plan recovers a primary alert's network by matching its key against the same alert's row in the open-alerts or history pages already read, never by guessing. `GET /v1/devices/{mac}` (`DeviceDetail`, read-scope) is also now used, by `mark_untrusted`'s rollback, to read a device's prior `trusted` state before ever writing `true` back — never assumed. `GET /v1/alerts/{key}` (read-scope) is used the same way by `alert_op`'s rollback, to restore the status the alert actually held rather than a fixed op-to-op guess. **Verified absent, not merely unconfirmed:** a beacon alert's `evidence["mac"]`. `sensor/guardian_sensor/alerts.py`'s `build_alerts()` raises every finding through `add(sev, key, msg)` — no evidence argument exists at that call site — and the beacon line (`alerts.py:849`) is one of its callers; the hub's own ingest only copies `evidence` through from whatever the sensor sent (`cur.get("evidence") or {}`), inventing nothing. So a beacon alert's `evidence` is always `{}` on real data today, and `mark_untrusted` never fires automatically: every active beacon is MANUAL (plus `rescan`, when the network is known) until the sensor is changed to emit a mac — a ruling for the owner, not this task. |
| **inroots** (Roots) | `matrix-standup-bot/app/api.py`; `status.internal.example/health` probed | 1 live, 7 waiting on a **read-scope `k9_`** token. All four old paths were invented. |
| **netbird** | `mesh.example.com/api/peers` probed (401 = exists) | 3 waiting on a token. Was pointed at the hosted `api.netbird.io`, which holds none of the owner's peers. |
| **gitlab** | `aicode.example.com/api/v4/*` probed; both hosts are real GitLab | **Paths are correct** — standard v4. Only needs `GITLAB_TOKEN`. |

## Base URL correct, specific paths unverified

These are public APIs whose base URLs are right. The paths look standard but
cannot be confirmed without a credential, and that is where errors hide. The
first real call will say; each check reports UNKNOWN naming the path rather
than guessing.

| Module | Base | Needs |
|---|---|---|
| **calendar** | `googleapis.com/calendar/v3` | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` |
| **mail** | `gmail.googleapis.com/gmail/v1` | same three (one OAuth app covers both) |
| **stripe_billing** | `api.stripe.com/v1` | `STRIPE_GUARDIAN_KEY`, `STRIPE_FLYCHEAP_KEY` — **restricted, read-only**. Money out is Tier 3 and has no override. |
| **instagram** | `graph.facebook.com/v21.0` | `INSTAGRAM_ACCOUNT_ID`, `INSTAGRAM_TOKEN` |
| **tiktok** | `open.tiktokapis.com/v2` | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN` |
| **exampleco** | `api.ebay.com` (`/buy/browse/v1`, `/sell/fulfillment/v1`, `/sell/inventory/v1`) | `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_REFRESH_TOKEN` |

## Unverified, and worth checking before spending effort

| Module | Assumed | Why it is doubtful |
|---|---|---|
| **cloudco** | `api.cloud-provider.example/apps/v2/{vm,snapshots,billing/summary}` | Not confirmed anywhere. The `cloudco-deployer` agent covers hardware and Proxmox, not this API. Given the record above, assume these are invented until a call proves otherwise. |
| **fiverr** | a saved browser session | **Blocked by Fiverr.** PerimeterX challenges every headless request — from the server *and* from the owner's Mac with his live session, so it is the automated browser being fingerprinted, not the IP. No selector fixes this. Same ruling as LinkedIn: driving a site that actively blocks automation is how the account gets suspended. The module now reports the wall as UNKNOWN; real signal should come from Fiverr's notification mail, which the mail module already reads. |
| **matrix_rooms** | Matrix client-server v3 | Paths are spec-correct. Needs `MATRIX_TOKEN`. |

## Phases 12–20 — what each new module assumes (2026-09-24)

Same rule as above: **verified** means a live call or the vendor's own
source was read, and says which; everything else is **assumed**, and the
module reports `Health.UNKNOWN` naming the path or tool rather than a
number when the assumption is wrong. None of these was exercised with a
real credential: no credential for any of them is set. Passing unit tests
are not verification here — they run against fakes built from the same
assumption.

| Module | Endpoint or tool | Status | What would settle it |
|---|---|---|---|
| **agency** (mockup, build) | GetLayers MCP at `GETLAYERS_MCP_URL` (default `https://mcp.getlayers.ai/mcp`): tools `getlayers_search`, `getlayers_compositions`, `getlayers_materialize` | **assumed.** The same three names appear in the GetLayers plugin exposed to Claude Code on 2026-09-24 — not a call to the server ATLAS calls, so not verification. `atlas/modules/_mcp.py` refuses by name when `tools/list` lacks one. The argument and result shapes `mockup.py` / `build.py` send and read are unchecked. The commercial licence is unconfirmed, so `AGENCY_GETLAYERS_LICENCE_OK` gates both | one `agency.mockup` with `GETLAYERS_TOKEN` set |
| **agency** (prospect, audit) | Places API (New) `POST places.googleapis.com/v1/places:searchText`; PageSpeed Insights v5 `GET www.googleapis.com/pagespeedonline/v5/runPagespeed` | assumed (public, documented APIs; paths look standard) | `GOOGLE_PLACES_API_KEY` and one `agency.prospect` |
| **agency** (send, deploy) | SMTP at `AGENCY_SMTP_*`; `rsync` over `ssh` to `AGENCY_DEPLOY_HOST:AGENCY_DEPLOY_ROOT` | assumed | a first approved send and deploy |
| **content** (render) | OpenArt MCP at `OPENART_MCP_URL` (default `https://mcp.openart.ai/mcp`); tool `render_video` | **assumed.** The URL and the tool name are guesses, but never called blind: `render_video` asks `list_tools()` first and refuses by name when the server does not offer `render_video`, and reads the arguments off that tool's `inputSchema`, falling back to the originally assumed `{"script": ...}` only when the schema names nothing more specific | `OPENART_TOKEN` and one render |
| **content** (render: Veo) | `POST generativelanguage.googleapis.com/v1beta/models/{VEO_MODEL}:predictLongRunning` (default `veo-3.1-fast-generate-preview`; `veo-3.1-generate-preview`, `veo-3.1-lite` also exist) with `{"instances":[{"prompt"}],"parameters":{"aspectRatio":"9:16","durationSeconds":8}}`, `x-goog-api-key`; poll the operation until `done`; clip at `response.generateVideoResponse.generatedSamples[0].video.uri`, downloaded with the same header | **VERIFIED live** on the owner's Gemini key (Task 55, relayed from production, 2026-09-24): 720×1280, 8 s, native audio, 1–2 min per clip. Not Sora: OpenAI's `/v1/videos` answered "Invalid endpoint". Charged per second on his Gemini billing, capped by `VEO_MONTHLY_SECONDS` (`veo_usage`) | — |
| **content** (render: stills) | `POST …/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent`, `responseModalities: ["IMAGE"]`, `imageConfig.aspectRatio: "9:16"`, `x-goog-api-key` | **VERIFIED live** for the model names `gemini-3.1-flash-image` (the default) and `gemini-3-pro-image` on the owner's key (Task 55, relayed from production: 6 stills at 9:16). The `imageConfig` field name and the `inlineData` response shape are **assumed**; a 404 refuses the stills with the model name and the render continues with typographic cards | one local render with stills |
| **content** (render: captions) | Groq `POST api.groq.com/openai/v1/audio/transcriptions`, `whisper-large-v3`, `response_format=verbose_json`, `timestamp_granularities[]=word,segment` | as specified from production (Task 55); the `words[].word/start/end` shape is Groq's documented OpenAI-compatible one, **assumed** here until the first Veo render | `GROQ_API_KEY` and one Veo render |
| **content** (render: ffmpeg) | `ffmpeg` / `ffprobe` on the box: `zoompan`, `drawtext` (libfreetype), `ass` (libass), `concat` demuxer | **VERIFIED** in the production image for `ass` and `subtitles` (libass) and DejaVu as its only fonts (2026-09-24); the argv shapes also ran clean against a local ffmpeg 7 on 2026-09-24 (1080×1920 h264 + aac). `content.render_backend` reads `ffmpeg -filters` every six hours and is UNKNOWN when a filter the chosen backend needs is not listed | — |
| **content** (cross-post: Facebook) | Graph v21.0 `POST /{FACEBOOK_STORIES_PAGE_ID}/videos` (`file_url`, `description`), `DELETE /{video-id}` for the rollback, Page token as a bearer header | the token is **VERIFIED** on production: a non-expiring Page token with `pages_manage_posts` (Task 55, relayed 2026-09-25), so this leg is live. The `/videos` upload-by-URL call and its `{"id"}` answer are Graph's documented shape, **assumed** until the first approved reel | `FACEBOOK_STORIES_PAGE_*` and one approved reel |
| **content** (cross-post: TikTok via Postiz) | Postiz Public API at `POSTIZ_API_URL` (default `https://api.postiz.com/public/v1`), key as the raw `Authorization` header: `POST /upload` (multipart) → `path`; `GET /integrations` (identifier `tiktok-business`, or `tiktok`; since 2026-09-28 chosen per account: the clips lane only the integration `POSTIZ_TIKTOK_CLIPS_INTEGRATION` names, the reels lane never that one nor one named `atlasclips12`, and with several left only `atlasstories7`, else it refuses; a clip's link is read from the post answer's `releaseURL`, **assumed**, and used only when it is on the account posted to); `GET /integration-settings/{id}`; `POST /posts` (`type: now`, settings `__type: tiktok`, `privacy_level: PUBLIC_TO_EVERYONE`, `duet`, `stitch`, `content_posting_method: DIRECT_POST`); `GET /posts/{id}/missing` + `PUT /posts/{id}/release-id` for a missing TikTok id; `GET /posts?startDate&endDate` for the state, `DELETE /posts/{id}` while still queued | **VERIFIED live on production (2026-09-25):** the auth shape — the raw key as `Authorization: <key>`, since `Bearer <key>` answers 401 "Invalid API key" — and `GET /integrations`, which returned `[{"identifier": "tiktok-business", "name": "atlasstories7", "disabled": false}]` (matched on `tiktok-business`, or `tiktok` should Postiz rename it; disabled ones skipped). The upload, settings, post, missing-content, release-id, listing and delete endpoints stay **assumed** from Postiz's public docs until seen live (The owner's choice, Task 55); the settings, missing-content and release-id paths and their response shapes are the least certain: an unreadable settings answer refuses the leg (Postiz drops unknown settings silently, so nothing is posted blind), a missing-content 404 leaves the leg "posted, id pending", and an unconfirmable state leaves a published post alone with a MANUAL note (deleting a published post is Tier 3) | `POSTIZ_API_KEY` and one approved reel |
| **clips** (YouTube Shorts via Postiz) | The same Postiz Public API and key: `POST /upload` → `path`; `GET /integrations`, taking ONLY the one `POSTIZ_YOUTUBE_CLIPS_INTEGRATION` names (id or name), which must have identifier `youtube` and not be either TikTok account; `GET /integration-settings/{id}` (every key sent must be declared); `POST /posts` (`type: now`, settings `__type: youtube`, `title`, `type: public`, `selfDeclaredMadeForKids: no`, `tags: [{value, label}]`, the full caption as the post content); `GET /posts?startDate&endDate` and `DELETE /posts/{id}` while still queued | **Assumed** from Postiz's public docs (the `postiz` skill's PROVIDER_SETTINGS and its youtube-video example) until seen live: the `youtube` identifier, the settings shape, and that the post answer carries `releaseId` (the video id) and a `releaseURL`; the link is recorded only when that URL is a YouTube one for the same id, else the owner sends it (`clips.record_link`). Postiz declares no paid-promotion setting for YouTube, so `#Ad` in title and description is the disclosure. A published Short is never deleted (Tier 3): a MANUAL note to set it private | `POSTIZ_API_KEY`, `POSTIZ_YOUTUBE_CLIPS_INTEGRATION` and one approved clip |
| **content** (reel results) | Instagram `GET /{media-id}/insights?metric=views,reach,likes,comments,shares,saved,total_interactions` (stories token); Postiz `GET /analytics/post/{id}?date=7`, and `{"missing": true}` reported as "connect it by hand (Postiz)" — the check never writes; the one `PUT /posts/{id}/release-id` happens inside `content.publish_reel` at posting time, only for exactly one caption-matched candidate | **partly verified.** The token carries `instagram_manage_insights` since 2026-09-25; the account-level call (`reach,profile_views,accounts_engaged`, `metric_type=total_value` → `total_value.value`) was read live on both accounts. The media-level names are Meta's current ones (`views`, not the deprecated `plays`) and are read on the first posted reel. The Postiz analytics answer (`[{label, data: [{total}]}]`) is read from the `postiz` skill's description, and the REST path is assumed; the largest daily total is taken because the docs do not say whether points are cumulative. A metric that is not read is NULL, never 0 | the insights permission, and one TikTok post read |
| **content** (YouTube Shorts via Postiz) | Postiz `POST /upload` → `path`; `GET /integrations` (the one YouTube integration `POSTIZ_YOUTUBE_INTEGRATION` names, by id or name, among those whose `identifier` / `providerIdentifier` / `platform` is `youtube`); `GET /integration-settings/{id}`; `POST /posts` (`type: now`, settings `{"__type": "youtube", "title", "type": "public", "selfDeclaredMadeForKids": "no", "tags": [{"value", "label"}]}`, the description as content); `GET /posts?startDate&endDate` + `DELETE /posts/{id}` only while queued | **VERIFIED 2026-09-29** against Postiz's own schema for `youtube` (the MCP `integrationSchema` and docs.postiz.com/public-api/providers/youtube): `title` 2-100 and `type` public/private/unlisted required, `selfDeclaredMadeForKids` the string `yes`/`no`, `tags` as value/label pairs, optional `thumbnail`, content <= 5000; **no AI / altered-content field exists**, so the disclosure is in the description. The MCP listing (2026-09-29) shows `{"id": "postiz_example_id_00", "name": "AtlasClips", "platform": "youtube"}`. **Assumed:** that REST `/integrations` names it in `identifier` (as the TikTok row seen live does; all three keys are read), and the post answer's `releaseURL` (kept only when it is a YouTube link) | `POSTIZ_YOUTUBE_INTEGRATION`, and one Short read back in YouTube Studio |
| **course** (organic posts for the owner's course) | The store's public `GET <product>.json` (no credential; https, the store's own host, no redirects); Postiz `GET /integrations`, `GET /integration-settings/{id}` (a provider's `required` settings refuse the post), `POST /posts` (`type: now`, `shortLink: false`, text only, settings `{"__type": <provider>}`); PostHog HogQL on `events` (`properties.$host`, `properties.utm_campaign`) | **VERIFIED live 2026-09-29:** the product JSON (`$20`, compare-at `$49`, USD). **Assumed** from Postiz's docs: the text-post body and the settings `required` list; the last live `/integrations` read on record (2026-09-25) listed only TikTok `atlasstories7`, so no text channel is known to be connected. **Not available:** Shopify orders (no Admin API credential; sales are always UNKNOWN), a Shopify blog write (the article is a hand-off card), LinkedIn profile posting (hand-off card), YouTube community posts (no API; module off) | a text channel connected in Postiz (X, LinkedIn, Threads, Bluesky, ...); the store sending PostHog events for visits to be read |
| **perplexity** | MCP at `PERPLEXITY_MCP_URL` (default `https://api.perplexity.ai/mcp`), tools `perplexity_search`, `perplexity_ask`, `perplexity_reason`, `perplexity_research`. No fallback (Task 50 removed it) | **The four tool names are VERIFIED**: a live, authenticated `tools/list` against the real server on 2026-09-25 returned exactly these four. **A live `perplexity_ask` call on the same day returned citations, also VERIFIED** — Perplexity's `tools/call` for that tool really does answer with sources, not just prose. Which of the three citation paths `_parse_mcp` tries (`structuredContent.citations` / `search_results[].url` / a trailing "Sources:" block) that live answer actually used was not recorded, so the result's exact shape stays **assumed** — `_perplexity.py`'s docstring says where to correct it. Also verified live, 2026-09-25: the chat-completions endpoint is dead (`POST /chat/completions` → `403 chat_completions_not_available`) and `/v1/responses` rejects model `sonar` — this is why Task 50 deleted the fallback rather than fixing it | one more live call per tool to pin down the citation shape |
| **_web** (Firecrawl fallback) | `POST https://api.firecrawl.dev/v1/scrape`, `formats: ["markdown"]` | assumed (Firecrawl's documented v1 scrape) | `FIRECRAWL_API_KEY` and one JavaScript-only page |
| **voice** (speech) | Fish Audio `POST https://api.fish.audio/v1/tts`, `reference_id` = `FISH_AUDIO_VOICE_ID`, model header default `s1`, body `{text, format: "mp3", reference_id}` | **verified live** on production, 2026-09-24: 200 with 44.1 kHz mono MP3 in about a second, played by Twilio `<Play>`; HTTP 402 "Insufficient API credit" when the API credit is spent | — |
| **voice** (`fish_credit`) | Fish Audio `GET https://api.fish.audio/wallet/self/api-credit`, bearer key; balance read from the first of `credit`, `api_credit`, `balance`, `amount` (top level or under `data`) | **assumed until a response is seen.** Any other shape is UNKNOWN "unexpected shape" naming the fields present, never their values | `FISH_AUDIO_API_KEY` and one `voice.fish_credit` run |
| **phone line** (Task 54) | Twilio `<Gather input="speech dtmf">`, `<Play>` from `/webhooks/twilio/audio/{token}`, webhook parameters `CallSid`, `Direction` (`outbound-api` for REST-created calls), `From`, `To`, `StirVerstat` | assumed per Twilio's documented request parameters; `<Play>` of Fish MP3 seen working on production | one real call each way on production |
| **messages** (iMessage) | BlueBubbles `POST /api/v1/message/query`, `POST /api/v1/message/text` (`method: private-api`) | **partly verified**: `/api/v1/chat/query` is exercised by `atlas.cli imessage_chats`; the two message paths and `dateCreated` in epoch ms are assumed | `BLUEBUBBLES_URL` / `BLUEBUBBLES_PASSWORD` over the mesh |
| **messages** (Instagram DMs) | Graph `GET /{ig_id}/conversations?platform=instagram`, `POST /{ig_id}/messages`, `GET /debug_token` for the scope check | assumed; the `debug_token` call reuses the user token where Graph normally wants an app token, the first thing to correct if the scope check is always UNKNOWN | a token with `instagram_manage_messages` |
| **instagram** (stories account, publish, moderation) | Graph v21.0 `/{ig_id}/media` → `/{ig_id}/media_publish`, comment hide/reply; `/{ig_id}/insights?metric=reach,profile_views&period=day&metric_type=total_value` | publish assumed (Task 25); the insights call **verified live 2026-09-25** on both accounts — without `metric_type=total_value` Graph answers 400 (#100) | `INSTAGRAM_STORIES_*` and one Tier 2 publish |
| **messaging** | WhatsApp Cloud API `graph.facebook.com/v21.0/{WHATSAPP_PHONE_ID}/messages` and its webhook signature; Telegram Bot API; Slack `conversations.history` / `conversations.replies` | assumed (documented public APIs) | each channel's token and one inbound message |
| **school** | Brightspace calendar subscription (`.ics` from `BRIGHTSPACE_ICS_URL`); D2L notification emails in Gmail; Valence `GET {BRIGHTSPACE_HOST}/d2l/api/le/1.67/calendar/events/myEvents/` | feed and email subjects parsed from their documented formats, **assumed** until the real feed is read; Valence **not granted** by the university, so it is never called and says so | `BRIGHTSPACE_ICS_URL` and a week of notification mail |
| **youtube** | Data API v3 `www.googleapis.com/youtube/v3`, resumable upload `upload/youtube/v3/videos`, Analytics API v2 `youtubeanalytics.googleapis.com/v2/reports` | assumed (documented public APIs) | `YOUTUBE_*_*` per channel and one private upload |
| **browser** | Playwright Chromium; Brave Search `api.search.brave.com/res/v1/web/search`, DuckDuckGo HTML otherwise | Chromium verified live by Task 24 (every redirect, frame, worker and socket checked against private addresses); search endpoints assumed | `BRAVE_API_KEY` |
| **screening** | Twilio voice webhooks `/webhooks/twilio/screen*` (signature-verified, the same check as the existing Twilio routes) | assumed for the second number | `SCREENING_FROM` and one call to it |
| **purchasing** | eBay Browse API `api.ebay.com/buy/browse/v1/item_summary/search` for carts; no fare source | assumed; `prepare_flight` has nothing to search until flycheap exposes one | `EBAY_*` |

## Token scopes to ask for

Two of these products enforce read-only in the credential rather than in
ATLAS's code, which is the stronger guarantee — it survives any future change
to this repo.

- **Guardian: read scope.** A write token permits `POST /v1/devices/{mac}`,
  which sets `trusted` and `label`. Those are human vouches; Guardian's own
  test suite fails if an automatic path writes either, and a trusted device is
  skipped by the rogue-uplink check and exempt from risky-port alerts.
  Forging one does not produce an audit entry — it produces a rogue device
  nobody hears about. `guardian.token_scope` reads `/v1/whoami` and reports a
  write token as a finding.
- **Roots: read scope.** A `read`-scoped `k9_` token is refused any non-GET
  *before the route runs*, and a token acts as a user and can only narrow that
  user's authority.
- **Stripe: restricted, read-only keys.** Money out is Tier 3.

## Tiers corrected while auditing

- `inroots.nudge_missing` — **removed.** It messaged colleagues about their
  attendance, which is what the owner's Matrix rules forbid, down a different pipe.
  Its endpoint did not exist either.
- `gitlab.open_issue` — **Tier 1 → Tier 2.** An issue emails every watcher and
  hits the activity feed on creation; on the work instance those are
  colleagues. The offered rollback closes the issue, which undoes none of
  that.
- `inroots_nudge` (the agent write tool) — **removed 2026-09-24.** It kept
  pointing at `inroots.nudge_missing` after the action went;
  `tests/test_tool_catalog.py` now fails on any write tool naming an action
  no module declares.
- `instagram.hide_comment` — **Tier 1 → Tier 2 (2026-09-24).** Hiding a
  comment is a decision about another person (REELS `Da3bCbINuQf`), and at
  Tier 1 the scheduler could have run it unattended on a
  `comments_pending` finding.
- `linkedin.draft_post`, `flycheap.rerun_scraper`, `flycheap.regenerate_links`,
  `guardian.requeue_alerts` — **removed.** Endpoints that do not exist, or a
  second write path around a control that exists for a reason.
- `guardian.contain` — **kept, permanently refusing (2026-09-25).** Task 37
  assumed `POST /v1/containments`; Task 52 re-read the hub's write-scope
  surface end to end and found no containment route exists at all. Rather
  than remove the action, `refusal()` now answers "Guardian has no
  containment API (block/isolate not exposed)" for every kind,
  unconditionally, so an incident card can still show a client what
  containment v1 cannot do. `block_ip` and `isolate_host` join
  `disable_account` as MANUAL playbook entries. The four routes v1 actually
  exposes — alert ack/snooze/resolve/reopen, a device's `trusted`, a scan, a
  speed test — get their own actions instead: `guardian.alert_op`,
  `guardian.mark_untrusted`, `guardian.rescan` (see the table above).

- `instagram.funnel_reply` — **Tier 1, kept (2026-09-30, kernel-owner
  review).** It is outward-facing and its DM leg cannot be recalled: the
  rollback hides only the public reply, and says so. Accepted because
  nothing is decided at run time — the public reply and the DM text were
  approved once, whole, on the Tier 2 `instagram.funnel_set` card; their
  sha256 is recomputed from the row before every send and a mismatch
  refuses; and the recipient asked for it by commenting the keyword (a
  whole-word match recorded first — there is no other path to a person).
  Once per person per funnel, under a daily cap, never 22:00–08:00; a
  policy or block error stops the funnel. Listed in
  `tests/test_products.py`'s ruled exemptions with these reasons.

A sweep test now fails if any module ships a Tier 1 action whose verb is
outward-facing. Verified draft-only actions are exempt, and both were read to
confirm it rather than taken on their docstring's word.

## Still true

Nothing above changes the fact that **no reporting channel is configured**.
Slack, Twilio and BlueBubbles are all unset, so a CRITICAL check flipping
today reaches nobody. The channel code is fixed and tested; it has nothing to
send through.
