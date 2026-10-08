"""The end-of-trial letters.

Miro's rule for a trial-only hub: when the three months are nearly up, email
and ask whether they want to carry on. If they reply, a subscription is
arranged by hand. If they do not, the trial simply ends -- nothing charged,
nothing deleted.

What has to hold, in the order it would hurt:

1. **Nobody is mailed twice.** Both letters are latched by state, not by a
   clock: the warning by `trial_warned`, the ending by the `trialing ->
   paused` transition itself. The job runs hourly, so a leaky latch is an
   hourly email to a customer.
2. **The demo tenant is never mailed.** Its admin is a real person and the
   tenant is reseeded on a schedule.
3. **A refused mailbox never un-pauses an account.** The state change is
   committed before any letter is attempted.
4. **No letter quotes a price.** The public site quotes none by design; an
   email that did would re-publish exactly what was taken off it.
"""

import datetime as dt

import pytest

from guardian_hub import billing, mailer, models as m, plans, scheduler
from guardian_hub.db import SessionLocal


def _tenant(db, slug: str, *, profile_email: str = "") -> m.Tenant:
    t = m.Tenant(slug=slug, name=slug.title(),
                 settings={"profile": {"email": profile_email}} if profile_email else {})
    db.add(t); db.commit()
    return t


def _member(db, tenant: m.Tenant, username: str, email: str, role: str) -> None:
    u = m.User(username=username, email=email)
    db.add(u); db.commit()
    db.add(m.Membership(user_id=u.id, tenant_id=tenant.id, role=role)); db.commit()


def _trial(db, tenant: m.Tenant, *, ends_in_days: float) -> m.Subscription:
    sub = m.Subscription(
        tenant_id=tenant.id, plan="trial", interval="month", status="trialing",
        trial_end=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=ends_in_days),
    )
    db.add(sub); db.commit()
    return sub


@pytest.fixture
def db(client):
    """A session on the test database. `client` builds the schema."""
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def sent(monkeypatch):
    """Capture letters instead of sending them. Nothing here touches SMTP."""
    out = {"ending": [], "ended": []}
    monkeypatch.setattr(mailer, "send_trial_ending",
                        lambda email, name, days, on: out["ending"].append((email, name, days, on)))
    monkeypatch.setattr(mailer, "send_trial_ended",
                        lambda email, name: out["ended"].append((email, name)))
    return out


def test_a_trial_near_its_end_is_warned_once_and_never_again(db, sent):
    t = _tenant(db, "acme", profile_email="owner@acme.test")
    _trial(db, t, ends_in_days=3)

    scheduler.trial_expiry_job()
    assert [e[0] for e in sent["ending"]] == ["owner@acme.test"]
    assert sent["ending"][0][2] in (2, 3)          # days left, as the letter says it
    assert sent["ended"] == []                     # it has not ended yet

    scheduler.trial_expiry_job()                   # the hourly re-run
    assert len(sent["ending"]) == 1, "the trial_warned latch leaked"


def test_a_trial_with_months_to_run_is_left_alone(db, sent):
    t = _tenant(db, "later", profile_email="owner@later.test")
    _trial(db, t, ends_in_days=plans.TRIAL_DAYS)
    scheduler.trial_expiry_job()
    assert sent["ending"] == [] and sent["ended"] == []


def test_an_elapsed_trial_pauses_and_is_written_to_once(db, sent):
    t = _tenant(db, "done", profile_email="owner@done.test")
    sub = _trial(db, t, ends_in_days=-1)

    scheduler.trial_expiry_job()
    db.refresh(sub)
    assert sub.status == "paused"
    assert sent["ended"] == [("owner@done.test", "Done")]
    assert billing.ingest_allowed(sub) is False    # the trial really is over

    scheduler.trial_expiry_job()
    assert len(sent["ended"]) == 1, "a paused account was mailed a second time"


def test_admins_are_written_to_when_no_profile_address_is_set(db, sent):
    t = _tenant(db, "noprofile")
    _member(db, t, "boss", "boss@noprofile.test", "admin")
    _member(db, t, "watcher", "watcher@noprofile.test", "viewer")
    _trial(db, t, ends_in_days=-1)

    scheduler.trial_expiry_job()
    # The admin who can act on it, and not the viewer who cannot.
    assert [e[0] for e in sent["ended"]] == ["boss@noprofile.test"]


def test_the_demo_tenant_is_never_mailed(db, sent):
    t = _tenant(db, "demo", profile_email="demo@guarder.test")
    _member(db, t, "demoadmin", "real.person@guarder.test", "admin")
    sub = _trial(db, t, ends_in_days=-1)

    scheduler.trial_expiry_job()
    db.refresh(sub)
    assert sub.status == "paused"                  # it still expires
    assert sent["ending"] == [] and sent["ended"] == []


def test_a_refused_mailbox_does_not_un_pause_the_account(db, monkeypatch):
    t = _tenant(db, "bounces", profile_email="full@bounces.test")
    sub = _trial(db, t, ends_in_days=-1)

    def boom(*a, **k):
        raise RuntimeError("550 mailbox unavailable")
    monkeypatch.setattr(mailer, "send_trial_ended", boom)

    scheduler.trial_expiry_job()                   # must not raise
    db.refresh(sub)
    assert sub.status == "paused"


def test_one_bad_address_does_not_cost_the_others_their_letter(db, monkeypatch):
    t = _tenant(db, "mixed", profile_email="bad@mixed.test")
    _member(db, t, "good", "good@mixed.test", "admin")
    _trial(db, t, ends_in_days=-1)

    got = []
    def picky(email, name):
        if email.startswith("bad@"):
            raise RuntimeError("550 no such user")
        got.append(email)
    monkeypatch.setattr(mailer, "send_trial_ended", picky)

    scheduler.trial_expiry_job()
    assert got == ["good@mixed.test"]


def test_neither_letter_quotes_a_price(monkeypatch):
    """The site was stripped of prices on purpose; the mail must match.

    Both parts are checked: a price that leaked only into the HTML would be
    the one most readers actually see.
    """
    parts = []
    # Both letters go through `mailer.send`, the one primitive: it sanitises
    # header values, carries the optional HTML alternative and the mark, and
    # swallows SMTP failures so a notification cannot break the background
    # job that triggered it.
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, text, html="": parts.append(subject + text + html))
    mailer.send_trial_ending("x@y.test", "Acme", 14, "16 December 2026")
    mailer.send_trial_ended("x@y.test", "Acme")
    both = "".join(parts)
    assert len(parts) == 2

    for price in {plans.price_label(p, i) for p in plans.PAID_PLANS for i in plans.INTERVALS}:
        assert price not in both
    assert "$" not in both
    # It has to say the two things the reader needs: how to continue, and
    # what happens if they do nothing.
    assert "reply" in both.lower()
    assert "nothing is charged" in both.lower() or "nothing was charged" in both.lower()


def test_each_letter_carries_the_mark_and_a_readable_text_part():
    """A letter is built as text + HTML + the logo, in that order of trust.

    The plain part is the whole message, not a stub, and the mark rides
    along as an attachment rather than a remote URL -- Gmail and Outlook
    block remote images by default.
    """
    import email as _email

    sent = {}
    original = mailer.smtplib.SMTP

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): sent["msg"] = msg

    mailer.smtplib.SMTP = _FakeSMTP
    was_console = mailer.settings.dev_console_otp
    try:
        # `send` collects into SENT and returns early while dev_console_otp is
        # on (conftest sets it, so nothing in the suite posts real mail), and
        # this test is specifically about the message that gets built on the
        # real path -- so it has to turn that short-circuit off.
        mailer.settings.dev_console_otp = False
        mailer.settings.smtp_host = "smtp.test"
        mailer.send_trial_ending("x@y.test", "Acme", 14, "16 December 2026")
    finally:
        mailer.smtplib.SMTP = original
        mailer.settings.smtp_host = ""
        mailer.settings.dev_console_otp = was_console

    msg = sent["msg"]
    types = {part.get_content_type() for part in msg.walk()}
    assert "text/plain" in types and "text/html" in types and "image/png" in types

    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "reply to this email" in text and "16 December 2026" in text
    html = msg.get_body(preferencelist=("html",)).get_content()
    # The mark is referenced by content-id, never by a URL the client has to
    # go and fetch.
    assert 'src="cid:guardian-logo"' in html
    assert "http://" not in html
    assert "16 December 2026" in html and "Acme" in html
