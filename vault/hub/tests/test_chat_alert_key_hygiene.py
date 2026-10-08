"""An alert key is an identifier, and nothing about it may depend on which
hyphen the model typed.

The transcript that made this test: the owner opened the conversation about
`email:dmarc-none:example.org`, said "don't show this incident again", and the bot
-- having re-typed the key with a NON-BREAKING hyphen (U+2011) -- looked it
up, found nothing, and answered that no such alert existed while it sat open
in front of them. It then offered to create a "custom suppression rule" that
Vault does not have. Two faults: the lookup, and the invented capability.
The lookup is fixed here; the capability is fixed in the prompt and in the
snooze tool's own description.
"""
import datetime as dt

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import registry
from guardian_hub.chat.tools import clean_text, resolve_alert_key


def ctx_for(subjects=()):
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    u = db.query(m.User).filter_by(username="demoadmin").one()
    return registry.ToolContext(db=db, tenant=t, user=u, role="admin", request_ip="127.0.0.1",
                                subjects=tuple(subjects))


def _alert(ctx, key, msg, status="open"):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id).delete()
    a = m.Alert(tenant_id=ctx.tenant.id, key=key, sev="INFO", msg=msg, first_seen=now, last_seen=now, status=status)
    ctx.db.add(a); ctx.db.commit()
    return a


def test_every_unicode_dash_becomes_a_hyphen():
    assert clean_text("email:dmarc‑none:example.org") == "email:dmarc-none:example.org"
    assert clean_text("email:dmarc–none:example.org") == "email:dmarc-none:example.org"
    assert clean_text(" aa:bb:cc​:dd ") == "aa:bb:cc:dd"
    assert clean_text("host one") == "host one"


def test_a_key_retyped_with_a_unicode_hyphen_still_finds_the_alert(client):
    seeded_demo(client)
    ctx = ctx_for()
    _alert(ctx, "email:dmarc-none:example.org", "example.org: DMARC policy is p=none (monitor only)")
    a = resolve_alert_key(ctx, "email:dmarc‑none:example.org")
    assert a is not None and a.key == "email:dmarc-none:example.org"


def test_this_means_the_alert_the_conversation_is_about(client):
    seeded_demo(client)
    ctx = ctx_for(subjects=["email:dmarc-none:example.org"])
    _alert(ctx, "email:dmarc-none:example.org", "example.org: DMARC policy is p=none (monitor only)")
    for word in ("this", "it", "this incident", ""):
        a = resolve_alert_key(ctx, word)
        assert a is not None and a.key == "email:dmarc-none:example.org", word


def test_a_unique_fragment_resolves_and_an_ambiguous_one_does_not(client):
    seeded_demo(client)
    ctx = ctx_for()
    _alert(ctx, "email:dmarc-none:example.org", "example.org: DMARC policy is p=none (monitor only)")
    assert resolve_alert_key(ctx, "example.org").key == "email:dmarc-none:example.org"
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ctx.db.add(m.Alert(tenant_id=ctx.tenant.id, key="email:spf-missing:example.org", sev="INFO",
                       msg="example.org: no SPF record", first_seen=now, last_seen=now, status="open"))
    ctx.db.commit()
    assert resolve_alert_key(ctx, "example.org") is None, "two open alerts match; never guess between them"


def test_dont_show_this_again_snoozes_the_subject_alert_through_dispatch(client):
    """End to end through `dispatch`, which is where the cleaning happens:
    the model passes 'this' and a month, and the alert leaves the inbox for
    a month rather than being declared non-existent."""
    seeded_demo(client)
    ctx = ctx_for(subjects=["email:dmarc-none:example.org"])
    _alert(ctx, "email:dmarc-none:example.org", "example.org: DMARC policy is p=none (monitor only)")
    out = registry.dispatch(ctx, "snooze_alert", {"key": "this", "hours": 720})
    assert getattr(out, "ok", False), getattr(out, "text", out)
    assert "30 days" in out.text
    row = ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, key="email:dmarc-none:example.org").one()
    assert row.status == "snoozed"


def test_the_prompt_forbids_invented_rules_and_names_the_real_tool():
    from guardian_hub.chat import prompt
    assert "NO custom rules" in prompt.SYSTEM
    assert "snooze_alert" in prompt.SYSTEM and "720 hours" in prompt.SYSTEM


def test_make_it_stop_is_an_action_and_reaches_the_snooze_tool():
    """The live transcript's second fault. "Don't show this incident again"
    has no repair verb in it, so the turn was offered read-only tools and
    the bot answered that no tool could hide the alert."""
    from guardian_hub.routers.chat import _tools_for_turn
    names = {t.name for t in _tools_for_turn("admin", "dont show this incident again")}
    assert {"snooze_alert", "dismiss_alert"} <= names
    names = {t.name for t in _tools_for_turn("admin", "stop nagging me about example.org")}
    assert "snooze_alert" in names


def test_naming_a_tool_without_calling_it_is_a_broken_promise():
    """"Next: dismiss_alert." reads as a plan and runs nothing."""
    from guardian_hub.routers.chat import _promises_a_change
    assert _promises_a_change("No open tool can hide the alert; I can dismiss it instead. Next: dismiss_alert.", [])
    assert not _promises_a_change("Nothing is open on example.org. Next: nothing to do.", [])
    assert not _promises_a_change("Snoozed it for 30 days. Next: it comes back if still true.", ["snooze_alert"])

