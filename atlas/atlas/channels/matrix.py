"""Matrix — Miro already runs a homeserver for matrix.example.com, so this is free,
self-hosted and private. Buttons are rendered as a numbered reply list.

Two things here are deliberate rather than stylistic:

* **PUT with a transaction id**, not POST. The spec's send endpoint is
  `PUT .../send/{eventType}/{txnId}`; the txn id makes a retry idempotent.
  A CRITICAL page is exactly the message most likely to be retried, and
  exactly the one you least want duplicated in the room.
* **The room id is percent-encoded.** Room ids look like `!abc:matrix.example.com`, which
  is a path segment carrying reserved characters.
"""
from __future__ import annotations

import html
import os
import time
from urllib.parse import quote

import httpx

from ..kernel.comms import Outbound, register


def _env(name: str) -> str:
    # Read per call: register() runs at import, so __init__ would freeze
    # whatever the environment held before .env was loaded.
    return os.getenv(name, "").strip()


class MatrixChannel:
    name = "matrix"
    rich = False
    interactive = True

    @property
    def base(self) -> str:
        return _env("MATRIX_HOMESERVER").rstrip("/")

    @property
    def token(self) -> str:
        return _env("MATRIX_TOKEN")

    @property
    def room(self) -> str:
        return _env("MATRIX_ALERT_ROOM")

    async def available(self) -> bool:
        return bool(self.base.startswith(("http://", "https://"))
                    and self.token and self.room)

    async def send(self, msg: Outbound) -> str | None:
        lines = [f"{msg.title}", "", msg.body]
        if msg.buttons:
            lines += [""] + [f"- {b.value} — {b.label}" for b in msg.buttons]
        body = "\n".join(lines)

        formatted = (f"<b>{html.escape(msg.title)}</b><br/><br/>"
                     + html.escape(msg.body).replace("\n", "<br/>"))
        if msg.buttons:
            formatted += "<ul>" + "".join(
                f"<li><code>{html.escape(b.value)}</code> — "
                f"{html.escape(b.label)}</li>" for b in msg.buttons) + "</ul>"

        txn = f"atlas-{int(time.time() * 1000)}"
        url = (f"{self.base}/_matrix/client/v3/rooms/"
               f"{quote(self.room, safe='')}/send/m.room.message/{txn}")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.put(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                json={"msgtype": "m.text", "body": body,
                      "format": "org.matrix.custom.html",
                      "formatted_body": formatted},
            )
            r.raise_for_status()
            return r.json().get("event_id")


register(MatrixChannel())
