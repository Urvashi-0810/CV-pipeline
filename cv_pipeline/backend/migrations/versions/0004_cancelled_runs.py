"""Pipeline runs can end as cancelled (stopped with the Cancel button)

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f('ck_pipeline_runs_status'), 'pipeline_runs', type_='check')
    op.create_check_constraint(op.f('ck_pipeline_runs_status'), 'pipeline_runs',
                               "status in ('running', 'done', 'cancelled', 'error')")


def downgrade() -> None:
    op.execute("UPDATE pipeline_runs SET status = 'done' WHERE status = 'cancelled'")
    op.drop_constraint(op.f('ck_pipeline_runs_status'), 'pipeline_runs', type_='check')
    op.create_check_constraint(op.f('ck_pipeline_runs_status'), 'pipeline_runs',
                               "status in ('running', 'done', 'error')")
