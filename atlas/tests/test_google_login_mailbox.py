"""bootstrap/google_login.py's mailbox mode (ATLAS_GOOGLE_MAILBOX=CLIPS).

the owner, 2026-10-11: Porkbun forwarded the support, alerts and owner addresses to
contact14@example.com. Connecting that Gmail must add a MAIL_<KEY>_*
account on the SHARED client, Gmail only, without touching the GOOGLE_*
token, and append the key to MAIL_ACCOUNTS rather than replace the list.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(monkeypatch, **env):
    for k in ("ATLAS_GOOGLE_MAILBOX", "ATLAS_GOOGLE_YOUTUBE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("google_login_t",
                                                  ROOT / "bootstrap" / "google_login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mailbox_mode_asks_for_gmail_and_the_address_only(monkeypatch):
    gl = _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="clips")
    assert gl.MAILBOX_KEY == "CLIPS"
    assert set(gl.SCOPES.split()) == {"https://www.googleapis.com/auth/gmail.modify",
                                      "https://www.googleapis.com/auth/userinfo.email"}
    plain = _load(monkeypatch)
    assert "calendar.events" in plain.SCOPES and plain.MAILBOX_KEY == ""


def test_mailbox_lines_add_the_account_and_append_it_to_mail_accounts(monkeypatch):
    gl = _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="CLIPS")
    lines = gl.mailbox_lines("CLIPS", "contact14@example.com", "1//tok",
                             "exampleco,owner,flycheap,guardian")
    assert lines == ["MAIL_CLIPS_KIND=google", "MAIL_CLIPS_EMAIL=contact14@example.com",
                     "MAIL_CLIPS_REFRESH_TOKEN=1//tok",
                     "MAIL_ACCOUNTS=exampleco,owner,flycheap,guardian,clips"]
    assert not any(x.startswith("GOOGLE_") for x in lines)   # the shared token is kept
    assert gl.merged_accounts("owner, Clips", "CLIPS") == "owner,clips"   # once
    assert gl.merged_accounts("", "clips") == "clips"


def test_mailbox_mode_reads_the_shared_client_from_the_server(monkeypatch):
    gl = _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="CLIPS")
    asked = []

    def server_value(host, name):
        asked.append(name)
        return ""                       # unreadable: stop before any consent
    monkeypatch.setattr(gl, "_server_value", server_value)
    monkeypatch.setattr(sys, "argv", ["google_login.py"])
    with pytest.raises(SystemExit) as stop:
        gl.main()
    assert asked == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    assert "GOOGLE_CLIENT_ID/SECRET" in str(stop.value)


def test_a_bad_mailbox_name_or_both_modes_are_refused(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["google_login.py"])
    gl = _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="clips; rm -rf /")
    with pytest.raises(SystemExit, match="short name"):
        gl.main()
    gl = _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="CLIPS", ATLAS_GOOGLE_YOUTUBE="CLIPS")
    with pytest.raises(SystemExit, match="not both"):
        gl.main()


def test_youtube_mode_asks_for_upload_playlists_comments_and_analytics(monkeypatch):
    """Final review I3: the growth log reads YouTube Analytics, so a channel
    token must be granted yt-analytics.readonly alongside the other three."""
    gl = _load(monkeypatch, ATLAS_GOOGLE_YOUTUBE="CLIPS")
    g = "https://www.googleapis.com/auth/"
    assert set(gl.SCOPES.split()) == {g + "youtube.upload", g + "youtube",
                                      g + "youtube.force-ssl",
                                      g + "yt-analytics.readonly",
                                      g + "userinfo.email"}
