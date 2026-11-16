"""The client's fields: gender, nationality, passport expiry and type, and the dated work history

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

COLUMNS = ("gender", "nationality", "passport_expiry", "passport_type")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column('candidates', sa.Column(name, sa.Text(), nullable=True))
    op.add_column('candidates', sa.Column('work_history', postgresql.JSONB(astext_type=sa.Text()),
                                          server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column('candidates', 'work_history')
    for name in reversed(COLUMNS):
        op.drop_column('candidates', name)
