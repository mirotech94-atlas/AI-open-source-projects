from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def test_a_decision_row_freezes_the_features_it_was_made_from(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.DecisionOutcome(
            tenant_id=tid, alert_key="pkg:prod:security", host="prod",
            action="security_updates",
            features={"host_history": 4, "held": 0},
            score=88, verdict="auto", chosen="auto"))
        db.commit()
        row = db.query(m.DecisionOutcome).filter_by(tenant_id=tid).one()
    finally:
        db.close()
    assert row.features["host_history"] == 4
    assert row.outcome == ""          # not known yet
    assert row.settled_at is None
    assert row.decided_at is not None
