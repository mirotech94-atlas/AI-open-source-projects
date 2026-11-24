"""The store's Help page: FAQ, the support policy and how to ask.

    files(cat) -> {"help/index.html": ...}

Pure apart from the environment (the community links), like `site.render`,
whose page chrome (`site._page`) it reuses so the Help page looks like every
other page of the store.

What it says, and only that:

* The FAQ is `support_kb.faq_entries` -- the same words the support
  assistant answers with -- so the page and the replies cannot drift.
* Scope (access and downloads, kit setup, broken workflows), the reply
  promise (two business days, New York business days), how to report a
  broken workflow, and that custom builds are a paid ATLAS Agency service with
  no price on this page (the quote is the owner's).
* How to ask: since 2026-11-14 "Ask the owner" posts to `/public/agency/contact` (same
  origin): stored first, then emailed and sent to the owner's WhatsApp, with Reply-To set to
  the visitor. The mailto address stays on the page as plain text.
* The community links appear only when ACADEMY_SLACK_COMMUNITY_URL /
  ACADEMY_TEAMS_COMMUNITY_URL are set to https URLs.
"""
from __future__ import annotations

from html import escape

from .. import store_origin
from .catalog import Catalog
from .support import community_urls
from .support_kb import REPLY_DAYS_WORD, faq_entries

ACTION = "/public/agency/contact"   # the API route, same origin (Caddyfile.public)
SUBJECT_MAX = 200
MESSAGE_MIN, MESSAGE_MAX = 10, 4000


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _faq(cat: Catalog) -> str:
    items = "".join(f"<details><summary>{_e(q)}</summary><p>{_e(a)}</p></details>"
                    for q, a in faq_entries(cat))
    return f'<h2 id="faq">Common questions</h2>{items}'


def _form(cat: Catalog) -> str:
    mail = _e(store_origin.CONTACT_MAIL)
    return f"""<h2 id="ask">Ask the owner</h2>
<p>Write below, or email <a href="mailto:{mail}">{mail}</a>. The support assistant answers
what the product files cover; anything else goes to the owner. Either way you get a reply
within {REPLY_DAYS_WORD} business days.</p>
<form class="contact" method="post" action="{_e(ACTION)}" data-contact-form>
<input type="hidden" name="source" value="store-help">
<input type="hidden" name="topic" value="support">
<p class="hp" aria-hidden="true"><label>Leave this empty <input type="text" name="website" tabindex="-1" autocomplete="off"></label></p>
<label><span>Your name</span>
<input name="name" type="text" required maxlength="80" autocomplete="name"></label>
<label><span>Your email</span>
<input name="email" type="email" required maxlength="254" autocomplete="email" inputmode="email"></label>
<label><span>Your question, and which product it is about</span>
<textarea id="ask-message" name="message" required minlength="{MESSAGE_MIN}" maxlength="{MESSAGE_MAX}" rows="7"></textarea></label>
<div><button class="btn btn-buy" type="submit" data-contact-submit>Send to the owner</button></div>
<p class="status" role="status" aria-live="polite" data-contact-status></p>
<p class="note">Writing about an order? Use the address you paid with. Never send
passwords, API keys or tokens.</p>
</form>"""


def _policy(cat: Catalog) -> str:
    return f"""<h2 id="covers">What support covers</h2>
<ul>
<li><b>Access and downloads:</b> your access page, expired download links, a file that
won't open. Lost your access page? <a href="../recover/">Here's how to get it back</a>
with your Stripe receipt number.</li>
<li><b>Setting up the kits:</b> Docker, n8n, Claude and Obsidian, as each guide
describes.</li>
<li><b>Broken workflows and errors</b> in the files you bought.</li>
</ul>
<p><b>Custom builds are a paid service.</b> Building something new for you, changing a
workflow to fit your business, or setting it up on your systems is done by ATLAS Agency,
the owner's studio, and quoted separately. Ask by email and the owner replies with a
quote.</p>
<h2 id="broken">Reporting a broken workflow</h2>
<ul>
<li>Which product, and which workflow, chapter or lesson.</li>
<li>What you did, step by step, and what you expected.</li>
<li>The exact error text, or a screenshot.</li>
<li>For n8n: your n8n version and whether it runs in Docker. For Claude or Obsidian:
which app, and which Claude plan.</li>
<li>Never passwords, API keys or tokens.</li>
</ul>
<h2 id="reply">When you'll hear back</h2>
<p>Within {REPLY_DAYS_WORD} business days (Monday to Friday, New York time). The AI
support assistant answers questions the product files cover, usually much sooner, and
signs every reply as the assistant. Refunds, anything about money, and anything it is
unsure of go to the owner himself. Refund terms are on the
<a href="../refunds/">refunds page</a>.</p>"""


def _community() -> str:
    links = community_urls()
    if not links:
        return ""
    items = []
    if "slack" in links:
        items.append(f'<li><a href="{_e(links["slack"])}" rel="noopener">Join the '
                     "ATLAS Academy community on Slack</a></li>")
    if "teams" in links:
        items.append(f'<li><a href="{_e(links["teams"])}" rel="noopener">Join the '
                     "ATLAS Academy community on Microsoft Teams</a></li>")
    return ('<h2 id="community">Community</h2><p>Ask other buyers and share what you '
            "built. Support questions still go through this page.</p>"
            f"<ul>{''.join(items)}</ul>")


def help_body(cat: Catalog) -> str:
    return ('<section class="page"><div class="wrap">'
            '<div class="crumbs"><a href="../academy/">Home</a><span>/</span>Help</div>'
            "<h1>Help</h1><div class=\"prose\">"
            "<p class=\"lede\">Help with your purchase, your downloads and setting up "
            f"the kits. Every question gets a reply within {REPLY_DAYS_WORD} business "
            "days.</p>"
            + _faq(cat) + _policy(cat) + _community() + _form(cat)
            + "</div></div></section>")


def files(cat: Catalog) -> dict[str, bytes]:
    """The Help page, for `site.render`."""
    from .site import _page
    return {
        "help/index.html": _page(cat, title="Help", description=(
            f"Help with {cat.store['name']} purchases, downloads and kit setup."),
            body=help_body(cat), depth=1, current="help",
            head='<script src="../assets/contact.js" defer></script>').encode(),
    }
