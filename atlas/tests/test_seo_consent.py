"""The Google consent grants Search Console (read only) by default.

bootstrap/google_login.py's default mode (Gmail + Calendar) also asks for
`webmasters.readonly`, behind ATLAS_GOOGLE_SEARCH_CONSOLE (on unless 0/off),
so one run grants it. `--reconsent` reuses the client ATLAS already has, so
an old refresh token without the scope is replaced by one command. The SEO
check names that command. Mailbox and YouTube modes are untouched.
subprocess.run is monkeypatched: nothing reaches ssh, docker or Google."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WM = "https://www.googleapis.com/auth/webmasters.readonly"
KEEP = ("https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/userinfo.email")


def _load(monkeypatch, **env):
    for k in ("ATLAS_GOOGLE_MAILBOX", "ATLAS_GOOGLE_YOUTUBE", "ATLAS_SSH_HOST",
              "ATLAS_ENV_PATH", "ATLAS_COMPOSE_DIR", "ATLAS_GOOGLE_SEARCH_CONSOLE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("google_login_seo_t",
                                                  ROOT / "bootstrap" / "google_login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_scopes_add_search_console_and_keep_the_rest(monkeypatch):
    mod = _load(monkeypatch)
    scopes = mod.SCOPES.split()
    assert WM in scopes
    for s in KEEP:
        assert s in scopes


@pytest.mark.parametrize("off", ["0", "off", "false", "no"])
def test_search_console_can_be_left_out(monkeypatch, off):
    mod = _load(monkeypatch, ATLAS_GOOGLE_SEARCH_CONSOLE=off)
    scopes = mod.SCOPES.split()
    assert WM not in scopes
    for s in KEEP:
        assert s in scopes


def test_pure_scope_function():
    spec = importlib.util.spec_from_file_location("gl_pure",
                                                  ROOT / "bootstrap" / "google_login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert WM in mod.default_scopes(True).split()
    assert WM not in mod.default_scopes(False).split()


def test_mailbox_and_youtube_modes_do_not_get_search_console(monkeypatch):
    assert WM not in _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="CLIPS").SCOPES
    assert WM not in _load(monkeypatch, ATLAS_GOOGLE_YOUTUBE="CLIPS").SCOPES


def test_reconsent_reuses_the_client_from_the_env(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    mod = _load(monkeypatch, ATLAS_SSH_HOST="local", ATLAS_ENV_PATH=str(env))
    asked: list[str] = []

    def fake_value(host, name):
        asked.append(name)
        return {"GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
                "GOOGLE_CLIENT_SECRET": "fake-not-a-secret"}[name]

    monkeypatch.setattr(mod, "_server_value", fake_value)
    monkeypatch.setattr(mod, "_port_free", lambda: False)   # stop before consent
    monkeypatch.setattr(sys, "argv", ["google_login.py", "--reconsent"])
    with pytest.raises(SystemExit) as stop:
        mod.main()
    assert "port" in str(stop.value)
    assert asked == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]


def test_reconsent_without_a_client_says_so(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    mod = _load(monkeypatch, ATLAS_SSH_HOST="local", ATLAS_ENV_PATH=str(env))
    monkeypatch.setattr(mod, "_server_value", lambda h, n: "")
    monkeypatch.setattr(sys, "argv", ["google_login.py", "--reconsent"])
    with pytest.raises(SystemExit) as stop:
        mod.main()
    assert "GOOGLE_CLIENT_ID" in str(stop.value)


def test_the_shell_wrapper_passes_reconsent_and_documents_it():
    sh = (ROOT / "bootstrap" / "google-login.sh").read_text()
    assert "--reconsent" in sh
    assert "Search Console" in sh


def test_the_seo_check_names_the_exact_rerun_command():
    from atlas.modules.seo import gsc
    assert "./bootstrap/google-login.sh --reconsent" in gsc.NO_SCOPE
    assert "webmasters.readonly" in gsc.NO_SCOPE


def test_the_shell_wrapper_runs(monkeypatch):
    # bash -n: the wrapper parses.
    r = subprocess.run(["bash", "-n", str(ROOT / "bootstrap" / "google-login.sh")],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
