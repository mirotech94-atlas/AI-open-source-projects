"""`dismiss_alert`: the "delete/clear the inbox alert" chat tool.

The bug it fixes: a site went down, Vault raised (and then auto-cleared)
the alert when the site recovered, and "delete inbox alert" answered a bare
"nothing to delete" without saying the alert had already gone away on its own.
This tool clears a named alert, and when the inbox is already empty it names
what cleared itself recently instead.
"""
import datetime as dt

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import registry


def ctx_for(role="admin", username="demoadmin"):
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    u = db.query(m.User).filter_by(username=username).one()
    return registry.ToolContext(db=db, tenant=t, user=u, role=role, request_ip="127.0.0.1")


def _dismiss(ctx, **kw):
    return registry.TOOLS["dismiss_alert"].handler(ctx, **kw)


def _add_alert(ctx, key, msg, status="open", resolved_by="", last_seen=None, sev="CRIT"):
    now = dt.datetime.now(dt.timezone.utc)
    ls = last_seen or now.isoformat(timespec="seconds")
    a = m.Alert(tenant_id=ctx.tenant.id, key=key, sev=sev, msg=msg,
                first_seen=ls, last_seen=ls, status=status, resolved_by=resolved_by)
    ctx.db.add(a)
    ctx.db.commit()
    return a


def _clear_alerts(ctx):
    ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id).delete()
    ctx.db.commit()


def test_dismiss_a_named_open_alert_resolves_it(client):
    seeded_demo(client)
    ctx = ctx_for()
    _clear_alerts(ctx)
    _add_alert(ctx, "site:FlyCheap:down", "FlyCheap DOWN — http 502")
    r = _dismiss(ctx, key="site:FlyCheap:down")
    assert r.ok
    row = ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, key="site:FlyCheap:down").one()
    assert row.status == "resolved"


def test_dismiss_a_key_that_already_cleared_says_so(client):
    seeded_demo(client)
    ctx = ctx_for()
    _clear_alerts(ctx)
    _add_alert(ctx, "site:FlyCheap:down", "FlyCheap DOWN — http 502",
               status="resolved", resolved_by="auto-cleared")
    r = _dismiss(ctx, key="site:FlyCheap:down")
    assert r.ok
    assert "already" in r.text.lower()
    assert "nothing to delete" in r.text.lower()
    assert "FlyCheap" in r.text


def test_empty_inbox_names_what_cleared_itself_recently(client):
    seeded_demo(client)
    ctx = ctx_for()
    _clear_alerts(ctx)
    # This is the reported scenario: nothing open, but FlyCheap's outage
    # auto-cleared minutes ago.
    _add_alert(ctx, "site:FlyCheap:down", "FlyCheap DOWN — http 502",
               status="resolved", resolved_by="auto-cleared")
    r = _dismiss(ctx)  # no key: "delete the inbox alert"
    assert r.ok
    assert "FlyCheap" in r.text
    assert "on its own" in r.text.lower()
    assert r.data.get("recently_cleared") == ["site:FlyCheap:down"]


def test_empty_inbox_ignores_alerts_that_cleared_long_ago(client):
    seeded_demo(client)
    ctx = ctx_for()
    _clear_alerts(ctx)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat(timespec="seconds")
    _add_alert(ctx, "site:Old:down", "Old thing DOWN", status="resolved",
               resolved_by="auto-cleared", last_seen=old)
    r = _dismiss(ctx)
    assert r.ok
    assert "nothing to delete" in r.text.lower()
    assert "Old thing" not in r.text


def test_open_alerts_are_listed_not_silently_resolved(client):
    seeded_demo(client)
    ctx = ctx_for()
    _clear_alerts(ctx)
    _add_alert(ctx, "site:A:down", "A DOWN")
    _add_alert(ctx, "site:B:down", "B DOWN")
    r = _dismiss(ctx)  # no key, several open
    assert r.ok
    assert "2 open" in r.text
    # Nothing was resolved without a named target.
    assert ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, status="resolved").count() == 0
