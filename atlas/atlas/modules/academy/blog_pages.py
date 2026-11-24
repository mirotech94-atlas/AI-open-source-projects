"""The ATLAS Academy blog: agency.example.com/blog/ (the owner's site pack, 2026-10-18).

`files(cat, videos, now)` returns {path: bytes} for `site.render`:

* blog/index.html: every live post, newest first, with a search box and a
  topic filter (assets/blog.js; without it the whole list reads as a plain
  page), "Latest from the channel" and the visitor's lesson progress;
* blog/<slug>/index.html for each live post;
* blog/feed.xml (RSS 2.0) and assets/data/posts.json (the post index).

The posts are the owner's own words, committed as Markdown in academy/blog/ with
front matter (title, slug, date, summary, tags, and optionally video, kit,
kit_url, draft). They are rendered as written (mdlite.py); a post that
conflicts with one of the store's rules is not rewritten to pass: it carries
`draft: true` and is left out of every file the build writes, until the owner
changes it. A post that names the guarantee is left out too while any
product's refund terms don't say it (site._store_guarantee).

What the blog takes from the rest of the store, so the two never disagree:

* the page chrome, nav, footer, fonts and colours (site._page, style.css);
  assets/blog.css only lays out the blog's own parts;
* "Latest from the channel" is the /videos/ list (site.published: ATLAS's
  own tutorial posts, public and not withdrawn), newest upload first, each
  linking its lesson page, with the thumbnail the store serves itself;
* a post's `video:` gets the /videos/ player (an `a.yt` that app.js swaps
  for the youtube-nocookie player on click) only when that id is one of the
  published tutorials; otherwise no player at all;
* the "Pairs with" line names a catalog product with the catalog's title,
  summary and URL, only while it is on sale (`buy_live`): the post's own
  `kit_url` (no line when it names no catalog product), else the lesson's
  LESSON_PRODUCTS title when the post carries a published video, else the
  first kit_map.json rule whose keyword is in the post's title, summary or
  tags. A rule whose product the catalog doesn't have is dropped.

No figure is estimated: there is no reading time. assets/blog.js makes no
request; it keeps one thing, the lessons a visitor ticks as finished, in that
browser's localStorage (every access in try/catch; nothing is shown when
storage is unavailable). The store is dark only, so there is no theme switch.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import mdlite, site
from . import tutorial_series as ts
from .catalog import Catalog, Product
from .site import _e

BLOG_DIR = Path(__file__).with_name("blog")
KIT_MAP = BLOG_DIR / "kit_map.json"
BLOG_SLUG = "blog"
FEED_PATH = f"{BLOG_SLUG}/feed.xml"
POSTS_JSON = "assets/data/posts.json"
FEED_TITLE = "ATLAS Academy blog"
LATEST_MAX = 6
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED = ("title", "slug", "date", "summary")


# ------------------------------------------------------------------ posts

@dataclass(frozen=True)
class Post:
    slug: str
    title: str
    day: date
    summary: str
    body: str                                    # Markdown, as the owner wrote it
    tags: tuple[str, ...] = ()
    video: str = ""
    kit: str = ""
    kit_url: str = ""
    draft: bool = False
    source: str = ""

    @property
    def path(self) -> str:
        return f"{BLOG_SLUG}/{self.slug}/"


def front_matter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """`---` front matter of `key: value` lines (`[a, b]` is a list), and
    the Markdown after it."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError("missing front matter")
    meta: dict[str, str | list[str]] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [x.strip() for x in value[1:-1].split(",") if x.strip()]
        else:
            meta[key] = value
    return meta, m.group(2)


def _text(meta: dict[str, str | list[str]], key: str) -> str:
    value = meta.get(key, "")
    return value if isinstance(value, str) else ""


def parse(text: str, source: str = "") -> Post:
    meta, body = front_matter(text)
    for key in REQUIRED:
        if not _text(meta, key):
            raise ValueError(f"{source}: missing {key}")
    slug = _text(meta, "slug")
    if not SLUG_RE.match(slug):
        raise ValueError(f"{source}: slug must be lowercase letters, digits, hyphens")
    video = _text(meta, "video")
    if video and not site.VIDEO_ID_RE.fullmatch(video):
        raise ValueError(f"{source}: video is not a YouTube id")
    tags = meta.get("tags", [])
    return Post(slug=slug, title=_text(meta, "title"),
                day=date.fromisoformat(_text(meta, "date")),
                summary=_text(meta, "summary"), body=body,
                tags=tuple(tags) if isinstance(tags, list) else (str(tags),),
                video=video, kit=_text(meta, "kit"), kit_url=_text(meta, "kit_url"),
                draft=_text(meta, "draft").lower() == "true", source=source)


def load(directory: Path = BLOG_DIR) -> list[Post]:
    """Every post in `directory`, drafts included, newest first."""
    posts = [parse(f.read_text(encoding="utf-8"), f.name)
             for f in sorted(directory.glob("*.md"))]
    slugs = [p.slug for p in posts]
    if len(slugs) != len(set(slugs)):
        raise ValueError("duplicate blog slug")
    return sorted(posts, key=lambda p: (p.day, p.slug), reverse=True)


def live(cat: Catalog, posts: Iterable[Post] | None = None) -> list[Post]:
    """The posts the build publishes: not drafts, and none that names the
    guarantee unless every product's refund terms say it."""
    out = []
    for p in load() if posts is None else posts:
        if p.draft:
            continue
        words = f"{p.title} {p.summary} {p.body}".lower()
        if "guarantee" in words and not site._store_guarantee(cat):
            continue
        out.append(p)
    return out


# ------------------------------------------------------------------ kits

@dataclass(frozen=True)
class KitRule:
    keywords: tuple[str, ...]
    product: str


@dataclass(frozen=True)
class KitMap:
    rules: tuple[KitRule, ...]
    dropped: tuple[str, ...] = field(default=())   # slugs the catalog lacks


def kit_map(cat: Catalog, path: Path = KIT_MAP) -> KitMap:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules, dropped = [], []
    for r in raw.get("rules", []):
        slug = str(r.get("product") or "")
        if cat.product(slug) is None:
            dropped.append(slug)
            continue
        rules.append(KitRule(tuple(str(k).lower() for k in r.get("keywords", [])), slug))
    return KitMap(tuple(rules), tuple(dropped))


def _slug_of(url: str) -> str:
    return url.strip().strip("/").split("/")[-1] if url.strip().strip("/") else ""


def _matches(keyword: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(keyword)}(?![\w-])", text) is not None


def pairing(cat: Catalog, post: Post, shown: dict[str, site.Video] | None = None,
            kits: KitMap | None = None) -> Product | None:
    """The catalog product a post's "Pairs with" line names, or None. Only
    a product with an offer on sale."""
    p: Product | None
    if post.kit or post.kit_url:
        p = cat.product(_slug_of(post.kit_url))
    elif shown and post.video in shown:
        v = shown[post.video]
        pair = site.lesson_pairing(cat, v.series, v.episode)
        p = pair[0] if pair else None
    else:
        kits = kits or kit_map(cat)
        text = " ".join((post.title, post.summary, " ".join(post.tags))).lower()
        slug = next((r.product for r in kits.rules
                     if any(_matches(k, text) for k in r.keywords)), "")
        p = cat.product(slug) if slug else None
    if p is None or not any(o.buy_live for o in p.offers):
        return None
    return p


# ------------------------------------------------------------------ pages

def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _tag_label(tag: str) -> str:
    return tag.replace("-", " ")


def _kit_line(p: Product, root: str) -> str:
    guarantee = (f'<p class="muted">{_e(site.GUARANTEE)}.</p>'
                 if site._guarantee(p) else "")
    return f"""<aside class="blog-kit" aria-label="Pairs with">
<p class="eyebrow">Pairs with</p>
<p class="blog-kit-t"><a href="{root}{p.slug}/">{_e(p.title)}</a></p>
<p>{_e(p.summary)}</p>
{guarantee}<p><a class="btn btn-ghost btn-sm" href="{root}{p.slug}/">See what's included</a></p>
</aside>"""


def _player(post: Post, v: site.Video, root: str) -> str:
    """The /videos/ player: the store's own poster, click-to-load."""
    vid, title = v.video_id, _e(post.title)
    return f"""<div class="vcard blog-player" id="v-{vid}">
<a class="yt" target="_blank" rel="noopener noreferrer" href="https://youtu.be/{vid}" data-embed="{_e(site._embed(vid))}" data-title="{title}" aria-label="Watch on YouTube: {title} (opens in a new tab)">
<img src="{root}{site._thumb_path(v)}" alt="" width="1280" height="720" decoding="async">
<span class="play" aria-hidden="true">{site.PLAY_SVG}</span>
</a>
<p class="vplay"><span>Opens on YouTube in a new tab.</span><a href="https://youtu.be/{vid}" rel="noopener">Watch on YouTube</a></p>
<p class="blog-done" hidden><label><input type="checkbox" data-done="{vid}"> I've finished this lesson</label></p>
</div>"""


def _head(root: str) -> str:
    return (f'<link rel="stylesheet" href="{root}assets/blog.css">\n'
            f'<link rel="alternate" type="application/rss+xml" title="{FEED_TITLE}" '
            f'href="{root}{FEED_PATH}">\n'
            f'<script src="{root}assets/blog.js" defer></script>')


def post_page(cat: Catalog, post: Post, shown: dict[str, site.Video],
              kits: KitMap | None = None) -> str:
    root = "../../"
    v = shown.get(post.video) if post.video else None
    player = _player(post, v, root) if v is not None else ""
    kit = pairing(cat, post, shown, kits)
    tags = "".join(f'<span class="tag">{_e(_tag_label(t))}</span>' for t in post.tags)
    author = _e(cat.store["author"])
    email = _e(cat.contact_email)
    body = f"""<section class="vhero blog-hero"><div class="wrap blog-narrow">
<div class="crumbs"><a href="{root}academy/">Home</a><span>/</span><a href="{root}{BLOG_SLUG}/">Blog</a></div>
<p class="blog-tags">{tags}</p>
<h1>{_e(post.title)}</h1>
<p class="lede">{_e(post.summary)}</p>
<p class="blog-meta"><time datetime="{post.day.isoformat()}">{_day(post.day)}</time><span>By {author}</span></p>
</div></section>
<section class="blog-article"><div class="wrap blog-narrow">
{player}
<article class="post-body">
{mdlite.to_html(post.body, root)}
</article>
{_kit_line(kit, root) if kit is not None else ""}
<div class="blog-author"><p><b>{author}</b> runs ATLAS Academy. <a href="{root}academy/#about">About the owner</a> &#183; <a href="mailto:{email}">{email}</a></p>
<p><a href="{root}{BLOG_SLUG}/">All posts</a> &#183; <a href="{root}{FEED_PATH}">RSS feed</a></p></div>
</div></section>"""
    html = site._page(cat, title=post.title,
                      description=site.meta_description(post.summary),
                      body=body, depth=2, current="blog", head=_head(root))
    return site._with_ld(html, post_ld(cat, post, v))


def _latest(videos: list[tuple[ts.Series, ts.Episode, site.Video]], root: str) -> str:
    newest = sorted(videos, key=lambda x: x[2].uploaded, reverse=True)[:LATEST_MAX]
    if not newest:
        return ('<p class="muted">No lesson is published here yet. Subscribe on YouTube '
                "to see the first one when it goes up.</p>")
    items = []
    for s, ep, v in newest:
        day = v.uploaded.astimezone(UTC)
        items.append(f"""<li class="blog-vid" data-video="{v.video_id}">
<a href="{root}{site.lesson_slug(s.key, ep.number)}/"><img src="{root}{site._thumb_path(v)}" alt="" width="1280" height="720" loading="lazy" decoding="async"><span class="blog-vid-t">{_e(ep.title)}</span></a>
<p class="blog-vid-m"><span class="tag">{_e(s.mark)}</span><time datetime="{day.date().isoformat()}">{_day(day.date())}</time></p>
<p class="blog-done" hidden><label><input type="checkbox" data-done="{v.video_id}"> Finished</label></p>
</li>""")
    return f'<ul class="blog-vids">{"".join(items)}</ul>'


def index_page(cat: Catalog, posts: list[Post],
               videos: list[tuple[ts.Series, ts.Episode, site.Video]]) -> str:
    root = "../"
    topics = sorted({t for p in posts for t in p.tags})
    chips = ['<button class="chip" type="button" data-topic="" aria-pressed="true">All</button>']
    chips += [f'<button class="chip" type="button" data-topic="{_e(t)}" aria-pressed="false">'
              f"{_e(_tag_label(t))}</button>" for t in topics]
    rows = []
    for p in posts:
        words = " ".join((p.title, p.summary, " ".join(p.tags), mdlite.plain(p.body)))
        tags = "".join(f'<span class="tag">{_e(_tag_label(t))}</span>' for t in p.tags)
        rows.append(f"""<li class="blog-post" data-tags="{_e(" ".join(p.tags))}" data-q="{_e(words.lower())}">
<p class="blog-meta"><time datetime="{p.day.isoformat()}">{_day(p.day)}</time>{tags}</p>
<h2><a href="{p.slug}/">{_e(p.title)}</a></h2>
<p>{_e(p.summary)}</p>
</li>""")
    listing = (f'<ul class="blog-posts" data-blog-list>{"".join(rows)}</ul>' if rows else
               '<p class="muted">No post is published yet.</p>')
    body = f"""<section class="vhero blog-hero"><div class="wrap">
<p class="eyebrow">Blog</p>
<h1>Notes from the <em>workbench</em>.</h1>
<p class="lede">Plain-language notes from the owner on AI, automation and keeping both under a person's control. The free video lessons are on YouTube; the written notes are here.</p>
<p class="blog-feed"><a href="feed.xml">RSS feed</a></p>
</div></section>
<section class="blog-list"><div class="wrap">
<form class="blog-tools" data-blog-tools role="search" aria-label="Search the blog" hidden>
<label class="blog-q"><span>Search posts</span><input type="search" name="q" autocomplete="off" spellcheck="false" placeholder="For example: n8n, scams, agents"></label>
<div class="chips blog-chips" role="group" aria-label="Filter by topic">{"".join(chips)}</div>
</form>
<p class="blog-count muted" data-blog-count aria-live="polite" hidden></p>
{listing}
<p class="muted" data-blog-empty hidden>No post matches that yet. Try a shorter search, or <a href="../{site.VIDEOS_SLUG}/">the free lessons</a>.</p>
</div></section>
<section class="blog-channel" aria-labelledby="channel-h"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Free lessons</p><h2 id="channel-h">Latest from the channel</h2>
<p>The newest free lessons, each with its own lesson page. <a href="{site.CHANNEL_URL}?sub_confirmation=1" rel="noopener">Subscribe on YouTube</a></p></div></div>
{_latest(videos, root)}
<div class="blog-progress" data-progress hidden>
<p><span data-progress-count></span> Ticks are kept only in this browser, on this device.</p>
<button class="btn btn-ghost btn-sm" type="button" data-progress-clear>Clear my progress</button>
</div>
<p class="muted blog-note">The pictures here are served by this store. Nothing loads from YouTube until you open a lesson and press play.</p>
</div></section>"""
    return site._page(cat, title="Blog",
                      description=("Notes from the owner on practical AI and automation, and "
                                   "the latest free lessons from the ATLAS Academy channel."),
                      body=body, depth=1, current="blog", head=_head(root))


# ------------------------------------------------------------------ data

def post_ld(cat: Catalog, post: Post, v: site.Video | None = None) -> dict:
    base = cat.base_url
    data: dict = {
        "@context": "https://schema.org", "@type": "BlogPosting",
        "headline": post.title, "description": post.summary,
        "datePublished": post.day.isoformat(),
        "author": {"@type": "Person", "name": cat.store["author"]},
        "publisher": {"@type": "Organization", "name": cat.store["name"]},
        "mainEntityOfPage": f"{base}/{post.path}", "url": f"{base}/{post.path}",
        "keywords": list(post.tags),
    }
    if v is not None:
        data["video"] = {"@type": "VideoObject", "name": post.title,
                         "description": post.summary,
                         "embedUrl": f"{site.YT_EMBED}/{v.video_id}",
                         "contentUrl": f"https://youtu.be/{v.video_id}",
                         "thumbnailUrl": [f"{base}/{site._thumb_path(v)}"],
                         "uploadDate": v.uploaded.astimezone(UTC)
                         .isoformat(timespec="seconds").replace("+00:00", "Z")}
    return data


def feed_xml(cat: Catalog, posts: list[Post]) -> str:
    base = cat.base_url
    items = []
    for p in posts:
        url = xml_escape(f"{base}/{p.path}")
        when = format_datetime(datetime.combine(p.day, time(12), tzinfo=UTC))
        items.append(
            f"<item><title>{xml_escape(p.title)}</title><link>{url}</link>"
            f'<guid isPermaLink="true">{url}</guid><pubDate>{when}</pubDate>'
            f"<description>{xml_escape(p.summary)}</description></item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{FEED_TITLE}</title><link>{xml_escape(base)}/{BLOG_SLUG}/</link>"
            "<description>Notes from the owner on practical AI and automation.</description>"
            f"<language>en</language>{''.join(items)}</channel></rss>\n")


def posts_index(cat: Catalog, posts: list[Post], shown: dict[str, site.Video],
                kits: KitMap | None = None) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for p in posts:
        kit = pairing(cat, p, shown, kits)
        out.append({"title": p.title, "slug": p.slug, "url": f"/{p.path}",
                    "date": p.day.isoformat(), "summary": p.summary,
                    "tags": list(p.tags),
                    "video": p.video if p.video in shown else None,
                    "kit": kit.slug if kit is not None else None})
    return out


def page_urls(cat: Catalog, posts: Iterable[Post] | None = None) -> list[str]:
    base = cat.base_url
    return [f"{base}/{BLOG_SLUG}/"] + [f"{base}/{p.path}" for p in live(cat, posts)]


def files(cat: Catalog, videos: Iterable[site.Video] | None,
          posts: Iterable[Post] | None = None) -> dict[str, bytes]:
    shown_list = site.published(videos)
    shown = {v.video_id: v for _s, _ep, v in shown_list}
    published = live(cat, posts)
    kits = kit_map(cat)
    out: dict[str, bytes] = {
        f"{BLOG_SLUG}/index.html": index_page(cat, published, shown_list).encode(),
        FEED_PATH: feed_xml(cat, published).encode(),
        POSTS_JSON: (json.dumps(posts_index(cat, published, shown, kits), indent=2,
                                ensure_ascii=False) + "\n").encode(),
    }
    for p in published:
        out[f"{p.path}index.html"] = post_page(cat, p, shown, kits).encode()
    return out


__all__ = ["Post", "files", "kit_map", "live", "load", "page_urls", "pairing", "parse"]
