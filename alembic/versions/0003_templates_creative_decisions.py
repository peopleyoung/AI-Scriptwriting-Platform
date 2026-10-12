"""add versioned templates and creative decisions

Revision ID: 0003_templates_creative_decisions
Revises: 0002_works_access
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_templates_creative_decisions"
down_revision: Union[str, Sequence[str], None] = "0002_works_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "script_templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_script_templates_created_by_user_id", "script_templates", ["created_by_user_id"], unique=False)
    op.create_table(
        "script_template_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["script_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version", name="uq_script_template_versions_template_version"),
    )
    op.create_index("ix_script_template_versions_template_id", "script_template_versions", ["template_id"], unique=False)
    op.create_index("ix_script_template_versions_status", "script_template_versions", ["status"], unique=False)
    op.create_index(
        "ix_script_template_versions_created_by_user_id", "script_template_versions", ["created_by_user_id"], unique=False
    )

    with op.batch_alter_table("works") as batch:
        batch.add_column(sa.Column("template_version_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("settings_json", sa.JSON(), nullable=False, server_default="{}"))
        batch.create_foreign_key(
            "fk_works_template_version_id", "script_template_versions", ["template_version_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_index("ix_works_template_version_id", ["template_version_id"], unique=False)

    op.create_table(
        "work_creative_data",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("brief_json", sa.JSON(), nullable=True),
        sa.Column("characters_json", sa.JSON(), nullable=True),
        sa.Column("outline_json", sa.JSON(), nullable=True),
        sa.Column("brief_confirmed_json", sa.JSON(), nullable=True),
        sa.Column("characters_confirmed_json", sa.JSON(), nullable=True),
        sa.Column("outline_confirmed_json", sa.JSON(), nullable=True),
        sa.Column("brief_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("characters_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outline_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id"),
    )
    op.create_table(
        "change_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("submitted_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("rejection_reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_change_proposals_work_id", "change_proposals", ["work_id"], unique=False)
    op.create_index("ix_change_proposals_status", "change_proposals", ["status"], unique=False)
    op.create_index(
        "uq_change_proposals_pending_stage",
        "change_proposals",
        ["work_id", "stage"],
        unique=True,
        sqlite_where=sa.text("status = 'PENDING'"),
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_change_proposals_pending_stage", table_name="change_proposals")
    op.drop_index("ix_change_proposals_status", table_name="change_proposals")
    op.drop_index("ix_change_proposals_work_id", table_name="change_proposals")
    op.drop_table("change_proposals")
    op.drop_table("work_creative_data")
    with op.batch_alter_table("works") as batch:
        batch.drop_index("ix_works_template_version_id")
        batch.drop_constraint("fk_works_template_version_id", type_="foreignkey")
        batch.drop_column("settings_json")
        batch.drop_column("template_version_id")
    op.drop_index("ix_script_template_versions_created_by_user_id", table_name="script_template_versions")
    op.drop_index("ix_script_template_versions_status", table_name="script_template_versions")
    op.drop_index("ix_script_template_versions_template_id", table_name="script_template_versions")
    op.drop_table("script_template_versions")
    op.drop_index("ix_script_templates_created_by_user_id", table_name="script_templates")
    op.drop_table("script_templates")
