"""T10: `python -m atlas.cli shop-build` builds the ATLAS Academy storefront
into `build_root()/<stamp>`, leaves it there, points `current` at it, and
prints the path and the time its DB reads were made. It never writes under
the publish root (AGENCY_PUBLISH_DIR): nothing it makes is public."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from atlas import cli
from atlas.modules.academy import publish, site
from atlas.modules.agency import hosting


@pytest.fixture
def roots(monkeypatch, tmp_path):
    pub = tmp_path / "sites"
    pub.mkdir()
    build = tmp_path / "build"
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", pub)
    monkeypatch.setenv(publish.BUILD_ENV, str(build))

    async def videos():
        return [site.Video("claude", 0, "abcdefghijk",
                           datetime(2026, 10, 20, 12, tzinfo=UTC))]

    async def nothing():
        return []
    monkeypatch.setattr(publish, "public_videos", videos)
    monkeypatch.setattr(publish, "_approved_reviews", nothing)
    monkeypatch.setattr(publish, "published_lessons", nothing)
    monkeypatch.setattr(publish, "_fetch_thumb", lambda vid: None)   # no network
    return pub, build


def _files(root):
    return sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink())


async def test_shop_build_writes_a_complete_site_and_points_current_at_it(
        roots, capsys):
    pub, build = roots
    await cli.shop_build()
    out = capsys.readouterr().out
    current = build / "current"
    assert current.is_symlink()
    target = os.readlink(current)
    assert "/" not in target and hosting.STAMP_RE.fullmatch(target), target
    stamp_dir = build / target
    assert (stamp_dir / "academy" / "index.html").is_file()
    assert (stamp_dir / "academy" / "catalog.json").is_file()
    assert "ATLAS Academy" in (stamp_dir / "academy" / "index.html").read_text()
    assert str(stamp_dir) in out
    assert "read at" in out and "UTC" in out
    assert _files(pub) == []                      # nothing under the publish root


async def test_a_second_build_moves_current_and_keeps_the_first(roots, capsys):
    _pub, build = roots
    await cli.shop_build()
    first = os.readlink(build / "current")
    await cli.shop_build()
    second = os.readlink(build / "current")
    assert first != second
    assert (build / first / "academy" / "index.html").is_file()
    assert (build / second / "academy" / "index.html").is_file()


async def test_shop_build_refuses_when_the_videos_table_is_unreadable(
        roots, monkeypatch, capsys):
    _pub, build = roots

    async def unreadable():
        return None
    monkeypatch.setattr(publish, "public_videos", unreadable)
    with pytest.raises(SystemExit) as stop:
        await cli.shop_build()
    assert stop.value.code == 1
    assert publish.VIDEOS_UNREADABLE in capsys.readouterr().out
    assert not (build / "current").exists()


async def test_shop_build_refuses_a_build_root_inside_the_publish_root(
        roots, monkeypatch, capsys):
    pub, _build = roots
    monkeypatch.setenv(publish.BUILD_ENV, str(pub / "inside"))
    with pytest.raises(SystemExit):
        await cli.shop_build()
    assert "overlaps" in capsys.readouterr().out
    assert _files(pub) == []


def test_shop_build_is_a_registered_command():
    assert cli.COMMANDS["shop-build"] is cli.shop_build
