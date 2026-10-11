"""VJ-001: the eight Milestone 1 domain tables."""
from pathlib import Path

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in ("reviews", "judgments", "evidence", "documents", "retrieval_steps",
                  "research_runs", "propositions", "submissions"):
        op.drop_table(table)

