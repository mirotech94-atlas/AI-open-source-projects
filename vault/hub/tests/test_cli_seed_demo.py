import pytest

from guardian_hub import cli, models as m
from guardian_hub.db import SessionLocal


def test_reset_refuses_a_non_demo_slug_without_force(client):
    # seed a real (non-demo) tenant first, so --reset has something to wipe
    db = SessionLocal()
    db.add(m.Tenant(slug="myhome", name="My Home")); db.commit(); db.close()

    with pytest.raises(SystemExit):
        cli.main(["seed-demo", "--slug", "myhome", "--reset"])


def test_reset_allows_demo_without_force(client):
    cli.main(["seed-demo", "--slug", "demo", "--reset"])   # must not raise


def test_reset_allows_a_non_demo_slug_with_force(client):
    db = SessionLocal()
    db.add(m.Tenant(slug="myhome2", name="My Home 2")); db.commit(); db.close()

    cli.main(["seed-demo", "--slug", "myhome2", "--reset", "--force"])   # must not raise
