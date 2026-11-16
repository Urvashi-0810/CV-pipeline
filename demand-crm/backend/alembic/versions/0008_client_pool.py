"""client pool: client companies and their contact persons

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'clients',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('length(btrim(name)) between 1 and 200', name=op.f('ck_clients_name_length')),
        sa.CheckConstraint('email is null or length(email) <= 254', name=op.f('ck_clients_email_length')),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_clients_created_by_users'),
                                ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_clients')),
    )
    op.create_index(op.f('ix_clients_created_by'), 'clients', ['created_by'], unique=False)
    op.create_index('uq_clients_name_lower', 'clients', [sa.text('lower(btrim(name))')], unique=True)

    op.create_table(
        'client_contacts',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('client_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('phone', sa.Text(), nullable=True),
        sa.Column('email', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint('length(btrim(name)) between 1 and 100', name=op.f('ck_client_contacts_name_length')),
        sa.CheckConstraint('phone is null or length(phone) <= 30', name=op.f('ck_client_contacts_phone_length')),
        sa.CheckConstraint('email is null or length(email) <= 254', name=op.f('ck_client_contacts_email_length')),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_client_contacts_client_id_clients'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_client_contacts')),
    )
    op.create_index(op.f('ix_client_contacts_client_id'), 'client_contacts', ['client_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_client_contacts_client_id'), table_name='client_contacts')
    op.drop_table('client_contacts')
    op.drop_index('uq_clients_name_lower', table_name='clients')
    op.drop_index(op.f('ix_clients_created_by'), table_name='clients')
    op.drop_table('clients')
