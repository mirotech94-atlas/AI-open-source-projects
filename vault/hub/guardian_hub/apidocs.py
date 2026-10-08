"""The generated API reference — one document, three renderings.

FastAPI already knows the shape of every `/v1` route. This module turns that
knowledge into:

1. `document()` — an OpenAPI 3.1 document covering **only** the `/v1` routes.
2. `reference_html()` — a self-contained HTML reference page rendered from that
   same document, served at `/v1/docs`.
3. `guide()` — the payload the app's in-app API-guide section renders, also
   built from that same document.

Because all three read one source, the reference on the API host and the guide
inside the app cannot describe different APIs. Adding a route to
`routers/api_v1.py` updates every one of them; there is no second list to keep
in step.

Why a pruned document rather than `app.openapi()`
-------------------------------------------------
Two reasons, and the first is a leak.

The dashboard's own routes carry internal engineering notes in their docstrings
— file paths, contract notes for the sensor's edit replay, references to
internal task numbers. `app.openapi()` publishes every one of those verbatim.
A public document must contain only copy written to be read by a customer, so
the document is generated from the `/v1` router's routes alone, and its
components therefore contain only the models those routes actually use.

The second is simply that a customer reading api.guarder.io should see the API
they can call, not the app's internal surface alongside it.

Why the reference page is rendered here rather than loading Swagger UI
---------------------------------------------------------------------
Swagger UI is fetched from a CDN, which would mean the API host's
Content-Security-Policy had to permit third-party scripts on the one origin
whose whole job is handling customer credentials. The page below reaches
nothing outside this origin: its CSS is inline, its two faces come from
`/v1/fonts/`, and its one script comes from `/v1/docs.js`. The vhost keeps
`default-src 'self'` with `script-src 'none'` for API traffic, and relaxes only
the two documentation paths to `script-src 'self'` -- no inline allowance, no
nonce, no third party. See the note above `CONSOLE_JS`.
"""

import datetime as dt
import html
import ipaddress
import json

from fastapi.openapi.utils import get_openapi

from . import apikeys, apideps
from .config import settings

API_VERSION = "v1"
API_TITLE = "Vault API"

#: Public base URL of the API host. Derived from the app's own `base_url` by
#: swapping the leading label for "api", so a deployment that renames its app
#: hostname gets a consistent API hostname without a second setting. Falls back
#: to the app's own origin (with the `/v1` prefix still correct) when the host
#: is a bare name or an address, which is what a local dev hub looks like.
def base_url() -> str:
    raw = (settings.base_url or "").rstrip("/")
    if not raw:
        return ""
    scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    host, _, path = rest.partition("/")
    hostname, _, port = host.partition(":")
    # An address literal, or a single-label name, has no public API hostname to
    # derive: a dev hub on 127.0.0.1 or localhost keeps its own origin, and the
    # examples in the guide then point somewhere that actually answers.
    try:
        ipaddress.ip_address(hostname)
        return raw
    except ValueError:
        pass
    labels = hostname.split(".")
    if len(labels) < 2:
        return raw
    # `my.guarder.io` -> `api.guarder.io`; `guarder.io` -> `api.guarder.io`.
    hostname = ".".join(["api"] + (labels[1:] if len(labels) >= 3 else labels))
    return f"{scheme}://{hostname}{':' + port if port else ''}" + (f"/{path}" if path else "")


DESCRIPTION = """
Read your Vault account and act on what it finds, from your own code.

**Authentication.** Every route except `/v1/health` needs a token, sent as
`Authorization: Bearer gapi_...`. Tokens are created by an admin in the
Vault app, under Config → API tokens. The secret is shown once, at creation,
and Vault keeps only a hash of it — if you lose it, revoke it and make
another.

**Scopes.** A token is either `read` (GET only) or `write` (GET plus the write
routes). A token also carries a role — `viewer`, `operator` or `admin` — which
can never exceed the role of the admin who created it. The write routes need
`operator` or better.

**Your account only.** A token is bound to one account. There is no account
parameter anywhere in this API, and an identifier belonging to another account
answers 404 — the same as one that does not exist.

**Rate limit.** 120 requests a minute per token, and 30 writes a minute. Over
either, the API answers `429` with a `Retry-After` header. Back off for that
long rather than retrying immediately.

**Paging.** Every list route takes `limit` and `offset` and answers
`{"items": [...], "total": N, "limit": N, "offset": N}`. `total` counts
everything that matched, so you can size the work before you start.

**Errors.** Non-2xx responses are `{"detail": "what went wrong"}`.
""".strip()

#: What v1 deliberately does not do, and why. Printed in the reference and in
#: the app's guide — a documented refusal, never a silent gap.
#:
#: The dividing line is the app's own tier-3 rule: an action Vault will not
#: take without a human confirming it, one at a time, is not an action a script
#: holding a long-lived credential should be able to take at all.
EXCLUSIONS: list[tuple[str, str]] = [
    (
        "Rebooting or upgrading a server",
        "Vault classes a reboot, a full package upgrade and an SSH policy change as needing "
        "a person to confirm them, one at a time, because each can take a machine off the "
        "network. A long-lived token cannot confirm anything, so these stay in the app.",
    ),
    (
        "Blocking a subnet, or long-banning or rate-limiting an address",
        "Same reason: a wide block can lock you out of your own network, and Vault will not "
        "make one without a person looking at it.",
    ),
    (
        "Running a repair on a host",
        "Repairs are approved per host and per action in the app, where you can see exactly what "
        "will run before it does. The API can tell you what Vault recommends; approving it "
        "is a decision, not a call.",
    ),
    (
        "Isolating or removing a device",
        "Isolating a device cuts it off, and removing one loses its history. Both are reachable "
        "in the app, where they can be undone by someone who can see what happened.",
    ),
    (
        "Changing the DNS allow and block lists",
        "You can read the lists. Changing them changes what every device on the network can "
        "reach, so it is an app action for now — tell us if you need it here.",
    ),
    (
        "Scheduling a reboot window, or changing how Vault responds to attackers",
        "Both set standing policy rather than doing one thing, and standing policy belongs "
        "where the person who owns it can see it.",
    ),
    (
        "Account settings, users, integrations, sensors and billing",
        "Administration is done by a person in the app. A token exists to read your data and "
        "act on findings, never to reconfigure the account that issued it.",
    ),
]

#: Grouped for the app's guide, in the order the guide prints them. A path not
#: matched by any prefix here still appears, under "Other".
_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Getting started", "Check the API is up and confirm what your token can do.",
     ("/v1/health", "/v1/whoami")),
    ("Overview", "The headline picture of the account.", ("/v1/overview",)),
    ("Networks, sites and servers", "What Vault is watching.",
     ("/v1/networks", "/v1/sites", "/v1/hosts")),
    ("Devices", "Everything on the monitored networks.", ("/v1/devices",)),
    ("Findings", "Alerts, incidents and recommendations — and acting on them.",
     ("/v1/alerts", "/v1/incidents", "/v1/recommendations")),
    ("Activity", "What has been happening.", ("/v1/events", "/v1/presence")),
    ("DNS", "Lookups, summaries and the current lists.", ("/v1/dns",)),
    ("Security", "Posture, checks and the network map.", ("/v1/security", "/v1/topology")),
    ("Evidence", "The raw measurements and the repair record behind the figures — enough to "
                 "recompute an availability number yourself and check it against ours.",
     ("/v1/metrics", "/v1/fixes", "/v1/compliance", "/v1/eol", "/v1/sla",
      "/v1/offboarding")),
    # `/v1/alerts/{key}/evidence` groups under Findings with the rest of the
    # alert routes rather than here: a reader looking for it is looking at an
    # alert, not browsing the raw series.
    ("Reports", "Generated reports.", ("/v1/reports",)),
    ("Jobs", "Work handed to the sensor.", ("/v1/jobs", "/v1/scans", "/v1/speedtest")),
]


def _dashboard_component_names() -> set[str]:
    """Component names in the generated document that come from the dashboard's
    own contract module."""
    from . import schemas_dash

    return {n for n in dir(schemas_dash)
            if n[:1].isupper() and hasattr(getattr(schemas_dash, n), "model_fields")}


def _strip_descriptions(node) -> None:
    """Recursively drop every `description` under `node`."""
    if isinstance(node, dict):
        node.pop("description", None)
        for value in node.values():
            _strip_descriptions(value)
    elif isinstance(node, list):
        for value in node:
            _strip_descriptions(value)


def _scrub_internal_prose(doc: dict) -> None:
    """Remove the dashboard models' docstrings from the public document.

    The public API reuses several dashboard payloads verbatim -- reusing them is
    right, because a second copy of those shapes would drift. But their
    docstrings were written for whoever maintains the hub: they name source
    files, sensor contracts, internal task numbers and the product's former
    name. FastAPI turns every one of those into an OpenAPI `description`, so
    without this the customer-facing document would publish them.

    Only components that come from `schemas_dash` are scrubbed. Everything in
    `schemas_api` -- and every `summary`/`description` on a route -- is copy
    written to be read by a customer, and survives untouched.

    Field names and types are unaffected, so the document still describes those
    payloads completely; it simply describes them without commentary. A
    dashboard payload that deserves customer-facing prose gets it by being
    re-declared in `schemas_api`, deliberately.
    """
    schemas = (doc.get("components") or {}).get("schemas") or {}
    dashboard = _dashboard_component_names()
    for name, schema in schemas.items():
        # pydantic + FastAPI split an input/output-shared model into
        # `<Name>-Input` / `<Name>-Output`.
        base = name.split("-")[0]
        if base in dashboard:
            _strip_descriptions(schema)


def _flatten(routes) -> list:
    """Every real route reachable from `app.routes`.

    FastAPI 0.141 wraps an `include_router` call in an `_IncludedRouter` that
    has no `.path` of its own and holds the real routes on `.original_router`,
    so a flat scan of `app.routes` finds nothing. Recursing is version-tolerant:
    it works on both the flat and the wrapped shape, and on a future third one
    as long as the nesting is still reachable through `routes`.
    """
    out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            out.extend(_flatten(inner.routes))
        elif hasattr(route, "routes"):
            out.extend(_flatten(route.routes))
        else:
            out.append(route)
    return out


def _v1_routes(app) -> list:
    prefix = f"/{API_VERSION}"
    return [r for r in _flatten(app.routes)
            if getattr(r, "path", "") == prefix or getattr(r, "path", "").startswith(f"{prefix}/")]


def document(app) -> dict:
    """The OpenAPI document for the public API, and nothing else."""
    doc = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        openapi_version="3.1.0",
        description=DESCRIPTION,
        routes=_v1_routes(app),
    )
    api_base = base_url()
    if api_base:
        doc["servers"] = [{"url": api_base, "description": "Vault API"}]
    # Declared rather than inferred: the routes authenticate in a dependency, so
    # FastAPI has no security scheme to emit on its own, and a reference with no
    # stated auth scheme is a reference nobody can use.
    doc.setdefault("components", {})["securitySchemes"] = {
        "bearerToken": {
            "type": "http",
            "scheme": "bearer",
            "description": f"An API token created in the Vault app. Always starts with "
                           f"`{apikeys.TOKEN_PREFIX}`.",
        }
    }
    doc["security"] = [{"bearerToken": []}]
    _scrub_internal_prose(doc)
    for path, ops in (doc.get("paths") or {}).items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            op["x-guardian-scope"] = scope_of(method)
            op["x-guardian-role"] = role_of(method)
            if path == f"/{API_VERSION}/health":
                # The one route with no credential: say so in the document
                # rather than leaving the global requirement to imply otherwise.
                op["security"] = []
                op["x-guardian-scope"] = "none"
                op["x-guardian-role"] = "none"
    return doc


def scope_of(method: str) -> str:
    """The lowest token scope a route of this method needs. The API's shape
    makes this a property of the method: every `/v1` GET is a read and every
    `/v1` POST is a write, which is exactly why there is no route where the two
    disagree."""
    return "read" if method.lower() == "get" else "write"


def role_of(method: str) -> str:
    return "viewer" if method.lower() == "get" else "operator"


# --------------------------------------------------------------------------
# The in-app guide
# --------------------------------------------------------------------------

def _group_for(path: str) -> tuple[int, str, str]:
    for i, (name, blurb, prefixes) in enumerate(_GROUPS):
        if any(path == pre or path.startswith(f"{pre}/") for pre in prefixes):
            return i, name, blurb
    return len(_GROUPS), "Other", ""


def _examples(api_base: str) -> list[dict]:
    base = api_base or "https://api.guarder.io"
    return [
        {
            "title": "Confirm your token works",
            "curl": f'curl -s {base}/v1/whoami \\\n  -H "Authorization: Bearer $GUARDIAN_TOKEN"',
        },
        {
            "title": "List the untrusted devices on your network",
            "curl": f'curl -s "{base}/v1/devices?trusted=false&limit=100" \\\n'
                    f'  -H "Authorization: Bearer $GUARDIAN_TOKEN"',
        },
        {
            "title": "Page through every open alert",
            "curl": f'curl -s "{base}/v1/alerts?status=open&limit=50&offset=0" \\\n'
                    f'  -H "Authorization: Bearer $GUARDIAN_TOKEN"',
        },
        {
            "title": "Acknowledge an alert (needs a write token)",
            "curl": f'curl -s -X POST {base}/v1/alerts/$ALERT_KEY/ack \\\n'
                    f'  -H "Authorization: Bearer $GUARDIAN_TOKEN"',
        },
        {
            "title": "Snooze an alert for 24 hours",
            "curl": f'curl -s -X POST {base}/v1/alerts/$ALERT_KEY/snooze \\\n'
                    f'  -H "Authorization: Bearer $GUARDIAN_TOKEN" \\\n'
                    f'  -H "Content-Type: application/json" \\\n'
                    f'  -d \'{{"hours": 24}}\'',
        },
        {
            "title": "Mark a device as one you recognise",
            "curl": f'curl -s -X POST {base}/v1/devices/$MAC \\\n'
                    f'  -H "Authorization: Bearer $GUARDIAN_TOKEN" \\\n'
                    f'  -H "Content-Type: application/json" \\\n'
                    f'  -d \'{{"trusted": true, "label": "Kitchen tablet"}}\'',
        },
        {
            "title": "Ask for a scan now, then follow it",
            "curl": f'JOB=$(curl -s -X POST {base}/v1/scans \\\n'
                    f'  -H "Authorization: Bearer $GUARDIAN_TOKEN" | jq -r .id)\n'
                    f'curl -s {base}/v1/jobs/$JOB -H "Authorization: Bearer $GUARDIAN_TOKEN"',
        },
    ]


def guide(app) -> dict:
    """Everything the app's API-guide section renders, from `document()`."""
    doc = document(app)
    api_base = base_url()
    buckets: dict[int, dict] = {}
    for path, ops in sorted((doc.get("paths") or {}).items()):
        idx, name, blurb = _group_for(path)
        bucket = buckets.setdefault(idx, {"name": name, "blurb": blurb, "endpoints": []})
        for method in ("get", "post", "put", "patch", "delete"):
            op = ops.get(method)
            if not op:
                continue
            bucket["endpoints"].append({
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary") or "",
                "description": op.get("description") or "",
                "scope": op.get("x-guardian-scope") or scope_of(method),
                "role": op.get("x-guardian-role") or role_of(method),
            })

    per_min, window = apideps.PER_TOKEN
    writes_min, _ = apideps.PER_TOKEN_WRITE
    return {
        "base_url": api_base,
        "version": API_VERSION,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "openapi_url": f"{api_base}/{API_VERSION}/openapi.json" if api_base else "",
        "reference_url": f"{api_base}/{API_VERSION}/docs" if api_base else "",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "token_prefix": apikeys.TOKEN_PREFIX,
        "rate_limit": f"{per_min} requests per {window} seconds per token, "
                      f"and {writes_min} writes in the same window. Over either, the API answers "
                      f"429 with a Retry-After header.",
        "scopes": [
            {"action": "read", "why": "Every GET route. Cannot change anything."},
            {"action": "write", "why": "Everything a read token can do, plus acknowledging, "
                                       "snoozing, resolving and reopening alerts, labelling and "
                                       "trusting devices, and asking for a scan or a speed test."},
        ],
        "groups": [buckets[i] for i in sorted(buckets)],
        "excluded": [{"action": a, "why": w} for a, w in EXCLUSIONS],
        "examples": _examples(api_base),
    }


# --------------------------------------------------------------------------
# The rendered reference page
# --------------------------------------------------------------------------
#
# The reference is styled from Vault's own design tokens rather than a
# palette of its own, so api.guarder.io reads as the same product as the app on
# my.guarder.io and the site on guarder.io. The `:root` block below is lifted
# from `site/styles.css` value for value — same background, panel, hairline,
# ink, muted ink, status hues, accent and radius, and the same
# `prefers-color-scheme` convention, which is the right one for a page with no
# signed-in user to remember a choice for.
#
# Fonts: served from this origin, under `/v1/`. Vault self-hosts Manrope and
# IBM Plex Mono for the app and the site, and the reference renders in the same
# two faces rather than a lookalike stack.
#
#   * `deploy/nginx-api.conf.tmpl` proxies `location /v1/` and default-denies
#     everything else, so the faces are reachable only because
#     `routers/api_v1.py` serves them at `/v1/fonts/{name}` — an allow-listed
#     asset route, not part of the API and absent from the OpenAPI document.
#   * inlining them as `data:` URIs would be refused by the very policy this
#     page exists to keep tight: `font-src` falls back to `default-src 'self'`,
#     and `'self'` does not match `data:`. Hence real URLs, not data URIs.
#
# Both families are still named first in a full fallback stack, so a face that
# fails to load costs nothing: nothing on this page depends on the webfont.

_CSS = """
@font-face{font-family:Manrope;src:url("/v1/fonts/manrope-latin-var.woff2") format("woff2-variations");font-weight:200 800;font-display:swap}
@font-face{font-family:"IBM Plex Mono";src:url("/v1/fonts/ibm-plex-mono-latin-400.woff2") format("woff2");font-weight:400;font-display:swap}
@font-face{font-family:"IBM Plex Mono";src:url("/v1/fonts/ibm-plex-mono-latin-600.woff2") format("woff2");font-weight:600;font-display:swap}

:root{
--bg:#f4f7fb;--panel:#ffffff;--panel2:#eef3fa;--line:#dbe4f0;
--ink:#0f1a2b;--mute:#5b6b82;--ok:#16a34a;--warn:#e0b000;--crit:#e02424;
--acc:#1d6fe0;--accink:#ffffff;--accsoft:#e8f0ff;
--shadow:0 8px 28px rgba(15,26,43,.1);--radius:14px;--radius-sm:6px;
--font-sans:Manrope,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--font-mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
--fs:15.5px;--fs-dense:13.5px;--fs-xs:11.5px;
color-scheme:light}
@media (prefers-color-scheme:dark){:root{
--bg:#070d1a;--panel:#0d1628;--panel2:#121d33;--line:#1e2c47;
--ink:#e8eef8;--mute:#8fa3c2;--ok:#34d16a;--warn:#f5c518;--crit:#ff5a5a;
--acc:#4d9bff;--accink:#04101f;--accsoft:#16273f;
--shadow:0 10px 40px rgba(0,0,0,.55);--radius:10px;--radius-sm:5px;
--fs-dense:12.5px;color-scheme:dark}}

*,*::before,*::after{box-sizing:border-box}
html{scroll-padding-top:96px}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font-sans);
font-size:var(--fs);line-height:1.55;-webkit-text-size-adjust:100%;
-webkit-font-smoothing:antialiased}
a{color:var(--acc)}
:focus-visible{outline:3px solid var(--acc);outline-offset:2px;border-radius:4px}
code,kbd,pre{font-family:var(--font-mono)}

.skip{position:absolute;left:-9999px;top:0;background:var(--panel);color:var(--ink);
border:1px solid var(--acc);border-radius:8px;padding:10px 14px;z-index:30}
.skip:focus{left:12px;top:12px}

/* ------------------------------------------------------------------ header */
.top{position:sticky;top:0;z-index:20;background:var(--panel);
border-bottom:1px solid var(--line)}
.top-in{max-width:1180px;margin:0 auto;padding:10px 20px;display:flex;
align-items:center;gap:12px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:1.05rem;
color:var(--ink);text-decoration:none}
.brand svg{width:22px;height:22px;flex:none}
.brand .ver{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:600;
letter-spacing:.06em;color:var(--mute);border:1px solid var(--line);
border-radius:999px;padding:2px 8px}
.top nav{margin-left:auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap}

/* Quiet chip-weight controls, one filled accent action — the app's rule. */
.btn{font-size:12px;font-weight:500;font-family:inherit;text-decoration:none;
border:1px solid var(--line);border-radius:var(--radius-sm);padding:6px 10px;
background:var(--panel2);color:var(--ink);transition:border-color .15s ease}
.btn:hover{border-color:var(--acc)}
.btn.primary{background:var(--acc);border-color:var(--acc);color:var(--accink);
font-weight:600}

/* -------------------------------------------------------------------- hero */
/* Full-bleed band above the two-column shell: the eyebrow, the headline, the
   counts and (once the console loads) the token panel. Everything in it is
   derived from the OpenAPI document, so it cannot describe a different API
   from the reference underneath it. */
.hero{border-bottom:1px solid var(--line);background:var(--panel)}
.hero-in{max-width:1180px;margin:0 auto;padding:34px 20px 26px}

.eyebrow{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin:0 0 14px}
.badge{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:600;
letter-spacing:.07em;text-transform:uppercase;color:var(--mute);
background:var(--panel2);border:1px solid var(--line);border-radius:999px;
padding:2px 9px;white-space:nowrap}
.badge.acc{color:var(--acc);background:var(--accsoft);border-color:var(--acc)}

/* The brand word carries the accent; `em` rather than a span so the emphasis
   survives with the stylesheet off. */
.hero h1{font-size:clamp(27px,5vw,42px);margin:0 0 10px;max-width:20ch}
.hero h1 em{font-style:normal;color:var(--acc)}
.hero-lede{color:var(--mute);max-width:66ch;margin:0 0 22px}

/* -------------------------------------------------------------------- tiles */
/* Counts, not decoration. Each number is computed from the same document the
   page below renders, in `_counts()`. */
.tiles{list-style:none;margin:0;padding:0;display:grid;gap:10px;
grid-template-columns:repeat(4,minmax(0,1fr))}
.tiles li{background:var(--panel2);border:1px solid var(--line);
border-radius:var(--radius);padding:13px 15px;min-width:0}
.tiles b{display:block;font-size:clamp(21px,3.2vw,27px);font-weight:700;
letter-spacing:-.02em;line-height:1.1;font-variant-numeric:tabular-nums}
.tiles span{display:block;margin-top:3px;font-size:var(--fs-xs);font-weight:600;
text-transform:uppercase;letter-spacing:.6px;color:var(--mute)}

/* ------------------------------------------------------- the live console */
/* Everything below is served with the `hidden` attribute set and is revealed
   by `/v1/docs.js`. With the script blocked, missing, or simply not loaded
   yet, the page is exactly the static reference it was before -- no dead
   buttons, no empty boxes. Nothing here is hidden with `display:none` in CSS,
   because then the script would have to fight the stylesheet to show it. */
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip-path:inset(50%);white-space:nowrap;border:0}

.tokenbar{margin-top:18px;background:var(--panel2);border:1px solid var(--line);
border-radius:var(--radius);padding:13px 15px}
.tokenbar-head{display:flex;align-items:center;gap:10px;margin-bottom:9px}
.tokenbar-head .label{margin:0}
.pill{margin-left:auto;font-family:var(--font-mono);font-size:var(--fs-xs);
border-radius:999px;padding:2px 9px;border:1px solid var(--line);
background:var(--panel);color:var(--mute);white-space:nowrap}
.pill[data-on="1"]{color:var(--ok);border-color:var(--ok)}
.tokenrow{display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.tokenrow input{flex:1 1 260px;min-width:0;font-family:var(--font-mono);
font-size:var(--fs-dense);color:var(--ink);background:var(--panel);
border:1px solid var(--line);border-radius:var(--radius-sm);padding:7px 10px}
.tokenrow input::placeholder{color:var(--mute)}
.tokenrow input:focus-visible{border-color:var(--acc)}
.tokennote{margin:9px 0 0;color:var(--mute);font-size:var(--fs-dense);max-width:74ch}

button.btn{cursor:pointer}
.try{border-top:1px dashed var(--line);padding-top:11px}
.try-params{display:grid;gap:7px;
grid-template-columns:repeat(auto-fit,minmax(215px,1fr));margin-bottom:9px}
.try-params label{display:flex;flex-direction:column;gap:3px;min-width:0}
.try-params label span{font-size:var(--fs-xs);color:var(--mute);
font-family:var(--font-mono)}
.try-params label span i{font-style:normal;text-transform:uppercase;
letter-spacing:.06em;opacity:.75}
.try-params input{min-width:0;font-family:var(--font-mono);font-size:var(--fs-dense);
color:var(--ink);background:var(--panel2);border:1px solid var(--line);
border-radius:var(--radius-sm);padding:5px 8px}
.try-params input:focus-visible{border-color:var(--acc)}
.try-run{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.try-status{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--mute)}
.try-status[data-tone="ok"]{color:var(--ok)}
.try-status[data-tone="bad"]{color:var(--crit)}
.try-out{margin-top:9px;max-height:340px;overflow:auto}
.try-no{margin:0;color:var(--mute);font-size:var(--fs-dense)}

/* ------------------------------------------------------------------- shell */
.shell{max-width:1180px;margin:0 auto;padding:26px 20px 72px;display:grid;
grid-template-columns:232px minmax(0,1fr);gap:34px;align-items:start}
.body{min-width:0}

/* -------------------------------------------------------------- the index */
/* Script-free: plain in-page anchors, and nothing is hidden behind a fragment
   selector, so every word stays findable with the browser's own find and a
   visitor arriving without a fragment sees the whole page. Sidebar on a wide
   viewport; below 900px the same one list becomes a chip strip that scrolls
   inside itself, which is why the page body never scrolls sideways. */
.index{position:sticky;top:80px;max-height:calc(100vh - 104px);overflow-y:auto;
font-size:var(--fs-dense)}
.index ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1px}
.index a{display:flex;gap:8px;align-items:baseline;text-decoration:none;
color:var(--mute);padding:4px 8px;border-radius:var(--radius-sm);
border:1px solid transparent}
.index a:hover{color:var(--ink);background:var(--panel2);border-color:var(--line)}
.index .n{margin-left:auto;font-family:var(--font-mono);font-size:var(--fs-xs);
font-variant-numeric:tabular-nums;color:var(--mute)}
.index hr{border:0;border-top:1px solid var(--line);margin:8px 0}

/* Small-caps, tracked-out, muted: the app's `.g-section-label`. */
.label{margin:0 0 8px;font-size:var(--fs-xs);font-weight:600;text-transform:uppercase;
letter-spacing:.6px;color:var(--mute);line-height:1.2}

/* ------------------------------------------------------------------- type */
h1{font-size:clamp(26px,4vw,34px);font-weight:700;letter-spacing:-.015em;
line-height:1.15;margin:0 0 6px}
h2{font-size:var(--fs-xs);font-weight:600;text-transform:uppercase;
letter-spacing:.6px;color:var(--mute);line-height:1.2;margin:0 0 4px}
h3{font-size:15px;font-weight:600;margin:0;letter-spacing:-.01em}
p{margin:0 0 12px}
.lede{color:var(--mute);max-width:70ch}
.sect{padding-top:26px;margin-top:26px;border-top:1px solid var(--line)}
.sect:first-of-type{border-top:0;margin-top:0;padding-top:0}
.sect>.lede{margin-bottom:14px}
.intro p{max-width:74ch}
.intro code{white-space:nowrap}

code{background:var(--panel2);border:1px solid var(--line);border-radius:4px;
padding:.05em .32em;font-size:.86em}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:var(--radius-sm);
padding:10px 12px;margin:0;overflow-x:auto;font-size:var(--fs-dense);line-height:1.5}
pre code{background:none;border:0;padding:0;font-size:1em;white-space:pre}

/* ------------------------------------------------------------------ panels */
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
padding:14px 16px}
.ep{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
padding:14px 16px;margin:10px 0}
.ep-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.m{font-family:var(--font-mono);font-size:10.5px;font-weight:600;letter-spacing:.06em;
padding:3px 7px;border-radius:5px;border:1px solid currentColor;color:var(--acc);
flex:none}
.m.POST{color:var(--warn)}
.path{background:none;border:0;padding:0;font-size:var(--fs-dense);font-weight:600;
color:var(--ink);word-break:break-all}
.tags{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.chip{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--mute);
background:var(--panel2);border:1px solid var(--line);border-radius:999px;
padding:1px 8px;white-space:nowrap}
.ep h3{margin-top:10px}
.ep p{color:var(--mute);margin:6px 0 0;max-width:74ch;font-size:var(--fs-dense)}
.ep .block{margin-top:12px}
.ep .block>.label{margin-bottom:5px}

/* ------------------------------------------------------------------ tables */
.tw{overflow-x:auto;width:100%}
table{border-collapse:collapse;width:100%;font-size:var(--fs-dense)}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);
vertical-align:top}
thead th{font-size:var(--fs-xs);font-weight:500;color:var(--mute);
background:var(--panel2);white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
td.k{font-family:var(--font-mono);font-weight:600;white-space:nowrap}
td.t{font-family:var(--font-mono);color:var(--mute);white-space:nowrap}
td.d{color:var(--mute)}

/* ------------------------------------------------------------- two examples */
.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}

/* ----------------------------------------------------------------- refusals */
.no{background:var(--panel);border:1px solid var(--line);
border-left:3px solid var(--acc);border-radius:var(--radius);padding:11px 14px;
margin:8px 0}
.no b{display:block;font-size:var(--fs-dense)}
.no span{color:var(--mute);font-size:var(--fs-dense)}

footer{border-top:1px solid var(--line);color:var(--mute);font-size:var(--fs-dense)}
footer .top-in{padding:16px 20px}
.push{margin-left:auto}

/* ----------------------------------------------------------------- narrow */
@media (max-width:900px){
html{scroll-padding-top:60px}
/* Two sticky bars would stack on a phone and the index would slide under the
   brand bar, whose height changes as its buttons wrap. Only one of them earns
   the space: on a narrow viewport the index stays, the brand bar scrolls away. */
.top{position:static}
.shell{grid-template-columns:minmax(0,1fr);gap:16px;padding-top:0}
.index{position:sticky;top:0;z-index:15;max-height:none;overflow:visible;
background:var(--bg);margin:0 -20px;padding:10px 20px;
border-bottom:1px solid var(--line)}
.index .label{display:none}
.index hr{display:none}
.index ul{flex-direction:row;overflow-x:auto;gap:6px;
scrollbar-width:thin;-webkit-overflow-scrolling:touch}
.index li{flex:none}
.index a{border-color:var(--line);background:var(--panel);white-space:nowrap;
padding:4px 10px}
.index .n{display:none}
.top-in{padding:8px 16px}
.shell{padding-left:16px;padding-right:16px}
.index{margin:0 -16px;padding:10px 16px}
.card,.ep,.no{padding:12px 13px}
.try-params{grid-template-columns:minmax(0,1fr)}
/* Four tiles in a row are unreadable at 375px, and a single column would push
   the index two screens down. 2x2 keeps the counts to one screen. */
.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
.hero-in{padding:22px 16px 20px}
.hero h1{max-width:none}
}
"""

_SHIELD = (
    '<svg viewBox="0 0 48 48" aria-hidden="true" focusable="false">'
    '<path d="M24 3 L42 10 V22 C42 34 34 42 24 45 C14 42 6 34 6 22 V10 Z" fill="var(--acc)"/>'
    '<path d="M15 24 L21 30 L33 17" fill="none" stroke="var(--accink)" stroke-width="3.6" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in str(text).lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "section"


def _md_lite(text: str) -> str:
    """The tiny subset of Markdown the descriptions actually use: `**bold**`,
    `` `code` `` and blank-line paragraphs. Everything is HTML-escaped first, so
    a description can never inject markup."""
    out = _esc(text)
    parts = []
    for para in out.split("\n\n"):
        para = para.replace("\n", " ").strip()
        if not para:
            continue
        while "**" in para:
            para = para.replace("**", "<strong>", 1)
            if "**" in para:
                para = para.replace("**", "</strong>", 1)
            else:
                para += "</strong>"
        while "`" in para:
            para = para.replace("`", "<code>", 1)
            if "`" in para:
                para = para.replace("`", "</code>", 1)
            else:
                para += "</code>"
        parts.append(f"<p>{para}</p>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Example payloads, synthesised from the document's own schemas
# --------------------------------------------------------------------------
#
# The reference shows a worked response for every endpoint, built from the
# schema the route already declares rather than from a second hand-written copy
# that could drift away from it.
#
# Two guards make this safe to run at import-of-page time. `seen` holds the
# component names on the *current branch* (siblings are unaffected), so a
# self-referential shape — a topology node whose children are nodes — stops the
# first time it comes round instead of recursing forever. `_MAX_DEPTH` stops
# the merely deep, which also keeps the printed example short enough to read.
#
# `anyOf: [{...}, {"type": "null"}]` is pydantic's optional field and it is
# everywhere in this document; taking the first branch naively would print
# `null` for half of every payload, so the first non-null branch wins.

_MAX_DEPTH = 7

#: Placeholder values for the shapes a bare `"type": "string"` hides. Keyed on
#: the property name, so an example reads like a real payload instead of a
#: column of `"string"`.
_STRING_HINTS: dict[str, str] = {
    "mac": "a4:83:e7:2c:11:09",
    "ip": "192.168.1.42",
    "ip_address": "192.168.1.42",
    "hostname": "kitchen-tablet",
    "vendor": "Apple, Inc.",
    "severity": "warn",
    "status": "open",
    "domain": "example.com",
    "url": "https://example.com",
    "email": "you@example.com",
    "key": "a1b2c3d4",
    "id": "a1b2c3d4",
    "slug": "home",
    "name": "Kitchen tablet",
    "label": "Kitchen tablet",
    "msg": "A new device joined the network",
    "why": "First seen 4 minutes ago, and it is not on the trusted list",
    "confidence": "high",
    "colour": "#1d6fe0",
    "first_seen": "2026-01-01T09:30:00Z",
    "last_seen": "2026-01-01T09:34:00Z",
    "created": "2026-01-01T09:30:00Z",
    "updated": "2026-01-01T09:34:00Z",
}


def _example_for(schema, schemas: dict, name: str = "", depth: int = 0,
                 seen: frozenset = frozenset()):
    """A representative value for `schema`, or `None`."""
    if not isinstance(schema, dict):
        return None

    ref = schema.get("$ref")
    if ref:
        component = ref.rsplit("/", 1)[-1]
        if component in seen or depth >= _MAX_DEPTH:
            return {}
        return _example_for(schemas.get(component) or {}, schemas, name,
                            depth + 1, seen | {component})

    if "example" in schema:
        return schema["example"]
    if schema.get("examples"):
        return schema["examples"][0]
    if schema.get("enum"):
        return schema["enum"][0]
    # An empty-string / empty-list default is pydantic's "nothing set yet", not a
    # value worth printing: fall through to the type below so the example shows
    # the shape of a populated payload.
    if schema.get("default") not in (None, "", [], {}):
        return schema["default"]

    for combinator in ("anyOf", "oneOf"):
        options = schema.get(combinator)
        if options:
            for option in options:
                if (option or {}).get("type") != "null":
                    return _example_for(option, schemas, name, depth, seen)
            return None
    if schema.get("allOf"):
        merged: dict = {}
        for part in schema["allOf"]:
            value = _example_for(part, schemas, name, depth, seen)
            if isinstance(value, dict):
                merged.update(value)
        return merged

    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if depth >= _MAX_DEPTH:
            return {}
        return {key: _example_for(sub, schemas, key, depth + 1, seen)
                for key, sub in (schema.get("properties") or {}).items()}
    if kind == "array":
        if depth >= _MAX_DEPTH:
            return []
        item = _example_for(schema.get("items") or {}, schemas, name, depth + 1, seen)
        return [] if item is None else [item]
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2026-01-01T09:30:00Z"
        if fmt == "date":
            return "2026-01-01"
        if fmt in ("uri", "url"):
            return "https://example.com"
        if fmt == "email":
            return "you@example.com"
        return _STRING_HINTS.get(name, "string")
    return None


def _json_block(value) -> str:
    return json.dumps(value, indent=2, sort_keys=False, default=str)


def _response_example(op: dict, schemas: dict) -> tuple[str, str]:
    """`(status, pretty json)` for the endpoint's own success response."""
    responses = op.get("responses") or {}
    codes = sorted(c for c in responses if str(c).startswith("2"))
    if not codes:
        return "", ""
    code = codes[0]
    schema = (((responses[code].get("content") or {}).get("application/json") or {})
              .get("schema"))
    if not schema:
        return str(code), ""
    value = _example_for(schema, schemas)
    return str(code), "" if value is None else _json_block(value)


def _curl_example(method: str, path: str, op: dict, api_base: str, schemas: dict) -> str:
    """A copy-and-run request for this one endpoint.

    Path parameters become shell variables (`/v1/alerts/$KEY/ack`), so the line
    can be pasted after setting them rather than edited in place.
    """
    concrete = path
    for prm in op.get("parameters") or []:
        if prm.get("in") == "path":
            concrete = concrete.replace("{%s}" % prm["name"], "$" + prm["name"].upper())
    lines = [f'curl -s{"" if method == "GET" else f" -X {method}"} '
             f'"{api_base}{concrete}" \\']
    if op.get("security") != []:
        lines.append('  -H "Authorization: Bearer $GUARDIAN_TOKEN"')
    else:
        lines[-1] = lines[-1].rstrip(" \\")
    body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
    if body and body.get("schema"):
        value = _example_for(body["schema"], schemas)
        if isinstance(value, dict) and value:
            lines[-1] += " \\"
            lines.append('  -H "Content-Type: application/json" \\')
            lines.append("  -d '%s'" % json.dumps(value))
    return "\n".join(lines)


def _params_table(op: dict) -> str:
    rows = []
    for prm in op.get("parameters") or []:
        schema = prm.get("schema") or {}
        kind = schema.get("type") or ""
        if not kind:
            for option in schema.get("anyOf") or []:
                if (option or {}).get("type") not in (None, "null"):
                    kind = option["type"]
                    break
        default = schema.get("default")
        notes = []
        if prm.get("required"):
            notes.append('<span class="chip">required</span>')
        if default not in (None, ""):
            notes.append(f'<span class="chip">default {_esc(json.dumps(default))}</span>')
        desc = " ".join([_esc(prm.get("description") or "")] + notes).strip()
        rows.append(
            f'<tr><td class="k">{_esc(prm.get("name"))}</td>'
            f'<td class="t">{_esc(prm.get("in"))}</td>'
            f'<td class="t">{_esc(kind)}</td>'
            f'<td class="d">{desc}</td></tr>'
        )
    if not rows:
        return ""
    return ('<div class="block"><p class="label">Parameters</p><div class="tw">'
            '<table><thead><tr><th>Name</th><th>In</th><th>Type</th>'
            f'<th>Notes</th></tr></thead><tbody>{"".join(rows)}</tbody>'
            '</table></div></div>')


#: Copy shown, in place of a Send button, on the routes the console will not
#: call. Read endpoints only is a deliberate limit, not an unfinished one, and
#: it is the same rule `EXCLUSIONS` states for the API as a whole: an action
#: that changes the account wants a person who meant to take it, not a stray
#: click on a documentation page.
_TRY_WRITE_NOTE = ("This one writes. The console here sends reads only \u2014 a write "
                   "belongs in your own code, where a stray click cannot change your "
                   "account.")


def _try_block(method: str, path: str, op: dict) -> str:
    """The per-endpoint console, served hidden.

    Every parameter the document declares gets an input, path parameters
    included: without them a Send button on `/v1/alerts/{alert_key}/ack` would
    request the literal braces and 404, which is worse than no button. The
    inputs carry only `data-` attributes; `/v1/docs.js` reads them and does the
    substitution, so nothing here needs a value interpolated into script.
    """
    if method.upper() != "GET":
        return (f'<div class="block try" hidden>'
                f'<p class="label">Try it out</p>'
                f'<p class="try-no">{_esc(_TRY_WRITE_NOTE)}</p></div>')

    fields = []
    for prm in op.get("parameters") or []:
        name, where = str(prm.get("name") or ""), str(prm.get("in") or "")
        if not name or where not in ("path", "query"):
            continue
        required = bool(prm.get("required"))
        default = (prm.get("schema") or {}).get("default")
        hint = "required" if required else (
            f"default {default}" if default not in (None, "") else "optional")
        req_attr = ' data-required="1"' if required else ""
        fields.append(
            f'<label><span>{_esc(name)} <i>{_esc(where)}</i></span>'
            f'<input type="text" spellcheck="false" autocomplete="off"'
            f' data-name="{_esc(name)}" data-in="{_esc(where)}"{req_attr}'
            f' placeholder="{_esc(hint)}"></label>'
        )
    params_html = f'<div class="try-params">{"".join(fields)}</div>' if fields else ""
    return (
        f'<div class="block try" hidden data-method="GET" data-path="{_esc(path)}">'
        f'<p class="label">Try it out</p>'
        f'{params_html}'
        f'<div class="try-run">'
        f'<button type="button" class="btn primary try-go">Send</button>'
        f'<span class="try-status" role="status"></span></div>'
        f'<pre class="try-out" hidden><code></code></pre></div>'
    )


def _counts(doc: dict, guide_doc: dict) -> list[tuple[str, str]]:
    """The hero's stat tiles, every one of them read off the generated
    document.

    Nothing here is a literal, and each number is computed with the same
    expression the page body uses, so a tile and the reference under it cannot
    disagree:

    * **Endpoints** counts *operations*, not paths. `/v1/alerts` answering both
      GET and POST is two endpoints, and two `<article class="ep">` blocks.
    * **Groups** counts the groups that actually rendered -- `guide()`'s
      buckets -- not the `_GROUPS` table. The two differ the moment a path
      falls through to "Other", and the tile must follow the page.
    * **Schemas** counts the components left after `_scrub_internal_prose`,
      which is what the response examples are built from.
    * **Rate limit** is the real per-token allowance from `apideps`, not a
      number repeated from the prose. A tile that always reads "1" (the server
      count, say) is filler; this one tells a reader something.
    """
    paths = doc.get("paths") or {}
    schemas = (doc.get("components") or {}).get("schemas") or {}
    per_min, _window = apideps.PER_TOKEN
    return [
        (str(sum(len(ops) for ops in paths.values())), "Endpoints"),
        (str(len(guide_doc["groups"])), "Groups"),
        (str(len(schemas)), "Schemas"),
        (str(per_min), "Calls a minute"),
    ]


def reference_html(app) -> str:
    """A complete, self-contained HTML reference. No external assets, so the
    API host can serve it under a `default-src 'self'` policy."""
    doc = document(app)
    guide_doc = guide(app)
    api_base = guide_doc["base_url"] or ""
    schemas = (doc.get("components") or {}).get("schemas") or {}
    paths = doc.get("paths") or {}

    groups_html = []
    index_rows = []
    for group in guide_doc["groups"]:
        gid = "g-" + _slug(group["name"])
        index_rows.append(
            f'<li><a href="#{gid}">{_esc(group["name"])}'
            f'<span class="n">{len(group["endpoints"])}</span></a></li>'
        )
        eps = []
        for ep in group["endpoints"]:
            method, path = ep["method"], ep["path"]
            op = (paths.get(path) or {}).get(method.lower()) or {}
            chips = ['<span class="chip">no token needed</span>'] if ep["scope"] == "none" else [
                f'<span class="chip">scope {_esc(ep["scope"])}</span>',
                f'<span class="chip">role {_esc(ep["role"])}</span>',
            ]
            title = f'<h3>{_esc(ep["summary"])}</h3>' if ep["summary"] else ""
            code, body = _response_example(op, schemas)
            request = _curl_example(method, path, op, api_base, schemas)
            examples = [f'<div><p class="label">Request</p>'
                        f'<pre><code>{_esc(request)}</code></pre></div>']
            if body:
                examples.append(f'<div><p class="label">Response {_esc(code)}</p>'
                                f'<pre><code>{_esc(body)}</code></pre></div>')
            eps.append(
                # The path stays one unbroken text node: it is the string a
                # reader searches for, and `tests/test_api_v1.py` asserts every
                # documented path appears in the page verbatim.
                f'<article class="ep" id="ep-{_slug(method + path)}">'
                f'<div class="ep-head">'
                f'<span class="m {_esc(method)}">{_esc(method)}</span>'
                f'<code class="path">{_esc(path)}</code>'
                f'<span class="tags">{"".join(chips)}</span></div>'
                f'{title}'
                f'{_md_lite(ep["description"]) if ep["description"] else ""}'
                f'{_params_table(op)}'
                f'<div class="block pair">{"".join(examples)}</div>'
                f'{_try_block(method, path, op)}'
                f'</article>'
            )
        groups_html.append(
            f'<section class="sect" id="{gid}" aria-labelledby="{gid}-h">'
            f'<h2 id="{gid}-h">{_esc(group["name"])}</h2>'
            f'<p class="lede">{_esc(group["blurb"])}</p>{"".join(eps)}</section>'
        )

    quickstart_html = "".join(
        f'<div class="block"><p class="label">{_esc(ex["title"])}</p>'
        f'<pre><code>{_esc(ex["curl"])}</code></pre></div>'
        for ex in guide_doc["examples"]
    )
    excluded_html = "".join(
        f'<div class="no"><b>{_esc(x["action"])}</b><span>{_esc(x["why"])}</span></div>'
        for x in guide_doc["excluded"]
    )
    scopes_html = "".join(
        f'<tr><td class="k">{_esc(s["action"])}</td><td class="d">{_esc(s["why"])}</td></tr>'
        for s in guide_doc["scopes"]
    )

    index_html = (
        '<nav class="index" aria-label="Contents"><p class="label">On this page</p><ul>'
        '<li><a href="#start">Getting a token</a></li>'
        '<li><a href="#scopes">Scopes</a></li>'
        '<li><a href="#quickstart">Quick start</a></li>'
        '</ul><hr><p class="label">Endpoints</p><ul>'
        + "".join(index_rows)
        + '</ul><hr><ul><li><a href="#not-in-v1">Not in v1</a></li></ul></nav>'
    )

    tiles_html = "".join(
        f'<li><b>{_esc(value)}</b><span>{_esc(label)}</span></li>'
        for value, label in _counts(doc, guide_doc)
    )
    # "3.1.0" -> "3.1": the patch digit of the spec version is noise in a badge,
    # and the number still comes off the document rather than a literal.
    spec = ".".join(str(doc.get("openapi") or "").split(".")[:2])
    generated_day = str(guide_doc["generated"])[:10]
    hero_html = f"""
<section class="hero" aria-labelledby="hero-h">
  <div class="hero-in">
    <p class="eyebrow">
      <span class="badge acc">{_esc(API_VERSION)}</span>
      {f'<span class="badge">OpenAPI {_esc(spec)}</span>' if spec else ''}
      <span class="badge">Updated {_esc(generated_day)}</span>
    </p>
    <h1 id="hero-h">The <em>Vault</em> API, in one page.</h1>
    <p class="hero-lede">Read your account and act on what it finds, from your own
    code. Every endpoint is below, with its parameters, a worked request and a real
    response &mdash; generated from the document Vault serves, so this page and
    the API cannot drift apart.</p>
    <ul class="tiles">{tiles_html}</ul>
    <div class="tokenbar" id="tokenbar" hidden>
      <div class="tokenbar-head">
        <p class="label" id="tokenbar-h">Live API token</p>
        <span class="pill" id="token-state">No token saved</span>
      </div>
      <div class="tokenrow" role="group" aria-labelledby="tokenbar-h">
        <label class="sr" for="token-input">API token</label>
        <input id="token-input" type="password" spellcheck="false" autocomplete="off"
               autocapitalize="off" autocorrect="off"
               placeholder="Paste a token beginning {_esc(apikeys.TOKEN_PREFIX)}">
        <button type="button" class="btn" id="token-show" aria-pressed="false">Show</button>
        <button type="button" class="btn primary" id="token-save">Save</button>
        <button type="button" class="btn" id="token-clear">Clear</button>
      </div>
      <p class="tokennote">Held in this tab&rsquo;s <code>sessionStorage</code> and nowhere
      else: it is gone when you close the tab, Clear wipes it now, and it is never written
      to a URL, a query string or the page. It is sent only as an
      <code>Authorization</code> header, only to this host, by the Send buttons below.</p>
    </div>
  </div>
</section>"""

    app_origin = (settings.base_url or "").rstrip("/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(API_TITLE)} reference</title>
<meta name="description" content="Read your Vault account and act on what it \
finds, from your own code. The {_esc(API_VERSION)} REST reference.">
<link rel="stylesheet" href="/{API_VERSION}/docs.css"></head><body>
<a class="skip" href="#main">Skip to content</a>

<header class="top">
  <div class="top-in">
    <span class="brand">{_SHIELD}{_esc(API_TITLE)}<span class="ver">{_esc(API_VERSION)}</span></span>
    <nav aria-label="Vault">
      <a class="btn" href="/{API_VERSION}/openapi.json">OpenAPI document</a>
      {f'<a class="btn primary" href="{_esc(app_origin)}">Open the app</a>' if app_origin else ''}
    </nav>
  </div>
</header>

<main id="main">
{hero_html}

<div class="shell">
{index_html}
<div class="body">

<section class="sect" id="start" aria-labelledby="start-h">
<h2 id="start-h">Getting a token</h2>
<p class="lede">Version {_esc(API_VERSION)}{f' &middot; <code>{_esc(api_base)}</code>' if api_base else ''}</p>
<div class="intro">{_md_lite(DESCRIPTION)}</div>
</section>

<section class="sect" id="scopes" aria-labelledby="scopes-h">
<h2 id="scopes-h">Scopes</h2>
<p class="lede">A token carries one of these, and a role that can never exceed the role
of the admin who created it.</p>
<div class="card"><div class="tw"><table>
<thead><tr><th>Scope</th><th>What it allows</th></tr></thead>
<tbody>{scopes_html}</tbody></table></div></div>
</section>

<section class="sect" id="quickstart" aria-labelledby="quickstart-h">
<h2 id="quickstart-h">Quick start</h2>
<p class="lede">Export your token as <code>GUARDIAN_TOKEN</code> and run any of these.</p>
<div class="card">{quickstart_html}</div>
</section>

{"".join(groups_html)}

<section class="sect" id="not-in-v1" aria-labelledby="not-in-v1-h">
<h2 id="not-in-v1-h">Not in v1</h2>
<p class="lede">These are left out deliberately, not by omission. Each one is available
in the Vault app, to a person who can see what will happen.</p>
{excluded_html}
</section>

</div>
</div>
</main>

<script src="/{API_VERSION}/docs.js" defer></script>

<footer><div class="top-in">
<span>Vault {_esc(API_VERSION)} &middot; generated {_esc(guide_doc["generated"])}</span>
<span class="push"><a href="mailto:miro@guarder.io">miro@guarder.io</a></span>
</div></footer>
</body></html>"""


# --------------------------------------------------------------------------
# The live console
# --------------------------------------------------------------------------
#
# Served as a file at `/v1/docs.js`, never inline, which is the whole point.
#
# `deploy/nginx-api.conf.tmpl` sets `script-src 'none'` across this vhost on
# purpose: it is the origin that handles customer API tokens, and a script
# running here would be worth a great deal to whoever got it there. The docs
# location relaxes that to `script-src 'self'` -- and *only* that. No
# `'unsafe-inline'`, no nonce, no hash, no third party, no CDN. The one script
# the page loads is this one, from this origin.
#
# What makes the relaxation defensible is not that self-hosted scripts are
# conventional; it is that this page has no injection vector. Every byte of it
# is generated from the OpenAPI document Vault itself produces, and every
# value that reaches the markup goes through `_esc()`. There is no user content
# on the page, no query parameter is reflected into it, and nothing this script
# writes back uses `innerHTML`. An attacker with no way to get a string into the
# document has no way to get a script into the origin.
#
# The token: `sessionStorage`, not `localStorage`. A docs console is something
# you use for ten minutes; the token should not outlive the tab, and on a shared
# or borrowed machine `localStorage` would leave it there for the next person.
# It is read only to build an `Authorization` header, it is never put in a URL
# or a query string (the vhost sets `Referrer-Policy: no-referrer` precisely so
# paths do not leak, and a token in a path would defeat that), it is never
# written into the page, and Clear removes the stored value, the field's value
# and the state pill together.
#
# Reads only. The Send buttons exist on GET operations; a write shows a note
# instead, for the reason `EXCLUSIONS` gives for the API's own refusals -- an
# action that changes the account wants a person who meant to take it.
CONSOLE_JS = r"""/* Vault API reference - the live console.
   Loaded as a file, never inline: the API vhost allows `script-src 'self'` on
   this path and nothing else. Nothing here is fetched from anywhere but this
   origin, and no measurement of any kind rides along.
   The token lives in this tab's sessionStorage and is sent only as an
   Authorization header, only to this origin. */
(function () {
  "use strict";

  var KEY = "guardian.docs.token";

  /* Private windows and "block site data" settings make the accessor itself
     throw, so every touch is guarded and the page still works with no store
     at all -- you just retype the token. */
  function store() {
    try { return window.sessionStorage; } catch (e) { return null; }
  }
  function load() {
    var s = store();
    if (!s) { return ""; }
    try { return s.getItem(KEY) || ""; } catch (e) { return ""; }
  }
  function save(value) {
    var s = store();
    if (!s) { return; }
    try { s.setItem(KEY, value); } catch (e) { /* nothing else to do */ }
  }
  function drop() {
    var s = store();
    if (!s) { return; }
    try { s.removeItem(KEY); } catch (e) { /* nothing else to do */ }
  }

  var input = document.getElementById("token-input");
  var pill = document.getElementById("token-state");
  var bar = document.getElementById("tokenbar");
  if (!input || !pill || !bar) { return; }

  function paint() {
    var held = load();
    pill.textContent = held ? "Token saved for this tab" : "No token saved";
    pill.setAttribute("data-on", held ? "1" : "0");
  }

  document.getElementById("token-save").addEventListener("click", function () {
    var value = input.value.trim();
    if (value) { save(value); } else { drop(); }
    paint();
  });

  document.getElementById("token-clear").addEventListener("click", function () {
    /* All three, or it is not cleared: the stored value, the field the reader
       can still see, and the pill that says what is held. */
    drop();
    input.value = "";
    input.type = "password";
    reveal(false);
    paint();
    input.focus();
  });

  function reveal(on) {
    var button = document.getElementById("token-show");
    input.type = on ? "text" : "password";
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Hide" : "Show";
  }

  document.getElementById("token-show").addEventListener("click", function () {
    reveal(input.type === "password");
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      document.getElementById("token-save").click();
    }
  });

  input.value = load();
  paint();
  bar.hidden = false;

  /* ---------------------------------------------------------------- send */

  function pretty(text) {
    try { return JSON.stringify(JSON.parse(text), null, 2); } catch (e) { return text; }
  }

  function run(block) {
    var button = block.querySelector(".try-go");
    var status = block.querySelector(".try-status");
    var out = block.querySelector(".try-out");
    var code = out.querySelector("code");
    var path = block.getAttribute("data-path");
    var query = [];
    var missing = null;

    Array.prototype.forEach.call(block.querySelectorAll("input[data-name]"), function (field) {
      var name = field.getAttribute("data-name");
      var value = field.value.trim();
      if (field.getAttribute("data-in") === "path") {
        if (!value) { missing = missing || name; return; }
        path = path.replace("{" + name + "}", encodeURIComponent(value));
      } else if (value) {
        query.push(encodeURIComponent(name) + "=" + encodeURIComponent(value));
      }
    });

    if (missing) {
      status.setAttribute("data-tone", "bad");
      status.textContent = "fill in " + missing;
      return;
    }

    /* Same origin, always: the docs page is served by the API host, so the
       request goes back to the host the reader is already reading. This is
       also what `connect-src` (falling back to `default-src 'self'`) allows,
       so there is nowhere else it could go. */
    var url = path + (query.length ? "?" + query.join("&") : "");
    var headers = { Accept: "application/json" };
    var token = load();
    if (token) { headers.Authorization = "Bearer " + token; }

    button.disabled = true;
    status.removeAttribute("data-tone");
    status.textContent = "sending" + String.fromCharCode(8230);
    var began = Date.now();

    fetch(url, { method: "GET", headers: headers, credentials: "omit" })
      .then(function (response) {
        return response.text().then(function (body) {
          var ms = Date.now() - began;
          status.setAttribute("data-tone", response.ok ? "ok" : "bad");
          status.textContent = response.status + " " + (response.statusText || "") +
            String.fromCharCode(32, 183, 32) + ms + " ms";
          /* textContent, never innerHTML: a response body is data, and this
             page renders no markup it did not generate itself. */
          code.textContent = pretty(body) ||
            "(no body)";
          out.hidden = false;
        });
      })
      .catch(function (error) {
        status.setAttribute("data-tone", "bad");
        status.textContent = "request failed";
        code.textContent = String(error && error.message ? error.message : error);
        out.hidden = false;
      })
      .then(function () { button.disabled = false; });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".try"), function (block) {
    var button = block.querySelector(".try-go");
    if (button) {
      button.addEventListener("click", function () { run(block); });
    }
    block.hidden = false;
  });
})();
"""
