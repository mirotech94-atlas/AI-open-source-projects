"""The letters Vault sends, as a design system.

Six letters share one shell and differ only where the reader's job differs.
What is worth protecting is not the pixels but the handful of properties that
keep the set coherent and legible, each of which a later edit could quietly
undo:

* every letter is text AND html, with the mark travelling as an attachment;
* nothing is fetched at render time (remote images are blocked by default, so
  a hosted logo or webfont is a hole in the page, not a fallback);
* the letter's kind is stated in WORDS as well as by the rail colour, so a
  reader in a colour-mangling client still knows what arrived;
* the decline carries no facts box and no links, on purpose.
"""

import base64
import re

import pytest

from guardian_hub import mailer
from guardian_hub.config import settings


class _Lead:
    id = "lead1"
    name = "Dana Whitfield"
    email = "dana@example.test"
    company = "Northwind Dental"
    site_name = "Northwind Dental — Queens"
    network_size = "one-site"
    note = "Two servers and about 30 devices."


@pytest.fixture
def letters(monkeypatch):
    """Every letter the hub can send, as {name: (subject, text, html)}."""
    monkeypatch.setattr(settings, "dev_console_otp", False)
    monkeypatch.setattr(settings, "base_url", "https://my.guarder.io")
    out = {}
    key = {"n": None}

    def cap(to, subject, text, *, html=""):
        out[key["n"]] = (subject, text, html)

    monkeypatch.setattr(mailer, "send", cap)

    key["n"] = "otp";        mailer.send_otp("a@b.test", "482917")
    key["n"] = "ready";      mailer.send_account_ready("a@b.test", "dana", "Northwind Dental",
                                                       user_existed=False)
    key["n"] = "request";    mailer.send_lead_notice("a@b.test", _Lead())
    key["n"] = "declined";   mailer.send_lead_declined("a@b.test")
    key["n"] = "ending";     mailer.send_trial_ending("a@b.test", "Northwind Dental", 14,
                                                      "16 December 2026")
    key["n"] = "ended";      mailer.send_trial_ended("a@b.test", "Northwind Dental")
    key["n"] = "ticket_new"; mailer.send_ticket_raised(
        "support@guarder.io", ticket_id="tk1", subject_line="Sensor keeps dropping off",
        tenant_name="Northwind Dental", who="dana",
        body="It goes offline every night around 2am.", priority="high")
    key["n"] = "ticket_reply"; mailer.send_ticket_reply(
        "a@b.test", ticket_id="tk1", subject_line="Sensor keeps dropping off",
        tenant_name="Northwind Dental", body="Found it: the power strip is on a timer.",
        from_owner=True, author="miro")
    key["n"] = "ticket_done"; mailer.send_ticket_resolved(
        "a@b.test", ticket_id="tk1", subject_line="Sensor keeps dropping off",
        tenant_name="Northwind Dental", note="Timer removed. Reporting since 03:10.")
    return out


def test_every_letter_has_both_parts_and_a_subject(letters):
    assert set(letters) == {"otp", "ready", "request", "declined", "ending", "ended",
                            "ticket_new", "ticket_reply", "ticket_done"}
    for name, (subject, text, html) in letters.items():
        assert subject.strip(), name
        assert len(text.strip()) > 40, f"{name}: the plain part must be the whole message"
        assert html.strip().startswith("<!doctype html>"), name


def test_nothing_is_fetched_when_the_letter_is_opened(letters):
    """Gmail and Outlook block remote content by default."""
    for name, (_, _, html) in letters.items():
        assert 'src="cid:guardian-logo"' in html, f"{name}: mark must ride along, not be fetched"
        assert "http://" not in html, name
        # The only permitted absolute URLs are ones the reader clicks, never
        # anything the client would load on open.
        for url in re.findall(r'(?:src|url\()\s*=?\s*["\']?(https?://[^"\')\s]+)', html):
            pytest.fail(f"{name}: loads {url} on open")
        assert "@font-face" not in html and "fonts.googleapis" not in html, name


def test_each_kind_says_what_it_is_in_words_not_only_in_colour(letters):
    expected = {
        "otp": "Sign-in code", "ready": "Account", "request": "Access request",
        "declined": "Access request", "ending": "Trial", "ended": "Trial",
        "ticket_new": "Support", "ticket_reply": "Support", "ticket_done": "Support",
    }
    for name, label in expected.items():
        assert label in letters[name][2], f"{name}: missing the '{label}' marker"


def test_the_rail_colour_separates_granted_from_decide_from_settled(letters):
    def rail(name):
        # the 3px cell that opens the body block
        m = re.search(r'width="3" style="background:(#[0-9a-f]{6});', letters[name][2])
        assert m, f"{name}: no status rail"
        return m.group(1)

    assert rail("ready") == "#1f7a4d"                     # granted -> green
    assert rail("request") == rail("ending") == "#a6631a"  # needs a person -> amber
    assert rail("declined") == rail("ended") == "#6b7a90"  # settled -> grey
    assert rail("otp") == "#123fbf"                        # the product acting -> blue
    # A ticket in progress is the product acting; a closed one is settled and
    # green, like every other "this is now done" letter in the set.
    assert rail("ticket_new") == rail("ticket_reply") == "#123fbf"
    assert rail("ticket_done") == "#1f7a4d"
    # No letter in this set is an alarm, so none of them may wear the alert red.
    for name, (_, _, html) in letters.items():
        assert "#a3273a" not in html, f"{name}: uses the alarm colour"


def test_the_sign_in_code_is_the_letter(letters):
    subject, text, html = letters["otp"]
    assert "482917" in subject and "482917" in text and "482917" in html
    # Set at display size in the monospace face: it is read off a phone and
    # selected as one word.
    assert re.search(r'font:600 34px/1 [^;]*monospace[^;]*;[^"]*letter-spacing', html), \
        "the code should be the one display-size element"
    assert "works once" in html


def test_the_decline_stays_bare(letters):
    """No panel, no readout, and the only link is the footer's.

    A "no" with a call to action attached reads as a sales letter. Two
    sentences and the door left open is the whole design.
    """
    _, text, html = letters["declined"]
    assert text == mailer.DECLINE_TEXT, "the wording is the decision; it must not drift"
    assert "border-radius" not in html, "no panel: the restraint is the design"
    hrefs = re.findall(r'<a href="([^"]+)"', html)
    assert hrefs == ["https://my.guarder.io"], f"the footer link should be the only one: {hrefs}"


def test_the_request_gives_its_two_links_different_words(letters):
    html = letters["request"][2]
    labels = re.findall(r'font-weight:600;">([^<]+)</a>', html)
    assert len(labels) == 2 and len(set(labels)) == 2, \
        f"both decision links read the same: {labels}"
    assert "decide=approve" in html and "decide=reject" in html
    assert _Lead.note in html, "the applicant's own words belong in the brief"


def test_no_letter_quotes_a_price(letters):
    """The public site quotes none; the mail must match."""
    from guardian_hub import plans
    for name, (subject, text, html) in letters.items():
        blob = subject + text + html
        assert "$" not in blob, name
        for p in plans.PAID_PLANS:
            for i in plans.INTERVALS:
                assert plans.price_label(p, i) not in blob, name


def test_values_from_a_stranger_are_escaped(letters):
    """`send_lead_notice`'s rows are whatever was typed into a public form."""
    class Nasty(_Lead):
        name = '<script>alert(1)</script>'
        note = '<img src=x onerror=alert(1)>'

    out = {}
    import guardian_hub.mailer as mod
    real = mod.send
    try:
        mod.send = lambda to, subject, text, *, html="": out.update(html=html, subject=subject)
        mod.send_lead_notice("a@b.test", Nasty())
    finally:
        mod.send = real
    html = out["html"]
    # The property is that it is INERT, not that the letters never appear:
    # escaped text legitimately contains the word "onerror" as content.
    assert "<script" not in html and "<img src=x" not in html, "a stranger's markup rendered raw"
    assert "&lt;script&gt;" in html, "the value should still be shown, escaped"
    assert "onerror=alert(1)&gt;" in html, "the note should read back as typed, inert"
    # A Subject is a header, not markup -- what matters is that it cannot be
    # split into extra headers.
    assert "\n" not in out["subject"] and "\r" not in out["subject"]


def test_a_ticket_letter_sends_the_reader_to_the_thread_not_to_a_reply(letters):
    """guarder.io does not receive mail into the app: Resend's receiving is
    off and Zoho holds the apex MX. So a ticket letter that reads like a
    reply-able email produces an answer nobody in the conversation sees.

    Each of the three therefore has to (a) link into the thread and (b) say
    that answering the letter is not the way."""
    for name in ("ticket_new", "ticket_reply", "ticket_done"):
        subject, text, html = letters[name]
        assert "/support/tk1" in html or "/owner/tickets/tk1" in html, name
        # And the plain part carries the same link, for a text-only reader.
        assert "tk1" in text, name

    for name in ("ticket_new", "ticket_reply"):
        _s, text, html = letters[name]
        assert "thread" in html.lower(), name
        assert "does not" in text.lower(), f"{name}: must say a reply here reaches nobody"


def test_the_operators_letter_leads_with_triage_and_quotes_the_customer(letters):
    subject, text, html = letters["ticket_new"]
    # Which customer, at a glance, in the subject line.
    assert "Northwind Dental" in subject
    # The facts that decide whether to open it now.
    for fact in ("Customer", "Raised by", "Priority"):
        assert fact in html, fact
    # Their words, not a paraphrase.
    assert "2am" in html and "2am" in text


def test_a_resolved_letter_says_how_to_disagree(letters):
    _subject, text, html = letters["ticket_done"]
    assert "Resolved" in html
    assert "reopen" in html.lower() and "reopen" in text.lower()
