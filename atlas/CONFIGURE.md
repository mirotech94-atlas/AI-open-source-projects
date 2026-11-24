# Configuring ATLAS

Which value unlocks what, where to get it, and how to install it without it
ever appearing in a chat.

For *why* a scope is what it is, and which endpoints were verified against
ground truth, see [MODULE_AUDIT.md](MODULE_AUDIT.md). Scope reasoning lives
there and only there, so the two files cannot drift apart.

## Never paste a secret into a chat

A value typed into a conversation is in that conversation permanently, and the
only remedy afterwards is to rotate it. That has already happened twice here.

```bash
bootstrap/set-secret.sh ATTENDANCE_TOKEN
```

It prompts with `read -rs` so nothing echoes, writes straight into the
server's `.env` over SSH stdin — so the value is not in a command line, in
`ps`, or in either shell's history — restarts the API, and verifies by
re-running the module's checks rather than by printing the value back.

It escapes a literal `$`, because Compose interpolates `.env` and an
unescaped `$` silently truncates the value. That cost a day once: an argon2
hash arrived as an empty string and every correct password was rejected with
no error anywhere.

## What is configured, right now

```bash
python -m atlas.cli connect            # the whole table
python -m atlas.cli connect attendance    # one module, with its checks run
```

`connect` is the source of truth, not this file. It distinguishes:

- **BLOCKED** — the module will not load until these are set
- **partial** — it is loaded and reporting, but some checks are UNKNOWN
  without these
- **no credentials needed**

## The roster: eight agents (2026-10-28)

The roster keeps eight agents: `inbox` (personal ops, and the front door for
any message no other agent owns), `sites` (website clients: preview approvals
and owner emails), `content`, `sales`, `bizops`, `seo`, `research` and `qa`.
Eighteen retired: agency, analyst, commerce, concierge, fiverr, flycheap,
growth, guardian, attendance, marketing, markets, mirotech, ops, reels,
scheduler, school, sentinel, cloudhost. Their module code, collectors, tools,
tables and data stay; only the roster agent, its department seat and its map
node went. A pending card a retired agent raised expires at the next sweep and
can never be claimed, so it never runs (silence is never consent).

There is no env var or runtime switch for this. Rollback is code only:
`git revert` the commits from `feat(agents): the roster keeps eight agents`
through the UI commit that follows it, then deploy through CD. Cards that
expired meanwhile stay expired; nothing re-raises them.

## Six agents: Sales, Content, BizOps, SEO, Research, QA (2026-10-27)

Every agent ships and loads without these; whatever needs one reads UNKNOWN
(grey, dashed, with the word) until it is set. Nothing in the build waits on
them. Set values with `set-secret.sh`, never in chat or in a file in the repo.

1. **Brave Search** free plan key -> `BRAVE_API_KEY`. Without it the shared
   scraper's search returns "no free search provider" (it never falls back
   to Perplexity).
2. **Google PageSpeed** key (free) -> `GOOGLE_PAGESPEED_API_KEY`; **YouTube
   Data API** key (free, public reads) -> `YOUTUBE_API_KEY`. Optional: Google
   CSE (`GOOGLE_CSE_KEY`, `GOOGLE_CSE_CX`), Bing Webmaster
   (`BING_WEBMASTER_KEY`).
3. **Search Console**: the Google consent now asks for `webmasters.readonly`
   (read only) beside `gmail.modify`, `calendar.events` and `userinfo.email`
   (`ATLAS_GOOGLE_SEARCH_CONSOLE=0` leaves it out). A refresh token granted
   before 2026-10-29 lacks it, and `search_console` says so. To fix it:
   3a. In the Cloud project of `GOOGLE_CLIENT_ID`, enable the **Google Search
   Console API** (APIs & Services -> Library). Without it Google answers 403
   and the check says the API is not enabled.
   3a. To let ATLAS register a real site itself (add the property, verify it by
   the META tag, submit the sitemap, each a Tier 2 card, never DNS), run 3b as
   `ATLAS_GOOGLE_SEARCH_CONSOLE_WRITE=1 ./bootstrap/google-login.sh --reconsent`
   and also enable the **Site Verification API**. Off by default; Vercel client
   previews are noindex and are never registered or counted.
   3b. On the Mac, from the checkout: `./bootstrap/google-login.sh --reconsent`
   (reuses the client in `.env`, no file). Sign in as the account that owns
   the Search Console properties and approve all four. It replaces
   `GOOGLE_REFRESH_TOKEN` with one that still carries Gmail and Calendar,
   restarts the api and runs `connect mail`, `connect calendar` and
   `connect seo`. If Google returns no refresh token, revoke ATLAS at
   https://myaccount.google.com/permissions and run it again.
   3c. In Search Console, add and verify each client domain and the store,
   agency.storefront.test (a Domain property `sc-domain:` is preferred; a URL
   prefix property also works). UNKNOWN per site until then.
4. **Stripe test mode**: a restricted key with Customers write, Invoices
   write, Invoice Items write and nothing else -> `MAPFRONT_STRIPE_INVOICE_KEY`.
   Only `sk_test_`/`rk_test_` keys are accepted; a live key is refused in
   code. The live key is a later decision.
5. **Reddit**: create the API app and accept the developer terms ->
   `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REFRESH_TOKEN`,
   `REDDIT_USER_AGENT`; list the subreddits allowed ->
   `REDDIT_SUBREDDIT_ALLOWLIST`. Every post, comment and profile edit is a
   card; `reddit.account` reads UNKNOWN until these are set.
6. **Playwright** (screenshots, PDFs). It ships disabled (no
   `--profile qa-browser`, `QA_BROWSER_URL` unset: every screenshot reads
   UNKNOWN). The egress deny is in `docker-compose.local.yml`: the browser
   sits on `qa_egress`, an `internal` network with no route off the host,
   and its only exit is `qa-proxy`, which forwards CONNECT to the allowlisted
   public hosts on 443 and refuses private, loopback, link-local and CGNAT
   addresses. The browser launches with that proxy always set (a dead
   address when `QA_PROXY` is missing) and WebRTC UDP off; the in-page rules
   (route allowlist, no WebSocket, no popups) are extra, not the control.
   6a. Check it before turning it on (this build could not run Docker):
   `docker compose -f docker-compose.yml -f docker-compose.local.yml --profile qa-browser run --rm qa-browser python3 -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)"`
   must fail (no route), and
   `curl -x http://qa-proxy:3128 https://agency.storefront.test/` from a container on
   `qa_egress` must answer while `curl -x http://qa-proxy:3128 https://10.0.0.1/`
   is refused 403. If the first command connects, keep the browser off.
   6b. Then
   `docker compose -f docker-compose.yml -f docker-compose.local.yml --profile qa-browser pull`
   then the same with `up -d qa-browser`, and set
   `QA_BROWSER_URL=unix:/run/qa/qa-browser.sock`, then recreate the api so it
   mounts the socket volume. Without it screenshots read
   UNKNOWN and proposals read "PDF not rendered". The QA gate does not need
   it: only the file launch check counts as a site's pass.
   `qa.security_scan` looks only at Mapfront's own hosts and sites Mapfront
   built; a site someone else built needs its owner's consent recorded in
   `agency_sites.security_consent_at` first. `QA_VERIFY_RUNS_MIN` (default 60)
   sets how often QA compares the scheduled windows with `schedule_runs`.
7. **Slack topics** (optional; code defaults to the same IDs):
   `SLACK_CHANNELS` += `agency_decisions:C0C3NHNKNSJ,agency_alerts:C0CCHC6DWMT`.
8. **Research seed lists**: Mapfront and client competitors. The Academy list
   is seeded with `python -m atlas.cli research-seed --file .claude/competitors-2026-10-10.json`
   (reads the file, never writes it).
9. Optional: one live Stripe **test** invoice with your test key to see the
   flow end to end.
10. **Sales** (none required): drop lead CSV files (`name,category,website,
   email,phone,address,city,country`; `name` and `category` required) in
   `data/sales/inbox/`; the 09:00 New York shift (`SALES_SHIFT_HOUR`) imports
   each file once. `SALES_DAILY_CARDS` (default 10) caps outreach cards a
   day. For proposal PDFs, serve `data/sales/proposals/` at an https URL on
   the qa-proxy allowlist and set `SALES_PROPOSAL_BASE_URL` (plus
   `QA_BROWSER_URL`, step 6); until then each proposal is HTML and says "PDF
   not rendered". New York leads come from Places with
   `GOOGLE_PLACES_API_KEY`, else from OpenStreetMap (no key, ODbL).
11. **Content** (the `content` agent). Reddit needs step 5; until then
   drafts land in `reddit_drafts` as `needs_rules` and raise no card.
   `REDDIT_POSTS_PER_WEEK` (default 2) caps posts; one in ten may be
   promotional. `ACADEMY_PLAN_PER_WEEK` (default 3) sets topics planned a
   week. The Monday 08:00 New York `content_weekly` job writes the growth
   report, plans topics and steps title tests; store clicks read "not
   measured" until `POSTHOG_PERSONAL_KEY` is set. Title tests and comment
   replies need the clips channel token with the `youtube.force-ssl` scope
   (already used by `academy.refresh_video_metadata`). Run the read-only
   channel study once per channel and read the report:
   `python -m atlas.cli channel-study --channel clips` (analytics needs the
   `yt-analytics.readonly` scope; without it each video reads UNKNOWN).
12. **BizOps** (the `bizops` agent, Sales department). Without step 4 the
   invoice checks read UNKNOWN and `bizops.send_invoice` is refused; a live
   key reads FAILING and is refused before any call. At 08:00 New York
   `bizops_digest` onboards each new won/paid deal once (a project with five
   tasks and a welcome-email card), drafts reminder cards for overdue test
   invoices (one per invoice per 7 days) and posts the digest to
   #agency-alerts; at 07:00 `bizops_find_repeats` cards work seen 3+ times in
   14 days. The welcome email proposes kickoff times read from the calendar;
   the invitation itself is still the agency's `agency.send_invite` card once
   you hold a slot. Invoices are drafted by asking for `bizops.draft_invoice`
   on a won lead (amount: the won deal's, else the lead's price; only $500 or
   $300).
13. **SEO** (the `seo` agent, Sales department). What Miro still has to do:
   - **Search Console consent**: steps 3a-3c above. Enable the Google
     Search Console API on the OAuth client's project, run
     `./bootstrap/google-login.sh --reconsent` on the Mac (adds
     `webmasters.readonly`, keeps Gmail and Calendar), and verify every
     client domain and agency.storefront.test in Search Console
     (a `sc-domain:` property is preferred). Until then `seo.search_console`
     reads UNKNOWN and its detail names that exact command; a site not
     verified is UNKNOWN by name.
   - **PageSpeed key**: `./bootstrap/set-secret.sh GOOGLE_PAGESPEED_API_KEY`
     (a Google Cloud API key restricted to the PageSpeed Insights API, free).
     Without it `seo.core_web_vitals` uses the keyless quota and reads
     UNKNOWN on a 429.
   - **Schema**: the new `seo_reports` table is in `db/schema.sql`; the next
     deploy applies it (locally: `docker compose exec -T postgres sh -c
     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/schema.sql`,
     idempotent).
   - Nothing else: `BRAVE_API_KEY` (step 1) already feeds ranks and the
     Academy gaps.

   What it does. `seo.search_console` (every 6 h, read only) per site: 28-day
   clicks, impressions, CTR and position; top queries and pages;
   striking-distance queries (position 8-20, 20+ impressions, CTR under 3%);
   pages losing clicks week over week; sitemaps; URL inspection of the home
   page and top pages. Windows end 3 days back (Google's lag). Issues (no
   sitemap, sitemap errors, pages not indexed) are DEGRADED.
   `seo.core_web_vitals` (daily, read only): PageSpeed mobile and desktop
   score, LCP, CLS, TBT and the CrUX field figures when Google has them;
   a clear drop against the last reading is a regression (DEGRADED). Both
   carry their read times and show in the Agency tab's SEO panel and, for
   Search Console, the Command Center's services.
   At 07:00 New York `seo_rank` snapshots ranks for every site with a
   domain plus both stores; on Mondays it also audits them, builds each
   client's weekly report (`seo.weekly_report`: rank moves, vitals, fixes
   done, next 3 actions, each figure with its read time) and the Academy
   keyword gaps (`seo.keyword_gaps`: up to 8 searches a week, where the
   store ranks and which keywords have YouTube results our channel titles
   do not cover, with a title idea). `seo.draft_report_email` saves a report
   as a client email draft (`content_drafts` kind `seo_report_email`) for
   the sites and sales agents to attach; nothing sends it, sending stays
   with the approved agency send cards. While `ATLAS_WATCH_ONLY` is on these
   Tier 1 actions store nothing; the panel still shows the check readings.
   Keywords come from `seo.keyword_research` (volume "not measured"). Blog
   posts (2 per client a month) and on-page changes (title, meta
   description, h1, alt only) are cards behind the QA gate; they edit our
   build copy, and shipping is still `qa.check_site` then
   `agency.deploy_site`. `SEO_PAID_CHECKS=on` adds the paid Perplexity AI
   visibility card (one call per keyword, up to 3); leave it off.
14. **Research** (the `research` agent, Knowledge department). Works with no
   key. Seed the Academy competitors once (the file is read, never written):
   `python -m atlas.cli research seed --file .claude/competitors-2026-10-10.json`.
   Add Mapfront or client competitors by asking for `research.add_competitor`
   (its homepage becomes a watch). At 06:00 New York `research_scan` reads
   every watched page through the scraper (robots.txt, deny list,
   `SCRAPE_DAILY_CAP`); a date-only edit is not a change, and three failed
   reads in a row make one remediation task. Sunday 18:00 `research_weekly`
   refreshes up to 10 profiles and files the weekly report. Upload cadence
   needs `YOUTUBE_API_KEY` (step 3's YouTube key) and the competitor's channel
   id; without them cadence reads "not measured". Client reports and adopted
   opportunities are cards (the report send is behind the QA gate; adopting
   only adds a build task). `RESEARCH_PAID_CHECKS=on` adds the paid deep-dive
   card (1 Perplexity call each); leave it off.

Paid switches stay `off`: `SEO_PAID_CHECKS`, `RESEARCH_PAID_CHECKS`,
`SALES_PAID_VERIFY` (ATLAS_FREE_ONLY). `AGENCY_FIRST_SALE_PRICE` is ignored.

## The order worth doing them in

### 1. A reporting channel — this gates everything else being useful

Nothing else matters until ATLAS can reach you. Every check below can go
CRITICAL and reach nobody today.

| Variable | Where |
|---|---|
| `SLACK_BOT_TOKEN` | api.slack.com → your app → OAuth & Permissions → Bot User OAuth Token (`xoxb-`). Scopes: `chat:write`, `chat:write.public`, and `files:write` for the reel previews: each AI-stories reel card in #instagram-reels gets its MP4 uploaded into the card's thread (`files.getUploadURLExternal` → the bytes → `files.completeUploadExternal`) so the video can be watched in Slack before approving. **The production app lacks `files:write` today** (Slack answers `missing_scope`): until it is added (api.slack.com → your app → OAuth & Permissions → Bot Token Scopes → `files:write`, then reinstall the app), the thread gets the render's public `/media/{token}` link instead, with "preview upload needs the files:write scope; link below" — the card itself never fails. This repo carries no Slack app manifest; the scope is added in the app's settings | **The bot must be a member of every channel it posts to.** `chat:write.public` lets it post into a channel it never joined, but Slack sends no message events from such a channel, so a reply typed there never reaches ATLAS. `comms.slack_channels` reads DEGRADED and names each such channel; the fix is `/invite @Atlas` typed in it (or add the `channels:join` scope and reinstall).
| `SLACK_CHANNEL` | the channel id (right-click the channel → Copy link; it is the `C…` part) |
| `SLACK_INSTAGRAM_REELS_CHANNEL` | optional; the #reels channel (renamed from #instagram-reels on 2026-09-27; same id, so the variable kept its name). Every short-video card lands here: the AI-stories account (@atlasstories3), its `instagram.publish` / `content.publish_reel` cards and its once-a-day digest (overnight line, format research, the day's drafts), topic `instagram_stories`; and TikTok's, topic `tiktok`. The main Instagram account (topic `instagram`) is not a reels account and keeps its own route. On production this is **`C0C3XH0F6S3`** (#reels) — the default in `.env.example`, deliberately not a code default. Unset (and not named in `SLACK_CHANNELS`), `content.reels_channel` reads DEGRADED "SLACK_INSTAGRAM_REELS_CHANNEL unset — cards fall to the default route", so it is never silent. An explicit `instagram_stories:<id>` entry in `SLACK_CHANNELS` wins over it; with neither, these fall back to `SLACK_CHANNEL`. Because this topic has a channel named for it, its messages are delivered there first; the same holds for any topic named in `SLACK_CHANNELS` |
| `ASSISTANT_ENABLED`, `SLACK_ASSISTANT_CHANNEL` | optional; `ASSISTANT_ENABLED` (1/true/yes/on) switches on the read-only "ask ATLAS anything" assistant -- **off by default**, and off everything below behaves as before the module. `SLACK_ASSISTANT_CHANNEL` is its channel (`atlas/modules/assistant`, topic `assistant`); `stop` there stops your runs, and `ask cancel <id>` works anywhere. Questions are answered in two modes: Ask (calculator, units, clock) or Search (the web and the outside APIs), the router choosing unless you say `search <q>` / `ask! <q>`. Every plain message an approver types there is a question for the general-purpose assistant: answered from read-only tools (web search through Perplexity/Brave, one page, Wikipedia, a dictionary, Open-Meteo weather, ECB exchange rates, a calculator, the clock, unit conversion, and ATLAS's own status and catalog), with a Sources list carrying the time each was read. It never runs an action. Unset (and not named in `SLACK_CHANNELS`), that door is closed and the assistant answers only to `ask <question>` on any channel, or `atlas, <question>` when it is plainly a question. Caps: `ASSISTANT_SEARCH_PER_DAY` (default 40), `ASSISTANT_FETCH_PER_DAY` (default 80), `ASSISTANT_MAX_TOOL_CALLS` (6 a question), `ASSISTANT_MAX_TOKENS` (12,000 a question); `assistant.budget` reads them against the audit log. `ASSISTANT_FX_URL` overrides the rate source (default Frankfurter). See `atlas/modules/assistant/ASSISTANT.md` |
| `SLACK_APPROVALS_CHANNEL`, `APPROVALS_BOARD`, `APPROVALS_BOARD_REFRESH_MIN` | optional; the approvals board (`atlas/kernel/board.py`). Every card still lands in its topic channel; #approvals also holds ONE pinned "Waiting for you" message, edited in place (never a new post), listing every pending card by area (ATLAS Academy, LinkedIn, Clips, Agency/Mapfront, School, System): the number linked to the card, what approving does, its risk (money / public / irreversible / reversible) and its expiry, with counts and the New York time it was read. It lists only -- no buttons; approving stays on the card. `SLACK_APPROVALS_CHANNEL` as a channel id uses that channel; a name, or unset, has `approvals_board.create_channel` (Tier 1, archive rollback) create or find `#approvals` once and invite `SLACK_APPROVER_IDS`. Redrawn when the pending cards change and every `APPROVALS_BOARD_REFRESH_MIN` (default 5); `APPROVALS_BOARD=off` turns it off. The 9:00 New York digest of waiting cards (only when one has waited over a day) goes there too. Needs the bot scopes `channels:manage` (create), `channels:read` (find an existing #approvals), `channels:history` (link cards raised before this), `pins:write` |
| `SLACK_SIGNING_SECRET` | same app → Basic Information → Signing Secret. Required for the approve/decline buttons to work |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | console.twilio.com dashboard |
| `TWILIO_FROM`, `TWILIO_TO` | your Twilio number, and your mobile, both `+1…` |
| `TUTORCO_OWNER_WHATSAPP` | optional; your own number in E.164 (`+…`). TUTORCO posts a templated WhatsApp note here for post decisions, urgent client items, portal fixes and prospect replies (`tutorco.notify_owner`). Notify only: a reply approves nothing. Unset or malformed: `tutorco_owner_whatsapp` reads UNKNOWN |
| `TUTORCO_WHATSAPP_FROM` | optional; the Twilio WhatsApp-enabled sender for the above; default `TWILIO_FROM`. `TUTORCO_NOTIFY_PER_HOUR` caps messages per hour (default 6) |
| `TUTORCO_OWNER_WHATSAPP`, `TWILIO_WHATSAPP_URL` (optional) | your mobile `+1…`; the URL Twilio signs, default `https://agency.storefront.test/webhooks/twilio/whatsapp` | WhatsApp sandbox: set the sandbox "When a message comes in" to that URL (POST). Only that number is answered, read-only; replays of a MessageSid are ignored |
| `WHATSAPP_DAILY`, `WHATSAPP_DAILY_HOUR` (optional) | `off` (default) or `on`; hour 0-23 New York time, default `20` | One WhatsApp a day to `TUTORCO_OWNER_WHATSAPP`, written as a short human note: a header with the time ("Last 24 hours, as of 8:00 PM ET"), one line per agent (Mark, Bea, Sage, Sam, Rex, Felix) with a light, then "Reply with an agent's name for details." Lights: red = a check is failing, amber = something failed or is slow, grey = something could not be read (never green), green = all good. Replying with a name (or "details", "why", "what failed") gets a short plain explanation from a fresh read. Sent once per New York date through the Tier 1 action `tutorco.notify_daily` (same hourly cap as other owner messages; split into several messages past 1500 characters). While `ATLAS_WATCH_ONLY` is on nothing is sent (the log says "would send"). If WhatsApp fails one Slack note says so and carries the report |
| `WHATSAPP_CARDS`, `WHATSAPP_CARDS_PER_HOUR` (optional) | `off` (default) or `on`; messages per hour, default `5` | The kill switch for the card catch-up: every pending Tier 2 approval card that never reached WhatsApp gets one short message (what it is, what approving does, the card number, Approve / Decline buttons, "or decide on the portal") through the Tier 1 action `tutorco.notify_card`, once per card. Every action whose tier is Tier 2 in the code registry (read live, so a new Tier 2 action is allowed on its own; Miro, 2026-11-11) gets buttons and a one-time code, and can be approved or declined from WhatsApp. Still portal-only, whatever a message says: Tier 3 / permanent-deny kinds (money out, DNS, deletion, credential revocation, self-modification), Tier 1 and unregistered actions, `atlas.dev_request` and the other self-changing kinds in `wa_approvals.NEVER_FROM_WHATSAPP`, any card whose text is cut in the message (over 220 characters), and any card whose parameters are missing, undeclared or not shown. The first real send pass looks at the newest 40 pending cards, sends the newest 5 and skips the rest silently; nothing is lost or marked while WhatsApp is unconfigured, the cap is 0, or the 24 h window is closed or unreadable. Quiet hours (`QUIET_HOURS`) wait until morning; a closed 24 h WhatsApp window leaves a portal banner (Slack note only with `APPROVALS_SLACK=on`); nothing is sent while `ATLAS_WATCH_ONLY` is on. Needs the same Twilio and `TUTORCO_OWNER_WHATSAPP` settings as the alerts |
| `WHATSAPP_CONTENT_SID_CARD` (optional) | `HX` + 32 hex, empty by default | The Twilio Content API quick-reply template with the Approve / Decline buttons (payload `approve:<card>:<code>` / `decline:<card>:<code>`). Create it once with `python tools/wa_card_template.py` (idempotent: it reuses a template named `atlas_card_buttons_v1`; needs `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`) and paste the printed line. Empty, or if Twilio refuses the template, cards fall back to plain text "Reply YES <code> or NO <code>". A bare yes/no never decides anything: ATLAS lists the waiting cards and says to tap a button or send YES / NO with the code. A late tap on an expired card answers "that card expired, nothing was done" |
| `APPROVALS_SLACK` (optional) | `off` (default) or `on` | Slack is no longer part of approvals. `on` brings back the Slack card with its Approve buttons at proposal time and the optional notices (failed WhatsApp send, lockout, closed window). A failed WhatsApp send is retried once a few minutes later and shows as a banner on the portal approvals page either way. Other Slack uses are unchanged |
| `WHATSAPP_ALERTS`, `WHATSAPP_ALERTS_PER_HOUR` (optional) | `off` (default) or `on`; messages per hour, default `3` | The kill switch for the bad-news ping: when a health check goes from OK to failing (never for UNKNOWN or degraded) Atlas sends one short WhatsApp line to `TUTORCO_OWNER_WHATSAPP` through the Tier 1 action `tutorco.notify_alert`, once per failing reading, at most `WHATSAPP_ALERTS_PER_HOUR` an hour. A new Tier 2 approval card already pings you the same way (`tutorco.notify_card`). Free-form WhatsApp text only works inside 24 hours of your last message to Atlas (the WhatsApp window); in quiet hours (`QUIET_HOURS`), when that window is closed, or when WhatsApp certainly refuses it, the ping goes to one Slack note instead. Nothing is sent while `ATLAS_WATCH_ONLY` is on |
| `WHATSAPP_CLIP_VIDEO`, `WHATSAPP_CLIP_VIDEO_HOST` (optional) | `off` (default) or `on`; the public host Twilio fetches from, default `agency.storefront.test` | Sends the sensor clip itself on WhatsApp instead of "open the portal" (Miro, 2026-11-12). When a person alert's clip is saved, `sensors.notify_clip` (Tier 1) re-encodes it (H.264, 720p, no audio, at most 16 MB; one smaller retry) into a private folder, mints a 256-bit random token and attaches `https://<host>/media/clipv.<token>` to the usual tagged message. The link works for 5 minutes and ends at the first full download; the file is deleted then, on expiry, on a refused send, or on rollback; the token is never logged or stored (only its hash names the file). Only that exact path is forwarded by `Caddyfile.public` (**yours to approve and apply**: reload the `shop-public` service); the portal stays loopback-only. If ffmpeg fails, the clip is still over 16 MB, or Twilio refuses the media, the message says the video could not be attached and the clip is on the portal, and the result reads `video_status: UNKNOWN` -- a video is never claimed. Twilio accepting the message is not delivery: the result says "delivery not confirmed". Off, nothing changes. Nothing is sent while `ATLAS_WATCH_ONLY` is on |
| `OWNER_CALLS`, `CALL_MAX_PER_DAY`, `CALL_ESCALATE_MINUTES`, `OWNER_CALL_FROM` (optional) | `off` (default) or `on`; calls per New York day, default `3`; minutes, default `15`; a voice-enabled caller id, default `TWILIO_FROM` | The kill switch for the owner phone call: Atlas rings `TUTORCO_OWNER_WHATSAPP` (that number only, from the environment, never from chat text) with a short fixed message (TwiML `<Say>` only, built in code, nothing from the event) through the Tier 1 action `tutorco.call_owner`. Two triggers: (1) escalation, when a WhatsApp alert (a check that went OK to failing and is still failing) or a pending Tier 2 card marked urgent (`evidence.urgent` true) was sent `CALL_ESCALATE_MINUTES` ago and you have not written since, it calls once; quiet hours (`QUIET_HOURS`) hold it; (2) you write "call me" in the verified chat: one strict regex, no model, the same executor call, quiet hours do not apply. One call per event, ever; `CALL_MAX_PER_DAY` caps all calls including requested ones; nothing is placed while `ATLAS_WATCH_ONLY` is on; a call that could not be placed leaves one Slack note. Needs `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and a Voice-enabled caller id. **Open question: `TWILIO_FROM` may be the WhatsApp sandbox sender, which cannot place voice calls (a `whatsapp:` sender is refused, not guessed). Confirm that `TWILIO_FROM` is a Voice-capable number, or set `OWNER_CALL_FROM` to one.** The older CRITICAL phone route in `comms` (to `TWILIO_TO`) is separate and unchanged |
| `DEV_REQUESTS_PER_DAY` (optional) | default `5` | A "fix X", "change Y" or "make him do Z" in the verified chat raises one pending Tier 2 `atlas.dev_request` card (deduped per text hash, at most this many a New York day, text up to 500 characters, never with a secret). Approving it in Slack only stores a task note for a developer session to pick up through git review; Atlas applies nothing and never edits his own code, config or prompts |
| WhatsApp chat (no setting) | | The reply path talks like a person: general questions come from the model's own knowledge with no Atlas data; questions about Atlas, the Academy (@atlasacademyphp), agency.storefront.test and the six agents add the curated fact sheet `atlas/kernel/about_atlas.md` (no numbers, no secrets); live figures come only from a fresh read stamped with its read time, else "I can't read that right now". The last six exchanges are kept for 24 hours in the Postgres table `whatsapp_chat_memory` (plain text, trimmed; Miro's words are always wrapped as untrusted; a reply built from a live read is kept only as a marker, never its figures). It still cannot act: it says it will send an approval card instead |

Twilio is the only channel that rings a phone, and CRITICAL is the only
severity that breaks quiet hours. Without `ATLAS_HOST` the voice channel
reports unavailable on purpose — it builds its callback URL from it.

**The phone line, both ways, is Twilio's own** (`atlas/api/phone.py`). No
agent platform sits in between:

- In the Twilio console, the number's **A call comes in** webhook is
  `https://$ATLAS_HOST/webhooks/twilio/voice/inbound` (HTTP POST). Optional:
  **Call status changes** to `https://$ATLAS_HOST/webhooks/twilio/voice/status`
  (POST), so a call you placed is noted too; a call ATLAS places sets its own.
- ATLAS speaks through **Fish Audio**: `FISH_AUDIO_API_KEY`, and
  `FISH_AUDIO_VOICE_ID`, the narration voice you chose in Fish Audio (its
  reference id; unset uses Fish Audio's default voice). `FISH_AUDIO_MODEL`
  defaults to `s1`. Each line is rendered per turn, cached under
  `data/voice/` by the hash of a short-lived token, and played from
  `/webhooks/twilio/audio/{token}` — at most three fetches, within ten
  minutes; the hourly retention run sweeps anything left. Without the key,
  or when Fish Audio fails or takes more than four seconds, Twilio's `<Say>`
  speaks the
  same words: a CRITICAL call never goes silent.
- On the dashboard, in Air Glass and in ATLAS Menu, Fish Audio is ATLAS's only
  voice. The fixed lines ("Listening", "I couldn't reach my voice", the
  free-models-out line) are rendered once in the `FISH_AUDIO_VOICE_ID` voice and
  kept in `ATLAS_VOICE_CACHE_DIR`, which defaults to `data/voice/cache`. That
  directory is not swept, and it only ever holds those lines, never an answer.
  When Fish can't speak, a kept line still plays. Anything else stays text.
- Fish Audio's **API credit** is separate from its platform credit. When it
  runs out, `/v1/tts` answers 402: calls fall back to Twilio's voice and one
  INFO signal a day says "Fish Audio API credit is exhausted". The
  `voice.fish_credit` check reads the balance every six hours and goes
  DEGRADED below `FISH_AUDIO_LOW_CREDIT` (default `5.00`); it reads UNKNOWN,
  never a guess, when the key is unset or the wallet cannot be read.
- The Caddyfile does not log `/webhooks/twilio/audio/*` (`log_skip`), and
  the API's own access log redacts the token: a log line is never a way to
  a clip. **Yours to approve** with the Caddyfile, like `/media/*` below.
- A call ATLAS places uses Twilio's answering-machine detection
  (`MachineDetection=DetectMessageEnd`): a machine is left one line, "ATLAS
  called about … it is waiting in Slack", and nothing on that call decides.
  On a call ATLAS places, **1 approves and 2 declines — the keypad alone,
  for the whole call**; speech there is for questions and requests, and no
  spoken word decides (a voicemail greeting can say anything).
- On a call you place, "do it" (or 1) answers only a card read back on that
  call, and only when the carrier signed it with STIR/SHAKEN A-attestation.
- ElevenLabs is gone entirely (account cancelled 2026-09-25). Fish Audio
  speaks; Groq Whisper (`GROQ_API_KEY`, first) or OpenAI Whisper
  (`OPENAI_API_KEY`) hears the dashboard microphone. Every
  `ELEVENLABS_*` variable is retired and should come out of `.env`.

Then: `python -m atlas.cli test-channels`

### 2. A reasoning provider

`ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`, or `OPENAI_API_KEY`.

Ollama is already running with `llama3.2:3b`, which covers `Need.PRIVATE`
(personal mail, browser history) and nothing else. Without one of the above
the reasoning path has no provider at all.

`ATLAS_PRIVATE_ALLOW_CLOUD` — **default off, and leave it off.** PRIVATE work
(personal mail, coursework, browser history, the inbox and school agents)
runs on the local model and nowhere else: when Ollama is down or missing,
`think(Need.PRIVATE, …)` refuses and the caller says the local model is
down, rather than sending your mail to a cloud API. Setting this to `1`
is your explicit opt-in to the opposite: PRIVATE work then falls through to
the Anthropic model when the local one fails (only with `ANTHROPIC_API_KEY`
set), the log says so once per process at WARNING, and `self_status` shows
the flag. Unset, empty or anything but `1`/`true`/`yes`/`on` means never.

Optional extra providers, each a further independent vendor and each wired
in only when its own key is present — `python -m atlas.cli` and
`self_status`'s `providers_present` list which of them are, by name, never
by key value:

| Variable | Joins |
|---|---|
| `GROQ_API_KEY`, `GEMINI_API_KEY` | the free/fast bench ahead of the paid model for TRIVIAL, DRAFT, REASON and HARD work |
| `CEREBRAS_API_KEY` | the same bench, and it leads it: first in TRIVIAL (behind only the local model) and DRAFT, ahead of Groq and Gemini — fastest inference of the free providers, which is what those two Needs value most. Still reaches REASON/HARD for depth, just not ahead of Gemini/Groq there. `ATLAS_MODEL_CEREBRAS` default `gpt-oss-120b` |
| `MISTRAL_API_KEY`, `COHERE_API_KEY` | the same bench, behind Groq and Gemini, in TRIVIAL/DRAFT/REASON/HARD; also join CRITICAL as further independent-vendor depth. `ATLAS_MODEL_MISTRAL` default `ministral-14b-2512` (`ATLAS_MODEL_MISTRAL_FAST` `ministral-3b-2512` in TRIVIAL); `ATLAS_MODEL_COHERE`/`ATLAS_MODEL_COHERE_FAST` defaults `command-a-03-2025`/`command-r-08-2024` |
| all three | a key being present is not the same as the provider working — a free tier that was never activated on the account answers 402 or 429 looking exactly like a live one — so `verify_cloud()` proves each one once at boot and drops it from every chain on 401/402/403/404 (kept on 429: that is rationing, which resets on its own, not misconfiguration) |
| any of the three, pinned to an agent | `codestral` (Mistral's coding model) or any other model that provider serves, by pinning `ATLAS_AGENT_MODEL_<NAME>=mistral:codestral` (see 2b) rather than through a Need |
| `HF_TOKEN` | Hugging Face's router, last in every free chain before OpenRouter. `ATLAS_MODEL_HF` default `openai/gpt-oss-120b:cheapest` |
| `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | Workers AI: its 8B (`ATLAS_MODEL_CLOUDFLARE_FAST`) second in the voice/TRIVIAL lane, its 70B (`ATLAS_MODEL_CLOUDFLARE`) in DRAFT/REASON/HARD |

### Free model keys

With `ATLAS_FREE_ONLY=true` every cloud chain is the free bench, and each free
account has a daily cap. On 2026-10-06 every one of them was spent within three
hours and ATLAS had nothing to answer Miro's voice with. Two things now help:
more free accounts, each a separate daily allowance, and a reserve. When a
model's remaining allowance for the day (as the provider reports it) drops
under `ATLAS_FREE_RESERVE_TOKENS` (default 40,000 tokens) or
`ATLAS_FREE_RESERVE_FRACTION` (default 0.2 of the day), only Miro's own turns
may use it: voice, Slack messages he types, and the assistant. Background work
skips it and waits for the reset (`atlas/kernel/quota.py`).

The free accounts, what each gives, and where each stands. None needs a card.
A value only ever goes in with `./bootstrap/set-secret.sh NAME`, one name at a
time. **Never paste a key into a chat.**

| Account | Env | Free allowance (read 2026-10-06) | On prod, 2026-10-06 |
|---|---|---|---|
| **Groq** | `GROQ_API_KEY` | 1,000 requests and 200,000 tokens a day for each of gpt-oss-120b, gpt-oss-20b and qwen3.8-27b. Groq retired `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` (404 on 2026-10-07); set `ATLAS_MODEL_GROQ_SMALL` / `ATLAS_MODEL_GROQ_BIG` only to name a replacement. At boot ATLAS also adds any other chat model Groq lists | set, working |
| **Google AI Studio**: a key from a Google Cloud project with **no billing account linked** (a project with billing is paid usage) | `GEMINI_API_KEY`; models `ATLAS_MODEL_GEMINI_FAST` (default `gemini-flash-lite-latest`), `ATLAS_MODEL_GEMINI_DEEP` (default `gemini-flash-latest`) | per model, shown on AI Studio's rate-limit page; daily quotas reset at midnight Pacific. `gemini-pro-latest` has no free quota | new free key set: flash-lite answers; flash sometimes answers 503 "high demand", which is a transient failure, so the call falls through to the next model and the model stays in the lane |
| **Cloudflare**: Workers AI → Use REST API → "Create a Workers AI API Token" (or a custom token with *Workers AI – Read* and *Workers AI – Edit*). The account ID is on the same page | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | 10,000 neurons a day, reset 00:00 UTC. The 8B fast model costs about 4,119 neurons per million input tokens and 34,868 per million output tokens; the 70B 26,668 and 204,805 | set; the 70B answered |
| **Mistral**: La Plateforme → Admin Console → Subscription → Compare plans → **Experiment** (free; one verified phone number per organization) | `MISTRAL_API_KEY`; `ATLAS_MODEL_MISTRAL_FAST` (default `ministral-3b-2512`, in the voice lane), `ATLAS_MODEL_MISTRAL` (default `ministral-14b-2512`) | tokens per minute, not per day: 1.3M a minute and 12.5 requests a second for ministral-3b, 937.5K and 0.5 for ministral-14b. `mistral-small-latest` is capped at 20K a minute and `mistral-large` isn't on the free tier. Mistral says requests on this plan may be used to train its models | set; ministral-8b and ministral-14b answered |
| **OpenRouter** | `OPENROUTER_API_KEY` | `:free` models share 50 requests a day per key (1,000 once $10 of credit has been bought). ATLAS reads what is left from `/api/v1/key` | set, working |
| **Cohere** | `COHERE_API_KEY` | trial key, capped monthly | set |
| **Hugging Face** (optional): Settings → Access Tokens → *fine-grained*, with **Make calls to Inference Providers** | `HF_TOKEN`; `ATLAS_MODEL_HF` | $0.10 of credit a month on a free account, a few hundred short answers. Last in every chain | not set; the only one left to create, if wanted |

`CEREBRAS_API_KEY` is set but the account has no free quota active (402), so
it is dropped at boot.

After a key is in, the boot log says `provider <name>: ok`, or why it was
dropped. A model that answers 401/402/403/404 at boot is dropped until the next
restart. A 429 or 5xx keeps it. Groq, Workers AI, Gemini and Mistral are
checked one model at a time, so a refused model never takes the provider's
other models with it.

### 2b. Each business agent's own model

Every business agent (attendance, cloudhost, mirotech, fiverr,
agency — everything in `kernel/businesses.py` except school) reasons on
**Hermes 4 405B, served through OpenRouter** (`nousresearch/hermes-4-405b`,
needs `OPENROUTER_API_KEY`) unless told otherwise. It is a good default
specifically because Hermes is trained for tool-calling and holds a system
prompt tightly — which is what an agent scoped to answer for one business
only needs. Tools still go through ATLAS's own executor either way; nothing
about invariant 1 or 2 changes because the model choosing a tool name
changed.

Override one agent with `ATLAS_AGENT_MODEL_<NAME>` (the business name,
upper-cased — `ATLAS_AGENT_MODEL_CLOUDHOST`, `ATLAS_AGENT_MODEL_MIROTECH`, …),
set to `"provider:model"`, e.g. `ATLAS_AGENT_MODEL_MIROTECH=anthropic:claude-sonnet-4-5`
or `ATLAS_AGENT_MODEL_AGENCY=mistral:codestral`. Recognised provider
prefixes: `anthropic`, `openai`, `openrouter`, `groq`, `gemini`, `cerebras`,
`mistral`, `cohere`. If the pin cannot be reached (unset key, provider down),
`think()` falls back to that business's ordinary Need route and says so in
the answer, rather than failing the turn.

There is no dashboard control for this — Phase 16 already rejected a model
dropdown — config only. `school` is the one business this never applies to:
it is `Need.PRIVATE`, so a pin set for it is ignored outright (logged once,
at WARNING) and it keeps running on the local model, same as the inbox
agent.

### 3. The modules that are already loaded and partly blind

| Variable | Where | Scope |
|---|---|---|
| `ATTENDANCE_TOKEN` | Attendance → API tokens (a `k9_…` value) | **read** |
| `NETBIRD_TOKEN` | netbird-host.storefront.test → Settings → Users → your user → Access tokens | read is enough |

### 4. The modules that will not load at all

| Variable(s) | Where |
|---|---|
| `GITLAB_TOKEN` | aicode.storefront.test → Preferences → Access tokens. Scope `api` — `gitlab.open_issue` writes, and `read_api` gets a 403 on every issue it tries to file |
| `GITLAB_WORK_TOKEN` | code.matrix.example.com, same. Optional |
| `MATRIX_TOKEN` | `./bootstrap/matrix-login.sh` — signs in as **your own** `@miro:matrix.example.com` (your decision: ATLAS is private to you, replies go out under your name). Read-only plus drafts: it reads rooms and DMs, uses your read receipts for "not seen by me", and a reply is a Tier 2 draft in Slack that only sends when you approve. Password goes to chat.matrix.example.com once and is never stored. |
| `GOOGLE_*` (id, secret, refresh token) | `./bootstrap/google-login.sh <client_secret.json>` — one OAuth app covers both `mail` and `calendar`. Run it with no arguments for the console steps. |
| `INSTAGRAM_ACCOUNT_ID`, `INSTAGRAM_TOKEN` | Meta app → Instagram Graph |
| `TIKTOK_*`, `EBAY_*` | their developer consoles |

**Rotate the Instagram token if production ever logged it.** Before Task 48
the Graph API token (which rides in the URL as `?access_token=`) could reach
`observations.detail` (from `instagram.*_engagement` / `*_comments_pending`),
the reasoner's prompt through `diagnose`, and `actions_log` (from a raising
`hide_comment` / `reply_comment` / `unhide_comment`). `actions_log` is a hash
chain, so a row there can never be scrubbed. Check both tables once:

```sql
SELECT count(*) FROM actions_log  WHERE payload LIKE '%access_token=%' AND payload NOT LIKE '%access_token=<redacted>%';
SELECT count(*) FROM observations WHERE detail  LIKE '%access_token=%' AND detail  NOT LIKE '%access_token=<redacted>%';
```

Anything but 0 and 0: generate a new long-lived token in the Meta app for that
account (`INSTAGRAM_TOKEN`, or `INSTAGRAM_STORIES_TOKEN`), set it with
`bootstrap/set-secret.sh`, and restart. The old one is compromised for as long
as it is valid; the rows stay, but the value in them stops working.

### 5. Fiverr — not an env var

Fiverr has no API, so ATLAS drives a browser session a human signed in.

```bash
./bootstrap/fiverr-login.sh
```

Run it from anywhere. It opens a real browser window, you log in yourself
(including 2FA — ATLAS never sees the password), and it then validates the
saved session, syncs it to the server, and re-runs the Fiverr checks so you
see it work.

**Not** `python -m atlas.modules.fiverr.login` from a home directory: that
picks up whatever `python` means there, which on this Mac is Python 2.7 and
cannot import the package at all. The script finds the repo and the
virtualenv itself.

Four checks are UNKNOWN until this is done.

### 7. Optional extras (Phase 18)

None of these gate a module's loading — each one only unlocks one more
thing that module can already do without it. `research`, `content` and
`instagram` are already loaded and reporting either way.

| Variable(s) | Where | Unlocks |
|---|---|---|
| `PERPLEXITY_API_KEY` | perplexity.ai → Settings → API | Research, reasoning and understanding everywhere it is good for — the full list, the cap and the rules are in **Perplexity — research and reasoning over MCP** below. ATLAS reaches Perplexity **only through its MCP server** (`PERPLEXITY_MCP_URL`, default `https://api.perplexity.ai/mcp`), this key sent as the MCP call's bearer. There is no fallback: Perplexity's chat-completions endpoint returns `403 chat_completions_not_available` in production and `/v1/responses` rejects model `sonar` (verified live, 2026-09-25, Task 50), so when the MCP server cannot be reached, refuses the token, or no longer offers a tool, that is a plain reason — never a second call elsewhere. It is also `research.report`'s search backend when `BRAVE_API_KEY` is not set (`atlas.modules._web.search` tries Brave first). Perplexity is a **wrapped source, not an oracle**: its answer text is quoted and cited like any other web page, never written into a report as if ATLAS had said it. With neither key set, `research.report` still loads and fails cleanly on every call, saying so |
| `FIRECRAWL_API_KEY` | firecrawl.dev → dashboard → API Keys | A **fallback fetch only**, for pages that draw themselves with JavaScript: `atlas.modules._web.fetch_text` asks Firecrawl's `/v1/scrape` for the page as markdown when the plain fetch succeeded but came back with fewer than 200 characters of text. It never replaces the plain fetch, never crawls a whole site (`agency.audit` reads one page per lead by design), and its text is wrapped like any other web page. A failed Firecrawl call leaves the plain result as it was; the key never appears in a reason. Unset, nothing changes |
| `OPENART_MCP_URL` (default `https://mcp.openart.ai/mcp`), `OPENART_TOKEN` | OpenArt → account → API/MCP access | `content.render_video`: turning an approved `story_script` draft into an actual short video, through the same MCP client shape `agency._mcp.McpClient` uses. The URL already has a working default; without `OPENART_TOKEN` the call reaches OpenArt with none and fails there, saying so — this has not been confirmed against the real service (assumed, not verified) |
| `RENDER_MONTHLY_CAP` (default 200) | not a credential — set it yourself | A spend cap, not a purchase: `content.render_video` refuses with a plain reason once the month's count reaches it, checked before OpenArt is ever called. Raising it does not buy anything, it only allows more of an OpenArt plan's already-paid-for credits to be spent |
| `GEMINI_API_KEY` (already set for the model router), `VEO_MODEL` (default `veo-3.1-fast-generate-preview`), `VEO_MONTHLY_SECONDS` (default 600), `VEO_MAX_BEATS` (default 4), `VEO_RESOLUTION` (default `1080p`; `720p` or `4k` — 4k costs about 3× per second and is downscaled to the platforms' 1080) | Google AI Studio → API keys | `content.render_video`'s first choice: real motion video from Veo, one 8 s 9:16 clip per story beat (at most `VEO_MAX_BEATS`), each headed by the story's character sheet so the people stay the same, joined and captioned. **Costs money per second on the Gemini billing.** `VEO_MONTHLY_SECONDS` is the cap and **the standing approval**: renders run unattended (Tier 1) up to it, the same model as OpenArt under `RENDER_MONTHLY_CAP` — set it to what you are willing to spend each month without being asked. It is counted in `veo_usage` when Veo accepts a clip; a story whose beats × 8 s would cross it is never started on Veo and goes to the next backend. `content.render_backend` shows seconds used / cap with the time it read them |
| `GEMINI_IMAGE_MODEL` (default `gemini-3.1-flash-image`) | not a secret | The free local render's stills (six at 9:16). A 404 refuses the stills with the model name and the reel is made from typographic cards instead, saying so |
| `GROQ_API_KEY` (already set for the model router) | console.groq.com → API Keys | Word-timed captions on a Veo reel (`whisper-large-v3`, word + segment timestamps → ASS, three-word chunks, the spoken word in yellow, burned in with libass). Unset, the reel still carries the burned "Made with AI" tag and the render says the captions were not burned |
| `REELS_FISH_VOICE_ID` | Fish Audio → open the voice you chose (the British man) → the id at the end of its `fish.audio/m/<id>` URL | The reels voice, on every backend. On Veo the chosen voice narrates over the clips (Veo is asked for no speech, its own sound stays under the voice as ambience, the last frame holds if the voice runs longer, captions are timed from the voice); on the free local render it narrates the stills. Unset, Veo's own narrator speaks and the local render uses Fish's default voice — the card says which, no voice id is ever guessed |
| `REELS_FONT_FILE` | not a secret — a path on the box | The font `drawtext` burns "Made with AI" and the typographic cards with. Unset, the first DejaVu Sans Bold found is used (the production image has DejaVu only); the ASS captions name `DejaVu Sans` for the same reason |
| `REELS_PER_DAY` (default 2) | not a secret | How many AI-story reels the daily step drafts, renders and puts on a card in #instagram-reels, once a day after `BRIEF_HOUR` (`kernel.reels.daily`, off the scheduler loop). Each day also spends one `perplexity_research` call from the shared `PERPLEXITY_MONTHLY_CAP` (about 30 a month) and, on Veo, up to `REELS_PER_DAY` × `VEO_MAX_BEATS` × 8 s of `VEO_MONTHLY_SECONDS`. `ATLAS_WATCH_ONLY` (the default) refuses the research, the drafts and the renders alike, so no cards appear until ATLAS is armed; the digest says so |
| `FINNHUB_API_KEY` (optional) | finnhub.io → Dashboard → API key (the free plan covers these three). Put it in with `bootstrap/set-secret.sh FINNHUB_API_KEY` | The trade-idea scan's extra inputs (`atlas/modules/markets/ideas.py`, via `finnhub.py`): earnings dates (`/calendar/earnings` — no single-stock idea within 3 trading days of earnings), the last few headlines (`/company-news`, wrapped as outside data before a model sees them) and analyst recommendation counts (`/stock/recommendation`). GETs only, the key in the `X-Finnhub-Token` header — never in a URL, a log line, an audit row or a prompt. Unset: those inputs are skipped and said so in the weekly — without an earnings date single stocks are skipped and only ETF ideas are raised |
| `PASSPORT_EXPIRY` (YYYY-MM-DD, optional) | not a secret — the expiry date printed in your passport | The travel organiser (`atlas/modules/mail/travel.py`) flags any trip ending less than six months before it, in the trip's vault note and as one signal per trip. Unset or malformed, nothing is flagged and each trip note says the expiry was not given — it is never assumed |
| `REELS_CALENDAR` (default on), `VEO_DAILY_CLIPS` (default 10) | not secrets | The two reels packages Miro supplied (`atlas/modules/content/calendars/`), made **now, not by date** (Miro, 2026-09-27: "don't take the 30-day calendar literally -- do everything now"). Every 30 minutes `kernel.reels.calendar_tick` spends what is left of the day's `VEO_DAILY_CLIPS` (counted from midnight Pacific) on the next unfinished reel -- two stories, then one business lesson, each package in its own order -- smooths each clip to 60 fps, assembles it (Arabic voice, English captions; only characters speak, on sensor, to each other; full 3D motion), and raises its Tier 2 `content.publish_reel` card the moment it is ready. A RETURN sequel's card waits until the episode before it published. While packaged reels remain, the `REELS_PER_DAY` rotation stands aside; a short backup reel runs only from Veo clips left over. `REELS_CALENDAR=off` stops the packages |
| `HIGGSFIELD_KEY_ID`, `HIGGSFIELD_KEY_SECRET`, `HIGGSFIELD_MONTHLY_USD` (default 20), `HIGGSFIELD_USD_PER_SECOND_MAX` (default 0.375), `HIGGSFIELD_RESOLUTION` (default: `VEO_RESOLUTION`) | console.higgsfield.ai → API keys (a key ID and a secret) | The reels' **overflow** generator (Miro, 2026-09-29), for the calendar and the daily reel (`content.render_video`) alike. Veo stays first; once Veo's daily allowance is used up (`VEO_DAILY_CLIPS` from Pacific midnight, the `VEO_RESERVED_CLIPS` reserve included, or Veo's own daily-quota 429) the clips the day still needs are made on Higgsfield — `bytedance/seedance-2.0/text-to-video`, 8 s, 9:16, native audio — through the same IP guard as Veo, normalised to 1080x1920 and marked as Higgsfield's beside the clip. In the calendar, Veo's monthly seconds cap or a plain 429 is not an overflow. In the daily reel, Veo makes the beats its month and day allow and the rest of the story goes to Higgsfield (backend `veo+higgsfield`); a daily-quota 429 mid-story moves the remaining beats there; every overflow clip of a story is reserved before the next clip, or the story fails whole. Without Higgsfield keys neither path changes. **Costs money.** `HIGGSFIELD_MONTHLY_USD` is a hard cap and the standing approval: each clip's price is Higgsfield's own `/estimate` for the exact request; when it names none the price is **not read** and a worst case is booked instead (`HIGGSFIELD_USD_PER_SECOND_MAX` x the seconds, x4 at 4k, never less than 1080p's; an assumption, labelled "price not read" on the row, the clip and the check), reserved in `higgsfield_usage` before the request, and a clip that would cross the cap is never started. Refunded only when Higgsfield cannot have charged (refused on submit, never reached, or ended failed / nsfw / canceled). `HIGGSFIELD_URL` must be https; the finished clip is downloaded without the key, every redirect hop https and public. `content.higgsfield_budget` shows booked / cap with its read time, and is OK only when the key is confirmed (the nil request's status answers the documented 404), the ledger is read and Higgsfield named a price: UNKNOWN when the keys are unset, the key is not confirmed, the API is unreachable or the price was not read; FAILING on a 401 (key rejected) or 403 (no credits) |
| `REELS_EXPORT_4K` (default on) | not a secret | Every Veo reel is motion-interpolated from 24 to 60 fps (one pass, up to an hour of CPU, falling back to native motion on failure). Beside its 1080x1920 delivery file, a 2160x3840 archive master at `data/content/<media id>-2160.mp4`, upscaled from Veo's native resolution and labelled so. The master is never posted: the Instagram Graph API refuses a Reel wider than 1920 px or above 60 fps. `0` turns it off |
| `RECEPTION_ENABLED` (default off) | not a secret | The business line (`atlas/api/reception.py`): a call to `TWILIO_FROM` from any number but `TWILIO_TO` is answered "Hi, this is Mapfront, Miro's AI assistant speaking", says it is an AI whenever asked, explains the Website Package and its published list price, gives the lead's preview link, never agrees another figure, a date or a payment, cannot act, and files the transcript in `reception_calls` and Slack. While on, outreach emails carry "Questions? Call or text Mapfront at `TWILIO_FROM`" and replies "For more information, call or text Mapfront at ...". Off: strangers hear "This is a private line" |
| `API_FOOTBALL_KEY` | [dashboard.api-football.com](https://dashboard.api-football.com/register) → sign up (the free plan is enough: 100 requests a day) → the key on the dashboard's Account page. Use the API-SPORTS dashboard key, not a RapidAPI one: the two are separate accounts with separate hosts. Put it in with `bootstrap/set-secret.sh API_FOOTBALL_KEY` | The football (soccer) stats reels (Miro, 2026-09-29; `atlas/modules/football`, `content.football`). ATLAS only reads `https://v3.football.api-sports.io` with the key in the `x-apisports-key` header: `players/topscorers`, `players/topassists`, `players` (a player's season statistics), `players/profiles` (to check a configured player id against its name) and `fixtures` (a club's last results). Every read is kept with its read time in `football_api_reads` and reused while fresh; every request is counted in `football_api_quota` before it is sent. The `api_football` check reads `/status` (free of quota) every 6 h: requests used against the plan's limit, with the read time. Unset, the lane is idle and the check says UNKNOWN "API_FOOTBALL_KEY not set"; a refused key is FAILING with the API's reason, never the key |
| `API_FOOTBALL_DAILY_LIMIT` (default 100), `FOOTBALL_REELS_PER_DAY` (default 1), `FOOTBALL_SEASON` (default: the current season) | not secrets | The lane's own request ceiling (the free plan's 100); how many football reels a day (0 is off) -- drafted after the day's story reels, only from Veo clips left over, posted on the AI reels path with the same automatic checks; and the season to read, as its start year (API-Football's naming). When the plan refuses a season, the API's own message names the ones it allows and the newest is read instead. No match footage, no AI likeness of a real player, no crest: every shot is from a closed catalog of stylised stadium, ball and silhouette scenes; names and numbers are captions, each number bound to a fetched fact and re-verified before a clip is made |
| `COURSE_REELS_PER_WEEK` (default 2) | not a secret | The course-promo lane (Miro, 2026-09-29; `content.course`): reels for his course "Build a Safe AI Operator" ($49) on the AI-stories accounts only -- the stories Instagram, the Atlas Stories Facebook Page and TikTok @atlasstories7 through Postiz; never the clips accounts. Due on fixed New York weekdays (Tuesday and Friday for 2); a slot missed because `REELS_AUTOPUBLISH_PER_DAY` is already spent, or no Veo clip is left, moves to the next day of the same week. The step runs before the football step, so on a course day it takes the day's third automatic post. One lesson per reel, rotating through `calendars/course-promo.json`; the script is composed in code, never by a model. No income claims (refused in code), no screens in any video prompt (refused at both clip doors), "my course" said and shown, and the caption carries "link in bio" and the product URL. The profile's bio link cannot be set by ATLAS: neither the Instagram Graph API (the IG User node has no update operation), TikTok's APIs nor Postiz edit a profile -- set it by hand |
| `COURSE_POSTS_PER_WEEK` (default 7) | not a secret | The course's organic posts (Miro, 2026-09-29; `atlas/modules/course`), for every product in the code-defined catalog (`catalog.py`: Build a Safe AI Operator, Everyday AI for Adults, Safe and Simple AI 60+, the AI for Every Age bundle and the free Family Scam-Safety Kit, the lead magnet; nothing else is ever marketed). Rotation gives each product its fair share of the cap (a product is proposed only while its count this week is the catalog's lowest); each has its own channels and languages (only the Operator is posted in Spanish, Portuguese or Arabic), its own `price_<product>` check (price and availability read live from `<product>.json` and `.js`; unreadable is UNKNOWN and nothing is drafted), and one Shopify blog draft (title <= 60, meta <= 155, 700-1,200 words of its own lesson excerpts, word for word). The 60+ guide and the kit also refuse fear words and any number not copied from their lesson files; every product refuses grades, accuracy and accreditation claims and "Claude for kids", and carries "Sold to adults 18+." when its live page does. Nora chooses from `course_catalog`, which carries `playbook.md` as quoted data. The marketing agent proposes one post at a time as a measured experiment (`course.propose`, Tier 1: a `marketing_experiments` row and a drafted `course_posts` row), and every post reaches Miro on its own Tier 2 card -- `course.publish_post` for a Postiz text channel (X, Bluesky, Threads, Mastodon, LinkedIn, Facebook, Telegram; never a clips account, never TikTok/Instagram/YouTube), `course.hand_off` for his LinkedIn profile and the Shopify blog (ATLAS has no Shopify Admin API credential, so the article comes to him whole to paste as a hidden draft). At most this many posts a New York week across every channel and language (a Spanish, Portuguese and Arabic variant are three posts), and one per channel a day. The price is read live from `<product>.json` with its read time on every draft and again on approval (a changed price refuses the card); English is composed in code from the lesson hooks, translations are the model's and the card says so. No income claims, reviews or urgency, in any of the four languages (refused in code). Links carry `utm_source=<platform>&utm_medium=organic&utm_campaign=<experiment id>`; after seven days the experiment is read from PostHog (`POSTHOG_PERSONAL_KEY`) as store pageviews for that campaign, or UNKNOWN -- sales are always UNKNOWN to ATLAS (no Shopify order read path); Shopify's own UTM report shows them. Cards land in the `course` Slack topic (map it in `SLACK_CHANNELS`) |
| `REELS_NUDGE_HOUR` (default 17) | not a secret — an hour, local time | The cadence goal is at least one post a day on the stories account and TikTok, and `REELS_PER_DAY` is 2 so there is always a spare. At this hour, if no `content.publish_reel` card was approved today, one message goes to #instagram-reels ("2 reels waiting — approve one to keep today's post"), once a day. Nothing is ever posted on silence: an unanswered card expires |
| `FACEBOOK_STORIES_PAGE_ID`, `FACEBOOK_STORIES_PAGE_TOKEN` | Meta app → the "Atlas Stories" Facebook Page → a non-expiring Page access token with `pages_manage_posts` (set on production, verified) | The Facebook leg of `content.publish_reel` (`POST /{page-id}/videos`, the render's public link as `file_url`). Unset, that leg reads "not configured" on the card and `content.reel_legs` is UNKNOWN for it |
| `POSTIZ_API_KEY`, `POSTIZ_API_URL` (default `https://api.postiz.com/public/v1`) | Postiz → Settings → Public API | The TikTok leg of `content.publish_reel`, through Postiz with TikTok connected there (Miro's choice; ATLAS's own `TIKTOK_*` app is not used for it). The render is uploaded to Postiz, the TikTok integration's settings are read and honoured first, and the post goes out public by `DIRECT_POST` (never `UPLOAD`, which only drops it in the TikTok inbox). TikTok often returns no id: the card then says "posted, id pending". The rollback deletes the Postiz post only while it is still queued; a published one gets a MANUAL note (deleting it is Tier 3). Unset, the leg reads "not configured" and `content.reel_legs` is UNKNOWN |
| `POSTIZ_TIKTOK_CLIPS_INTEGRATION` | Postiz → connect the clips TikTok **@atlasclips12** as a second TikTok channel → its Postiz integration id, or simply its name `atlasclips12` | The clips lane's TikTok leg (`clips.publish_clip_tiktok`, Tier 2, one card per clip), through the same Postiz key. Only the integration this names is ever used for clips, and only for a clip the lane cut (a plain `.mp4` in `data/clips/<campaign>/out/`); settings: public, DIRECT_POST, not AI-made, no library music, TikTok's branded-content toggle ON (a paid campaign). The reels lane never picks this integration (nor one named `atlasclips12`) and, with several TikTok integrations left, takes the one named `atlasstories7` or refuses. Unset (or `POSTIZ_API_KEY` unset), each TikTok clip is a hand-off card (file and exact caption) and `clips.legs` is UNKNOWN |
| `POSTIZ_YOUTUBE_CLIPS_INTEGRATION` | Postiz → connect the clips YouTube channel **AtlasClips (@miroatlas)** → its Postiz integration id (or its name, `AtlasClips`) | The clips lane's YouTube Shorts leg through Postiz (`clips.publish_clip_youtube_postiz`, Tier 2, one card per clip), through the same Postiz key. Only for a campaign whose rules pay on YouTube (`youtube` in its `platforms`: ATLUS and Irubyana, not COD), and only for a Short: 9:16 and at most 60 s (the cut window, then ffprobe before posting); a longer window is never carded for YouTube and the note says why. Only the integration this names is used, it must be a YouTube one (identifier `youtube`) and never either TikTok account, and only for a clip the lane cut. Title: the approved text whole + `#Ad` + the campaign's YouTube handle (refused past 100 characters, never cut); description: the full caption; tags: the campaign hashtags; `type: public`, `selfDeclaredMadeForKids: no` (Postiz declares no paid-promotion flag for YouTube, so the disclosure is `#Ad` in title and description). Recorded at `post_account` `youtube:clips` (the same channel as the Data API's `clips`), so the one-platform-per-clip rule and `max_accounts` hold across both routes. With it set, it is used before the Data API (`YOUTUBE_CLIPS_*`), except for a Short that route already uploaded. Unset (or `POSTIZ_API_KEY` unset), `clips.legs` is UNKNOWN saying "set POSTIZ_YOUTUBE_CLIPS_INTEGRATION" and, with the Data API unset too, each YouTube clip is a hand-off card |
| `POSTIZ_YOUTUBE_INTEGRATION` | Postiz → the connected YouTube channel → its integration id (`cmun6w6hm0fcfno0yqjqptqmo`, the channel "AtlasClips", @miroatlas, seen 2026-09-29), or its name | YouTube Shorts for the AI reels (Miro, 2026-09-29; `content.shorts`), through Postiz only -- ATLAS holds no Google token for it. Only the YouTube integration this names is used (never guessed, never a TikTok one). The clips lane shares that channel under its own `POSTIZ_YOUTUBE_CLIPS_INTEGRATION`, which the Shorts step never reads; a Short is only ever a `content_media` render whose file (symlinks resolved) is outside the clips data dir. Unset, the Shorts step says "not configured" and the `shorts_lane` check is UNKNOWN |
| `SHORTS_PER_WEEK` (default 3) | not a secret | How many YouTube Shorts a New York week (Monday, Wednesday and Friday for 3; `0` turns them off). Each is `content.publish_short` (Tier 1): the best AI-stories-lane reel (stories, series, football, course -- never a campaign clip) that posted in the last 7 days and is not on YouTube yet, ranked by read views (newest when none is read, said so), only if its render is 1080x1920 with sound and at most 60 s, its caption passes the reels' guards, and the part before it (a series) is already on YouTube. Title: the reel's own title or hook (<= 100); description: its caption plus an AI-disclosure line (Postiz has no AI-content field for YouTube; toggle "altered or synthetic content" in YouTube Studio by hand if wanted); public, not made for kids. A published Short is never deleted by ATLAS (Tier 3): its rollback is a MANUAL note |
| `INSTAGRAM_CLIPS_ACCOUNT_ID`, `INSTAGRAM_CLIPS_TOKEN`; `YOUTUBE_CLIPS_CLIENT_ID`, `YOUTUBE_CLIPS_CLIENT_SECRET`, `YOUTUBE_CLIPS_REFRESH_TOKEN` | Meta app (Instagram @atlas.clipsdrop); Google Cloud OAuth client for the clips YouTube channel | The clips lane's other two accounts. Each unset leg turns its clips into hand-off cards; `clips.legs` reads UNKNOWN until all three (with TikTok) are set |
| `ACADEMY_TUTORIALS_SLACK_CHANNEL` (default `C0C89V8RNQJ`, #atlas-academy-videos in ATLAS's own workspace), `ACADEMY_TUTORIALS_AUTOPUBLISH` (default on since 2026-10-15), `ACADEMY_TUTORIALS_HOUR_UTC` (default 17), `ACADEMY_TUTORIALS_PER_SERIES_DAILY` (default 1), `ACADEMY_TUTORIALS_VOICE_SPEED` (default 0.9, Fish prosody speed 0.5-2.0, tutorials only), `ACADEMY_TUTORIALS_PER_DAY` (default 7, one per series, the automatic-post cap only), `ACADEMY_TUTORIALS` (default on) | not secrets | The ATLAS Academy YouTube tutorials (`academy/tutorials.py`, Miro 2026-10-10). Once a day from `ACADEMY_TUTORIALS_HOUR_UTC`, for every series, its next episode in its own level ladder (strict order, Miro 2026-10-11: one video per series per day, `ACADEMY_TUTORIALS_PER_SERIES_DAILY`) is rendered, one after another, and each becomes a Tier 2 `academy.publish_tutorial` draft card in the channel this names, with a message under it carrying the thumbnail, the title, the description, the tags, the length, "Intro ✓ / Ending ✓" and a link to watch the whole MP4 (`/media/tut.<token>`, 7 days; the app has no `files:write`, so nothing is uploaded to Slack). Approving publishes at once: upload private, thumbnail, playlist, public. A series whose next episode has no script is skipped for the day and the channel is told "script not written for <series> #<n>" once a day. The narration is slowed with `ACADEMY_TUTORIALS_VOICE_SPEED`; changing it re-renders the cached intro/ending and any unpublished episode, never a public one. If Slack answers `not_in_channel`, the result says so with "/invite @Atlas". Since 2026-10-15 (Miro) each episode instead posts itself through the Tier 1 `academy.autopublish_tutorial` once every gate passes, and a failed gate is that card; `ACADEMY_TUTORIALS_AUTOPUBLISH=off` brings back the draft card for every episode; the older `ACADEMY_TUTORIALS_AUTOPOST` can only switch it off |
| `CLIPS_DAILY_CAP` (default 6), `CLIPS_INTAKE_MIN` (default 30), `CLIPS_INTAKE` (default on), `CLIPS_AUTOPUBLISH` (default on), `CLIPS_DATA_DIR` (default `data/clips`) | not secrets | The clips intake (`kernel.clip_intake`): every `CLIPS_INTAKE_MIN` minutes, for each active campaign with a permission source, new official files in `data/clips/<campaign>/source/` (on the server `./data/app/clips/<campaign>/source/`) are registered by sha256 and the oldest uncut source is cut (one cut per tick; Tier 1, through the executor), at most `CLIPS_DAILY_CAP` clips per campaign per UTC day. A campaign whose rules require its brand on every clip (Irubyana: logo and Twitch banner) is cut only with `./data/app/clips/<campaign>/brand/logo.png` and `banner.png` in place -- files Miro puts there, never fetched -- composited under the captions; while one is missing nothing is cut and a Tier 2 `clips.cut` card names the file and the folder. Then (Miro, 2026-09-30: clips post themselves, the reels lane's way) one waiting clip per platform is posted through the executor -- `clips.publish_clip`, `clips.publish_clip_tiktok`, `clips.publish_clip_youtube_postiz`, each Tier 1 and gated in code: the campaign active with its permission named and reviewed rules, the caption in the campaign's layout with its `#Ad` line, the file a 1080x1920 lane cut with sound inside the campaign's length band (ffprobe, read before every post), the account configured and within the campaign's account and one-platform rules, at most `CLIPS_DAILY_CAP` posts per platform per UTC day. `CLIPS_AUTOPUBLISH=off` holds every clip; a held clip, or one that fails a check, is never skipped in silence -- it is the ordinary Tier 2 post card with the reason first, and approving it answers the hold and runs every check again. Every automatic post is the executor's audit line plus one receipt in the clips channel (platform, clip id, the caption's first line). The hand-off (unconfigured account) and Whop cards follow on their own. Both live campaigns forbid the same video on several accounts, so each clip is cut for ONE platform, the highest-paying (ties taken in turn). `CLIPS_INTAKE=off` stops the intake; under `ATLAS_WATCH_ONLY` it executes nothing and `clips.status` says what waits |
| `CLIPS_LINKS_MIN` (default 60), `CLIPS_LINKS` (default on) | not secrets | The clip link pass (`modules.clips.links`): every `CLIPS_LINKS_MIN` minutes, a clip posted through Postiz (TikTok, or a Short) in the last 14 days with no post URL is looked up in Postiz's post listing (read only). Its URL is written, with the platform as its source so the Whop agent is offered it, only when Postiz reports the platform's own post URL: for TikTok `tiktok.com/@atlasclips12/video/<n>`, for YouTube the id's `shorts/` link. Never a URL built from an id. Postiz unreachable: logged, rows untouched. Seen live 2026-09-30: Postiz lists TikTok posts with the profile URL only, so TikTok clips still need `clips.record_link`. `CLIPS_LINKS=off` stops it |
| `INSTAGRAM_STORIES_ACCOUNT_ID`, `INSTAGRAM_STORIES_TOKEN` | Meta app → Instagram Graph, same as `INSTAGRAM_ACCOUNT_ID`/`INSTAGRAM_TOKEN` | The separate AI-stories account (`instagram.ACCOUNTS["stories"]`), already wired in this tree. It posts only AI-labelled short stories under `RENDER_MONTHLY_CAP`, always through the Tier 2 `instagram.publish` gate — never auto-posted, same as the main account |
| `NIGHTSHIFT_REPOS` | not a secret — comma-separated `host/owner/name` | The only repositories `nightshift.run` may clone and push to (with `NIGHTSHIFT_GIT_TOKEN`). Anything else is refused before the token is used; unset refuses every run. See `bootstrap/nightshift/README.md` |
| `PRESENCE_PEERS` | not a secret — a comma-separated list of your own NetBird peer names (the same ones `netbird` already reports on). List only a device that actually travels with you — a MacBook left on all day at home would read "home" whether or not you are | "Is Miro home" (`kernel/rhythm.presence()`): true only when a listed peer is connected AND its `connection_ip` (the address it is connecting from) falls inside `HOME_IPS` below — being on the mesh from a cafe or on mobile data is not being home. `None` — never guessed — when the mesh is unreachable, a listed peer is unrecognised, or `HOME_IPS` is unset. It drives brevity (texts trimmed when you are out, CRITICAL never), the short one-decision call when you are out, and the re-ring that becomes a text once you are home. With `PRESENCE_PEERS` set and `HOME_IPS` unset, `self_status` still lists each listed peer's current address — read one off there to fill in `HOME_IPS` |
| `HOME_IPS` | not a secret — a comma-separated list of your own public IPs and/or CIDRs (e.g. `203.0.113.7` or `203.0.113.0/24`; both a bare IP and a CIDR parse). Get it from the `self_status` "Peer IPs for HOME_IPS" line (ask "how are you"), or from your router's WAN address page. If the address there is IPv6, list a CIDR (your ISP's prefix, not the bare address) — a phone commonly gets a fresh IPv6 address from the same prefix on every reconnect, so a bare address would drift out of `HOME_IPS` on its own. One entry that is not an IP or a CIDR refuses the whole list (presence reads `None` and names the bad entry) rather than silently dropping just that entry. Never learned from history — a residential IP can be reassigned by your ISP, and guessing the new one from what a peer usually connects from is exactly the guess invariant 6 forbids | Which addresses count as "home" for `PRESENCE_PEERS` above. Unset, presence is always `None` ("HOME_IPS is not set"). If your ISP renumbers you, every listed peer reads as connected-from-outside until you update this — `rhythm`'s `home_ip_change` check notices a listed peer sitting on one new address for 12 hours straight and raises one INFO notice (at most once a week per address) suggesting you update it; it never rewrites `HOME_IPS` itself |
| `RENTCAST_API_KEY` (optional) | [app.rentcast.io](https://app.rentcast.io) → sign up (the free Developer plan is about 50 API calls a month) → API dashboard → create an API key. Put it in with `bootstrap/set-secret.sh RENTCAST_API_KEY` | The real estate deal checker's estimates (Miro, 2026-09-29; `atlas/modules/realestate`, `/atlas deal <address> \| <list price> \| <notes>`): RentCast's value estimate with its comparables (`GET https://api.rentcast.io/v1/avm/value`) as the ARV, and its long-term rent estimate (`/v1/avm/rent/long-term`), with the key only in the `X-Api-Key` header — never in a URL, a log line, an audit row, a card or a prompt. Every read is kept in `realestate_rentcast_reads` and reused for `REALESTATE_CACHE_DAYS` with its original read time; every call is counted in `realestate_rentcast_quota` before it is sent and shown on the card. Unset: the ARV reads "unknown — add RENTCAST_API_KEY or enter an ARV" (never 0), no request is made, and the `realestate.rentcast` check is UNKNOWN. Zillow is never read (no public API; its terms forbid scraping) |
| `RENTCAST_MONTHLY_LIMIT` (default 50), `REALESTATE_CACHE_DAYS` (default 30), `REALESTATE_ARV_FACTOR` (default 0.70), `REALESTATE_ASSIGNMENT_FEE` (default 10000), `REALESTATE_REPAIR_BANDS` (default `turnkey:0,light:15,medium:35,heavy:60`, $/sqft) | not secrets | The deal checker's call ceiling (the free plan's ~50; RentCast reports no remaining count, so ATLAS counts, over-counting a failed call rather than under-counting), how long a read is reused, and the stated assumptions on every card: MAO = factor × ARV − repairs − assignment fee, and the $/sqft for each repair level. The dashboard form is `/api/realestate/form` once the router is mounted (see the module's `api.py`) |
| `FUTURES_SYMBOL` (optional, default `MNQ=F`; `NQ=F` is the other value) | not a secret | The paper futures engine (`atlas/modules/futures`, Miro 2026-09-29): which contract's bars it reads from Yahoo's public chart endpoint (no key; unofficial, holes happen and are counted) and books on paper. Nothing here can trade — no broker, no order path; money out is Tier 3. Contract specs (tick, point value, slippage, commission) are in the module's `contracts.json`, the illustrative prop-firm rules in `propfirm.json`. The module runs its own 15-minute clock inside CME hours; pause it like any module (`/atlas pause futures`), and the −$2,000 paper breaker pauses it by itself |

### 8. The Phase 12–20 modules

Every module below loads with nothing set (`selftest` on a bare box lists
all sixteen as loaded). A check that needs a variable you have not set
reports `Health.UNKNOWN` and names it; `marketing`, `lessons`, `rhythm` and
`browser` need no variable at all. None of these variables arms anything:
`ATLAS_WATCH_ONLY` still decides whether the executor acts at all, and every
send to another person stays a Tier 2 proposal. Endpoint shapes each one
assumes are in [MODULE_AUDIT.md](MODULE_AUDIT.md).

**agency** — local businesses with a bad website (AGENCY.md).

| Variable | Where | Unlocks |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | Google Cloud console → APIs → Places API (New) → Credentials | `agency.prospect`. Spend is capped by `AGENCY_PLACES_MONTHLY_CAP` (requests per month, default 500); at the cap `prospect_budget` reads UNKNOWN and prospecting stops |
| `GOOGLE_PAGESPEED_API_KEY` | same console → PageSpeed Insights API | a real Lighthouse score for the `slow` audit finding; without it a size/time heuristic |
| `GETLAYERS_OAUTH_CLIENT_ID`, `GETLAYERS_OAUTH_REFRESH_TOKEN` (plus `GETLAYERS_OAUTH_TOKEN_URL`, and `GETLAYERS_OAUTH_CLIENT_SECRET` only if registration issued one) | **not pasted by hand.** GetLayers has no API keys — its MCP server speaks OAuth 2.1 (dynamic client registration + PKCE). Run `python3 bootstrap/getlayers-connect.py` on the Mac: it signs in through the browser and writes these four into the server's `.env` through `envlock.py`, then restarts the API. `--dry-run` only prints the discovered endpoints | mockups and site builds over the GetLayers MCP server. The server trades the refresh token for short-lived access tokens, cached in memory; if GetLayers rotates the refresh token, the new one is kept in `data/secrets/getlayers_oauth.json` (0600, override with `GETLAYERS_OAUTH_STORE`) and wins over `.env` until the next reconnect. The `getlayers_auth` check is OK only after a real handshake, UNKNOWN when not connected, FAILING when GetLayers refuses the token — re-run the connect script |
| `GETLAYERS_MCP_URL` | not a secret; default `https://mcp.getlayers.ai/mcp` | which MCP server the above talks to |
| `GETLAYERS_TOKEN` | legacy — **does not work against GetLayers** (there is no API key to put here). Still honoured for a self-hosted MCP that takes a static bearer; OAuth wins when both are set | nothing, against GetLayers: `getlayers_auth` reads FAILING and names the connect script |
| `AGENCY_GETLAYERS_LICENCE_OK=true` | not a credential: **your** confirmation that the GetLayers commercial licence covers client work | without it `agency.mockup` and `agency.build_site` refuse, by name |

GetLayers licence, as its Terms of Use read (checked 2026-09-29): paid plans
carry a commercial licence to use the library in "websites, designs, or other
deliverables for yourself or your clients", with no limit on the number of
clients — so client mockups and sites are covered. MCP access (what ATLAS
uses) needs the **Full Stack Lifetime** plan. The fair-use clause forbids
bulk extraction of the library by automation, which is why `agency.mockup`
and `agency.build_site` stay rate-limited (`max_per_hour=10` each, enforced
by the executor) — do not raise those limits to batch-generate.
| `AGENCY_SMTP_HOST`, `AGENCY_SMTP_PORT`, `AGENCY_SMTP_USER`, `AGENCY_SMTP_PASSWORD`, `AGENCY_SEND_FROM`, `AGENCY_SEND_DOMAIN` | a separate sending domain's SMTP (never `example.com`, which is refused) | `agency.send_outreach` (Tier 2, irreversible). The From address must sit on `AGENCY_SEND_DOMAIN` |
| `AGENCY_POSTAL_ADDRESS` | your business postal address | the CAN-SPAM footer; without it every send refuses. Also the footer of https://agency.storefront.test (Caddy renders it, HTML-escaped; recreate caddy after changing it); unset leaves a TODO comment there, never an invented address |
| `AGENCY_DAILY_CAP` | default 10 | outreach sends per day |
| `AGENCY_DEPLOY_HOST=local` | not a credential: `local` (or unset) publishes on this box for Caddy to serve at `https://agency.storefront.test/preview/<slug>/`; a `user@host` instead rsyncs to `AGENCY_DEPLOY_ROOT` on that host | `agency.deploy_site` (Tier AUTO). Local: one release per deploy under `AGENCY_PUBLISH_DIR`, live by an atomic symlink swap; rollback restores the previous release or unpublishes (`atlas/modules/agency/hosting.py`) |
| `AGENCY_DEPLOY_ROOT` | the directory on a remote `AGENCY_DEPLOY_HOST` | rsync target only; unused when local |
| `AGENCY_PREVIEW_ORIGIN` | not a credential: default `https://agency.storefront.test` (https, scheme and host only) | the origin that serves client previews at `/preview/<slug>/` (no wildcard DNS, no on-demand TLS); used for public URLs and the deploy read-back (`atlas/modules/agency/hosting.py`) |
| `AGENCY_PUBLISH_DIR` | default `data/sites` (= host `./data/app/sites`, mounted read-only into Caddy at `/srv/sites`) | where local deploys publish; refused if it overlaps `AGENCY_SITES_DIR`. The host directory must be owned by uid 10001 |
| `AGENCY_DEMO_PUBLISH_DIR` | default `data/demos` (= host `./data/app/demos`, mounted read-only into Caddy at `/srv/demos`) | where `agency.deploy_demo` (Tier ASK) publishes client demos, one release per deploy, live by an atomic symlink swap; refused if it overlaps the intake dir, `AGENCY_SITES_DIR` or `AGENCY_PUBLISH_DIR`. Served only by an explicit Caddy block per hostname (`import demo_site <slug>`). Host dir owned by uid 10001 (`docs/client-sites.md`) |
| `AGENCY_DEMO_INTAKE_DIR` | default `data/demos-intake` (= host `./data/app/demos-intake`, never mounted into Caddy) | where an operator drops a demo's static build as `<slug>/` before the `agency.deploy_demo` card; the audit (noindex, the demo banner, no live keys, no external form posts or scripts, size caps, no symlinks) runs on it and again on the release copy |
| `agency_skills` table (no env var) | rows written only by an approved `agency.add_skill` card (Tier ASK, card only) | guidance TEXT for the `sites` agent: at most 1500 characters, no `<`, no `{{`/`{%`, https links only, 1-8 lowercase triggers, never a built-in skill's name; loaded at agency setup under a fixed header. The card's rollback retires the row (nothing is deleted). `agency.request_template` (Tier ASK, card only) hands a new template family to the coding agent; ATLAS writes no HTML/JS: templates arrive only through the repo and CD |
| `"package": "500"` / `"300"` / `"100"` in `AGENCY_UPLOADS_DIR/<lead_id>/client.json` | not an env var: **you** write what the client bought. Optional `"domain"` (the client's own address for the site) and `"area"` (where they work) | the AI chat assistant on the site for `500` and `300` only; `100`, missing or anything else builds no chat. Never a build argument, so no model can pick a tier |
| `SITE_CHAT` | default on; `off` is the kill switch | every client site's chat rests at once ("Chat is resting — call/email us"); the `site_chat` check reads UNKNOWN |
| `SITE_CHAT_MONTHLY_CAP_USD` | default `5` | total chat spend per calendar month across all sites, booked in `site_chat_usage` (worst case reserved before each question). At the cap every chat rests; at 80% `site_chat` reads DEGRADED |
| `SITE_CHAT_USD_PER_MTOK_IN`, `SITE_CHAT_USD_PER_MTOK_OUT` | default `1` / `5` (Claude Haiku 4.5's list price) | what each answer is booked at, whichever TRIVIAL provider wrote it — a free tier is over-counted, never under |
| `SITE_CHAT_PER_IP_PER_MIN`, `SITE_CHAT_PER_SITE_PER_DAY` | default `10` / `300` | rate limits: questions per visitor address a minute (in memory), per site a day (from the table) |
| `SITE_CHAT_API_BASE` | default `https://atlas.storefront.test` (https only) | the endpoint baked into each site's `mapfront-chat.js` at build time; a change needs a rebuild. The "Have Mapfront call me" form's `mapfront-callme.js` posts to `<this>/public/call-me/<slug>` |
| `AGENCY_ESCALATION_PHONE` | default: `TWILIO_TO` | Miro's own number: every drafted reply to a lead ends "To speak to Miro directly, call ...", and the business line gives it when a caller or texter asks for him, a person or a manager. Never in a first cold email |
| `AGENCY_CALLME_IP_KEY` | default: `SESSION_SECRET` | HMAC key for the visitor IP stored with a "Have Mapfront call me" consent (`agency_lead_consent.evidence`) |
| `RECEPTION_SMS_AI` | default on (needs `RECEPTION_ENABLED` and `AGENCY_SMS_ENABLED`) | a lead's text to the business line is answered in the webhook's TwiML by Mapfront's AI assistant (package, list price, preview link; never a deal). `off` stops only the text replies |
| `AGENCY_PREVIEW_ORIGIN` | default `https://agency.storefront.test` | the chat answers CORS for this origin and the client's `domain`, nothing else |

**browser** — a server-side headless browser that never signs in.
Needs `pip install 'atlas[browser]' && playwright install chromium`;
`BRAVE_API_KEY` for search (DuckDuckGo otherwise); `BROWSER_DENY` adds hosts
to the deny list (nothing narrows it); `BROWSER_DATA_DIR` for screenshots.
The lasting guard against Chromium reaching a private address is a network
rule on the box, not only the in-process check — that is your call.

**messages** — Instagram DMs, replies Tier 2.
`INSTAGRAM_ACCOUNT_ID`, `INSTAGRAM_TOKEN` with the
`instagram_manage_messages` permission. Unset or unreachable is UNKNOWN.

**messaging** — Slack read only; no reply action.
`MESSAGING_SLACK_CHANNELS` (comma-separated channel ids to read, with the
existing `SLACK_BOT_TOKEN`).

(iMessage/BlueBubbles, Telegram and WhatsApp were removed on 2026-09-28.
Any `BLUEBUBBLES_*`, `TELEGRAM_*`, `WHATSAPP_*`, `MIRO_TELEGRAM_CHAT_ID` or
`MIRO_WHATSAPP_NUMBER` left in `.env` is ignored and can be deleted, and the
`/webhooks/whatsapp` callback set in Meta's app settings no longer exists.)

**calendar** (extended) — `FOCUS_BLOCKS` (`Mon-Fri 09:00-12:00 deep work`)
for the focus guard and booking; it rides on the existing `GOOGLE_*`.
**mail** (extended) — follow-ups and triage ride on `GOOGLE_*`;
`GOOGLE_ACCOUNT_EMAIL` (or each `MAIL_ACCOUNTS` entry's address) is what
"he sent it" is measured against, and unset reads UNKNOWN.

**research** — `BRAVE_API_KEY` or `PERPLEXITY_API_KEY` (section 7).
**vault** — documents under `data/vault` (or `VAULT_DIR`), then
`vault.reindex`. Recall by meaning needs `OLLAMA_URL` and an embedding model
(`ATLAS_EMBED_MODEL`, default `mxbai-embed-large`, pulled into Ollama);
without them recall is keyword-only and says so, and `check_index` counts
the chunks still waiting for an embedding.
**content** — `OPENART_*`, `RENDER_MONTHLY_CAP` (section 7).

**nightshift** — the overnight sandbox. `NIGHTSHIFT_ANTHROPIC_KEY` and
`NIGHTSHIFT_GIT_TOKEN` (a separate Anthropic key and a git token for the
clone and push, passed into the container and no other variable ATLAS
holds), the `nightshift` Docker network and the
`atlas-nightshift` image — see `bootstrap/nightshift/README.md`.
`NIGHTSHIFT_PREVIEW_BASE` for preview links. Merging stays Tier 2.

**purchasing** — prepares, never pays. `EBAY_*` unlocks
`purchasing.prepare_cart`; `prepare_flight` has no fare source. Paying is Tier 3, permanently denied.

**youtube** — per channel, `YOUTUBE_CLIPS_*`:
`CLIENT_ID`, `CLIENT_SECRET`, `REFRESH_TOKEN`, `CHANNEL_ID` (Google Cloud →
YouTube Data API v3 + YouTube Analytics API, an OAuth client, a refresh
token from the channel's own account). Uploads land private; going public
is Tier 2 every time.

**marketing**, **lessons**, **rhythm** — nothing to set. `MARKETING_DAY`
(0 = Monday … 6 = Sunday, default 6) moves the weekly ideas pass;
`REVIEW_HOUR` (default 21) the nightly review. Rhythm learns his hours from
28 days of his own activity before it says anything about them.

**Weather on the bottom bar** — `ATLAS_LAT`, `ATLAS_LON` (and an optional
`ATLAS_LOCATION_LABEL`); without them the bar says why there is no weather.

## Clips: the Mac agent hands Whop the posted links

Miro, 2026-09-29, in writing: an agent on his Mac submits each posted clip
to its campaign on Whop, in his own Chrome. ATLAS never submits there. It
lists what to submit and records what the agent says it submitted.

**Access.** Both calls work the way `/handoffs` does. They are mesh-only
(`MESH_ALLOWED_CIDRS` in the API; Caddy also lets in `ATLAS_TRUSTED_IPS`,
which must be added to `MESH_ALLOWED_CIDRS` as well). An address alone is
not enough: each call also needs the bearer `HANDOFF_TOKEN`, which must be
at least 24 characters. On the Mac the token lives in
`~/.config/atlas/handoff-token` (mode 0600). Give it to curl as config on
stdin, so it never shows in the process list or the shell history:

```sh
# 1. What to submit: posted clips awaiting submission, oldest first.
printf 'header = "Authorization: Bearer %s"\n' "$(cat ~/.config/atlas/handoff-token)" |
  curl -sS -K - https://atlas.storefront.test/clips/whop/pending
# -> 200 {"read_at": "2026-09-29T16:00:02+00:00",
#         "clips": [{"clip_id": 12, "campaign": "cod-mw4-warzone-operator",
#                    "campaign_name": "...", "whop_url": "https://whop.com/...",
#                    "platform": "instagram",
#                    "post_url": "https://www.instagram.com/reel/.../",
#                    "posted_at": "2026-09-29T14:03:11+00:00"}]}

# 2. After submitting clip 12 on Whop, record it (post_url must be the listed one):
printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' \
    "$(cat ~/.config/atlas/handoff-token)" |
  curl -sS -K - -X POST https://atlas.storefront.test/clips/whop/12/submitted \
       --data '{"post_url": "https://www.instagram.com/reel/.../"}'
# -> 200 {"ok": true, "detail": "...", "already": false}
```

**What is listed.** A clip is listed only when all of these hold:

- it is posted and not yet submitted;
- its post URL came from the platform's own publish response or from
  Miro's word through `clips.record_link`, never from a model
  (`clip_outputs.post_url_source`);
- its campaign has both a `permission_source` and a `whop_url` on file.

Until Miro gives a campaign's Whop link, the list stays empty for that
campaign.

**Recording.** The POST runs `clips.record_whop_submission` through
`kernel.unattended` and the executor: Tier 1, dry run first, audited, with a
rollback that touches only the agent's own record. The responses:

| Response | When |
|---|---|
| 409 with the reason | The URL is not the clip's own, or the clip is not offered. |
| 409 | The clip was already recorded as submitted any other way (for example on Miro's card). A second submission is refused. |
| 200 `"already": true` | The agent repeats its own identical record. Nothing is re-recorded or changed. |
| 409 | Watch-only mode, or a pause. |
| 400 | No `post_url`. |
| 401 | The token is missing or wrong. |
| 404 | The caller is off the mesh. |

Miro's Tier 2 `clips.submit_to_whop` card comes up at once, as before, for
a posted clip the agent will never see: its campaign has no `whop_url` or
no valid `permission_source`, or its post URL has no recorded source. For a
clip the list does offer, the card comes up only as the fallback, when the
clip is still not recorded 48 h after it posted. `clips.clips_earnings` reports
counts only: clips posted, submitted and awaiting submission, each with the
time it was read. It shows no money figure, because ATLAS does not read
Whop.

## Agency: Webild as an alternative builder

Miro, 2026-09-29: a client site can be built on Webild (webild.io) instead of
by `agency.build_site`, by an agent in his own Chrome where his Webild account
is signed in. ATLAS drafts the prompt (`agency.webild_brief`, from the lead
row and `AGENCY_UPLOADS_DIR/<lead_id>/client.json` only, which may now also
carry a `services` list), raises a Tier 2 card with the whole brief
(`agency.build_with_webild`), and once the agent reports the published URL
runs the 20-item launch check against the live site.

**Access.** The same as the Whop agent above: mesh-only, bearer
`HANDOFF_TOKEN`. `GET /webild/pending`, `POST /webild/{lead_id}/submitted`
`{"url": ...}`, `POST /webild/{lead_id}/report` `{"status": "needs_miro" |
"failed", "summary": ...}`. The Mac side is `bootstrap/mac/webild.py`
(install to `~/.config/atlas/webild.py`) and its instructions are
`bootstrap/mac/webild-agent.md`.

**`WEBILD_HOSTS`** — comma list of the domains Webild publishes sites on
(default `webild.io,webild.site`; neither was verifiable on 2026-09-29, so set
it from the first real published URL). A recorded URL must be https on a
subdomain of one of them, never the builder's own `www`/`app`/`api` hosts.

## ATLAS Academy support

Miro, 2026-10-11: a Help page with a form, a support address, an AI help
chat on the store, a community; replies within two business days;
"everything auto unless unsure". Code: `atlas/modules/academy/support*.py`,
`atlas/kernel/academy_support.py`, `atlas/api/academy_support.py`.

| Variable | Default | What it does |
|---|---|---|
| `ACADEMY_SUPPORT_ADDRESS` | `owner@example.com` | The address replies come from and the mailbox swept for tickets. Must be on `AGENCY_SEND_DOMAIN` (default `gmail.com`). |
| `ACADEMY_SUPPORT_MAILBOX` | `clips` | The `MAIL_ACCOUNTS` Gmail account that holds the support mail (connect it with `ATLAS_GOOGLE_MAILBOX=CLIPS bootstrap/google-login.sh`). Not configured means the SLA check is UNKNOWN. |
| `ACADEMY_SUPPORT_AUTOSEND` | on | `off` holds every reply for a Tier 2 card. |
| `ACADEMY_SUPPORT_DAILY_CAP` | 30 | Replies a day that send themselves (three a day to one address, always). |
| `ACADEMY_SUPPORT_TICK_MIN` | 5 | How often tickets are answered and the mailbox read. |
| `ACADEMY_SUPPORT_MIN_COVERAGE`, `ACADEMY_SUPPORT_MIN_SCORE` | 0.5, 4.0 | Below these the retrieval is "unsure" and the reply is a card. |
| `ACADEMY_SUPPORT_KB` | `data/academy/support_kb.json` | The product-file knowledge base, built on the Mac by `bootstrap/academy-files.sh` (never in git). |
| `ACADEMY_SUPPORT_SLACK_CREATE`, `ACADEMY_SUPPORT_SLACK_NAME` | off, `academy-support` | `on`: the next tick creates the channel with the bot (`academy.create_support_channel`) and routes support cards there. Or set `SLACK_CHANNELS=...,academy_support:<channel id>` yourself. |
| `ACADEMY_CHAT`, `ACADEMY_CHAT_DAILY_CAP` | on, 300 | The store's help chat, and its model calls a day. |
| `ACADEMY_SLACK_COMMUNITY_URL`, `ACADEMY_TEAMS_COMMUNITY_URL` | unset | Join links shown on the Help page only when set (https). |

What Miro sets up (ATLAS cannot):

1. **The support address.** All public, reply and support mail is
   owner@example.com (single-sourced in `atlas/modules/agency/outreach.py`).
   Sending goes through Gmail SMTP with the existing `GMAIL_APP_PASSWORD`;
   no forward, Resend or registrar setup is needed.
2. **The Slack channel for cards.** Either set `ACADEMY_SUPPORT_SLACK_CREATE=on`
   (the bot makes #academy-support and is a member; join it yourself), or
   make it and set `SLACK_CHANNELS` with its id. Unset, the cards go to the
   default channel.
3. **The community.** Buyers invited into ATLAS's own workspace would see
   its operations channels and cards, so use a separate Slack workspace (or
   single-channel guests): create the workspace, then Invite people, then
   copy an invite link and set `ACADEMY_SLACK_COMMUNITY_URL`. For Microsoft
   Teams, create a community in the free Teams app with your own Microsoft
   account, copy its invite link and set `ACADEMY_TEAMS_COMMUNITY_URL`.
   Then republish the store.

## Agency: the AI chat assistant on client sites

> **Off since 2026-10-29.** ATLAS runs on Miro's Mac at `127.0.0.1` only, so
> nothing outside the Mac can reach `/public/*`. `PUBLIC_DOOR = False` in
> `atlas/modules/agency/client.py` keeps the chat widget and the "Have Mapfront
> call me" form off every build (sites already deployed keep their old copy
> until rebuilt). The storefront, landing and help pages use `mailto:` forms to
> owner@example.com instead (`store_origin.CONTACT_MAIL`). Turning the widgets
> back on is a reviewed code change plus a public door that is not ATLAS itself;
> the section below describes how they work behind one.

The $500 and $300 Mapfront packages include an AI chat assistant on the
client's site; the $100 one does not. `agency.build_site` adds it when the
client's `client.json` says `"package": "500"` or `"300"`: a small script
(`mapfront-chat.js`) on every page, a line on the privacy page saying what the
chat does with what is typed, and the facts it answers from, written BESIDE
the site as `.<slug>.chat.json` (never deployed). Those facts are the client's
own `client.json` and lead row (services, area, address, phone, email, hours,
reply time, FAQs), plus the home page's visible text kept apart as marketing
copy. The visitor's question goes to `POST /public/site-chat/<slug>` on
`atlas.storefront.test` (atlas/api/site_chat.py), which answers with the TRIVIAL
model tier in 60 words or fewer. Code, not the prompt, enforces the rules:
a figure, email or link that the client's facts do not contain is replaced
with "call/email them"; a card or SSN number never reaches a model; a reply
that asks for card, bank or ID details becomes a fixed refusal. The chat
cannot book, order or send anything. It says it is an AI, and nothing it is
asked is stored: `site_chat_usage` keeps slug, outcome, tokens and dollars.

Launch check item 21 (`AI chat assistant`, chat packages only) is an ISSUE,
and blocks `agency.deploy_site`, when a page lacks the widget, the widget file
is missing or posts somewhere else, the privacy page does not name the
assistant, `SITE_CHAT=off`, or the endpoint has no facts for the slug.

The `agency.site_chat` check asks a fixture site's phone number through the
same pipeline once a day. It reads OK only when a real answer contains it,
DEGRADED at 80% of `SITE_CHAT_MONTHLY_CAP_USD`, FAILING at the cap or on a
wrong answer, and UNKNOWN when switched off, with no model, or with an
unreadable ledger. Questions about the chat itself: owner@example.com.

**Turning on `AGENCY_AI_CHAT_BUILT=true`** (the lead-follow-up drafts may then
promise a chat; until it is set they must not). Set it only after all of
these hold on the server:

1. This branch is deployed. The `site_chat_usage` table exists (`init-db`
   creates it), and Caddy has reloaded with the `handle /public/site-chat/*`
   block.
2. From outside the mesh, `curl -s https://atlas.storefront.test/public/site-chat/nobody-1`
   prints `{"error":"not here"}`. That is the API's JSON 404, so Caddy passed
   the request through; Caddy's own 404 is plain `not here`. Then
   `curl -s --path-as-is -o /dev/null -w '%{http_code}\n' https://atlas.storefront.test/public/site-chat/../api/overview`
   prints `404`.
3. Build a real $500 or $300 client (`"package"` in its `client.json`). Its
   `<slug>.LAUNCH_CHECK.md` shows item 21 OK, and after deploy
   `curl -s -H 'Origin: https://agency.storefront.test' https://atlas.storefront.test/public/site-chat/<slug>`
   answers `{"status": "ready"}`.
4. Open `https://agency.storefront.test/preview/<slug>/` on a phone, ask for the opening hours and
   something the client never said (a price). The first answer matches
   `client.json`; the second offers the phone/email.
5. The dashboard shows `agency.site_chat` OK after its first run.

Then `bootstrap/set-secret.sh AGENCY_AI_CHAT_BUILT` with the value `true` and
restart the API. To stop every chat at once, set `SITE_CHAT=off`. The widgets
show "Chat is resting — call/email us" and nothing needs rebuilding. Turn
`AGENCY_AI_CHAT_BUILT` back off if the chat will stay off.

## Agency: client-site documents are Claude Docs artifacts

Miro's rule, 2026-10-29: every document made for a client site (brief,
handoff, report, proposal, client guide) is a Claude Docs artifact. ATLAS
cannot create one (only the executor acts, and a Docs artifact is made from
Miro's Mac), so it goes through the handoff runner:

1. The `sites` agent drafts it with `sites_draft_doc` (`agency.draft_doc`,
   Tier AUTO: one row in `agency_site_docs`, nothing leaves ATLAS) and raises
   an `agency.publish_doc` card (Tier ASK, card only). Client words go in
   `client_text` and reach the brief through `wrap_untrusted`. By hand:
   `python -m atlas.cli sites-doc <slug> <kind> "<title>" body.md [client.txt]`.
2. Approving the card queues a handoff. The runner on the Mac claims it,
   creates the artifact with the docs skill or connector, adds one README line
   to the site's repo when the brief allows, and reports with
   `handoff.py report <id> done <summary-file> --artifact-url <claude.ai link>`
   (runner prompt: `~/.claude/scheduled-tasks/atlas-handoff-runner/SKILL.md`,
   "DOCS TASKS").
3. `/handoffs/{id}/report` records the link on the document (only a
   `https://claude.ai/artifact/…` or `…/code/artifact/…` link, only with
   `done`) and the Slack receipt carries it, labelled as the agent's word.
   The Client Sites tab links it; a document without a link shows UNKNOWN.

Needs `HANDOFF_TOKEN` (24+ characters) on both sides, like every handoff.
Rollback: the draft card's rollback withdraws the draft; the publish card's
cancels the handoff before it is claimed. Nothing is deleted.

## LinkedIn

No key, no token. Sign in once from the Mac:

```bash
./bootstrap/linkedin-login.sh
```

A real browser window opens; sign in with "Keep me signed in". The saved
session (cookies, no password) is synced to the server and the checks run
from it. Re-run it whenever `linkedin.sweep` says the session is signed out.
The sweep runs twice a day; the daily post for each page arrives as an
approval card, and drafts arrive as cards too.

### Sage and the ExampleTutor page (`SAGE_TUTORCO_LINKEDIN`)

Off by default. `SAGE_TUTORCO_LINKEDIN=on` (with `tutorco` in `LINKEDIN_PAGES_ACTIVE`)
makes Sage, the SEO agent, owner of the page calendar; the TUTORCO agent keeps the
product facts. Every Monday 10:00 New York, job `sage_tutorco_weekly`:

- reads the page's followers and top post from the sweep's state file (each with
  its read time; UNKNOWN when the sweep could not read them, never a guess);
- drafts at most `SAGE_TUTORCO_MAX_POSTS_PER_WEEK` posts (default 3, max 7) into the
  page's post bank as drafts. Each passes the claim check (built features only,
  no planned or group-class features, no invented stats or testimonials) and
  becomes the usual `linkedin.approve_draft` card, then the Tier 2
  `linkedin.publish_post` card. Nothing posts without you; an unanswered card
  expires;
- stores one SEO note of three suggested edits (page text is fetched read-only
  and wrapped as untrusted) and raises a card-only `tutorseo.accept_seo_note`
  that records your decision. ATLAS changes no page.

While on, the old series refill for `tutorco` stands down. `ATLAS_WATCH_ONLY`
holds the job like any other.

## Hands-free wake

Say "atlas" and talk, with no click: whisper-tiny.en runs in the browser tab
and listens for the word. Nothing is uploaded until the conversation opens.

1. Fetch the model **before** building the UI. Vite copies
   `ui/public/wake/` into `ui/dist/wake/`, and `/wake` is served from there:

   ```bash
   ./ui/scripts/fetch-whisper-model.sh   # about 75 MB, onnx-community/whisper-tiny.en
   cd ui && npm run build                # or rebuild the Docker image
   ```

   The script pins the model revision and prints each file's size and
   SHA-256. The files are git-ignored, so re-run it after a fresh clone.
2. Set `VOICE_LOCAL_WAKE=1`.
3. For spoken replies, set `FISH_AUDIO_API_KEY`. `FISH_AUDIO_VOICE_ID` and
   `FISH_AUDIO_MODEL` are optional; the model defaults to `s1`. Without the
   key ATLAS still answers, typed only.

`/api/voice/capabilities` reports `local_wake: true` only when the flag is
set **and** every model file is in the built UI under `ui/dist/wake/whisper/`.
Files fetched after the build do not count until you rebuild.

**When the model is missing**, the wake detector falls back to the next one
available (Porcupine, pinch, the browser recogniser, then click-to-talk).
The hint under the core names whichever one is actually running. With none
configured it reads **"Click to talk — no wake detector running"**, never
"say atlas". When the core is open, the "Not configured" line lists
`wake/whisper/ in the built UI (local wake -- run
ui/scripts/fetch-whisper-model.sh, then build)`. When the hands-free model
is running, the hint reads **"say “atlas” — heard on this machine, nothing
sent"**.

The mic badge (and "Tap to mute" beside the status pill) is the mute
control, and only that. It releases the microphone and disarms the wake
detector, pinch-to-wake included, until you tap it again. Waking belongs to
the wake word, the pinch, the core and "Talk to ATLAS". If the whisper gate
stops while running (for example, the worker crashes), the next detector
takes over and the hint says **"wake gate stopped (…) — now: …"**.

### The other wake gates

| Variable | Needs | What it does |
|---|---|---|
| `VOICE_PINCH_WAKE=1` | `ui/scripts/fetch-hand-model.sh` run before the build (the hand model plus MediaPipe's wasm under `ui/public/wake/`) | pinch to wake, tracked on this device; the sensor frames never leave the tab |
| `VOICE_GESTURES=1` | the same hand model | palm as a cursor, fist to grab and drag, swipe, dismiss |
| `VOICE_CLAP_WAKE=1` | nothing else | a double clap wakes ATLAS too, heard on this device and never uploaded. It rides **beside** whichever of the spoken wake word, the local whisper gate or the pinch is live; beside the browser recogniser or click-to-talk it is inert and nothing claims it. The hint under the core, the core's screen-reader label and the "Talk to ATLAS" caption all add "or double clap" only when it is really armed |
| `VOICE_BROWSER_WAKE=1` | nothing else | the browser's own recogniser as a last-resort wake word. **In Chrome this streams audio to Google**, which is why it is off unless you set it, and the hint says so |

A flag without its model file is reported, not honoured:
`/api/voice/capabilities` says `false` and `missing` names the script to run.

## Deploy order

The image is built on the server from the files you copy there; there is no
git checkout on the box.

1. **Fetch the models on a machine with `ui/node_modules`** (the Mac is
   fine): `./ui/scripts/fetch-hand-model.sh` copies MediaPipe's wasm out of
   `ui/node_modules/@mediapipe/tasks-vision/wasm`, which the server does not
   have outside Docker; `./ui/scripts/fetch-whisper-model.sh` downloads
   whisper-tiny.en. Both write under `ui/public/wake/`, which is git-ignored
   but **not** Docker-ignored, so run them in `~/atlas-agency` (the checkout
   the local stack builds from) and the Dockerfile's `COPY ui/ ./` carries
   `ui/public/wake/` into the build.
2. **Then** `docker compose up -d --build api`. The UI stage runs `npm ci`
   against `ui/package-lock.json`, which must list every dependency in
   `ui/package.json` (it does as of Task 17; `npm ci` refuses otherwise), and
   `onnxruntime-node`'s install script needs outbound HTTPS during the build.
3. Set `ATLAS_DEPLOY_SHA` to the commit you deployed (`git rev-parse --short
   HEAD` on the Mac, into `.env`). The image carries no `.git`, so without it
   `self_status` and "how are you" report the deploy as unknown rather than
   guessing.
4. Then any bootstrap step (`docker compose exec -T api python
   bootstrap/<step>.py`): `exec` runs the image's copy, so it must follow the
   rebuild, never precede it.

Files fetched after the build do not count until you rebuild:
`/api/voice/capabilities` checks the built UI, not the source tree.

## Your sign-off: the public `/media/*` route

`instagram.publish` posts a rendered video by URL, so Instagram's own
servers have to fetch the file. `GET /media/{token}` (`atlas/api/media.py`)
serves exactly one kind of file — a finished `content.render_video` render
under `data/content` — to anyone holding its token: 32 random bytes minted
only once the render is saved, expiring after 24 hours, and every miss
(wrong token, expired, rolled back, file gone, a path outside
`data/content`, a symlink) is the same 404. No session, because Instagram
has none to present; the token is the gate, the same model as
`/webhooks/twilio/audio/{token}`.

For Instagram to reach it, the public listener must pass it through. The
Caddyfile in this repo already has the block:

```
handle /media/* {
    reverse_proxy api:8080
}
```

ATLAS Academy's tutorial drafts use the same route with a `tut.` token
(`academy_tutorial_reviews`, 7 days): the episode's `video.mp4`, or with
`.jpg` on the end its `thumb.jpg`, so Miro can watch a draft from the
Slack card before approving it. Same 404 for every miss.

That is a widening of the public surface — until now 443 served only
`/webhooks/*` and `/healthz` — so it is **yours to approve** before the new
Caddyfile goes on the box. Without it, every `/media/*` request 404s at the
edge, so Instagram cannot fetch a render and a publish that references one
fails there; nothing else changes.

## TradingView alerts to Ivan: `/hooks/tradingview`

Your TradingView alerts reach Ivan (the markets agent). ATLAS stores each
one and reads it back to you. It never trades on one: no code in ATLAS can
place an order, and money out is Tier 3. The route is
`atlas/api/tradingview.py`, and the check and brief are
`atlas/modules/markets/tradingview.py`.

**1. The secret.** TradingView cannot sign a request, so the gate is a long
random secret that you put in the alert's JSON body. Generate one on your
Mac, and don't paste it into a chat:

```bash
openssl rand -hex 32          # copy it; you need it twice
bootstrap/set-secret.sh TRADINGVIEW_WEBHOOK_SECRET
```

A secret shorter than 32 characters counts as not set. While it is not set,
the route answers 503 `not configured`, and the `markets.tradingview` check
is UNKNOWN.

**2. The webhook URL:** `https://atlas.storefront.test/hooks/tradingview`. Only
that exact path is public, not `/hooks/*`. It needs the
`handle /hooks/tradingview` block in the Caddyfile. That block widens the
public surface the same way `/media/*` did, so it is **yours to approve**
before the Caddyfile goes on the box. Without it, TradingView gets a 404 at
the edge.

**3. The alert.** You need 2-factor authentication on for TradingView to
allow webhooks. In the alert dialog, go to Notifications → Webhook URL and
enter the URL above. Put this in Message, with your secret in place of
`PASTE-THE-SECRET`:

```json
{
  "secret": "PASTE-THE-SECRET",
  "ticker": "{{ticker}}",
  "exchange": "{{exchange}}",
  "interval": "{{interval}}",
  "price": {{close}},
  "time": "{{time}}",
  "name": "AAPL 50-day breakout",
  "action_hint": "alert",
  "message": "crossed the 50-day line"
}
```

Leave `{{close}}` unquoted so it arrives as a number. A quoted price is
refused. Only `ticker` is required. The accepted fields are a closed set,
and any other key gets a 422 that names the field:

| Field | Rule |
|---|---|
| `ticker` | A–Z 0–9 `.` `:` `-` `!`, 20 characters at most (upper-cased; `ES1!`, `NQ1!`) |
| `exchange` | A–Z 0–9 `_` `.` `-`, 20 characters at most |
| `interval` | A–Z 0–9, 8 characters at most (`{{interval}}`: `1D`, `60`) |
| `price` | a JSON number |
| `time` | ISO 8601 with a zone (`{{time}}` is UTC) |
| `name` (or `strategy`) | 80 characters at most |
| `action_hint` | one of buy, sell, long, short, exit, alert. It is stored as your label and never acted on |
| `message` | 500 characters at most. It is stored as untrusted text and shown to Ivan only inside `wrap_untrusted` |

The body can be at most 8 KB and must be JSON. TradingView sends
`application/json` only when the message parses as JSON, so check the
braces. A wrong secret gets a bare 401. The same alert (same ticker, time,
name and price) sent again within 10 minutes is stored once. If an alert
is refused, the "Webhook status" column in TradingView's alert log shows
it.

**Where the secret shows up.** It sits in the alert's message, so every
other notification you turn on for that alert (email, app, popup) shows it.
Turn those off for these alerts. To rotate it, run `set-secret.sh` again and
update the message in every alert.

**4. Optional: only TradingView's addresses.** TradingView publishes the
addresses it sends webhooks from (their "About webhooks" help page, read
2026-09-29): 52.89.214.238, 34.212.75.30, 54.218.53.128, 52.32.178.7.

```bash
bootstrap/set-secret.sh TRADINGVIEW_ALLOWED_IPS
# 52.89.214.238,34.212.75.30,54.218.53.128,52.32.178.7
```

When this is set, any other address gets a 403. If TradingView changes its
addresses, check their page and update the list. An address with 30
refused requests in a minute (a wrong secret, bad JSON, a disallowed
address) gets a 429 for every further refusal. A request with the right
secret is never counted or rate-limited. Every TradingView user sends from
the same four addresses, so counting those requests would let someone
else's spam, or your own burst of alerts, lose you a real alert, and
TradingView does not retry.

**What reads it.** The `markets.tradingview` check runs every 15 minutes. It
reports the last alert's time, how many arrived since local midnight, and
the latest alert per ticker. It is UNKNOWN when the secret is not set, when
the table can't be read, or when no alert has arrived within
`TRADINGVIEW_STALE_HOURS` (default 72). Ivan's brief (`module_brief
markets`, which also feeds your morning brief and the dashboard) lists the
newest three alerts. It shows each alert's name and message quoted inside a
`wrap_untrusted` block. Where Ivan has a fresh basket price for the same
stock, the brief shows both prices with both times.

**The Slack alert.** Each accepted alert is also sent to you once, as a
`markets.alert` with topic `markets`. It lands in the channel mapped to
`markets` in `SLACK_CHANNELS`; for #trades, add `markets:<the #trades
channel id>` there. Unmapped, it falls back to `SLACK_CHANNEL`. The text
gives the alert's name, the ticker, TradingView's price and time, when
ATLAS received it, and Ivan's own latest price of that stock with its
times when he has a reading from the last 6 hours. It never carries the
`action_hint` or the message.

The pipeline's rules all apply:
- It is sent once.
- It counts toward the `MARKETS_ALERT_DAILY_CAP` of 10 market alerts a day.
- In quiet hours it is held for the morning brief.
- Anything not sent within 24 hours expires and is never sent late.
- The pipeline refuses any text with an advice word. An alert named, for
  example, "AAPL buy zone" is still sent, but its name is replaced with
  "(name left out: it carries an advice word)".
- Under `ATLAS_WATCH_ONLY` nothing is sent.

The row in `tradingview_alerts` is marked notified only when the alert is
delivered. An alert held for the morning brief counts as delivered.

## After setting anything

```bash
python -m atlas.cli connect <module>
```

It runs that module's real checks against the credential and reports what it
saw — a value that is present but wrong looks identical to one that is
missing, from any other angle.


## PDF viewer behind Caddy

The Knowledge page shows vault PDFs in an `<iframe>` from
`/api/knowledge/file/{id}`. Through `atlas.storefront.test` that frame is blank,
because the Caddyfile forbids framing everywhere:

- the global CSP is `default-src 'none'` with no `frame-src`;
- every response, the PDF included, carries `X-Frame-Options "DENY"` and
  `frame-ancestors 'none'`.

The "open in a new tab" link beside the frame always works. To let the frame
render, which loosens framing on one mesh-only path, make these two edits to
the `atlas.storefront.test` site block, then reload Caddy.

1. In the global `header` block, add `frame-src 'self';` to the
   Content-Security-Policy value:

   ```
   Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
   ```

2. Add a path-specific override after the global `header` block:

   ```
   header /api/knowledge/file/* {
       X-Frame-Options "SAMEORIGIN"
       Content-Security-Policy "default-src 'none'; frame-ancestors 'self'"
   }
   ```

The PDF can then only be framed by the dashboard itself, and nothing else on
the site becomes frameable. This is your call to make; it is not applied by
default.

## The learning layer

Nothing to configure; it learns from the decision log alone. Two knobs:

- `TOURNAMENT_HOUR` — local hour for the nightly bake-off (default 3).
- `QUIET_HOURS` — already set for comms; the same window is a feature.

From the box: `atlas model status`, `atlas model tournament` (run tonight's
now), `atlas model rollback`, `atlas model backfill`. The Activity screen
shows the reliability curve and who is serving.

## Perplexity — research and reasoning over MCP (Phase 20)

One key turns Perplexity on everywhere it is good for: knowledge mode's
answers (with "Sources:" under "More:"), the research report's source list,
the weekly monetisation pass's background reading, agency lead reputation,
the night shift's `nightshift.research` job, school references (on the
course and a short topic from the item's title, never the item's text), and
the `perplexity_ask` / `perplexity_reason` / `perplexity_research` read
tools for concierge, growth, ops, marketing and agency — never for a private
agent: inbox and school hold none. Without it, each of those behaves exactly
as before.

| Variable | Value |
|---|---|
| `PERPLEXITY_API_KEY` | perplexity.ai → Settings → API (a secret; sent only as the bearer to Perplexity's **MCP server** — there is no other endpoint this key is used against) |
| `PERPLEXITY_MCP_URL` | optional; default `https://api.perplexity.ai/mcp`. This is the **only door** (Task 50): Perplexity's chat-completions endpoint returns `403 chat_completions_not_available` in production and `/v1/responses` rejects model `sonar` — verified live, 2026-09-25. When the MCP server cannot be reached, refuses the token, or no longer offers one of the four tools ATLAS calls, that is a plain reason, never a second attempt against another endpoint |
| `PERPLEXITY_MONTHLY_CAP` | optional; calls per calendar month, default `300`, **shared by every caller** — knowledge, research, monetisation, agency reputation (one call per lead, cached 30 days), the night shift, school and the agents' tools all count against the same number. At the cap every caller is refused with "perplexity cap reached: N/N this month" and nothing is sent |

What it is and is not: Perplexity is a **source, never an oracle**. Every
answer is quoted as untrusted data with its citations and the time it was
read, and nothing it says is ever reported as a figure ATLAS measured. A
question that names one of your own modules, businesses, agents or checks
("how is CloudHost doing") is refused before the call — that is answered from
the estate. Only a topic or a question leaves the box: email addresses and
phone numbers are stripped first, and a document, message or coursework text
is never sent. `perplexity.budget` on the board shows this month's count
against the cap with its read time, and reads UNKNOWN while the key is
unset, when the count cannot be read, or when a live probe (`initialize` /
`tools/list`, never a `tools/call` — checking the budget never spends one)
finds the MCP server unreachable or missing a tool it used to offer.
