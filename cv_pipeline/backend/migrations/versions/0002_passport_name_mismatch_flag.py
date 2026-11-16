"""Flag type for a CV attached by passport number whose name clearly differs

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f('ck_review_flags_type'), 'review_flags', type_='check')
    op.create_check_constraint(
        op.f('ck_review_flags_type'), 'review_flags',
        "type in ('possible_duplicate', 'edit_replaced', 'passport_name_mismatch')")


def downgrade() -> None:
    op.execute("DELETE FROM review_flags WHERE type = 'passport_name_mismatch'")
    op.drop_constraint(op.f('ck_review_flags_type'), 'review_flags', type_='check')
    op.create_check_constraint(op.f('ck_review_flags_type'), 'review_flags',
                               "type in ('possible_duplicate', 'edit_replaced')")
