"""Item 25: findings reach email, not only chat.

Slack, Teams, Discord, Telegram, ntfy, PagerDuty and webhooks were all
notification channels. Email -- the one every customer already has, and the
only one that still works when a chat tool is down -- was not one, even
though the hub could already send letters.
"""
from unittest.mock import patch

from guardian_hub import mailer
from guardian_hub.config import settings
from guardian_hub.integrations import registry
from guardian_hub.integrations.base import Message, SendContext
from guardian_hub.integrations.email import compose


def _msg(**over):
    base = dict(title="Open port 23 on the printer", body="Telnet sends its password in the clear.",
                severity="CRIT", url="https://my.guarder.io/inbox?row=alert:printer",
                actions=[("Acknowledge", "https://my.guarder.io/act/tok/ack")], kind="alert")
    base.update(over)
    return Message(**base)


def test_the_subject_says_how_bad_it_is_before_it_says_anything_else():
    """A phone shows the first few words of a subject and nothing else."""
    subject, _ = compose(_msg(), "https://my.guarder.io")
    assert subject.startswith("Vault URGENT:")
    assert compose(_msg(severity="WARN"), "")[0].startswith("Vault:")


def test_the_body_carries_the_link_and_lists_the_actions():
    _, body = compose(_msg(), "https://my.guarder.io")
    assert "Telnet sends its password" in body
    assert "https://my.guarder.io/inbox?row=alert:printer" in body
    # Listed, never followed for them: a client that prefetches links would
    # acknowledge a customer's alerts on their behalf.
    assert "Acknowledge: https://my.guarder.io/act/tok/ack" in body


def test_with_no_url_it_still_points_somewhere_useful():
    _, body = compose(_msg(url=""), "https://my.guarder.io/")
    assert body.rstrip().endswith("/inbox") or "/inbox" in body


def test_it_sends_from_the_alerts_address_not_the_one_that_sends_login_codes(client):
    """A customer filtering findings into a folder must not also be filing
    their OTPs there."""
    integ = registry.REGISTRY["email"]
    ctx = SendContext(tenant=None, secret="ops@northwind.example", secrets={}, config={},
                      db=None, base_url="https://my.guarder.io")
    with patch.object(mailer, "send", return_value=True) as sent:
        res = integ.send(ctx, _msg())
    assert res.ok
    assert sent.call_args.kwargs["sender"] == settings.smtp_from_alerts
    assert "alerts@guarder.io" in settings.smtp_from_alerts


def test_an_unconfigured_hub_says_so_instead_of_looking_connected(client):
    """The same rule item 47 applies to the Support page: a channel that
    looks green and delivers nothing is worse than one that is visibly off."""
    integ = registry.REGISTRY["email"]
    ctx = SendContext(tenant=None, secret="ops@northwind.example", secrets={}, config={},
                      db=None, base_url="https://my.guarder.io")
    with patch.object(settings, "smtp_host", ""), patch.object(settings, "dev_console_otp", False):
        res = integ.send(ctx, _msg())
    assert res.ok is False
    assert "SMTP is not configured" in res.error


def test_no_address_is_not_a_silent_success(client):
    integ = registry.REGISTRY["email"]
    ctx = SendContext(tenant=None, secret="", secrets={}, config={}, db=None, base_url="")
    assert integ.send(ctx, _msg()).ok is False
