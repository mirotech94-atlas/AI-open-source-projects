"""The series engine: a series is planned whole, then made part by part.

Miro, 2026-09-27 (relayed): "make all the parts without me having to tell
you, and finish the video correctly." A series is never left on a "Part 2"
forever:

  * **Planned up front.** `plan_series` (Tier AUTO) writes the whole arc --
    3 to 8 parts with a real ending -- and a fixed character sheet, and
    stores it (`reel_series`). Miro's own series are seeded with his
    outline instead (`SEEDS`).
  * **Made in order.** `draft_part` (Tier AUTO) drafts the next part from
    the stored outline, the synopses of the parts before it and the same
    character sheet word for word, as dialogue (no narrator). Part N of M is
    in the FORMAT line, so the renderer ends it on a "PART N" card and the
    finale on "THE END"; the finale's caption says "Final part".
  * **One at a time.** A part is drafted only after the part before it
    posted; when the finale posts, the series is done and the next queued
    one starts (`kernel.reels.series_step`).

Series text is ATLAS's own (Miro's outline or a plan it drafted); a plan
drafted from research quotes that research as untrusted, like every draft.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.llm import Message, Need, think
from ...kernel.permissions import wrap_untrusted
from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import guard, reel_script
from .reels import _json_object, beat_range

log = logging.getLogger("atlas.content")

MIN_PARTS, MAX_PARTS = 3, 8
ARABIC = "Egyptian Arabic"

# Miro's own series, seeded once (by title) with his outline.
SEEDS: tuple[dict[str, Any], ...] = (
    {
        "title": "Emergency Room: Code Chaos",
        "format": "Egyptian Arabic hospital ER comedy series",
        "characters": (
            "Original stylized 3D characters at the fictional St. Nowhere "
            "General (no real hospital): Dr. Kareem, an overconfident surgeon, "
            "slicked-back hair, sunglasses worn indoors, teal scrubs, a "
            "confident male voice; Nurse Hoda, the head nurse who secretly "
            "runs the place, arms always crossed, lavender scrubs, a dry "
            "sarcastic female voice; Bassem, a nervous first-day intern with a "
            "shaking clipboard, oversized white coat, a young male voice; Mr. "
            "Sayed, a dramatic grandpa in a striped pyjama and flat cap, an "
            "old raspy male voice."),
        "outline": [
            ("Mr. Sayed arrives screaming that he is dying -- it is a paper cut. "
             "Dr. Kareem orders a full surgery team; Nurse Hoda hands Sayed a "
             "bandage. Cliffhanger: the hospital inspection is tomorrow."),
            ("Inspection panic. Bassem mislabels everything in sight. Sayed "
             "slips away to find his 'last meal' in the cafeteria."),
            ("The inspector arrives. Kareem, showing off, gets stuck in a "
             "wheelchair rolling down the hall. Cliffhanger: the inspector "
             "collapses -- real, or acting?"),
            ("Panic. Only Bassem stays calm and does everything by the book. "
             "The inspector sits up: it was a test."),
            ("Finale: the report praises Hoda and Bassem; Kareem is humbled; "
             "Sayed is discharged, 'dies' again at the door, and everyone "
             "laughs. The end."),
        ],
        # Being rendered by hand, one part a day Sep 28 - Oct 2, by Miro's
        # engine session (relayed 2026-09-28): registered as made, so this
        # engine never remakes it and starts The Parking Spot after it.
        "status": "done",
        "position": 1,
        "rules": ("No real hospital names, no medical claims or drug names, "
                  "never mocking a real illness -- the situation is the joke. "
                  "Parts 1-5 rendered by hand Sep 28 - Oct 2."),
    },

)


PACKAGE = Path(__file__).parent / "calendars" / "series-v1.json"


@lru_cache(maxsize=1)
def package() -> list[dict[str, Any]]:
    """Fourteen series, 56 episodes, written in full by Miro's engine-setup
    session (2026-09-28) and handed to this engine when Miro chose ATLAS's
    pipeline to own the reels. Each part is 8 beats: the scene with its
    quoted spoken line, the speaker, and the English caption. Checked against
    the brand and IP guards when they were taken in."""
    with open(PACKAGE, encoding="utf-8") as fh:
        return list(json.load(fh)["series"])


def format_of(item: dict[str, Any]) -> str:
    return ("Egyptian Arabic series" if item.get("arabic") else "series")


async def ensure_seeds() -> None:
    for index, item in enumerate(package(), start=10):
        # A series added later can take a place in the queue (`position`);
        # among equal positions the one taken in first goes first.
        position = int(item.get("position", index))
        scripts = json.dumps(item["parts"], ensure_ascii=False)
        outline = json.dumps([p["caption"].splitlines()[0] for p in item["parts"]],
                             ensure_ascii=False)
        # A series of the same title planned here before the scripts arrived
        # (The Parking Spot, 2026-09-28) takes the scripts, and any part it
        # drafted but never posted is dropped, so nothing is made twice.
        replaced = await fetch_all(
            """UPDATE reel_series SET scripts = $2::jsonb, characters = $3,
                      outline = $4::jsonb, total_parts = $5, format = $6
                WHERE title = $1 AND scripts IS NULL AND status <> 'done'
            RETURNING id""",
            item["title"], scripts, item["cast"], outline, len(item["parts"]),
            format_of(item))
        for row in replaced:
            await execute_sql(
                """UPDATE content_drafts SET status = 'dropped' WHERE id IN (
                       SELECT draft_id FROM reel_series_parts
                        WHERE series_id = $1 AND status = 'drafted')""", row["id"])
            await execute_sql(
                """DELETE FROM reel_series_parts WHERE series_id = $1
                      AND status = 'drafted'""", row["id"])
        await execute_sql(
            """INSERT INTO reel_series (title, format, characters, outline,
                                        total_parts, status, position, scripts)
               SELECT $1, $2, $3, $4::jsonb, $5, 'queued', $6, $7::jsonb
               WHERE NOT EXISTS (SELECT 1 FROM reel_series WHERE title = $1)""",
            item["title"], format_of(item), item["cast"], outline,
            len(item["parts"]), position, scripts)
    for seed in SEEDS:
        await execute_sql(
            """INSERT INTO reel_series (title, format, characters, outline,
                                        total_parts, status, position, rules)
               SELECT $1, $2, $3, $4::jsonb, $5, $6, $7, $8
               WHERE NOT EXISTS (SELECT 1 FROM reel_series WHERE title = $1)""",
            seed["title"], seed["format"], seed["characters"],
            json.dumps(seed["outline"]), len(seed["outline"]), seed["status"],
            seed["position"], seed["rules"])


async def current() -> dict[str, Any] | None:
    """The series being made, starting the next queued one when none is."""
    row = await fetch_one(
        "SELECT * FROM reel_series WHERE status = 'active' ORDER BY id LIMIT 1")
    if row is None:
        row = await fetch_one(
            """UPDATE reel_series SET status = 'active'
                WHERE id = (SELECT id FROM reel_series WHERE status = 'queued'
                             ORDER BY position, id LIMIT 1)
            RETURNING *""")
    return dict(row) if row else None


async def parts(series_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in await fetch_all(
        """SELECT part_no, draft_id, media_id, status, redos FROM reel_series_parts
            WHERE series_id = $1 ORDER BY part_no""", series_id)]


def outline_of(series: dict[str, Any]) -> list[str]:
    raw = series.get("outline")
    return list(json.loads(raw) if isinstance(raw, str) else (raw or []))


# ----------------------------------------------------------------- drafting

PART_SYSTEM = (
    "You write ONE part of a short comedy/drama series for an AI-stories "
    "account, as a scene the characters ACT and SPEAK to each other on "
    "camera. There is no narrator and no voice-over, ever. Return only JSON: "
    "{\"scenes\": [\"<beat>\", ...], \"caption\": \"<a one-line caption>\"}. "
    "Write between {low} and {high} beats; each beat is ONE exchange: one or "
    "two lines written 'Name: line', two lines separated by ' | ', spoken to "
    "one another while they move and interact, every line a reaction to the "
    "one before. {language} Use exactly the characters on the sheet, by "
    "name; follow this part's synopsis and stay true to the parts before it. "
    "{ending} Never a real person, brand, hospital, studio or film; no "
    "timings, camera directions or commentary about the video. {rules}"
)


async def draft_part(series_id: int) -> ActionResult:
    """Draft the next part of a series as a story_script (Tier AUTO)."""
    series = await fetch_one("SELECT * FROM reel_series WHERE id = $1", series_id)
    if series is None:
        return ActionResult(False, f"no series {series_id}")
    series = dict(series)
    outline = outline_of(series)
    made = await parts(series_id)
    n = len(made) + 1
    if n > len(outline):
        return ActionResult(False, f"{series['title']}: every part is drafted")
    if made and made[-1]["status"] != "posted":
        return ActionResult(False, f"{series['title']}: part {n - 1} has not "
                                   f"posted yet, so part {n} waits")
    total = len(outline)
    final = n == total
    raw = series.get("scripts")
    scripts = json.loads(raw) if isinstance(raw, str) else raw
    if scripts:
        return await _draft_scripted(series, scripts[n - 1], n, total)
    arabic = ARABIC.lower() in str(series["format"]).lower()
    low, high = beat_range(str(series["format"]))
    system = (PART_SYSTEM.replace("{low}", str(low)).replace("{high}", str(high))
              .replace("{language}", (
                  "Write every line in Egyptian Arabic (Egyptian colloquial, "
                  "Arabic script); the names before the colons stay in English."
                  if arabic else "Write the lines in English."))
              .replace("{ending}", (
                  "This is the FINALE: resolve the story fully, a real ending, "
                  "no cliffhanger. The caption starts 'Final part:'." if final else
                  "End on a cliffhanger spoken by a character (the last line), "
                  "and the caption asks viewers to follow for the next part."))
              .replace("{rules}", str(series.get("rules") or "")))
    before = "\n".join(f"Part {i}: {s}" for i, s in enumerate(outline[:n - 1], 1))
    ask = (f"Series: {series['title']} ({series['format']})\n"
           f"Character sheet: {series['characters']}\n"
           f"Parts so far:\n{before or '(this is part 1)'}\n"
           f"THIS is part {n} of {total}: {outline[n - 1]}")
    completion = await think(Need.DRAFT, system, [Message("user", ask)],
                             max_tokens=1600)
    got = _json_object(completion.text) or {}
    scenes = [" ".join(str(x).split()) for x in got.get("scenes") or [] if str(x).strip()]
    caption = " ".join(str(got.get("caption") or "").split())
    if len(scenes) < low or not caption:
        return ActionResult(False, f"{series['title']} part {n}: the model returned "
                                   f"{len(scenes)} beat(s) and "
                                   f"{'a' if caption else 'no'} caption; it needs "
                                   f"{low}-{high}")
    if final and not caption.lower().startswith("final part"):
        caption = f"Final part: {caption}"
    scenes = scenes[:high]
    if (why := guard.refusal("\n".join([caption, *scenes]))) is not None:
        return ActionResult(False, f"not drafted: {why}")
    name = f"{series['title']} ({series['format']}) — Part {n} of {total}"
    body = reel_script.compose(name, scenes, caption, str(series["characters"]))
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body, status)
           VALUES ('story_script', $1, $2, $3, 'draft') RETURNING id""",
        f"series:{series_id}:{n}", f"{series['title']} — Part {n}"[:200], body)
    draft_id = int(row["id"])
    await execute_sql(
        """INSERT INTO reel_series_parts (series_id, part_no, draft_id, status)
           VALUES ($1, $2, $3, 'drafted')""", series_id, n, draft_id)
    script = reel_script.parse(body, max_scenes=high)
    return ActionResult(
        True, f"drafted {series['title']} part {n} of {total} ({len(scenes)} beats)",
        evidence={"id": draft_id, "part": n, "total": total, "final": final,
                  "caption": script.caption, "scenes": list(script.scenes),
                  "body_sha256": hashlib.sha256(body.encode()).hexdigest()[:12]},
        rollback_token=str(draft_id))


def scripted_beat(beat: dict[str, Any]) -> str:
    """One written beat as a story_script line: the speaker and the English
    caption, then the whole scene -- with its quoted line in the language
    it is spoken -- in [[...]] for the video prompt."""
    speaker = " ".join(str(beat["speaker"]).split()).title()
    return f"{speaker}: {beat['caption_en']} [[{' '.join(str(beat['scene']).split())}]]"


async def _draft_scripted(series: dict[str, Any], part: dict[str, Any], n: int,
                          total: int) -> ActionResult:
    """A part that was written in full: no model is asked; the script is
    filed as the draft, word for word."""
    sid = int(series["id"])
    scenes = [scripted_beat(b) for b in part["beats"]]
    caption = str(part.get("caption") or f"{series['title']} — Part {n}")
    text = "\n".join([caption, str(series["characters"]), *scenes])
    if (why := guard.refusal(text) or guard.ip_refusal(text)) is not None:
        return ActionResult(False, f"not drafted: {why}")
    name = f"{series['title']} ({series['format']}) — Part {n} of {total}"
    body = reel_script.compose(name, scenes, caption, str(series["characters"]))
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body, status)
           VALUES ('story_script', $1, $2, $3, 'draft') RETURNING id""",
        f"series:{sid}:{n}", f"{series['title']} — Part {n}"[:200], body)
    draft_id = int(row["id"])
    await execute_sql(
        """INSERT INTO reel_series_parts (series_id, part_no, draft_id, status)
           VALUES ($1, $2, $3, 'drafted')""", sid, n, draft_id)
    script = reel_script.parse(body, max_scenes=20)
    return ActionResult(
        True, f"filed {series['title']} part {n} of {total} from its script "
              f"({len(scenes)} beats)",
        evidence={"id": draft_id, "part": n, "total": total, "final": n == total,
                  "caption": script.caption, "scenes": list(script.scenes),
                  "body_sha256": hashlib.sha256(body.encode()).hexdigest()[:12]},
        rollback_token=str(draft_id))


async def draft_part_dry(series_id: int) -> ActionResult:
    series = await fetch_one("SELECT title FROM reel_series WHERE id = $1", series_id)
    if series is None:
        return ActionResult(False, f"no series {series_id}")
    return ActionResult(True, f"would draft the next part of {series['title']}")


async def draft_part_rollback(token: str) -> ActionResult:
    await execute_sql("UPDATE content_drafts SET status = 'dropped' WHERE id = $1",
                      int(token))
    await execute_sql("DELETE FROM reel_series_parts WHERE draft_id = $1", int(token))
    return ActionResult(True, f"series part draft {token} dropped")


# ------------------------------------------------------------------ results
#
# Miro, 2026-09-29: the AI-stories reels "improve over time with each video",
# so a new series is planned from what the ones before it earned. Linkage: a
# posted part's `reel_series_parts.media_id` is the `reel_posts.media_id` of
# each platform it went to, and each post's figures are its LATEST
# `reel_metrics` row by (platform, post_ref). `reel_posts.format_id` is NULL
# for a series part (only `reel_format:` drafts carry one), so it is never
# joined on.
#
# Figures stay per platform, never summed across them: a part on Instagram
# and TikTok is not "twice" a part on Instagram alone, and TikTok is often
# unread. A reading taken less than `MATURE_DAYS` after the post is shown
# but never ranked, or yesterday's series would always look worst. A figure
# that was not read is None ("not read"), never 0.

MATURE_DAYS = 3.0          # a reading this long after posting can be ranked
MIN_READ_PARTS = 2         # a series is ranked on at least this many parts
TOP_N, BOTTOM_N = 3, 2

RESULTS_SQL = """
    SELECT s.id AS series_id, s.title, s.format, s.status, s.total_parts,
           sp.part_no, p.platform, p.at AS posted_at,
           m.views, m.likes, m.comments, m.shares, m.at AS metrics_read_at
      FROM reel_series s
      LEFT JOIN reel_series_parts sp
        ON sp.series_id = s.id AND sp.status = 'posted'
      LEFT JOIN reel_posts p
        ON sp.media_id IS NOT NULL AND p.media_id = sp.media_id
       AND p.platform <> 'youtube'
      LEFT JOIN LATERAL (
            SELECT views, likes, comments, shares, at FROM reel_metrics
             WHERE platform = p.platform AND post_ref = p.post_ref
             ORDER BY at DESC LIMIT 1) m ON true
     WHERE s.status IN ('active', 'done')
     ORDER BY s.id, sp.part_no, p.platform"""


def _platform_summary(readings: list[dict[str, Any]]) -> dict[str, Any]:
    """One platform's readings of one series. Engagement is summed
    interactions over summed views, from posts where all four were read --
    never an average of ratios."""
    views = {r["part_no"]: r["views"] for r in readings}
    engaged = [r for r in readings if r["interactions"] is not None and r["views"]]
    seen = sum(r["views"] for r in engaged)
    mature = {r["part_no"]: r["views"] for r in readings if r["mature"]}
    return {
        "parts_read": len(views),
        "views": sum(views.values()),
        "views_per_part": sum(views.values()) / len(views),
        "per_part_views": dict(sorted(views.items())),
        "engagement": (sum(r["interactions"] for r in engaged) / seen
                       if seen else None),
        "read_at": min(r["read_at"] for r in readings),      # the oldest figure
        "min_age_days": min(r["age_days"] for r in readings),
        "parts_mature": len(mature),
        "mature_views_per_part": (sum(mature.values()) / len(mature)
                                  if mature else None),
    }


def digest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per active or finished series: title, format, parts posted, and per
    platform the parts read, total and per-part views, engagement and when
    it was read. A series with nothing read has `read` False and no
    platforms -- never a zero. Pure: the rows are `RESULTS_SQL`'s."""
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        sid = int(row["series_id"])
        item = out.setdefault(sid, {
            "series_id": sid, "title": str(row["title"]), "format": str(row["format"]),
            "status": row["status"], "total_parts": int(row["total_parts"]),
            "arabic": ARABIC.lower() in str(row["format"]).lower(),
            "posted": set(), "readings": []})
        if row.get("part_no") is None:
            continue
        item["posted"].add(int(row["part_no"]))
        if row.get("platform") is None or row.get("views") is None:
            continue                          # never recorded, or not read
        read_at, posted_at = row["metrics_read_at"], row["posted_at"]
        age = (read_at - posted_at).total_seconds() / 86400
        counts = [row.get(k) for k in ("likes", "comments", "shares")]
        item["readings"].append({
            "platform": str(row["platform"]), "part_no": int(row["part_no"]),
            "views": float(row["views"]), "posted_at": posted_at,
            "read_at": read_at, "age_days": age, "mature": age >= MATURE_DAYS,
            "interactions": (sum(float(c) for c in counts)
                             if all(c is not None for c in counts) else None)})
    result = []
    for item in out.values():
        by_platform: dict[str, list[dict[str, Any]]] = {}
        for r in item["readings"]:
            by_platform.setdefault(r["platform"], []).append(r)
        item["parts_posted"] = len(item.pop("posted"))
        item["platforms"] = {k: _platform_summary(v)
                             for k, v in sorted(by_platform.items())}
        item["read"] = bool(item["platforms"])
        result.append(item)
    return result


async def results_digest() -> list[dict[str, Any]]:
    """What every active or finished series earned, read fresh each time
    (never reused). Raises when the tables cannot be read."""
    return digest([dict(r) for r in await fetch_all(RESULTS_SQL)])


def ranked(results: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """(platform, the series with at least `MIN_READ_PARTS` mature parts on
    it, best mature views-per-part first). The platform is the one with the
    most mature parts; Instagram on a tie."""
    counts: dict[str, int] = {}
    for item in results:
        for name, plat in item["platforms"].items():
            counts[name] = counts.get(name, 0) + plat["parts_mature"]
    counts = {k: v for k, v in counts.items() if v}
    if not counts:
        return None, []
    platform = max(sorted(counts), key=lambda k: (counts[k], k == "instagram"))
    eligible = [i for i in results
                if i["platforms"].get(platform, {}).get("parts_mature", 0)
                >= MIN_READ_PARTS]
    eligible.sort(key=lambda i: -i["platforms"][platform]["mature_views_per_part"])
    return platform, eligible


def _line(item: dict[str, Any], platform: str) -> str:
    p = item["platforms"][platform]
    eng = (f"{p['engagement'] * 100:.1f}% engagement" if p["engagement"] is not None
           else "engagement not read")
    return (f"- {item['title']} ({item['format']}; {item['parts_posted']} of "
            f"{item['total_parts']} parts posted): "
            f"{p['mature_views_per_part']:,.0f} views per part over "
            f"{p['parts_mature']} part(s) read {MATURE_DAYS:.0f}+ days after "
            f"posting ({p['views']:,.0f} views on all {p['parts_read']} read), "
            f"{eng}; read {p['read_at']:%Y-%m-%d %H:%M} UTC")


def _distinguished(top: list[dict[str, Any]], bottom: list[dict[str, Any]]) -> str:
    """What the top had that the bottom did not, from fields on file only
    -- never a judgement the model makes up."""
    def arabic(group: list[dict[str, Any]]) -> str:
        return f"{sum(1 for i in group if i['arabic'])}/{len(group)}"

    def length(group: list[dict[str, Any]]) -> str:
        return f"{sum(i['total_parts'] for i in group) / len(group):.1f}"

    def formats(group: list[dict[str, Any]]) -> str:
        return "; ".join(sorted({i["format"] for i in group}))

    return (f"Top vs weakest: Egyptian Arabic {arabic(top)} vs {arabic(bottom)}; "
            f"average length {length(top)} vs {length(bottom)} parts; formats "
            f"[{formats(top)}] vs [{formats(bottom)}].")


def results_block(results: list[dict[str, Any]]) -> str | None:
    """The bounded context `plan_series` gets: the top 3 and bottom 2 series
    by views per part on one platform, or None without enough data. Fewer
    than five split evenly (two: one against one; one: no comparison)."""
    platform, eligible = ranked(results)
    if platform is None or not eligible:
        return None
    n = len(eligible)
    bottom_n = min(BOTTOM_N, n // 2)
    top = eligible[:min(TOP_N, n - bottom_n)]
    bottom = eligible[n - bottom_n:] if bottom_n else []
    lines = [f"Past series on {platform}, by views per part:", "Best:",
             *(_line(i, platform) for i in top)]
    if bottom:
        lines += ["Weakest:", *(_line(i, platform) for i in bottom),
                  _distinguished(top, bottom)]
    return "\n".join(lines)


def format_bonus(results: list[dict[str, Any]],
                 now: datetime | None = None) -> dict[str, float]:
    """Points per series format name (lower-cased) for choosing the next
    series' format: each mature post's latest reading, weighted exactly as
    `metrics.bonus` weights a reel format (views bar, engagement bar,
    half-life from the post). An unread post earns and costs nothing."""
    from .metrics import bonus
    names = sorted({i["format"].lower() for i in results})
    rows = [{"format_id": names.index(i["format"].lower()), "views": r["views"],
             "engagement": (r["interactions"] / r["views"]
                            if r["interactions"] is not None and r["views"] else None),
             "posted_at": r["posted_at"]}
            for i in results for r in i["readings"] if r["mature"]]
    return {names[k]: v for k, v in bonus(rows, now).items()}


# ----------------------------------------------------------------- planning

PLAN_SYSTEM = (
    "You plan a whole short series for an AI-stories account, in the format "
    "named below: {low_parts}-{high_parts} parts with a real ending (the last "
    "part resolves it; no loose 'to be continued'). Return only JSON: "
    "{\"title\": \"...\", \"characters\": \"<one line: each character's name, "
    "original look -- an unusual colour pattern, their own accessories and "
    "eye shape, no badge or logo -- and voice>\", \"parts\": [\"<one-"
    "sentence synopsis per part>\", ...]}. Original characters only: never a "
    "real person, brand, studio, film or a known film character."
)


async def plan_series(format_name: str) -> ActionResult:
    """Plan the next series in a playbook format and queue it (Tier AUTO)."""
    system = (PLAN_SYSTEM.replace("{low_parts}", str(MIN_PARTS))
              .replace("{high_parts}", str(MAX_PARTS)))
    ask = f"Format: {format_name}"
    if (block := await _results_context()) is not None:
        # Titles are model output and a format can come from research, so
        # the whole block is quoted as data; the one instruction is outside.
        ask += ("\n\nWhat earlier series earned. Lean toward what the best "
                "did and away from what the weakest did; give this series a "
                "NEW title, never one of these:\n"
                + wrap_untrusted(block, "reel_series_results"))
    completion = await think(Need.DRAFT, system, [Message("user", ask)],
                             max_tokens=900)
    got = _json_object(completion.text) or {}
    title = " ".join(str(got.get("title") or "").split())[:120]
    characters = " ".join(str(got.get("characters") or "").split())
    outline = [" ".join(str(p).split()) for p in got.get("parts") or [] if str(p).strip()]
    if not title or not characters or not MIN_PARTS <= len(outline) <= MAX_PARTS:
        return ActionResult(False, f"no usable plan for {format_name!r} "
                                   f"({len(outline)} part(s))")
    text = "\n".join([title, characters, *outline])
    if (why := guard.refusal(text) or guard.ip_refusal(text)) is not None:
        return ActionResult(False, f"plan refused: {why}")
    # A title already on file (or in the package) would be overwritten by
    # `ensure_seeds`, which matches series by title.
    if title.lower() in {str(s["title"]).lower() for s in package()}:
        return ActionResult(False, f"plan refused: {title!r} is already a series")
    row = await fetch_one(
        """INSERT INTO reel_series (title, format, characters, outline,
                                    total_parts, status, position)
           SELECT $1, $2, $3, $4::jsonb, $5, 'queued',
                  (SELECT coalesce(max(position), 0) + 1 FROM reel_series)
            WHERE NOT EXISTS (SELECT 1 FROM reel_series
                               WHERE lower(title) = lower($1))
           RETURNING id""",
        title, format_name, characters, json.dumps(outline), len(outline))
    if row is None:
        return ActionResult(False, f"plan refused: {title!r} is already a series")
    return ActionResult(True, f"planned {title!r}: {len(outline)} parts",
                        evidence={"id": int(row["id"]), "title": title},
                        rollback_token=str(row["id"]))


async def _results_context() -> str | None:
    """The results block for the next plan, or None -- in which case the
    series is planned exactly as before. Unreadable results are logged and
    never a penalty."""
    try:
        return results_block(await results_digest())
    except Exception as exc:  # noqa: BLE001
        log.warning("series results unreadable, planning without them: %s", exc)
        return None


async def plan_series_dry(format_name: str) -> ActionResult:
    return ActionResult(True, f"would plan a {MIN_PARTS}-{MAX_PARTS} part series "
                              f"in the {format_name!r} format")


async def plan_series_rollback(token: str) -> ActionResult:
    await execute_sql(
        "UPDATE reel_series SET status = 'dropped' WHERE id = $1 AND status = 'queued'",
        int(token))
    return ActionResult(True, f"series {token} dropped")


async def redo(series_id: int, part_no: int) -> None:
    """A part that failed a quality check is rendered again, once, from the
    same draft; a second failure is a card for Miro."""
    await execute_sql(
        """UPDATE reel_series_parts SET status = 'drafted', media_id = NULL,
                  redos = redos + 1 WHERE series_id = $1 AND part_no = $2""",
        series_id, part_no)


async def mark(series_id: int, part_no: int, status: str,
               media_id: int | None = None) -> None:
    await execute_sql(
        """UPDATE reel_series_parts SET status = $3,
                  media_id = coalesce($4, media_id)
            WHERE series_id = $1 AND part_no = $2""",
        series_id, part_no, status, media_id)
    if status == "posted":
        await execute_sql(
            """UPDATE reel_series SET status = 'done'
                WHERE id = $1 AND total_parts <= (
                      SELECT count(*) FROM reel_series_parts
                       WHERE series_id = $1 AND status = 'posted')""", series_id)
