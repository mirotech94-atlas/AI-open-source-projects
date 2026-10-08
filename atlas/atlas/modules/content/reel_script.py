"""A reel's script as a `story_script` draft body, and back.

The daily drafting step (`content.draft_reel`) writes a body in this shape,
so the renderer never has to guess which line is narration and which is the
caption:

    FORMAT: cliffhanger "Part 1" story
    SCENES: 4
    CHARACTERS: an old lighthouse keeper, grey beard, yellow oilskin coat
    NARRATION (Fish Audio, British voice):
    1. The lighthouse keeper had not seen a ship in forty years.
    2. ...
    CAPTION: Nobody told him the sea was gone.

    Made with AI

`parse()` also reads an older free-form `story_script` (script lines, a
caption line, then the label), so a draft written by `content.draft` still
renders: every line before the caption is a scene.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .drafts import AI_LABEL

VOICE_MARK = "NARRATION (Fish Audio, British voice):"
# Miro, 2026-09-27: characters talk to each other on camera; no narrator.
# A beat is one or two "Name: line" exchanges, spoken by Veo, lip-synced.
DIALOGUE_MARK = "DIALOGUE (characters speak on camera, Veo voices them):"
DEFAULT_MAX_SCENES = 6
_NUMBERED = re.compile(r"^\s*\d+[.)]\s*")
_PART_OF = re.compile(r"\bPart (\d+) of (\d+)\b", re.IGNORECASE)
# A written series' beat carries its whole scene -- with the line in the
# language it is spoken -- as [[...]], after the speaker and English caption.
_SCENE = re.compile(r"\s*\[\[(.*?)\]\]", re.DOTALL)


def scene_of(beat: str) -> str | None:
    found = _SCENE.search(beat or "")
    return found.group(1).strip() if found else None
# Between two lines of one beat: a '|' or the start of the next "Name:".
# A new speaker starts after a sentence ends, so "Silver Sedan:" stays one name.
_SPLIT = re.compile(r"\s*\|\s*|(?<=[.!?\u061f])\s+(?=[^\s:.!?|][^:.!?|]{0,40}:\s)")


@dataclass(frozen=True, slots=True)
class Script:
    format: str
    scenes: tuple[str, ...]
    caption: str            # always ends with AI_LABEL
    characters: str = ""
    dialogue: bool = False  # characters speak; no narrator, no Fish voice
    # The course lane's store read, verbatim from its PRICE line (course.py
    # composes and checks it); "" on every other reel.
    price: str = ""

    def character_sheet(self) -> str:
        """Built once per story and put at the head of every beat's video
        prompt, so the same people look the same in every 8 s clip."""
        who = self.characters.strip() or "the same characters throughout"
        return (f"Character sheet (identical in every shot): {who}. "
                f"Same look, clothes and setting in every shot.")

    @property
    def scripted(self) -> bool:
        """Written in full: every beat carries its scene and an English line."""
        return bool(self.scenes) and all(scene_of(b) for b in self.scenes)

    @property
    def end_card(self) -> str:
        """The card over the last second and a half: 'PART N' on a series
        episode, 'THE END' on its finale, 'PART 1' on a cliffhanger, or ""."""
        found = _PART_OF.search(self.format or "")
        if found:
            n, total = int(found.group(1)), int(found.group(2))
            return "THE END" if n >= total else f"PART {n}"
        return "PART 1" if self.part_one else ""

    @property
    def part_one(self) -> bool:
        """A cliffhanger ends on a PART 1 card."""
        name = self.format.lower()
        return "cliffhanger" in name or "part 1" in name

    def exchanges(self) -> list[list[tuple[str, str]]]:
        """Each beat's dialogue as (speaker, line) pairs: lines split on
        '|' or a new 'Name:'; a line with no speaker keeps an empty one."""
        out = []
        for beat in self.scenes:
            pairs = []
            for piece in _SPLIT.split(_SCENE.sub("", beat)):
                piece = piece.strip()
                if not piece:
                    continue
                name, sep, line = piece.partition(":")
                if sep and 0 < len(name) <= 40 and line.strip():
                    pairs.append((name.strip(), line.strip()))
                else:
                    pairs.append(("", piece))
            out.append(pairs)
        return out

    @property
    def narration(self) -> str:
        return " ".join(self.scenes)


def compose(fmt: str, scenes: list[str], caption: str,
            characters: str = "", *, dialogue: bool = True, price: str = "") -> str:
    lines = [f"FORMAT: {fmt}", f"SCENES: {len(scenes)}"]
    if price.strip():
        lines.append(f"PRICE: {' '.join(price.split())}")
    if characters.strip():
        lines.append(f"CHARACTERS: {' '.join(characters.split())}")
    lines.append(DIALOGUE_MARK if dialogue else VOICE_MARK)
    lines += [f"{i}. {s}" for i, s in enumerate(scenes, 1)]
    cap = caption.strip()
    if cap.endswith(AI_LABEL):
        cap = cap[: -len(AI_LABEL)].rstrip()
    lines.append(f"CAPTION: {cap}")
    return "\n".join(lines) + f"\n\n{AI_LABEL}"


def _labelled(caption: str) -> str:
    caption = caption.strip()
    if caption.endswith(AI_LABEL):
        return caption
    return f"{caption}\n\n{AI_LABEL}".strip()


def parse(body: str, *, max_scenes: int = DEFAULT_MAX_SCENES) -> Script:
    lines = [ln.strip() for ln in (body or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if lines and lines[-1] == AI_LABEL:
        lines = lines[:-1]
    fmt, scenes, caption, in_narration, who, price = "", [], "", False, "", ""
    structured = any(ln in (VOICE_MARK, DIALOGUE_MARK) for ln in lines)
    dialogue = any(ln == DIALOGUE_MARK for ln in lines)
    if structured:
        for ln in lines:
            if ln.startswith("FORMAT:"):
                fmt = ln.split(":", 1)[1].strip()
            elif ln.startswith("SCENES:"):
                continue
            elif ln.startswith("CHARACTERS:"):
                who = ln.split(":", 1)[1].strip()
            elif ln.startswith("PRICE:") and not in_narration:
                price = ln.split(":", 1)[1].strip()
            elif ln in (VOICE_MARK, DIALOGUE_MARK):
                in_narration = True
            elif ln.startswith("CAPTION:"):
                caption = ln.split(":", 1)[1].strip()
                in_narration = False
            elif in_narration:
                scenes.append(_NUMBERED.sub("", ln))
    else:
        if len(lines) > 1:
            caption, scenes = lines[-1], lines[:-1]
        else:
            scenes = lines
    scenes = [s for s in scenes if s][:max(1, max_scenes)]
    return Script(fmt, tuple(scenes), _labelled(caption), who, dialogue, price)
