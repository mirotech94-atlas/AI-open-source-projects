from pydantic import BaseModel, Field, field_validator

class AuthStart(BaseModel): username: str = Field(min_length=1, max_length=64)
class AuthVerify(BaseModel):
    username: str; code: str = Field(min_length=6, max_length=6); remember: bool = False
class TenantOut(BaseModel): id: str; slug: str; name: str; role: str
class UserOut(BaseModel): username: str; display_name: str; is_superadmin: bool
# `chat_providers` is the configured chat provider ladder, in the order
# `chat/providers.chat()` walks it (e.g. ["groq", "anthropic", "rules"]) --
# the chat panel's header caption renders it as "via groq -> anthropic ->
# rules". Names only: never a key, a key fragment or a model name. It rides
# on /me rather than a dashboard payload because it is process-level config
# that cannot change between polls, and /me is fetched once per session.
class MeOut(BaseModel):
    user: UserOut; tenants: list[TenantOut]; active_tenant: TenantOut | None = None
    chat_providers: list[str] = Field(default_factory=list)
class SwitchTenant(BaseModel): tenant_id: str


# --- public-site lead capture ------------------------------------------------
# These live here rather than in `schemas_dash.py` on purpose: `scripts/
# check_types.py` requires every schemas_dash model to have a mirrored
# `export type` in web/src/types.ts, and its own docstring reserves this file
# for schemas mirrored in `web/src/api.ts` instead. A lead is not a dashboard
# payload -- it is one unauthenticated form post and one superadmin-only list.

class LeadIn(BaseModel):
    """The public form's body. Every field is capped here, which is what makes
    an oversized submission a 422 at parse time -- before it can reach the DB,
    and before it can even spend a rate-limit slot.

    `website` is the honeypot: the form renders it off-screen, `aria-hidden`
    and `tabindex="-1"`, so no person and no screen reader can fill it in. A
    request that arrives with it non-empty is a bot, and `POST /api/v1/public/
    lead` drops it (see that route for what the bot is told)."""
    name: str = Field(min_length=1, max_length=120)
    # `max_length=200` stays generous on the field itself so a too-long or
    # malformed address fails the validator below (whose message explains
    # what is wrong) rather than a bare Pydantic length error. 64 is
    # `User.username`'s own cap (`String(64)`, and `AuthStart.username`'s
    # `max_length=64`) -- a lead's email becomes that username at approval,
    # so it cannot be longer than what will eventually store it.
    email: str = Field(min_length=3, max_length=200)
    company: str = Field(default="", max_length=120)
    network_size: str = Field(default="", max_length=32)
    site_name: str = Field(default="", max_length=120)
    # The sign-up page's "I agree to the Terms of Service". Refused (422) when
    # false: see `routers/leads.py::submit_lead`.
    accept_terms: bool = False
    note: str = Field(default="", max_length=2000)
    website: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        at = v.find("@")
        if (
            at <= 0
            or "." not in v[at + 1:]
            or any(c.isspace() for c in v)
            or "," in v
            or len(v) > 64
        ):
            raise ValueError("enter a valid email address")
        return v


class LeadOut(BaseModel):
    """What the public route answers with. Deliberately a fixed shape with no
    field carrying anything the submitter typed: nothing they sent is ever
    reflected back into the page."""
    ok: bool = True
    message: str = "Thanks, that is with us."


class LeadRow(BaseModel):
    """One lead, for the superadmin-only list."""
    id: str
    name: str
    email: str
    company: str
    network_size: str
    note: str
    ip: str
    created: str
    site_name: str = ""
    status: str = "pending"
    decided_at: str = ""
    decided_by: str = ""
    tenant_slug: str = ""


class LeadsOut(BaseModel):
    leads: list[LeadRow]


class LeadApproveIn(BaseModel):
    """What the owner confirmed in the approve dialog. Empty = derive from the
    lead (site name, then company, then "<Name>'s network"; email as username)."""
    tenant_name: str = Field(default="", max_length=120)
    slug: str = Field(default="", max_length=64)
    username: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=120)

    @field_validator("username")
    @classmethod
    def _no_spaces(cls, v: str) -> str:
        if any(c.isspace() for c in v.strip()):
            raise ValueError("username must not contain spaces")
        return v


class PublicFeedRow(BaseModel):
    """One line in the marketing site's live panel.

    Every field is composed by `routers/public_feed.py` from an allow-list --
    none of it is a text column copied out of the database. `target` is a
    class of thing ("A laptop", "A monitored server"), never an identity.
    """
    sev: str        # crit | warn | ok | info -- drives the badge and the dot
    kind: str       # site | cert | dns | fix | device | port
    title: str      # what happened, e.g. "A port opened"
    target: str     # what it happened to, generically
    detail: str     # one sentence of substance
    ts: str         # ISO 8601, UTC


class PublicFeedOut(BaseModel):
    """`GET /api/v1/public/feed`.

    `rows` is newest-first and capped at `public_feed.ROWS`; an empty list is
    a valid answer (a hub with no demo tenant) and the page renders its own
    explanatory line for it rather than an empty box. `last_scan` is when the
    demo sensor last reported -- the page's "last scan N minutes ago" stamp --
    and is null when it never has.
    """
    rows: list[PublicFeedRow]
    last_scan: str | None = None
    generated: str


# --- billing (task 68) -------------------------------------------------------
# Request/response bodies for the checkout and portal routes. Here rather than
# in `schemas_dash.py` for the reason the lead schemas above give: these are
# mirrored in `web/src/lib/api.ts`, not in `web/src/types.ts`, and adding them
# to the dashboard module would demand `export type` entries that would then
# have to be kept field-for-field in sync for no benefit.

class CheckoutIn(BaseModel):
    """Start a Stripe Checkout for one plan at one interval.

    `plan` and `interval` are validated against `guardian_hub.plans` in the
    route, not here, so the error the caller gets names the plans that exist.
    `quantity` is the site count for the per-site plans; capped so a typo
    cannot open a checkout for ten thousand sites."""
    plan: str = Field(min_length=1, max_length=64)
    interval: str = Field(default="month", max_length=8)
    quantity: int = Field(default=1, ge=1, le=500)


class CheckoutOut(BaseModel):
    """Where to send the browser. Stripe's hosted page -- this hub never
    renders a card field, so no card data ever touches it."""
    url: str


class PortalOut(BaseModel):
    """Where to send the browser for Stripe's customer portal: change plan,
    update the card, cancel. Cancelling happens there rather than here so
    there is one authority on subscription state, and it is Stripe's."""
    url: str
