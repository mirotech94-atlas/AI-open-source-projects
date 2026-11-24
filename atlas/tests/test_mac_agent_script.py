"""The agent on Miro's Mac (bootstrap/mac/mac_agent.py), without a Mac.

`subprocess.run` is replaced, so nothing here opens, sends or runs anything.
What is asserted is the argv each operation would hand to macOS: a value
from the server is only ever an argument after `--`, never part of an
AppleScript `-e` line, never a flag, and a Shortcut not on the local
allowlist is never run.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bootstrap/mac/mac_agent.py"


def _load():
    spec = importlib.util.spec_from_file_location("mac_agent_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def agent(monkeypatch, tmp_path):
    m = _load()
    apps = tmp_path / "Applications"
    for name in ("Notes", "Safari", "Terminal", "YouTube Music"):
        (apps / f"{name}.app").mkdir(parents=True)
    monkeypatch.setattr(m, "APP_DIRS", [apps])
    monkeypatch.setattr(m, "SHORTCUTS_ALLOW", tmp_path / "shortcuts-allow.txt")
    monkeypatch.setattr(m.time, "sleep", lambda _s: None)
    calls: list[list[str]] = []
    replies: dict[str, str] = {}

    def fake_run(cmd, *a, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        out = ""
        joined = " ".join(cmd)
        for fragment, reply in replies.items():
            if fragment in joined:
                out = reply
                break
        return subprocess.CompletedProcess(cmd, 0, out if kw.get("text") else
                                           out.encode(), "" if kw.get("text") else b"")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    m.calls, m.replies = calls, replies
    return m


def _osa_calls(m) -> list[list[str]]:
    return [c for c in m.calls if c[0] == "osascript"]


def _assert_values_only_after_dashdash(m, *values: str) -> None:
    """Every osascript call: the script is `-e` constants, then `--`, then
    the values. No value appears before `--`."""
    for cmd in _osa_calls(m):
        assert "--" in cmd, cmd
        cut = cmd.index("--")
        script = cmd[1:cut]
        assert all(script[i] == "-e" for i in range(0, len(script), 2)), cmd
        for v in values:
            assert not any(v in line for line in script[1::2]), (v, cmd)


def test_the_agent_imports_without_its_token(agent):
    """The token is read per request, not at import: nothing reads the
    real secret just by loading the script."""
    assert callable(agent.token)
    assert not hasattr(agent, "TOKEN")


def test_the_agent_runs_only_its_named_operations(agent):
    assert set(agent.OPS) == {
        "open_app", "quit_app", "music_search", "music_control", "add_reminder",
        "complete_reminder", "resolve_recipient", "send_imessage", "open_url",
        "shortcut_allowed", "run_shortcut", "get_volume", "set_volume", "notify",
        "new_note"}


INJECTIONS = [
    '"; do shell script "touch /tmp/pwned"; "',
    '" & (do shell script "id") & "',
    "-e", "--", "-l JavaScript",
    "end tell\ndo shell script \"id\"",
]


@pytest.mark.parametrize("evil", INJECTIONS)
def test_a_reminder_title_is_an_argument_never_script(agent, evil):
    agent.op_add_reminder({"title": evil})
    _assert_values_only_after_dashdash(agent, evil)
    cmd = _osa_calls(agent)[-1]
    assert cmd[cmd.index("--") + 1] == evil


@pytest.mark.parametrize("evil", INJECTIONS)
def test_a_message_text_is_an_argument_never_script(agent, evil):
    ok, _detail, _ = agent.op_send_imessage({"to": "+15551234567", "text": evil})
    assert ok
    _assert_values_only_after_dashdash(agent, evil)
    cmd = _osa_calls(agent)[-1]
    assert cmd[cmd.index("--") + 1:] == ["+15551234567", evil]


@pytest.mark.parametrize("evil", INJECTIONS)
def test_a_notification_is_an_argument_never_script(agent, evil):
    ok, _d, _ = agent.op_notify({"title": evil, "body": evil})
    assert ok
    _assert_values_only_after_dashdash(agent, evil)


def test_a_contact_name_is_looked_up_as_an_argument(agent):
    agent.replies["Contacts"] = "count\t1\nphone\t+1 (555) 123-4567\n"
    handle, label = agent.resolve("Jean-Luc O'Brien")
    assert handle == "+1 (555) 123-4567" and "Jean-Luc O'Brien" in label
    _assert_values_only_after_dashdash(agent, "Jean-Luc O'Brien")


@pytest.mark.parametrize("to", ['Mum" & (do shell script "id") & "', "Mum; id",
                                "-e", "tel:+1555", "", "5551234567",
                                "+15551234567\n", "bob@example.com\n", "Mum\n"])
def test_a_recipient_that_is_not_a_handle_or_a_plain_name_sends_nothing(agent, to):
    ok, _d, _ = agent.op_send_imessage({"to": to, "text": "hi"})
    assert not ok and _osa_calls(agent) == []


@pytest.mark.parametrize("reply,ok", [
    ("count\t0\n", False),
    ("count\t2\nphone\t+15550000001\nphone\t+15550000002\n", False),
    ("count\t1\nphone\t+15550000001\nphone\t+15550000002\n", False),
    ("count\t1\nemail\ta@x.com\nemail\tb@x.com\n", False),
    ("count\t1\nphone\t+15550000001\nemail\ta@x.com\n", True),
    ("count\t1\nemail\ta@x.com\n", True),
])
def test_a_name_must_be_one_person_with_one_handle(agent, reply, ok):
    agent.replies["Contacts"] = reply
    handle, _label = agent.resolve("Sam")
    assert (handle is not None) is ok


def test_an_ambiguous_name_is_refused_at_send_time_too(agent):
    agent.replies["Contacts"] = "count\t2\n"
    ok, detail, _ = agent.op_send_imessage({"to": "Sam", "text": "hi"})
    assert not ok and "2 people" in detail
    assert not any("Messages" in " ".join(c) for c in agent.calls)


@pytest.mark.parametrize("text", ["", "x" * 1001, "a\x00b", "pay \u202eevil"])
def test_a_bad_message_text_sends_nothing(agent, text):
    ok, _d, _ = agent.op_send_imessage({"to": "+15551234567", "text": text})
    assert not ok and agent.calls == []


def test_an_app_name_is_an_argument_when_quitting(agent):
    agent.replies["is running"] = "true"
    agent.op_quit_app({"app": "Notes"})
    _assert_values_only_after_dashdash(agent, "Notes")
    assert any(c[c.index("--") + 1:] == ["Notes"] for c in _osa_calls(agent)
               if "tell application (item 1 of argv) to quit" in c)


@pytest.mark.parametrize("app", ["Finder", "loginwindow", "Dock", "System Events",
                                 "Terminal", "../Notes", "/Applications/Notes.app",
                                 'Notes" to do shell script "id'])
def test_the_desktop_system_and_denied_apps_are_never_quit(agent, app):
    ok, _d, _ = agent.op_quit_app({"app": app})
    assert not ok and _osa_calls(agent) == []


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "ftp://x.com", "-a Calculator",
    "https://u:p@x.com", "https://x.com/\nx", " https://x.com", "https:x.com",
    "x-apple.systempreferences:", "https://x.com\\@y.io", "",
    "https://x.com/<!here>", 'https://x.com/"a"',
])
def test_only_a_plain_http_url_reaches_open(agent, url):
    ok, _d, _ = agent.op_open_url({"url": url})
    assert not ok and agent.calls == []


def test_a_url_is_the_one_argument_to_open(agent):
    url = "https://www.youtube.com/watch?v=x&list=y;id"
    ok, _d, _ = agent.op_open_url({"url": url})
    assert ok and agent.calls == [["open", url]]


def _allow(agent, *lines: str) -> None:
    agent.SHORTCUTS_ALLOW.write_text("\n".join(lines) + "\n")


def _ran_shortcut(agent) -> bool:
    return any(c[:2] == ["shortcuts", "run"] for c in agent.calls)


def test_no_allowlist_file_runs_no_shortcut(agent):
    ok, detail, _ = agent.op_run_shortcut({"name": "Morning"})
    assert not ok and "shortcuts-allow.txt" in detail and not _ran_shortcut(agent)


def test_an_empty_allowlist_runs_no_shortcut(agent):
    _allow(agent, "", "# nothing yet")
    ok, _d, _ = agent.op_run_shortcut({"name": "Morning"})
    assert not ok and not _ran_shortcut(agent)


@pytest.mark.parametrize("name", ["morning", "Morning ", "Morn", "# Morning",
                                  "Evening"])
def test_only_an_exact_listed_name_runs(agent, name):
    _allow(agent, "Morning", "# Evening")
    agent.replies["shortcuts list"] = "Morning\nEvening\n"
    ok, _d, _ = agent.op_run_shortcut({"name": name})
    assert not ok and not _ran_shortcut(agent)


def test_a_listed_shortcut_runs_by_name_after_dashdash(agent):
    _allow(agent, "# mine", "Morning", "Log water")
    agent.replies["shortcuts list"] = "Morning\nLog water\n"
    ok, _d, _ = agent.op_run_shortcut({"name": "Log water"})
    assert ok
    assert ["shortcuts", "run", "--", "Log water"] in agent.calls
    assert not any("--input-path" in c or "-i" in c for c in agent.calls)


def test_a_listed_name_that_is_not_a_shortcut_does_not_run(agent):
    _allow(agent, "Morning")
    agent.replies["shortcuts list"] = "Evening\n"
    ok, _d, _ = agent.op_run_shortcut({"name": "Morning"})
    assert not ok and not _ran_shortcut(agent)


@pytest.mark.parametrize("name", ["-i", "--input-path=/etc/passwd", " Morning",
                                  "a\nb"])
def test_a_name_that_could_be_an_option_is_refused_even_if_listed(agent, name):
    _allow(agent, name.strip() or "x", name)
    ok, _d, _ = agent.op_run_shortcut({"name": name})
    assert not ok and not _ran_shortcut(agent)


def test_the_allowlist_check_learns_nothing_but_yes_or_no(agent):
    _allow(agent, "Morning", "Secret thing")
    agent.replies["shortcuts list"] = "Morning\nSecret thing\n"
    ok, detail, token = agent.op_shortcut_allowed({"name": "Morning"})
    assert ok and "Secret" not in detail and token is None
    ok, detail, _ = agent.op_shortcut_allowed({"name": "Evening"})
    assert not ok and "Secret" not in detail


def test_set_volume_records_the_level_it_replaced(agent):
    agent.replies["get volume settings"] = "19"
    ok, _d, token = agent.op_set_volume({"level": 60})
    assert ok and json.loads(token) == {"previous": 19}
    set_cmd = _osa_calls(agent)[-1]
    assert set_cmd[set_cmd.index("--") + 1:] == ["60"]


@pytest.mark.parametrize("level", [True, 101, -1, "60; do shell script", 5.5, None])
def test_set_volume_takes_only_a_whole_number(agent, level):
    ok, _d, _ = agent.op_set_volume({"level": level})
    assert not ok and _osa_calls(agent) == []


def test_an_unreadable_volume_changes_nothing(agent):
    agent.replies["get volume settings"] = "missing value"
    ok, _d, _ = agent.op_set_volume({"level": 60})
    assert not ok
    assert not any("set volume" in " ".join(c) for c in agent.calls)


# ---- quit: the token that lets the undo reopen it (2026-10-06)

def test_a_quit_that_closed_the_app_says_so_in_its_token(agent, monkeypatch):
    states = iter([True, False])
    monkeypatch.setattr(agent, "running", lambda _name: next(states))
    ok, _d, token = agent.op_quit_app({"app": "Notes"})
    assert ok and json.loads(token) == {"app": "Notes", "quit_by_us": True}


def test_a_quit_of_an_app_that_was_not_open_has_no_token(agent, monkeypatch):
    monkeypatch.setattr(agent, "running", lambda _name: False)
    ok, detail, token = agent.op_quit_app({"app": "Notes"})
    assert ok and "wasn't open" in detail and token is None


# ---- Notes: a new note (2026-10-06)

def test_new_note_is_an_operation(agent):
    assert agent.OPS["new_note"] is agent.op_new_note


@pytest.mark.parametrize("evil", INJECTIONS)
def test_a_note_title_and_text_are_one_argument_never_script(agent, evil):
    ok, _d, _ = agent.op_new_note({"title": evil.replace("\n", " "), "body": evil})
    assert ok
    calls = _osa_calls(agent)
    assert len(calls) == 1
    cmd = calls[0]
    # The script is the fixed handler; the note is the one value after `--`.
    assert cmd[1:cmd.index("--")][1::2] == list(agent.NOTE_SCRIPT)
    assert len(cmd[cmd.index("--") + 1:]) == 1


def test_a_note_is_escaped_html_with_the_title_first(agent):
    agent.replies["Notes"] = "x-coredata://abc/ICNote/p1"
    ok, detail, token = agent.op_new_note({"title": "hello <b>&", "body": "a\n\nb"})
    assert ok and "'hello <b>&'" in detail
    sent = _osa_calls(agent)[0][-1]
    assert sent == ("<div><h1>hello &lt;b&gt;&amp;</h1></div>"
                    "<div>a</div><div><br></div><div>b</div>")
    assert json.loads(token) == {"id": "x-coredata://abc/ICNote/p1",
                                 "title": "hello <b>&"}


def test_a_title_only_note_has_no_empty_line_under_it(agent):
    agent.op_new_note({"title": "hello", "body": ""})
    assert _osa_calls(agent)[0][-1] == "<div><h1>hello</h1></div>"


@pytest.mark.parametrize("title,body", [
    ("", ""), ("  ", "\n"), ("x" * 101, ""), ("a", "x" * 2001), ("a\nb", ""),
    ("a\x00", ""), ("ok", "pay \u202eevil"), (None, "x"), ("x", None), (5, ""),
])
def test_a_bad_note_creates_nothing(agent, title, body):
    ok, _d, _ = agent.op_new_note({"title": title, "body": body})
    assert not ok and agent.calls == []


@pytest.mark.parametrize("stderr,says", [
    ("execution error: Not authorized to send Apple events to Notes. (-1743)",
     "allow the ATLAS agent to control Notes"),
    ("execution error: Notes got an error: Can't get default account. (-1728)",
     "Notes refused the new note: execution error: Notes got an error"),
    ("", "Notes refused the new note: no reason given"),
])
def test_a_notes_failure_is_said_as_what_it_was(agent, monkeypatch, stderr, says):
    """Only a real Automation denial is reported as the permission prompt."""
    def refuse(cmd, *a, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", stderr)
    monkeypatch.setattr(agent.subprocess, "run", refuse)
    ok, detail, token = agent.op_new_note({"title": "hello", "body": ""})
    assert not ok and says in detail and token is None


def test_the_install_script_updates_the_running_copy_and_restarts_it():
    """bootstrap/mac/install-agent.sh: the copy launchd runs is
    ~/.config/atlas/mac_agent.py (the plist says so), and it is restarted
    with kickstart. It never touches the token."""
    script = (SCRIPT.parent / "install-agent.sh").read_text()
    plist = (SCRIPT.parent / "com.atlas.mac-agent.plist").read_text()
    assert "__HOME__/.config/atlas/mac_agent.py" in plist
    assert 'dest="$config/mac_agent.py"' in script
    assert 'config="$HOME/.config/atlas"' in script
    assert 'launchctl kickstart -k "$domain/$label"' in script
    assert "label=com.atlas.mac-agent" in script
    code = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    # Its only mention is a size test and a note that it is missing.
    token_lines = [ln for ln in code if "mac-agent-token" in ln]
    assert token_lines and all(
        not any(w in ln for w in ("cat ", "< ", "read ", "$(<", "cp ", "install "))
        for ln in token_lines)


@pytest.mark.parametrize("app", ["Photo Booth", "FaceTime", "facetime"])
def test_the_camera_apps_are_never_opened(agent, tmp_path, app):
    (agent.APP_DIRS[0] / "Photo Booth.app").mkdir()
    (agent.APP_DIRS[0] / "FaceTime.app").mkdir()
    ok, _d, _ = agent.op_open_app({"app": app})
    assert not ok and agent.calls == []


def test_there_is_no_typing_and_no_camera_operation(agent):
    """No free typing into whatever app is in front (it could be Terminal),
    and no camera: neither exists to be asked for."""
    assert not any(k in op for op in agent.OPS
                   for k in ("type", "keystroke", "camera", "photo", "booth"))
