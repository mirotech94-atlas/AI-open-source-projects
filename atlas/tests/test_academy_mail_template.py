"""The branded Academy email: HTML part with the ATLAS look, text part kept."""
from atlas.modules.academy import mail_template
from atlas.modules.agency import outreach


def test_render_is_branded_and_escapes():
    html = mail_template.render("Hi", "Hello <b>x</b>,\n\nhttps://agency.example.com/academy/confirm/abc\n\nBye")
    assert "#0A0D14" in html and "#5B8CFF" in html and "ATLAS" in html
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert 'href="https://agency.example.com/academy/confirm/abc"' in html
    assert "Confirm my email" in html and "owner@example.com" in html


def test_message_has_text_and_html_parts():
    m = outreach._message("a@b.c", "d@e.f", "S", "plain body", None,
                          html=mail_template.render("S", "plain body"))
    kinds = [p.get_content_type() for p in m.iter_parts()]
    assert kinds == ["text/plain", "text/html"]
    assert m.get_body(("plain",)).get_content().strip() == "plain body"


def test_message_without_html_stays_plain():
    m = outreach._message("a@b.c", "d@e.f", "S", "plain body", None)
    assert m.get_content_type() == "text/plain"
