"""Slack replies are short and errors are plain words.

`_format_reply` used to print one line per tool step with the raw error text
tacked on; now the steps collapse to a single line, an error is humanised, and
the Slack system prompt asks the model for three or four lines, not a page.
"""
import types

from guardian_hub import errfmt
from guardian_hub.routers import slackbot, chat


def test_humanize_maps_common_raw_errors():
    assert errfmt.humanize("curl: (7) Failed to connect to 127.0.0.1 port 8800: Connection refused") == "connection refused (nothing is listening)"
    assert errfmt.humanize("E: Could not get lock /var/lib/dpkg/lock-frontend").startswith("apt is busy")
    assert errfmt.humanize("HTTP Error 502: Bad Gateway").startswith("502 bad gateway")
    assert errfmt.humanize("ssh: connect to host x port 22: Operation timed out") == "timed out (no answer in time)"
    assert errfmt.humanize("") == ""


def _step(status, tool, args=None, result=""):
    return types.SimpleNamespace(status=status, tool=tool, args=args or {}, result=result)


def test_format_reply_collapses_steps_and_humanises_errors():
    resp = types.SimpleNamespace(text="FlyCheap is back online.", proposal=None, tool_steps=[
        _step("ok", "site_check", {"target": "flycheap"}),
        _step("ok", "resolve_alert", {"key": "site:flycheap:down"}),
        _step("error", "speedtest", {}, "ssh: connect to host x port 22: Connection refused"),
        _step("proposed", "reboot", {}),          # proposals are not steps
    ])
    out = slackbot._format_reply(resp)
    lines = out.split("\n")
    assert lines[0] == "FlyCheap is back online."
    assert "✓ site check flycheap · ✓ resolve alert site:flycheap:down" in out   # one line, not two
    assert "✗ speedtest — connection refused (nothing is listening)" in out    # humanised, not raw
    assert "Connection refused" not in out
    assert len(lines) <= 3


def test_format_reply_keeps_the_yes_no_line_for_a_proposal():
    resp = types.SimpleNamespace(text="I can reboot it.", proposal=object(), tool_steps=[])
    out = slackbot._format_reply(resp)
    assert out.endswith("Reply *yes* to run it, or *no* to drop it.")


def test_slack_gets_a_brevity_directive_the_web_does_not():
    assert "Be brief" in chat._SLACK_STYLE and "three or four short lines" in chat._SLACK_STYLE
