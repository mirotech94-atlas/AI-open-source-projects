"""The e-mail that carries an enrolment code.

It exists because the person who can put a sensor on the LAN is usually not the
person who signed up -- it is the IT contractor, or the owner's son, or nobody
yet. A code on a screen only helps if the right person is standing in front of
it; an e-mail is forwardable, and forwarding IS the delivery mechanism.

Which makes what the e-mail contains a security question, because the code is a
bearer credential: whoever holds it can attach a sensor to this tenant.
"""
from guardian_hub import mailer

CODE = "ABCD2345EFGH6789"


def _send(**over):
    mailer.SENT.clear()
    kwargs = dict(to="owner@example.com", tenant_name="Northwind Dental",
                  network_name="Clinic Wi-Fi", code=CODE,
                  expires_on="27 September 2026",
                  install_cmd=f"curl -fsSL https://my.guarder.io/install | sh -s {CODE}")
    kwargs.update(over)
    ok = mailer.send_sensor_install(**kwargs)
    return ok, (mailer.SENT[-1] if mailer.SENT else None)


def test_the_email_says_which_network_it_is_for():
    """A customer with three LANs gets three of these. One that did not name
    its network would put the sensor on the wrong one, and the customer would
    then be watching an estate that is not the one they were looking at."""
    _ok, sent = _send()
    _to, subject, text = sent
    assert "Clinic Wi-Fi" in subject or "Clinic Wi-Fi" in text
    assert "Northwind Dental" in text


def test_the_email_carries_the_command_and_says_when_it_stops_working():
    _ok, (_to, _subject, text) = _send()
    assert "curl -fsSL https://my.guarder.io/install" in text
    assert "27 September 2026" in text


def test_the_code_is_in_the_body_and_never_in_the_subject():
    """A subject line shows on a lock screen, in a notification popup, and in
    the logs of every mail server between here and the recipient. A bearer
    credential belongs in the body, which only the recipient opens."""
    _ok, (_to, subject, text) = _send()
    assert CODE not in subject
    assert CODE in text


def test_it_reports_whether_the_letter_actually_went():
    """`mailer.send` returns a bool because a relay accepting a message is not
    the same as somebody receiving it -- Zoho accepted Vault's mail for two
    days while Gmail silently discarded it. A sender that swallowed that would
    let the wizard tell a customer instructions are on the way when nothing
    left the building."""
    ok, _sent = _send()
    assert ok is True


def test_the_network_name_is_escaped_where_it_becomes_markup(monkeypatch):
    """The client typed this name into the wizard, and "a customer typed it" is
    not the same as "it is safe as markup" -- `send_account_ready` makes the
    same point about names the owner typed.

    Asserted on the HTML part specifically. The plain-text body SHOULD carry
    the raw name; it is not markup, and escaping it there would show a customer
    `&lt;` in their own network's name.
    """
    captured = {}

    def fake_send(to, subject, text, *, html=""):
        captured["html"] = html
        return True

    monkeypatch.setattr(mailer, "send", fake_send)
    mailer.send_sensor_install(
        to="owner@example.com", tenant_name="Northwind Dental",
        network_name="<script>alert(1)</script>", code=CODE,
        expires_on="27 September 2026", install_cmd="curl -fsSL https://x/install | sh",
    )
    html = captured["html"]
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_failed_send_is_reported_as_failed(monkeypatch):
    """The caller is about to tell a customer their instructions are on the
    way. If the relay refused, it must not say that."""
    monkeypatch.setattr(mailer, "send", lambda *a, **k: False)
    assert mailer.send_sensor_install(
        to="owner@example.com", tenant_name="T", network_name="N", code=CODE,
        expires_on="soon", install_cmd="x") is False
