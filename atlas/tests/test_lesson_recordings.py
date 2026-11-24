"""Part D5: lesson recordings manifest, segment classifier and scrub check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.lesson_recordings import record as rec


def seg(heading: str = "", html: str = "<p>x</p>", kind: str = "body") -> dict:
    return {"kind": kind, "heading": heading, "html": html, "say": "x"}


# ---------------------------------------------------------------- classifier
def test_pre_block_is_a_command_segment() -> None:
    assert rec.classify(seg(html="<pre>uv init hello</pre>"), "n8n") == "command"


def test_inline_code_alone_is_not_a_command() -> None:
    assert rec.classify(seg(html="<p>press <code>Enter</code></p>"), "n8n") == "none"


def test_node_walkthrough_heading_counts() -> None:
    assert rec.classify(seg(heading="Node 2: the HTTP Request"), "n8n") == "walkthrough"


def test_title_and_plain_body_are_none() -> None:
    assert rec.classify(seg(kind="title", heading="Lesson 1.1"), "n8n") == "none"
    assert rec.classify(seg(heading="The idea"), "n8n") == "none"


def test_claude_screen_walkthrough_is_simulated_not_real() -> None:
    s = seg(heading="Walk-through: where each setting lives")
    assert rec.classify(s, "adults") == "simulated"
    assert rec.classify(s, "seniors") == "simulated"


def test_pre_in_a_claude_course_is_still_simulated() -> None:
    assert rec.classify(seg(html="<pre>Settings</pre>"), "seniors") == "simulated"


# --------------------------------------------------------------------- scrub
@pytest.mark.parametrize(
    "text",
    [
        "/Users/mac/projects/x",
        "/home/miro/.ssh",
        "contact miro@gmail.com",
        "key sk-ant-abc123def456ghi789",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "AKIAABCDEFGHIJKLMNOP",
        "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
    ],
)
def test_scrub_flags_real_looking_data(text: str) -> None:
    assert rec.scrub_hits(text)


def test_scrub_allows_scratch_home_and_fake_data() -> None:
    assert rec.scrub_hits("~/hello/server.py") == []
    assert rec.scrub_hits("demo@example.test") == []
    assert rec.scrub_hits("owner@demo.invalid") == []


# ------------------------------------------------------------------ manifest
def clip(tmp: Path, name: str = "a.mp4", **over) -> dict:
    p = tmp / name
    p.write_bytes(b"x")
    c = {
        "course": "n8n",
        "lesson": "1.2",
        "segment": 3,
        "path": str(p),
        "focus": [[0.5, 10, 20, 300, 80]],
        "recorded_at": "2026-11-14T10:00:00Z",
        "tool": "playwright",
    }
    c.update(over)
    return c


def test_manifest_shape_and_simulated_separate(tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    sim = {"course": "adults", "lesson": "3.4", "segment": 4, "reason": "claude screen"}
    rec.write_manifest(out, [clip(tmp_path)], [sim])
    data = json.loads(out.read_text())
    assert list(data["clips"][0]) == [
        "course", "lesson", "segment", "path", "focus", "recorded_at", "tool"]
    assert data["simulated"] == [sim]
    assert all(c["path"] for c in data["clips"])


def test_manifest_refuses_missing_file(tmp_path: Path) -> None:
    c = clip(tmp_path, path=str(tmp_path / "nope.mp4"))
    with pytest.raises(rec.ManifestError, match="missing"):
        rec.write_manifest(tmp_path / "m.json", [c], [])


def test_manifest_refuses_bad_focus(tmp_path: Path) -> None:
    with pytest.raises(rec.ManifestError, match="focus"):
        rec.write_manifest(tmp_path / "m.json", [clip(tmp_path, focus=[[1, 2, 3]])], [])


def test_manifest_refuses_a_clip_marked_simulated_tool(tmp_path: Path) -> None:
    with pytest.raises(rec.ManifestError, match="tool"):
        rec.write_manifest(tmp_path / "m.json", [clip(tmp_path, tool="")], [])


def test_manifest_refuses_scrub_hit_in_any_field(tmp_path: Path) -> None:
    c = clip(tmp_path, tool="/Users/mac/x")
    with pytest.raises(rec.ManifestError, match="scrub"):
        rec.write_manifest(tmp_path / "m.json", [c], [])


def test_manifest_write_is_atomic_and_replaces(tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    out.write_text("old")
    rec.write_manifest(out, [clip(tmp_path)], [])
    assert json.loads(out.read_text())["clips"]
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------- plan
def test_plan_reads_segments_and_marks_simulated(tmp_path: Path) -> None:
    d = tmp_path / "lesson-video-adults" / "3.4"
    d.mkdir(parents=True)
    (d / "segments.json").write_text(json.dumps([
        seg(kind="title", heading="Lesson 3.4"),
        seg(heading="Walk-through: where each setting lives"),
    ]))
    real, sim = rec.plan(tmp_path, {"adults": "lesson-video-adults"})
    assert real == []
    assert sim == [{"course": "adults", "lesson": "3.4", "segment": 1,
                    "reason": "Claude screens are animated diagrams, no login"}]


def test_plan_real_segments_for_n8n(tmp_path: Path) -> None:
    d = tmp_path / "lesson-video" / "1.2"
    d.mkdir(parents=True)
    (d / "segments.json").write_text(json.dumps([seg(html="<pre>docker run n8n</pre>")]))
    real, sim = rec.plan(tmp_path, {"n8n": "lesson-video"})
    assert real == [{"course": "n8n", "lesson": "1.2", "segment": 0, "kind": "command"}]
    assert sim == []


# ----------------------------------------------------------------- recording
def test_record_is_blocked_without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec, "playwright_available", lambda: False)
    with pytest.raises(rec.Blocked, match="playwright"):
        rec.require_recorder()


def test_main_plan_prints_counts(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    d = tmp_path / "lesson-video" / "1.1"
    d.mkdir(parents=True)
    (d / "segments.json").write_text(json.dumps([seg(html="<pre>x</pre>")]))
    code = rec.main(["--plan", "--data", str(tmp_path), "--course", "n8n=lesson-video"])
    assert code == 0
    assert "real 1" in capsys.readouterr().out


def test_main_record_without_recorder_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rec, "playwright_available", lambda: False)
    assert rec.main(["--record", "--data", str(tmp_path), "--out", str(tmp_path)]) == 2
