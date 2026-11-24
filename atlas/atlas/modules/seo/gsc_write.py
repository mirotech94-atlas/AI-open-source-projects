"""Search Console registration: add a property, verify it, submit a sitemap.

Three Tier 2 actions (a card the owner approves), each with a dry run and a
rollback. They are the only place ATLAS writes to Search Console, and they
need two scopes the default consent does not carry (`webmasters`,
`siteverification`), asked for only with `ATLAS_GOOGLE_SEARCH_CONSOLE_WRITE=1`
(`bootstrap/google_login.py`). While the token lacks them each action refuses
and says exactly what to consent; nothing is guessed and nothing is sent.

Scope kept narrow on purpose:
  * a URL-prefix property, `https://<host>/`, verified with the META tag
    method. DNS verification (a domain property) needs a TXT record, and DNS
    is Tier 3, permanently denied: that one stays the owner's, at his DNS host.
  * a Vercel client preview is refused: previews are noindex on purpose.
  * `verify` goes ahead only when the tag Google issued is already live on the
    page, so it never asks Google to check something that cannot pass.
  * a sitemap must sit on the property's own host.
Reading Google's token answer is not a write; the verify tag is fetched in the
dry run for that reason."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from ...kernel.contracts import ActionResult, FailureClass
from .. import _http, _netguard
from . import gsc

SCOPE_SITES = "https://www.googleapis.com/auth/webmasters"
SCOPE_VERIFY = "https://www.googleapis.com/auth/siteverification"
VERIFY_API = "https://www.googleapis.com/siteVerification/v1"
NEED_WRITE = ("the Google consent lacks the Search Console write scopes: set "
              "ATLAS_GOOGLE_SEARCH_CONSOLE_WRITE=1 and run "
              "`./bootstrap/google-login.sh --reconsent` on the Mac, approve "
              "Search Console and Site Verification (CONFIGURE.md, six agents step 3)")
MAX_PAGE = 2_000_000


def _no(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def _site(url: str) -> tuple[str | None, str | None]:
    """(`https://host/`, None) for a public https home URL, else (None, why)."""
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return None, "the property must be an https URL like https://example.com/"
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        return None, "the property is the site's home, https://<host>/ (no path)"
    if "." not in host or _netguard.literal(host) is not None:
        return None, f"{host} is not a public host name"
    if gsc.is_preview(host):
        return None, (f"{host} is a client preview (noindex on purpose): previews "
                      "are not registered with Google")
    return f"https://{host}/", None


async def _auth(*scopes: str) -> tuple[dict[str, str] | None, str | None]:
    tok, why = await gsc.access_token()
    if tok is None:
        return None, f"Google token: {why}"
    missing = [s for s in scopes if s not in gsc._scopes]
    if missing:
        return None, NEED_WRITE
    return {"Authorization": f"Bearer {tok}"}, None


async def _page(url: str) -> tuple[str | None, str | None]:
    """A public page as text (GET, the property's own host)."""
    why = await _netguard.assert_public(url)
    if why:
        return None, why
    body, why = await _http.get_bytes(url, max_bytes=MAX_PAGE)
    if body is None:
        return None, why or "no body"
    return body.decode("utf-8", "replace"), None


async def _listed(auth: dict[str, str], site: str) -> tuple[bool | None, str | None]:
    data, why = await _http.get_json(f"{gsc.API}/sites", headers=auth)
    if why is not None:
        return None, gsc.forbidden(why)
    urls = {str(e.get("siteUrl")) for e in (data or {}).get("siteEntry") or []}
    return site in urls, None


# ------------------------------------------------------------------- add

async def add_site_dry(site_url: str) -> ActionResult:
    site, why = _site(site_url)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_SITES)
    if auth is None:
        return _no(why or NEED_WRITE)
    listed, why = await _listed(auth, site)
    if listed is None:
        return _no(f"could not read the property list: {why}")
    if listed:
        return ActionResult(True, f"{site} is already a property: nothing to add")
    return ActionResult(True, f"would add {site} as a Search Console property "
                              "(URL prefix; it still has to be verified)")


async def add_site(site_url: str) -> ActionResult:
    site, why = _site(site_url)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_SITES)
    if auth is None:
        return _no(why or NEED_WRITE)
    listed, why = await _listed(auth, site)
    if listed is None:
        return _no(f"could not read the property list: {why}")
    if listed:
        return ActionResult(True, f"{site} was already a property: nothing added")
    _, why = await _http.send_body(
        "PUT", f"{gsc.API}/sites/{quote(site, safe='')}", headers=auth)
    if why is not None:
        return ActionResult(False, f"Google refused to add {site}: {gsc.forbidden(why)}")
    return ActionResult(True, f"added {site}; verify it next", rollback_token=site)


async def remove_site(token: str) -> ActionResult:
    site, why = _site(token)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_SITES)
    if auth is None:
        return _no(why or NEED_WRITE)
    _, why = await _http.send_body(
        "DELETE", f"{gsc.API}/sites/{quote(site, safe='')}", headers=auth)
    if why is not None:
        return ActionResult(False, f"Google refused to remove {site}: {why}")
    return ActionResult(True, f"removed the {site} property")


# ---------------------------------------------------------------- verify

async def _tag(auth: dict[str, str], site: str) -> tuple[str | None, str | None]:
    """The META tag Google issues for this site (reading it changes nothing)."""
    data, why = await _http.post_json(
        f"{VERIFY_API}/token", headers=auth,
        json_body={"site": {"type": "SITE", "identifier": site},
                   "verificationMethod": "META"})
    tag = str((data or {}).get("token") or "") if why is None else ""
    if not tag:
        return None, f"no verification tag from Google: {why or 'empty answer'}"
    return tag, None


async def _live(auth: dict[str, str], site: str) -> tuple[bool, str, str | None]:
    tag, why = await _tag(auth, site)
    if tag is None:
        return False, "", why
    page, why = await _page(site)
    if page is None:
        return False, tag, f"could not read {site}: {why}"
    return tag in page, tag, None


async def verify_site_dry(site_url: str) -> ActionResult:
    site, why = _site(site_url)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_SITES, SCOPE_VERIFY)
    if auth is None:
        return _no(why or NEED_WRITE)
    live, tag, why = await _live(auth, site)
    if why is not None:
        return _no(why)
    if not live:
        return _no(f"the verification tag is not on the page yet: put {tag} in the "
                   f"<head> of {site} and deploy it, then verify")
    return ActionResult(True, f"the verification tag is live on {site}: would ask "
                              "Google to verify it")


async def verify_site(site_url: str) -> ActionResult:
    site, why = _site(site_url)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_SITES, SCOPE_VERIFY)
    if auth is None:
        return _no(why or NEED_WRITE)
    live, tag, why = await _live(auth, site)
    if why is not None:
        return _no(why)
    if not live:
        return _no(f"the verification tag is not on the page yet: put {tag} in the "
                   f"<head> of {site} and deploy it, then verify")
    data, why = await _http.post_json(
        f"{VERIFY_API}/webResource", headers=auth, params={"verificationMethod": "META"},
        json_body={"site": {"type": "SITE", "identifier": site}})
    if why is not None:
        return ActionResult(False, f"Google did not verify {site}: {why}")
    return ActionResult(True, f"{site} is verified", rollback_token=site,
                        evidence={"resource": str((data or {}).get("id") or "")})


async def unverify_site(token: str) -> ActionResult:
    site, why = _site(token)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_VERIFY)
    if auth is None:
        return _no(why or NEED_WRITE)
    _, why = await _http.send_body(
        "DELETE", f"{VERIFY_API}/webResource/{quote(site, safe='')}", headers=auth)
    if why is not None:
        return ActionResult(False, f"Google refused to drop the verification: {why}")
    return ActionResult(True, f"dropped the verification of {site}")


# --------------------------------------------------------------- sitemap

def _sitemap(site: str, sitemap_url: str) -> tuple[str | None, str | None]:
    url = str(sitemap_url or "").strip()
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != urlsplit(site).hostname:
        return None, f"the sitemap must be an https URL on the same host as {site}"
    return url, None


async def submit_sitemap_dry(site_url: str, sitemap_url: str) -> ActionResult:
    site, why = _site(site_url)
    if site is None:
        return _no(why or "bad property")
    smap, why = _sitemap(site, sitemap_url)
    if smap is None:
        return _no(why or "bad sitemap")
    auth, why = await _auth(SCOPE_SITES)
    if auth is None:
        return _no(why or NEED_WRITE)
    page, why = await _page(smap)
    if page is None or "<urlset" not in page and "<sitemapindex" not in page:
        return _no(f"{smap} does not serve a sitemap yet: {why or 'no <urlset>'}")
    return ActionResult(True, f"would submit {smap} for {site}")


async def submit_sitemap(site_url: str, sitemap_url: str) -> ActionResult:
    dry = await submit_sitemap_dry(site_url, sitemap_url)
    if not dry.ok:
        return dry
    site = _site(site_url)[0] or ""
    smap = str(sitemap_url).strip()
    auth, why = await _auth(SCOPE_SITES)
    if auth is None:
        return _no(why or NEED_WRITE)
    _, why = await _http.send_body(
        "PUT", f"{gsc.API}/sites/{quote(site, safe='')}/sitemaps/{quote(smap, safe='')}",
        headers=auth)
    if why is not None:
        return ActionResult(False, f"Google refused the sitemap: {gsc.forbidden(why)}")
    return ActionResult(True, f"submitted {smap} for {site}",
                        rollback_token=f"{site} {smap}")


async def remove_sitemap(token: str) -> ActionResult:
    site_url, _, smap = str(token).partition(" ")
    site, why = _site(site_url)
    if site is None:
        return _no(why or "bad property")
    auth, why = await _auth(SCOPE_SITES)
    if auth is None:
        return _no(why or NEED_WRITE)
    _, why = await _http.send_body(
        "DELETE", f"{gsc.API}/sites/{quote(site, safe='')}/sitemaps/{quote(smap, safe='')}",
        headers=auth)
    if why is not None:
        return ActionResult(False, f"Google refused to remove the sitemap: {why}")
    return ActionResult(True, f"removed the sitemap {smap}")


def params_site() -> dict[str, str]:
    return {"site_url": "the property home, https://<host>/ (a real domain, never a "
                       "Vercel preview)"}


def describe(params: dict[str, Any]) -> str:
    return f"Search Console {params.get('site_url', '')}"


# ------------------------------------------------------------------ cards
# Raising the Tier 2 cards is the missing half: the three actions above only
# ran if something proposed them, and nothing did, so the Board said "no
# property" while the approvals list stayed empty. This reads (read-only
# scope) and proposes; the writes still wait for the owner's approval.

MMT_DOMAIN = "tutoring.example"


def own_hosts(academy_host: str | None) -> list[str]:
    """Our own public sites only (the store and TutorSite). Client sites and
    previews are never registered by us."""
    import os
    mmt = os.environ.get("MMT_PUBLIC_DOMAIN", MMT_DOMAIN).strip().lower()
    hosts = [h for h in (str(academy_host or "").lower(), mmt) if h]
    return list(dict.fromkeys(h for h in hosts if _site(f"https://{h}/")[0]))


async def raise_cards(academy_host: str | None) -> dict[str, str]:
    """One card per missing step per own site: add, then verify, then sitemap.
    Deduplicated while a card is pending; unreadable Google raises nothing."""
    from ...kernel import approvals
    tok, why = await gsc.access_token()
    if tok is None:
        return {"cards": f"not raised: {why}"}
    auth = {"Authorization": f"Bearer {tok}"}
    data, why = await _http.get_json(f"{gsc.API}/sites", headers=auth)
    if why is not None:
        return {"cards": f"not raised: {gsc.forbidden(why)}"}
    entries = list((data or {}).get("siteEntry") or [])
    out: dict[str, str] = {}
    for host in own_hosts(academy_host):
        site = f"https://{host}/"
        mine = [e for e in entries if str(e.get("siteUrl")) in {site, f"sc-domain:{host}"}]
        if not mine:
            action, params, why_ = ("seo.gsc_add_site", {"site_url": site},
                                    f"{host} is not a Search Console property yet")
        elif all(str(e.get("permissionLevel")) == "siteUnverifiedUser" for e in mine):
            action, params, why_ = ("seo.gsc_verify_site", {"site_url": site},
                                    f"{host} is added but not verified")
        else:
            prop = str(mine[0].get("siteUrl"))
            maps, mwhy = await gsc._sitemaps(prop, auth)
            if mwhy is not None or maps:
                out[host] = "nothing to raise" if mwhy is None else f"unread: {mwhy}"
                continue
            action = "seo.gsc_submit_sitemap"
            params = {"site_url": site, "sitemap_url": f"{site}sitemap.xml"}
            why_ = f"{host} is verified but has no sitemap submitted"
        try:
            pid = await approvals.propose(
                action, params, f"{why_}; approving lets ATLAS do this one step.",
                rollback_plan="the action's own rollback removes what it added",
                dedup_key=f"{action}:{host}")
            out[host] = f"card #{pid} ({action})"
        except Exception as exc:                        # noqa: BLE001
            out[host] = f"card held: {type(exc).__name__}"
    return out
