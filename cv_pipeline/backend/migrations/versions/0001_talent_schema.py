"""talent schema

Revision ID: 0001
Revises: 
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trusted extension: the database owner can create it without being a superuser.
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.create_table('candidates',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=True),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('phone', sa.Text(), nullable=True),
    sa.Column('passport_number', sa.Text(), nullable=True),
    sa.Column('job_title', sa.Text(), nullable=True),
    sa.Column('company', sa.Text(), nullable=True),
    sa.Column('date_of_birth', sa.Text(), nullable=True),
    sa.Column('location', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('education', sa.Text(), nullable=True),
    sa.Column('skills', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('certifications', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('languages', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('experience_years', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('age', sa.SmallInteger(), nullable=True),
    sa.Column('email_norm', sa.Text(), nullable=True),
    sa.Column('phone_norm', sa.Text(), nullable=True),
    sa.Column('passport_norm', sa.Text(), nullable=True),
    sa.Column('latest_cv_id', sa.BigInteger(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('manual_fields', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('needs_review', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('search_text', sa.Text(), server_default='', nullable=False),
    sa.Column('row_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates'))
    )
    op.create_index('ix_candidates_age', 'candidates', ['age'], unique=False)
    op.create_index('ix_candidates_email_norm', 'candidates', ['email_norm'], unique=False)
    op.create_index('ix_candidates_experience_years', 'candidates', ['experience_years'], unique=False)
    op.create_index('ix_candidates_full_name', 'candidates', ['full_name'], unique=False)
    op.create_index('ix_candidates_job_title_trgm', 'candidates', ['job_title'], unique=False, postgresql_using='gin', postgresql_ops={'job_title': 'gin_trgm_ops'})
    op.create_index('ix_candidates_location_trgm', 'candidates', ['location'], unique=False, postgresql_using='gin', postgresql_ops={'location': 'gin_trgm_ops'})
    op.create_index('ix_candidates_phone_norm', 'candidates', ['phone_norm'], unique=False)
    op.create_index('ix_candidates_received_at', 'candidates', ['received_at'], unique=False)
    op.create_index('ix_candidates_search_text_trgm', 'candidates', ['search_text'], unique=False, postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.create_index('ix_candidates_skills', 'candidates', ['skills'], unique=False, postgresql_using='gin')
    op.create_table('pipeline_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status in ('running', 'done', 'error')", name=op.f('ck_pipeline_runs_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pipeline_runs'))
    )
    op.create_index('ix_pipeline_runs_kind_started_at', 'pipeline_runs', ['kind', 'started_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('admin_failed_attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('admin_locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index('uq_users_username_lower', 'users', [sa.literal_column('lower(username)')], unique=True)
    op.create_table('app_settings',
    sa.Column('id', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('admin_password_hash', sa.Text(), nullable=True),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('id', name=op.f('ck_app_settings_single_row')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_app_settings_updated_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_app_settings'))
    )
    op.create_index(op.f('ix_app_settings_updated_by'), 'app_settings', ['updated_by'], unique=False)
    op.create_table('cv_documents',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('candidate_id', sa.BigInteger(), nullable=False),
    sa.Column('file_hash', sa.Text(), nullable=False),
    sa.Column('file_name', sa.Text(), nullable=False),
    sa.Column('local_rel_path', sa.Text(), nullable=True),
    sa.Column('s3_key', sa.Text(), nullable=True),
    sa.Column('s3_status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('source_type', sa.Text(), server_default='mail', nullable=False),
    sa.Column('mail_account', sa.Text(), nullable=True),
    sa.Column('mail_from', sa.Text(), nullable=True),
    sa.Column('mail_from_name', sa.Text(), nullable=True),
    sa.Column('mail_subject', sa.Text(), nullable=True),
    sa.Column('mail_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('extracted', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('passport_norm', sa.Text(), nullable=True),
    sa.Column('email_norm', sa.Text(), nullable=True),
    sa.Column('phone_norm', sa.Text(), nullable=True),
    sa.Column('extraction_method', sa.Text(), nullable=True),
    sa.Column('llm', sa.Text(), nullable=True),
    sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("s3_status in ('success', 'failed', 'pending', 'missing')", name=op.f('ck_cv_documents_s3_status')),
    sa.CheckConstraint("source_type in ('mail', 'manual')", name=op.f('ck_cv_documents_source_type')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_cv_documents_candidate_id_candidates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cv_documents')),
    sa.UniqueConstraint('file_hash', name=op.f('uq_cv_documents_file_hash'))
    )
    op.create_index(op.f('ix_cv_documents_candidate_id'), 'cv_documents', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_cv_documents_email_norm'), 'cv_documents', ['email_norm'], unique=False)
    op.create_index(op.f('ix_cv_documents_passport_norm'), 'cv_documents', ['passport_norm'], unique=False)
    op.create_index(op.f('ix_cv_documents_phone_norm'), 'cv_documents', ['phone_norm'], unique=False)
    op.create_index(op.f('ix_cv_documents_received_at'), 'cv_documents', ['received_at'], unique=False)
    op.create_index('ix_cv_documents_s3_status', 'cv_documents', ['s3_status'], unique=False)
    # candidates and cv_documents point at each other, so this link waits for both tables.
    op.create_foreign_key(op.f('fk_candidates_latest_cv_id_cv_documents'), 'candidates', 'cv_documents',
                          ['latest_cv_id'], ['id'], ondelete='SET NULL')
    op.create_table('review_flags',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='open', nullable=False),
    sa.Column('candidate_id', sa.BigInteger(), nullable=False),
    sa.Column('other_candidate_id', sa.BigInteger(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('resolved_by_user_id', sa.BigInteger(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("resolution is null or resolution in ('merged', 'different_people', 'kept_edit', 'accepted')", name=op.f('ck_review_flags_resolution')),
    sa.CheckConstraint("status in ('open', 'resolved')", name=op.f('ck_review_flags_status')),
    sa.CheckConstraint("type in ('possible_duplicate', 'edit_replaced')", name=op.f('ck_review_flags_type')),
    sa.CheckConstraint('other_candidate_id is distinct from candidate_id', name=op.f('ck_review_flags_two_candidates')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_review_flags_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['other_candidate_id'], ['candidates.id'], name=op.f('fk_review_flags_other_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by_user_id'], ['users.id'], name=op.f('fk_review_flags_resolved_by_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_flags'))
    )
    op.create_index(op.f('ix_review_flags_candidate_id'), 'review_flags', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_review_flags_other_candidate_id'), 'review_flags', ['other_candidate_id'], unique=False)
    op.create_index(op.f('ix_review_flags_resolved_by_user_id'), 'review_flags', ['resolved_by_user_id'], unique=False)
    op.create_index('ix_review_flags_status', 'review_flags', ['status'], unique=False)
    op.create_index('uq_review_flags_open_pair', 'review_flags', ['type', sa.literal_column('least(candidate_id, other_candidate_id)'), sa.literal_column('greatest(candidate_id, other_candidate_id)')], unique=True, postgresql_where=sa.text("status = 'open' AND other_candidate_id IS NOT NULL"))
    op.create_table('sessions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('csrf_token', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('admin_until', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('edit_history',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('candidate_id', sa.BigInteger(), nullable=False),
    sa.Column('field', sa.Text(), nullable=False),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('cv_document_id', sa.BigInteger(), nullable=True),
    sa.Column('changed_by_user_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind in ('manual_edit', 'replaced_by_cv', 'keep_my_edit')", name=op.f('ck_edit_history_kind')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_edit_history_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['changed_by_user_id'], ['users.id'], name=op.f('fk_edit_history_changed_by_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['cv_document_id'], ['cv_documents.id'], name=op.f('fk_edit_history_cv_document_id_cv_documents'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_edit_history'))
    )
    op.create_index(op.f('ix_edit_history_candidate_id'), 'edit_history', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_edit_history_changed_by_user_id'), 'edit_history', ['changed_by_user_id'], unique=False)
    op.create_index(op.f('ix_edit_history_cv_document_id'), 'edit_history', ['cv_document_id'], unique=False)


def downgrade() -> None:
    op.drop_constraint(op.f('fk_candidates_latest_cv_id_cv_documents'), 'candidates', type_='foreignkey')
    op.drop_index(op.f('ix_edit_history_cv_document_id'), table_name='edit_history')
    op.drop_index(op.f('ix_edit_history_changed_by_user_id'), table_name='edit_history')
    op.drop_index(op.f('ix_edit_history_candidate_id'), table_name='edit_history')
    op.drop_table('edit_history')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_expires_at'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index('uq_review_flags_open_pair', table_name='review_flags', postgresql_where=sa.text("status = 'open' AND other_candidate_id IS NOT NULL"))
    op.drop_index('ix_review_flags_status', table_name='review_flags')
    op.drop_index(op.f('ix_review_flags_resolved_by_user_id'), table_name='review_flags')
    op.drop_index(op.f('ix_review_flags_other_candidate_id'), table_name='review_flags')
    op.drop_index(op.f('ix_review_flags_candidate_id'), table_name='review_flags')
    op.drop_table('review_flags')
    op.drop_index('ix_cv_documents_s3_status', table_name='cv_documents')
    op.drop_index(op.f('ix_cv_documents_received_at'), table_name='cv_documents')
    op.drop_index(op.f('ix_cv_documents_phone_norm'), table_name='cv_documents')
    op.drop_index(op.f('ix_cv_documents_passport_norm'), table_name='cv_documents')
    op.drop_index(op.f('ix_cv_documents_email_norm'), table_name='cv_documents')
    op.drop_index(op.f('ix_cv_documents_candidate_id'), table_name='cv_documents')
    op.drop_table('cv_documents')
    op.drop_index(op.f('ix_app_settings_updated_by'), table_name='app_settings')
    op.drop_table('app_settings')
    op.drop_index('uq_users_username_lower', table_name='users')
    op.drop_table('users')
    op.drop_index('ix_pipeline_runs_kind_started_at', table_name='pipeline_runs')
    op.drop_table('pipeline_runs')
    op.drop_index('ix_candidates_skills', table_name='candidates', postgresql_using='gin')
    op.drop_index('ix_candidates_search_text_trgm', table_name='candidates', postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.drop_index('ix_candidates_received_at', table_name='candidates')
    op.drop_index('ix_candidates_phone_norm', table_name='candidates')
    op.drop_index('ix_candidates_location_trgm', table_name='candidates', postgresql_using='gin', postgresql_ops={'location': 'gin_trgm_ops'})
    op.drop_index('ix_candidates_job_title_trgm', table_name='candidates', postgresql_using='gin', postgresql_ops={'job_title': 'gin_trgm_ops'})
    op.drop_index('ix_candidates_full_name', table_name='candidates')
    op.drop_index('ix_candidates_experience_years', table_name='candidates')
    op.drop_index('ix_candidates_email_norm', table_name='candidates')
    op.drop_index('ix_candidates_age', table_name='candidates')
    op.drop_table('candidates')
    op.execute('DROP EXTENSION IF EXISTS pg_trgm')
