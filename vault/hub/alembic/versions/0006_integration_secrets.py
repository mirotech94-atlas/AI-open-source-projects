"""integration.secrets_enc + move the plaintext Home Assistant token into it

Task 41 fix round 1, ruling (a) / finding 1: `Integration.secret` holds
exactly one credential (`fields[0]`), so Home Assistant's long-lived REST
token was declared a plain `kind="text"` field and landed in the
**plaintext** `integration.config` JSON column -- and `_card` echoed it back
to the client verbatim. A long-lived HA token is full control of the user's
home, so every `kind="secret"` field is now encrypted at rest:
`secrets_enc` is a single Fernet blob (`crypto.encrypt_json`) holding a
`{field_name: plaintext}` dict for every secret field beyond `fields[0]`.

The data step moves any already-stored plaintext `config["ha_token"]` into
that blob and deletes it from `config`, so upgrading an existing hub does
not leave the token sitting in the clear waiting for the admin to re-save
the integration. Guarded: a row whose token cannot be re-encrypted (a
`GUARDIAN_SECRET_KEY` that has since been rotated is not a problem here --
`encrypt` never needs the old key -- but a hand-edited non-string value
is) is left with an empty blob rather than failing the whole migration.

Revision ID: 0006_integration_secrets
Revises: 0005_alert_escalated_at
Create Date: 2026-09-10

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006_integration_secrets'
down_revision: Union[str, Sequence[str], None] = '0005_alert_escalated_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Field names that used to be stored plaintext in `config` and are now
# `kind="secret"`. Only Home Assistant's REST token was ever shipped that
# way; PagerDuty's signing secret and Telegram's webhook secret are new in
# the same fix round and have no legacy plaintext form.
_MOVED = ("ha_token",)


def upgrade() -> None:
    """Upgrade schema."""
    # NOT NULL with a server default, so a migrated DB matches the model's
    # `Mapped[str]` rather than differing from a `create_all` one.
    op.add_column('integration', sa.Column('secrets_enc', sa.Text(), nullable=False, server_default=''))

    # `env.py` already imports guardian_hub, so the same Fernet key the app
    # uses is available here -- no separate key material to configure.
    from guardian_hub import crypto

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, config FROM integration")).fetchall()
    for row_id, config_raw in rows:
        try:
            cfg = json.loads(config_raw) if isinstance(config_raw, str) else (config_raw or {})
        except Exception:
            continue
        if not isinstance(cfg, dict):
            continue
        moved = {}
        for name in _MOVED:
            val = cfg.get(name)
            if isinstance(val, str) and val:
                moved[name] = val
        if not moved:
            continue
        for name in moved:
            cfg.pop(name, None)
        try:
            blob = crypto.encrypt_json(moved)
        except Exception:
            blob = ""
        conn.execute(sa.text("UPDATE integration SET config = :cfg, secrets_enc = :blob WHERE id = :id"),
                      {"cfg": json.dumps(cfg), "blob": blob, "id": row_id})


def downgrade() -> None:
    """Downgrade schema.

    One-way for the data: the plaintext token is NOT written back into
    `config` on the way down -- putting a credential back in the clear is
    not something a schema rollback should do silently. Re-save the
    integration after a downgrade.
    """
    with op.batch_alter_table('integration') as batch:
        batch.drop_column('secrets_enc')
