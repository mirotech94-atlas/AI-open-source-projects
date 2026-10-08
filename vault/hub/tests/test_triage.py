import datetime as dt

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import triage


def test_triage_shape_and_lead_ranks_above_the_rest(client):
    seeded_demo(client)
    body = client.get("/api/v1/inbox/triage").json()
    assert set(body) == {"lead", "rest", "outcomes", "total_open"}
    assert set(body["outcomes"]) == {"handled", "handled_period", "needs_yes", "needs_you"}

    if body["lead"] is None:
        return  # an unseeded/empty tenant carries no open items at all
    item_keys = {"kind", "id", "view", "sev", "title", "entity", "why", "since_text", "count",
                 "confidence", "cause", "rank_score", "rank_reason", "remedy", "actions",
                 "primary_label", "primary_min_role", "alerts", "recommendations"}
    assert set(body["lead"]) == item_keys
    # Every item names why it ranks where it does -- an unexplained score is
    # worse than no score (the hard constraint this feature is built on).
    assert body["lead"]["rank_reason"]
    for item in [body["lead"], *body["rest"]]:
        assert item["rank_reason"]
        assert item["count"] == len(item["alerts"]) if item["kind"] != "rec" else True
    scores = [body["lead"]["rank_score"], *[i["rank_score"] for i in body["rest"]]]
    assert scores == sorted(scores, reverse=True), "rest must not outrank the lead"


def test_rollup_never_hides_or_duplicates_an_alert(client):
    seeded_demo(client)
    triage_body = client.get("/api/v1/inbox/triage").json()
    alerts_body = client.get("/api/v1/inbox?view=alerts").json()

    all_open_ids = {r["id"] for r in alerts_body["rows"]}
    rolled_ids: list[str] = []
    for item in [triage_body["lead"], *triage_body["rest"]] if triage_body["lead"] else triage_body["rest"]:
        if item["kind"] == "rec":
            assert item["alerts"] == []
            continue
        rolled_ids.extend(a["id"] for a in item["alerts"])

    # Set equality catches a dropped alert; the length check on top of it
    # catches one counted twice under two different items.
    assert set(rolled_ids) == all_open_ids
    assert len(rolled_ids) == len(all_open_ids)


def test_a_shared_cause_collapses_into_one_group_row(client):
    """The demo fixture's SPF/DMARC findings (`email:spf:*`, `email:dmarc:*`,
    `email:dmarc-none:*` -- see sensor/tests/fixtures/state.json) share one
    cause under `remedy.cause_key` even across domains. Among the ones NOT
    already swept into an open `Incident` (entity correlation runs first and
    independently of cause -- two email findings on the same domain can
    already be one incident, which is also correct and covered by the
    roll-up test above), triage must show the rest as a single `group` item
    whose `alerts` lists every one of them -- never as separate top-level
    rows each still carrying the full write-controls."""
    seeded_demo(client)
    alerts = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    incidents = client.get("/api/v1/inbox?view=incidents").json()["rows"]
    in_incident = {c["id"] for inc in incidents for c in inc["children"]}

    email_alerts = [a for a in alerts if a["id"].startswith("email:") and a["id"] not in in_incident]
    if len(email_alerts) < 2:
        return  # nothing standalone to roll up in this fixture
    causes = {triage.remedy_mod.cause_key(a["id"]) for a in email_alerts}
    if len(causes) != 1:
        return  # fixture's domains don't all share one cause key; not this test's case

    body = client.get("/api/v1/inbox/triage").json()
    items = [body["lead"], *body["rest"]] if body["lead"] else body["rest"]
    matches = [i for i in items if {a["id"] for a in i["alerts"]} & {a["id"] for a in email_alerts}]
    assert len(matches) == 1, "every standalone email-cause alert should land in exactly one rolled-up item"
    group = matches[0]
    assert group["kind"] == "group"
    assert {a["id"] for a in group["alerts"]} == {a["id"] for a in email_alerts}
    assert group["count"] == len(email_alerts)


def test_outcomes_partition_every_open_alert_and_rec_exactly_once(client):
    seeded_demo(client)
    db = SessionLocal()
    tid = db.query(m.Tenant).first().id
    tenant = db.get(m.Tenant, tid)

    # The shared definition, not a hand-copied list: this test was the fifth
    # place that spelled the set out, and it broke the moment `acked` joined
    # it -- which is exactly the drift `SETTLED_ALERT_STATUSES` exists to stop.
    open_alerts = db.query(m.Alert).filter(m.Alert.tenant_id == tid,
                                           m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES)).all()
    recs_open = client.get("/api/v1/inbox?view=recs").json()["rows"]

    needs_you = 0
    needs_yes = 0
    for a in open_alerts:
        r = triage.remedy_mod.resolve(db, tenant, a, "admin")
        if r.kind == "human":
            needs_you += 1
        elif r.kind in ("auto", "approval"):
            needs_yes += 1
    unmatched_recs = [r for r in recs_open if r["id"] not in {a.key for a in open_alerts}]
    needs_yes += len(unmatched_recs)

    body = client.get("/api/v1/inbox/triage").json()
    assert body["outcomes"]["needs_you"] == needs_you
    assert body["outcomes"]["needs_yes"] == needs_yes
    # The partition covers every open alert plus every open rec exactly once
    # -- arithmetic alone would pass on two cancelling bugs, so this checks
    # both the total and that the total is the population triage claims.
    assert needs_you + needs_yes - len(unmatched_recs) == len(open_alerts)
    assert body["total_open"] == len(open_alerts) + len(unmatched_recs)


def test_handled_counts_only_autonomous_successful_fixes_in_the_window(client):
    # The demo fixture seeds its own 30-day history of real Fix rows -- this
    # asserts the *delta* four new rows make, not an absolute count, so the
    # test does not depend on how many autonomous fixes happen to already be
    # in this fixture's trailing window.
    tid = seeded_demo(client)
    before = client.get("/api/v1/inbox/triage").json()["outcomes"]["handled"]

    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc)

    def _fix(**kw):
        base = dict(tenant_id=tid, ts=now.isoformat(timespec="seconds"), host="h", action="a",
                    by="auto", ok=True, verified_at=now)
        base.update(kw)
        db.add(m.Fix(**base))

    _fix()                                    # counts: autonomous, ok, recent
    _fix(verified_at=None)                    # counts: verification is a stronger
                                              # claim than "handled", and only 5 of
                                              # 139 live fixes could ever earn it
    _fix(by="test")                           # counts: an unrecognised stamp is
                                              # Vault's own work, not an approval
    _fix(by="hub:miro")                       # a person clicked it -- not "without asking"
    _fix(by="accepted-recommendation")        # the owner accepted it -- also a yes
    _fix(ok=False)                            # failed -- not "handled"
    _fix(ts=(now - dt.timedelta(days=30)).isoformat(timespec="seconds"))  # outside the window
    db.commit()
    db.close()

    after = client.get("/api/v1/inbox/triage").json()["outcomes"]["handled"]
    assert after - before == 3


def test_since_phrase_turns_a_repeat_count_into_a_duration():
    now = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.timezone.utc)  # a Thursday
    tuesday = (now - dt.timedelta(days=2)).isoformat(timespec="seconds")  # Tue Sep 8
    old = (now - dt.timedelta(days=40)).isoformat(timespec="seconds")

    assert triage.since_phrase(tuesday, 184, now) == "since Tuesday"
    assert triage.since_phrase(tuesday, 1, now) == "since Tuesday"
    assert "Tuesday" not in triage.since_phrase(old, 12, now)
    assert triage.since_phrase("", 5, now) == "seen repeatedly"
    assert triage.since_phrase("not-a-date", 1, now) == "just seen"


def test_viewer_sees_every_verb_disabled_and_the_role_it_needs(client):
    # Exercised at the payload layer, the way `test_remedy.py` checks role
    # boundaries -- adding a real viewer *member* needs a superadmin, which
    # the demo tenant's own admin is not; `triage.build` takes a role string
    # directly, so no session/membership plumbing is needed to prove the
    # same rows come back stripped of write actions for a lesser role.
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)

    payload = triage.build(db, tenant, role="viewer")
    assert payload.lead is not None or payload.rest == []
    items = ([payload.lead] if payload.lead else []) + payload.rest
    for item in items:
        # Every action a viewer cannot run is tagged with the tier it needs,
        # so the UI renders it disabled and says which role. Stripping them
        # left a viewer looking at a row with none of the verbs everyone talks
        # about and no explanation, which is what F6 set out to fix.
        for a in item.actions:
            assert a.tier >= 2, (
                f"a viewer was offered {a.kind!r} at tier {a.tier}, which reads "
                "as runnable"
            )
        if item.primary_min_role:
            assert item.primary_min_role in ("operator", "admin")


def test_the_server_refuses_a_viewer_who_posts_a_triage_action_anyway(client):
    """The payload offering an action is a display decision. The refusal is not.

    `triage.build` hands a viewer every action tagged with the role it needs,
    so the UI can render each one disabled rather than silently removing it.
    Nothing about that weakens the boundary: every route those actions name
    sits behind `require_role("operator")` or `require_role("admin")` in
    `routers/write.py`. This test is the proof, because a display rule that is
    mistaken for a security rule is how a real hole gets shipped.
    """
    seeded_demo(client)
    r = client.get("/api/v1/inbox/triage")
    assert r.status_code == 200, r.text
    rows = ([r.json()["lead"]] if r.json().get("lead") else []) + r.json()["rest"]
    actions = [a for row in rows for a in row["actions"]]
    assert actions, "nothing to prove the boundary with"

    db = SessionLocal()
    for mem in db.query(m.Membership).all():
        mem.role = "viewer"
    db.commit()
    db.close()

    a = actions[0]
    refused = client.request(a["method"], a["path"], json=a["body"])
    assert refused.status_code == 403, (
        f"{a['method']} {a['path']} was not refused for a viewer: "
        f"{refused.status_code} {refused.text}"
    )
