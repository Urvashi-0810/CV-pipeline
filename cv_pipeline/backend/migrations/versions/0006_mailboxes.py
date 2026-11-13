"""Several mailboxes to fetch CVs from, with encrypted app passwords

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mailboxes',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('host', sa.Text(), server_default='imap.zoho.in', nullable=False),
        sa.Column('port', sa.Integer(), server_default='993', nullable=False),
        sa.Column('password_encrypted', sa.Text(), nullable=False),
        sa.Column('default_folder', sa.Text(), server_default='INBOX', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mailboxes')),
    )
    op.create_index('uq_mailboxes_email_host', 'mailboxes',
                    [sa.literal_column('lower(email)'), sa.literal_column('lower(host)')], unique=True)
    op.add_column('app_settings', sa.Column('mailboxes_imported', sa.Boolean(),
                                            server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    op.drop_column('app_settings', 'mailboxes_imported')
    op.drop_index('uq_mailboxes_email_host', table_name='mailboxes')
    op.drop_table('mailboxes')
