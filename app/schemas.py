from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)

    @field_validator("new_password")
    @classmethod
    def passwords_must_differ(cls, value: str, info):
        if info.data.get("current_password") == value:
            raise ValueError("new password must differ from current password")
        return value


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)
    role: Literal["ADMIN", "WRITER"] = "WRITER"


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class SetActiveRequest(BaseModel):
    active: bool


class BootstrapRequest(CreateUserRequest):
    bootstrap_token: str | None = None


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    user: UserResponse
    must_change_password: bool


class AuditResponse(BaseModel):
    id: str
    event_type: str
    actor_user_id: str | None
    target_user_id: str | None
    metadata: dict[str, Any]
    created_at: datetime


class WorkCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    template_id: str | None = Field(default=None, min_length=1, max_length=36)
    template_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    settings: dict[str, Any] = Field(default_factory=dict)


class InviteMemberRequest(BaseModel):
    user_id: str | None = Field(default=None, min_length=1, max_length=36)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    role: Literal["EDITOR", "VIEWER"]

    @model_validator(mode="after")
    def has_target(self):
        if not self.user_id and not self.username:
            raise ValueError("user_id or username is required")
        if self.user_id and self.username:
            raise ValueError("provide only one invitation target")
        return self


class MemberRoleRequest(BaseModel):
    role: Literal["EDITOR", "VIEWER"]


class TransferOwnershipRequest(BaseModel):
    user_id: str | None = Field(default=None, min_length=1, max_length=36)
    username: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def has_target(self):
        if not self.user_id and not self.username:
            raise ValueError("user_id or username is required")
        if self.user_id and self.username:
            raise ValueError("provide only one ownership transfer target")
        return self


class WorkMemberResponse(BaseModel):
    id: str
    work_id: str
    user_id: str
    username: str
    role: str
    status: str
    invited_at: datetime | None
    accepted_at: datetime | None
    revoked_at: datetime | None


class WorkResponse(BaseModel):
    id: str
    title: str
    owner_id: str
    template_version_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkDetailResponse(WorkResponse):
    members: list[WorkMemberResponse]


class TemplateCreateRequest(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=128)
    key: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    definition: dict[str, Any]

    @model_validator(mode="after")
    def require_slug(self):
        if not self.slug and not self.key:
            raise ValueError("slug or key is required")
        if self.slug and self.key and self.slug != self.key:
            raise ValueError("slug and key must match when both are provided")
        if self.slug is None:
            self.slug = self.key
        return self


class TemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    definition: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.definition is None:
            raise ValueError("name or definition is required")
        return self


class TemplateVersionResponse(BaseModel):
    id: str
    template_id: str
    version: int
    status: str
    definition: dict[str, Any]
    created_at: datetime
    published_at: datetime | None


class TemplateResponse(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    published_version: TemplateVersionResponse | None = None
    versions: list[TemplateVersionResponse] = Field(default_factory=list)


class CreativeResponse(BaseModel):
    work_id: str
    brief: dict[str, Any] | None
    characters: list[dict[str, Any]] | None
    outline: dict[str, Any] | None
    confirmed: dict[str, Any | None]
    revisions: dict[str, int]


class ChangeProposalRequest(BaseModel):
    stage: Literal["brief", "characters", "outline"]
    content: Any


class ChangeProposalResponse(BaseModel):
    id: str
    work_id: str
    stage: str
    content: Any
    base_revision: int
    status: str
    submitted_by_user_id: str
    reviewed_by_user_id: str | None
    rejection_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None



class DraftUpdateRequest(BaseModel):
    content: dict[str, Any]
    expected_revision: int | None = None
    autosave: bool = True


class VersionCreateRequest(BaseModel):
    content: dict[str, Any] | None = None
    expected_revision: int | None = None


class ScriptVersionResponse(BaseModel):
    id: str
    work_id: str
    source: str
    author_user_id: str | None
    parent_version_id: str | None
    content: dict[str, Any]
    content_hash: str
    work_revision: int
    created_at: datetime


class VersionCompareResponse(BaseModel):
    from_version: ScriptVersionResponse
    to_version: ScriptVersionResponse
    changed: bool


class ReleaseCreateRequest(BaseModel):
    version_id: str


class ScriptReleaseResponse(BaseModel):
    id: str
    work_id: str
    version_id: str
    released_by_user_id: str
    is_current: bool
    created_at: datetime
    version: ScriptVersionResponse


class ProviderCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    adapter: Literal["openai", "anthropic", "mock"] = "mock"
    retention_description: str | None = None
    deletion_description: str | None = None
    incident_description: str | None = None
    credential: str | None = None


class ProviderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    retention_description: str | None = None
    deletion_description: str | None = None
    incident_description: str | None = None
    enabled: bool | None = None
    credential: str | None = None


class ProviderResponse(BaseModel):
    id: str
    slug: str
    name: str
    adapter: str
    enabled: bool
    retention_description: str | None
    deletion_description: str | None
    incident_description: str | None
    created_at: datetime
    updated_at: datetime


class ModelConfigCreateRequest(BaseModel):
    provider_id: str
    model_name: str = Field(min_length=1, max_length=256)
    endpoint: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    credential: str | None = None


class ModelConfigUpdateRequest(BaseModel):
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    endpoint: str | None = None
    parameters: dict[str, Any] | None = None
    credential: str | None = None


class ModelConfigResponse(BaseModel):
    id: str
    provider_id: str
    version: int
    model_name: str
    endpoint: str | None
    parameters: dict[str, Any]
    status: str
    created_at: datetime


class ConsentCreateRequest(BaseModel):
    model_config_version_id: str
    data_categories: list[str] = Field(min_length=1)
    input_revision: int | None = None


class ConsentResponse(BaseModel):
    id: str
    work_id: str
    input_revision: int
    model_config_version_id: str
    data_categories: list[str]
    status: str
    granted_by_user_id: str
    created_at: datetime
    revoked_at: datetime | None


class ConversationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    consent_id: str


class ConversationResponse(BaseModel):
    id: str
    work_id: str
    user_id: str
    user_message: str
    assistant_message: str | None
    status: str
    invocation_id: str | None
    created_at: datetime


class GenerationJobRequest(BaseModel):
    target_type: Literal["WORK", "EPISODE", "SCENE", "FRAGMENT"] = "WORK"
    target_path: dict[str, Any] = Field(default_factory=dict)
    instruction: str | None = Field(default=None, max_length=10000)
    consent_id: str
    model_config_version_id: str | None = None
    input_revision: int | None = None
    expected_revision: int | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    auto_run: bool = False


class GenerationJobResponse(BaseModel):
    id: str
    work_id: str
    requested_by_user_id: str
    idempotency_key: str
    input_revision: int
    target_type: str
    target_path: dict[str, Any]
    status: str
    retry_count: int
    error_code: str | None
    error_message: str | None
    result_version_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExportRequest(BaseModel):
    format: Literal["markdown", "docx"]
    release_id: str | None = Field(default=None, min_length=1, max_length=36)


class ExportJobResponse(BaseModel):
    id: str
    work_id: str
    release_id: str
    format: str
    status: str
    download_url: str | None = None
    download_expires_at: datetime | None = None
    error_code: str | None = None
    created_at: datetime

