"""positions come from the letter: salary column, positions refilled from each newest version

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.modules.letters.positions import positions_from_letter


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('demand_trades', sa.Column('salary', sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f('ck_demand_trades_salary_length'), 'demand_trades', "salary is null or length(salary) <= 200")
    # Positions can no longer be edited by hand, so the "edited by hand" mark goes.
    op.execute("UPDATE demand_letters SET manual_fields = array_remove(manual_fields, 'trades')")

    # Refill every letter's positions from its newest version's positions table (if it has one).
    bind = op.get_bind()
    latest = bind.execute(sa.text(
        "SELECT DISTINCT ON (demand_id) demand_id, content FROM letter_versions "
        "ORDER BY demand_id, version_no DESC"
    )).all()
    for demand_id, content in latest:
        positions = positions_from_letter(content or {})
        if positions is None:
            continue
        bind.execute(sa.text("DELETE FROM demand_trades WHERE demand_id = :d"), {"d": demand_id})
        for order, p in enumerate(positions):
            bind.execute(sa.text(
                "INSERT INTO demand_trades (demand_id, project, trade, quantity, salary, sort_order) "
                "VALUES (:d, :project, :trade, :quantity, :salary, :o)"
            ), {"d": demand_id, "o": order, **p})


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_demand_trades_salary_length'), 'demand_trades', type_='check')
    op.drop_column('demand_trades', 'salary')
