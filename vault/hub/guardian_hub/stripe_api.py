"""The whole of Vault's contact with Stripe: HTTP calls out, signature
verification on the way in. Deliberately small, and deliberately not the
Stripe SDK.

Why no `stripe` package: the one security-critical piece here -- verifying a
webhook signature -- has to be written and tested against the documented
scheme either way, `httpx` is already a dependency, and the three REST calls
this product makes (create a checkout session, create a portal session, list
prices by lookup key) are each one form POST/GET. A dependency that can only
be exercised with live credentials is a dependency that never gets tested in
CI; these functions are one monkeypatch away from being fully covered.

Nothing in this module logs a key, a key fragment, or a signing secret. When
a Stripe call fails, the message that escapes is Stripe's own `error.message`
(safe: it is written for end users) or a generic string -- never the request
that was sent, because that request carries the Authorization header.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from .config import settings


class StripeNotConfigured(RuntimeError):
    """No secret key. The caller turns this into a 503, never a 500."""


class StripeError(RuntimeError):
    """Stripe answered, and said no."""


class SignatureError(ValueError):
    """A webhook whose signature did not verify. Never applied, never stored."""


# --------------------------------------------------------------------------
# Outbound
# --------------------------------------------------------------------------

#: The API version every call is pinned to. Pinned rather than floating so a
#: Stripe release cannot change a response shape under a running hub.
API_VERSION = "2024-06-20"

#: The version every Checkout Session is created under, whether or not this
#: hub sends `managed_payments`. It used to be sent only WITH the flag, on the
#: theory that only a request carrying the parameter needed a version that
#: knew it. Stripe enforces it at the account level instead: once an account
#: has accepted Managed Payments, it refuses ANY session created under a
#: pre-basil version, parameter or no parameter -- reproduced 2026-09-21
#: against the sandbox account with two identical, flag-less requests
#: ("Managed Payments is not supported on API version 2024-06-20" vs a
#: session). Both Vault accounts have accepted it, so with the flag off
#: every Upgrade click was a 502 that `billing-check` could not see.
#:
#: Only this one call moves forward. The rest of the integration keeps
#: `API_VERSION`: a blanket bump would change the response shape of every
#: other Stripe call -- prices, the portal, the webhook payloads -- for no
#: reason connected to checkout.
CHECKOUT_API_VERSION = "2025-03-31.basil"


def _request(method: str, path: str, data: dict[str, Any] | None = None,
             *, idempotency_key: str = "", api_version: str = "") -> dict:
    if not settings.billing_configured():
        raise StripeNotConfigured("Stripe is not configured on this hub")
    headers = {
        "Authorization": f"Bearer {settings.stripe_secret_key.strip()}",
        "Stripe-Version": api_version or API_VERSION,
    }
    if idempotency_key:
        # Stripe dedupes writes carrying the same key for 24h. A customer who
        # double-clicks "Upgrade" gets one checkout session, not two.
        headers["Idempotency-Key"] = idempotency_key
    url = f"{settings.stripe_api_base.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=20.0) as c:
            r = c.request(method, url, headers=headers,
                          data=_flatten(data or {}) if method != "GET" else None,
                          params=_flatten(data or {}) if method == "GET" else None)
    except httpx.HTTPError as exc:
        # `exc` stringifies to the URL and the transport error, never a header.
        raise StripeError(f"could not reach Stripe: {type(exc).__name__}") from None
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error") or {}).get("message") or ""
        except ValueError:
            msg = ""
        raise StripeError(msg or f"Stripe returned {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise StripeError("Stripe returned a body that was not JSON") from None


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Stripe's form encoding: nested dicts/lists become `a[b][0]` keys."""
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.update(_flatten(item, f"{key}[{i}]"))
                else:
                    out[f"{key}[{i}]"] = _scalar(item)
        elif v is not None:
            out[key] = _scalar(v)
    return out


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def price_for_lookup_key(lookup_key: str) -> dict:
    """Resolve a `plans.Plan.lookup_key()` to the whole Stripe price object.

    Lookup keys rather than price ids pasted into the environment: the owner
    creates the prices once in the dashboard with these keys and there is
    nothing to redeploy afterwards. `active=true` so an archived price from a
    previous pricing round is never resurrected.

    This is the ONE place that knows how a lookup key is turned into a price,
    and it has to stay that way. Checkout wants only the id; `guardian
    billing-check` wants the amount, the currency and the recurring interval.
    If the two grew separate queries, the check could pass against a price
    that checkout would never have selected -- which is precisely the failure
    it exists to rule out.
    """
    body = _request("GET", "/v1/prices", {"lookup_keys": [lookup_key], "active": True, "limit": 1})
    rows = body.get("data") or []
    if not rows:
        raise StripeError(
            f"no active Stripe price with lookup key '{lookup_key}' -- create it "
            "in the Stripe dashboard before offering this plan"
        )
    return rows[0]


def price_id_for_lookup_key(lookup_key: str) -> str:
    """The price id for a lookup key -- what a checkout line item carries."""
    return str(price_for_lookup_key(lookup_key)["id"])


def create_checkout_session(*, price_id: str, quantity: int, tenant_id: str,
                            customer_id: str, customer_email: str,
                            success_url: str, cancel_url: str,
                            idempotency_key: str = "", probe: bool = False) -> dict:
    """A hosted Stripe Checkout session for one subscription.

    `client_reference_id` and `subscription_data.metadata.tenant_id` both
    carry the tenant, so the webhook can find the account from either the
    session or the subscription object -- Stripe does not put the session's
    reference on later `customer.subscription.*` events.
    """
    data: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": max(1, quantity)}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": tenant_id,
        "metadata": {"tenant_id": tenant_id},
        "subscription_data": {"metadata": {"tenant_id": tenant_id}},
        "allow_promotion_codes": True,
    }
    if probe:
        # `billing-check`'s session: created to prove the account will make
        # one, then expired at once. Tagged so it can never be mistaken for a
        # customer's in the dashboard, and given Stripe's shortest allowed
        # lifetime in case the expire call after it fails.
        data["metadata"] = {**data["metadata"], "probe": "billing-check"}
        data["expires_at"] = int(time.time()) + 30 * 60 + 5
    if settings.managed_payments():
        # Stripe becomes the merchant of record for this subscription: it
        # calculates and withholds the sales tax, and owns the dispute and the
        # receipt. Everything else in `data` survives the switch -- the
        # parameters Managed Payments forbids are all ones this hub does not
        # send (automatic_tax, tax_id_collection, the shipping and Connect
        # fields, invoice_creation, subscription_data.invoice_settings).
        #
        # `subscription_data.metadata` in particular is NOT forbidden, which is
        # the part that matters: it carries `tenant_id` onto the subscription,
        # and `billing.apply_event` reads it to find the account on every later
        # `customer.subscription.*` event, which do not carry the session's
        # `client_reference_id`. Losing it would leave paid subscriptions that
        # belong to nobody.
        data["managed_payments"] = {"enabled": True}
    if customer_id:
        data["customer"] = customer_id
    elif customer_email:
        data["customer_email"] = customer_email
    # The caller's key names a SCOPE -- the route sends tenant, plan, interval
    # and the UTC hour, so a double-click yields one session rather than two.
    # A digest of what is actually sent is appended, because Stripe holds a
    # key to the exact parameters it was first used with: reuse it with a
    # different body and the answer is "Keys for idempotent requests can only
    # be used with the same parameters they were first used with", not a
    # session. Reproduced 2026-09-21 -- one attempt failed on the API version,
    # the config was corrected, and the retry under the same scope was refused
    # for the rest of the hour. With the digest, an identical retry is still
    # deduplicated and a changed one gets a key of its own.
    if idempotency_key:
        idempotency_key = f"{idempotency_key}:{_request_digest(data, CHECKOUT_API_VERSION)}"
    return _request("POST", "/v1/checkout/sessions", data,
                    idempotency_key=idempotency_key,
                    api_version=CHECKOUT_API_VERSION)


def _request_digest(data: dict[str, Any], api_version: str) -> str:
    """A short, stable fingerprint of a request as Stripe will see it: the
    form-encoded body in key order, plus the API version, since the version
    changes what the same body means."""
    flat = _flatten(data)
    material = "\n".join(f"{k}={flat[k]}" for k in sorted(flat)) + f"\n@{api_version}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def expire_checkout_session(session_id: str) -> dict:
    """End a session early so it can never be completed. Used by
    `billing-check`, whose probe session must not sit on the account waiting
    for someone to pay it; harmless on a session that already expired."""
    return _request("POST", f"/v1/checkout/sessions/{session_id}/expire", {},
                    api_version=CHECKOUT_API_VERSION)


def create_portal_session(*, customer_id: str, return_url: str) -> dict:
    """Stripe's own customer portal: change plan, update card, cancel.

    Everything a customer does to their subscription after the first purchase
    happens here rather than in Vault, so this hub never sees a card
    number and never has to build a plan-change flow of its own.
    """
    return _request("POST", "/v1/billing_portal/sessions",
                    {"customer": customer_id, "return_url": return_url})


# --------------------------------------------------------------------------
# Inbound: webhook signature verification
# --------------------------------------------------------------------------

def verify_signature(payload: bytes, sig_header: str, *, secret: str = "",
                     tolerance: int | None = None, now: float | None = None) -> dict:
    """Verify a `Stripe-Signature` header against the raw body, then parse it.

    Stripe's documented scheme, implemented exactly:

        Stripe-Signature: t=1712345678,v1=<hex>,v1=<hex>

    the signed payload is ``f"{t}.{raw_body}"``, the MAC is HMAC-SHA256 under
    the endpoint's signing secret, and a header may carry SEVERAL `v1`
    signatures -- during a secret rotation Stripe signs with both -- so any
    one matching is enough.

    Non-negotiables, each of which is a real attack if dropped:

    * The MAC is computed over the **raw bytes**, which is why the route
      hands us `await request.body()` and declares no pydantic body model.
      A re-serialized dict differs from what Stripe signed (key order,
      separators) and the MAC would never match.
    * `hmac.compare_digest`, so a wrong signature cannot be recovered a byte
      at a time from response timing.
    * A timestamp outside `tolerance` is rejected: without it, one captured
      request is replayable forever.
    * **No secret configured means reject.** This route is unauthenticated
      and internet-facing; "billing is not set up" must not degrade into
      "anyone may post subscription changes".
    """
    secret = (secret or settings.stripe_webhook_secret).strip()
    if not secret:
        raise SignatureError("webhook signing secret is not configured")
    if not sig_header:
        raise SignatureError("missing Stripe-Signature header")

    ts = ""
    sigs: list[str] = []
    for part in sig_header.split(","):
        k, _, v = part.strip().partition("=")
        if k == "t":
            ts = v.strip()
        elif k == "v1":
            sigs.append(v.strip())
    if not ts or not sigs:
        raise SignatureError("malformed Stripe-Signature header")
    try:
        sent = int(ts)
    except ValueError:
        raise SignatureError("malformed Stripe-Signature timestamp") from None

    tol = settings.stripe_webhook_tolerance if tolerance is None else tolerance
    if tol > 0 and abs((time.time() if now is None else now) - sent) > tol:
        raise SignatureError("Stripe-Signature timestamp is outside the tolerance window")

    expected = hmac.new(secret.encode(), b"%d." % sent + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError("Stripe-Signature does not match the request body")

    try:
        event = json.loads(payload.decode())
    except (ValueError, UnicodeDecodeError):
        raise SignatureError("webhook body is not JSON") from None
    if not isinstance(event, dict):
        raise SignatureError("webhook body is not a JSON object")
    return event


def sign_payload(payload: bytes, secret: str, timestamp: int) -> str:
    """Build a valid `Stripe-Signature` header. Test helper -- the only
    caller outside tests would be a local replay tool, and it takes the
    secret as an argument so nothing reads a configured one to sign with."""
    mac = hmac.new(secret.encode(), b"%d." % timestamp + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"
