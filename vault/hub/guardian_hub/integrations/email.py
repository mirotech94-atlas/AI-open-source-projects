"""Email as a notification channel.

Item 25 asked for findings to reach "Slack (or the client's integration)
AND email". Slack, Teams, Discord, Telegram, ntfy, PagerDuty and webhooks
were all here; email -- the one channel every customer already has, and the
only one that still works when a chat tool is down -- was not. The hub could
send a letter (`mailer`), but nothing ever asked it to send an alert.

Two things about this provider are deliberate:

**It sends from its own address.** Everything else the hub posts is
transactional mail about an account -- a code, a receipt, a ticket reply.
A security finding is a different kind of letter, and a customer filtering
`alerts@guarder.io` into its own folder should not also be filing their
login codes there. `settings.smtp_from_alerts` is that address, and it is a
setting rather than a constant because the address has to match a mailbox
the configured SMTP account is allowed to send as.

**It reports honestly when the hub cannot send.** With no `smtp_host`,
`mailer.send` logs and returns False rather than raising. That is returned
as a failed `SendResult` with the reason, so the Integrations tab says
"SMTP is not configured on this hub" instead of showing a green channel
that silently delivers nothing -- the same rule item 47 applies to the
Support page.
"""

from __future__ import annotations

from .. import mailer
from ..config import settings
from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register

#: A subject tells the reader what happened and how bad it is before they
#: open anything. The severity leads because a phone shows the first words.
_PREFIX = {"CRIT": "Vault URGENT", "WARN": "Vault", "INFO": "Vault"}


def compose_html(msg: Message, base_url: str) -> str:
    """The same finding as a Vault letter: severity rail, the words, the link
    as the one button, the act links listed and never auto-followed. The plain
    part from `compose` stays the whole message; this is the alternative."""
    kind = {"CRIT": mailer.KIND_FINDING_CRIT, "WARN": mailer.KIND_FINDING_WARN}.get(
        msg.severity, mailer.KIND_FINDING_INFO)
    url = msg.url or ((base_url.rstrip("/") + "/inbox") if base_url else "")
    blocks = [mailer._p(mailer._esc(msg.body.strip()))]
    if url:
        blocks += [mailer._gap(18), mailer._button(url, "Open in Vault")]
    if msg.actions:
        blocks += [mailer._gap(18),
                   mailer._p("Or act from here (each link does exactly what it says, once):",
                             quiet=True), mailer._gap(8)]
        blocks += [mailer._p(mailer._link(u, label)) for label, u in msg.actions[:3]]
    return mailer._shell(kind, mailer._esc(msg.title), blocks)


def compose(msg: Message, base_url: str) -> tuple[str, str]:
    """(subject, body) for one message. Pure, so a test can read it."""
    subject = f"{_PREFIX.get(msg.severity, 'Vault')}: {msg.title}".strip()
    lines = [msg.body.strip()]
    if msg.url:
        lines += ["", msg.url]
    elif base_url:
        lines += ["", base_url.rstrip("/") + "/inbox"]
    # The actions carry one-click act tokens. They are listed, never
    # auto-followed: a mail client that prefetches links would otherwise
    # acknowledge a customer's alerts for them.
    for label, url in msg.actions[:3]:
        lines.append(f"{label}: {url}")
    return subject, "\n".join(lines).strip()


@register
class EmailIntegration(Integration):
    kind = "email"
    title = "Email"
    help = ("Findings and resolutions by email, from alerts@guarder.io. The one "
            "channel that still works when a chat tool is down.")
    direction = "out"
    # Not a secret: an address the customer types, and one they will want to
    # read back to check it. Masking it would only make it unverifiable.
    fields = [IntegrationField(name="address", label="Send to", kind="text", required=True)]

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        to = (ctx.secret or "").strip()
        if not to:
            return SendResult(False, "no address configured")
        if not settings.smtp_host and not settings.dev_console_otp:
            # Said plainly, because a channel that looks connected and
            # delivers nothing is worse than one that is visibly off.
            return SendResult(False, "SMTP is not configured on this hub, so no mail can leave it")
        subject, body = compose(msg, ctx.base_url)
        if mailer.send(to, subject, body, html=compose_html(msg, ctx.base_url),
                       sender=settings.smtp_from_alerts):
            return SendResult(True, "")
        return SendResult(False, "the mail server refused the message")
