"""ytlearn -- ATLAS listens to YouTube videos and keeps the lessons for Miro.

Two sources feed it (kernel/video_learn.py): videos Miro pastes ("learn <url>")
and the Atlas Academy channel's own public videos. `youtube.py` reads a
transcript (a read-only collector), `drafter.py` turns it into a lessons draft
with the transcript wrapped as untrusted, and the draft becomes one Tier 2 card.

  * `ytlearn.save_video_notes` -- approving the card marks ONE row of
    `video_lessons` as approved knowledge. That is all it does. It never edits a
    prompt, rule, code, config, memory, lesson ledger or any agent's skills
    (invariant 3: self-modification stays Tier 3, permanently denied). Nothing
    in ATLAS reads an approved note back into a prompt; Miro reads it and
    decides what to do with it himself.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from ...kernel import idempotency
from ...kernel.audit import audit
from ...kernel.contracts import Action, ActionResult, FailureClass, Tier
from ...kernel.permissions import wrap_untrusted
from .drafter import LESSON_MAX
from .youtube import ID_RE, TITLE_MAX, WATCH

log = logging.getLogger("atlas.ytlearn")

ACTION = "ytlearn.save_video_notes"
PARAMS = {
    "video_id": "the 11-character YouTube video id",
    "title": "the video's title, untrusted",
    "channel": "the channel that posted it, untrusted",
    "published": "the upload date, or 'unknown'",
    "source_url": "the watch address, built from the id",
    "read_at": "when the transcript was read (UTC)",
    "lessons": "the drafted takeaways, rules and checklist, untrusted",
    "lessons_sha256": "hash of the lessons text",
}


def _vet(params: dict[str, Any]) -> str | None:
    vid = str(params.get("video_id") or "")
    if not ID_RE.fullmatch(vid):
        return "the video id is not a YouTube id"
    if str(params.get("source_url") or "") != WATCH.format(id=vid):
        return "the source link is not the video's own address"
    lessons = str(params.get("lessons") or "")
    if not lessons.strip() or len(lessons) > LESSON_MAX:
        return "the lessons are empty or too long"
    if len(str(params.get("title") or "")) > TITLE_MAX:
        return "the title is too long"
    if hashlib.sha256(lessons.encode("utf-8")).hexdigest() != str(
            params.get("lessons_sha256") or ""):
        return "the lessons do not match their hash"
    if not str(params.get("read_at") or "").strip():
        return "the read time is missing"
    return None


def _card_detail(params: dict[str, Any]) -> str:
    """Every parameter, whole: the card IS the decision (what is shown is what
    is saved). Outside text sits inside the untrusted wrapper."""
    body = (f"Title: {params.get('title')}\nChannel: {params.get('channel')}\n"
            f"Published: {params.get('published')}\n\n{params.get('lessons')}")
    return (wrap_untrusted(body, f"youtube:{params.get('video_id')}")
            + f"\nVideo: {params.get('video_id')} -- {params.get('source_url')}"
              f"\nRead at: {params.get('read_at')}"
              f"\nLessons hash: {params.get('lessons_sha256')}"
              "\nApproving saves these lessons as a note in ATLAS's database for "
              "you to read. It changes no prompt, rule, code, setting or agent skill.")


async def save_dry(video_id: str, title: str, channel: str, published: str,
                   source_url: str, read_at: str, lessons: str,
                   lessons_sha256: str) -> ActionResult:
    why = _vet(locals())
    if why:
        return ActionResult(False, f"would not save: {why}")
    return ActionResult(True, "would mark this video's lessons as saved knowledge; "
                              "applies nothing else")


async def save_video_notes(video_id: str, title: str, channel: str, published: str,
                           source_url: str, read_at: str, lessons: str,
                           lessons_sha256: str) -> ActionResult:
    """Mark the drafted row approved, only if it still holds exactly the text
    that was shown. One UPDATE on `video_lessons`; nothing else is touched."""
    why = _vet(locals())
    if why:
        return ActionResult(False, f"not saved: {why}",
                            failure_class=FailureClass.VALIDATION)
    run = idempotency.current_run() or f"sha:{lessons_sha256[:40]}"
    if await idempotency.claim(ACTION, run) != idempotency.CLAIMED:
        return ActionResult(True, "already saved; nothing else was changed")
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """UPDATE video_lessons SET status = 'approved', approved_at = now()
           WHERE video_id = $1 AND status = 'drafted' AND lessons_sha256 = $2
             AND lessons = $3 RETURNING video_id""",
        video_id, lessons_sha256, lessons)
    if row is None:
        await idempotency.release(ACTION, run)
        return ActionResult(False, "not saved: the stored draft is not the one "
                                   "shown, or was already decided",
                            failure_class=FailureClass.VALIDATION)
    await idempotency.settle(ACTION, run, f"video {video_id}")
    await audit("ytlearn.save_video_notes.saved", video_id,
                {"sha256": lessons_sha256[:16]})
    return ActionResult(True, f"saved the lessons for {video_id}; nothing else was "
                              "changed", evidence={"video_id": video_id})


class YtLearnModule:
    name = "ytlearn"
    title = "Learning from YouTube videos"
    requires_env = ()
    optional_env = ("YOUTUBE_LEARN", "YOUTUBE_LEARN_HOUR",
                    "YOUTUBE_LEARN_OWNER_PER_DAY", "YOUTUBE_LEARN_CHANNEL_PER_DAY")
    setup_note = ("Off until YOUTUBE_LEARN=on. Needs the youtube-transcript-api "
                  "package in the image; without it every read is UNKNOWN.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple:
        return ()

    def actions(self) -> tuple[Action, ...]:
        return (
            Action(ACTION, Tier.ASK,
                   "Save a video's drafted lessons as approved knowledge for the "
                   "owner to read (one database row; no prompt, rule, code, "
                   "setting or agent skill is changed)",
                   run=save_video_notes, dry_run=save_dry, params=dict(PARAMS),
                   max_per_hour=10, card_only=True, verbose_card=True,
                   vet=_vet, card_detail=_card_detail),
        )


MODULE = YtLearnModule()
