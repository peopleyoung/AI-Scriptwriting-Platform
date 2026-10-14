"""Durable direct conversations and an atomic work-wide AI slot."""
from alembic import op
import sqlalchemy as sa

revision = '0007_direct_conversations'
down_revision = '0006_independent_models'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('creative_conversations') as batch:
        batch.add_column(sa.Column('model_config_version_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('idempotency_key', sa.String(256), nullable=True))
        batch.add_column(sa.Column('request_hash', sa.String(64), nullable=True))
        batch.add_column(sa.Column('input_revision', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('input_snapshot_json', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('error_code', sa.String(64), nullable=True))
        batch.create_foreign_key('fk_conversation_model_version', 'model_config_versions', ['model_config_version_id'], ['id'], ondelete='RESTRICT')
        batch.create_unique_constraint('uq_conversations_idempotency', ['work_id', 'idempotency_key'])
    op.execute('UPDATE creative_conversations SET model_config_version_id = (SELECT model_config_version_id FROM model_invocation_records WHERE model_invocation_records.id = creative_conversations.invocation_id)')
    op.create_table('work_ai_activities',
        sa.Column('work_id', sa.String(36), sa.ForeignKey('works.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('reference_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.execute("INSERT INTO work_ai_activities (work_id, kind, reference_id, user_id) SELECT work_id, 'GENERATION', id, requested_by_user_id FROM generation_jobs WHERE status IN ('QUEUED','RUNNING')")


def downgrade():
    op.drop_table('work_ai_activities')
    with op.batch_alter_table('creative_conversations') as batch:
        batch.drop_constraint('uq_conversations_idempotency', type_='unique')
        batch.drop_constraint('fk_conversation_model_version', type_='foreignkey')
        for column in ('error_code', 'input_snapshot_json', 'input_revision', 'request_hash', 'idempotency_key', 'model_config_version_id'):
            batch.drop_column(column)
