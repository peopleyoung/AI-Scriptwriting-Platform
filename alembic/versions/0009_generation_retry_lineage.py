"""Retry lineage: new generation jobs reference the failed job they retried."""
from alembic import op
import sqlalchemy as sa

revision = '0009_generation_retry_lineage'
down_revision = '0008_direct_generation'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('generation_jobs') as batch:
        batch.add_column(sa.Column('retry_of_job_id', sa.String(36), nullable=True))
        batch.create_foreign_key(
            'fk_generation_jobs_retry_of_job_id',
            'generation_jobs',
            ['retry_of_job_id'], ['id'],
            ondelete='SET NULL',
        )
    op.create_index('ix_generation_jobs_retry_of_job_id', 'generation_jobs', ['retry_of_job_id'])

def downgrade():
    op.drop_index('ix_generation_jobs_retry_of_job_id', table_name='generation_jobs')
    with op.batch_alter_table('generation_jobs') as batch:
        batch.drop_constraint('fk_generation_jobs_retry_of_job_id', type_='foreignkey')
        batch.drop_column('retry_of_job_id')
