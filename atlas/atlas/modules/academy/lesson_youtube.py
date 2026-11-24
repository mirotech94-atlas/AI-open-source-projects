"""Academy lesson videos on YouTube (the owner, 2026-11-14).

Free preview lessons go PUBLIC, with a "Watch on YouTube" link on the store
and a link back. Every other lesson goes UNLISTED and is embedded
(youtube-nocookie) only on the buyer's paid course page; it is never linked
publicly. The video id is stored in `academy_lesson_youtube`, keyed by the
catalog's file key, never in catalog.json (a repo file is not written at
runtime).

One action, `academy.upload_lesson`, Tier 2 (a card per lesson). Everything
that decides *what* may go up is code in this file, not a runtime argument:

  * the source is a gated NEW render in the staging folder, never a file under
    the store's files folder and never an old slide video;
  * the audience (public or unlisted) comes from `FREE_PREVIEWS`, a code
    constant, not from a parameter;
  * the channel is @example_handle (the `clips` key, YOUTUBE_CLIPS_*); the
    clips channel's own upload gate opens for a gated lesson render only;
  * a per-day cap is counted from the table, so a restart cannot reset it.

The rollback sets the video private and takes it out of its playlist.
Deletion is permanently Tier 3, so nothing here deletes.

Nothing here touches the network until `run` calls `youtube.api`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from ...kernel import idempotency
from ...kernel.contracts import ActionResult

ACTION = "academy.upload_lesson"
STAGING_ENV = "ACADEMY_LESSONS_STAGING"
PER_DAY_ENV = "ACADEMY_LESSONS_PER_DAY"
DEFAULT_STAGING = "data/academy-staging/video"

# Lessons whose YouTube copy is PUBLIC. the owner names them; empty until he does,
# so every lesson is unlisted until then. A code constant on purpose.
FREE_PREVIEWS: frozenset[str] = frozenset()

COURSES = ("operator", "adults", "60plus", "sell")
_NUM = re.compile(r"^\d+\.\d+$")
# Upload kinds (the owner 2026-11-14): the full lesson (UNLISTED, embedded on the paid
# page), a short PUBLIC preview of a lesson or of one ebook/PDF product, and a
# vertical Short. Each has its own staged name, all behind the same gate.
KINDS = ("full", "preview", "short")
SHORT_MAX_SECONDS = 60
_STEM = re.compile(
    r"^(?:(?P<pre>preview|short)-)?lesson-(?P<course>operator|adults|60plus|sell)-"
    r"(?P<num>\d+\.\d+)\.mp4$"
    r"|^preview-product-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.mp4$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Quota units per call (YouTube Data API v3). Insert is the figure
# tutorials.py already budgets (~1,600); the other four are YouTube's
# published costs and are marked "verify" in the design note.
COST_INSERT = 1600
COST_THUMBNAIL = 50
COST_CAPTIONS = 400
COST_PLAYLIST_ITEM = 50
COST_STATUS = 50
COST_PER_LESSON = (COST_INSERT + COST_THUMBNAIL + COST_CAPTIONS
                   + COST_PLAYLIST_ITEM + COST_STATUS)
DAILY_QUOTA = 10_000
SHARED_RESERVE = 1_700          # the tutorials lane posts one video a day
PER_DAY_MAX = 4
PER_DAY_DEFAULT = 3


def safe_per_day(quota: int = DAILY_QUOTA, reserve: int = SHARED_RESERVE,
                 cost: int = COST_PER_LESSON) -> int:
    """Whole lessons a day the quota pays for after the reserve. Pure."""
    return max(0, (quota - reserve) // cost)


def days_needed(lessons: int, per_day: int) -> int:
    if per_day <= 0:
        raise ValueError("per_day must be at least 1")
    return -(-lessons // per_day)


def per_day() -> int:
    try:
        n = int(os.getenv(PER_DAY_ENV, str(PER_DAY_DEFAULT)))
    except ValueError:
        n = PER_DAY_DEFAULT
    return max(1, min(n, PER_DAY_MAX))


def lesson_channel() -> str:
    """The channel the lessons go to: @example_handle, which ATLAS knows as
    the `clips` key (YOUTUBE_CLIPS_* credentials). Fixed in code; the only
    uploads it takes besides the clips lane are gated lesson renders."""
    from ..youtube import api
    return api.CLIPS_CHANNEL


# ------------------------------------------------------------------ names

def file_key(course: str, num: str) -> str | None:
    """The catalog file key of a lesson, or None when it is not one."""
    if course not in COURSES or not _NUM.match(num or ""):
        return None
    dashed = num.replace(".", "-")
    if course == "operator":
        return f"operator-video-{dashed}"
    if course == "sell":
        return f"sell-video-{dashed}"
    return f"aiea-{course}-video-{dashed}"


def stem(course: str, num: str, kind: str = "full") -> str:
    """The staged file's name without `.mp4`; `kind` full/preview/short."""
    head = "" if kind == "full" else f"{kind}-"
    return f"{head}lesson-{course}-{num}"


def product_stem(slug: str) -> str:
    return f"preview-product-{slug}"


def row_key(kind: str, base: str) -> str:
    """The table key of one upload. A full lesson keeps its bare catalog key
    (what every existing row and the paid page use); the others are prefixed so
    one lesson can carry a full video, a preview and a Short."""
    return base if kind == "full" else f"{kind}:{base}"


def kind_of_name(name: str) -> str | None:
    """full / preview / short from a staged file name, None when it is none."""
    m = _STEM.match(name or "")
    if m is None:
        return None
    return "preview" if m.group("slug") else (m.group("pre") or "full")


def valid_video_id(value: object) -> bool:
    return isinstance(value, str) and bool(_VIDEO_ID.match(value))


def embed_url(video_id: str) -> str:
    if not valid_video_id(video_id):
        raise ValueError("not a YouTube video id")
    return (f"https://www.youtube-nocookie.com/embed/{video_id}"
            "?rel=0&modestbranding=1&playsinline=1")


def watch_url(video_id: str) -> str:
    if not valid_video_id(video_id):
        raise ValueError("not a YouTube video id")
    return f"https://www.youtube.com/watch?v={video_id}"


def privacy_for(key: str, kind: str = "full") -> str:
    """The audience, decided in code. A preview and a Short are the public
    face; a full lesson is unlisted unless the owner named it a free preview."""
    if kind in ("preview", "short"):
        return "public"
    return "public" if key in FREE_PREVIEWS else "unlisted"


def vet(params: dict) -> str | None:
    """Closed arguments: a kind, then a course and lesson number, or (preview
    only) one product slug. Pure."""
    kind = str(params.get("kind") or "full")
    if kind not in KINDS:
        return "kind must be full, preview or short"
    product = str(params.get("product") or "")
    if product:
        if kind != "preview":
            return "only a preview can be of a product"
        if params.get("course") or params.get("num"):
            return "a product preview takes a product and no course or num"
        return None if _SLUG.match(product) else "product must be a catalog slug"
    course, num = str(params.get("course") or ""), str(params.get("num") or "")
    if file_key(course, num) is None:
        return "course must be one of operator, adults, 60plus, sell and num like 3.2"
    return None


# ------------------------------------------------------------------ source

def staging_dir() -> Path:
    return Path(os.getenv(STAGING_ENV, DEFAULT_STAGING))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _gate_time(raw: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def vertical(size: object) -> bool:
    """A "WxH" size that is 9:16 (the gate records `checks.streams.size`)."""
    m = re.match(r"^(\d+)x(\d+)$", str(size or ""))
    return bool(m) and int(m.group(1)) * 16 == int(m.group(2)) * 9


def staged_refusal(path: str | Path, root: Path | None = None,
                   kind: str | None = None) -> str | None:
    """Why this file may not be uploaded, or None. Accepts only a plain
    `lesson-<course>-<n.m>.mp4` directly inside the staging folder, not a
    symlink, with a `.gate.json` that passed and is not older than the file.
    Pure apart from reading the two files' metadata."""
    base = (root or staging_dir())
    p = Path(path)
    try:
        st = os.lstat(p)
    except OSError:
        return "the video file is not there"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return "the source must be a plain file, not a link"
    if p.suffix.lower() != ".mp4" or not _STEM.match(p.name):
        return "the source must be a staged lesson-<course>-<n.m>.mp4"
    if kind is not None and kind_of_name(p.name) != kind:
        return f"the staged file is not a {kind} render"
    try:
        inside = p.resolve().parent == base.resolve()
    except OSError:
        inside = False
    if not inside:
        return ("only a gated new render in the staging video folder goes up, "
                "never a stored or old video")
    gate = p.with_name(p.name + ".gate.json")
    try:
        data = json.loads(gate.read_text())
    except (OSError, ValueError):
        return "no gate record for this render; it has not been checked"
    if not isinstance(data, dict) or data.get("pass") is not True:
        return "the motion gate did not pass this render"
    read_at = _gate_time(data.get("read_at"))
    if read_at is None:
        return "the gate record has no readable time"
    if read_at.timestamp() < st.st_mtime:
        return "the video changed after the gate read it; run the gate again"
    # The approval the clips channel itself checks (api.academy_video_path):
    # written only after a passing gate, bound to the file's hash.
    if data.get("approved") is not True:
        return "the gate has not approved this render (no approval in its record)"
    if data.get("sha256") != sha256_of(p):
        return "the approval is for a different file; run the gate again"
    if kind_of_name(p.name) == "short":
        secs = gate_seconds(p)
        if secs is None or secs >= SHORT_MAX_SECONDS:
            return f"a Short must be under {SHORT_MAX_SECONDS} seconds"
        size = ((data.get("checks") or {}).get("streams") or {}).get("size")
        if not vertical(size):
            return "a Short must be vertical 9:16"
    return None


def write_approval(path: str | Path) -> str | None:
    """The staging step: after the motion gate passed, bind approval to the
    exact bytes. Merges `approved` and `sha256` into `<name>.mp4.gate.json`,
    atomically (temp file in the same folder, then rename). Refuses, writing
    nothing, unless the record says `pass: true`, was read after the file last
    changed, and the file is a plain staged lesson render. Returns why not, or
    None when written. Called by the gate's staging step, never by an action."""
    p = Path(path)
    if not _STEM.match(p.name) or p.is_symlink() or not p.is_file():
        return "not a plain staged lesson render"
    gate = p.with_name(p.name + ".gate.json")
    try:
        data = json.loads(gate.read_text())
    except (OSError, ValueError):
        return "no gate record"
    if not isinstance(data, dict) or data.get("pass") is not True:
        return "the gate did not pass"
    read_at = _gate_time(data.get("read_at"))
    if read_at is None or read_at.timestamp() < p.stat().st_mtime:
        return "the gate record is older than the video"
    data.update(approved=True, sha256=sha256_of(p))
    tmp = gate.with_name(gate.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, gate)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return f"could not write the approval: {exc}"
    return None


# ------------------------------------------------------------------ text

def gate_seconds(path: str | Path) -> float | None:
    """The render's length in seconds, as the motion gate measured it."""
    p = Path(path)
    try:
        data = json.loads(p.with_name(p.name + ".gate.json").read_text())
        n = float(data["checks"]["length"]["actual"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return n if n > 0 else None


def minutes_limit(seconds: float) -> int:
    """The N of "in less than N minutes": the next whole minute above the
    real length, so the promise is always true."""
    return int(seconds // 60) + 1


def build_title(lesson_title: str, seconds: float) -> str:
    """the owner 2026-11-14: "Learn X in less than N minutes!". At most 100
    characters; a long topic is shortened, never the promise."""
    n = minutes_limit(seconds)
    tail = f" in less than {n} minute{'s' if n != 1 else ''}!"
    topic = lesson_title.strip().rstrip(".!?:") or "this lesson"
    room = 100 - len("Learn ") - len(tail)
    if len(topic) > room:
        topic = topic[:room - 1].rstrip() + "\u2026"
    return f"Learn {topic}{tail}"


def build_description(course_title: str, num: str, lesson_title: str,
                      store_url: str, *, public: bool, kind: str = "full",
                      product: bool = False) -> str:
    """The description. A preview and a Short carry the store link (that is what
    they are for); `product` = a preview of an ebook/PDF product, no lesson."""
    if product:
        lines = [f"{course_title}: a free preview", ""]
    else:
        lines = [f"{course_title}, lesson {num}: {lesson_title}", ""]
    if kind == "preview":
        what = "the full guide" if product else "the full course"
        lines += [f"Free preview. Get {what}: {store_url}", ""]
    elif kind == "short":
        lines += [f"The full course: {store_url}", ""]
    elif public:
        lines += [f"Free preview. The full course: {store_url}", ""]
    else:
        lines += [(f"Part of {course_title}. Course members watch it on their "
                   f"course page. {store_url}"), ""]
    lines += ["The narration is a synthetic (AI) voice."]
    if kind == "short":
        lines += ["#Shorts"]
    return "\n".join(lines)[:4900]


def playlist_for(course_title: str, privacy: str, kind: str = "full") -> tuple[str, str]:
    """A playlist is as visible as its videos: a public playlist of unlisted
    lessons would show them to everyone, so paid lessons get their own
    unlisted playlist and free previews a public one (Shorts their own)."""
    if kind == "short":
        return f"{course_title}: Shorts", "public"
    if privacy == "public":
        return f"{course_title}: free previews", "public"
    return f"{course_title} (course members)", "unlisted"


def _ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_from_cues(cues: list) -> str:
    """An SRT track from `render.json` cues `[[start, end, text], ...]`."""
    out = []
    n = 0
    for cue in cues:
        try:
            start, end, text = float(cue[0]), float(cue[1]), str(cue[2]).strip()
        except (TypeError, ValueError, IndexError):
            continue
        if not text or end <= start:
            continue
        n += 1
        out.append(f"{n}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    return "\n".join(out)


def store_url(catalog_store: Mapping[str, object]) -> str:
    domain = str(catalog_store.get("domain") or "").strip()
    return f"https://{domain}/academy/" if domain else ""


# ------------------------------------------------------------------ links

def watch_link_html(video_id: str, store_back: str = "") -> str:
    """The store's "Watch on YouTube" link for a PUBLIC lesson. Empty for a
    missing or malformed id. Callers pass only public ids (`public_ids`)."""
    if not valid_video_id(video_id):
        return ""
    return (f'<a class="yt" href="{escape(watch_url(video_id))}" rel="noopener" '
            f'target="_blank">Watch on YouTube</a>')


# ------------------------------------------------------------------ database

async def embed_ids() -> dict[str, str]:
    """file key -> video id for every lesson a paid page may embed (unlisted
    or public, not withdrawn). The one place the page asks; a database that
    cannot be read gives {} so the page falls back to the stored file."""
    from ...kernel.state import fetch_all
    try:
        rows = await fetch_all(
            "SELECT file_key, video_id FROM academy_lesson_youtube "
            "WHERE NOT withdrawn AND kind = 'full' "
            "AND privacy IN ('unlisted', 'public')")
    except Exception:                                   # noqa: BLE001
        return {}
    return {str(r["file_key"]): str(r["video_id"]) for r in rows
            if valid_video_id(r["video_id"])}


async def public_ids() -> dict[str, str]:
    """catalog key -> video id for the PUBLIC previews only (the store's "Watch
    on YouTube" links). A lesson's key is its file key; a product's is
    `product-<slug>`. An unlisted or private id never comes back."""
    from ...kernel.state import fetch_all
    try:
        rows = await fetch_all(
            "SELECT file_key, video_id FROM academy_lesson_youtube "
            "WHERE NOT withdrawn AND done AND privacy = 'public' "
            "AND kind = 'preview'")
    except Exception:                                   # noqa: BLE001
        return {}
    return {str(r["file_key"]).removeprefix("preview:"): str(r["video_id"])
            for r in rows if valid_video_id(r["video_id"])}


async def uploaded_today() -> int:
    """Uploads to the Academy so far today (UTC), of every kind: full lessons,
    previews, Shorts (all rows of `academy_lesson_youtube`) and anything sent
    through the generic card (`academy_youtube_uploads`, which may not exist
    yet). One shared budget, counted from the tables so a restart cannot reset
    it; the larger of the two counts, so neither path can undercount."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM academy_lesson_youtube "
        "WHERE uploaded_at >= date_trunc('day', now() AT TIME ZONE 'utc')")
    n = int(row["n"]) if row else 0
    try:
        row = await fetch_one(
            "SELECT count(*) AS n FROM academy_youtube_uploads "
            "WHERE at >= date_trunc('day', now() AT TIME ZONE 'utc')")
    except Exception:                                   # noqa: BLE001  no table yet
        row = None
    return max(n, int(row["n"]) if row else 0)


# ------------------------------------------------------------------ action

def _lookup(key: str):
    from . import catalog as cat_mod
    cat = cat_mod.load()
    for p in cat.products:
        for o in p.offers:
            for f in o.files:
                if f.key == key:
                    return cat, p, f
    return cat, None, None


def _label(label: str) -> str:
    m = re.match(r"^Lesson\s+\d+\.\d+:\s*(.*?)(?:\s*\(video\))?\s*$", label)
    return m.group(1) if m else label


async def _plan(course: str, num: str, kind: str = "full", product: str = ""):
    """Everything the upload needs, or (None, why). No network. Every kind
    passes the same source gate (`staged_refusal`)."""
    if (why := vet({"course": course, "num": num, "kind": kind,
                    "product": product})) is not None:
        return None, why
    ch = lesson_channel()
    if product:
        cat = _catalog_only()
        prod = cat.product(product)
        if prod is None or not any(f.path.lower().endswith(".pdf")
                                   for o in prod.offers for f in o.files):
            return None, f"{product} is not an ebook/PDF product in the catalog"
        base, name, title = f"product-{product}", product_stem(product), prod.title
        course, num = "product", product
    else:
        key = file_key(course, num) or ""
        cat, prod, f = _lookup(key)
        if prod is None or f is None:
            return None, f"{key} is not a lesson in the catalog"
        base, name, title = key, stem(course, num, kind), _label(f.label)
    path = staging_dir() / f"{name}.mp4"
    if (why := staged_refusal(path, kind=kind)) is not None:
        return None, why
    seconds = gate_seconds(path)
    if seconds is None:
        return None, "the gate record has no length, so the title cannot be made"
    return {"channel": ch, "seconds": seconds, "key": row_key(kind, base), "base": base,
            "kind": kind, "stem": name, "path": path, "product": prod,
            "course": course, "num": num, "is_product": bool(product),
            "lesson_title": title, "privacy": privacy_for(base, kind),
            "store": store_url(cat.store)}, None


def _catalog_only():
    from . import catalog as cat_mod
    return cat_mod.load()


async def upload_lesson_dry(course: str = "", num: str = "", kind: str = "full",
                            product: str = "") -> ActionResult:
    from ...kernel.state import fetch_one
    plan, why = await _plan(course, num, kind, product)
    if plan is None:
        return ActionResult(False, why or "not ready")
    row = await fetch_one(
        "SELECT video_id, withdrawn FROM academy_lesson_youtube WHERE file_key = $1",
        plan["key"])
    if row and not row["withdrawn"]:
        return ActionResult(False, f"{plan['key']} is already on YouTube ({row['video_id']})")
    if await uploaded_today() >= per_day():
        return ActionResult(False, f"today's cap of {per_day()} Academy uploads "
                                   "(all kinds together) is used")
    where = "PUBLIC" if plan["privacy"] == "public" else "UNLISTED"
    what = {"full": "lesson", "preview": "preview", "short": "Short"}[plan["kind"]]
    return ActionResult(True, f"would upload the {what} {plan['key']} to the "
                              f"{plan['channel']} channel and make it {where}")


async def upload_lesson(course: str = "", num: str = "", kind: str = "full",
                        product: str = "") -> ActionResult:
    from ...kernel.state import execute_sql, fetch_one
    from ..youtube import api
    plan, why = await _plan(course, num, kind, product)
    if plan is None:
        return ActionResult(False, why or "not ready")
    key, ch, path = plan["key"], plan["channel"], plan["path"]
    course, num, kind = plan["course"], plan["num"], plan["kind"]
    row = await fetch_one(
        "SELECT * FROM academy_lesson_youtube WHERE file_key = $1", key)
    if row and not row["withdrawn"] and row.get("done"):
        return ActionResult(False, f"{key} is already on YouTube ({row['video_id']})")
    if row is None and await uploaded_today() >= per_day():
        return ActionResult(False, f"today's cap of {per_day()} Academy uploads "
                                   "(all kinds together) is used")
    digest = sha256_of(path)
    if row is None:
        claim = f"{key}:{digest[:16]}"
        state = await idempotency.claim(ACTION, claim)
        if state != idempotency.CLAIMED:
            return ActionResult(False, idempotency.refusal(ACTION, state))
        public = plan["privacy"] == "public"
        up, why = await api.upload(
            ch, str(path), build_title(plan["lesson_title"], plan["seconds"]),
            build_description(plan["product"].title, num, plan["lesson_title"],
                             plan["store"], public=public, kind=kind,
                             product=plan["is_product"]),
            ["AI", "Claude", "tutorial", plan["product"].title][:15],
            category_id=api.EDUCATION, language=api.TUTORIAL_LANGUAGE, synthetic=True)
        if up is None:
            await idempotency.finish(ACTION, claim, why)
            return ActionResult(False, f"upload refused: {why}")
        from ..youtube import _mark_academy
        await _mark_academy(up["video_id"])
        await execute_sql(
            """INSERT INTO academy_lesson_youtube
                   (file_key, course, num, video_id, privacy, sha256, detail, kind)
               VALUES ($1, $2, $3, $4, 'private', $5, 'uploaded private', $6)
               ON CONFLICT (file_key) DO UPDATE
                  SET video_id = $4, privacy = 'private', sha256 = $5,
                      withdrawn = false, done = false, uploaded_at = now()""",
            key, course, num, up["video_id"], digest, kind)
        await idempotency.settle(ACTION, claim, f"uploaded {up['video_id']}")
        row = {"video_id": up["video_id"], "thumb_set": False, "captions_set": False,
               "item_id": None, "playlist_id": None}
    vid = str(row["video_id"])
    said = [f"uploaded {vid} (private)"]
    undo: dict = {"channel": ch, "video_id": vid, "key": key}
    # Captions are burned in; the track only adds search and translation, so a
    # miss never holds the lesson back.
    # A Short carries neither captions nor a thumbnail (burned in; vertical).
    cues = _cues(plan["stem"]) if kind != "short" else []
    if cues and not row.get("captions_set"):
        cap, why = await api.set_captions(ch, vid, srt_from_cues(cues))
        row["captions_set"] = cap is not None
        said.append("captions set" if cap else f"captions NOT set: {why}")
    thumb = _thumb(plan["stem"]) if kind != "short" else None
    if thumb is not None and not row.get("thumb_set"):
        ok, why = await api.set_thumbnail(ch, vid, str(thumb))
        row["thumb_set"] = ok is not None
        said.append("thumbnail set" if ok else f"thumbnail NOT set: {why}")
    ptitle, pprivacy = playlist_for(plan["product"].title, plan["privacy"], kind)
    if not row.get("item_id"):
        pl, why = await api.find_playlist(ch, ptitle)
        if pl == {}:
            pl, why = await api.create_playlist(
                ch, ptitle, f"{plan['product'].title} on ATLAS Academy", pprivacy)
        item = None
        if pl:
            item, why = await api.add_playlist_item(ch, pl["id"], vid)
        if item:
            row["playlist_id"], row["item_id"] = pl["id"], item["item_id"]
            said.append(f'in "{ptitle}"')
        else:
            said.append(f"NOT in the playlist: {why}")
    if row.get("item_id"):
        undo["item_id"] = row["item_id"]
    # Audience last, and only when the playlist took: an unlisted lesson with
    # no playlist is harmless, a public one is the owner's card.
    done_privacy, why = await api.set_privacy(ch, vid, plan["privacy"], synthetic=True)
    if done_privacy is None:
        said.append(f"still private: {why}")
    else:
        said.append(plan["privacy"])
    ok = done_privacy is not None
    await execute_sql(
        """UPDATE academy_lesson_youtube
              SET privacy = $2, playlist_id = $3, item_id = $4, thumb_set = $5,
                  captions_set = $6, done = $7, detail = $8
            WHERE file_key = $1""",
        key, plan["privacy"] if ok else "private", row.get("playlist_id"),
        row.get("item_id"), bool(row.get("thumb_set")),
        bool(row.get("captions_set")), ok, "; ".join(said)[:1000])
    evidence = {"video_id": vid, "privacy": plan["privacy"] if ok else "private"}
    if not ok:
        return ActionResult(False, f"{key}: " + "; ".join(said), evidence=evidence)
    return ActionResult(True, f"{key}: " + "; ".join(said), evidence=evidence,
                        rollback_token=json.dumps(undo, sort_keys=True))


def _cues(name: str) -> list:
    try:
        data = json.loads((staging_dir() / f"{name}.render.json").read_text())
    except (OSError, ValueError):
        return []
    cues = data.get("cues") if isinstance(data, dict) else None
    return cues if isinstance(cues, list) else []


def _thumb(name: str) -> Path | None:
    """The staged thumbnail `<stem>.jpg` or `<stem>.png` (lesson_thumb makes it)."""
    for ext in (".jpg", ".png"):
        p = staging_dir() / f"{name}{ext}"
        if p.is_file() and not p.is_symlink():
            return p
    return None


async def upload_lesson_rollback(token: str) -> ActionResult:
    """Private again and out of its playlist. Never a delete."""
    from ...kernel.state import execute_sql
    from ..youtube import api
    try:
        undo = json.loads(token)
        ch, vid = str(undo["channel"]), str(undo["video_id"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "rollback token unreadable")
    _r, why = await api.set_privacy(ch, vid, "private", synthetic=True)
    if why is not None:
        return ActionResult(False, why)
    said = [f"{vid} private"]
    if undo.get("item_id"):
        _ok, why = await api.take_out_of_playlist(ch, str(undo["item_id"]))
        said.append("out of its playlist" if why is None else f"still in playlist: {why}")
    await execute_sql(
        "UPDATE academy_lesson_youtube SET withdrawn = true, privacy = 'private' "
        "WHERE video_id = $1", vid)
    return ActionResult(True, "; ".join(said))
