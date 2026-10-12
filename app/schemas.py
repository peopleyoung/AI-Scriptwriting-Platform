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
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkDetailResponse(WorkResponse):
    members: list[WorkMemberResponse]

