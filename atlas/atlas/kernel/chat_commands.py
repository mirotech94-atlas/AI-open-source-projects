"""The fixed owner chat commands, parsed by regex with no model.

"call me" sends the one Tier 1 call through the executor (`owner_calls`); a "fix X"
request raises one pending Tier 2 card (`dev_requests`); "learn <youtube url>" only
queues a video (`video_learn`); "remember / forget / restore / correct ..." and "what do
you know about ..." keep or hide the owner's own notes (`owner_memory`, Tier 1 through the
executor, forget hides and never deletes). None decides a card.
Kept out of `api/whatsapp.py` so that module stays free of write paths.
"""
from __future__ import annotations

import logging

from . import dev_requests, owner_calls, owner_memory, video_learn, wa_format

log = logging.getLogger("atlas.chat_commands")


async def run(body: str, message_sid: str) -> str | None:
    cat = wa_format.Category
    try:
        # The tag for each fixed command is set here, in code, before it runs.
        if owner_calls.is_call_request(body):
            wa_format.set_reply_category(cat.SYSTEM)
            return await owner_calls.handle_call_request(message_sid)
        if video_learn.is_learn_request(body):
            wa_format.set_reply_category(cat.CONTENT)
            return await video_learn.queue(body)
        if owner_memory.is_memory_command(body):
            wa_format.set_reply_category(cat.GENERAL)
            return await owner_memory.handle(body)
        if dev_requests.is_dev_request(body):
            wa_format.set_reply_category(cat.APPROVALS)
            return await dev_requests.note(body)
    except Exception as exc:                                # noqa: BLE001
        log.warning("owner command failed: %s", type(exc).__name__)
        return "I couldn't do that just now. Nothing was changed."
    return None
