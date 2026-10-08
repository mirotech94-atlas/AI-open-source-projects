from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GUARDIAN_", env_file=".env", extra="ignore")
    data_dir: Path = Path("./data")
    database_url: str = ""          # default: sqlite file inside data_dir
    secret_key: str = "change-me"
    base_url: str = "http://127.0.0.1:8800"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Vault <guardian@example.com>"
    #: Item 25: security findings come from their own address, not the one
    #: that sends login codes and receipts. A customer filtering alerts into
    #: a folder should not also be filing their OTPs there. It has to name a
    #: mailbox the configured SMTP account may send as, which is why it is a
    #: setting and not a constant.
    smtp_from_alerts: str = "Vault Alerts <alerts@guarder.io>"
    # Where the OWNER hears about access requests (comma-separated). Empty:
    # every superadmin's email. Production sets this to the owner's inbox.
    owner_email: str = ""
    # Where a customer's support ticket lands, and the address the operator's
    # own ticket letters name as the way in. It is a real mailbox the owner
    # reads (Zoho), NOT an inbound-processing address: guarder.io does not
    # accept mail into the app, so every ticket letter links into the in-app
    # thread rather than inviting a reply this hub would never see.
    support_email: str = "support@guarder.io"
    dev_console_otp: bool = False   # print OTP to stdout instead of emailing (tests/dev)
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    session_hours: int = 12
    remember_days: int = 30
    web_dist: Path | None = None    # built frontend dir; env GUARDIAN_WEB_DIST overrides
    metrics_token: str = ""         # when set, /metrics requires ?token= or Authorization: Bearer to match

    # Task 64: `scheduler.demo_refresh_job` rolls the `demo` tenant's window
    # forward so guarder.io's public "Live from the demo network" panel
    # (routers/public_feed.py) never shows a newest row that is days old.
    # Off by default -- a self-hosted install running this same image must
    # never have a background job start wiping and rewriting its `demo`
    # tenant's data (if it even has one) unless it explicitly opts in. Only
    # the owner's own guarder.io deployment sets GUARDIAN_DEMO_REFRESH_ENABLED=1.
    demo_refresh_enabled: bool = False

    # Vault chat (POST /api/v1/chat) provider ladder: Groq -> Anthropic ->
    # rules.answer fallback. Empty key = that provider is skipped, never a
    # 500 -- see chat/providers.py's _groq_available/_anthropic_available.
    groq_api_key: str = ""
    # Slack incoming-webhook URL for the OPERATOR's own admin channel: platform
    # events (access requests, new tenants, first sign-ins, sensor enrolments,
    # chat hand-offs), not tenant alerts. Env GUARDIAN_ADMIN_SLACK_WEBHOOK.
    admin_slack_webhook: str = ""
    # The Vault Slack APP (two-way): one bot for the whole hub. Alerts are
    # posted by the bot when a tenant's Slack card carries a channel id, and a
    # reply in the channel ("fix it", "yes") runs the chat engine as that
    # tenant. Env GUARDIAN_SLACK_BOT_TOKEN / GUARDIAN_SLACK_SIGNING_SECRET.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    # The operator's admin channel id and which tenant a message there acts
    # as (the operator's own).
    admin_slack_channel: str = ""
    admin_slack_tenant: str = "home"
    anthropic_api_key: str = ""
    chat_model_first: str = "openai/gpt-oss-120b"   # first call per turn, carries the DATA blob
    chat_model_tools: str = "openai/gpt-oss-20b"    # tool-round follow-ups, DATA blob omitted
    chat_model_anthropic: str = "claude-sonnet-5"    # second rung of the chat provider ladder

    # Voice (POST /api/v1/voice/*). ElevenLabs does both halves: Scribe
    # transcribes what the customer says, TTS reads the answer back. Empty =
    # no microphone and no speaker anywhere in the app, never a 500 -- the
    # key's presence IS the switch, exactly as it is for the chat ladder
    # above, so there is no setting that can be on while the key is missing.
    #
    # Deliberately NOT in platform_settings.SPEC. That module's own rule is
    # that credentials belong in the environment and not in a database that
    # gets backed up, exported and copied to laptops; the console may show
    # whether this is set and nothing more.
    #
    # Both are required together. A key with no voice id can transcribe and
    # cannot speak, and a hub that advertises a speaker and then 502s is
    # worse than one that shows no speaker at all -- so `voice.ready()`
    # demands both. Pick a voice id from the ElevenLabs voice library.
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_stt_model: str = "scribe_v2"
    elevenlabs_tts_model: str = "eleven_multilingual_v2"

    # Task 68 -- billing. All three are empty by default and the hub must run
    # perfectly well that way: with no secret key every billing surface
    # reports "not configured" (GET /api/v1/billing still answers, the
    # checkout/portal routes 503) and the free trial, which touches Stripe
    # not at all, keeps working. Nothing here is ever logged or echoed.
    #
    #   stripe_secret_key      -- Stripe dashboard > Developers > API keys >
    #                             "Secret key" (use the TEST key, sk_test_...).
    #   stripe_webhook_secret  -- Stripe dashboard > Developers > Webhooks >
    #                             your endpoint > "Signing secret" (whsec_...).
    #                             With this unset the webhook route rejects
    #                             every call: an unsigned billing event is
    #                             never accepted, "not configured" or not.
    #   stripe_api_base        -- override only for tests; production leaves it.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_api_base: str = "https://api.stripe.com"
    # Seconds of clock skew tolerated on a webhook timestamp before the call
    # is treated as a replay. Stripe's own libraries default to 300.
    stripe_webhook_tolerance: int = 300

    # Whether Stripe is the merchant of record for this hub's subscriptions.
    #
    # Under Managed Payments, Stripe -- not us -- is liable for sales tax and
    # VAT, and it handles fraud, disputes, invoicing and receipts, for an extra
    # fee per transaction. On our side it is one parameter on the Checkout
    # Session; none of the parameters Managed Payments forbids are ones this
    # hub sends, so nothing else changes.
    #
    # It ships OFF and must stay off until Managed Payments is enabled AND its
    # terms of service are accepted in the Stripe dashboard. Sending the flag
    # to an account that has not done both is an error from Stripe, i.e. a
    # customer who cannot check out -- so this is deliberately a switch someone
    # throws after confirming the dashboard side, not a default.
    #
    # Note for later: Stripe can only put a NEW subscription under Managed
    # Payments. Flipping this does not move anyone who already subscribed, in
    # either direction, so a change here splits the customer base rather than
    # migrating it.
    stripe_managed_payments: bool = False

    # Which billing surface the product offers.
    #   "trial_only" -- the default: the free trial is the only thing on
    #     offer. The Billing page shows one card, checkout and the portal
    #     refuse, and the public site quotes no prices. The trial itself is
    #     unchanged: still `plans.TRIAL_DAYS`, still pauses at the end.
    #   "plans" -- the paid tiers in `plans.py` are offered too, and Stripe
    #     keys are expected.
    # Switching to "plans" is a setting, not a deploy: `plans.py` keeps the
    # whole catalogue either way.
    billing_mode: str = "trial_only"

    def trial_only(self) -> bool:
        return self.billing_mode.strip().lower() == "trial_only"

    def billing_configured(self) -> bool:
        """True when checkout/portal calls can actually reach Stripe."""
        return bool(self.stripe_secret_key.strip())

    def managed_payments(self) -> bool:
        """True when Stripe acts as merchant of record for new subscriptions."""
        return bool(self.stripe_managed_payments)

    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'guardian.db').resolve()}"

settings = Settings()

if settings.web_dist is None:
    # Default location: a "web/dist" build output. Preferred: the top-level
    # web/ package (repo_root/web/dist, built by `npm run build` in web/).
    # Fallback: a legacy hub/web/dist location, when present.
    for _candidate in (
        Path(__file__).resolve().parents[2] / "web" / "dist",
        Path(__file__).resolve().parent.parent / "web" / "dist",
    ):
        if _candidate.exists():
            settings.web_dist = _candidate
            break
