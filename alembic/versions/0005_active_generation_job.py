"""Enforce one active generation job per work under concurrent requests."""
from alembic import op
import sqlalchemy as sa
revision = "0005_active_generation_job"
down_revision = "0004_versions_models_generation_exports"
branch_labels = None
depends_on = None


def upgrade():
    # Older installations may contain duplicates; preserve the oldest active job.
    op.execute("UPDATE generation_jobs SET status='FAILED', error_code='DUPLICATE_ACTIVE_JOB' WHERE status IN ('QUEUED','RUNNING') AND id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY work_id ORDER BY created_at, id) AS position FROM generation_jobs WHERE status IN ('QUEUED','RUNNING')) AS ranked WHERE position > 1)")
    op.create_index("uq_generation_jobs_active_work", "generation_jobs", ["work_id"], unique=True, sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING')"), postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"))


def downgrade():
    op.drop_index("uq_generation_jobs_active_work", table_name="generation_jobs")
