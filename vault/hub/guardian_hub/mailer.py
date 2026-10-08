import logging
import smtplib, ssl
from email.message import EmailMessage
from html import escape as _esc
from pathlib import Path

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .config import settings

log = logging.getLogger(__name__)

#: Em dash. Named because an f-string expression part cannot contain a
#: backslash escape, and these letters interpolate it in a dozen places.
DASH = "\u2014"


# LAST_CODES / SENT are dev/test-only: populated ONLY when settings.dev_console_otp
# is set (local dev, and hub/tests/conftest.py sets GUARDIAN_DEV_CONSOLE_OTP=1),
# so tests can read what would otherwise have been emailed. A real deployment
# must never set dev_console_otp, and in that case both stay empty.
LAST_CODES: dict[str, str] = {}


SENT: list[tuple[str, str, str]] = []


# The site's own wording for its "What are you looking after?" answers, so the
# owner's email reads the way the person answered.
SIZE_LABEL = {"home": "Home", "one-site": "One office or shop",
              "few-sites": "A handful of sites", "msp": "Client sites (MSP or reseller)"}


#: The product's own palette, so a letter looks like a readout from the
#: dashboard rather than a newsletter about it. These are the same values
#: `web/src/styles/tokens.css` uses for text, hairlines and the primary
#: accent; the blues are the two facets of the shield in the mark.
INK, PAPER, EDGE, ACCENT, MUTED = "#0b1b2e", "#ffffff", "#dde5f0", "#123fbf", "#4a6280"

#: The one tinted surface in a letter. Used for a single panel per letter at
#: most -- the sign-in code, a quoted note -- never as a card background.
WASH = "#f4f8fd"

#: What kind of letter this is, as a colour and a word. Vault already
#: colours state this way in the app (good / warn / alert on the dashboard,
#: and the topology map's links), so a reader who has seen the product reads
#: the rail without being taught it.
#:
#: The word matters as much as the colour: it is the same signal in text, so
#: the letter still says what it is to a reader who cannot see the rail, in a
#: dark-mode client that mangles it, or in a plain-text-only view.
#:
#: Deliberately NO red. A decline and an elapsed trial are not failures, and
#: the alarm colour belongs to things that are actually broken -- if Vault
#: ever mails about an outage, that letter gets it and these do not.
KIND_SYSTEM  = ("Sign-in code",   ACCENT)     # blue  -- the product acting
KIND_GRANTED = ("Account",        "#1f7a4d")  # green -- something is now yours
KIND_DECIDE  = ("Access request", "#a6631a")  # amber -- waiting on a person
KIND_TRIAL   = ("Trial",          "#a6631a")  # amber -- a date is approaching
KIND_CLOSED  = ("Trial",          "#6b7a90")  # grey  -- settled, nothing owed
KIND_DECLINE = ("Access request", "#6b7a90")
KIND_TICKET  = ("Support",        ACCENT)     # blue  -- a conversation in progress
KIND_TICKET_DONE = ("Support",    "#1f7a4d")  # green -- answered and closed
#: Findings from the email channel. The one place the alarm colour is right:
#: a critical finding is something actually broken on the customer's network.
KIND_FINDING_CRIT = ("Finding",   "#a3273a")  # red   -- act today
KIND_FINDING_WARN = ("Finding",   "#a6631a")  # amber -- worth a look
KIND_FINDING_INFO = ("Finding",   ACCENT)     # blue  -- for the record


#: No webfonts: Gmail strips `@font-face`, so a letter that depends on one
#: renders in whatever the client picks. Prose gets the system stack; the
#: monospace face is used only for *values*, which is exactly what the app
#: does with `--font-num` and its `.mono` class -- it is a convention that
#: carries meaning, not decoration.
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"


_LOGO = Path(__file__).resolve().parent / "assets" / "guardian-logo.png"


def _header(value: str) -> str:
    """Collapse CR/LF and other control characters to a single space, so a
    value that ends up in an email header (Subject, To) can never inject an
    extra header or split into multiple lines -- e.g. a lead's `name` reaches
    `send_lead_notice`'s Subject unsanitised, and `EmailMessage` itself only
    raises on the raw `\\r`/`\\n`, which is exactly the crash this guards."""
    return "".join(" " if ord(c) < 32 or c == "\x7f" else c for c in value)


#: Domain suffixes that can NEVER receive mail. Deliberately the short list:
#: `.invalid` (RFC 2606) is what the hub's own seeded and demo accounts use
#: (`demoadmin@guardian.invalid`), and `localhost` resolves to the machine
#: itself. A caller that records "we emailed them" after handing one of these
#: to `send` records a fiction.
#:
#: `.test` and `.example` are reserved too, but they are NOT here: real test
#: fixtures and self-hosted internal domains use them, and an SMTP server
#: refusing them is the mail system's business, not this function's. The
#: point of this list is the addresses Vault itself invents.
UNDELIVERABLE = (".invalid", ".localhost", "@localhost")


def deliverable(to: str) -> bool:
    """False for an address no mail can ever reach. Not validation -- a real
    typo is the mail server's business; this only catches the reserved
    domains that are guaranteed not to exist."""
    addr = (to or "").strip().lower()
    if "@" not in addr:
        return False
    return not any(addr.endswith(suffix) for suffix in UNDELIVERABLE)


def send(to: str, subject: str, text: str, *, html: str = "", sender: str = "") -> bool:
    """Send one letter: plain text, with an optional HTML alternative.

    Returns whether the letter actually went out, so a caller that RECORDS
    having notified somebody can record the truth. It used to return None and
    swallow everything, which meant a ticket reply to a demo account with a
    `.invalid` address was stamped "emailed" identically to one that arrived.

    The one primitive every Vault mail goes through, and it has to stay
    that way. Two senders existed briefly, on separate branches, and they had
    already drifted: only one of them sanitised header values, and only one
    of them actually kept the promise both docstrings made about not raising.

    The plain part is never a stub: a reader whose client shows text only
    gets the whole message, which is also what keeps the letter readable to
    anything that strips HTML.

    Never raises into a caller. Some callers are request handlers, where a
    lost notification must not fail a sign-in or a form; the rest are
    background jobs, where it must not roll back the state change that
    prompted it. Both want the same thing, so this swallows and logs.
    """
    if not to:
        # A caller with nobody to write to (an owner with no address on file,
        # a lead row with the email cleared) is not an error worth logging on
        # every pass; there is simply no letter to send.
        return False
    if not deliverable(to):
        log.info("[guardian] not mailing %s: reserved, undeliverable domain", to)
        return False
    to, subject = _header(to), _header(subject)
    if settings.dev_console_otp:
        SENT.append((to, subject, text))
        # Dev/test-only, but a long-running dev process (or a test suite that
        # sends a lot of mail) must not let this grow without bound.
        del SENT[:-50]
        print(f"[guardian] mail to {to}: {subject}", flush=True)
        return True
    if not settings.smtp_host:
        log.warning("[guardian] SMTP not configured; mail to %s (%s) was not delivered", to, subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender or settings.smtp_from
        msg["To"] = to
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
            # The mark travels with the letter rather than being fetched:
            # Gmail and Outlook both block remote images by default, and a
            # logo that only appears after the reader clicks "show images"
            # is worse than no logo at all.
            try:
                png = _LOGO.read_bytes()
            except OSError:
                log.warning("[guardian] logo asset missing at %s; sending without it", _LOGO)
            else:
                msg.get_payload()[1].add_related(
                    png, maintype="image", subtype="png", cid="<guardian-logo>",
                    filename="vault.png",
                )
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001 -- notification only
        log.warning("[guardian] mail to %s failed: %s", to, str(e)[:200])
        return False
    return True


def send_otp(email: str, code: str) -> None:
    if settings.dev_console_otp:
        LAST_CODES[email] = code
        print(f"[guardian] OTP for {email}: {code}", flush=True)
        return
    minutes = settings.otp_ttl_seconds // 60
    send(email, f"{code} is your Vault sign-in code",
         f"Your Vault sign-in code is {code}. It expires in {minutes} minutes.\n"
         f"If you did not request it, ignore this email.",
         html=_shell(KIND_SYSTEM, "Your sign-in code", [
             _p("Enter this in the browser tab you started signing in from."),
             _gap(18),
             _code_panel(code, minutes),
             _gap(),
             _p("If you did not ask to sign in, you can ignore this email. The code "
                "only works together with your username, and only once; nobody at "
                "Vault can read it.", quiet=True),
         ]))


def owner_addresses(db: DbSession) -> list[str]:
    """`GUARDIAN_OWNER_EMAIL` (comma-separated), else every superadmin's email."""
    configured = [a.strip() for a in (settings.owner_email or "").split(",") if a.strip()]
    if configured:
        return configured
    return [u.email for u in db.query(m.User).filter_by(is_superadmin=True).order_by(m.User.username)
            if u.email]


DECLINE_TEXT = ("Thanks for your interest in Vault. We can't set you up at the moment.\n"
                "If that changes we will be in touch.")


def _trial_length() -> str:
    """The trial's length in the owner's words, read from the live setting so a
    letter can never promise "three months" after the console changed it to
    thirty days. Imported here rather than at module top: platform_settings
    reads the database, and mailer must stay importable by everything."""
    from . import platform_settings
    days = int(platform_settings.trial_days())
    months = {30: "month", 60: "two months", 90: "three months", 180: "six months", 365: "year"}
    return months.get(days, f"{days} days")


def _keep_running() -> tuple[str, str]:
    """How to continue after the trial, as (plain, html), read from the billing
    mode. In `trial_only` mode there is no plan to buy, so the answer is a
    reply; in `plans` mode the dashboard sells one, so the letter says where,
    and still offers the reply for anyone who would rather a person did it.
    Neither quotes a price -- what a plan costs is settled where it is bought.
    Both keep the word "reply", which `test_trial_emails` pins.
    """
    from . import platform_settings
    if platform_settings.trial_only():
        plain = ("reply to this email and tell us what you need covered. We will sort out a "
                 f"subscription with you directly {DASH} there is no card form and nothing to click.")
        return plain, plain
    url = f"{settings.base_url.rstrip('/')}/settings/billing"
    plain = (f"choose a plan under Settings > Billing in your dashboard ({url}). A card is asked "
             "for only there, when you choose. Or reply to this email if you would rather we "
             "set it up with you.")
    html = (f"choose a plan under {_link(url, 'Settings ' + DASH + ' Billing')} in your dashboard. "
            "A card is asked for only there, when you choose. Or reply to this email if you would "
            "rather we set it up with you.")
    return plain, html


def send_account_ready(to: str, username: str, tenant_name: str, *, user_existed: bool) -> None:
    """Approval: the first letter a customer ever gets from Vault.

    Fixed copy. The only variable parts are the username and the tenant name
    the owner confirmed, both of which the owner typed on the Leads page --
    they are still escaped where they reach the HTML, because "the owner
    typed it" is not the same as "it is safe as markup".

    The sign-in link carries `?u=` so the login page can prefill the username;
    it grants nothing on its own, and the code still goes to this address.
    """
    from urllib.parse import quote
    base = settings.base_url.rstrip("/")
    link = f"{base}/login?u={quote(username, safe='')}"
    shown = link.split("://", 1)[-1]
    if user_existed:
        how = (f"Your existing sign-in ({username}) works here too {DASH} {tenant_name} now appears "
               f"in your account list.")
        how_html = (f"Your existing sign-in (<strong>{_esc(username)}</strong>) works here too "
                    f"{DASH} {_esc(tenant_name)} now appears in your account list.")
    else:
        how = (f"Your username is {username}. Open the link, and we will email you a 6-digit code "
               f"to sign in {DASH} there is no password to remember.")
        how_html = (f"Your username is <strong>{_esc(username)}</strong>. Open the link and Vault "
                    f"emails you a six-digit code {DASH} there is no password to remember.")
    text = f"Your Vault account for {tenant_name} is ready.\n\n{how}\n\nSign in: {link}\n"
    send(to, "Your Vault account is ready", text,
         html=_shell(KIND_GRANTED, "Your account is ready", [
             _p(f"Vault is set up for {_esc(tenant_name)}. Everything is included for the "
                f"first {_trial_length()}, and there is no card on the account."),
             _gap(18),
             _facts([("Organisation", tenant_name), ("Username", username)]),
             _gap(),
             _p(how_html),
             _gap(18),
             _button(link, "Sign in to Vault"),
             _gap(16),
             _p(f"Or open {_esc(shown)} yourself.", quiet=True),
         ]))


def send_sensor_install(to: str, *, tenant_name: str, network_name: str,
                        code: str, expires_on: str, install_cmd: str) -> bool:
    """How to put a sensor on ONE network, sent to whoever can actually do it.

    One of these per declared network, each naming its own. The person who can
    reach a machine on the LAN is usually not the person who signed up -- it is
    the IT contractor, or the owner's son, or nobody yet -- and a code shown on
    a screen only helps if the right person is standing in front of it. This
    letter is forwardable, and forwarding is the point.

    Naming the network is not decoration. A customer with a clinic Wi-Fi and a
    lab VLAN gets two of these, the codes bind to different networks on
    redemption (`routers/sensor.enrol`), and a letter that did not say which
    was which would put the sensor on the wrong network -- after which the
    customer is watching an estate that is not the one they think they are.

    THE CODE IS NOT IN THE SUBJECT. A subject line shows on a lock screen, in
    a notification popup, and in the logs of every mail server between here and
    the recipient. The code is a bearer credential -- whoever holds it can
    attach a sensor to this tenant -- so it belongs in the body, which only the
    recipient opens.

    Returns whether the letter actually went, like every sender here should:
    a relay accepting a message is not somebody receiving it, and the caller
    is about to tell a customer that instructions are on the way.
    """
    subject = f"Set up Vault on {network_name}"
    text = (
        f"Vault is ready to watch {network_name} for {tenant_name}.\n\n"
        f"Run this on any machine that stays on that network:\n\n"
        f"    {install_cmd}\n\n"
        f"Enrolment code: {code}\n"
        f"It stops working on {expires_on}.\n\n"
        f"The machine needs to be on {network_name} itself {DASH} a sensor sees only "
        f"the network it is plugged into, so each network you asked us to watch gets "
        f"its own letter like this one.\n\n"
        f"If somebody else looks after your computers, forward this to them.\n"
    )
    return send(to, subject, text,
                html=_shell(KIND_SYSTEM, "Set up Vault on this network", [
                    _p(f"Vault is ready to watch <strong>{_esc(network_name)}</strong> for "
                       f"{_esc(tenant_name)}. Run one command on any machine that stays on "
                       f"that network."),
                    _gap(18),
                    _facts([("Network", network_name), ("Organisation", tenant_name),
                            ("Code", code), ("Works until", expires_on)], boxed=True),
                    _gap(18),
                    _p(f"<code>{_esc(install_cmd)}</code>"),
                    _gap(18),
                    _p(f"The machine has to be on {_esc(network_name)} itself {DASH} a sensor sees "
                       f"only the network it is plugged into, which is why each network gets its "
                       f"own letter.", quiet=True),
                    _gap(12),
                    _p("If somebody else looks after your computers, forward this to them.",
                       quiet=True),
                ]))


def send_lead_declined(to: str) -> None:
    """The decline. Short on purpose, and it does not explain itself: a reason
    invites an argument about the reason, and there is often no reason worth
    giving beyond capacity.

    The plain part stays exactly `DECLINE_TEXT` -- `test_lead_decisions.py`
    compares against it, and more importantly the wording is the decision, so
    it should not drift between the two parts of the same letter.
    """
    send(to, "About your Vault access request", DECLINE_TEXT,
         html=_shell(KIND_DECLINE, "About your request", [
             _p("Thanks for your interest in Vault. We can\u2019t set you up at the "
                "moment."),
             _gap(14),
             _p("If that changes we will be in touch. Nothing further is needed from you."),
         ]))


def send_lead_notice(to: str, lead: m.Lead) -> None:
    """The owner's copy of one access request, with the two decision links.

    Both links open the app -- a signed-in superadmin page that asks for
    confirmation -- and never act by themselves. That is deliberate: mail
    scanners and link previewers fetch every URL in a message, so a link that
    approved an account on GET would be handing accounts to a spam filter.

    Everything in the fact rows was typed by a stranger into a public form.
    `_facts` escapes it; the pieces interpolated into prose here are escaped
    at this call site, for the same reason.
    """
    base = settings.base_url.rstrip("/")
    approve = f"{base}/settings/leads?lead={lead.id}&decide=approve"
    reject = f"{base}/settings/leads?lead={lead.id}&decide=reject"
    who = lead.name or lead.email
    size = SIZE_LABEL.get(lead.network_size, lead.network_size or DASH)
    company = lead.company or DASH
    site_name = lead.site_name or DASH
    note = lead.note or DASH
    rows = [
        ("Name", lead.name or DASH),
        ("Email", lead.email),
        ("Company", company),
        ("Site name", site_name),
        ("Looking after", size),
    ]
    text = (
        "Someone asked for access to Vault.\n\n"
        f"Name:          {lead.name}\n"
        f"Email:         {lead.email}\n"
        f"Company:       {company}\n"
        f"Site name:     {site_name}\n"
        f"Looking after: {size}\n"
        f"Note:          {note}\n\n"
        f"Approve (creates their account): {approve}\n"
        f"Reject: {reject}\n\n"
        "Either link opens the Leads page in the app and asks you to confirm."
    )
    blocks = [
        _p(f"{_esc(who)} asked for access to Vault."),
        _gap(18),
        _facts(rows, boxed=True),
    ]
    if lead.note:
        blocks += [_gap(18), _p("In their words:", quiet=True), _gap(8), _quote(lead.note)]
    blocks += [
        _gap(),
        _p(f"Neither link acts on its own {DASH} both open the Leads page in the app and "
           "ask you to confirm there."),
        _gap(14),
        # Distinct link text on purpose: both URLs open the same page, so
        # identical wording would give a reader scanning only the links (or a
        # screen reader listing them) no way to tell the two apart.
        _branches([
            ("Approve", f"creates the account and emails them their sign-in "
                        f"{DASH} {_link(approve, 'review and approve')}."),
            ("Reject", f"sends a short decline and nothing else "
                       f"{DASH} {_link(reject, 'review and reject')}."),
        ]),
    ]
    send(to, f"Access request: {who}", text,
         html=_shell(KIND_DECIDE, "Access request", blocks))


def _facts(rows: list[tuple[str, str]], *, boxed: bool = False) -> str:
    """Label left, value right, hairline between -- the dashboard's own fact
    rows, as a table because Outlook does not do grid.

    `boxed` draws the enclosing border. Only a letter that is genuinely a
    *readout* takes it (an access request, a trial's standing); a letter that
    happens to carry two pieces of context does not, because an enclosing box
    around every group is how a page turns into a deck of identical cards.
    """
    if not rows:
        # The decline has nothing to tabulate, and an empty bordered box reads
        # as a rendering fault rather than as brevity.
        return ""
    cells = []
    for i, (label, value) in enumerate(rows):
        # Escaped here, in the one place a value is rendered, because some of
        # these come from a stranger: `send_lead_notice`'s rows are whatever
        # was typed into the public sign-up form. Callers pass raw values.
        label, value = _esc(str(label)), _esc(str(value))
        top = "" if i == 0 else f"border-top:1px solid {EDGE};"
        pad = "11px 14px" if boxed else "10px 0"
        cells.append(
            f'<tr>'
            f'<td style="{top}padding:{pad};font:400 14px/1.4 {SANS};color:{MUTED};'
            f'white-space:nowrap;">{label}</td>'
            f'<td style="{top}padding:{pad};font:500 14px/1.4 {MONO};color:{INK};'
            f'text-align:right;">{value}</td>'
            f'</tr>'
        )
    frame = (f'border:1px solid {EDGE};border-radius:3px;' if boxed
             else f'border-top:1px solid {EDGE};border-bottom:1px solid {EDGE};')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="{frame}border-collapse:separate;">' + "".join(cells) + "</table>")


def _code_panel(code: str, minutes: int) -> str:
    """The sign-in code, as the one thing in that letter worth looking at.

    Left-aligned like everything else -- a centred block would be the only
    centred thing in any Vault letter -- but set in the monospace face at
    display size and tracked out, so the digits are readable off a phone at
    arm's length and select cleanly as one word.
    """
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{WASH};border:1px solid {EDGE};border-radius:3px;">'
        f'<tr><td style="padding:18px 20px 16px;">'
        f'<div style="font:600 34px/1 {MONO};color:{INK};letter-spacing:0.18em;">{_esc(code)}</div>'
        f'<div style="margin-top:10px;font:400 13px/1.5 {SANS};color:{MUTED};">'
        f'Expires in {minutes} minutes, and works once.</div>'
        f'</td></tr></table>'
    )


def _quote(text: str) -> str:
    """Someone else's words, kept visibly theirs: a hairline on the left and
    the prose set slightly quieter than Vault's own voice around it."""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<tr><td style="border-left:2px solid {EDGE};padding:2px 0 2px 14px;'
            f'font:400 15px/1.6 {SANS};color:{MUTED};">{_esc(text)}</td></tr></table>')


def _button(href: str, label: str) -> str:
    """One primary action, and only where following it is safe.

    The decision links in an access request are deliberately NOT buttons: that
    letter says neither link acts on its own, and a button argues the opposite.
    """
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="background:{ACCENT};border-radius:3px;">'
            f'<a href="{_esc(href)}" style="display:inline-block;padding:11px 20px;'
            f'font:600 15px/1 {SANS};color:#ffffff;text-decoration:none;">{_esc(label)}</a>'
            f'</td></tr></table>')


def _link(href: str, label: str) -> str:
    return (f'<a href="{_esc(href)}" style="color:{ACCENT};text-decoration:none;'
            f'font-weight:600;">{_esc(label)}</a>')


def _p(html: str, *, quiet: bool = False) -> str:
    colour = MUTED if quiet else INK
    size = "13px/1.6" if quiet else "15px/1.65"
    return f'<p style="margin:0;font:400 {size} {SANS};color:{colour};">{html}</p>'


def _branches(items: list[tuple[str, str]]) -> str:
    """The two or three ways this letter can end, each led by its condition.

    The lead-in is a condition, not an accent on a word: "To keep it running,"
    tells the reader which paragraph is theirs before they read it.
    """
    return "".join(
        f'<p style="margin:0 0 14px;font:400 15px/1.65 {SANS};color:{INK};">'
        f'<strong style="font-weight:600;">{cond}</strong> {text}</p>'
        for cond, text in items
    )


def _gap(px: int = 22) -> str:
    return f'<div style="height:{px}px;line-height:{px}px;">&nbsp;</div>'


def _shell(kind: tuple[str, str], headline: str, blocks: list[str]) -> str:
    """One letter: 560px, single column, left-aligned throughout.

    A centred transactional email reads as marketing, and none of these are.
    The rail down the body is the only bold element; everything else is set
    quietly so the rail and the headline carry the whole message at a glance.
    """
    kind_label, rail = kind
    body = "".join(b for b in blocks if b)
    return f"""<!doctype html>
<html>
<head>
<!-- The MIME part already declares utf-8, but some webmail extracts this
     body and re-serves it on its own, at which point the part header is
     gone and an em dash becomes mojibake. One line, and it cannot hurt. -->
<meta charset="utf-8">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
</head>
<body style="margin:0;padding:0;background:{PAPER};">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background:{PAPER};">
 <tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="560"
         style="width:560px;max-width:100%;">

   <tr><td style="padding:0 0 16px;border-bottom:1px solid {EDGE};">
     <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
      <tr>
       <td width="28" style="padding-right:10px;" valign="middle">
         <img src="cid:guardian-logo" width="28" height="28" alt="Vault"
              style="display:block;width:28px;height:28px;border:0;">
       </td>
       <td valign="middle" style="font:600 17px/1 {SANS};color:{INK};letter-spacing:-0.01em;">
         Vault
       </td>
       <td valign="middle" align="right"
           style="font:400 12px/1 {MONO};color:{MUTED};white-space:nowrap;">
         {kind_label}
       </td>
      </tr>
     </table>
   </td></tr>

   <tr><td style="padding:26px 0 0;">
     <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
      <tr>
       <td width="3" style="background:{rail};" valign="top"></td>
       <td style="padding:0 0 0 22px;" valign="top">
         <h1 style="margin:0 0 14px;font:600 22px/1.3 {SANS};color:{INK};
                    letter-spacing:-0.01em;">{headline}</h1>
         {body}
       </td>
      </tr>
     </table>
   </td></tr>

   <tr><td style="padding:26px 0 0;">
     <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
            style="border-top:1px solid {EDGE};">
      <tr><td style="padding:16px 0 0;font:400 13px/1.6 {SANS};color:{MUTED};">
        Vault watches the machines, the websites and the traffic you are
        responsible for, and repairs the routine problems itself.
        <a href="https://my.guarder.io"
           style="color:{ACCENT};text-decoration:none;">my.guarder.io</a>
      </td></tr>
     </table>
   </td></tr>

  </table>
 </td></tr>
</table>
</body></html>"""


def send_trial_ending(email: str, tenant_name: str, days_left: int, ends_on: str) -> None:
    """The one warning letter, sent once per trial (see `billing.mark_trial_warned`).

    It asks a question and quotes no prices: what a plan costs is settled in
    the reply, not published here -- the same reason the public site quotes
    none. It also says what happens if the answer is nothing at all, because
    that is the half of the arrangement that protects the reader.
    """
    day = "day" if days_left == 1 else "days"
    subject = f"Your Vault trial ends in {days_left} {day}"
    keep_text, keep_html = _keep_running()
    text = f"""Hello,

Your free Vault trial for {tenant_name} ends in {days_left} {day}, on {ends_on}.

If you would like to keep Vault running, {keep_text}

If you would rather not, you do not need to do anything. On {ends_on} the
account simply pauses: nothing is charged, nothing is deleted, and everything
Vault has already collected stays readable and exportable for you. The
sensors stop reporting new data, and that is the end of it.

-- Vault
"""
    html = _shell(KIND_TRIAL, f"Your trial ends in {days_left} {day}", [
        _p("No card was ever taken, so nothing will be charged when it does."),
        _gap(18),
        _facts([("Account", tenant_name),
                ("Trial ends", ends_on),
                ("Days left", str(days_left))], boxed=True),
        _gap(),
        _branches([
            ("To keep it running,", keep_html),
            ("To let it end,",
             f"do nothing at all. On {ends_on} the account pauses: nothing is charged, nothing "
             "is deleted, and everything already collected stays readable and exportable. The "
             "sensors stop reporting new data, and that is the end of it."),
        ]),
    ])
    send(email, subject, text, html=html)


def send_trial_ended(email: str, tenant_name: str) -> None:
    """Sent once, from the pass that actually paused the account."""
    subject = "Your Vault trial has ended"
    keep_text, keep_html = _keep_running()
    text = f"""Hello,

The free Vault trial for {tenant_name} has ended, so the sensors have
stopped reporting new data.

Nothing was charged -- no card was ever taken -- and nothing has been deleted.
Everything Vault collected during the trial is still there, still readable
and still exportable from your dashboard.

If you would like to pick it back up, {keep_text} Collection resumes where it
left off.
If we do not hear from you, that is perfectly fine and nothing further happens.

-- Vault
"""
    html = _shell(KIND_CLOSED, "Your trial has ended", [
        _p("The sensors have stopped reporting new data. Nothing was charged, and "
           "nothing was deleted."),
        _gap(18),
        _facts([("Account", tenant_name),
                ("Status", "Paused"),
                ("Your data", "Kept")], boxed=True),
        _gap(),
        _branches([
            ("To pick it back up,", keep_html + " Collection resumes where it left off."),
            ("To leave it,",
             "nothing further happens. Your dashboard stays readable and your data stays "
             "exportable; no card was ever taken, so there is nothing to cancel."),
        ]),
    ])
    send(email, subject, text, html=html)


# --------------------------------------------------------------------------
# Support tickets.
#
# The hard constraint these letters are shaped by: guarder.io does not
# ACCEPT mail into the app. Resend's receiving is deliberately off and Zoho
# holds the apex MX, so a reply to any of these reaches a human mailbox, not
# the ticket. The in-app thread is the system of record.
#
# So every letter here carries a link straight into the thread and says to
# answer there. None of them is written in a way that invites a reply, and
# none of them pretends the email is the conversation -- it is the tap on the
# shoulder that says the conversation moved.
# --------------------------------------------------------------------------

def _ticket_url(ticket_id: str, *, owner: bool) -> str:
    base = settings.base_url.rstrip("/")
    if owner:
        # The operator reads tickets in the owner console, which is the same
        # app on its own hostname.
        return f"{base.replace('//my.', '//admin.')}/owner/tickets/{ticket_id}"
    return f"{base}/support/{ticket_id}"


def send_ticket_raised(to: str, *, ticket_id: str, subject_line: str, tenant_name: str,
                       who: str, body: str, priority: str) -> bool:
    """To the OPERATOR: a customer has opened a ticket.

    Goes to `settings.support_email` -- a mailbox the owner actually reads.
    The facts come first because this letter is triage: which customer, how
    urgent, who asked. Their words are quoted rather than paraphrased.
    """
    subject = f"[{tenant_name}] {subject_line}"
    url = _ticket_url(ticket_id, owner=True)
    text = f"""{who} at {tenant_name} opened a support ticket.

Subject:  {subject_line}
Priority: {priority}

What they said:

{body}

Answer it here: {url}

Replying to this email does not reach them -- the thread in the app does.

-- Vault
"""
    html = _shell(KIND_TICKET, "A customer needs help", [
        _facts([("Customer", tenant_name),
                ("Raised by", who),
                ("Priority", priority),
                ("Subject", subject_line)], boxed=True),
        _gap(18),
        _p("What they said:", quiet=True),
        _gap(8),
        _quote(body),
        _gap(),
        _button(url, "Open the ticket"),
        _gap(18),
        _p("Answering in the thread both posts it for them and emails them. "
           "A reply to this letter does neither.", quiet=True),
    ])
    return send(to, subject, text, html=html)


def send_ticket_reply(to: str, *, ticket_id: str, subject_line: str, tenant_name: str,
                      body: str, from_owner: bool, author: str) -> bool:
    """To the other side: somebody has answered.

    One sender for both directions, because the letter is the same act either
    way and two would drift. Only the headline and the link differ.
    """
    who = "Vault" if from_owner else author
    subject = f"Re: {subject_line}"
    url = _ticket_url(ticket_id, owner=not from_owner)
    text = f"""{who} replied to your support ticket.

Subject: {subject_line}

{body}

Read it and reply in the thread: {url}

Replying to this email does not reach the thread.

-- Vault
"""
    html = _shell(KIND_TICKET, f"{who} replied", [
        _facts([("Ticket", subject_line),
                ("Account", tenant_name)]),
        _gap(18),
        _quote(body),
        _gap(),
        _button(url, "Reply in the thread"),
        _gap(18),
        _p("Your reply belongs in the thread, not in an answer to this letter "
           f"{DASH} this address does not receive mail.", quiet=True),
    ])
    return send(to, subject, text, html=html)


def send_ticket_resolved(to: str, *, ticket_id: str, subject_line: str, tenant_name: str,
                         note: str = "") -> bool:
    """To the customer: the operator has marked it resolved.

    Green rail, because something is finished rather than pending. It says
    plainly how to disagree: a reply in the thread reopens it. A closed ticket
    that cannot be reopened just produces a duplicate ticket about the same
    problem.
    """
    subject = f"Resolved: {subject_line}"
    url = _ticket_url(ticket_id, owner=False)
    text = f"""Your support ticket has been marked resolved.

Subject: {subject_line}
{('Note: ' + note) if note else ''}

If it is not actually fixed, reply in the thread and it reopens:
{url}

-- Vault
"""
    html = _shell(KIND_TICKET_DONE, "Marked resolved", [
        _facts([("Ticket", subject_line),
                ("Account", tenant_name),
                ("Status", "Resolved")], boxed=True),
        *([_gap(18), _quote(note)] if note else []),
        _gap(),
        _p("If it is not actually fixed, reply in the thread and the ticket reopens."),
        _gap(18),
        _button(url, "Open the thread"),
    ])
    return send(to, subject, text, html=html)
