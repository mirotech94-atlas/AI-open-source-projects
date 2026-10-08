"""Platform settings the owner can change from the console, without a deploy.

Why this module exists: `config.Settings` is built once from the environment
at import time, so anything read through it can only be changed by editing
`.env` and restarting the service. `config.billing_mode`'s own docstring
already claimed that switching it was "a setting, not a deploy", and that was
not true of anything until this table existed.

Three rules it keeps.

**One accessor, and the DB wins.** Every editable key is read through the
functions here -- never `config.settings.<key>` -- so an edit takes effect
everywhere or nowhere. A key with no stored row falls back to its
environment/code default, which is what a fresh install runs on.

**Only keys that actually take effect are editable.** A setting the product
reads once at import time and then ignores is worse than a read-only one,
because the owner watches it save and nothing happens. Every key in `SPEC`
was checked for a module-level read before it was added: the two that had one
(`billing.TRIAL_WARN_DAYS` as a default argument, and
`payloads.platform.ENDING_SOON_DAYS`) were converted to call this.

**Secrets are never writable here.** Credentials belong in the environment,
where they are not in a database that gets backed up, exported and copied to
laptops. The console shows whether each one is set; that is all.

Caching: the values live in a process-level dict with a short TTL, because
they are read on hot paths (every billing banner) and the hub runs
`--workers 1` (see `deploy/guardian-hub.service`), so one process owns them.
A write refreshes the cache immediately; the TTL only matters for a change
made by some other process, such as the CLI.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session as DbSession

from . import models as m, plans
from .config import settings

#: How long a cached value is trusted before it is re-read. Small enough that
#: a change made outside this process is picked up promptly, large enough that
#: a banner render does not become a query.
CACHE_TTL_S = 20


@dataclass(frozen=True)
class Spec:
    """One editable setting: what it is, what it may be, and what it changes.

    `help` is written for the person clicking it, and it says what the change
    DOES, including what it does not do -- `trial_days` is the example that
    matters, because "90 days" could plausibly mean "and every trial already
    running now ends 90 days after it started", and it does not.
    """
    key: str
    label: str
    help: str
    kind: str                       # int | text | choice | bool
    default: Callable[[], Any]
    group: str = "General"
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()


SPEC: dict[str, Spec] = {
    "billing_mode": Spec(
        key="billing_mode", label="What the product offers", group="Billing",
        kind="choice", choices=("trial_only", "plans"),
        default=lambda: settings.billing_mode,
        help="Trial only: the free trial is the only thing on offer, checkout is "
             "refused and the public site quotes no prices. Plans: the paid tiers "
             "are offered too, which needs Stripe keys in the environment.",
    ),
    "trial_days": Spec(
        key="trial_days", label="Trial length (days)", group="Billing",
        kind="int", minimum=7, maximum=365,
        default=lambda: plans.TRIAL_DAYS,
        help="How long a NEW trial runs. Trials already under way keep the end "
             "date they were given -- their dates are stored on the account, so "
             "changing this never moves a date a customer has already been told.",
    ),
    "trial_warn_days": Spec(
        key="trial_warn_days", label="Warn before the trial ends (days)", group="Billing",
        kind="int", minimum=1, maximum=60,
        default=lambda: 14,
        help="How far ahead the in-app banner and the warning letter start. "
             "Applies to every live trial immediately, including ones already "
             "inside the old window.",
    ),
    "auto_heal": Spec(
        key="auto_heal", label="Let Vault repair without asking", group="Operations",
        kind="bool",
        default=lambda: True,
        help="On: a repair Vault is allowed to make on its own runs the moment "
             "it is needed -- restarting a service that died, clearing a full disk, "
             "blocking an address that is attacking. Off: every one of those waits "
             "for a person to say yes, on every customer, and nothing is repaired "
             "while you sleep. Turning this off does not stop Vault WATCHING, "
             "and it does not cancel a repair you have already approved.",
    ),
    "admin_mirror_floor": Spec(
        key="admin_mirror_floor", label="Mirror customer alerts to your Slack", group="Support",
        # Lowercase because `set_value` lower-cases every choice before it
        # validates (see the "choice" branch); the accessor normalises back.
        kind="choice", choices=("info", "warn", "crit", "off"),
        default=lambda: "info",
        help="Every customer's alerts, fixes and reports are copied into your own "
             "admin Slack channel at this severity and above, so you see what they "
             "see without joining their channels. This is a COPY -- their own "
             "channels are unaffected. Set it to off, or raise it, when the "
             "channel gets noisy.",
    ),
    "support_email": Spec(
        key="support_email", label="Support mailbox", group="Support",
        kind="text",
        default=lambda: settings.support_email,
        help="Where a customer's ticket is emailed, and the address the app names "
             "as the way to reach you. It must be a mailbox you read: Vault "
             "does not receive mail into the app, so replies land here, not in a thread.",
    ),
    "support_direct_email": Spec(
        key="support_direct_email", label="Direct line to you (email)", group="Support",
        kind="text",
        default=lambda: "",
        help="Shown to customers under 'If it is urgent' as the way to reach a "
             "person directly, beside the support mailbox. Empty hides the block. "
             "There is no phone line by design: a number that rings out during an "
             "outage is worse than no number.",
    ),
    "support_reply_sla": Spec(
        key="support_reply_sla", label="Typical reply time", group="Support",
        kind="text",
        default=lambda: "",
        help="One short phrase, e.g. 'under 2 hours, weekdays'. Shown beside "
             "the message box. Empty means Vault promises nothing, which is "
             "the safer default.",
    ),
    "email_reputation_note": Spec(
        key="email_reputation_note", label="Email reputation provider", group="Support",
        kind="text",
        default=lambda: "",
        help="Naming a provider here (e.g. 'EmailRep.io') turns the email checker's "
             "third-party lookup on and says so to the reader. EMPTY IS THE DEFAULT "
             "AND MEANS OFF: the address being checked then never leaves this hub, "
             "and the checker says it has asked nobody. Switching it on sends your "
             "customers' correspondents to somebody else's server — a real "
             "disclosure, and one for the person who owns the data to choose.",
    ),
}

#: Credentials and wiring the console REPORTS but never writes. The value is
#: the accessor used to decide "set or not set" -- never the secret itself,
#: which does not leave the process.
SECRETS: dict[str, tuple[str, Callable[[], bool]]] = {
    "smtp": ("Outbound email (SMTP)", lambda: bool(settings.smtp_host.strip())),
    "stripe": ("Stripe", lambda: bool(settings.stripe_secret_key.strip())),
    "stripe_webhook": ("Stripe webhook signing", lambda: bool(settings.stripe_webhook_secret.strip())),
    "groq": ("Groq (chat, first rung)", lambda: bool(settings.groq_api_key.strip())),
    "anthropic": ("Anthropic (chat, second rung)", lambda: bool(settings.anthropic_api_key.strip())),
    "slack_bot": ("Slack bot", lambda: bool(settings.slack_bot_token.strip())),
    "slack_signing": ("Slack request signing", lambda: bool(settings.slack_signing_secret.strip())),
    "metrics_token": ("Metrics endpoint token", lambda: bool(settings.metrics_token.strip())),
}

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_loaded_at: dt.datetime | None = None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _coerce(spec: Spec, raw: str) -> Any:
    if spec.kind == "int":
        return int(raw)
    if spec.kind == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw


def _load(db: DbSession | None = None) -> dict[str, Any]:
    """Read every stored row into the cache. Rows for unknown keys (a setting
    that existed in an older build) are ignored rather than crashing."""
    global _loaded_at
    own = db is None
    if own:
        from .db import SessionLocal
        db = SessionLocal()
    try:
        vals: dict[str, Any] = {}
        for row in db.query(m.PlatformSetting).all():
            spec = SPEC.get(row.key)
            if spec is None:
                continue
            try:
                vals[row.key] = _coerce(spec, row.value)
            except (TypeError, ValueError):
                continue
    finally:
        if own:
            db.close()
    with _lock:
        _cache.clear()
        _cache.update(vals)
        _loaded_at = _now()
    return vals


def refresh(db: DbSession | None = None) -> None:
    """Re-read now. Called after every write so the change is live at once."""
    _load(db)


def _fresh() -> dict[str, Any]:
    if _loaded_at is None or (_now() - _loaded_at).total_seconds() > CACHE_TTL_S:
        try:
            return _load()
        except Exception:  # noqa: BLE001
            # A settings read must never be the thing that breaks a page. An
            # unreadable table means "run on the defaults", which is what the
            # product did before this table existed.
            with _lock:
                return dict(_cache)
    with _lock:
        return dict(_cache)


def value(key: str) -> Any:
    """The live value of one setting: the stored one, else its default."""
    spec = SPEC[key]
    got = _fresh().get(key)
    return spec.default() if got is None else got


def set_value(db: DbSession, key: str, raw: Any, *, by: str) -> Any:
    """Validate and store one setting. Returns the stored value.

    Raises `ValueError` with a sentence fit to show the person who typed it --
    the caller turns that into a 422.
    """
    spec = SPEC.get(key)
    if spec is None:
        raise ValueError("That is not a setting this console can change")
    if spec.kind == "int":
        try:
            val: Any = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{spec.label} must be a whole number")
        if spec.minimum is not None and val < spec.minimum:
            raise ValueError(f"{spec.label} must be at least {spec.minimum}")
        if spec.maximum is not None and val > spec.maximum:
            raise ValueError(f"{spec.label} must be at most {spec.maximum}")
    elif spec.kind == "choice":
        val = str(raw).strip().lower()
        if val not in spec.choices:
            raise ValueError(f"{spec.label} must be one of: {', '.join(spec.choices)}")
    elif spec.kind == "bool":
        val = str(raw).strip().lower() in ("1", "true", "yes", "on")
    else:
        val = " ".join(str(raw).split())
        if not val:
            raise ValueError(f"{spec.label} cannot be empty")

    row = db.query(m.PlatformSetting).filter_by(key=key).one_or_none()
    if row is None:
        row = m.PlatformSetting(key=key)
        db.add(row)
    row.value = str(val)
    row.updated = _now()
    row.updated_by = by[:64]
    db.commit()
    refresh(db)
    return val


# --------------------------------------------------------------------------
# The named accessors. Every caller uses one of these rather than `value()`,
# so a rename is a compiler-visible change instead of a string hunt.
# --------------------------------------------------------------------------

def billing_mode() -> str:
    return str(value("billing_mode"))


def auto_heal() -> bool:
    """May Vault run a repair on its own, anywhere on the platform?

    The kill switch, and the only setting here that changes what Vault DOES
    rather than what it offers or says. Off means every automatic repair, on
    every customer, becomes one that waits for a person -- see
    `chat/tiers.effective_tier`, which is the single place it is enforced.
    """
    return bool(value("auto_heal"))


def trial_only() -> bool:
    return billing_mode().strip().lower() == "trial_only"


def trial_days() -> int:
    return int(value("trial_days"))


def trial_warn_days() -> int:
    return int(value("trial_warn_days"))


def support_email() -> str:
    return str(value("support_email") or "").strip()


def support_direct_email() -> str:
    return str(value("support_direct_email") or "").strip()


def support_reply_sla() -> str:
    return str(value("support_reply_sla") or "").strip()


def email_reputation_note() -> str:
    """A sentence naming the reputation provider in use, or "" for none.

    Off until an operator sets it, and the checker says so in its own
    limits rather than quietly implying it asked somebody. Sending a
    customer's correspondents to a third-party reputation API is a real
    disclosure — a security product does not make that choice on their
    behalf, it offers it.
    """
    return str(value("email_reputation_note") or "").strip()


def admin_mirror_floor() -> str:
    """INFO | WARN | CRIT | off -- how much of the customer stream is copied
    into the operator's own Slack channel.

    Stored lower-case (the choice validator lower-cases), returned in the
    same case the severities themselves use, so a caller can compare it
    against an alert's `sev` without thinking about it."""
    raw = str(value("admin_mirror_floor") or "info").strip().lower()
    return "off" if raw == "off" else raw.upper()
