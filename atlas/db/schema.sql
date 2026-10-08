-- ATLAS schema. Postgres 16 + pgvector.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- The watched things. One row per property; modules register themselves here.
CREATE TABLE IF NOT EXISTS properties (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL,              -- business | infra | personal | social
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS checks (
    id           SERIAL PRIMARY KEY,
    property_id  INT REFERENCES properties(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    interval_s   INT NOT NULL,
    severity     SMALLINT NOT NULL DEFAULT 1,
    baseline_sigma REAL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    -- Absolute wall-clock times. The scheduler's own schedule lives in a
    -- process-local monotonic clock, which cannot be rendered as a time, does
    -- not survive a restart, and is invisible to a request handler.
    last_run_at  TIMESTAMPTZ,
    next_run_at  TIMESTAMPTZ,
    UNIQUE (property_id, name)
);
ALTER TABLE checks ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMPTZ;
ALTER TABLE checks ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ;

-- The heart of it. Everything else is derived from this table.
CREATE TABLE IF NOT EXISTS observations (
    id         BIGSERIAL PRIMARY KEY,
    check_id   INT REFERENCES checks(id) ON DELETE CASCADE,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    health     TEXT NOT NULL,               -- ok | degraded | failing | unknown
    value      DOUBLE PRECISION,
    unit       TEXT,
    detail     TEXT,
    evidence   JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS observations_check_at ON observations (check_id, at DESC);
CREATE INDEX IF NOT EXISTS observations_at ON observations (at DESC);

CREATE TABLE IF NOT EXISTS incidents (
    id           SERIAL PRIMARY KEY,
    property_id  INT REFERENCES properties(id),
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ,
    severity     SMALLINT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT,
    correlation_key TEXT,                   -- shared when one cause hits many
    runbook_id   INT
);
CREATE INDEX IF NOT EXISTS incidents_open ON incidents (resolved_at) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS proposals (
    id          SERIAL PRIMARY KEY,
    incident_id INT REFERENCES incidents(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    action      TEXT NOT NULL,
    params      JSONB NOT NULL DEFAULT '{}',
    tier        SMALLINT NOT NULL,
    reason      TEXT NOT NULL,
    evidence    JSONB NOT NULL DEFAULT '{}',
    rollback_plan TEXT,
    confidence  REAL,
    -- pending | approved | declined | expired | executed | failed
    -- | suppressed | superseded
    -- | held (restored from a backup as pending; kernel/restore.py)
    status      TEXT NOT NULL DEFAULT 'pending',
    decided_at  TIMESTAMPTZ,
    decided_via TEXT,                        -- slack | sms | matrix | imessage
    always      BOOLEAN NOT NULL DEFAULT FALSE,  -- "approve + always do this"
    -- The dry run as it stood when the proposal was raised. Miro must be able
    -- to read what WOULD happen before deciding; execute() runs its own dry
    -- run again at approval time, because the world may have moved since.
    dry_run     TEXT,
    dry_run_ok  BOOLEAN,
    dry_run_at  TIMESTAMPTZ
);
-- What caused this proposal, so the same cause cannot raise it twice while
-- the first one is still waiting. Without it, a check that stays UNKNOWN
-- produces a fresh proposal every cycle: 70 identical "file an issue about
-- the missing FlyCheap Stripe key" cards accumulated in one day, which
-- buries every real decision in the same list.
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS dedup_key TEXT;
CREATE INDEX IF NOT EXISTS proposals_dedup
    ON proposals (dedup_key, status) WHERE status = 'pending';
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS dry_run TEXT;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS dry_run_ok BOOLEAN;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS dry_run_at TIMESTAMPTZ;
-- What was approved, recorded when the card is raised (P04, S08):
-- the catalog version of the action (registry.action_version) and the
-- sha256 of the canonical JSON of its params. decide() compares both with
-- the action and params as they are when it runs; a mismatch is refused
-- before anything runs and the card is superseded by a fresh one. NULL on a
-- card raised before this existed: no check, exactly as before.
-- Statuses gain 'superseded' (terminal): approved, then refused because
-- what would run is not what was approved.
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS catalog_version TEXT;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS approved_payload_hash TEXT;
CREATE INDEX IF NOT EXISTS proposals_pending ON proposals (status, expires_at);

-- Append-only, hash-chained. Never UPDATE or DELETE from this table.
CREATE TABLE IF NOT EXISTS actions_log (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL,
    event       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prev_digest CHAR(64) NOT NULL,
    digest      CHAR(64) NOT NULL
);
REVOKE UPDATE, DELETE ON actions_log FROM PUBLIC;
-- A linear chain has exactly one entry per predecessor. This makes a fork
-- impossible at the storage layer even if two writers race the advisory lock.
CREATE UNIQUE INDEX IF NOT EXISTS actions_log_prev_digest ON actions_log (prev_digest);
-- Slack event ids are kept here (event 'slack.event') so a redelivered event
-- is never acted on twice, across restarts; this makes that lookup cheap.
CREATE INDEX IF NOT EXISTS actions_log_event_subject ON actions_log (event, subject);

CREATE TABLE IF NOT EXISTS runbooks (
    id          SERIAL PRIMARY KEY,
    signature   TEXT NOT NULL,
    embedding   vector(1024),
    property    TEXT,
    diagnosis   TEXT NOT NULL,
    action      TEXT NOT NULL,
    params      JSONB NOT NULL DEFAULT '{}',
    rollback_plan TEXT,
    successes   INT NOT NULL DEFAULT 0,
    failures    INT NOT NULL DEFAULT 0,
    promoted    BOOLEAN NOT NULL DEFAULT FALSE,   -- promoted to Tier 1 by Miro
    last_used   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    source      TEXT NOT NULL,        -- gmail | fiverr | matrix | slack | imessage
    external_id TEXT,
    counterpart TEXT,
    received_at TIMESTAMPTZ,
    body        TEXT,
    draft       TEXT,
    sent_body   TEXT,                 -- what Miro actually sent, after editing
    status      TEXT NOT NULL DEFAULT 'drafted',
    embedding   vector(1024)
);

-- Every finding the detector raised. Previously a Finding existed only in
-- memory for the length of one scheduler iteration, so there was nothing to
-- link to, scroll back through, or point a permalink at.
CREATE TABLE IF NOT EXISTS findings (
    id             BIGSERIAL PRIMARY KEY,
    at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    check_key      TEXT NOT NULL,          -- "flycheap.affiliate_links"
    property       TEXT NOT NULL,          -- "flycheap"
    severity       SMALLINT NOT NULL,
    kind           TEXT NOT NULL,          -- rule | baseline
    health         TEXT NOT NULL,          -- ok | degraded | failing | unknown
    title          TEXT NOT NULL,
    detail         TEXT NOT NULL,
    value          DOUBLE PRECISION,
    unit           TEXT,
    evidence       JSONB NOT NULL DEFAULT '{}',
    observed_at    TIMESTAMPTZ NOT NULL,
    proposal_id    INT REFERENCES proposals(id) ON DELETE SET NULL,
    incident_id    INT REFERENCES incidents(id) ON DELETE SET NULL,
    correlation_key TEXT
);
CREATE INDEX IF NOT EXISTS findings_at ON findings (at DESC);
CREATE INDEX IF NOT EXISTS findings_property ON findings (property, at DESC);
CREATE INDEX IF NOT EXISTS findings_severity ON findings (severity, at DESC);

-- Things ATLAS wants to say that are not failures: recommendations, trends.
CREATE TABLE IF NOT EXISTS signals (
    id         SERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    property   TEXT,
    severity   SMALLINT NOT NULL DEFAULT 1,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    suggests   TEXT,
    params     JSONB NOT NULL DEFAULT '{}',
    delivered  BOOLEAN NOT NULL DEFAULT FALSE
);

-- Queue for anything held back by quiet hours.
CREATE TABLE IF NOT EXISTS brief_queue (
    id        SERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,
    severity  SMALLINT NOT NULL DEFAULT 1,
    delivered BOOLEAN NOT NULL DEFAULT FALSE
);

-- Persistent agent memory. Four kinds, namespaced, semantically searchable.
CREATE TABLE IF NOT EXISTS memories (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind       TEXT NOT NULL,        -- episodic | semantic | procedural | working
    namespace  TEXT NOT NULL,        -- people | incidents | brand_voice | security ...
    content    TEXT NOT NULL,
    meta       JSONB NOT NULL DEFAULT '{}',
    importance REAL NOT NULL DEFAULT 0.5,
    embedding  vector(1024)
);
CREATE INDEX IF NOT EXISTS memories_ns_kind ON memories (namespace, kind, at DESC);
CREATE INDEX IF NOT EXISTS memories_vec ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Agent run transcripts, for debugging why a specialist did what it did.
CREATE TABLE IF NOT EXISTS agent_runs (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent      TEXT NOT NULL,
    task       TEXT NOT NULL,
    model      TEXT,
    provider   TEXT,
    turns      INT NOT NULL DEFAULT 0,
    tokens_in  INT NOT NULL DEFAULT 0,
    tokens_out INT NOT NULL DEFAULT 0,
    outcome    TEXT,
    transcript JSONB NOT NULL DEFAULT '[]'
);


-- Briefs and incident reports ATLAS has sent. A brief is built from live
-- queries, so unless the rendered result is kept there is no archive to look
-- back at and nothing for a texted link to point at a week later.
CREATE TABLE IF NOT EXISTS reports (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,            -- brief | incident | weekly
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,            -- the rendered text, as sent
    sections  JSONB NOT NULL DEFAULT '[]',
    modules   TEXT[] NOT NULL DEFAULT '{}',
    delivered_via TEXT
);
CREATE INDEX IF NOT EXISTS reports_at ON reports (at DESC);
CREATE INDEX IF NOT EXISTS reports_kind ON reports (kind, at DESC);


-- Voice conversations. Held to the same retention as browsing history: the
-- audio is never stored at all, only what was said and what ATLAS answered.
CREATE TABLE IF NOT EXISTS voice_turns (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    transcript TEXT NOT NULL,
    answer     TEXT,
    intent     TEXT NOT NULL DEFAULT 'question',   -- question | action_request | knowledge
    agent      TEXT,
    ms         INT
);
CREATE INDEX IF NOT EXISTS voice_turns_at ON voice_turns (at DESC);


-- ---------------------------------------------------------------- browser
-- Raw browsing history from the extension. PRIVATE: this table is the reason
-- Need.PRIVATE exists and routes to a local model only. It is deleted after 90
-- days by kernel.privacy.purge_browser_history; the rolled-up stats below
-- survive, so long-term patterns remain without keeping the URLs.
CREATE TABLE IF NOT EXISTS browser_events (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL,
    kind        TEXT NOT NULL,            -- visit | dwell | tabs
    domain      TEXT NOT NULL,
    url         TEXT,                     -- NULL once rolled up
    title       TEXT,
    dwell_ms    BIGINT NOT NULL DEFAULT 0,
    tab_count   INT,
    client      TEXT NOT NULL DEFAULT 'extension'
);
CREATE INDEX IF NOT EXISTS browser_events_at ON browser_events (at DESC);
CREATE INDEX IF NOT EXISTS browser_events_domain ON browser_events (domain, at DESC);

-- Permanent, aggregated, no URLs. One row per domain per local day.
CREATE TABLE IF NOT EXISTS browser_rollup (
    day         DATE NOT NULL,
    domain      TEXT NOT NULL,
    visits      INT NOT NULL DEFAULT 0,
    dwell_ms    BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, domain)
);
CREATE INDEX IF NOT EXISTS browser_rollup_day ON browser_rollup (day DESC);


-- Standing rules: things Miro said once that should hold from then on.
--
-- "From now on, whenever any node hits 80% disk, just prune and tell me
-- after."
--
-- Read this table for DISPLAY ONLY. Nothing in the permission layer or the
-- executor consults it, and nothing may be added that does. A standing rule
-- that made the next occurrence run unasked would be a runtime tier
-- override, which CLAUDE.md forbids in terms that name this exact situation.
-- What a rule does is record the answer in advance so the promotion can be
-- made deliberately, in code, with the brief as the reminder -- not skip the
-- gate that asks.
CREATE TABLE IF NOT EXISTS standing_rules (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    said        TEXT NOT NULL,          -- his words, verbatim
    readback    TEXT NOT NULL,          -- what ATLAS said back to him
    via         TEXT NOT NULL,          -- phone | slack | dashboard
    -- Surfaced in the brief until he has seen it once, so a rule made at
    -- midnight is not quietly forgotten by morning.
    briefed_at  TIMESTAMPTZ,
    dropped_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS standing_rules_live
    ON standing_rules (at DESC) WHERE dropped_at IS NULL;

-- ---------------------------------------------------------------------------
-- The decision log (JUDGMENT.md §5). One row per proposal: the conditions
-- it was raised under, what ATLAS predicted, and what Miro answered.
-- Written only by kernel/approvals.py; read only by kernel/judgment/.
-- Starts filling in Phase 4, during watch-only, so by the time ATLAS can act
-- there is already a history of "what would Miro have said".
--
-- outcome is THREE classes. Expiry is never a denial: deny means the wrong
-- action, expiry means it was wrong to ask.
CREATE TABLE IF NOT EXISTS decision_log (
    id                  BIGSERIAL PRIMARY KEY,
    proposal_id         INTEGER UNIQUE REFERENCES proposals(id),
    at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    module              TEXT NOT NULL,
    action              TEXT NOT NULL,
    severity            SMALLINT NOT NULL,
    tier                SMALLINT NOT NULL,
    reversible          BOOLEAN NOT NULL,
    blast_radius        SMALLINT NOT NULL DEFAULT 1,
    cost_usd            REAL NOT NULL DEFAULT 0,
    hour                SMALLINT NOT NULL,
    weekday             SMALLINT NOT NULL,
    quiet_hours         BOOLEAN NOT NULL,
    times_seen          INTEGER NOT NULL,
    prior_approve_rate  REAL NOT NULL,
    days_since_similar  REAL NOT NULL,
    you_were_busy       BOOLEAN NOT NULL,
    vector              REAL[] NOT NULL,            -- encode.py output, WIDTH wide
    predicted           REAL,                       -- NULL = abstained
    predicted_by        TEXT,                       -- serving head, or 'abstain'
    reasons             JSONB NOT NULL DEFAULT '[]', -- explain.py, top three
    outcome             TEXT CHECK (outcome IN ('approved', 'denied', 'expired')),
    decided_at          TIMESTAMPTZ,
    decided_via         TEXT
);
CREATE INDEX IF NOT EXISTS decision_log_at ON decision_log (at);
CREATE INDEX IF NOT EXISTS decision_log_pair ON decision_log (module, action);

-- Every model ever trained, with its score, so rollback is a flag flip and
-- the handover is auditable. blob is a pickled sklearn Pipeline; envelope is
-- what abstain.py judges novelty against; mean is what explain.py probes with.
CREATE TABLE IF NOT EXISTS judgment_models (
    id          SERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    target      TEXT NOT NULL,                       -- 'approve' | 'answer'
    head        TEXT NOT NULL,
    width       SMALLINT NOT NULL,
    brier       REAL NOT NULL,
    n_train     INTEGER NOT NULL,
    n_test      INTEGER NOT NULL,
    scores      JSONB NOT NULL DEFAULT '{}',
    blob        BYTEA NOT NULL,
    envelope    JSONB NOT NULL,
    mean        REAL[] NOT NULL,
    serving     BOOLEAN NOT NULL DEFAULT FALSE,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS judgment_models_serving
    ON judgment_models (target, serving) WHERE serving;

-- The tournament log: who won, by how much, on how many rows.
CREATE TABLE IF NOT EXISTS judgment_tournaments (
    id          SERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    target      TEXT NOT NULL,
    scores      JSONB NOT NULL,
    winner      TEXT,
    incumbent   TEXT,
    changed     BOOLEAN NOT NULL,
    n_train     INTEGER NOT NULL,
    n_test      INTEGER NOT NULL,
    line        TEXT NOT NULL
);

-- A proposal the number said not to raise (p < 0.30, enough history). It is
-- still recorded, still listed in the brief, and never executed.
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS predicted REAL;

-- Phases 12-14 (2026-09-24)

-- Phase 12: agency
CREATE TABLE IF NOT EXISTS agency_spend (
    month   TEXT PRIMARY KEY,           -- 'YYYY-MM'
    places_requests INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agency_leads (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    place_id    TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    address     TEXT,
    phone       TEXT,
    website     TEXT,
    rating      REAL,
    reviews     INT,
    distance_km REAL,
    status      TEXT NOT NULL DEFAULT 'new',   -- new | audited | mocked | contacted | closed | dropped
    score       REAL,
    features    JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS agency_audits (
    id        BIGSERIAL PRIMARY KEY,
    lead_id   BIGINT NOT NULL REFERENCES agency_leads(id),
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    url       TEXT,
    findings  JSONB NOT NULL DEFAULT '[]',     -- [{check, severity, detail}]
    severity  SMALLINT NOT NULL DEFAULT 0,
    measured  JSONB NOT NULL DEFAULT '{}'      -- raw numbers with their source
);
CREATE INDEX IF NOT EXISTS agency_audits_lead ON agency_audits (lead_id, at DESC);
CREATE TABLE IF NOT EXISTS agency_mockups (
    id        BIGSERIAL PRIMARY KEY,
    lead_id   BIGINT NOT NULL REFERENCES agency_leads(id),
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    path      TEXT NOT NULL,                    -- data/agency/mockups/<id>.html
    screenshot TEXT,                            -- path or NULL
    layers    JSONB NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS agency_outreach (
    id          BIGSERIAL PRIMARY KEY,
    lead_id     BIGINT NOT NULL REFERENCES agency_leads(id),
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL,                  -- email | call_script | one_pager
    to_address  TEXT,
    subject     TEXT,
    body        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',  -- draft | sent | replied | bounced
    sent_at     TIMESTAMPTZ,
    followup_of BIGINT REFERENCES agency_outreach(id)
);
CREATE TABLE IF NOT EXISTS agency_suppression (
    address   TEXT PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agency_sites (
    id        BIGSERIAL PRIMARY KEY,
    lead_id   BIGINT REFERENCES agency_leads(id),
    slug      TEXT UNIQUE NOT NULL,
    domain    TEXT,
    path      TEXT NOT NULL,                    -- data/agency/sites/<slug>
    deployed_at TIMESTAMPTZ,
    previous_release TEXT                       -- rollback pointer
);

-- Phase 13: inbox follow-ups, research, vault/library, messaging, screening,
-- content, nightshift, purchasing
CREATE TABLE IF NOT EXISTS mail_followups (
    thread_id   TEXT PRIMARY KEY,
    sent_at     TIMESTAMPTZ NOT NULL,
    to_address  TEXT NOT NULL,
    subject     TEXT NOT NULL,
    asked       TEXT,                           -- the question he asked, first line
    nudged_at   TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS research_reports (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    question  TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'queued',   -- queued | done | failed | withdrawn
    body      TEXT,
    sources   JSONB NOT NULL DEFAULT '[]',      -- [{url, title, fetched_at}]
    report_id BIGINT                            -- reports.id once stored
);
CREATE TABLE IF NOT EXISTS vault_documents (
    id        BIGSERIAL PRIMARY KEY,
    path      TEXT UNIQUE NOT NULL,
    sha256    TEXT NOT NULL,
    title     TEXT NOT NULL,
    kind      TEXT NOT NULL,                    -- lease | insurance | contract | receipt | other
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    chunks    INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS library_entries (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,                    -- book | article | video | note
    title     TEXT NOT NULL,
    source    TEXT,
    notes     TEXT NOT NULL,
    memory_id BIGINT
);
CREATE TABLE IF NOT EXISTS messaging_inbound (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform  TEXT NOT NULL,                    -- whatsapp | telegram | slack
    chat_id   TEXT NOT NULL,
    sender    TEXT NOT NULL,
    text      TEXT NOT NULL,
    external_id TEXT UNIQUE,
    replied_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS messaging_inbound_open ON messaging_inbound (platform, at DESC)
    WHERE replied_at IS NULL;
CREATE TABLE IF NOT EXISTS screening_calls (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    call_sid  TEXT UNIQUE NOT NULL,
    caller    TEXT NOT NULL,
    transcript TEXT NOT NULL DEFAULT '',
    outcome   TEXT,                             -- message | booked | urgent | hung_up
    summary   TEXT,
    event_id  TEXT
);
CREATE TABLE IF NOT EXISTS content_drafts (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,                    -- linkedin_post | ebay_listing | flycheap_deal | short_script | youtube_video
    source    TEXT NOT NULL,                    -- what it was drafted from
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'draft'     -- draft | approved | published | dropped
);
CREATE TABLE IF NOT EXISTS nightshift_jobs (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    task      TEXT NOT NULL,
    repo      TEXT NOT NULL,
    branch    TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | stopped
    container TEXT,
    log_path  TEXT,
    preview_url TEXT,
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS purchasing_prepared (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,                    -- flight | cart
    request   TEXT NOT NULL,
    options   JSONB NOT NULL DEFAULT '[]',      -- [{title, price, currency, url, source, seen_at}]
    checkout_url TEXT,
    withdrawn BOOLEAN NOT NULL DEFAULT FALSE
);

-- Phase 14: daily review
CREATE TABLE IF NOT EXISTS reviews (
    id        BIGSERIAL PRIMARY KEY,
    day       DATE UNIQUE NOT NULL,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    went_well JSONB NOT NULL DEFAULT '[]',
    went_wrong JSONB NOT NULL DEFAULT '[]',
    decisions JSONB NOT NULL DEFAULT '[]',
    score     REAL,
    report_id BIGINT
);

-- Phase 16 (2026-09-24)
CREATE TABLE IF NOT EXISTS memory_reads (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    namespace TEXT NOT NULL,
    hits      INT NOT NULL DEFAULT 0,
    agent     TEXT
);
CREATE INDEX IF NOT EXISTS memory_reads_at ON memory_reads (at DESC);
CREATE TABLE IF NOT EXISTS browser_sessions (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    url       TEXT NOT NULL,
    title     TEXT,
    text_path TEXT,                             -- data/browser/<id>.txt
    shot_path TEXT,                             -- data/browser/<id>.png
    status    INT,
    reason    TEXT,
    agent     TEXT
);
CREATE TABLE IF NOT EXISTS messages_inbound (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform    TEXT NOT NULL,                  -- imessage | instagram
    thread_id   TEXT NOT NULL,
    sender      TEXT NOT NULL,
    text        TEXT NOT NULL,
    external_id TEXT UNIQUE,
    replied_at  TIMESTAMPTZ,
    draft       TEXT
);
CREATE INDEX IF NOT EXISTS messages_inbound_open ON messages_inbound (platform, at DESC)
    WHERE replied_at IS NULL;
-- The other participant's IGSID, resolved from the conversation: thread_id is a
-- conversation id, and the Graph send endpoint addresses a person.
ALTER TABLE messages_inbound ADD COLUMN IF NOT EXISTS recipient_id TEXT;
CREATE TABLE IF NOT EXISTS rhythm_hours (
    dow       SMALLINT NOT NULL,                -- 0 = Monday
    hour      SMALLINT NOT NULL,
    weeks     INT NOT NULL DEFAULT 0,           -- weeks with any activity in this slot
    samples   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (dow, hour)
);
CREATE TABLE IF NOT EXISTS rhythm_notes (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,                    -- early | late | absent | circumstance
    detail    TEXT NOT NULL,
    until     DATE,
    memory_id BIGINT
);
-- Phase 18 (2026-09-24): content_media, render_spend
CREATE TABLE IF NOT EXISTS content_media (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    draft_id  BIGINT,
    provider  TEXT NOT NULL,                    -- openart
    kind      TEXT NOT NULL,                    -- video | image
    path      TEXT,
    external_id TEXT,
    status    TEXT NOT NULL DEFAULT 'queued',   -- queued | rendering | done | failed
    credits   INT,
    ai_label  BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS render_spend (
    month     TEXT PRIMARY KEY,                 -- 'YYYY-MM'
    credits   INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS school_items (
    id        BIGSERIAL PRIMARY KEY,
    course    TEXT NOT NULL,
    title     TEXT NOT NULL,
    due_at    TIMESTAMPTZ,
    kind      TEXT NOT NULL,                    -- assignment | quiz | exam | announcement
    external_id TEXT UNIQUE,
    seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    done      BOOLEAN NOT NULL DEFAULT FALSE
);

-- Phase 17 (2026-09-24)
CREATE TABLE IF NOT EXISTS youtube_videos (
    id          BIGSERIAL PRIMARY KEY,
    channel     TEXT NOT NULL,                  -- flycheap | guardian
    video_id    TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    privacy     TEXT,                           -- private | unlisted | public
    views       BIGINT, likes BIGINT, comments BIGINT, watch_minutes REAL,
    measured_at TIMESTAMPTZ,
    experiment_id BIGINT
);
CREATE TABLE IF NOT EXISTS marketing_experiments (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    business    TEXT NOT NULL,                  -- flycheap | guardian | mirotech | agency
    platform    TEXT NOT NULL,                  -- youtube | instagram | tiktok | linkedin
    hypothesis  TEXT NOT NULL,                  -- "route-deal shorts beat explainer shorts on views/48h"
    variant     TEXT NOT NULL,                  -- the concrete post kind / hook / format
    draft_id    BIGINT,                         -- content_drafts.id
    posted_ref  TEXT,                           -- video id / media id once published
    posted_at   TIMESTAMPTZ,
    measure_at  TIMESTAMPTZ,                    -- when the 48h/7d read is due
    metric      TEXT,                           -- views_48h | reach_48h | ...
    baseline    REAL, observed REAL, measured_at TIMESTAMPTZ,
    outcome     TEXT,                           -- won | lost | flat | unmeasured
    lesson      TEXT
);
CREATE TABLE IF NOT EXISTS monetization_ideas (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    business    TEXT NOT NULL,
    idea        TEXT NOT NULL,
    mechanism   TEXT NOT NULL,                  -- affiliate | sponsorship | ads | product | service | partner_program
    evidence    JSONB NOT NULL DEFAULT '{}',    -- the measured facts it rests on, each with observed_at
    requires    TEXT,                           -- what Miro must do (sign up, enable, decide)
    est_note    TEXT,                           -- prose only; no invented revenue figures
    score       REAL,
    status      TEXT NOT NULL DEFAULT 'proposed' -- proposed | pursuing | done | dropped
);
-- Phase 18 (2026-09-24)
CREATE TABLE IF NOT EXISTS lessons (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,                    -- failed_action | declined | lost_experiment | review | told
    agent     TEXT,
    action    TEXT,
    params_key TEXT,                            -- sha256 of canonical params, for never-repeat
    lesson    TEXT NOT NULL,
    times     INT NOT NULL DEFAULT 1,
    last_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_at TIMESTAMPTZ,                     -- he said "it's different now"
    memory_id BIGINT
);
CREATE INDEX IF NOT EXISTS lessons_action ON lessons (action, params_key);

-- Task 30: the 48-hour reading a marketing experiment is actually scored on.
-- `views` is a lifetime counter refreshed every six hours, so a median of it
-- is not a median of "views in the first 48 hours" and comparing the two
-- would be a measurement of the wrong thing wearing the right name.
ALTER TABLE youtube_videos ADD COLUMN IF NOT EXISTS views_48h REAL;
ALTER TABLE youtube_videos ADD COLUMN IF NOT EXISTS views_48h_at TIMESTAMPTZ;

-- Voice notes (18.3): how a row's text came to be. {} for a typed message;
-- {"voice": true, "transcribed": bool, "spoken": bool, "forwarded": bool}
-- for a voice or audio note, whose text is a machine transcript.
ALTER TABLE messaging_inbound ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb;


-- Phase 19 (2026-09-24): school_courses, and school_items gains its sources
CREATE TABLE IF NOT EXISTS school_courses (
    code      TEXT PRIMARY KEY,                 -- 'PHY 120', or a department: 'MAT'
    title     TEXT,
    term      TEXT,                             -- '2026FA'
    work_kind TEXT,                             -- handwritten | webwork | files | lab_report
    notify    BOOLEAN DEFAULT TRUE
);
-- kind now also carries: assignment | quiz | exam | lab | news | grade | alert
ALTER TABLE school_items ADD COLUMN IF NOT EXISTS course_code TEXT;
ALTER TABLE school_items ADD COLUMN IF NOT EXISTS source TEXT;    -- ics | email | valence
ALTER TABLE school_items ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE school_items ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE school_items ADD COLUMN IF NOT EXISTS signalled_at TIMESTAMPTZ;   -- email kinds told
ALTER TABLE school_items ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}';  -- sender_unverified

-- Phase 20 (2026-09-24): store variants. A store build's ranking -- style id,
-- primary angle, and per variant its rubric score, what it failed and where
-- its file is. The primary is the slug's index.html; the alternates sit
-- beside it in .<slug>.variants/ and are never deployed.
ALTER TABLE agency_sites ADD COLUMN IF NOT EXISTS variants JSONB;

-- Phase 20 (2026-09-24): Perplexity over MCP — a source with a budget
CREATE TABLE IF NOT EXISTS perplexity_calls (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    tool      TEXT NOT NULL,                    -- perplexity_search | _ask | _reason | _research
    caller    TEXT NOT NULL,                    -- knowledge | research | marketing | agency | school | tool
    chars_in  INT NOT NULL DEFAULT 0,           -- the scrubbed question's length, never its text
    chars_out INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS perplexity_calls_at ON perplexity_calls (at);
-- agency lead reputation (perplexity_search), cached 30 days per lead
ALTER TABLE agency_leads ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}';
-- nightshift job kind: 'code' (a sandbox container) | 'research' (a filed report)
ALTER TABLE nightshift_jobs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'code';
-- RA-D1 (2026-09-29): the commit a finished code job's branch pointed at,
-- read from GitLab when check_jobs marks the job done. nightshift.merge
-- merges only this commit, so nothing pushed after the job finished can be
-- merged under the card Miro approved.
ALTER TABLE nightshift_jobs ADD COLUMN IF NOT EXISTS head_sha TEXT;

-- Phase 20 (2026-09-24): a rendered video reachable by a publish proposal.
-- OpenArt's own video_url and expiry (if it gives one) are kept for the
-- record; the media_token pair is ATLAS's own, unrelated URL -- what
-- GET /media/{token} (atlas/api/media.py) actually serves, generated by
-- content.render_video with secrets.token_urlsafe(32) and good for 24h,
-- because OpenArt's URL may not stay fetchable long enough for Instagram's
-- Graph API to publish from it, and serving OpenArt's own link publicly was
-- never this module's decision to make.
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS video_url TEXT;
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS video_url_expires_at TIMESTAMPTZ;
-- UNIQUE already creates its own index; a second CREATE INDEX on the same
-- column would just be a duplicate to maintain.
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS media_token TEXT UNIQUE;
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS media_token_expires_at TIMESTAMPTZ;
-- RA-E5: whether the render burned a non-empty set of captions (NULL = not
-- recorded, e.g. media made before this column). Burned pixels cannot be
-- read back, so the reel quality gate reads this record.
ALTER TABLE content_media ADD COLUMN IF NOT EXISTS captions BOOLEAN;

-- "I know. Stop telling me." One row per check Miro acknowledged from a
-- Slack alert. While it stands, the check neither pages nor raises a card.
-- It lapses after `until`, or as soon as the check's health differs from the
-- health he acknowledged (degraded -> failing is news), or when the check
-- reads OK again (a later failure is a new problem, not the one he knew).
CREATE TABLE IF NOT EXISTS acks (
    key     TEXT PRIMARY KEY,            -- module.check, e.g. vpsie.balance
    at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    until   TIMESTAMPTZ NOT NULL,
    health  TEXT NOT NULL,               -- the state he acknowledged
    via     TEXT NOT NULL
);

-- Task 55 (2026-09-24): Veo's seconds, against VEO_MONTHLY_SECONDS. One row
-- per clip Veo ACCEPTED (charged on acceptance, not on a successful
-- download: a clip that failed after Veo took it may still be billed, and a
-- spend cap must never under-count). content.render_backend reads the sum.
CREATE TABLE IF NOT EXISTS veo_usage (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    seconds   INT NOT NULL,
    model     TEXT NOT NULL,
    media_id  BIGINT                          -- content_media.id, when known
);
CREATE INDEX IF NOT EXISTS veo_usage_at ON veo_usage (at);

-- Miro, 2026-09-29: Higgsfield renders the calendar clips Veo's daily
-- allowance cannot, under HIGGSFIELD_MONTHLY_USD. Its own ledger, never
-- veo_usage (which Veo's allowance and seconds cap read). One positive row
-- reserved per clip BEFORE the request, a negative row when Higgsfield
-- cannot have charged (refused on submit, failed / nsfw / canceled).
CREATE TABLE IF NOT EXISTS higgsfield_usage (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    usd         NUMERIC(10, 4) NOT NULL,
    model       TEXT NOT NULL,
    job_id      TEXT,                          -- reel_calendar.job_id
    part        TEXT                           -- the clip's part key
);
CREATE INDEX IF NOT EXISTS higgsfield_usage_at ON higgsfield_usage (at);
-- Invariant 6: what was asked for, and whether `usd` is a price Higgsfield's
-- estimate stated (price_read) or a worst case booked because it named none.
-- No default: a row from before this column is NULL, "not known to be read",
-- and is counted with the unread ones -- never presented as a read price.
ALTER TABLE higgsfield_usage ADD COLUMN IF NOT EXISTS seconds INT;
ALTER TABLE higgsfield_usage ADD COLUMN IF NOT EXISTS resolution TEXT;
ALTER TABLE higgsfield_usage ADD COLUMN IF NOT EXISTS price_read BOOLEAN;

-- Task 55 (2026-09-24): the AI-stories account's format playbook. Miro's
-- seeds (source 'miro') and what the daily research found (source
-- 'perplexity'). `score` is a RANKING WEIGHT, never a measurement: 2 for a
-- format he chose, 1 per research mention in the trailing 30 days. Rows are
-- retired (retired_at), never deleted -- content.reel_research's rollback.
CREATE TABLE IF NOT EXISTS reel_formats (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    format      TEXT NOT NULL,
    evidence    TEXT NOT NULL,                -- why it is here, with its read time and sources
    source      TEXT NOT NULL,                -- miro | perplexity
    score       REAL NOT NULL,
    max_scenes  INT NOT NULL DEFAULT 4,       -- beats a reel in this format gets
    retired_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS reel_formats_at ON reel_formats (at);

-- Task 55 (2026-09-25): what each AI-stories reel did, so the playbook
-- learns. One reel_posts row per platform a content.publish_reel posted to
-- (format_id from the draft's reel_format:<id> source); one reel_metrics row
-- per daily read. A metric the platform did not give is NULL -- never 0.
CREATE TABLE IF NOT EXISTS reel_posts (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    media_id    BIGINT,                        -- content_media.id
    format_id   BIGINT,                        -- reel_formats.id, when known
    platform    TEXT NOT NULL,                 -- instagram | tiktok
    post_ref    TEXT NOT NULL                  -- IG media id | Postiz post id
);
CREATE TABLE IF NOT EXISTS reel_metrics (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when it was read
    platform    TEXT NOT NULL,
    post_ref    TEXT NOT NULL,
    format_id   BIGINT,
    views       REAL,
    likes       REAL,
    comments    REAL,
    shares      REAL,
    engagement  REAL                           -- (likes+comments+shares)/views, only when all four were read
);
CREATE INDEX IF NOT EXISTS reel_metrics_at ON reel_metrics (at);

-- 2026-09-27: the 30-day reels calendar Miro supplied
-- (atlas/modules/content/calendars/*.json). One row per job; the job itself
-- is read from the package file. status: draft | rendering | rendered |
-- carded | published | declined | blocked. The business package stays 'draft' until
-- Miro picks its account, language and start date.
CREATE TABLE IF NOT EXISTS reel_calendar (
    job_id      TEXT PRIMARY KEY,
    package     TEXT NOT NULL,                 -- stories | business
    day         INT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    draft_id    BIGINT,                        -- content_drafts.id
    media_id    BIGINT,                        -- content_media.id
    proposal_id BIGINT,                        -- proposals.id of its publish card
    note        TEXT,                          -- why it is waiting, in words
    failures    INT NOT NULL DEFAULT 0,        -- Veo refusals; blocked at 2
    card_day    DATE,                          -- the New York day its last card went up
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2026-09-27: the business line (atlas/api/reception.py). One row per call a
-- business made to it: who rang, the lead it matched, what was said, and
-- whether the caller asked (and was told) that it is an AI assistant.
CREATE TABLE IF NOT EXISTS reception_calls (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    call_sid    TEXT NOT NULL,
    from_number TEXT NOT NULL,
    lead_id     BIGINT,                        -- agency_leads.id, when matched
    transcript  JSONB NOT NULL DEFAULT '[]',   -- [{who, text}]
    disclosed   BOOLEAN NOT NULL DEFAULT FALSE
);

-- 2026-09-27: the series engine (atlas/modules/content/series.py). A series
-- is planned whole (3-8 parts with a real ending) and made part by part;
-- a part is drafted only after the one before it posted.
CREATE TABLE IF NOT EXISTS reel_series (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    title       TEXT NOT NULL,
    format      TEXT NOT NULL,
    characters  TEXT NOT NULL,                 -- the fixed character sheet
    outline     JSONB NOT NULL DEFAULT '[]',   -- one synopsis per part
    total_parts INT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',-- queued | active | done | dropped
    position    INT NOT NULL DEFAULT 0,        -- queue order
    rules       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reel_series_parts (
    series_id   BIGINT NOT NULL,               -- reel_series.id
    part_no     INT NOT NULL,
    draft_id    BIGINT,                        -- content_drafts.id
    media_id    BIGINT,                        -- content_media.id
    proposal_id BIGINT,                        -- its card, when it was held
    status      TEXT NOT NULL DEFAULT 'drafted', -- drafted | rendered | carded | posted | declined
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, part_no)
);
ALTER TABLE reel_series ADD COLUMN IF NOT EXISTS scripts JSONB;  -- a series written in full, part by part
ALTER TABLE reel_calendar ADD COLUMN IF NOT EXISTS redos INT NOT NULL DEFAULT 0;      -- remade after a failed check
ALTER TABLE reel_series_parts ADD COLUMN IF NOT EXISTS redos INT NOT NULL DEFAULT 0;  -- rendered again after a failed check

-- Work ATLAS hands to the coding agent on Miro's Mac (CLAUDE_CODE_AGENT.md
-- §9 option b). ATLAS writes the brief and never runs it: a row is `draft`
-- until Miro approves its card (`delegate.claude_code`), `queued` until the
-- scheduled agent claims it, and expires unclaimed -- silence is never
-- consent. The report comes back as untrusted text.
CREATE TABLE IF NOT EXISTS handoffs (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    request     TEXT NOT NULL,                 -- his words, verbatim
    brief       TEXT NOT NULL,                 -- handoff.delegate(...).render()
    target      TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT 'decisions',
    channel     TEXT NOT NULL DEFAULT '',      -- where he asked (Slack channel id)
    thread      TEXT NOT NULL DEFAULT '',      -- the thread the report goes back to
    status      TEXT NOT NULL DEFAULT 'draft', -- draft | queued | claimed | done | failed | needs_miro | expired | cancelled
    queued_at   TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    claimed_at  TIMESTAMPTZ,
    claimed_by  TEXT,
    reported_at TIMESTAMPTZ,
    report      TEXT
);
CREATE INDEX IF NOT EXISTS handoffs_status ON handoffs (status, id);

-- The kill switch (kernel/pause.py): a pause stops new dispatch through
-- executor.execute for everything ('all') or one module, until Miro
-- resumes it. Rows are never deleted; resuming stamps resumed_at.
CREATE TABLE IF NOT EXISTS pauses (
    id          BIGSERIAL PRIMARY KEY,
    scope       TEXT NOT NULL,
    paused_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    paused_by   TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    resumed_at  TIMESTAMPTZ,
    resumed_by  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pauses_one_active
    ON pauses (scope) WHERE resumed_at IS NULL;

-- One run per scheduled window (kernel/occurrence.py): a daily job claims
-- its window here before it runs, so a restart inside the window never runs
-- it twice (37 briefs on 2026-09-22, 10 reviews on 2026-09-27).
CREATE TABLE IF NOT EXISTS schedule_runs (
    job         TEXT NOT NULL,
    occurrence  TEXT NOT NULL,
    status      TEXT NOT NULL,                 -- running | done | failed | skipped
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    attempts    INT NOT NULL DEFAULT 1,
    PRIMARY KEY (job, occurrence)
);
-- RA-F2 (kernel/missed.py): when the window was due, and why a window the
-- missed-run policy did not run was 'skipped' (or how a run failed).
ALTER TABLE schedule_runs ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;
ALTER TABLE schedule_runs ADD COLUMN IF NOT EXISTS detail TEXT;

-- The agent on Miro's Mac (modules/mac, api/mac_agent.py). A job expires 60 s
-- after it is queued, checked inside the claim; 'unknown' is a job the Mac
-- claimed and never answered.
CREATE TABLE IF NOT EXISTS mac_jobs (
    id          BIGSERIAL PRIMARY KEY,
    op          TEXT NOT NULL,
    params      JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued|claimed|done|expired|unknown
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    claimed_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    ok          BOOLEAN,
    detail      TEXT,
    token       TEXT
);
CREATE TABLE IF NOT EXISTS mac_agent (
    host        TEXT PRIMARY KEY,
    last_seen   TIMESTAMPTZ NOT NULL
);

-- The shop (modules/shop): one product per reel type, sold through a Stripe
-- payment link Miro approved. file_path is what /deliver hands over after a
-- paid checkout, under data/products/.
CREATE TABLE IF NOT EXISTS shop_products (
    key              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    reel_type        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'draft',   -- draft | live | paused
    price_cents      INT,
    stripe_account   TEXT,
    payment_link_id  TEXT,
    payment_link_url TEXT,
    file_path        TEXT
);
INSERT INTO shop_products (key, name, reel_type, status, payment_link_url, file_path) VALUES
    ('business_guide', '30 Business Lessons', 'business', 'draft', NULL,
     'data/products/30-business-lessons.pdf'),
    ('vault_kit', 'Claude + Obsidian Vault Kit', 'tech', 'draft', NULL,
     'data/products/claude-obsidian-vault-kit.zip'),
    ('flycheap_pro', 'FlyCheap Pro', 'travel', 'live',
     'https://flycheap.live/pricing', NULL)
ON CONFLICT (key) DO NOTHING;

-- Money per reel (kernel/reel_money.py): the Improver's reel-type mix, in
-- reels a week per type, from week_start (a Monday) until the next row set.
-- REAL, not INT: a +-20% step of a small whole number would never move.
-- content.calendar reads the stories:business ratio; no row keeps today's
-- two stories to each business lesson. DATA only -- never tiers or spending.
CREATE TABLE IF NOT EXISTS reel_mix (
    week_start  DATE NOT NULL,
    reel_type   TEXT NOT NULL,
    per_week    REAL NOT NULL,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, reel_type)
);
-- One row per weekly decision, changed or not, with the Analyst's table it
-- was based on. UNIQUE week_start: a second run in a week changes nothing.
CREATE TABLE IF NOT EXISTS reel_mix_log (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    week_start  DATE NOT NULL UNIQUE,
    changed     BOOLEAN NOT NULL,
    reason      TEXT NOT NULL,
    before      JSONB NOT NULL,
    after       JSONB NOT NULL,
    based_on    JSONB NOT NULL
);

-- The markets module's PAPER basket: $100 split equally on its first run.
-- Paper only: ATLAS never trades (money out is Tier 3).
CREATE TABLE IF NOT EXISTS markets_paper (
    ticker      TEXT PRIMARY KEY,
    shares      DOUBLE PRECISION NOT NULL,
    start_price DOUBLE PRECISION NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Miro's own TradingView alerts, as TradingView sent them to
-- POST /hooks/tradingview (atlas/api/tradingview.py). Read only: nothing
-- here is ever traded on (money out is Tier 3). `name` and `message` are
-- untrusted text and reach a prompt only through wrap_untrusted();
-- `action_hint` is TradingView's word, stored, never acted on. The secret
-- and the raw body are never stored. `dedup_key` is sha256 over (ticker,
-- time, name, price): a resend inside ten minutes is not stored twice.
-- `notified_at` is set only when the markets.alert pipeline has consumed
-- the row (atlas/modules/markets/tradingview.py).
CREATE TABLE IF NOT EXISTS tradingview_alerts (
    id          BIGSERIAL PRIMARY KEY,
    dedup_key   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    exchange    TEXT,
    bar_interval TEXT,                                     -- TradingView's {{interval}}
    price       NUMERIC(24,8),
    alert_time  TIMESTAMPTZ,                               -- TradingView's {{time}}
    name        TEXT,
    action_hint TEXT CHECK (action_hint IS NULL OR action_hint IN
                            ('buy','sell','long','short','exit','alert')),
    message     TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tradingview_alerts_dedup
    ON tradingview_alerts (dedup_key, received_at);
CREATE INDEX IF NOT EXISTS tradingview_alerts_received
    ON tradingview_alerts (received_at);
CREATE INDEX IF NOT EXISTS tradingview_alerts_unnotified
    ON tradingview_alerts (received_at) WHERE notified_at IS NULL;

-- The Claude playlist (paper), markets/playlist.py: one row per weekly
-- proposal. status: carded | unchanged | invalid | no_model | no_prices |
-- refused | applied | rolled_back. weights/before are percent by ticker;
-- NULL weights means nothing valid was proposed (UNKNOWN, never a hold).
-- card_id is the proposals.id of its approval card. Prices only, no secrets.
CREATE TABLE IF NOT EXISTS markets_playlist_proposals (
    id          BIGSERIAL PRIMARY KEY,
    week        DATE NOT NULL UNIQUE,              -- the proposal day, New York
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL,
    why         TEXT NOT NULL DEFAULT '',
    model       TEXT,                              -- provider:model that answered
    weights     JSONB,
    before      JSONB,
    reasons     JSONB,
    stats       JSONB NOT NULL DEFAULT '{}',       -- the figures it was given, with read time
    card_id     BIGINT
);
-- Its paper holdings, append-only: the latest row is what it holds. kind:
-- start ($100 equal split) | rebalance (an approved proposal) | rollback.
-- prices/price_days/prices_read_at: the closes it was valued and bought at.
CREATE TABLE IF NOT EXISTS markets_playlist_holdings (
    id              BIGSERIAL PRIMARY KEY,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind            TEXT NOT NULL,
    proposal_id     BIGINT,                        -- markets_playlist_proposals.id
    weights         JSONB NOT NULL,
    shares          JSONB NOT NULL,
    prices          JSONB NOT NULL,
    price_days      JSONB NOT NULL,
    prices_read_at  TIMESTAMPTZ NOT NULL,
    value           DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS markets_playlist_rebalance_once
    ON markets_playlist_holdings (proposal_id) WHERE kind = 'rebalance';

-- Trade idea cards, markets/ideas.py: PAPER only, long only. One row per
-- idea raised. status: carded | approved (waits for the next close) | open |
-- closed | declined | expired (their hypothetical result tracked in the same
-- entry/exit columns) | cancelled | refused. Prices with their read time.
CREATE TABLE IF NOT EXISTS markets_ideas (
    id           BIGSERIAL PRIMARY KEY,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    day          DATE NOT NULL,                     -- the scan's New York date
    ticker       TEXT NOT NULL,
    rule         TEXT NOT NULL,                     -- breakout | pullback
    numbers      JSONB NOT NULL,                    -- the rule's figures
    note         TEXT NOT NULL DEFAULT '',
    ranked_by    TEXT NOT NULL DEFAULT '',
    ref_close    DOUBLE PRECISION NOT NULL,
    ref_day      DATE NOT NULL,
    read_at      TIMESTAMPTZ NOT NULL,
    atr          DOUBLE PRECISION NOT NULL,
    stop         DOUBLE PRECISION NOT NULL,
    target       DOUBLE PRECISION NOT NULL,
    shares       INTEGER NOT NULL,
    size_pct     DOUBLE PRECISION NOT NULL,
    book         DOUBLE PRECISION NOT NULL,         -- the book's value when sized
    status       TEXT NOT NULL,
    card_id      BIGINT,                            -- proposals.id
    decided_at   TIMESTAMPTZ,
    entry_day    DATE,
    entry_price  DOUBLE PRECISION,
    exit_day     DATE,
    exit_price   DOUBLE PRECISION,
    exit_reason  TEXT,                              -- stop | target | time
    r_multiple   DOUBLE PRECISION,
    pnl          DOUBLE PRECISION,
    UNIQUE (day, ticker)
);
-- One row per weekday scan: what it did and why (paused, full, none, raised).
CREATE TABLE IF NOT EXISTS markets_idea_scans (
    day         DATE PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    finnhub     BOOLEAN NOT NULL,                   -- was the key set (never the key)
    candidates  JSONB NOT NULL DEFAULT '[]',
    raised      INTEGER NOT NULL DEFAULT 0
);
-- The idea book's value each scan day (start $10,000), and SPY's close.
CREATE TABLE IF NOT EXISTS markets_idea_marks (
    day      DATE PRIMARY KEY,
    equity   DOUBLE PRECISION NOT NULL,
    spy      DOUBLE PRECISION,
    read_at  TIMESTAMPTZ NOT NULL
);

-- The durable core (RA-B1/B3/B4, docs/durable-core.md). Additive only: the
-- proposal claim, the audit chain and the reapers are unchanged, and nothing
-- here is written unless ATLAS_DURABLE_CORE=on.
--
-- One workflow per (type, correlation id): the inbound dedup key is the
-- UNIQUE below, so a redelivered event lands on the row it already made.
-- The record holds references and hashes, never raw payloads: input_refs
-- names where the input lives (a Slack event id, a proposal, a finding),
-- input_hash is sha256 of its canonical JSON. States and the permitted
-- transitions live in kernel/durable.py; an illegal one raises before SQL.
-- fencing_token only ever grows (a row is never deleted), so a worker whose
-- lease lapsed cannot commit over the one that took the row after it.
CREATE TABLE IF NOT EXISTS workflows (
    id                    BIGSERIAL PRIMARY KEY,
    workflow_type         TEXT NOT NULL,
    workflow_version      INT NOT NULL DEFAULT 1,
    correlation_id        TEXT NOT NULL,
    tenant                TEXT NOT NULL DEFAULT 'miro',
    originating_actor     TEXT NOT NULL DEFAULT '',
    originating_channel   TEXT NOT NULL DEFAULT '',
    thread_id             TEXT NOT NULL DEFAULT '',
    desired_outcome       TEXT NOT NULL DEFAULT '',
    input_refs            JSONB NOT NULL DEFAULT '[]',
    input_hash            TEXT NOT NULL DEFAULT '',
    data_classification   TEXT NOT NULL DEFAULT 'internal',
    state                 TEXT NOT NULL DEFAULT 'RECEIVED',
    current_step          TEXT NOT NULL DEFAULT '',
    policy_version        TEXT NOT NULL DEFAULT '',
    proposal_id           BIGINT,
    approved_payload_hash TEXT,
    approval_expiry       TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline              TIMESTAMPTZ,
    next_attempt_at       TIMESTAMPTZ,
    attempt_count         INT NOT NULL DEFAULT 0,
    retry_budget          INT NOT NULL DEFAULT 3,
    cancellation_state    TEXT NOT NULL DEFAULT '',
    lease_owner           TEXT,
    lease_expiry          TIMESTAMPTZ,
    fencing_token         BIGINT NOT NULL DEFAULT 0,
    provider_request_ids  JSONB NOT NULL DEFAULT '[]',
    provider_resource_ids JSONB NOT NULL DEFAULT '[]',
    reconciliation_state  TEXT NOT NULL DEFAULT '',
    evidence_refs         JSONB NOT NULL DEFAULT '[]',
    result_summary        TEXT NOT NULL DEFAULT '',
    remaining_unknowns    JSONB NOT NULL DEFAULT '[]',
    UNIQUE (workflow_type, correlation_id)
);
CREATE INDEX IF NOT EXISTS workflows_state ON workflows (state, next_attempt_at);

-- Outbound intents (kernel/outbox.py). The dispatcher claims a row and hands
-- it to executor.execute; it never sends anything itself. idempotency_key is
-- sha256(action, version, destination, logical occurrence) -- never message
-- text -- so one intent is recorded once however often it is enqueued.
-- 'delivered' is written only when the run returned evidence; a row whose
-- lease lapsed mid-dispatch goes to 'reconciling', never back to 'pending'.
CREATE TABLE IF NOT EXISTS outbox (
    id              BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    workflow_id     BIGINT,
    kind            TEXT NOT NULL DEFAULT 'effect',   -- effect | notice
    action          TEXT NOT NULL,
    action_version  TEXT NOT NULL DEFAULT '1',
    destination     TEXT NOT NULL,
    occurrence      TEXT NOT NULL,
    params          JSONB NOT NULL DEFAULT '{}',
    params_hash     TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    approved_by     TEXT,
    incident_id     BIGINT,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- pending | claimed | dispatching | delivered | unverified | reconciling
    -- | needs_human | failed | shadowed (terminal; kernel/shadow.py)
    -- | superseded (terminal; the action changed since it was queued)
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 5,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner     TEXT,
    lease_expiry    TIMESTAMPTZ,
    fencing_token   BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at      TIMESTAMPTZ,
    dispatched_at   TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    detail          TEXT,
    evidence        JSONB
);
CREATE INDEX IF NOT EXISTS outbox_status ON outbox (status, next_attempt_at, id);
-- The same record for a queued row (S08): the catalog version of the action
-- and the params hash when it was queued. `action_version` above is the
-- caller's label inside the idempotency key and is left alone, so a version
-- bump never mints a second key for the same occurrence. A mismatch at
-- dispatch ends the row 'superseded' (terminal) and raises a fresh card.
-- NULL on rows queued before this existed: no check, exactly as before.
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS catalog_version TEXT;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS approved_payload_hash TEXT;

-- The restore fence (R12, kernel/restore.py). The real restore procedure
-- (docs/restore-drill.md) inserts one row right after pg_restore, with the
-- app stopped. Until the kernel has fenced it, the outbox claims nothing and
-- no card is decided; fencing moves restored pending/claimed/dispatching
-- outbox rows to 'reconciling' and restored pending cards to 'held'.
CREATE TABLE IF NOT EXISTS restores (
    id          BIGSERIAL PRIMARY KEY,
    restored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dump        TEXT NOT NULL DEFAULT '',
    restored_by TEXT NOT NULL DEFAULT '',
    fenced_at   TIMESTAMPTZ,
    outbox_held INT,
    cards_held  INT
);

-- Named leases for recurring jobs (kernel/leases.py). A row is never
-- deleted: releasing stamps expires_at, so the fencing token keeps growing
-- and a holder of an old token can never match again.
CREATE TABLE IF NOT EXISTS leases (
    name          TEXT PRIMARY KEY,
    owner         TEXT,
    fencing_token BIGINT NOT NULL DEFAULT 0,
    acquired_at   TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ
);

-- Life agents (atlas/modules/mail/travel.py, money.py). Personal data read
-- from his own mail by the local model; nothing here is ever sent anywhere.
-- Which emails an agent has already read, so the model reads each once.
CREATE TABLE IF NOT EXISTS mail_extractions (
    agent       TEXT NOT NULL,                  -- travel | money | alinea
    message_id  TEXT NOT NULL,                  -- gmail:<id> | imap:<acct>:<Message-ID>
    account     TEXT NOT NULL,
    outcome     TEXT NOT NULL,                  -- found | none | unparsed
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent, message_id)
);
-- One row per booking found in a confirmation email. Every field the email
-- did not state is NULL -- shown as unknown, never filled in.
CREATE TABLE IF NOT EXISTS travel_bookings (
    id                  BIGSERIAL PRIMARY KEY,
    booking_key         TEXT NOT NULL UNIQUE,
    message_id          TEXT NOT NULL,
    account             TEXT NOT NULL,
    subject             TEXT,
    kind                TEXT NOT NULL,          -- flight | hotel | transfer
    confirmation        TEXT,
    flight_number       TEXT,
    origin              TEXT,
    destination         TEXT,
    origin_country      TEXT,
    destination_country TEXT,
    start_date          DATE,
    start_time          TEXT,                   -- HH:MM, local to the place
    end_date            DATE,
    end_time            TEXT,
    location            TEXT,
    checkin_hours       INT,                    -- only when the email states it
    at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS travel_bookings_start ON travel_bookings (start_date);
-- One row per receipt or renewal email the money-leak finder read. An
-- amount not written in the email is NULL, shown as "amount unknown".
CREATE TABLE IF NOT EXISTS money_charges (
    id            BIGSERIAL PRIMARY KEY,
    message_id    TEXT NOT NULL UNIQUE,
    account       TEXT NOT NULL,
    sender_domain TEXT,
    merchant      TEXT NOT NULL,
    merchant_key  TEXT NOT NULL,
    kind          TEXT NOT NULL,                -- receipt | renewal_notice | trial_ending
    amount        NUMERIC(14,2),
    currency      TEXT,
    amount_text   TEXT,                         -- exactly as written in the email
    plan_term     TEXT,                         -- only when the email states it
    category      TEXT NOT NULL DEFAULT 'other',
    seen_on       DATE,                         -- the email's own date
    subject       TEXT,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS money_charges_seen ON money_charges (seen_on);
-- One row per event in an Alinea Invest email (Miro's auto-investing app;
-- it trades on its own, ATLAS only reads its emails). Every figure is the one written in
-- the email; one the email does not state is NULL, shown as unknown -- never
-- 0, never estimated (invariant 6). One row per event: an email listing
-- several trades keeps each, the first under its message id and the rest
-- under `<message id>#<n>`; mail_extractions holds the email itself.
CREATE TABLE IF NOT EXISTS alinea_events (
    id          BIGSERIAL PRIMARY KEY,
    message_id  TEXT NOT NULL UNIQUE,
    account     TEXT NOT NULL,                  -- the mailbox it arrived in
    kind        TEXT NOT NULL,                  -- deposit | trade | statement | dividend | withdrawal
    ticker      TEXT,
    side        TEXT,                           -- buy | sell, trades only
    quantity    NUMERIC(20,8),                  -- fractional shares
    price       NUMERIC(20,6),
    amount      NUMERIC(14,2),
    currency    TEXT,
    amount_text TEXT,                           -- the amount exactly as written
    balance     NUMERIC(14,2),                  -- as a statement states it
    realized    NUMERIC(14,2),                  -- realised gain/loss, only when stated
    seen_on     DATE,                           -- the email's own date
    subject     TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alinea_events_seen ON alinea_events (seen_on);
-- A fee the email states (a subscription, a trade fee); NULL when none is.
ALTER TABLE alinea_events ADD COLUMN IF NOT EXISTS fee NUMERIC(14,2);
-- TRUE when the mailbox's own authentication result shows dkim=pass for the
-- sender's domain; NULL when the mailbox exposes none (IMAP): counted, and
-- shown as unverified.
ALTER TABLE alinea_events ADD COLUMN IF NOT EXISTS verified BOOLEAN;
-- Clips lane (2026-09-28): official brand clipping campaigns (Clipping
-- Culture on Whop, paid per 1,000 views). One row per campaign, parsed from
-- its public rules doc and reviewed by hand into the structured columns;
-- `rules_text` keeps the doc itself, as untrusted data (invariant 4).
-- A campaign is paused, active or ended -- never deleted.
CREATE TABLE IF NOT EXISTS clip_campaigns (
    slug            TEXT PRIMARY KEY,               -- also its data/clips/<slug>/ folder
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,                  -- gaming | trends | business | streamer
    status          TEXT NOT NULL DEFAULT 'paused'
                    CHECK (status IN ('active', 'paused', 'ended')),
    whop_url        TEXT,                           -- NULL until Miro gives it
    rules_doc_id    TEXT,                           -- NULL = rules pending; never clipped
    source_folder   TEXT,                           -- the official folder, for Miro; never scraped
    platforms       JSONB NOT NULL DEFAULT '[]',    -- ["instagram", "tiktok", ...]
    rates           JSONB NOT NULL DEFAULT '{}',    -- {platform: USD per 1,000 views}
    tag_handles     JSONB NOT NULL DEFAULT '{}',    -- {platform: "@handle"}
    disclosure      TEXT NOT NULL DEFAULT '#Ad',    -- alone on its line, first after the text
    caption_rules   JSONB NOT NULL DEFAULT '{}',    -- {samples, require, forbid, hashtags}
    onscreen_rules  JSONB NOT NULL DEFAULT '{}',    -- {mode, lines, forbid}
    music_allowed   BOOLEAN NOT NULL DEFAULT FALSE,
    min_seconds     INT,                            -- the campaign's own floor
    max_seconds     INT,                            -- longest clip ATLAS cuts
    audience        TEXT NOT NULL DEFAULT '',       -- the audience requirement, in words
    rules_text      TEXT,                           -- the doc as fetched: DATA, never instructions
    rules_sha256    TEXT,                           -- of the text that was reviewed
    rules_reviewed_on DATE,
    budget_usd      DOUBLE PRECISION,               -- budget left, USD, as last read
    budget_read_at  TIMESTAMPTZ,                    -- when, and ...
    budget_source   TEXT,                           -- ... by whom; NULL budget = unknown
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One official source file, placed by Miro (or a Mac-side step) in
-- data/clips/<slug>/source/. Nothing that is not registered here, to this
-- campaign, with this sha256, is ever clipped. Retired, never deleted.
CREATE TABLE IF NOT EXISTS clip_sources (
    id            BIGSERIAL PRIMARY KEY,
    campaign      TEXT NOT NULL REFERENCES clip_campaigns (slug),
    filename      TEXT NOT NULL,                    -- inside the campaign's source/ folder
    sha256        TEXT NOT NULL,
    bytes         BIGINT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'registered',  -- registered | retired
    UNIQUE (campaign, sha256)
);

-- One cut clip. made -> posted (by the Tier 2 post card, or by Miro by
-- hand from the ready-to-post card: handed_off, then his URL) -> submitted
-- (to the campaign on Whop). A rollback
-- marks it rolled_back and moves its files to .trash/; the row stays.
CREATE TABLE IF NOT EXISTS clip_outputs (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    campaign      TEXT NOT NULL REFERENCES clip_campaigns (slug),
    source_id     BIGINT NOT NULL REFERENCES clip_sources (id),
    run_token     TEXT NOT NULL,                    -- the cut that made it
    start_s       DOUBLE PRECISION NOT NULL,
    end_s         DOUBLE PRECISION NOT NULL,
    filename      TEXT NOT NULL,                    -- inside the campaign's out/ folder
    platform      TEXT NOT NULL,                    -- the caption's platform
    caption       TEXT NOT NULL,
    onscreen      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'made',
    -- made | handed_off | posted | submitted | rolled_back | withdrawn
    -- (withdrawn: a YouTube Short set back to private by its rollback;
    -- never uploaded again)
    post_url      TEXT,
    post_ref      TEXT,                             -- the platform's media id, when ATLAS posted it
    posted_at     TIMESTAMPTZ,
    submitted_at  TIMESTAMPTZ,
    -- A 24 h public link for the platform to fetch the file from, minted
    -- only when a post card is approved ("clip." + a random token; served
    -- by GET /media/{token} while the clip is unposted).
    media_token   TEXT UNIQUE,
    media_token_expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS clip_outputs_campaign ON clip_outputs (campaign, status);
-- Clips, 2026-09-29. Added by ALTER so deploy's init-db upgrades the live
-- tables. permission_source: who allowed ATLAS to clip this footage
-- ('whop:<program or url>' or 'written:<who, date>'); NULL = never
-- activated, registered or cut. max_accounts: how many accounts ON EACH
-- PLATFORM may post this campaign's clips (its rules doc's number; 1 when
-- the doc names none). preset: how a clip fills 9:16 -- blur (the whole
-- frame over a blurred copy), crop (centre crop) or split (this source
-- over a second registered source of the same campaign); presets_allowed
-- is the campaign's rules' own restriction of those.
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS permission_source TEXT;
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS max_accounts INT NOT NULL DEFAULT 1
    CHECK (max_accounts >= 1);
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS preset TEXT NOT NULL DEFAULT 'blur'
    CHECK (preset IN ('blur', 'crop', 'split'));
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS presets_allowed JSONB NOT NULL
    DEFAULT '["blur", "crop", "split"]';
-- The account a clip went to on its platform ('instagram:clips',
-- 'youtube:clips'), set when ATLAS posts it or hands it to Miro to post:
-- what the max_accounts guard counts. One row per clip per platform, so
-- a clip posts at most once on each.
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS post_account TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS clip_outputs_once_per_platform
    ON clip_outputs (campaign, filename, platform);
-- One external effect per run (kernel/idempotency.py, RA-B7). A send to a
-- provider with no idempotency key of its own (Instagram / WhatsApp DMs,
-- Matrix past its txn window) is claimed here BEFORE it is attempted, so a
-- retried run finds the row and does not send twice. 'sending' that never
-- became 'sent' is an unknown outcome: it is never re-claimed on a timer.
CREATE TABLE IF NOT EXISTS effect_claims (
    action      TEXT NOT NULL,
    key         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'sending',   -- sending | sent
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at  TIMESTAMPTZ,
    detail      TEXT,
    PRIMARY KEY (action, key)
);
-- Clips, 2026-09-28 (TikTok leg). May ONE clip go up on more than one
-- account? Read from the reviewed rules doc by hand; FALSE unless it says
-- so. Both live docs forbid it ("do not copy the same video across
-- multiple accounts"), so each of their clips is cut for the one platform
-- that pays the most. The TikTok account is @atlasclips12 ('tiktok:clips').
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS cross_post_allowed BOOLEAN
    NOT NULL DEFAULT FALSE;
-- Clips, 2026-09-28 (unattended intake, kernel.clip_intake). On each
-- source: when the intake last tried to cut it, what the cut said, and how
-- many real cut runs failed (at 3 it is left alone and named by
-- clips.status). A dry-run refusal only waits six hours; it never counts.
ALTER TABLE clip_sources ADD COLUMN IF NOT EXISTS cut_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE clip_sources ADD COLUMN IF NOT EXISTS last_cut_at TIMESTAMPTZ;
ALTER TABLE clip_sources ADD COLUMN IF NOT EXISTS last_cut_detail TEXT;
-- The intake's last outcome per campaign, replaced each tick: what
-- clips.status reads. ok = FALSE (a folder that could not be read, a
-- failed registration or cut, watch-only) is UNKNOWN there, never OK.
CREATE TABLE IF NOT EXISTS clip_intake (
    campaign    TEXT PRIMARY KEY REFERENCES clip_campaigns (slug),
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ok          BOOLEAN NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);

-- The football (soccer) reels lane, Miro 2026-09-29: every number on screen
-- comes from API-Football (v3, api-sports.io), read by the read-only
-- collector in atlas/modules/football. Each read is kept with the time it
-- was read (invariant 6) and served from here while fresh, so the free
-- plan's 100 requests a day are not spent twice on one question.
CREATE TABLE IF NOT EXISTS football_api_reads (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when it was read
    endpoint    TEXT NOT NULL,                         -- e.g. players/topscorers
    params      JSONB NOT NULL,
    status      INT NOT NULL,                          -- HTTP status
    ok          BOOLEAN NOT NULL,                      -- 200 with no API errors
    body        JSONB
);
CREATE INDEX IF NOT EXISTS football_api_reads_lookup
    ON football_api_reads (endpoint, at);
-- Requests spent per API day (00:00 UTC), reserved BEFORE each request and
-- reconciled from the x-ratelimit-requests-* headers and /status.
CREATE TABLE IF NOT EXISTS football_api_quota (
    day         DATE PRIMARY KEY,
    used        INT NOT NULL DEFAULT 0,
    limit_day   INT
);
-- The fetched facts a football draft was written from, with the templates
-- the model wrote ({f1} for a fact): the render re-resolves and verifies
-- every number on screen against these before a single Veo clip is asked.
CREATE TABLE IF NOT EXISTS football_draft_facts (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    draft_id    BIGINT NOT NULL,                       -- content_drafts.id
    topic       TEXT NOT NULL,
    facts       JSONB NOT NULL,                        -- [{id, label, value, read_at, source}]
    beats       JSONB NOT NULL,                        -- [{text, shot}] as the model wrote them
    caption     TEXT NOT NULL                          -- the caption template
);
CREATE INDEX IF NOT EXISTS football_draft_facts_draft ON football_draft_facts (draft_id);
-- Clips, 2026-09-29 (Whop through the Mac agent). post_url_source: where a
-- clip's post_url came from -- 'instagram' / 'youtube' / 'tiktok' (the
-- platform's own publish response) or 'record_link' (Miro's word); NULL =
-- not recorded, and such a row is never offered to the agent.
-- submitted_by: who recorded it submitted -- 'mac-agent' (POST
-- /clips/whop/{id}/submitted) or 'miro' (his clips.submit_to_whop card).
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS post_url_source TEXT;
-- Clips, 2026-10-01 (Irubyana: "include brand logo", "twitch banner in the
-- background"). brand_assets on a campaign: which of logo | banner its rules
-- require on every clip, composited by the cut from data/clips/<slug>/brand/
-- (files Miro placed; never fetched); NULL = never set, so the seed's apply.
-- brand_assets on a clip: the ones it was cut with -- a clip cut without
-- the campaign's is never posted.
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS brand_assets JSONB;
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS brand_assets JSONB NOT NULL
    DEFAULT '[]';
-- Clips, 2026-10-01 (Irubyana on Whop: "submit a draft for review; drafts
-- need brand approval before you post"). approval_before_post: the cut
-- lands at draft_pending, the Mac agent uploads the draft on Whop
-- (draft_submitted), and only a draft_approved clip posts; a rejected one
-- is withdrawn with the brand's note. max_file_mb: Whop's draft upload
-- refuses more, so the cut's encode is sized to land under it.
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS approval_before_post BOOLEAN
    NOT NULL DEFAULT FALSE;
ALTER TABLE clip_campaigns ADD COLUMN IF NOT EXISTS max_file_mb INT NOT NULL
    DEFAULT 256;
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS draft_submitted_at TIMESTAMPTZ;
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS draft_decided_at TIMESTAMPTZ;
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS draft_note TEXT;
-- Irubyana's clips 5 (Instagram), 6 and 8 (TikTok) and 7 (as a revision)
-- were uploaded as drafts BY HAND on 2026-10-01, before this flow existed:
-- they await the brand's decision (draft_submitted), never a second
-- upload. Guarded on the campaign and the state, so it runs once.
UPDATE clip_outputs
   SET status = 'draft_submitted', draft_submitted_at = coalesce(draft_submitted_at, now()),
       draft_note = 'uploaded by hand on Whop, 2026-10-01'
 WHERE id IN (5, 6, 7, 8) AND campaign = 'irubyana-twitch' AND status = 'made';
ALTER TABLE clip_outputs ADD COLUMN IF NOT EXISTS submitted_by TEXT;

-- Clips, 2026-09-29: the campaigns' Whop share links, copied from each
-- campaign's own page. Seeds are insert-only, so the rows already there get
-- the link here, and only where none is on file.
UPDATE clip_campaigns SET whop_url = 'https://contentrewards.com/discover/97baa7f1-f953-4c09-b608-929ad0b48dbe'
 WHERE slug = 'atlus-persona-metaphor' AND whop_url IS NULL;
UPDATE clip_campaigns SET whop_url = 'https://contentrewards.com/discover/91c4990a-b37e-4c59-8232-8bb1ae8d9a94'
 WHERE slug = 'cod-mw4-warzone-operator' AND whop_url IS NULL;

-- The Agency meeting, 2026-09-29 (kernel/meeting.py): one row per local date,
-- built once a day by the scheduler's `agency_meeting` step from agent_runs
-- and the roster, stored before its Slack summary is posted.
-- GET /api/agency/meeting serves today's row; ?live=1 rebuilds from records.
-- payload is the whole meeting as served, every line with its read_at; a
-- line a model reworded carries polished=true and its deterministic `plain`.
CREATE TABLE IF NOT EXISTS agency_meetings (
    day         DATE PRIMARY KEY,                      -- local date
    theme       TEXT NOT NULL,                         -- meeting.THEMES key
    payload     JSONB NOT NULL,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Markets, 2026-09-29 (Ivan: more data, and alerts that act). SEC EDGAR,
-- read only (atlas/modules/markets/edgar.py): the watchlist's 10-K, 10-Q,
-- 8-K and Form 4 filings of the last 30 days, one row per accession number.
-- `baseline`: stored by the first read of its ticker, so reported but never
-- alerted. `parsed` (Form 4 only): NULL waiting, TRUE read, FALSE given up.
-- `role` is from the filing's yes/no flags only (director / officer / 10%
-- owner / other); no owner name or free text is kept.
CREATE TABLE IF NOT EXISTS markets_filings (
    accession   TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    cik         BIGINT NOT NULL,
    form        TEXT NOT NULL,
    filed_on    DATE NOT NULL,
    report_date DATE,
    items       TEXT NOT NULL DEFAULT '',                  -- 8-K item codes, "2.02,9.01"
    doc         TEXT,                                      -- primary document basename
    url         TEXT NOT NULL,
    baseline    BOOLEAN NOT NULL DEFAULT FALSE,
    parsed      BOOLEAN,
    parse_tries INT NOT NULL DEFAULT 0,
    role        TEXT,
    plan_10b5_1 BOOLEAN,
    read_at     TIMESTAMPTZ NOT NULL DEFAULT now()         -- first seen
);
CREATE INDEX IF NOT EXISTS markets_filings_filed ON markets_filings (filed_on);
-- One Form 4's non-derivative transactions, as stated. Only code P (open-
-- market purchase) and S (open-market sale) are reported as insider trades.
CREATE TABLE IF NOT EXISTS markets_insider_trades (
    accession   TEXT NOT NULL,
    seq         INT NOT NULL,
    ticker      TEXT NOT NULL,
    code        TEXT NOT NULL,
    shares      NUMERIC(20,4),
    price       NUMERIC(20,6),
    traded_on   DATE,
    acquired    TEXT,                                      -- A | D
    PRIMARY KEY (accession, seq)
);
-- Which tickers EDGAR has been read for: the first read is the baseline.
CREATE TABLE IF NOT EXISTS markets_edgar_reads (
    ticker        TEXT PRIMARY KEY,
    cik           BIGINT NOT NULL,
    first_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_read_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- markets.alert (Tier 1): one row per event, keyed so an event is alerted
-- once. pending -> sending -> sent (or queued for the brief in quiet
-- hours, which is sent) | expired | retracted. The daily cap counts rows
-- claimed since local midnight.
CREATE TABLE IF NOT EXISTS markets_alerts (
    key          TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,                            -- move | filing | insider | alinea
    ticker       TEXT,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    event_at     TIMESTAMPTZ NOT NULL,                     -- when the reading was taken
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at   TIMESTAMPTZ,
    channels     TEXT,
    retracted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS markets_alerts_status ON markets_alerts (status, created_at);
-- The course's posts (atlas/modules/course): one row per post, one post per
-- card. Proposed by the marketing agent under a marketing_experiments row
-- (its id is the post's utm_campaign), drafted in code (translations by the
-- model, marked so), then carded: course.publish_post for a Postiz channel,
-- course.hand_off for LinkedIn and the Shopify blog.
-- proposed -> drafted -> publishing -> published | failed
--                     -> handed_off ; dropped (rolled back)
CREATE TABLE IF NOT EXISTS course_posts (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    experiment_id BIGINT NOT NULL,                        -- marketing_experiments.id
    channel       TEXT NOT NULL,                          -- linkedin | shopify_blog | postiz:<id>
    platform      TEXT NOT NULL,                          -- utm_source: x | linkedin | shopify_blog | ...
    fmt           TEXT NOT NULL,                          -- social | short | article
    language      TEXT NOT NULL,                          -- en | es | pt | ar
    lesson_id     TEXT NOT NULL,
    link          TEXT NOT NULL,                          -- the UTM-tagged store URL
    body          TEXT,                                   -- exactly what goes out
    english       TEXT,                                   -- the English original, for a translation's card
    translated    TEXT NOT NULL DEFAULT '',               -- 'model' when the model translated it
    price         JSONB NOT NULL DEFAULT '{}',            -- the live read: amount, compare_at, read_at, why
    status        TEXT NOT NULL DEFAULT 'proposed',
    note          TEXT,                                   -- why a draft or a publish did not happen
    posted_ref    TEXT,
    posted_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS course_posts_status ON course_posts (status, at);
-- The course catalog, 2026-09-29 (atlas/modules/course/catalog.py): which
-- product a post markets. Lesson ids repeat across products ("0.1" is in the
-- Operator and in Everyday AI for Adults), so a post is its (product,
-- lesson_id). Rows written before the catalog were all the Operator's.
ALTER TABLE course_posts ADD COLUMN IF NOT EXISTS product TEXT NOT NULL
    DEFAULT 'operator';
-- Clips, 2026-09-29: a source's footage removed from disk by the retention
-- run (kernel/privacy.py::purge_clip_sources) once every clip cut from it
-- is posted, submitted, withdrawn or rolled back, or its post card was
-- declined or expired; a source never cut only once the intake gave up on
-- it or its campaign ended, CLIPS_SOURCE_RETENTION_DAYS (default 21) after
-- it was registered. The row stays; NULL = still on disk (or never checked).
ALTER TABLE clip_sources ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- agency.webild_brief -> agency.build_with_webild -> agency.record_webild_site
-- (2026-09-29): a Webild prompt drafted from the lead's own data, queued by
-- Miro's card for the agent on his Mac, and the site it published with its
-- live launch check. drafted | queued | claimed | published | failed |
-- needs_miro | expired | cancelled. A queued or claimed row past expires_at
-- is never handed out or recorded (the UPDATEs test it), whatever its
-- status still says.
CREATE TABLE IF NOT EXISTS agency_webild (
    id           BIGSERIAL PRIMARY KEY,
    lead_id      BIGINT NOT NULL REFERENCES agency_leads(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at      TIMESTAMPTZ NOT NULL,                     -- when the brief's facts were read
    brief        TEXT NOT NULL,
    brief_sha    TEXT NOT NULL,
    left_out     JSONB NOT NULL DEFAULT '[]',              -- launch items not on record
    status       TEXT NOT NULL DEFAULT 'drafted',
    queued_at    TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    claimed_at   TIMESTAMPTZ,
    claimed_by   TEXT,
    url          TEXT,
    published_at TIMESTAMPTZ,
    report       TEXT,                                     -- the agent's words, untrusted
    reported_at  TIMESTAMPTZ,
    launch       JSONB,                                    -- the live launch check
    audited_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agency_webild_lead ON agency_webild (lead_id, status);

-- Missions, 2026-09-29 (kernel/missions.py): a job bigger than one catalog
-- action, handed to the roster agent that owns it -- "find 50 local
-- businesses with a poor or no website and make them one" goes to agency.
-- `request` is Miro's words verbatim, wrapped as untrusted before any
-- prompt. `slots` is what the recipe was filled with (n, business, area --
-- never coordinates from a model). `state` is the runner's bookkeeping: the
-- area as read (AGENCY_HOME_AREA or Places, with read_at), the searches
-- tried, the lead ids found, per-lead failures and whole-phase blockers.
-- `steps` is one entry per step: action, params, ok, detail, and the card
-- id when a Tier 2 step became a card. What to do next is worked out from
-- the agency tables each pass, never replayed from here.
-- needs_input, then running or waiting, then done | stopped | expired;
-- superseded when a newer request or his answer replaced a question.
-- `lease_until` keeps two passes off one mission at once.
CREATE TABLE IF NOT EXISTS missions (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    recipe      TEXT NOT NULL,                             -- agency.sites | single
    request     TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT '',
    who         TEXT NOT NULL DEFAULT '',
    thread      TEXT NOT NULL DEFAULT '',
    slots       JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'running',
    phase       TEXT NOT NULL DEFAULT '',
    state       JSONB NOT NULL DEFAULT '{}',
    steps       JSONB NOT NULL DEFAULT '[]',
    report      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    deadline    TIMESTAMPTZ,
    lease_until TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS missions_status ON missions (status, created_at);

-- Real estate deal checker, 2026-09-29 (atlas/modules/realestate). One row
-- per deal Miro asked ATLAS to check: what he gave (address, list price,
-- the listing blurb he pasted -- untrusted, wrapped before any prompt) and
-- what was worked out, each estimate with its source and read time. A NULL
-- figure is unknown, never 0. `card` is the text sent to him. Withdrawn by
-- the action's rollback (withdrawn_at), never deleted.
CREATE TABLE IF NOT EXISTS realestate_deals (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    address        TEXT NOT NULL,
    list_price     NUMERIC,
    notes          TEXT NOT NULL DEFAULT '',
    arv            NUMERIC,
    arv_source     TEXT NOT NULL DEFAULT '',                -- source, or why unknown
    arv_read_at    TIMESTAMPTZ,
    comps          INTEGER,
    repairs        NUMERIC,
    repairs_basis  TEXT NOT NULL DEFAULT '',
    assignment_fee NUMERIC,
    arv_factor     NUMERIC,
    mao            NUMERIC,
    spread         NUMERIC,                                 -- mao - list_price
    rent           NUMERIC,
    rent_read_at   TIMESTAMPTZ,
    verdict        TEXT NOT NULL DEFAULT '',
    card           TEXT NOT NULL DEFAULT '',
    calls          TEXT NOT NULL DEFAULT '',                -- RentCast calls line
    requested_by   TEXT NOT NULL DEFAULT '',
    withdrawn_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS realestate_deals_created ON realestate_deals (created_at);

-- RentCast reads (realestate/rentcast.py): every response kept with the
-- time it was read; a fresh ok row is served instead of a new call and
-- shows its ORIGINAL read time. Never holds the API key.
CREATE TABLE IF NOT EXISTS realestate_rentcast_reads (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    endpoint   TEXT NOT NULL,
    query_key  TEXT NOT NULL,
    status     INTEGER NOT NULL,
    ok         BOOLEAN NOT NULL,
    body       JSONB
);
CREATE INDEX IF NOT EXISTS realestate_rentcast_reads_key
    ON realestate_rentcast_reads (endpoint, query_key, at DESC);

-- RentCast calls ATLAS counted per UTC month, reserved BEFORE each call is
-- sent (a failed call counts too): over-counts, never under-counts.
CREATE TABLE IF NOT EXISTS realestate_rentcast_quota (
    month       DATE PRIMARY KEY,
    used        INTEGER NOT NULL DEFAULT 0,
    limit_month INTEGER
);

-- Instagram comment-keyword -> DM funnels, 2026-09-30
-- (atlas/modules/instagram/funnel.py). One row per funnel: the account,
-- the keyword, the media scope, and the EXACT public reply and private
-- reply Miro approved on the instagram.funnel_set card. `text_hash` is
-- sha256 over both texts as approved; the sender recomputes it from the
-- row before every send and refuses on a mismatch, so a text edited
-- behind the card never reaches anyone. Deactivating is a flag, never a
-- DELETE: the sends below keep their history.
CREATE TABLE IF NOT EXISTS ig_funnels (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    account       TEXT NOT NULL,                       -- instagram.ACCOUNTS key
    keyword       TEXT NOT NULL,                       -- lower-cased
    media_id      TEXT NOT NULL DEFAULT '',            -- '' = all recent media
    public_reply  TEXT NOT NULL,                       -- never carries a link
    dm_text       TEXT NOT NULL,                       -- the private reply
    text_hash     TEXT NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    daily_cap     INTEGER NOT NULL DEFAULT 200,
    timezone      TEXT NOT NULL DEFAULT 'America/New_York',
    created_by    TEXT NOT NULL DEFAULT '',
    approved_at   TIMESTAMPTZ,
    stopped_at    TIMESTAMPTZ,                         -- a policy/block error
    stopped_why   TEXT NOT NULL DEFAULT '',
    last_error    TEXT NOT NULL DEFAULT '',
    last_error_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS ig_funnels_scope
    ON ig_funnels (account, keyword, media_id);

-- The newest comment timestamp the sender has read per (funnel, media):
-- the next sweep pages from the top until it is back behind this mark.
CREATE TABLE IF NOT EXISTS ig_funnel_cursors (
    funnel_id   BIGINT NOT NULL REFERENCES ig_funnels(id) ON DELETE CASCADE,
    media_id    TEXT NOT NULL,
    newest_at   TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (funnel_id, media_id)
);

-- One row per (funnel, commenter), ever: the once-only rule is the
-- UNIQUE index, not a lookup. queued -> sent | failed | needs_human. The
-- two provider writes are recorded leg by leg (public_reply_id,
-- private_message_id) so a retry never repeats the leg that went through.
CREATE TABLE IF NOT EXISTS ig_funnel_sends (
    id                 BIGSERIAL PRIMARY KEY,
    funnel_id          BIGINT NOT NULL REFERENCES ig_funnels(id) ON DELETE CASCADE,
    ig_user_id         TEXT NOT NULL,
    username           TEXT NOT NULL DEFAULT '',
    comment_id         TEXT NOT NULL,
    media_id           TEXT NOT NULL DEFAULT '',
    comment_text       TEXT NOT NULL DEFAULT '',
    commented_at       TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'queued',
    queued_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_attempt_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts           INTEGER NOT NULL DEFAULT 0,
    sent_at            TIMESTAMPTZ,
    public_reply_id    TEXT,
    private_message_id TEXT,
    error              TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ig_funnel_sends_once_per_user
    ON ig_funnel_sends (funnel_id, ig_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ig_funnel_sends_comment
    ON ig_funnel_sends (funnel_id, comment_id);
CREATE INDEX IF NOT EXISTS ig_funnel_sends_status
    ON ig_funnel_sends (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ig_funnel_sends_sent ON ig_funnel_sends (sent_at);


-- ---------------------------------------------------------------- futures
-- The paper futures strategy engine (atlas/modules/futures). MNQ/NQ bars
-- read from Yahoo's public chart endpoint (unofficial; gaps happen and are
-- recorded), a deterministic rulebook of engines, a backtester with honesty
-- guards, and a PAPER forward book. Nothing here can trade: no broker, no
-- order path, no key. Money out is Tier 3, permanently denied.

-- Closed bars, one row per (symbol, interval, bar open time), each with the
-- time it was read. A bar is never invented: a hole in the feed is a hole.
CREATE TABLE IF NOT EXISTS futures_bars (
    symbol    TEXT NOT NULL,                        -- MNQ=F | NQ=F
    interval  TEXT NOT NULL,                        -- 5m | 15m | 1h | 1d
    ts        TIMESTAMPTZ NOT NULL,                 -- bar open, UTC
    open      DOUBLE PRECISION NOT NULL,
    high      DOUBLE PRECISION NOT NULL,
    low       DOUBLE PRECISION NOT NULL,
    close     DOUBLE PRECISION NOT NULL,
    volume    DOUBLE PRECISION,                     -- NULL when the feed had none
    read_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, interval, ts)
);

-- Every read of the feed: what came back, or why nothing did (UNKNOWN,
-- never a guess), and the gaps found inside CME hours.
CREATE TABLE IF NOT EXISTS futures_reads (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol    TEXT NOT NULL,
    interval  TEXT NOT NULL,
    ok        BOOLEAN NOT NULL,
    status    INTEGER,                              -- HTTP status, when any
    bars      INTEGER NOT NULL DEFAULT 0,           -- closed bars kept
    newest    TIMESTAMPTZ,                          -- newest closed bar
    gaps      JSONB NOT NULL DEFAULT '[]',
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS futures_reads_at ON futures_reads (symbol, interval, at DESC);

-- The paper forward book: one row per paper trade, from the signal to the
-- exit. Prices carry their slippage; the bar each fill came from is kept.
CREATE TABLE IF NOT EXISTS futures_trades (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol         TEXT NOT NULL,
    interval       TEXT NOT NULL,
    engine         TEXT NOT NULL,
    side           INTEGER NOT NULL,                -- +1 long, -1 short
    contracts      INTEGER NOT NULL,
    signal_ts      TIMESTAMPTZ NOT NULL,            -- the closed bar that fired
    signal_close   DOUBLE PRECISION NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    session_date   DATE NOT NULL,                   -- CME session of the entry
    status         TEXT NOT NULL,                   -- open | closed
    entry_ts       TIMESTAMPTZ NOT NULL,
    entry_price    DOUBLE PRECISION NOT NULL,       -- after slippage
    entry_slippage DOUBLE PRECISION NOT NULL,       -- points
    stop           DOUBLE PRECISION NOT NULL,       -- initial
    target         DOUBLE PRECISION,
    trail_atr      DOUBLE PRECISION,
    time_stop_min  INTEGER NOT NULL,
    exit_ts        TIMESTAMPTZ,
    exit_price     DOUBLE PRECISION,
    exit_slippage  DOUBLE PRECISION,
    exit_reason    TEXT,                            -- stop|target|trail|time|eod|daily_stop|breaker
    commission     DOUBLE PRECISION NOT NULL,       -- $ both sides
    pnl            DOUBLE PRECISION,                -- $ net
    r_multiple     DOUBLE PRECISION,
    UNIQUE (symbol, interval, engine, signal_ts)
);
CREATE INDEX IF NOT EXISTS futures_trades_session ON futures_trades (session_date);

-- Every fill, entry or exit, as its own row: the bar, the price, the
-- slippage and the reason. The trade row summarises; this is the record.
CREATE TABLE IF NOT EXISTS futures_fills (
    id        BIGSERIAL PRIMARY KEY,
    trade_id  BIGINT NOT NULL REFERENCES futures_trades (id),
    kind      TEXT NOT NULL,                        -- entry | exit
    bar_ts    TIMESTAMPTZ NOT NULL,
    price     DOUBLE PRECISION NOT NULL,            -- after slippage
    raw_price DOUBLE PRECISION NOT NULL,            -- the bar price filled at
    slippage  DOUBLE PRECISION NOT NULL,
    reason    TEXT NOT NULL,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trade_id, kind)
);

-- The book's own state: one JSON row per key (the simulator's state, the
-- last processed bar, the breaker). Written only by the futures loop.
CREATE TABLE IF NOT EXISTS futures_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backtest runs, kept so the weekly scoreboard can set the forward book
-- against the expectation it was deployed with.
CREATE TABLE IF NOT EXISTS futures_backtests (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol    TEXT NOT NULL,
    interval  TEXT NOT NULL,
    first_ts  TIMESTAMPTZ,
    last_ts   TIMESTAMPTZ,
    bars      INTEGER NOT NULL,
    results   JSONB NOT NULL                        -- backtest.summary()
);

-- 2026-09-29: agency lead follow-up (atlas/modules/agency/leads.py,
-- consent.py, replies.py, sms.py, calls.py). What a lead said back, the
-- consent that alone lets ATLAS text or ring them, every contact ATLAS made,
-- the replies it drafted, and the deals Miro recorded.
--
-- The Message-ID `send_outreach` stamps, kept on the row so a reply's
-- In-Reply-To / References can be matched to it.
ALTER TABLE agency_outreach ADD COLUMN IF NOT EXISTS message_id TEXT;

-- One row per message a lead sent us (email, sms, call transcript line set).
-- `body` is the lead's own words, stored as received; it is untrusted and is
-- only ever put in a prompt through wrap_untrusted(). `source_id` is the
-- provider's id (Message-ID, MessageSid, CallSid) and makes intake idempotent.
CREATE TABLE IF NOT EXISTS agency_lead_messages (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_id     BIGINT REFERENCES agency_leads(id),
    channel     TEXT NOT NULL,                  -- email | sms | call
    source_id   TEXT NOT NULL,
    sender      TEXT NOT NULL DEFAULT '',       -- address or number, as received
    subject     TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    intent      TEXT,                           -- the closed set in leads.INTENTS
    alerted_at  TIMESTAMPTZ,
    reply_channel TEXT,                         -- queued for a drafted reply: email | sms
    drafted_at  TIMESTAMPTZ,                    -- claimed by kernel.lead_followup
    UNIQUE (channel, source_id)
);
CREATE INDEX IF NOT EXISTS agency_lead_messages_lead
    ON agency_lead_messages (lead_id, at DESC);
ALTER TABLE agency_lead_messages ADD COLUMN IF NOT EXISTS reply_channel TEXT;
ALTER TABLE agency_lead_messages ADD COLUMN IF NOT EXISTS drafted_at TIMESTAMPTZ;

-- Prior express consent to be texted or called (TCPA; FCC 2024 on AI
-- voices). No live row for a lead's number: agency.text_lead / call_lead /
-- send_reply(sms) refuse. The evidence is the lead's own words, quoted
-- verbatim with the id of the message they came in and when. Revoked, never
-- deleted: an opt-out is history too.
CREATE TABLE IF NOT EXISTS agency_lead_consent (
    id             BIGSERIAL PRIMARY KEY,
    lead_id        BIGINT NOT NULL REFERENCES agency_leads(id),
    phone          TEXT NOT NULL,               -- E.164, e.g. +12125550123
    scope          TEXT NOT NULL,               -- sms | call
    source_channel TEXT NOT NULL,               -- email | sms | call | web_form
    source_id      TEXT NOT NULL,               -- the message that gave it
    quoted         TEXT NOT NULL,               -- their exact words (untrusted)
    granted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at     TIMESTAMPTZ,
    revoked_why    TEXT
);
CREATE INDEX IF NOT EXISTS agency_lead_consent_phone
    ON agency_lead_consent (phone, scope) WHERE revoked_at IS NULL;

-- Every text and call ATLAS placed to a lead, for the per-lead cap and the
-- disclosure evidence (`opening` is the exact first line said or sent).
-- `token_sha` is the hash of the call's own URL secret; a lead-call webhook
-- is admitted only for a row it matches.
CREATE TABLE IF NOT EXISTS agency_contacts (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_id     BIGINT NOT NULL REFERENCES agency_leads(id),
    channel     TEXT NOT NULL,                  -- sms | call
    phone       TEXT NOT NULL,
    opening     TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    sid         TEXT,
    token_sha   TEXT,
    status      TEXT NOT NULL DEFAULT 'placed', -- placed | answered | unanswered | failed
    transcript  JSONB NOT NULL DEFAULT '[]',    -- [{who, text}], the lead's turns untrusted
    ended_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agency_contacts_lead ON agency_contacts (lead_id, at DESC);

-- Replies ATLAS drafted to a lead (agency.draft_reply) and what became of
-- them (agency.send_reply, agency.send_agreement). `quoted_prices` is every
-- figure the draft names, checked against the price rules when drafted and
-- again when sent.
CREATE TABLE IF NOT EXISTS agency_reply_drafts (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_id       BIGINT NOT NULL REFERENCES agency_leads(id),
    in_reply_to   BIGINT REFERENCES agency_lead_messages(id),
    kind          TEXT NOT NULL DEFAULT 'reply', -- reply | agreement
    channel       TEXT NOT NULL,                 -- email | sms
    to_address    TEXT NOT NULL,
    subject       TEXT,
    body          TEXT NOT NULL,
    quoted_prices JSONB NOT NULL DEFAULT '[]',
    price         INTEGER,                       -- agreement only: the agreed figure
    status        TEXT NOT NULL DEFAULT 'draft', -- draft | sent | superseded
    sent_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agency_reply_drafts_lead
    ON agency_reply_drafts (lead_id, at DESC);
-- 2026-10-06: the 2-3 real calendar slots a reply to an interested lead
-- offered (ISO strings with offset), so the lead's answer can be matched
-- to one IN CODE (leads.match_slot) and the draft refused if it names any
-- other time. NULL: no times were offered in this reply.
ALTER TABLE agency_reply_drafts ADD COLUMN IF NOT EXISTS offered_slots JSONB;

-- 2026-10-06 (Miro: "schedule meetings with them via email ... approve
-- invoices too"): what a lead's own words asked for, decided in code when
-- the message came in. `booking_slot` is the one offered slot their words
-- matched (held and invited by kernel.lead_followup.bookings, claimed once
-- in `booked_at`); `agreement_price` is the allowed price their words
-- accepted (the agreement sent by kernel.lead_followup.agreements, claimed
-- once in `agreed_at`). Ambiguous words set neither.
ALTER TABLE agency_lead_messages ADD COLUMN IF NOT EXISTS booking_slot TIMESTAMPTZ;
ALTER TABLE agency_lead_messages ADD COLUMN IF NOT EXISTS booked_at TIMESTAMPTZ;
ALTER TABLE agency_lead_messages ADD COLUMN IF NOT EXISTS agreement_price INTEGER;
ALTER TABLE agency_lead_messages ADD COLUMN IF NOT EXISTS agreed_at TIMESTAMPTZ;

-- Deals Miro recorded (agency.record_deal, his word only). The first-sale
-- price is offered only while no row here is won or paid -- read fresh, and
-- an unreadable table means the regular floor.
CREATE TABLE IF NOT EXISTS agency_deals (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_id   BIGINT NOT NULL REFERENCES agency_leads(id),
    status    TEXT NOT NULL,                    -- won | paid | lost
    amount    INTEGER,
    note      TEXT NOT NULL DEFAULT ''
);

-- 2026-09-30: only real, active New York businesses that need help
-- (atlas/modules/agency/verify.py). Places' own businessStatus, kept from
-- the search; and one row per verification: the verdict, why, what each
-- check said, the Perplexity answer (wrapped untrusted) with its source
-- URLs and when it was read. A lead is never audited, marketed or contacted
-- on anything but a live 'verified' row.
ALTER TABLE agency_leads ADD COLUMN IF NOT EXISTS business_status TEXT;
CREATE TABLE IF NOT EXISTS agency_verifications (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_id    BIGINT NOT NULL REFERENCES agency_leads(id),
    verdict    TEXT NOT NULL,                  -- verified | disqualified | unknown
    reasons    JSONB NOT NULL DEFAULT '[]',
    checks     JSONB NOT NULL DEFAULT '{}',    -- operating, chain, government, nyc, web
    answer     TEXT NOT NULL DEFAULT '',       -- wrap_untrusted(Perplexity's answer)
    sources    JSONB NOT NULL DEFAULT '[]',
    read_at    TIMESTAMPTZ,                    -- when Perplexity was read; NULL if not
    previous_status TEXT,                      -- the lead's status before, for rollback
    withdrawn  BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS agency_verifications_lead
    ON agency_verifications (lead_id, at DESC);

-- 2026-09-30: a meeting with Miro booked on a lead call (agency.hold_meeting,
-- agency.send_invite). The hold is on Miro's own calendar with no attendee;
-- the invite goes to the lead only on his yes. `reminded_at` claims the
-- one-hour reminder once.
CREATE TABLE IF NOT EXISTS agency_lead_meetings (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    lead_id     BIGINT NOT NULL REFERENCES agency_leads(id),
    event_id    TEXT NOT NULL,
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    email       TEXT,
    call_sid    TEXT,
    status      TEXT NOT NULL DEFAULT 'held',  -- held | invited | released
    invited_at  TIMESTAMPTZ,
    reminded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agency_lead_meetings_start
    ON agency_lead_meetings (start_at) WHERE status <> 'released';

-- 2026-09-30: Mapfront's Stripe account, read through its signed webhook
-- (atlas/api/stripe_mapfront.py, atlas/modules/agency/payments.py). One row
-- per Stripe event id -- a replay is a no-op -- and one row per payment,
-- with the amount exactly as Stripe stated it, in the smallest unit.
-- Nothing here ever calls Stripe: it reads and records.
CREATE TABLE IF NOT EXISTS agency_stripe_events (
    event_id   TEXT PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    type       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agency_payments (
    id             BIGSERIAL PRIMARY KEY,
    at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_id       TEXT NOT NULL,                 -- the event that recorded it
    source         TEXT NOT NULL,                 -- checkout | invoice
    object_id      TEXT NOT NULL,                 -- cs_... | in_...
    payment_intent TEXT,
    charge         TEXT,
    lead_id        BIGINT REFERENCES agency_leads(id),
    matched_by     TEXT NOT NULL,                 -- reference | metadata | email | none
    email          TEXT,
    amount_minor   BIGINT NOT NULL,               -- as Stripe stated it
    currency       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'paid',  -- paid | refunded | partially_refunded | disputed
    refunded_minor BIGINT NOT NULL DEFAULT 0,
    deal_id        BIGINT
);
CREATE INDEX IF NOT EXISTS agency_payments_pi ON agency_payments (payment_intent);
CREATE INDEX IF NOT EXISTS agency_payments_charge ON agency_payments (charge);
ALTER TABLE agency_deals ADD COLUMN IF NOT EXISTS amount_minor BIGINT;
ALTER TABLE agency_deals ADD COLUMN IF NOT EXISTS currency TEXT;

-- The AI chat assistant on Mapfront client sites (atlas/modules/agency/
-- sitechat.py). One row per question: counts and dollars, never the words.
-- The worst case is booked ('reserved') under a lock BEFORE the model is
-- asked, then settled to what the provider reported; SITE_CHAT_MONTHLY_CAP_USD
-- is summed over this month, SITE_CHAT_PER_SITE_PER_DAY counts today's rows
-- per slug. slug '_health' is the `site_chat` check's own round trip.
CREATE TABLE IF NOT EXISTS site_chat_usage (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    slug          TEXT NOT NULL,
    outcome       TEXT NOT NULL,     -- reserved|answered|fallback|refused|error
    usd           NUMERIC(12, 6) NOT NULL DEFAULT 0,
    provider      TEXT,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS site_chat_usage_at ON site_chat_usage (at);
CREATE INDEX IF NOT EXISTS site_chat_usage_slug_at ON site_chat_usage (slug, at);

-- 2026-09-30: the email address a lead PUBLISHES on its own site
-- (atlas/modules/agency/contacts.py, `agency.find_email`), with where and
-- when it was read. Never a guessed address. `withdrawn` is the rollback.
CREATE TABLE IF NOT EXISTS agency_lead_emails (
    id          BIGSERIAL PRIMARY KEY,
    lead_id     BIGINT NOT NULL REFERENCES agency_leads(id),
    address     TEXT NOT NULL,
    found_url   TEXT NOT NULL,                  -- the page it was read on
    found_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    same_domain BOOLEAN NOT NULL DEFAULT FALSE, -- on the site's own domain
    withdrawn   BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (lead_id, address)
);

-- 2026-09-30: a consent given on the "Have Mapfront call me" form on a
-- preview site (source_channel 'web_form', atlas/api/call_me.py) carries its
-- evidence here: page URL, time, IP hash, user agent, and the exact consent
-- text shown. `call_card_at` is when the agency.call_lead card for it was
-- raised (or why it could not be, in `call_card_note`).
ALTER TABLE agency_lead_consent ADD COLUMN IF NOT EXISTS evidence JSONB;
ALTER TABLE agency_lead_consent ADD COLUMN IF NOT EXISTS call_card_at TIMESTAMPTZ;
ALTER TABLE agency_lead_consent ADD COLUMN IF NOT EXISTS call_card_note TEXT;

-- 2026-09-30: after a site is deployed, the outreach card (or the call brief
-- when no published email is known) is raised once: `kernel.lead_followup`
-- claims the site here.
ALTER TABLE agency_sites ADD COLUMN IF NOT EXISTS followed_up_at TIMESTAMPTZ;

-- 2026-09-30: ATLAS Academy purchases (atlas/modules/academy/delivery.py),
-- one row per Stripe Checkout Session, for Miro's dashboard. Written only
-- from what Stripe answered when the session was read (the access page, the
-- signed webhook): never a guessed amount. The first INSERT of a session is
-- what posts the purchase to Slack, so a session posts once. `offers` are
-- the catalog offer ids its line items matched; an empty array with status
-- 'unknown_price' is a paid checkout the catalog could not deliver.
CREATE TABLE IF NOT EXISTS academy_purchases (
    session_id      TEXT PRIMARY KEY,               -- cs_live_... | cs_test_...
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL,                  -- access | webhook
    status          TEXT NOT NULL,                  -- paid | refunded | disputed | unknown_price
    offers          TEXT[] NOT NULL DEFAULT '{}',
    price_ids       TEXT[] NOT NULL DEFAULT '{}',
    amount_minor    BIGINT NOT NULL,                -- amount_total, as Stripe stated it
    currency        TEXT NOT NULL,
    refunded_minor  BIGINT NOT NULL DEFAULT 0,
    email           TEXT,
    payment_intent  TEXT,
    livemode        BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS academy_purchases_seen ON academy_purchases (first_seen_at);

-- Ivan's check of a TradingView alert (markets/ideas.py `check_alert`),
-- 2026-09-30: the idea rules run once per stored alert, and the one-line
-- verdict rides on the alert's Slack line. Once per row, never twice.
ALTER TABLE tradingview_alerts ADD COLUMN IF NOT EXISTS ivan_checked_at TIMESTAMPTZ;
ALTER TABLE tradingview_alerts ADD COLUMN IF NOT EXISTS ivan_note TEXT;

-- 2026-10-01: the outreach email sends itself (Miro: non-urgent work runs
-- without a card; only emergencies reach him) -- but only a draft
-- `draft_outreach` wrote. Its body's SHA-256 is stored with the row and
-- checked before every unattended send (`outreach.auto_hold`); a body
-- edited by hand or written by anything else is held for a card instead.
ALTER TABLE agency_outreach ADD COLUMN IF NOT EXISTS body_sha TEXT;
-- The one timed follow-up (kernel.lead_followup.followups): claimed once per
-- sent message that got no reply, so two ticks never draft it twice.
ALTER TABLE agency_outreach ADD COLUMN IF NOT EXISTS followup_claimed_at TIMESTAMPTZ;

-- The store's contact form (atlas/api/academy.py `contact`): what a visitor
-- wrote, kept so a message survives a failed Slack post. Untrusted text.
-- The daily Academy reel on the clips accounts (clips.autopublish_promo):
-- one row per automatic post, read for the daily cap and the rotation.
-- Tracked requests (kernel/tasks.py): everything Miro asks ATLAS to do
-- from Slack or the phone, where it went and how it ended.
CREATE TABLE IF NOT EXISTS tasks (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    source    TEXT NOT NULL,                    -- slack | phone | voice
    who       TEXT NOT NULL DEFAULT '',
    thread    TEXT NOT NULL DEFAULT '',
    text      TEXT NOT NULL,
    route     TEXT NOT NULL DEFAULT '',         -- action | mission | improve | solve | cards | answer | search
    status    TEXT NOT NULL DEFAULT 'open',     -- open | done | held | failed
    outcome   TEXT NOT NULL DEFAULT '',
    cards     JSONB NOT NULL DEFAULT '[]',
    done_at   TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS academy_promo_posts (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    slug      TEXT NOT NULL,
    media_id  BIGINT NOT NULL,
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS academy_contacts (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    order_ref       TEXT,
    message         TEXT NOT NULL,
    client          TEXT NOT NULL                   -- the address it came from
);
CREATE INDEX IF NOT EXISTS academy_contacts_received ON academy_contacts (received_at);

-- The "ask for a free preview" form on mapfront.io (atlas/api/preview_request.py):
-- who asked and how to reach them, kept so a request survives a failed Slack
-- post. Untrusted text; nothing reads it back into a prompt unwrapped.
CREATE TABLE IF NOT EXISTS agency_preview_requests (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL,
    business        TEXT NOT NULL,
    phone           TEXT,
    email           TEXT,
    site            TEXT,
    note            TEXT,
    client          TEXT NOT NULL                   -- the address it came from
);
CREATE INDEX IF NOT EXISTS agency_preview_requests_received
    ON agency_preview_requests (received_at);

-- Verified-buyer reviews (modules/academy/reviews.py): written on the paid
-- access page, shown only once Miro approves (academy.approve_review).
CREATE TABLE IF NOT EXISTS academy_reviews (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL,                  -- the paid checkout it came from
    offer_id        TEXT NOT NULL,
    product_slug    TEXT NOT NULL,
    stars           SMALLINT NOT NULL CHECK (stars BETWEEN 1 AND 5),
    body            TEXT NOT NULL,                  -- the buyer's words; untrusted
    display_name    TEXT,
    client          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    UNIQUE (session_id, offer_id)
);
CREATE INDEX IF NOT EXISTS academy_reviews_status ON academy_reviews (status, written_at);

-- Every call ATLAS places (kernel/callrecord.py): what it opened with, the
-- question, the cards, each deciding key press and how it ended. ATLAS's
-- own words and card ids only: no audio, no transcript of his speech.
CREATE TABLE IF NOT EXISTS voice_calls (
    call_sid        TEXT PRIMARY KEY,
    placed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt         INT NOT NULL DEFAULT 0,          -- 0 first ring, 1+ a re-ring
    title           TEXT NOT NULL,
    opening         TEXT NOT NULL,
    ask             TEXT NOT NULL DEFAULT '',
    pids            BIGINT[] NOT NULL DEFAULT '{}',
    incident_key    TEXT,
    away            BOOLEAN NOT NULL DEFAULT FALSE,
    night           BOOLEAN NOT NULL DEFAULT FALSE,
    outcome         TEXT NOT NULL DEFAULT 'ringing', -- ringing | answered | missed | machine
    answered_by     TEXT,
    decided         JSONB NOT NULL DEFAULT '[]',
    ended_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS voice_calls_placed ON voice_calls (placed_at);
-- How long ATLAS took to answer each turn, in milliseconds (its own share
-- of the pause; the phone network's is not measurable without a recording).
ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS turn_ms INT[] NOT NULL DEFAULT '{}';

-- The mistake ledger (kernel/ledger.py): one row per failure shape, derived
-- nightly from `lessons`. A report, never a rule: nothing reads this table
-- to decide what may run.
CREATE TABLE IF NOT EXISTS ledger_mistakes (
    fingerprint     TEXT PRIMARY KEY,               -- sha256(action, error with variables removed)
    action          TEXT NOT NULL,
    sample          TEXT NOT NULL,
    occurrences     INT NOT NULL DEFAULT 1,
    variants        INT NOT NULL DEFAULT 1,         -- different parameter sets
    first_seen      TIMESTAMPTZ NOT NULL,
    last_seen       TIMESTAMPTZ NOT NULL,
    state           TEXT NOT NULL DEFAULT 'new',    -- new | recurring | reopened | closed
    told_at         TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    recurrences_after_close INT NOT NULL DEFAULT 0
);

-- 2026-10-06: the assistant's runs (atlas/modules/assistant/runs.py). One
-- row a question. The question itself is never stored, only its sha256;
-- `id` is a short id made in code, so Slack can say `ask cancel <id>`.
CREATE TABLE IF NOT EXISTS assistant_runs (
    id            TEXT PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    question_sha  TEXT NOT NULL,
    mode          TEXT NOT NULL,                  -- ask | search
    skill         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'completed', 'failed',
                                    'cancelled')),
    tool_calls    INT NOT NULL DEFAULT 0,
    tokens        INT NOT NULL DEFAULT 0,
    provider      TEXT,
    model         TEXT,
    latency_ms    INT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS assistant_runs_at ON assistant_runs (at);

-- 2026-10-07: requests and messages to the Guardian and FlyCheap LinkedIn
-- pages (atlas/modules/linkedin/requests.py). One row per request or page
-- thread, unique on (page, source, external_key): the request/thread URL,
-- or the surface and requester when LinkedIn gave no link. `words` is the
-- requester's text, truncated -- UNTRUSTED, wrapped before any prompt.
-- `inbound_sha` is their latest words; `replied_sha` the inbound the last
-- reply answered, so one inbound gets at most one reply.
CREATE TABLE IF NOT EXISTS linkedin_requests (
    id              BIGSERIAL PRIMARY KEY,
    page            TEXT NOT NULL,                  -- linkedin.PAGES key: guardian | flycheap
    source          TEXT NOT NULL
                    CHECK (source IN ('service_request', 'page_message')),
    external_key    TEXT NOT NULL,
    surface         TEXT NOT NULL DEFAULT '',
    requester       TEXT NOT NULL DEFAULT '',
    words           TEXT NOT NULL DEFAULT '',
    inbound_sha     TEXT NOT NULL DEFAULT '',
    service_raw     TEXT NOT NULL DEFAULT '',
    label           TEXT NOT NULL DEFAULT 'other',  -- closed set per page, in code
    label_by        TEXT NOT NULL DEFAULT 'none',   -- keyword | model | none
    budget          TEXT NOT NULL DEFAULT '',
    timeline        TEXT NOT NULL DEFAULT '',
    url             TEXT,
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'drafted', 'replied', 'held', 'suppressed')),
    note            TEXT NOT NULL DEFAULT '',
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_inbound_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at      TIMESTAMPTZ,
    replied_at      TIMESTAMPTZ,
    replied_sha     TEXT,
    UNIQUE (page, source, external_key)
);
CREATE INDEX IF NOT EXISTS linkedin_requests_status ON linkedin_requests (status);
CREATE TABLE IF NOT EXISTS linkedin_request_drafts (
    id                BIGSERIAL PRIMARY KEY,
    request_id        BIGINT NOT NULL REFERENCES linkedin_requests (id),
    inbound_sha       TEXT NOT NULL,                -- the inbound this answers
    body              TEXT NOT NULL,
    body_sha          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'sent', 'discarded', 'unverified',
                                        'rolled_back')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    send_attempted_at TIMESTAMPTZ,                  -- set just before Send is clicked
    sent_at           TIMESTAMPTZ
);
