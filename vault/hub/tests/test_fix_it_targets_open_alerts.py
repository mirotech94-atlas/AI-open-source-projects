"""A bare "fix it" in Slack must be about what is actually OPEN, and the
rules fallback must never answer "Everything is up" while a WARN is open.

The 4:16 case: the owner said "fix it" under a slow-download WARN and got
"Everything is up. Next: nothing needed right now." Three things stacked:
a resolved FlyCheap alert was still in the "recent alerts" handed to the
engine (its DOWN text steered the rules fallback into the status branch),
the status branch matched "down" as a substring (so "Download" trips it
too), and its verdict ignored open alerts entirely.
"""
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import rules
from guardian_hub.integrations import slack as slack_mod
from guardian_hub.integrations.base import Message, SendContext

CH = "C0RECENT"


def _integ_ctx(monkeypatch):
    class _Res:
        ok = True
        error = "1.0"  # the ts slot on success
    monkeypatch.setattr(slack_mod, "bot_post", lambda *a, **k: _Res())
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test")
    slack_mod._RECENT.clear()
    return slack_mod.SlackIntegration(), SendContext(tenant=None, secret="", config={"channel_id": CH}, db=None, base_url="")


def test_a_resolve_forgets_the_alert_instead_of_adding_a_stale_line(monkeypatch):
    integ, ctx = _integ_ctx(monkeypatch)
    integ.send(ctx, Message(title="Guardian: CRIT alert", body="FlyCheap DOWN — http 502",
                            severity="CRIT", kind="alert", dedup_key="site:FlyCheap:down"))
    assert any("FlyCheap DOWN" in x for x in slack_mod.recent_alerts(CH))
    integ.send(ctx, Message(title="Guardian: alert resolved", body="FlyCheap DOWN — http 502",
                            severity="CRIT", kind="resolve", dedup_key="site:FlyCheap:down"))
    assert slack_mod.recent_alerts(CH) == []   # gone, and no "resolved" line either
    integ.send(ctx, Message(title="Guardian: WARN alert", body="Download 95.1 Mbps — well below usual 597.6 Mbps",
                            severity="WARN", kind="alert", dedup_key="speed:slow"))
    recent = slack_mod.recent_alerts(CH)
    assert len(recent) == 1 and "speed:slow" in recent[0]


def _open_warn(tid, key, msg):
    db = SessionLocal()
    try:
        db.query(m.Alert).filter_by(tenant_id=tid, key=key).delete()
        now = "2026-09-16T16:00:00-04:00"
        db.add(m.Alert(tenant_id=tid, key=key, sev="WARN", status="open", msg=msg, first_seen=now, last_seen=now))
        db.commit()
    finally:
        db.close()


def test_rules_fallback_never_says_everything_is_up_with_a_warn_open(client):
    tid = seeded_demo(client)
    _open_warn(tid, "speed:slow", "Download 95.1 Mbps — well below usual 597.6 Mbps")
    db = SessionLocal(); t = db.get(m.Tenant, tid)
    # Exactly the engine text the Slack "fix it" produces (no stale lines now).
    q = ("About the alert(s) you just sent me:\n[speed:slow] Guardian: WARN alert\n"
         "Download 95.1 Mbps — well below usual 597.6 Mbps\n\nfix it.")
    out = rules.answer(db, t, q, role="admin")
    assert "Everything is up" not in out
    assert "nothing needed" not in out.lower()
    # It must engage with the alert's subject (line speed), not describe a
    # generic status: the rules engine answers from its speed branch here.
    assert "Mbps" in out or "need a look" in out


def test_status_question_reports_open_alerts_not_just_down_hosts(client):
    tid = seeded_demo(client)
    _open_warn(tid, "speed:slow", "Download 95.1 Mbps — well below usual 597.6 Mbps")
    db = SessionLocal(); t = db.get(m.Tenant, tid)
    out = rules.answer(db, t, "is everything down?", role="admin")
    assert "Everything is up" not in out
    assert "need a look" in out
