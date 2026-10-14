"""Independent model identities, retaining all historical version IDs and credentials."""
import json

from alembic import op
import sqlalchemy as sa

revision = "0006_independent_models"
down_revision = "0005_active_generation_job"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    with op.batch_alter_table("model_config_versions") as batch:
        batch.add_column(sa.Column("model_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("display_name", sa.String(256), nullable=True))
        batch.add_column(sa.Column("adapter", sa.String(32), nullable=True))
        batch.create_foreign_key("fk_config_model_identity", "model_configurations", ["model_id"], ["id"], ondelete="RESTRICT")
        batch.drop_constraint("uq_model_config_provider_version", type_="unique")
        batch.create_unique_constraint("uq_model_config_identity_version", ["model_id", "version"])
        batch.create_index("ix_model_config_versions_model_id", ["model_id"])
    db = op.get_bind()
    metadata = sa.MetaData()
    configs = sa.Table("model_config_versions", metadata, autoload_with=db)
    providers = {r.id: r for r in db.execute(sa.text("SELECT * FROM model_providers")).mappings()}
    rows = list(db.execute(sa.select(configs).order_by(configs.c.version, configs.c.id)).mappings())
    parent = {r.id: r.id for r in rows}

    def root(value):
        while parent[value] != value:
            value = parent[value]
        return value

    # Only an explicit replacement links two identities. Matching names/URLs
    # cannot establish identity: adding another model used to retire its sibling.
    for audit in db.execute(sa.text("SELECT metadata_json FROM audit_logs WHERE event_type = 'MODEL_CONFIG_VERSION_CREATED' ORDER BY created_at, id")).mappings():
        data = audit.metadata_json
        if isinstance(data, str):
            data = json.loads(data)
        current, previous = data.get("config_id"), data.get("replaced_config_id")
        if current in parent and previous in parent:
            parent[root(current)] = root(previous)
    identities = {}
    for row in rows:
        identity = root(row.id)
        provider = providers[row.provider_id]
        identities[identity] = identities.get(identity, False) or (row.status == "ACTIVE" and provider.enabled)
    table = sa.table("model_configurations", sa.column("id"), sa.column("enabled"))
    for identity, enabled in identities.items():
        db.execute(table.insert().values(id=identity, enabled=enabled))
    for row in rows:
        provider = providers[row.provider_id]
        db.execute(configs.update().where(configs.c.id == row.id).values(
            model_id=root(row.id), display_name=row.model_name, adapter=provider.adapter,
            credential_ciphertext=row.credential_ciphertext or provider.credential_ciphertext,
        ))


def downgrade():
    # Separate models may now share a provider/version. Recreating the old unique
    # constraint would destroy data or fail; keep the archive and remove new fields.
    with op.batch_alter_table("model_config_versions") as batch:
        batch.drop_index("ix_model_config_versions_model_id")
        batch.drop_constraint("uq_model_config_identity_version", type_="unique")
        batch.drop_constraint("fk_config_model_identity", type_="foreignkey")
        batch.drop_column("adapter")
        batch.drop_column("display_name")
        batch.drop_column("model_id")
    op.drop_table("model_configurations")
