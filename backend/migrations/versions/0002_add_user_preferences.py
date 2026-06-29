"""add user preferences

Adds the ``users.preferences`` JSONB column for per-user client state
(Fleet Cam truck row ordering, etc.).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-29 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add preferences JSONB column with empty-object default."""
    op.add_column(
        "users",
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Drop the preferences column."""
    op.drop_column("users", "preferences")
