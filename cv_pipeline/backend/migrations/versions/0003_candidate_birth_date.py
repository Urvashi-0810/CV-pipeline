"""Candidate birth date, so the age is worked out from it when read and never goes stale

Revision ID: 0003
Revises: 0002
"""
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('candidates', sa.Column('birth_date', sa.Date(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, date_of_birth FROM candidates WHERE date_of_birth IS NOT NULL"))
    for candidate_id, text in rows.fetchall():
        try:
            born = date.fromisoformat(text.strip()[:10])
        except ValueError:
            continue
        connection.execute(sa.text("UPDATE candidates SET birth_date = :born WHERE id = :id"),
                           {"born": born, "id": candidate_id})


def downgrade() -> None:
    op.drop_column('candidates', 'birth_date')
