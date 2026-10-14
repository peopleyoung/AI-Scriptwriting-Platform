"""Generation requests no longer require a user-facing external consent."""
from alembic import op
import sqlalchemy as sa

revision = '0008_direct_generation'
down_revision = '0007_direct_conversations'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('generation_jobs') as batch:
        batch.alter_column('consent_id', existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column('input_snapshot_json', sa.JSON(), nullable=True))

def downgrade():
    with op.batch_alter_table('generation_jobs') as batch:
        batch.drop_column('input_snapshot_json')
        batch.alter_column('consent_id', existing_type=sa.String(36), nullable=False)
