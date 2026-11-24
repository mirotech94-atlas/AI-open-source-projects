"""One page per public free lesson: agency.storefront.test/lessons/<series>-<n>/
(shop master plan F11 individual lesson pages, F12 learning paths with
honest availability, F15 small practice checks; 16 Oct 2026).

`files(cat, videos)` returns {path: bytes} for `site.render`. The lessons
are exactly the ones /videos/ lists (`site.published`: public on YouTube and
not withdrawn), so the page, the /videos/ card's link to it and the sitemap
entry can never disagree.

Every word is the script's own (tutorial_series.py), the store catalog's, or
the published render's manifest's, carried on the `Video` by
publish._with_manifests from a Fish-narrated render made no later than the
video went public (a newer render cannot be the published one):

* the outcome is `ep.summary`, the requirements `ep.needs` (the channel
  welcome is no lesson: no level, no outcome heading);
* the length and the chapters come from the manifest; without a trusted one
  (a build or test environment, a render gone or redone) both are left out,
  never estimated;
* "Transcript" is what the published video says: the manifest's narrated
  lines through the captions' `shown` -- no delivery cue reaches the page.
  Without them the section is the "Lesson script", the script's lines in
  the render's order, labelled as the script; a lesson's opener (it says the
  measured minutes) is then included only when the manifest carries it;
* the practice check is the episode's lab scene (Do, Expect, Stuck?,
  Challenge) and its free files from /free/; without a lab, a "Stuck?" line
  where the script has one; else no section;
* the matching resource is LESSON_PRODUCTS' (via site.lesson_pairing), shown
  as the store's product card while it is on sale; nothing while it is not;
  "no paid recommendation" and the finder where the table maps no title;
* the next lesson is linked only when it is public; otherwise it is named
  ("Coming next: ...") with no link, so no page links a page that is not
  there.

The video is click-to-load exactly as on /videos/: a poster served by the
store inside a `.vcard`, an `a.yt` whose `data-embed` app.js swaps for the
youtube-nocookie player on click, and a plain "Watch on YouTube" link.
No new script, style, colour or storage.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC

from . import site, tutorials
from . import tutorial_series as ts
from .catalog import Catalog
from .site import _e

EXPECT_PREFIX = "Your screen should show: "
STUCK_PREFIX = "Stuck?"
ROOT = "../../"


SAID_NOTE = "What the video says, line by line."
SCRIPT_NOTE = ("The lesson's script, line by line. The published video's own lines "
               "are not on record here, so the video may differ in places.")


def said_lines(v: site.Video) -> list[str]:
    """What the published video says: its manifest's narrated lines in order,
    as read (tutorials.shown: no cue). Empty without a trusted manifest."""
    return [x for x in (tutorials.shown(said) for _a, _b, said in v.timings) if x]


def transcript(ep: ts.Episode, opener: str) -> list[str]:
    """The script's narrated lines, in the order the render speaks them, as
    read (tutorials.shown: no cue, "dot" written as "."): the fallback when
    the published video's own lines (said_lines) are not on record. `opener`
    is the published render's voiced opener, "" when unknown: a lesson's
    opener states its measured minutes and is then left out; the channel
    welcome's names no number and is always the script's."""
    lines: list[str] = []
    if opener:
        lines.append(tutorials.shown(opener))
    elif not ep.opener_topic:
        lines.append(tutorials.shown(ts.INTRO_SAID))
    if ts.topic_line(ep):
        lines.append(tutorials.shown(ts.topic_line(ep)))
    for b in tutorials.beats(tutorials.opening(ep) + ep.scenes):
        if b.said:
            lines.append(tutorials.shown(b.said))
    return [x for x in lines if x]


def _lab(ep: ts.Episode) -> ts.Scene | None:
    return next((sc for sc in ep.scenes if sc.kind == "lab" and len(sc.items) == 4), None)


def _stuck(ep: ts.Episode) -> str:
    """A "Stuck?" line the script has outside a lab, or ""."""
    for sc in ep.scenes:
        for x in (*sc.items, *sc.said):
            if ts.plain(x).startswith(STUCK_PREFIX):
                return tutorials.shown(x)
    return ""


def _files_of(ep: ts.Episode) -> list[str]:
    """The episode's lab files that the store serves (never a dead link)."""
    return [n for n in ts.downloads_of(ep) if n in ts.LAB_FILES]


def _practice(ep: ts.Episode) -> str:
    lab = _lab(ep)
    files = _files_of(ep)
    rows = ""
    if lab is not None:
        do, expect, stuck, challenge = (ts.plain(x) for x in lab.items)
        values = (do, EXPECT_PREFIX + expect, stuck, challenge)
        rows = "".join(f"<div><dt>{_e(label)}</dt><dd>{_e(v)}</dd></div>"
                       for label, v in zip(ts.LAB_LABELS, values, strict=True))
    elif stuck_line := _stuck(ep):
        rows = f"<div><dt>{_e(ts.LAB_LABELS[2])}</dt><dd>{_e(stuck_line)}</dd></div>"
    if not rows and not files:
        return ""
    links = "".join(
        f'<li><a href="{ROOT}{ts.LAB_DIR}/{_e(n)}" download>{_e(n)}</a></li>' for n in files)
    files_html = (f"<p>Free lab file{'s' if len(files) > 1 else ''}, no sign-up:</p>"
                  f"<ul>{links}</ul>" if files else "")
    heading = _e(lab.heading) if lab is not None else ""
    lede = f"<p><b>{heading}</b></p>" if heading else ""
    facts = f'<dl class="facts">{rows}</dl>' if rows else ""
    return (f'<h2 id="practice">Practice check</h2>{lede}{files_html}{facts}')


def _resource(cat: Catalog, s: ts.Series, ep: ts.Episode) -> str:
    """The mapped title's card while it is on sale; "no paid recommendation"
    only where LESSON_PRODUCTS maps the lesson to no title; nothing for a
    mapped title with nothing on sale (the table's rule)."""
    pair = site.lesson_pairing(cat, s.key, ep.number)
    if pair is None:
        return ('<h2 id="resource">Go deeper</h2>'
                "<p>No paid recommendation for this lesson.</p>"
                f'<p><a href="{ROOT}academy/#finder">Find your starting point</a></p>')
    p, why = pair
    if not any(o.buy_live for o in p.offers):
        return ""
    return (f'<h2 id="resource">Go deeper</h2><p class="why-pair">{_e(why)}</p>'
            f'<div class="grid">{site._card_full(p, ROOT)}</div>')


def _next(s: ts.Series, ep: ts.Episode, public: set[tuple[str, int]]) -> str:
    """F12: the next episode by position in the series, linked only when it
    is public; otherwise named by its topic with no link; else nothing."""
    eps = list(s.episodes)
    i = next(k for k, e in enumerate(eps) if e.number == ep.number)
    back = (f'<p><a href="{ROOT}{site.VIDEOS_SLUG}/#series-{_e(s.key)}">'
            f"All lessons in {_e(series_name(s))}</a></p>")
    if i + 1 >= len(eps):
        return f'<h2 id="next">Next lesson</h2>{back}'
    nxt = eps[i + 1]
    if (s.key, nxt.number) in public:
        line = (f'<p><a href="{ROOT}{site.lesson_slug(s.key, nxt.number)}/">'
                f"{_e(nxt.title)}</a></p>")
    elif nxt.opener_topic:
        line = f'<p class="muted">Coming next: {_e(nxt.opener_topic)}</p>'
    else:
        line = ""
    return f'<h2 id="next">Next lesson</h2>{line}{back}'


def series_name(s: ts.Series) -> str:
    """The series as /videos/ heads it: its playlist, or "Start here"."""
    return s.playlist or "Start here"


def _chapters(v: site.Video) -> str:
    if not v.chapters:
        return ""
    rows = "".join(
        f'<li><a href="https://youtu.be/{v.video_id}?t={int(t)}" rel="noopener">'
        f"{tutorials.stamp(t)}</a> {_e(name)}</li>" for t, name in v.chapters)
    return f'<h2 id="chapters">Chapters</h2><ul>{rows}</ul>'


def _player(s: ts.Series, ep: ts.Episode, v: site.Video) -> str:
    vid, title = v.video_id, _e(ep.title)
    day = v.uploaded.astimezone(UTC)
    length = site._duration(v.seconds)
    length_html = (f'<span class="vlen"><span class="sr">Length </span>{length}</span>'
                   if length else "")
    # The channel welcome is no lesson: no level and no outcome heading.
    lesson = bool(ep.opener_topic)
    needs = "; ".join(ep.needs)
    facts = (f'<div><dt>Level</dt><dd><span class="level">{_e(ep.level_label)}</span>'
             "</dd></div>" if lesson else "")
    if needs:
        facts += f"<div><dt>You need</dt><dd>{_e(needs)}</dd></div>"
    tag = f'<span class="tag tag-accent">{_e(ep.level_label)}</span>' if lesson else ""
    outcome = "<h3>What you'll be able to do</h3>" if lesson else ""
    facts_html = f'<dl class="facts">{facts}</dl>' if facts else ""
    return f"""<article class="vcard" id="v-{vid}">
<a class="yt" target="_blank" rel="noopener noreferrer" href="https://youtu.be/{vid}" data-embed="{_e(site._embed(vid))}" data-title="{title}" aria-label="Watch on YouTube: {title} (opens in a new tab)">
<img src="{ROOT}{site._thumb_path(v)}" alt="" width="1280" height="720" decoding="async">
<span class="play" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg></span>
</a>
<p class="vplay"><span>Opens on YouTube in a new tab.</span><a href="https://youtu.be/{vid}" rel="noopener">Watch on YouTube</a></p>
<div class="body">
<p class="vtopic">{_e(site._topic(s, ep))}</p>
<p class="vmeta">{tag}{length_html}<time datetime="{day.date().isoformat()}">{day.day} {day:%b %Y}</time></p>
{outcome}
<p>{_e(ep.summary)}</p>
{facts_html}
<p class="vlinks"><a href="#transcript">Read what it says</a><a href="{site.CHANNEL_URL}?sub_confirmation=1" rel="noopener">Subscribe on YouTube</a></p>
</div>
</article>"""


def page(cat: Catalog, s: ts.Series, ep: ts.Episode, v: site.Video,
         public: set[tuple[str, int]]) -> str:
    said = said_lines(v)
    heading, note = ("Transcript", SAID_NOTE) if said else ("Lesson script", SCRIPT_NOTE)
    lines = "".join(f"<p>{_e(x)}</p>" for x in (said or transcript(ep, v.opener)))
    eyebrow = _e(s.mark) + (f" &#183; {_e(ep.level_label)}" if ep.opener_topic else "")
    body = f"""<section class="vhero"><div class="wrap">
<div class="crumbs"><a href="{ROOT}academy/">Home</a><span>/</span><a href="{ROOT}{site.VIDEOS_SLUG}/">Free lessons</a><span>/</span><a href="{ROOT}{site.VIDEOS_SLUG}/#series-{_e(s.key)}">{_e(series_name(s))}</a></div>
<p class="eyebrow">{eyebrow}</p>
<h1>{_e(ep.title)}</h1>
</div></section>
<section class="vseries"><div class="wrap"><div class="vlist">
{_player(s, ep, v)}
</div></div></section>
<section class="page"><div class="wrap"><div class="prose">
{_chapters(v)}
{_practice(ep)}
{_resource(cat, s, ep)}
{_next(s, ep, public)}
<h2 id="transcript">{heading}</h2>
<p class="muted">{_e(note)}</p>
{lines}
</div></div></section>"""
    html = site._page(cat, title=ep.title, description=site.meta_description(ep.summary),
                      body=body, depth=2, current="lessons")
    url = f"{cat.base_url}/{site.lesson_slug(s.key, ep.number)}/"
    # The poster the store serves itself, not YouTube's image host.
    ld = site.video_object(cat, ep, v) | {
        "url": url, "thumbnailUrl": [f"{cat.base_url}/{site._thumb_path(v)}"]}
    head = (f'<link rel="canonical" href="{_e(url)}">\n'
            f'{site._ld({"@context": "https://schema.org", **ld})}\n</head>')
    return html.replace("</head>", head, 1)


def files(cat: Catalog, videos: Iterable[site.Video] | None) -> dict[str, bytes]:
    shown = site.published(videos)
    public = {(s.key, ep.number) for s, ep, _v in shown}
    return {f"{site.lesson_slug(s.key, ep.number)}/index.html":
            page(cat, s, ep, v, public).encode() for s, ep, v in shown}


__all__ = ["files", "page", "transcript"]
