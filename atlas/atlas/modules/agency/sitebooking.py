"""agency.sitebooking -- the booking request on every designed client site.

**Why (2026-09-30).** the owner, from the phone review of the new templates:
"design it better with buttons, reservations if it's a restaurant or
booking system that will be good for them". Every family now leads with one
booking action, in the hero and in the sticky bar, and the page carries the
form it opens:

* trades: "Request a visit" (or "Get a quote" for the trades that quote
  before they start): the job, where, when, name, phone;
* beauty: "Book an appointment": the treatment, a day and a time;
* food and retail, by what the business is (`kind_for`): "Reserve a table"
  for restaurants, cafes and bars, "Place an order" for bakeries,
  "Order flowers" for florists, "Request a quote" for caterers, "Book a
  fitting" for tailors, and "Send a request" for anything else in the
  family (a dry cleaner takes no reservation).

**Where a request goes (`path_for`), in this order, and nowhere else:**

1. `LINK`: `client.json` names a `booking_url` (OpenTable, Resy, Booksy,
   Square, Calendly...). Every booking button opens it; the page has no
   booking form of its own.
2. `FORM`: a form service is configured (`client.json` `form_action`, or
   `AGENCY_FORM_ACTION`, the same endpoint the enquiry form posts to). The
   form posts there natively (no fetch: the service's JSON API shape is not
   something this code may guess) and the service sends the visitor to
   thank-you.html. The wording is a request -- "We'll call to confirm" --
   never a booking, and nothing on the page claims a free table or slot.
3. `PREVIEW`: an unsold preview with neither. The form is fully
   interactive and sends nothing, provably: no `action`, `method="dialog"`
   (a no-op outside a dialog), and no `name` on any control a visitor types
   into, so even a browser with scripts off has nothing to serialise. On
   submit it says "Bookings start when <Business> launches this site. For
   now, call <phone>." Launch item 4 checks exactly that shape.

A sold site with neither has no booking form (`path_for` is None) and its
buttons call, as before.

**Design (GetLayers, at design time; see templates/getlayers.json).** One
interactive moment per family, each an answer to the visitor's own input,
none of it ambient: the trades form is a work order whose docket fills in
as you choose and is stamped when you send; the beauty form opens under an
arched mirror that reflects the treatment and time you pick; the
restaurant form sets a place at a round table for each guest you add. The
script (`templates/booking.js`) is inlined, has no dependency, and every
motion it starts is skipped under `prefers-reduced-motion`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from urllib.parse import urlparse

from .client import ClientData
from .sitetemplate import TEMPLATES_DIR, button, is_preview, tel, wordmark

LINK, FORM, PREVIEW = "link", "form", "preview"
THANKS = "thank-you.html"
BOOK_ID = "book"
SHEET_ID = "book-sheet"

# host fragment -> the name a visitor knows the booking service by
_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("opentable.", "OpenTable"), ("resy.com", "Resy"), ("exploretock.com", "Tock"),
    ("sevenrooms.com", "SevenRooms"), ("toasttab.com", "Toast"),
    ("booksy.com", "Booksy"), ("vagaro.com", "Vagaro"), ("fresha.com", "Fresha"),
    ("styleseat.com", "StyleSeat"), ("glossgenius.com", "GlossGenius"),
    ("mindbodyonline.com", "Mindbody"), ("squareup.com", "Square"),
    ("square.site", "Square"), ("calendly.com", "Calendly"),
    ("housecallpro.com", "Housecall Pro"), ("getjobber.com", "Jobber"),
)


@dataclass(frozen=True, slots=True)
class Kind:
    """One way of asking for a booking: what the button says and which
    controls the form carries, in order."""
    key: str
    cta: str
    lead: str
    subject: str
    fields: tuple[str, ...]
    day_label: str = "Day"
    time_label: str = "Time"
    notes_label: str = "Anything we should know?"
    wish_label: str = "What would you like?"


_NAME_PHONE = ("name", "phone")
VISIT = Kind("visit", "Request a visit",
             "Tell us what needs doing and where. We'll call you to arrange a time.",
             "Visit request from your website",
             ("job", "where", "day_opt", "part", *_NAME_PHONE, "notes"),
             day_label="Preferred day", notes_label="What's going on?")
QUOTE = Kind("quote", "Get a quote",
             "Tell us about the job and where it is. We'll call you to talk it through.",
             "Quote request from your website",
             ("job", "where", "day_opt", "part", *_NAME_PHONE, "notes"),
             day_label="Preferred day", notes_label="Tell us about the job")
APPOINTMENT = Kind("appointment", "Book an appointment",
                   "Pick a treatment and a time that suits you. We'll call to confirm.",
                   "Appointment request from your website",
                   ("service", "day", "time", *_NAME_PHONE, "notes"))
RESERVE = Kind("reserve", "Reserve a table",
               "Tell us when you'd like to come in and how many of you there are. "
               "We'll call to confirm.",
               "Table request from your website",
               ("day", "time", "party", *_NAME_PHONE, "notes"))
ORDER = Kind("order", "Place an order",
             "Tell us what you'd like and when you need it. We'll call to confirm.",
             "Order request from your website",
             ("wish", "day", "time", *_NAME_PHONE),
             day_label="Day you need it")
FLOWERS = Kind("order", "Order flowers",
               "Tell us what you have in mind and when you need it. "
               "We'll call to confirm.",
               "Flower order from your website",
               ("wish", "day", "time", *_NAME_PHONE),
               day_label="Day you need them", wish_label="What do you have in mind?")
EVENT = Kind("quote", "Request a quote",
             "Tell us about the event. We'll call you to talk it through.",
             "Catering request from your website",
             ("day", "guests", "wish", *_NAME_PHONE),
             day_label="Event date", wish_label="Tell us about the event")
FITTING = Kind("fitting", "Book a fitting",
               "Pick a day and a time that suits you. We'll call to confirm.",
               "Fitting request from your website",
               ("day", "time", *_NAME_PHONE, "notes"),
               notes_label="What needs altering?")
REQUEST = Kind("request", "Send a request",
               "Tell us what you need and we'll call you back.",
               "Request from your website",
               ("wish", "day_opt", *_NAME_PHONE),
               day_label="Preferred day", wish_label="What can we help with?")

_QUOTE_TRADES = re.compile(r"paint|mov|roof|landscap|contractor|construction|floor|"
                           r"fence|pav|tile|mason|gutter|carpent", re.IGNORECASE)
_TABLE = re.compile(r"restaurant|cafe|café|coffee|pizz|bistro|diner|eatery|kitchen|"
                    r"\bbar\b|\bpub\b|deli\b|grill|brasserie|tavern|trattoria|"
                    r"taqueria|sushi|ramen|noodle|steak", re.IGNORECASE)
_BAKE = re.compile(r"bak|cake|patisserie|chocolat|dessert", re.IGNORECASE)


def kind_for(client: ClientData, family: str) -> Kind:
    """What the booking is called for this business. Beauty always books
    an appointment; a trade asks for a visit or a quote; food and retail
    by what they sell (see the module docstring)."""
    text = (client.category or "").replace("_", " ")
    if family == "beauty":
        return APPOINTMENT
    if family == "food":
        if re.search(r"cater", text, re.IGNORECASE):
            return EVENT
        if re.search(r"florist|flower", text, re.IGNORECASE):
            return FLOWERS
        if _BAKE.search(text):
            return ORDER
        if re.search(r"tailor|alteration", text, re.IGNORECASE):
            return FITTING
        if _TABLE.search(text):
            return RESERVE
        return REQUEST
    return QUOTE if _QUOTE_TRADES.search(text) else VISIT


def path_for(client: ClientData, form_action: str | None) -> str | None:
    """Where a booking request goes: LINK, FORM, PREVIEW, or None (a sold
    site with neither a booking link nor a form service). Only designed
    template sites (`client.theme`) carry a booking at all."""
    if not client.theme:
        return None
    if client.booking_url:
        return LINK
    # An unsold preview never collects a visitor's details, whatever form
    # service is configured: nobody has bought the site to answer them.
    if is_preview(client):
        return PREVIEW
    if form_action:
        return FORM
    return None


def provider(client: ClientData) -> str | None:
    """The booking service's name: the client's own word, else read off
    the link's host, else None (the page then just says "online")."""
    if client.booking_provider:
        return client.booking_provider
    host = urlparse(client.booking_url or "").netloc.lower()
    return next((name for frag, name in _PROVIDERS if frag in host), None)


_FOOD_TYPES = ((r"cafe|café|coffee", "CafeOrCoffeeShop"), (r"\bbar\b|\bpub\b|tavern",
                                                          "BarOrPub"))


def reservations(client: ClientData, form_action: str | None
                 ) -> tuple[str, bool | str] | None:
    """(schema.org type, acceptsReservations) for the LocalBusiness JSON-LD,
    only for a business that takes table reservations AND has a real path
    for one: the booking link itself, or True for a form that reaches the
    business. A preview claims nothing: its form reaches nobody, or an
    agency inbox (`AGENCY_FORM_ACTION`), which is not the business's own."""
    family = client.theme or ""
    if kind_for(client, family) is not RESERVE or family != "food" or is_preview(client):
        return None
    where = path_for(client, form_action)
    if where not in (LINK, FORM):
        return None
    text = (client.category or "").replace("_", " ")
    kind = next((t for pattern, t in _FOOD_TYPES
                 if re.search(pattern, text, re.IGNORECASE)), "Restaurant")
    return kind, (client.booking_url if where == LINK and client.booking_url else True)


# ----------------------------------------------------------------- buttons

def cta(client: ClientData, kind: Kind, where: str, *, style: str = "",
        label: str | None = None, pick: str | None = None, extra: str = "",
        base: str = "") -> str:
    """The booking button. LINK opens the booking service in a new tab;
    FORM and PREVIEW link to the page's own #book section, which the script
    opens as a sheet (without a script the link still scrolls there)."""
    text = label or kind.cta
    cls = f"{style} book-cta".strip()
    if where == LINK and client.booking_url:
        return button(text, client.booking_url, kind=cls, attrs=(
            ' target="_blank" rel="noopener" data-atlas="book-cta"' + extra))
    attrs = ' data-atlas="book-cta" data-book-open'
    if pick:
        attrs += f' data-book-pick="{escape(pick)}"'
    if base:
        # a sub-page: the home page's section, no sheet
        return button(text, f"{base}#{BOOK_ID}", kind=cls,
                      attrs=' data-atlas="book-cta"' + extra)
    return button(text, f"#{BOOK_ID}", kind=cls, attrs=attrs + extra)


# ----------------------------------------------------------------- the form

def _named(where: str, name: str) -> str:
    """A `name` only when the form really sends (FORM). A preview's typed
    fields carry none, so nothing they hold can ever be submitted."""
    return f' name="{name}"' if where == FORM else ""


def _msg(fid: str) -> str:
    return f'<p class="mf-bfield__msg" id="{fid}-msg" aria-live="polite"></p>'


def _text_field(fid: str, label: str, control: str, *, need: str | None,
                hint: str = "", cls: str = "") -> str:
    need_attr = f' data-need="{escape(need)}"' if need else ""
    optional = "" if need else '<span class="mf-bfield__opt">optional</span>'
    hint_html = f'<p class="mf-bfield__hint">{escape(hint)}</p>' if hint else ""
    return (f'<div class="mf-bfield {cls}"{need_attr}>'
            f'<label for="{fid}">{escape(label)}{optional}</label>{hint_html}'
            f"{control}{_msg(fid)}</div>")


def _input(fid: str, where: str, name: str, *, type_: str = "text", extra: str = "",
           need: bool = True) -> str:
    req = " required" if need and where == FORM else ""
    return (f'<input class="mf-input" id="{fid}" type="{type_}"{_named(where, name)}'
            f'{req} aria-describedby="{fid}-msg"{extra}>')


def _chips(group: str, legend: str, options: list[str], *, need: str,
           cls: str = "") -> str:
    """Radio buttons drawn as chips. Radios keep their `name` in every mode
    -- it is what groups them -- and their values are the page's own words
    (the business's services, "Morning"), never anything a visitor typed."""
    items = "".join(
        f'<label class="mf-chip"><input type="radio" name="{group}" '
        f'value="{escape(o)}"><span>{escape(o)}</span></label>' for o in options)
    return (f'<fieldset class="mf-bfield mf-chips {cls}" data-need="{escape(need)}" '
            f'aria-describedby="b-{group}-msg"><legend>{escape(legend)}</legend>'
            f'<div class="mf-chips__row">{items}</div>{_msg("b-" + group)}</fieldset>')


def _date(fid: str, where: str, name: str, label: str, *, need: bool) -> str:
    """Native date input (the iOS wheel), styled; the page's script sets
    `min` to the visitor's today, since a date baked in at build time goes
    stale on a static page."""
    control = (f'<span class="mf-dt" data-empty="Choose a day">'
               f'{_input(fid, where, name, type_="date", need=need)}</span>')
    return _text_field(fid, label, control, need="Choose a day." if need else None)


def _time(fid: str, where: str, name: str, label: str) -> str:
    control = (f'<span class="mf-dt" data-empty="Choose a time">'
               f'{_input(fid, where, name, type_="time", extra=" step=\"900\"")}</span>')
    return _text_field(fid, label, control, need="Choose a time.")


def _seats() -> str:
    """Twelve places round a table, drawn once; the script lights one per
    guest, in document order -- which is laid facing pairs first (top and
    bottom, then the sides), so two guests sit opposite each other."""
    seats = []
    for i in (0, 6, 3, 9, 1, 7, 4, 10, 2, 8, 5, 11):
        angle = -math.pi / 2 + i * (2 * math.pi / 12)
        x, y = 60 + 47 * math.cos(angle), 60 + 47 * math.sin(angle)
        seats.append(f'<circle class="mf-seat" cx="{x:.1f}" cy="{y:.1f}" r="7.5"/>')
    return ('<svg class="mf-table" viewBox="0 0 120 120" aria-hidden="true" '
            'focusable="false"><circle class="mf-table__top" cx="60" cy="60" r="31"/>'
            '<circle class="mf-table__rim" cx="60" cy="60" r="24"/>'
            + "".join(seats) + "</svg>")


def _stepper(fid: str, where: str, name: str, legend: str, *, value: int, low: int,
             high: int, noun: tuple[str, str], table: bool) -> str:
    """A number with − and + buttons that disable at their limits; the
    restaurant's version sets the table beside it."""
    minus = (f'<button class="mf-step" type="button" data-step="-1" data-for="{fid}" '
             f'aria-label="One fewer {noun[0]}"><svg viewBox="0 0 24 24" aria-hidden="true" '
             'focusable="false"><path d="M6 12h12"/></svg></button>')
    plus = (f'<button class="mf-step" type="button" data-step="1" data-for="{fid}" '
            f'aria-label="One more {noun[0]}"><svg viewBox="0 0 24 24" aria-hidden="true" '
            'focusable="false"><path d="M6 12h12M12 6v12"/></svg></button>')
    control = (f'<input class="mf-step__value" id="{fid}" type="number" inputmode="numeric" '
               f'min="{low}" max="{high}" value="{value}"{_named(where, name)}'
               f' aria-describedby="{fid}-msg">')
    say = (f'<p class="mf-party__say" data-echo="party" aria-hidden="true">'
           f"Table for {value}</p>") if table else ""
    return (f'<fieldset class="mf-bfield mf-party" data-need="Add how many {noun[1]}.">'
            f"<legend>{escape(legend)}</legend>"
            f'<div class="mf-party__row">{_seats() if table else ""}'
            f'<div class="mf-party__ctl"><div class="mf-stepper">{minus}'
            f'<label class="mf-hp" for="{fid}">{escape(legend)}</label>{control}{plus}'
            f"</div>{say}</div></div>{_msg(fid)}</fieldset>")


def _field(key: str, kind: Kind, where: str, services: list[str], groomer: bool) -> str:
    if key == "job":
        return _chips("job", "What's the job?", [*services, "Something else"],
                      need="Choose the kind of job.")
    if key == "service":
        return _chips("service", "Which treatment?", [*services, "Not sure yet"],
                      need="Choose a treatment, or Not sure yet.")
    if key == "part":
        return _chips("part", "Best time of day", ["Morning", "Afternoon", "Flexible"],
                      need="Choose a time of day.", cls="mf-chips--seg")
    if key == "where":
        return _text_field("b-where", "Address or ZIP code", _input(
            "b-where", where, "address", extra=' autocomplete="street-address"'),
            need="Add the address or ZIP code of the job.")
    if key in ("day", "day_opt"):
        return _date("b-day", where, "date", kind.day_label, need=key == "day")
    if key == "time":
        return _time("b-time", where, "time", kind.time_label)
    if key == "party":
        return _stepper("b-party", where, "party_size", "How many of you?", value=2,
                        low=1, high=40, noun=("person", "people"), table=True)
    if key == "guests":
        return _stepper("b-guests", where, "guests", "How many guests?", value=20,
                        low=1, high=999, noun=("guest", "guests"), table=False)
    if key == "name":
        return _text_field("b-name", "Your name", _input(
            "b-name", where, "name", extra=' autocomplete="name"'),
            need="Add your name.")
    if key == "phone":
        return _text_field("b-phone", "Phone number", _input(
            "b-phone", where, "phone", type_="tel",
            extra=' autocomplete="tel" inputmode="tel"'),
            need="Add a phone number we can call back.",
            hint="We'll call this number to confirm.")
    if key == "wish":
        control = (f'<textarea class="mf-input" id="b-wish" rows="3"'
                   f'{_named(where, "request")}{" required" if where == FORM else ""} '
                   'aria-describedby="b-wish-msg"></textarea>')
        return _text_field("b-wish", kind.wish_label, control,
                           need="Tell us a little about it.")
    label = "Your pet's name and breed" if groomer else kind.notes_label
    control = (f'<textarea class="mf-input" id="b-notes" rows="3"'
               f'{_named(where, "notes")} aria-describedby="b-notes-msg"></textarea>')
    return _text_field("b-notes", label, control, need=None)


def _decor(client: ClientData, family: str, kind: Kind) -> str:
    """Each family's one moment, echoing the visitor's own choices."""
    name = escape(wordmark(client.name))
    if family == "trades":
        rows = (("Job", "job", "Not chosen yet"), ("Where", "where", "Not given yet"),
                ("When", "when", "Whenever suits"))
        dl = "".join(f'<div><dt>{t}</dt><dd data-echo="{k}" data-empty="{e}">{e}</dd></div>'
                     for t, k, e in rows)
        return (f'<div class="mf-docket" aria-hidden="true"><div class="mf-docket__top">'
                f'<span class="mf-docket__name">{name}</span>'
                f'<span class="mf-docket__kind">Work order</span></div>'
                f'<dl class="mf-docket__rows">{dl}</dl>'
                '<span class="mf-stamp" data-stamp>Not sent</span></div>')
    if family == "beauty":
        initial = escape((re.sub(r"[^A-Za-z]", "", wordmark(client.name))[:1] or "·")
                         .upper())
        return (f'<div class="mf-glass" aria-hidden="true"><div class="mf-glass__arch">'
                f'<span class="mf-glass__initial">{initial}</span>'
                '<p class="mf-glass__say"><span data-echo="service" '
                'data-empty="Your next visit">Your next visit</span>'
                '<span data-echo="when" data-empty="Pick a day and time">'
                "Pick a day and time</span></p>"
                '<span class="mf-stamp" data-stamp>Not sent</span></div></div>')
    party = ('<span data-echo="party" data-empty="">Table for 2</span>'
             if kind is RESERVE else "")
    say = (f'<p class="mf-canopy__say">{party}<span data-echo="when" '
           'data-empty="Pick a day">Pick a day</span></p>')
    return (f'<div class="mf-canopy" aria-hidden="true"><div class="mf-awning"></div>'
            f'{say}<span class="mf-stamp" data-stamp>Not sent</span></div>')


def _fallback_line(client: ClientData) -> tuple[str, str | None]:
    """(what to do instead, as escaped markup; the button for it) in a
    preview."""
    if client.phone:
        return (f'For now, call <span class="mf-nowrap">{escape(client.phone)}</span>.',
                button(f"Call {client.phone}", f"tel:{tel(client.phone)}", kind="",
                       icon_name="phone", attrs=' data-atlas="cta"'))
    if client.email:
        return (f"For now, email {escape(client.email)}.",
                button(f"Email {client.email}", f"mailto:{client.email}",
                       icon_name="mail"))
    return "For now, use the contact details on this page.", None


def form(client: ClientData, family: str, kind: Kind, where: str,
         form_action: str | None, services: list[str]) -> str:
    """The booking form for FORM and PREVIEW (see the module docstring for
    what each sends)."""
    groomer = kind is APPOINTMENT and bool(re.search(r"groom", client.category or "",
                                                      re.IGNORECASE))
    fields = "".join(_field(k, kind, where, services, groomer) for k in kind.fields)
    if where == FORM and form_action:
        thanks = f"{client.website}/{THANKS}" if client.website else THANKS
        opening = (f'<form class="mf-bform mf-bform--{family}" data-atlas="booking" '
                   f'data-mode="send" data-kind="{kind.key}" '
                   f'action="{escape(form_action)}" method="post">'
                   f'<input type="hidden" name="_next" value="{escape(thanks)}">'
                   f'<input type="hidden" name="_subject" value="{escape(kind.subject)}">'
                   '<label class="mf-hp" for="b-hp">Leave this empty</label>'
                   '<input class="mf-hp" id="b-hp" type="text" name="_gotcha" '
                   'tabindex="-1" autocomplete="off">')
        note = "This sends a request, not a booking. We'll call you to confirm."
        done = ""
        stamp = "Sending"
    else:
        opening = (f'<form class="mf-bform mf-bform--{family}" data-atlas="booking" '
                   f'data-mode="preview" data-kind="{kind.key}" method="dialog">')
        note = "This is a preview: nothing you type here is sent."
        instead, alt = _fallback_line(client)
        done = (
            '<div class="mf-bdone" data-book-done hidden tabindex="-1">'
            '<p class="mf-bdone__title">Nothing was sent</p>'
            f"<p>Bookings start when {escape(client.name)} launches this site. "
            f"{instead}</p>"
            f'<div class="mf-actions">{alt or ""}'
            '<button class="mf-btn mf-btn--ghost" type="button" data-book-edit>'
            '<span class="mf-btn__label"><span data-label="Change the request">'
            "Change the request</span></span></button></div></div>")
        stamp = "Not sent"
    submit = (
        '<button class="mf-btn mf-btn--block mf-bsubmit" type="submit" '
        'data-atlas="book-submit" data-busy="Sending request" data-bad="Check the details">'
        '<span class="mf-spin" aria-hidden="true"></span>'
        '<span class="mf-btn__label"><span data-label="Send request">Send request</span>'
        "</span></button>")
    decor = _decor(client, family, kind).replace(
        '<span class="mf-stamp" data-stamp>Not sent</span>',
        f'<span class="mf-stamp" data-stamp>{stamp}</span>')
    return (f"{opening}{decor}"
            f'<div class="mf-bform__body" data-book-body>{fields}'
            '<div class="mf-bform__foot">'
            '<p class="mf-bform__alert" data-book-alert aria-live="assertive"></p>'
            f'{submit}<p class="mf-bform__note">{escape(note)}</p></div></div>'
            f"{done}</form>")


def section(client: ClientData, family: str, kind: Kind, where: str | None,
            form_action: str | None, services: list[str], title_id: str = "book-title",
            *, eyebrow: str = "", statement: str | None = None, talk: str = "") -> str:
    """The #book section, or "" when there is no booking path. Two
    columns (v4, the Lovable reference): on the left the numbered label
    (`eyebrow`, the booking's own name), the statement heading, the lead
    and `talk` (the phone, "Prefer to talk?"); on the right the form in
    its white card, or the one pill that opens the booking service."""
    if where is None:
        return ""
    head = (f'<div class="mf-book__intro">{eyebrow}<h2 id="{title_id}" class="mf-title">'
            f'{escape(statement or kind.cta)}</h2>')
    if where == LINK:
        name = provider(client)
        lead = (f"{kind.cta} online with {name}." if name else f"{kind.cta} online.")
        return ('<section class="mf-section mf-section--soft mf-book" id="book" '
                f'aria-labelledby="{title_id}" data-atlas="book-link"><div class="mf-split">'
                f'{head}<p class="mf-lead">{escape(lead)}</p>{talk}</div>'
                '<div class="mf-book__panel mf-book__panel--link"><div class="mf-actions">'
                + cta(client, kind, where, label=f"{kind.cta} online")
                + f'</div><p class="mf-note">Opens {escape(name or "the booking page")} '
                "in a new tab.</p></div></div></section>")
    marker = "book-form" if where == FORM else "book-preview"
    return ('<section class="mf-section mf-section--soft mf-book" id="book" '
            f'aria-labelledby="{title_id}" data-atlas="{marker}"><div class="mf-split">'
            f'{head}<p class="mf-lead">{escape(kind.lead)}</p>{talk}</div>'
            f'<div class="mf-book__panel" data-book-panel>'
            f"{form(client, family, kind, where, form_action, services)}"
            "</div></div></section>")


_CLOSE = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
          '<path d="M6 6l12 12M18 6 6 18"/></svg>')


def sheet(kind: Kind) -> str:
    """The dialog the booking form moves into when a booking button is
    tapped (bottom sheet on a phone, centred panel on a wide screen)."""
    return (f'<dialog class="mf-sheet" id="{SHEET_ID}" aria-labelledby="book-sheet-title">'
            '<div class="mf-sheet__bar"><span class="mf-sheet__grip" aria-hidden="true">'
            f'</span><p class="mf-sheet__title" id="book-sheet-title">{escape(kind.cta)}</p>'
            f'<button class="mf-x" type="button" data-book-close aria-label="Close">{_CLOSE}'
            '</button></div><div class="mf-sheet__body" data-sheet-body></div></dialog>')


@lru_cache(maxsize=1)
def _script_source() -> str:
    raw = (TEMPLATES_DIR / "booking.js").read_text(encoding="utf-8")
    raw = re.sub(r"(?m)^\s*//.*\n", "", raw)
    return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


def script() -> str:
    return f'<script data-atlas="booking">{_script_source()}</script>'


def extras(kind: Kind, where: str | None) -> str:
    """What goes at the end of <body>: the sheet and its script, only when
    the page has a form of its own."""
    if where not in (FORM, PREVIEW):
        return ""
    return sheet(kind) + script()

