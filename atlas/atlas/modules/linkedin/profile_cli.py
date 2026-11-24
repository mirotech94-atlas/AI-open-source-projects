"""Draft helper and approval-card script for the profile lane.

    python -m atlas.modules.linkedin.profile_cli add P1 --text "..." | --file post.txt
    python -m atlas.modules.linkedin.profile_cli card P1

`add` writes a `draft` entry into `profile_page.post_bank` of the LinkedIn
state file; it never approves or posts anything. `card` raises the Tier 2
`linkedin.approve_draft` card for it (text hash included, so an edited draft
cannot ride on an old card). His tap approves; only then does the daily
`linkedin.publish_post` card exist, and its tap is the post (invariants 1, 3, 5).
Both need LINKEDIN_PROFILE_ENABLED=on in the environment they run in.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from . import PAGES, PROFILE_ENV
from .publish import leftover_label, payload_sha256
from .store import file_lock, has_banned_link, load, save, stamp, today

PAGE = "profile"
BANK_KEY = "profile_page"


def _refusal_if_off() -> str | None:
    if PAGE not in PAGES:
        return f"the profile lane is off; set {PROFILE_ENV}=on"
    return None


def _holder(state: dict[str, Any]) -> dict[str, Any]:
    holder = state.setdefault(BANK_KEY, {})
    holder.setdefault("posting", {"enabled": True, "max_per_day": 1})
    holder.setdefault("post_bank", [])
    return holder


async def add_draft(entry_id: str, text: str) -> str:
    """Add a text draft. Returns "" on success, else why it was refused."""
    if (why := _refusal_if_off()) is not None:
        return why
    entry_id, text = entry_id.strip(), text.strip()
    if not entry_id or not text:
        return "an id and the post text are both needed"
    if has_banned_link(text):
        return "the text carries a banned link"
    if (left := leftover_label(text)):
        return f"the text still carries the drafting label {left!r}"
    async with file_lock():
        try:
            state = load()
        except FileNotFoundError:
            state = {}
        bank = _holder(state)["post_bank"]
        if any(str(e.get("id")) == entry_id for e in bank):
            return f"{entry_id} already exists in the profile bank"
        bank.append({"id": entry_id, "status": "draft", "kind": "text",
                     "text": text, "created": today(), "created_at": stamp(),
                     "source": "miro"})
        save(state)
    return ""


async def raise_card(entry_id: str) -> str:
    """Raise the approve card for a draft. Returns "" on success."""
    if (why := _refusal_if_off()) is not None:
        return why
    try:
        state = load()
    except (FileNotFoundError, ValueError) as exc:
        return f"cannot read the LinkedIn state: {exc}"
    entry = next((e for e in (state.get(BANK_KEY) or {}).get("post_bank") or []
                  if str(e.get("id")) == entry_id), None)
    if entry is None:
        return f"no entry {entry_id} in the profile bank"
    if entry.get("status") != "draft":
        return f"{entry_id} is {entry.get('status')}, not a draft"
    from ...kernel.approvals import propose
    from ...kernel.registry import REGISTRY
    REGISTRY.load_all()
    params = {"page": PAGE, "entry_id": entry_id,
              "text_sha256": payload_sha256(entry)}
    try:
        await propose(
            "linkedin.approve_draft", params,
            f"Approve {entry_id} for your profile. It posts only after the "
            f"next card.\n\n{str(entry.get('text'))[:600]}",
            evidence={"page": PAGE, "entry": entry_id},
            dedup_key=f"linkedin.approve_draft@profile:{entry_id}")
    except Exception as exc:  # noqa: BLE001
        return f"card not raised: {type(exc).__name__}: {exc}"
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("entry_id")
    src = add.add_mutually_exclusive_group(required=True)
    src.add_argument("--text")
    src.add_argument("--file")
    sub.add_parser("card").add_argument("entry_id")
    args = ap.parse_args(argv)
    if args.cmd == "add":
        text = args.text if args.text is not None else Path(args.file).read_text()
        why = asyncio.run(add_draft(args.entry_id, text))
    else:
        why = asyncio.run(raise_card(args.entry_id))
    print(why or "ok")
    return 1 if why else 0


if __name__ == "__main__":
    sys.exit(main())
