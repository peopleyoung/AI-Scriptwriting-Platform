from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="WRITER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    sessions: Mapped[list["SessionRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Work(Base):
    __tablename__ = "works"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    template_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("script_template_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped["User"] = relationship(foreign_keys=[owner_id])
    template_version: Mapped["ScriptTemplateVersion | None"] = relationship(foreign_keys=[template_version_id])
    members: Mapped[list["WorkMember"]] = relationship(back_populates="work", cascade="all, delete-orphan")
    creative_data: Mapped["WorkCreativeData | None"] = relationship(
        back_populates="work", uselist=False, cascade="all, delete-orphan"
    )
    change_proposals: Mapped[list["ChangeProposal"]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )


class WorkMember(Base):
    __tablename__ = "work_members"
    __table_args__ = (
        UniqueConstraint("work_id", "user_id", name="uq_work_members_work_user"),
        Index("ix_work_members_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACCEPTED", index=True)
    invited_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    work: Mapped["Work"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(foreign_keys=[user_id])


class ScriptTemplate(Base):
    __tablename__ = "script_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    versions: Mapped[list["ScriptTemplateVersion"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="ScriptTemplateVersion.version"
    )


class ScriptTemplateVersion(Base):
    __tablename__ = "script_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_script_template_versions_template_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    template_id: Mapped[str] = mapped_column(ForeignKey("script_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False, index=True)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    template: Mapped["ScriptTemplate"] = relationship(back_populates="versions")


class WorkCreativeData(Base):
    __tablename__ = "work_creative_data"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), unique=True, nullable=False)
    brief_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    characters_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    outline_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    brief_confirmed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    characters_confirmed_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    outline_confirmed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    brief_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    characters_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outline_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    work: Mapped["Work"] = relationship(back_populates="creative_data")


class ChangeProposal(Base):
    __tablename__ = "change_proposals"
    __table_args__ = (
        Index(
            "uq_change_proposals_pending_stage",
            "work_id",
            "stage",
            unique=True,
            sqlite_where=text("status = 'PENDING'"),
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    content_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False, index=True)
    submitted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    work: Mapped["Work"] = relationship(back_populates="change_proposals")
    submitted_by: Mapped["User"] = relationship(foreign_keys=[submitted_by_user_id])
    reviewed_by: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by_user_id])



class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (Index("ix_script_versions_work_created", "work_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey("script_versions.id", ondelete="SET NULL"), nullable=True)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    work_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    work: Mapped["Work"] = relationship(foreign_keys=[work_id])
    author: Mapped["User | None"] = relationship(foreign_keys=[author_user_id])


class ScriptRelease(Base):
    __tablename__ = "script_releases"
    __table_args__ = (Index("ix_script_releases_work_current", "work_id", "is_current"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    released_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    work: Mapped["Work"] = relationship(foreign_keys=[work_id])
    version: Mapped["ScriptVersion"] = relationship(foreign_keys=[version_id])
    released_by: Mapped["User"] = relationship(foreign_keys=[released_by_user_id])


class ModelProvider(Base):
    __tablename__ = "model_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    adapter: Mapped[str] = mapped_column(String(32), default="openai", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    retention_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ModelConfiguration(Base):
    """Stable identity and availability; invocation settings live in immutable versions."""
    __tablename__ = "model_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ModelConfigVersion(Base):
    __tablename__ = "model_config_versions"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_config_identity_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider_id: Mapped[str] = mapped_column(ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("model_configurations.id", ondelete="RESTRICT"), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    adapter: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    provider: Mapped["ModelProvider"] = relationship()
    model: Mapped["ModelConfiguration | None"] = relationship()


class ExternalConsent(Base):
    __tablename__ = "external_consents"
    __table_args__ = (Index("ix_external_consents_work_status", "work_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model_config_version_id: Mapped[str] = mapped_column(ForeignKey("model_config_versions.id", ondelete="RESTRICT"), nullable=False)
    data_categories_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    granted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CreativeConversation(Base):
    __tablename__ = "creative_conversations"
    __table_args__ = (Index("ix_creative_conversations_work_created", "work_id", "created_at"), UniqueConstraint("work_id", "idempotency_key", name="uq_conversations_idempotency"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="SUCCEEDED", nullable=False)
    invocation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_config_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_config_versions.id", ondelete="RESTRICT"), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped["ModelConfigVersion | None"] = relationship()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class WorkAIActivity(Base):
    __tablename__ = "work_ai_activities"

    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ModelInvocationRecord(Base):
    __tablename__ = "model_invocation_records"
    __table_args__ = (Index("ix_model_invocations_work_created", "work_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("model_providers.id", ondelete="RESTRICT"), nullable=False)
    model_config_version_id: Mapped[str] = mapped_column(ForeignKey("model_config_versions.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("work_id", "idempotency_key", name="uq_generation_jobs_work_idempotency"),
        Index("ix_generation_jobs_work_status", "work_id", "status"),
        Index("uq_generation_jobs_active_work", "work_id", unique=True, sqlite_where=text("status IN ('QUEUED', 'RUNNING')"), postgresql_where=text("status IN ('QUEUED', 'RUNNING')")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_path_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_config_version_id: Mapped[str] = mapped_column(ForeignKey("model_config_versions.id", ondelete="RESTRICT"), nullable=False)
    consent_id: Mapped[str | None] = mapped_column(ForeignKey("external_consents.id", ondelete="RESTRICT"), nullable=True)
    input_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", nullable=False, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_version_id: Mapped[str | None] = mapped_column(ForeignKey("script_versions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (Index("ix_export_jobs_work_created", "work_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    work_id: Mapped[str] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=False, index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("script_releases.id", ondelete="RESTRICT"), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="SUCCEEDED", nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    download_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

