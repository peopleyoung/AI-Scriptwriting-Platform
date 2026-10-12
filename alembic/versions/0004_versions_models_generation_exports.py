"""add script versions, model configuration, generation and export state

Revision ID: 0004_versions_models_generation_exports
Revises: 0003_templates_creative_decisions
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_versions_models_generation_exports"
down_revision: Union[str, Sequence[str], None] = "0003_templates_creative_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("works") as batch:
        batch.add_column(sa.Column("draft_json", sa.JSON(), nullable=False, server_default="{}"))

    op.create_table(
        "script_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("author_user_id", sa.String(36)),
        sa.Column("parent_version_id", sa.String(36)),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("work_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_version_id"], ["script_versions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_script_versions_work_id", "script_versions", ["work_id"])
    op.create_index("ix_script_versions_author_user_id", "script_versions", ["author_user_id"])
    op.create_index("ix_script_versions_work_created", "script_versions", ["work_id", "created_at"])

    op.create_table(
        "script_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("released_by_user_id", sa.String(36), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["script_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_script_releases_work_id", "script_releases", ["work_id"])
    op.create_index("ix_script_releases_version_id", "script_releases", ["version_id"])
    op.create_index("ix_script_releases_work_current", "script_releases", ["work_id", "is_current"])

    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("adapter", sa.String(32), nullable=False, server_default="openai"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retention_description", sa.Text()),
        sa.Column("deletion_description", sa.Text()),
        sa.Column("incident_description", sa.Text()),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column("created_by_user_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_model_providers_enabled", "model_providers", ["enabled"])

    op.create_table(
        "model_config_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("endpoint", sa.Text()),
        sa.Column("parameters_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by_user_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("provider_id", "version", name="uq_model_config_provider_version"),
    )
    op.create_index("ix_model_config_versions_provider_id", "model_config_versions", ["provider_id"])
    op.create_index("ix_model_config_versions_status", "model_config_versions", ["status"])

    op.create_table(
        "external_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("model_config_version_id", sa.String(36), nullable=False),
        sa.Column("data_categories_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("granted_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_config_version_id"], ["model_config_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_external_consents_work_id", "external_consents", ["work_id"])
    op.create_index("ix_external_consents_work_status", "external_consents", ["work_id", "status"])

    op.create_table(
        "creative_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_message", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="SUCCEEDED"),
        sa.Column("invocation_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_creative_conversations_work_id", "creative_conversations", ["work_id"])
    op.create_index("ix_creative_conversations_work_created", "creative_conversations", ["work_id", "created_at"])

    op.create_table(
        "model_invocation_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("provider_id", sa.String(36), nullable=False),
        sa.Column("model_config_version_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_config_version_id"], ["model_config_versions.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_model_invocation_records_work_id", "model_invocation_records", ["work_id"])
    op.create_index("ix_model_invocations_work_created", "model_invocation_records", ["work_id", "created_at"])

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_path_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("instruction", sa.Text()),
        sa.Column("model_config_version_id", sa.String(36), nullable=False),
        sa.Column("consent_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("result_version_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_config_version_id"], ["model_config_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["consent_id"], ["external_consents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_version_id"], ["script_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("work_id", "idempotency_key", name="uq_generation_jobs_work_idempotency"),
    )
    op.create_index("ix_generation_jobs_work_id", "generation_jobs", ["work_id"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index("ix_generation_jobs_work_status", "generation_jobs", ["work_id", "status"])

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="SUCCEEDED"),
        sa.Column("content", sa.Text()),
        sa.Column("download_token_hash", sa.String(64), unique=True),
        sa.Column("download_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["script_releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_export_jobs_work_id", "export_jobs", ["work_id"])
    op.create_index("ix_export_jobs_work_created", "export_jobs", ["work_id", "created_at"])

    op.create_table(
        "backup_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )



def downgrade() -> None:
    for index, table in [
        ("ix_export_jobs_work_created", "export_jobs"),
        ("ix_export_jobs_work_id", "export_jobs"),
        ("ix_generation_jobs_work_status", "generation_jobs"),
        ("ix_generation_jobs_status", "generation_jobs"),
        ("ix_generation_jobs_work_id", "generation_jobs"),
        ("ix_model_invocations_work_created", "model_invocation_records"),
        ("ix_model_invocation_records_work_id", "model_invocation_records"),
        ("ix_creative_conversations_work_created", "creative_conversations"),
        ("ix_creative_conversations_work_id", "creative_conversations"),
        ("ix_external_consents_work_status", "external_consents"),
        ("ix_external_consents_work_id", "external_consents"),
        ("ix_model_config_versions_status", "model_config_versions"),
        ("ix_model_config_versions_provider_id", "model_config_versions"),
        ("ix_model_providers_enabled", "model_providers"),
        ("ix_script_releases_work_current", "script_releases"),
        ("ix_script_releases_version_id", "script_releases"),
        ("ix_script_releases_work_id", "script_releases"),
        ("ix_script_versions_work_created", "script_versions"),
        ("ix_script_versions_author_user_id", "script_versions"),
        ("ix_script_versions_work_id", "script_versions"),
    ]:
        op.drop_index(index, table_name=table)
    for table in ["backup_records", "export_jobs", "generation_jobs", "model_invocation_records", "creative_conversations", "external_consents", "model_config_versions", "model_providers", "script_releases", "script_versions"]:
        op.drop_table(table)
    with op.batch_alter_table("works") as batch:
        batch.drop_column("draft_json")
