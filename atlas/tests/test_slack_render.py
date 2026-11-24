"""What a Slack message actually looks like: the payload, not the intent.

From the 2026-09-27 review: every reply showed its first 150 characters
twice (a header built from the body, then the body); **bold** rendered as
literal asterisks; long card bodies were cut at 2,900 characters; nothing
said whether a message wanted a decision or was only information.
"""
from __future__ import annotations

from atlas.channels import slack as s
from atlas.kernel.comms import Button, Outbound
from atlas.kernel.contracts import Severity

CH = s.SlackChannel()


def _texts(blocks):
    out = []
    for b in blocks:
        t = b.get("text")
        if isinstance(t, dict):
            out.append(t.get("text", ""))
        for el in b.get("elements") or []:
            if isinstance(el, dict) and el.get("type") == "mrkdwn":
                out.append(el.get("text", ""))
    return out


def test_a_reply_is_shown_once_not_as_header_and_body():
    body = ("You have **9 servers** running — the cloudco.instances check reports "
            "9 of 9 running (read 23:22).\n\nAnything else?")
    msg = Outbound(title=body[:150], body=body, kind="receipt", topic="decisions")
    blocks = CH._blocks(msg)
    assert [b["type"] for b in blocks] == ["section"]
    joined = "\n".join(_texts(blocks))
    assert joined.count("9 servers") == 1
    assert "*9 servers*" in joined and "**" not in joined
    assert CH.headline(msg).startswith("You have *9 servers* running")


def test_a_card_says_what_the_reader_must_do_and_for_which_project():
    msg = Outbound(title="#180 — Post a rendered reel", body="Caption: x",
                   topic="instagram_stories", presentation="REVIEW",
                   buttons=(Button("Approve", "approve:180"),))
    blocks = CH._blocks(msg)
    assert blocks[0]["text"]["text"] == "[Reels] REVIEW: #180 — Post a rendered reel"


def test_the_type_is_derived_when_not_given():
    action = Outbound(title="#9 — File an issue", body="b", topic="gitlab",
                      buttons=(Button("Approve", "approve:9"),))
    alert = Outbound(title="guardian.hub_api is failing", body="b",
                     topic="guardian", severity=Severity.HIGH,
                     buttons=(Button("Acknowledge", "ack:guardian.hub_api"),))
    brief = Outbound(title="Morning brief — Mon 28 Sep", body="b", topic="brief",
                     kind="digest")
    assert CH.headline(action) == "[GitLab] ACTION: #9 — File an issue"
    assert CH.headline(alert) == "[Guardian] INCIDENT: guardian.hub_api is failing"
    assert CH.headline(brief) == "[Atlas] INFO: Morning brief — Mon 28 Sep"


def test_a_long_body_is_split_never_cut():
    caption = "\n".join(f"line {i}: " + "x" * 90 for i in range(80))   # ~8 KB
    msg = Outbound(title="#1 — Publish", body=caption, topic="flycheap",
                   buttons=(Button("Approve", "approve:1"),))
    blocks = CH._blocks(msg)
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) >= 3
    assert all(len(b["text"]["text"]) <= s.SECTION_LIMIT for b in sections)
    assert "".join(b["text"]["text"] for b in sections).replace("\n", "") == \
        caption.replace("\n", "")
    assert blocks[-1]["type"] == "actions", "the buttons still come last"


def test_text_cannot_ping_the_whole_channel():
    msg = Outbound(title="t", body="a buyer wrote <!channel> and <!here|here>",
                   kind="receipt")
    joined = "\n".join(_texts(CH._blocks(msg)))
    assert "<!channel>" not in joined and "<!here" not in joined
    assert "@channel" in joined and "@here" in joined


def test_headings_become_bold_lines():
    assert s.slack_text("### Status\nall fine") == "*Status*\nall fine"


async def test_the_notification_text_matches_the_header(monkeypatch):
    import httpx
    sent: list[dict] = []

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json)
            return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    monkeypatch.setattr(httpx, "AsyncClient", C)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(s.SlackChannel, "channel_for", lambda self, topic: "C1")
    await CH.send(Outbound(title="cloudco.balance is failing", body="b",
                           topic="cloudco", severity=Severity.HIGH))
    assert sent[-1]["text"] == "[CloudCo] INCIDENT: cloudco.balance is failing"
    assert sent[-1]["blocks"][0]["text"]["text"] == sent[-1]["text"]


def test_a_receipt_whose_title_carries_the_result_keeps_it():
    """Per-platform results live in the title: '#180 — instagram: published'."""
    msg = Outbound(title="#180 — instagram: published", body="permalink https://x/p/1",
                   kind="receipt", topic="instagram_stories")
    blocks = CH._blocks(msg)
    joined = "\n".join(_texts(blocks))
    assert [b["type"] for b in blocks] == ["section"]
    assert joined.startswith("*#180 — instagram: published*\npermalink")
    assert CH.headline(msg) == "#180 — instagram: published"


def test_a_long_header_is_cut_at_a_word_with_an_ellipsis():
    title = ("#180 — Post a rendered AI-stories reel to the stories Instagram "
             "account, the Atlas Stories Facebook Page and TikTok (via Postiz), "
             "then reshare it to the main account's story")
    head = CH.headline(Outbound(title=title, body="b", topic="instagram_stories",
                                presentation="REVIEW"))
    assert len(head) <= 150 and head.endswith("…")
    assert not head[:-1].endswith(" ") and head.startswith("[Reels] REVIEW: #180")


def test_a_greeting_is_never_the_notification_line():
    body = "Hey the owner,\n\nYou have *9 servers* running (read 00:37)."
    msg = Outbound(title=body[:150], body=body, kind="receipt")
    assert CH.headline(msg) == "You have *9 servers* running (read 00:37)."


def test_the_conversation_prompt_forbids_greetings_and_filler():
    from atlas.api.slack_events import CONVERSE
    assert "no greeting" in CONVERSE and "anything else?" in CONVERSE
