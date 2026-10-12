"""create works and explicit memberships

Revision ID: 0002_works_access
Revises: 0001_account_sessions
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_works_access"
down_revision: Union[str, Sequence[str], None] = "0001_account_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "works",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_works_owner_id", "works", ["owner_id"], unique=False)
    op.create_index("ix_works_status", "works", ["status"], unique=False)
    op.create_table(
        "work_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACCEPTED", nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_id", "user_id", name="uq_work_members_work_user"),
    )
    op.create_index("ix_work_members_work_id", "work_members", ["work_id"], unique=False)
    op.create_index("ix_work_members_user_id", "work_members", ["user_id"], unique=False)
    op.create_index("ix_work_members_status", "work_members", ["status"], unique=False)
    op.create_index("ix_work_members_user_status", "work_members", ["user_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_work_members_user_status", table_name="work_members")
    op.drop_index("ix_work_members_status", table_name="work_members")
    op.drop_index("ix_work_members_user_id", table_name="work_members")
    op.drop_index("ix_work_members_work_id", table_name="work_members")
    op.drop_table("work_members")
    op.drop_index("ix_works_status", table_name="works")
    op.drop_index("ix_works_owner_id", table_name="works")
    op.drop_table("works")
