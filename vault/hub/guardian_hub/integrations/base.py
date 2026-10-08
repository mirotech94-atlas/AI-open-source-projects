"""The provider interface every integration kind implements.

`Integration` is a small ABC-by-convention (no `abc.ABC` — subclasses are
plain classes registered as singleton instances in `registry.REGISTRY`, and
tests routinely monkeypatch a single method on that instance). A provider
declares its own `fields` (the form the Integrations tab renders) and
implements `send`/`test`/`inbound`/`watchdog` as needed; every method has a
safe default so a phase-1, outbound-only provider only has to implement
`send`.

Field-binding convention: `fields[0]` is always backed by
`Integration.secret` (Fernet-encrypted at rest — a topic, webhook URL, or
bot token). Fix round 1, ruling (a): EVERY other `kind="secret"` field is
*also* encrypted at rest, in `Integration.secrets_enc` — one Fernet blob
holding a `{field_name: plaintext}` JSON dict, so a provider that needs a
second or third credential (Home Assistant's long-lived REST token,
PagerDuty's webhook signing secret, Telegram's `secret_token`) never has to
fall back to the plaintext `config` column. Only genuinely non-secret
fields (chat id, project key, base url, public key, ...) live in
`Integration.config`.

A provider reads its own credentials off `SendContext`: `ctx.secret` is
still `fields[0]`'s plaintext, and `ctx.secrets` is the full
`{field_name: plaintext}` map (including `fields[0]` under its own name).
Never re-decrypt inside a provider.

Reserved config keys (ruling b): `config["_sync"]` is provider bookkeeping
that survives a re-save — the form never writes it and
`upsert_integration` never replaces it. Anything a `watchdog()`/`inbound()`
needs to remember between ticks (the ticket-sync's incident->ticket map,
Telegram's paired `chat_id`) belongs there, reached via `sync_state(ctx)`.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class IntegrationField:
    """One form field on the Integrations tab. `value` is filled in by the
    router when building a response card (and is always masked/omitted for
    a secret-shaped field — see `crypto.py` and `routers/integrations.py`)."""
    name: str
    label: str
    kind: str = "text"          # text|secret|url|select|bool
    required: bool = False
    options: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Message:
    """A notification to deliver. `kind` is one of alert|digest|weekly|
    report|resolve. `dedup_key` is the Vault alert key — PagerDuty and
    the Task 41 ticket sync use it to close their side of a resolved
    alert."""
    title: str
    body: str
    severity: str                # CRIT|WARN|INFO
    url: str = ""
    actions: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    kind: str = "alert"
    dedup_key: str = ""


@dataclasses.dataclass
class SendContext:
    """Everything a provider needs to act: the tenant row, its OWN
    already-decrypted secret (never re-decrypt inside a provider), any
    non-secret per-kind config, the db session (for a watchdog that needs
    to query alerts) and the hub's base_url (for building links).

    `secrets` (fix round 1, ruling a) is the full decrypted
    `{field_name: plaintext}` map for every `kind="secret"` field on this
    provider, `fields[0]` included — a provider with more than one
    credential reads the extra ones from here instead of from `config`.

    `ip` is the calling client's address on an inbound webhook (finding 6),
    so an integration-driven ack lands in the audit log with a real IP the
    way a dashboard ack does. Empty for outbound/watchdog contexts, which
    have no request behind them.
    """
    tenant: object
    secret: str
    config: dict
    db: object
    base_url: str
    secrets: dict = dataclasses.field(default_factory=dict)
    ip: str = ""


@dataclasses.dataclass
class SendResult:
    ok: bool
    error: str = ""
    #: What a SUCCESSFUL test found, for the card to show beside "OK". The
    #: PSA integrations use it to say which id each typed name resolved to;
    #: a provider with nothing to add leaves it empty and the card shows
    #: "OK" alone, as before.
    detail: str = ""


@dataclasses.dataclass
class InboundResult:
    ok: bool
    body: dict = dataclasses.field(default_factory=dict)
    status: int = 200


# Every `direction` literal a provider may declare. `out+status sync` is
# the ticket integrations' (ruling d): outbound alert relay is deliberately
# a no-op, but the provider does sync ticket status back, so it is neither
# plain "out" nor an inbound-token holder.
DIRECTIONS = ("out", "out+in", "out+command", "out+status sync")

INBOUND_DIRECTIONS = ("out+in", "out+command")

# Config keys the Integrations form never writes and `upsert_integration`
# never replaces (ruling b). `_sync` holds provider bookkeeping — the
# ticket-sync's incident->ticket map, Telegram's paired `chat_id`, ntfy's
# escalation hand-off flags — so re-saving an integration can no longer
# wipe it (finding 3: duplicate tickets, silent Telegram unpair).
RESERVED_CONFIG_KEYS = ("_sync",)


def sync_state(ctx: "SendContext") -> dict:
    """The provider's own `config["_sync"]` sub-dict, created on first use.

    Returns a live dict: mutate it in place and the write-back in
    `registry.watchdog_job` / `routers/inbound.py` persists it (both snapshot
    `row.config` with a DEEP copy precisely so a nested mutation here is
    still visible to their compare-and-reassign)."""
    cfg = ctx.config
    state = cfg.get("_sync")
    if not isinstance(state, dict):
        state = {}
        cfg["_sync"] = state
    return state


def wants_inbound_token(direction: str) -> bool:
    """True for a direction that needs an `Integration.inbound_token`
    minted (`routers/integrations.py`'s upsert route) and a card's
    `inbound_url` shown (`_card`) -- `"out+in"` (PagerDuty, Telegram, Home
    Assistant) and `"out+command"` (Discord's `/guardian status` slash
    command). An exact-match set, not a substring check: the old
    `"in" in direction` test happened to work for `"out+in"` but silently
    missed `"out+command"` (no "in" substring in "command") and would have
    also matched a hypothetical direction like "out+status sync" (Task 41's
    ticket-sync integration, which is genuinely outbound-only)."""
    return direction in INBOUND_DIRECTIONS


class Integration:
    """Subclass and decorate with `@registry.register`. Class attributes
    below are the provider's static description; `fields` is the form
    schema (see field-binding convention above)."""

    kind: str = ""
    title: str = ""
    help: str = ""
    direction: str = "out"       # one of DIRECTIONS above
    fields: list[IntegrationField] = []
    supports_routes: bool = True

    #: What a person can SAY to this channel once it is connected, one line
    #: each, in the words they would type. Declared next to the code that
    #: implements it so the two cannot drift: the Integrations page shows
    #: these verbatim, and a channel whose `inbound` handler ignores a
    #: command has no business advertising it here.
    #:
    #: Empty for an outbound-only provider, which is most of them. The page
    #: shows this block only for a channel that is actually CONNECTED --
    #: nine channels' worth of commands for things a tenant has not set up
    #: is a catalogue, not help.
    commands: list[str] = []

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        raise NotImplementedError

    def test(self, ctx: SendContext) -> SendResult:
        """Default: send a real message through `send()` so the Test
        button exercises the exact same code path as a live alert."""
        return self.send(ctx, Message(
            title="Vault test", body="This is a test notification from Vault.",
            severity="INFO", kind="alert",
        ))

    def inbound(self, ctx: SendContext, request_body: dict, headers: dict) -> InboundResult:
        """Handle a webhook call FROM the provider (button press, reply,
        ticket-status change). Phase-1 providers are outbound-only."""
        return InboundResult(ok=True, body={}, status=200)

    def watchdog(self, ctx: SendContext) -> "SendResult | None":
        """Periodic duty (hourly, via `registry.watchdog_job`) so each
        integration "does a job, not just relays text" (spec §10.1).
        Default: no-op.

        Return a failed `SendResult` to record `Integration.last_error`
        rather than raising (finding 9): an exception aborts the tick's
        `ctx.config` write-back path, so a provider that both remembers
        state and can fail on the network must report the failure as a
        value. `registry.watchdog_job` writes `ctx.config` back either way,
        but a raise still costs the provider the chance to say what broke
        without losing whatever it had already recorded."""
        return None

    def validate(self, fields: dict) -> str:
        """Return a non-empty error string if the raw (pre-encryption)
        `fields` dict fails this provider's own validation, else ''."""
        return ""
