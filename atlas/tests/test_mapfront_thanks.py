"""The thank-you page is honest, light and carries the same contact rule as the landing."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "atlas" / "modules" / "agency"
        / "landing" / "thanks" / "index.html")
HTML = PAGE.read_text()


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "title"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script", "title"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _visible() -> str:
    p = _Text()
    p.feed(HTML)
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


def test_copy_is_true_to_a_mailto_form():
    text = _visible()
    assert "number or address you gave" not in text
    assert "within a day" in text


def test_light_brand_look():
    low = HTML.lower()
    assert "#0a0d14" in low and "#5b8cff" in low
    assert "radial-gradient" not in low and "linear-gradient" not in low


def test_no_external_requests_or_scripts():
    assert not re.search(r"""(src|href)=["']https?://""", HTML.lower())
    # The only script is the same-origin /contact.js that sends the follow-up form.
    scripts = re.findall(r"<script[^>]*>", HTML.lower())
    assert scripts == ['<script src="/contact.js" defer>']
    assert "noindex" in HTML


def test_contact_is_the_gmail_and_the_phone():
    assert 'href="mailto:owner@example.com"' in HTML
    assert 'href="tel:+1 555 010 0142"' in HTML
    assert set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", HTML)) == {"owner@example.com"}


def test_only_the_phone_is_a_figure():
    rest = _visible().replace("+1 555 010 0142", "").replace("owner@example.com", "")
    rest = rest.replace("agency.storefront.test", "")        # the site's own host name
    assert not re.search(r"\d", rest), rest
