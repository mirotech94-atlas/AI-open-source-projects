"""What the client told us — the only source a client site's claims come from.

the owner, 2026-09-29, from the "20 things before launch" reel: reviews, case
studies, a team photo, a response-time promise, opening hours and any
stat must be the client's own. None of it is ever generated, paraphrased or
guessed. It lives in one file the owner puts beside the client's other uploads:

    AGENCY_UPLOADS_DIR/<lead_id>/client.json

Deliberately NOT an `agency.build_site` parameter: those are words a model
picks (invariant 2), and a reviews list a model could fill in is a
fabricated review waiting to happen. A file on disk that only a person puts
there is the honest source. Every field is optional; a missing one means
the section that needs it is not rendered and the launch check lists it as
NEEDS_CLIENT. A field that is present but malformed is dropped with a named
reason, never repaired into something the client did not say.

Shape (all keys optional):

    {"name": "...", "tagline": "...", "phone": "...", "email": "...",
     "services": ["Boiler repair", "Bathroom fitting"],
     "address": "one line, as the client writes it",
     "geo": {"lat": 43.66, "lng": -70.25},
     "hours": ["Mo-Fr 08:00-17:00", "Sa 09:00-12:00"],
     "response_time_hours": 2,
     "booking_url": "https://...", "booking_provider": "OpenTable",
     "form_action": "https://...",
     "reviews": [{"author": "...", "text": "...", "rating": 5,
                  "source_url": "https://..."}],
     "case_studies": [{"title": "...", "summary": "...", "outcome": "...",
                       "url": "https://..."}],
     "faqs": [{"q": "...", "a": "..."}],
     "team_photo": "team.jpg", "team_photo_alt": "...",
     "og_image": "share.png",
     "colours": {"primary": "#0f6e8c", "background": "#ffffff",
                 "text": "#1b2430"},
     "package": "500", "area": "Portland and 20 miles around",
     "domain": "https://www.example-plumbing.com"}

`team_photo` and `og_image` are paths inside the same uploads folder,
confined exactly like `reviews_path` (symlinks followed, suffix checked).

`package` is what the client bought: "500", "300" or "100" (dollars). The
$500 and $300 packages include the AI chat assistant (`sitechat.py`); the
retired "100" package does not, and neither does a missing or unrecognised value -- a
tier is a commercial fact a person writes down, never a build argument a
model could pick. `area` is the client's own words for where they work.
`domain` is the client's own address for the site when it has one besides
the preview (`agency.example.com/preview/<slug>/`); it is the only other origin the chat answers. The
lead's old `website` never is: that is the site ATLAS audited, not this one.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CLIENT_FILE = "client.json"

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_DAY = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)"
_HOURS = re.compile(
    rf"^{_DAY}(?:-{_DAY})?(?:,{_DAY}(?:-{_DAY})?)* "
    r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-4]):[0-5]\d$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_COLOURS = {"primary": "#0f6e8c", "background": "#ffffff", "text": "#1b2430"}

PACKAGES = ("500", "300", "100")
CHAT_PACKAGES = frozenset({"500", "300"})
# 2026-10-29: ATLAS runs on the owner's Mac at 127.0.0.1 only, so no visitor's
# browser can reach /public/site-chat/<slug> or /public/call-me/<slug>. While
# there is no public door, a build ships neither the chat widget nor "Have
# Mapfront call me"; the API routes stay for the day one exists. A code
# constant: flipping it is a reviewed change, never a runtime switch.
PUBLIC_DOOR = False


@dataclass(frozen=True, slots=True)
class ClientReview:
    author: str
    text: str
    rating: float | None = None
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class CaseStudy:
    title: str
    summary: str
    outcome: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class Faq:
    q: str
    a: str


@dataclass(frozen=True, slots=True)
class ClientData:
    """Everything a client site may say about the business. Built from the
    lead row (name, category, phone, address, website) overlaid with the
    client's own `client.json`."""
    name: str
    category: str | None = None
    tagline: str | None = None
    services: tuple[str, ...] = ()
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    address: str | None = None
    geo: tuple[float, float] | None = None
    hours: tuple[str, ...] = ()
    response_time_hours: float | None = None
    booking_url: str | None = None
    booking_provider: str | None = None  # "OpenTable", "Booksy"...: the client's word
    form_action: str | None = None
    reviews: tuple[ClientReview, ...] = ()
    case_studies: tuple[CaseStudy, ...] = ()
    faqs: tuple[Faq, ...] = ()
    team_photo: Path | None = None
    team_photo_alt: str | None = None
    og_image: Path | None = None
    colours: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLOURS))
    problems: tuple[str, ...] = ()
    package: str | None = None
    area: str | None = None
    domain: str | None = None           # https://host, from client.json only
    # The designed template family (`sitetemplate.FAMILIES`) the site was
    # built from; set by `agency.build_site`, never read from client.json.
    # None means the plain launch chrome (a store page).
    theme: str | None = None

    @property
    def chat(self) -> bool:
        """Whether this site carries the AI chat assistant: the $500 and $300
        packages only, and only when client.json says which was bought, and
        only while a public door to the chat endpoint exists (`PUBLIC_DOOR`)."""
        return PUBLIC_DOOR and self.package in CHAT_PACKAGES


def origin(website: str | None) -> str | None:
    """`https://host` for a site address, or None when there is no usable
    one. The same normalisation `launch.py` has always used."""
    if not website:
        return None
    parsed = urlparse(website if "://" in website else f"https://{website}")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return f"https://{parsed.netloc.lower()}"


def http_url(value: object) -> str | None:
    """An absolute http(s) URL, or None. Nothing else (javascript:, data:,
    a bare path) is ever put in an href."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value


def resolve_in(root: Path, raw_path: str, allowed_suffixes: tuple[str, ...],
               what: str) -> tuple[Path | None, str | None]:
    """Confine `raw_path` to `root` (symlinks followed) and an allow-listed
    suffix — the check `build._resolve_upload` has always made, shared so
    `client.json`'s own file references get it too. Blocking (`resolve`)."""
    root = root.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None, f"{what} not found: {raw_path}"
    if not resolved.is_relative_to(root):
        return None, (f"{what} must be inside this lead's uploads folder "
                      f"({root}): {raw_path}")
    if resolved.suffix.lower() not in allowed_suffixes:
        return None, (f"{what} must end in one of {', '.join(allowed_suffixes)}: "
                      f"{raw_path}")
    return resolved, None


def _text(value: object, limit: int = 2000) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return " ".join(str(value).split())[:limit]


def parse_reviews(raw: object, problems: list[str]) -> tuple[ClientReview, ...]:
    """`[{author, text, rating?, source_url?}]`. An entry with no text or
    no author is dropped (an anonymous quote is not a review anyone can
    check); a rating outside 1..5 or a source that is not an http(s) URL is
    dropped from that entry, not rounded or guessed."""
    out: list[ClientReview] = []
    for i, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            problems.append(f"reviews[{i}] is not an object; dropped")
            continue
        author, text = _text(item.get("author"), 120), _text(item.get("text"))
        if not author or not text:
            problems.append(f"reviews[{i}] needs both author and text; dropped")
            continue
        rating = item.get("rating")
        if rating is not None and (
                isinstance(rating, bool) or not isinstance(rating, (int, float))
                or not math.isfinite(rating) or not 1 <= rating <= 5):
            problems.append(f"reviews[{i}].rating {rating!r} is not 1-5; left off")
            rating = None
        source = item.get("source_url")
        source_url = http_url(source)
        if source is not None and source_url is None:
            problems.append(f"reviews[{i}].source_url is not an http(s) URL; left off")
        out.append(ClientReview(author, text,
                                None if rating is None else float(rating), source_url))
    return tuple(out)


def _case_studies(raw: object, problems: list[str]) -> tuple[CaseStudy, ...]:
    out: list[CaseStudy] = []
    for i, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            problems.append(f"case_studies[{i}] is not an object; dropped")
            continue
        title, summary = _text(item.get("title"), 160), _text(item.get("summary"))
        if not title or not summary:
            problems.append(f"case_studies[{i}] needs both title and summary; dropped")
            continue
        out.append(CaseStudy(title, summary, _text(item.get("outcome")) or None,
                             http_url(item.get("url"))))
    return tuple(out)


def _faqs(raw: object, problems: list[str]) -> tuple[Faq, ...]:
    out: list[Faq] = []
    for i, item in enumerate(raw if isinstance(raw, list) else []):
        q = _text(item.get("q"), 300) if isinstance(item, dict) else ""
        a = _text(item.get("a")) if isinstance(item, dict) else ""
        if not q or not a:
            problems.append(f"faqs[{i}] needs both q and a; dropped")
            continue
        out.append(Faq(q, a))
    return tuple(out)


def _services(raw: object, problems: list[str]) -> tuple[str, ...]:
    """What the client says they do, as they word it. Only a list of short
    strings; anything else is dropped by name."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append("services must be a list of strings; left off")
        return ()
    out = []
    for i, item in enumerate(raw):
        text = _text(item, 120) if isinstance(item, str) else ""
        if text:
            out.append(text)
        else:
            problems.append(f"services[{i}] is not a short string; dropped")
    return tuple(out[:20])


def _geo(raw: object, problems: list[str]) -> tuple[float, float] | None:
    if raw is None:
        return None
    try:
        lat, lng = float(raw["lat"]), float(raw["lng"])      # type: ignore[index]
    except (TypeError, KeyError, ValueError):
        problems.append("geo needs numeric lat and lng; left off")
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        problems.append("geo is out of range; left off")
        return None
    return lat, lng


def _hours(raw: object, problems: list[str]) -> tuple[str, ...]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        line = _text(item, 60)
        if _HOURS.match(line):
            out.append(line)
        else:
            problems.append(f"hours {item!r} is not like 'Mo-Fr 09:00-17:00'; dropped")
    return tuple(out)


def _response_time(raw: object, problems: list[str]) -> float | None:
    if raw is None:
        return None
    if (isinstance(raw, bool) or not isinstance(raw, (int, float))
            or not math.isfinite(raw) or not 0 < raw <= 24 * 14):
        problems.append(f"response_time_hours {raw!r} is not a positive number of "
                        "hours; left off")
        return None
    return float(raw)


def _package(raw: object, problems: list[str]) -> str | None:
    """"500", "300" or "100" ("$500" and the bare number read the same).
    Anything else is left off by name -- and so is the chat it would bring."""
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if isinstance(raw, str) and raw.strip().lstrip("$") in PACKAGES:
        return raw.strip().lstrip("$")
    problems.append(f"package {raw!r} is not one of {', '.join(PACKAGES)}; left off "
                    "(no chat assistant)")
    return None


def _domain(raw: object, problems: list[str]) -> str | None:
    if raw is None:
        return None
    value = origin(_text(raw, 300))
    if value is None:
        problems.append(f"domain {raw!r} is not a web address; left off")
    return value


def _colours(raw: object, problems: list[str]) -> dict[str, str]:
    colours = dict(DEFAULT_COLOURS)
    if isinstance(raw, dict):
        for key in DEFAULT_COLOURS:
            value = raw.get(key)
            if value is None:
                continue
            if isinstance(value, str) and _HEX.match(value.strip()):
                colours[key] = value.strip().lower()
            else:
                problems.append(f"colours.{key} {value!r} is not #rrggbb; default used")
    return colours


def read_client_file(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The raw `client.json` under `root`, `(None, None)` when there is none,
    or `(None, reason)` when it exists but is not a JSON object — a file
    the owner put there and that ATLAS could not read must stop the build, not
    be silently treated as "no client data". Blocking."""
    path = root / CLIENT_FILE
    if not path.exists():
        return None, None
    resolved, reason = resolve_in(root, CLIENT_FILE, (".json",), CLIENT_FILE)
    if reason is not None:
        return None, reason
    try:
        data = json.loads(resolved.read_text())               # type: ignore[union-attr]
    except (OSError, ValueError) as exc:
        return None, f"{CLIENT_FILE} could not be read as JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{CLIENT_FILE} must be a JSON object"
    return data, None


def client_data(lead: dict[str, Any], raw: dict[str, Any] | None, root: Path, *,
                contact_email: str | None = None,
                extra_reviews: tuple[ClientReview, ...] = ()) -> ClientData:
    """Overlay the client's own file on the lead row. Blocking (it resolves
    `team_photo` / `og_image` on disk)."""
    raw = raw or {}
    problems: list[str] = []

    def _image(key: str) -> Path | None:
        value = raw.get(key)
        if not value:
            return None
        path, reason = resolve_in(root, str(value), IMAGE_SUFFIXES, key)
        if reason is not None:
            problems.append(f"{reason}; left off")
        return path

    reviews = parse_reviews(raw.get("reviews"), problems)
    seen = {r.text for r in reviews}
    reviews += tuple(r for r in extra_reviews if r.text not in seen)
    return ClientData(
        name=_text(raw.get("name"), 160) or _text(lead.get("name"), 160) or "This business",
        category=_text(lead.get("category"), 80) or None,
        tagline=_text(raw.get("tagline"), 200) or None,
        services=_services(raw.get("services"), problems),
        phone=_text(raw.get("phone"), 40) or _text(lead.get("phone"), 40) or None,
        email=(_text(raw.get("email"), 200) or _text(contact_email, 200)
               or _text(lead.get("email"), 200) or None),
        website=origin(_text(raw.get("website"), 300) or lead.get("website")),
        address=_text(raw.get("address"), 300) or _text(lead.get("address"), 300) or None,
        geo=_geo(raw.get("geo"), problems),
        hours=_hours(raw.get("hours"), problems),
        response_time_hours=_response_time(raw.get("response_time_hours"), problems),
        booking_url=http_url(raw.get("booking_url")),
        booking_provider=_text(raw.get("booking_provider"), 40) or None,
        form_action=http_url(raw.get("form_action")),
        reviews=reviews,
        case_studies=_case_studies(raw.get("case_studies"), problems),
        faqs=_faqs(raw.get("faqs"), problems),
        team_photo=_image("team_photo"),
        team_photo_alt=_text(raw.get("team_photo_alt"), 200) or None,
        og_image=_image("og_image"),
        colours=_colours(raw.get("colours"), problems),
        package=_package(raw.get("package"), problems),
        area=_text(raw.get("area"), 200) or None,
        domain=_domain(raw.get("domain"), problems),
        problems=tuple(problems),
    )
