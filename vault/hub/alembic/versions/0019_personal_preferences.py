"""User-owned presentation preferences."""
from alembic import op
import sqlalchemy as sa
revision = "0019_personal_preferences"
down_revision = "0018_queue_lifecycle"
branch_labels = depends_on = None

def upgrade():
    if "preferences" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}:
        op.add_column("user", sa.Column("preferences", sa.JSON(), nullable=True))
def downgrade():
    op.drop_column("user", "preferences")
