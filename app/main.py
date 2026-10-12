from contextlib import asynccontextmanager
from datetime import datetime, timezone
from copy import deepcopy
from typing import Any, Annotated

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, build_engine, build_session_factory, get_db
from app.models import (
    AuditLog,
    ChangeProposal,
    ScriptTemplate,
    ScriptTemplateVersion,
    SessionRecord,
    User,
    Work,
    WorkCreativeData,
    WorkMember,
)
from app.features import invalidate_work_external_state, register_feature_routes
from app.schemas import (
    AuditResponse,
    BootstrapRequest,
    ChangePasswordRequest,
    ChangeProposalRequest,
    ChangeProposalResponse,
    CreateUserRequest,
    HealthResponse,
    InviteMemberRequest,
    LoginRequest,
    LoginResponse,
    MemberRoleRequest,
    ResetPasswordRequest,
    SetActiveRequest,
    TransferOwnershipRequest,
    CreativeResponse,
    TemplateCreateRequest,
    TemplateResponse,
    TemplateUpdateRequest,
    TemplateVersionResponse,
    UserResponse,
    WorkCreateRequest,
    WorkDetailResponse,
    WorkMemberResponse,
    WorkResponse,
)
from app.security import hash_password, new_session_token, session_expiry, session_token_hash, verify_password


_failed_logins: dict[tuple[str, str], list[datetime]] = {}

_CREATIVE_STAGES = ("brief", "characters", "outline")


def _builtin_template_definitions() -> list[dict[str, Any]]:
    common_settings = {
        "episode_count": {"type": "integer", "min": 1, "max": 200},
        "episode_duration_minutes": {"type": "integer", "min": 1, "max": 120},
        "episode_word_target": {"type": "integer", "min": 1, "max": 100000},
        "genre_tags": {"type": "array", "items": {"type": "string", "max_length": 64}},
        "output_format": {"type": "enum", "values": ["markdown", "docx"]},
    }
    return [
        {
            "slug": "vertical-micro-drama",
            "name": "竖屏微短剧",
            "definition": {
                "settings": common_settings,
                "creative_brief": {
                    "fields": {
                        "theme": {"type": "string", "max_length": 2000},
                        "tone": {"type": "string", "max_length": 2000},
                        "logline": {"type": "string", "max_length": 2000},
                        "target_audience": {"type": "string", "max_length": 500},
                        "world_setting": {"type": "string", "max_length": 4000},
                    }
                },
                "characters": {
                    "item_fields": {
                        "name": {"type": "string", "max_length": 100, "required": True},
                        "role": {"type": "string", "max_length": 200},
                        "traits": {"type": "string", "max_length": 1000},
                        "goal": {"type": "string", "max_length": 1000},
                        "relationship": {"type": "string", "max_length": 1000},
                    }
                },
                "outline": {
                    "fields": {
                        "premise": {"type": "string", "max_length": 4000},
                        "beginning": {"type": "string", "max_length": 4000},
                        "middle": {"type": "string", "max_length": 4000},
                        "ending": {"type": "string", "max_length": 4000},
                        "episode_summaries": {"type": "array", "items": {"type": "string", "max_length": 4000}},
                    }
                },
                "script_structure": {"blocks": ["SCENE_HEADING", "ACTION", "CHARACTER", "DIALOGUE"]},
                "output_formats": ["markdown", "docx"],
            },
        },
        {
            "slug": "web-short-drama",
            "name": "网剧短剧",
            "definition": {
                "settings": common_settings,
                "creative_brief": {
                    "fields": {
                        "theme": {"type": "string", "max_length": 2000},
                        "tone": {"type": "string", "max_length": 2000},
                        "logline": {"type": "string", "max_length": 2000},
                        "target_audience": {"type": "string", "max_length": 500},
                        "world_setting": {"type": "string", "max_length": 4000},
                    }
                },
                "characters": {
                    "item_fields": {
                        "name": {"type": "string", "max_length": 100, "required": True},
                        "role": {"type": "string", "max_length": 200},
                        "traits": {"type": "string", "max_length": 1000},
                        "goal": {"type": "string", "max_length": 1000},
                        "relationship": {"type": "string", "max_length": 1000},
                    }
                },
                "outline": {
                    "fields": {
                        "premise": {"type": "string", "max_length": 4000},
                        "beginning": {"type": "string", "max_length": 4000},
                        "middle": {"type": "string", "max_length": 4000},
                        "ending": {"type": "string", "max_length": 4000},
                        "episode_summaries": {"type": "array", "items": {"type": "string", "max_length": 4000}},
                    }
                },
                "script_structure": {"blocks": ["SCENE_HEADING", "ACTION", "CHARACTER", "DIALOGUE"]},
                "output_formats": ["markdown", "docx"],
            },
        },
    ]


def _definition_fields(definition: dict[str, Any], section: str) -> dict[str, Any]:
    aliases = {
        "settings": ("settings", "settings_schema", "settings_fields", "work_settings"),
        "brief": ("creative_brief", "creative_brief_fields", "brief_fields"),
        "characters": ("characters", "character_fields"),
        "outline": ("outline", "outline_fields"),
    }
    value = next((definition[key] for key in aliases[section] if key in definition), {})
    if isinstance(value, list):
        fields: dict[str, Any] = {}
        for field in value:
            if isinstance(field, str):
                fields[field] = {"type": "string"}
            elif isinstance(field, dict) and isinstance(field.get("key", field.get("name")), str):
                key = field.get("key", field.get("name"))
                fields[key] = {name: item for name, item in field.items() if name not in {"key", "name"}}
            else:
                raise _error("INVALID_TEMPLATE_DEFINITION", f"{section} contains an invalid field definition", status.HTTP_422_UNPROCESSABLE_CONTENT)
        return fields
    if not isinstance(value, dict):
        return {}
    if section == "characters":
        value = value.get("item_fields", value.get("fields", value))
    elif "fields" in value and isinstance(value["fields"], dict):
        value = value["fields"]
    if isinstance(value, list):
        fields: dict[str, Any] = {}
        for field in value:
            if isinstance(field, str):
                fields[field] = {"type": "string"}
            elif isinstance(field, dict) and isinstance(field.get("key", field.get("name")), str):
                key = field.get("key", field.get("name"))
                fields[key] = {name: item for name, item in field.items() if name not in {"key", "name"}}
            else:
                raise _error("INVALID_TEMPLATE_DEFINITION", f"{section} contains an invalid field definition", status.HTTP_422_UNPROCESSABLE_CONTENT)
        value = fields
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, rule in value.items():
        result[key] = {"type": rule} if isinstance(rule, str) else (rule if isinstance(rule, dict) else {"type": "string"})
    return result


def _validate_value(value: Any, rule: Any, path: str) -> None:
    if isinstance(rule, str):
        rule = {"type": rule}
    if not isinstance(rule, dict):
        rule = {"type": "string"}
    kind = rule.get("type", "string")
    minimum = rule.get("min", rule.get("minimum"))
    maximum = rule.get("max", rule.get("maximum"))
    if kind in {"string", "text"}:
        if not isinstance(value, str):
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} must be a string", status.HTTP_422_UNPROCESSABLE_CONTENT)
        max_length = rule.get("max_length", rule.get("maxLength"))
        if max_length is not None and len(value) > max_length:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} is too long", status.HTTP_422_UNPROCESSABLE_CONTENT)
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} must be an integer", status.HTTP_422_UNPROCESSABLE_CONTENT)
        if minimum is not None and value < minimum:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} is below the minimum", status.HTTP_422_UNPROCESSABLE_CONTENT)
        if maximum is not None and value > maximum:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} is above the maximum", status.HTTP_422_UNPROCESSABLE_CONTENT)
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} must be numeric", status.HTTP_422_UNPROCESSABLE_CONTENT)
        if minimum is not None and value < minimum:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} is below the minimum", status.HTTP_422_UNPROCESSABLE_CONTENT)
        if maximum is not None and value > maximum:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} is above the maximum", status.HTTP_422_UNPROCESSABLE_CONTENT)
    elif kind in {"boolean", "bool"} and not isinstance(value, bool):
        raise _error("INVALID_TEMPLATE_VALUE", f"{path} must be a boolean", status.HTTP_422_UNPROCESSABLE_CONTENT)
    elif kind in {"array", "list"}:
        if not isinstance(value, list):
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} must be a list", status.HTTP_422_UNPROCESSABLE_CONTENT)
        min_items = rule.get("min_items", rule.get("minItems"))
        if min_items is not None and len(value) < min_items:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} has too few items", status.HTTP_422_UNPROCESSABLE_CONTENT)
        for index, item in enumerate(value):
            if "items" in rule:
                _validate_value(item, rule["items"], f"{path}[{index}]")
    elif kind in {"object", "dict"}:
        fields = rule.get("fields", rule.get("properties", {}))
        _validate_object(value, fields if isinstance(fields, dict) else {}, path)
    elif kind == "enum":
        if value not in rule.get("values", []):
            raise _error("INVALID_TEMPLATE_VALUE", f"{path} is not an allowed value", status.HTTP_422_UNPROCESSABLE_CONTENT)


def _validate_object(value: Any, fields: dict[str, Any], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error("INVALID_TEMPLATE_VALUE", f"{path} must be an object", status.HTTP_422_UNPROCESSABLE_CONTENT)
    unknown = set(value) - set(fields)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise _error("INVALID_TEMPLATE_FIELD", f"{path} contains unsupported fields: {names}", status.HTTP_422_UNPROCESSABLE_CONTENT)
    for name, rule in fields.items():
        if isinstance(rule, dict) and rule.get("required") and name not in value:
            raise _error("INVALID_TEMPLATE_VALUE", f"{path}.{name} is required", status.HTTP_422_UNPROCESSABLE_CONTENT)
        if name in value:
            _validate_value(value[name], rule, f"{path}.{name}")
    return value


def _validate_settings(template_version: ScriptTemplateVersion | None, settings: dict[str, Any]) -> dict[str, Any]:
    if template_version is None:
        raise _error("TEMPLATE_REQUIRED", "the work has no template snapshot", status.HTTP_409_CONFLICT)
    return _validate_object(settings, _definition_fields(template_version.definition_json, "settings"), "settings")


def _validate_template_definition(definition: dict[str, Any]) -> None:
    required_sections = ("settings", "brief", "characters", "outline")
    missing = [section for section in required_sections if not _definition_fields(definition, section)]
    structure = next((definition[key] for key in ("script_structure", "script_blocks", "body_structure") if key in definition), None)
    if missing or not structure:
        raise _error(
            "INVALID_TEMPLATE_DEFINITION",
            "template must define settings, creative brief, characters, outline, and script structure",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    formats = next((definition[key] for key in ("output_formats", "output_format", "formats") if key in definition), None)
    if not formats:
        raise _error(
            "INVALID_TEMPLATE_DEFINITION",
            "template must define output formats",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


def _validate_creative(template_version: ScriptTemplateVersion | None, stage: str, content: Any) -> Any:
    if stage not in _CREATIVE_STAGES:
        raise _error("INVALID_STAGE", "creative stage is invalid", status.HTTP_422_UNPROCESSABLE_CONTENT)
    if template_version is None:
        raise _error("TEMPLATE_REQUIRED", "the work has no template snapshot", status.HTTP_409_CONFLICT)
    fields = _definition_fields(template_version.definition_json, stage)
    if stage == "characters":
        if not isinstance(content, list):
            raise _error("INVALID_TEMPLATE_VALUE", "characters must be a list", status.HTTP_422_UNPROCESSABLE_CONTENT)
        return [_validate_object(item, fields, f"characters[{index}]") for index, item in enumerate(content)]
    return _validate_object(content, fields, stage)


def _published_template_version(db: Session, template_id: str | None = None, version_id: str | None = None) -> ScriptTemplateVersion:
    if version_id:
        version = db.get(ScriptTemplateVersion, version_id)
        if version is None or version.status != "PUBLISHED":
            raise _error("TEMPLATE_NOT_FOUND", "published template version not found", status.HTTP_404_NOT_FOUND)
        if template_id and version.template_id != template_id:
            raise _error("TEMPLATE_VERSION_MISMATCH", "template and template version do not match", status.HTTP_422_UNPROCESSABLE_CONTENT)
        return version
    query = select(ScriptTemplateVersion).join(ScriptTemplate).where(ScriptTemplateVersion.status == "PUBLISHED")
    if template_id:
        query = query.where(ScriptTemplateVersion.template_id == template_id)
    else:
        query = query.where(ScriptTemplate.slug == "vertical-micro-drama")
    version = db.scalars(query.order_by(ScriptTemplateVersion.version.desc(), ScriptTemplateVersion.created_at.desc())).first()
    if version is None:
        raise _error("TEMPLATE_NOT_FOUND", "published template not found", status.HTTP_404_NOT_FOUND)
    return version


def _template_version_response(version: ScriptTemplateVersion) -> TemplateVersionResponse:
    return TemplateVersionResponse(
        id=version.id,
        template_id=version.template_id,
        version=version.version,
        status=version.status,
        definition=version.definition_json,
        created_at=version.created_at,
        published_at=version.published_at,
    )


def _template_response(template: ScriptTemplate, include_versions: bool = False) -> TemplateResponse:
    published = next((version for version in reversed(template.versions) if version.status == "PUBLISHED"), None)
    return TemplateResponse(
        id=template.id,
        slug=template.slug,
        name=template.name,
        status="PUBLISHED" if published else "DRAFT",
        created_at=template.created_at,
        updated_at=template.updated_at,
        published_version=_template_version_response(published) if published else None,
        versions=[_template_version_response(version) for version in template.versions] if include_versions else [],
    )


def _creative_data(work: Work) -> WorkCreativeData:
    if work.creative_data is None:
        work.creative_data = WorkCreativeData(work_id=work.id)
    return work.creative_data


def _stage_revision(data: WorkCreativeData, stage: str) -> int:
    return getattr(data, f"{stage}_revision")


def _set_stage(data: WorkCreativeData, stage: str, content: Any, *, confirmed: bool = False) -> None:
    setattr(data, f"{stage}_json", deepcopy(content))
    if confirmed:
        setattr(data, f"{stage}_confirmed_json", deepcopy(content))


def _creative_response(work: Work) -> CreativeResponse:
    data = _creative_data(work)
    return CreativeResponse(
        work_id=work.id,
        brief=data.brief_json,
        characters=data.characters_json,
        outline=data.outline_json,
        confirmed={stage: getattr(data, f"{stage}_confirmed_json") for stage in _CREATIVE_STAGES},
        revisions={stage: _stage_revision(data, stage) for stage in _CREATIVE_STAGES},
    )


def _proposal_response(proposal: ChangeProposal) -> ChangeProposalResponse:
    return ChangeProposalResponse(
        id=proposal.id,
        work_id=proposal.work_id,
        stage=proposal.stage,
        content=proposal.content_json,
        base_revision=proposal.base_revision,
        status=proposal.status,
        submitted_by_user_id=proposal.submitted_by_user_id,
        reviewed_by_user_id=proposal.reviewed_by_user_id,
        rejection_reason=proposal.rejection_reason,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
    )


def _audit(db: Session, event_type: str, actor: User | None = None, target: User | None = None, metadata: dict | None = None) -> None:
    db.add(
        AuditLog(
            event_type=event_type,
            actor_user_id=actor.id if actor else None,
            target_user_id=target.id if target else None,
            metadata_json=metadata or {},
        )
    )


def _ensure_builtin_templates(db: Session) -> None:
    for builtin in _builtin_template_definitions():
        template = db.scalar(select(ScriptTemplate).where(ScriptTemplate.slug == builtin["slug"]))
        if template is not None:
            continue
        template = ScriptTemplate(slug=builtin["slug"], name=builtin["name"], status="ACTIVE")
        db.add(template)
        db.flush()
        db.add(
            ScriptTemplateVersion(
                template_id=template.id,
                version=1,
                status="PUBLISHED",
                definition_json=builtin["definition"],
                published_at=datetime.now(timezone.utc),
            )
        )
    db.commit()


def _public_user(user: User) -> UserResponse:
    return UserResponse.model_validate(user)


def _error(code: str, detail: str, http_status: int) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": detail})


def _find_user(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def _get_user_by_id(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise _error("USER_NOT_FOUND", "user not found", status.HTTP_404_NOT_FOUND)
    return user


def _get_work(db: Session, work_id: str) -> Work:
    work = db.get(Work, work_id)
    if work is None:
        raise _error("WORK_NOT_FOUND", "work not found", status.HTTP_404_NOT_FOUND)
    return work


def _resolve_user_target(db: Session, user_id: str | None, username: str | None) -> User:
    target = _find_user(db, username) if username else db.get(User, user_id)
    if target is None:
        raise _error("USER_NOT_FOUND", "user not found", status.HTTP_404_NOT_FOUND)
    return target


def _require_work_member(
    db: Session,
    user: User,
    work_id: str,
    roles: set[str] | None = None,
    *,
    allow_trashed: bool = False,
) -> tuple[Work, WorkMember]:
    work = _get_work(db, work_id)
    if work.status == "TRASHED" and not allow_trashed:
        raise _error("WORK_NOT_FOUND", "work not found", status.HTTP_404_NOT_FOUND)
    member = db.scalar(
        select(WorkMember).where(
            WorkMember.work_id == work.id,
            WorkMember.user_id == user.id,
            WorkMember.status == "ACCEPTED",
        )
    )
    if member is None or (roles is not None and member.role not in roles):
        raise _error("WORK_ACCESS_DENIED", "work access is not allowed", status.HTTP_403_FORBIDDEN)
    if roles == {"OWNER"} and work.owner_id != user.id:
        raise _error("WORK_ACCESS_DENIED", "work access is not allowed", status.HTTP_403_FORBIDDEN)
    return work, member


def _work_response(work: Work) -> WorkResponse:
    return WorkResponse(
        id=work.id,
        title=work.title,
        owner_id=work.owner_id,
        status=work.status,
        revision=work.revision,
        template_version_id=work.template_version_id,
        settings=work.settings_json or {},
        created_at=work.created_at,
        updated_at=work.updated_at,
    )


def _member_response(member: WorkMember) -> WorkMemberResponse:
    return WorkMemberResponse(
        id=member.id,
        work_id=member.work_id,
        user_id=member.user_id,
        username=member.user.username,
        role=member.role,
        status=member.status,
        invited_at=member.invited_at,
        accepted_at=member.accepted_at,
        revoked_at=member.revoked_at,
    )


def _work_detail_response(work: Work, member: WorkMember) -> WorkDetailResponse:
    members = work.members if member.role == "OWNER" else [item for item in work.members if item.status == "ACCEPTED"]
    return WorkDetailResponse(**_work_response(work).model_dump(), members=[_member_response(item) for item in members])


def _cookie_options(settings: Settings) -> dict:
    return {
        "key": settings.session_cookie_name,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "max_age": settings.session_ttl_seconds,
        "path": "/",
    }


def _check_login_rate_limit(request: Request, username: str, settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    key = (request.client.host if request.client else "unknown", username.lower())
    attempts = [stamp for stamp in _failed_logins.get(key, []) if (now - stamp).total_seconds() < settings.login_window_seconds]
    _failed_logins[key] = attempts
    if len(attempts) >= settings.login_max_attempts:
        raise _error("LOGIN_RATE_LIMITED", "too many failed login attempts", status.HTTP_429_TOO_MANY_REQUESTS)


def _record_failed_login(request: Request, username: str) -> None:
    key = (request.client.host if request.client else "unknown", username.lower())
    _failed_logins.setdefault(key, []).append(datetime.now(timezone.utc))


def _clear_failed_logins(request: Request, username: str) -> None:
    key = (request.client.host if request.client else "unknown", username.lower())
    _failed_logins.pop(key, None)


def _check_csrf(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    session_cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
    if not session_cookie:
        return
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    if origin and origin.rstrip("/") != expected_origin.rstrip("/"):
        raise _error("CSRF_ORIGIN_REJECTED", "request origin is not allowed", status.HTTP_403_FORBIDDEN)
    csrf_cookie = request.cookies.get(request.app.state.settings.csrf_cookie_name)
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_cookie or csrf_cookie != csrf_header:
        raise _error("CSRF_TOKEN_REQUIRED", "a matching CSRF token is required", status.HTTP_403_FORBIDDEN)


def _expected_revision(request: Request, payload: Any = None) -> int | None:
    value = request.headers.get("if-match")
    if value is None and isinstance(payload, dict):
        value = payload.get("expected_revision")
    if value is None:
        return None
    try:
        return int(str(value).strip('"'))
    except (TypeError, ValueError):
        raise _error("INVALID_REVISION", "expected revision must be an integer", status.HTTP_422_UNPROCESSABLE_CONTENT)


def _load_session(request: Request, db: Session) -> tuple[SessionRecord, User] | None:
    token = request.cookies.get(request.app.state.settings.session_cookie_name)
    if not token:
        return None
    record = db.scalar(select(SessionRecord).where(SessionRecord.token_hash == session_token_hash(token)))
    if record is None or record.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        record.revoked_at = now
        db.commit()
        return None
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    return record, user


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    loaded = _load_session(request, db)
    if loaded is None:
        raise _error("UNAUTHENTICATED", "authentication required", status.HTTP_401_UNAUTHORIZED)
    return loaded[1]


def active_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.must_change_password:
        raise _error("PASSWORD_CHANGE_REQUIRED", "change the initial password before continuing", status.HTTP_403_FORBIDDEN)
    return user


def admin_user(request: Request, user: Annotated[User, Depends(active_user)]) -> User:
    _check_csrf(request)
    if user.role != "ADMIN":
        raise _error("FORBIDDEN", "administrator role required", status.HTTP_403_FORBIDDEN)
    return user


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    if settings.auto_create_schema and settings.environment != "production":
        Base.metadata.create_all(bind=app.state.engine)
    with app.state.session_factory() as db:
        _ensure_builtin_templates(db)
    if settings.bootstrap_admin_username and settings.bootstrap_admin_password:
        with app.state.session_factory() as db:
            if db.scalar(select(User).where(User.role == "ADMIN")) is None:
                admin = User(
                    username=settings.bootstrap_admin_username,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role="ADMIN",
                    must_change_password=True,
                )
                db.add(admin)
                db.flush()
                _audit(db, "ADMIN_BOOTSTRAPPED", target=admin)
                db.commit()
    yield
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    engine = build_engine(app_settings.database_url)
    factory = build_session_factory(engine)
    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.settings = app_settings

    @app.get("/healthz", response_model=HealthResponse)
    def healthz(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
        db.execute(text("SELECT 1"))
        return HealthResponse(status="ok")

    @app.get("/api/v1/templates", response_model=list[TemplateResponse])
    def list_published_templates(_: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> list[TemplateResponse]:
        templates = db.scalars(select(ScriptTemplate).order_by(ScriptTemplate.created_at)).all()
        return [_template_response(template) for template in templates if any(version.status == "PUBLISHED" for version in template.versions)]

    @app.get("/api/v1/templates/{template_id}", response_model=TemplateResponse)
    def get_published_template(template_id: str, _: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> TemplateResponse:
        template = db.get(ScriptTemplate, template_id)
        if template is None or not any(version.status == "PUBLISHED" for version in template.versions):
            raise _error("TEMPLATE_NOT_FOUND", "template not found", status.HTTP_404_NOT_FOUND)
        return _template_response(template)

    @app.post("/api/v1/admin/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
    def create_template(
        payload: TemplateCreateRequest,
        actor: Annotated[User, Depends(admin_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> TemplateResponse:
        _validate_template_definition(payload.definition)
        template = ScriptTemplate(slug=payload.slug or payload.key or "", name=payload.name, created_by_user_id=actor.id)
        db.add(template)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise _error("TEMPLATE_SLUG_TAKEN", "template slug is already in use", status.HTTP_409_CONFLICT)
        db.add(
            ScriptTemplateVersion(
                template_id=template.id,
                version=1,
                status="DRAFT",
                definition_json=payload.definition,
                created_by_user_id=actor.id,
            )
        )
        _audit(db, "SCRIPT_TEMPLATE_CREATED", actor=actor, metadata={"template_id": template.id, "slug": template.slug})
        db.commit()
        return _template_response(template, include_versions=True)

    @app.get("/api/v1/admin/templates", response_model=list[TemplateResponse])
    def list_admin_templates(_: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> list[TemplateResponse]:
        return [_template_response(template, include_versions=True) for template in db.scalars(select(ScriptTemplate).order_by(ScriptTemplate.created_at)).all()]

    @app.patch("/api/v1/admin/templates/{template_id}", response_model=TemplateResponse)
    def update_template(
        template_id: str,
        payload: TemplateUpdateRequest,
        actor: Annotated[User, Depends(admin_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> TemplateResponse:
        template = db.get(ScriptTemplate, template_id)
        if template is None:
            raise _error("TEMPLATE_NOT_FOUND", "template not found", status.HTTP_404_NOT_FOUND)
        latest = max(template.versions, key=lambda version: version.version, default=None)
        if latest is None:
            raise _error("TEMPLATE_VERSION_NOT_FOUND", "template version not found", status.HTTP_404_NOT_FOUND)
        if latest.status == "DRAFT":
            draft = latest
        else:
            draft = ScriptTemplateVersion(
                template_id=template.id,
                version=latest.version + 1,
                status="DRAFT",
                definition_json=deepcopy(latest.definition_json),
                created_by_user_id=actor.id,
            )
            db.add(draft)
        if payload.name is not None:
            template.name = payload.name
        if payload.definition is not None:
            draft.definition_json = {**(draft.definition_json or {}), **payload.definition}
        _validate_template_definition(draft.definition_json)
        _audit(db, "SCRIPT_TEMPLATE_UPDATED", actor=actor, metadata={"template_id": template.id, "version": draft.version})
        db.commit()
        return _template_response(template, include_versions=True)

    @app.post("/api/v1/admin/templates/{template_id}/publish", response_model=TemplateVersionResponse)
    def publish_template(
        template_id: str,
        actor: Annotated[User, Depends(admin_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> TemplateVersionResponse:
        template = db.get(ScriptTemplate, template_id)
        if template is None:
            raise _error("TEMPLATE_NOT_FOUND", "template not found", status.HTTP_404_NOT_FOUND)
        draft = max((version for version in template.versions if version.status == "DRAFT"), key=lambda version: version.version, default=None)
        if draft is None:
            raise _error("TEMPLATE_ALREADY_PUBLISHED", "there is no draft template version to publish", status.HTTP_409_CONFLICT)
        _validate_template_definition(draft.definition_json)
        for version in template.versions:
            if version.status == "PUBLISHED":
                version.status = "ARCHIVED"
        draft.status = "PUBLISHED"
        draft.published_at = datetime.now(timezone.utc)
        template.status = "ACTIVE"
        _audit(db, "SCRIPT_TEMPLATE_PUBLISHED", actor=actor, metadata={"template_id": template.id, "version": draft.version})
        db.commit()
        return _template_version_response(draft)

    @app.post("/api/v1/works", response_model=WorkResponse, status_code=status.HTTP_201_CREATED)
    def create_work(
        payload: WorkCreateRequest,
        user: Annotated[User, Depends(active_user)],
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkResponse:
        _check_csrf(request)
        now = datetime.now(timezone.utc)
        template_version = _published_template_version(db, payload.template_id, payload.template_version_id)
        settings = _validate_settings(template_version, payload.settings)
        work = Work(
            title=payload.title,
            owner_id=user.id,
            template_version_id=template_version.id,
            settings_json=deepcopy(settings),
        )
        db.add(work)
        db.flush()
        work.creative_data = WorkCreativeData(work_id=work.id)
        db.add(
            WorkMember(
                work_id=work.id,
                user_id=user.id,
                role="OWNER",
                status="ACCEPTED",
                accepted_at=now,
            )
        )
        _audit(
            db,
            "WORK_CREATED",
            actor=user,
            metadata={"work_id": work.id, "template_version_id": template_version.id},
        )
        db.commit()
        return _work_response(work)

    @app.get("/api/v1/works", response_model=list[WorkResponse])
    def list_works(user: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> list[WorkResponse]:
        works = db.scalars(
            select(Work)
            .join(WorkMember, WorkMember.work_id == Work.id)
            .where(
                WorkMember.user_id == user.id,
                WorkMember.status == "ACCEPTED",
                Work.status == "ACTIVE",
            )
            .order_by(Work.created_at)
        ).all()
        return [_work_response(work) for work in works]

    @app.get("/api/v1/works/{work_id}", response_model=WorkDetailResponse)
    def get_work(work_id: str, user: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> WorkDetailResponse:
        work, member = _require_work_member(db, user, work_id)
        return _work_detail_response(work, member)

    @app.get("/api/v1/works/{work_id}/settings", response_model=dict[str, Any])
    def get_work_settings(work_id: str, user: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
        work, _ = _require_work_member(db, user, work_id)
        return work.settings_json or {}

    @app.patch("/api/v1/works/{work_id}/settings", response_model=WorkResponse)
    @app.put("/api/v1/works/{work_id}/settings", response_model=WorkResponse, include_in_schema=False)
    def update_work_settings(
        work_id: str,
        request: Request,
        payload: Any = Body(...),
        owner: Annotated[User, Depends(active_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ) -> WorkResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        incoming_settings = payload.get("settings", payload) if isinstance(payload, dict) else payload
        expected = _expected_revision(request, payload)
        if expected is not None and expected != work.revision:
            raise _error("REVISION_CONFLICT", "work revision has changed", status.HTTP_409_CONFLICT)
        if not isinstance(incoming_settings, dict):
            raise _error("INVALID_TEMPLATE_VALUE", "settings must be an object", status.HTTP_422_UNPROCESSABLE_CONTENT)
        settings = _validate_settings(work.template_version, {**(work.settings_json or {}), **incoming_settings})
        work.settings_json = deepcopy(settings)
        work.revision += 1
        invalidate_work_external_state(db, work.id)
        work.updated_at = datetime.now(timezone.utc)
        _audit(db, "WORK_SETTINGS_UPDATED", actor=owner, metadata={"work_id": work.id})
        db.commit()
        return _work_response(work)

    def _normalize_stage(stage: str) -> str:
        aliases = {"creative_brief": "brief", "character": "characters", "story_outline": "outline"}
        normalized = aliases.get(stage, stage)
        if normalized not in _CREATIVE_STAGES:
            raise _error("INVALID_STAGE", "creative stage is invalid", status.HTTP_422_UNPROCESSABLE_CONTENT)
        return normalized

    @app.get("/api/v1/works/{work_id}/creative", response_model=CreativeResponse)
    def get_creative(work_id: str, user: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> CreativeResponse:
        work, _ = _require_work_member(db, user, work_id)
        return _creative_response(work)

    @app.get("/api/v1/works/{work_id}/creative/{stage}", response_model=dict[str, Any])
    @app.get("/api/v1/works/{work_id}/creative-{stage}", response_model=dict[str, Any], include_in_schema=False)
    def get_creative_stage(work_id: str, stage: str, user: Annotated[User, Depends(active_user)], db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
        stage = _normalize_stage(stage)
        work, _ = _require_work_member(db, user, work_id)
        data = _creative_data(work)
        return {
            "work_id": work.id,
            "stage": stage,
            "draft": getattr(data, f"{stage}_json"),
            "confirmed": getattr(data, f"{stage}_confirmed_json"),
            "revision": _stage_revision(data, stage),
        }

    @app.patch("/api/v1/works/{work_id}/creative/{stage}", response_model=dict[str, Any])
    @app.put("/api/v1/works/{work_id}/creative/{stage}", response_model=dict[str, Any], include_in_schema=False)
    @app.patch("/api/v1/works/{work_id}/creative-{stage}", response_model=dict[str, Any], include_in_schema=False)
    @app.put("/api/v1/works/{work_id}/creative-{stage}", response_model=dict[str, Any], include_in_schema=False)
    def update_creative_stage(
        work_id: str,
        stage: str,
        request: Request,
        payload: Any = Body(...),
        user: Annotated[User, Depends(active_user)] = None,
        db: Annotated[Session, Depends(get_db)] = None,
    ) -> dict[str, Any]:
        _check_csrf(request)
        stage = _normalize_stage(stage)
        work, _ = _require_work_member(db, user, work_id, {"OWNER", "EDITOR"})
        expected = _expected_revision(request, payload)
        if expected is not None and expected != work.revision:
            raise _error("REVISION_CONFLICT", "work revision has changed", status.HTTP_409_CONFLICT)
        if isinstance(payload, dict) and "content" in payload and set(payload) <= {"content", "expected_revision"}:
            content = payload["content"]
        elif isinstance(payload, dict) and "expected_revision" in payload:
            content = {key: value for key, value in payload.items() if key != "expected_revision"}
        else:
            content = payload
        content = _validate_creative(work.template_version, stage, content)
        data = _creative_data(work)
        _set_stage(data, stage, content)
        setattr(data, f"{stage}_revision", _stage_revision(data, stage) + 1)
        work.revision += 1
        invalidate_work_external_state(db, work.id)
        work.updated_at = datetime.now(timezone.utc)
        _audit(db, "CREATIVE_DRAFT_UPDATED", actor=user, metadata={"work_id": work.id, "stage": stage})
        db.commit()
        return {
            "work_id": work.id,
            "stage": stage,
            "draft": getattr(data, f"{stage}_json"),
            "confirmed": getattr(data, f"{stage}_confirmed_json"),
            "revision": _stage_revision(data, stage),
        }

    @app.post("/api/v1/works/{work_id}/creative/{stage}/confirm", response_model=dict[str, Any])
    @app.post("/api/v1/works/{work_id}/creative-{stage}/confirm", response_model=dict[str, Any], include_in_schema=False)
    def confirm_creative_stage(
        work_id: str,
        stage: str,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> dict[str, Any]:
        _check_csrf(request)
        stage = _normalize_stage(stage)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        data = _creative_data(work)
        if getattr(data, f"{stage}_confirmed_json") is not None:
            raise _error("STAGE_ALREADY_CONFIRMED", "the creative stage is already confirmed; submit a change proposal", status.HTTP_409_CONFLICT)
        previous = {"brief": None, "characters": "brief", "outline": "characters"}[stage]
        if previous and getattr(data, f"{previous}_confirmed_json") is None:
            raise _error("STAGE_ORDER_INVALID", "creative stages must be confirmed in order", status.HTTP_409_CONFLICT)
        draft = getattr(data, f"{stage}_json")
        if draft is None:
            raise _error("CREATIVE_DRAFT_REQUIRED", "save a draft before confirming the stage", status.HTTP_409_CONFLICT)
        draft = _validate_creative(work.template_version, stage, draft)
        _set_stage(data, stage, draft, confirmed=True)
        setattr(data, f"{stage}_revision", _stage_revision(data, stage) + 1)
        work.revision += 1
        invalidate_work_external_state(db, work.id)
        work.updated_at = datetime.now(timezone.utc)
        _audit(db, "CREATIVE_STAGE_CONFIRMED", actor=owner, metadata={"work_id": work.id, "stage": stage})
        db.commit()
        return {
            "work_id": work.id,
            "stage": stage,
            "draft": getattr(data, f"{stage}_json"),
            "confirmed": getattr(data, f"{stage}_confirmed_json"),
            "revision": _stage_revision(data, stage),
        }

    @app.post("/api/v1/works/{work_id}/change-proposals", response_model=ChangeProposalResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/works/{work_id}/proposals", response_model=ChangeProposalResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
    def submit_change_proposal(
        work_id: str,
        payload: ChangeProposalRequest,
        request: Request,
        user: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> ChangeProposalResponse:
        _check_csrf(request)
        stage = _normalize_stage(payload.stage)
        work, _ = _require_work_member(db, user, work_id, {"OWNER", "EDITOR"})
        data = _creative_data(work)
        if getattr(data, f"{stage}_confirmed_json") is None:
            raise _error("SETTING_NOT_CONFIRMED", "only confirmed settings can have change proposals", status.HTTP_409_CONFLICT)
        content = _validate_creative(work.template_version, stage, payload.content)
        existing = db.scalar(
            select(ChangeProposal).where(
                ChangeProposal.work_id == work.id,
                ChangeProposal.stage == stage,
                ChangeProposal.status == "PENDING",
            )
        )
        if existing is not None:
            raise _error("PROPOSAL_ALREADY_PENDING", "a change proposal for this setting is already pending", status.HTTP_409_CONFLICT)
        proposal = ChangeProposal(
            work_id=work.id,
            stage=stage,
            content_json=deepcopy(content),
            base_revision=_stage_revision(data, stage),
            status="PENDING",
            submitted_by_user_id=user.id,
        )
        db.add(proposal)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise _error("PROPOSAL_ALREADY_PENDING", "a change proposal for this setting is already pending", status.HTTP_409_CONFLICT)
        _audit(db, "CHANGE_PROPOSAL_SUBMITTED", actor=user, metadata={"work_id": work.id, "proposal_id": proposal.id, "stage": stage})
        db.commit()
        return _proposal_response(proposal)

    @app.get("/api/v1/works/{work_id}/change-proposals", response_model=list[ChangeProposalResponse])
    @app.get("/api/v1/works/{work_id}/proposals", response_model=list[ChangeProposalResponse], include_in_schema=False)
    def list_change_proposals(
        work_id: str,
        user: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> list[ChangeProposalResponse]:
        work, _ = _require_work_member(db, user, work_id)
        proposals = db.scalars(
            select(ChangeProposal).where(ChangeProposal.work_id == work.id).order_by(ChangeProposal.created_at)
        ).all()
        return [_proposal_response(proposal) for proposal in proposals]

    def _review_proposal(
        work_id: str,
        proposal_id: str,
        request: Request,
        owner: User,
        db: Session,
        approved: bool,
    ) -> ChangeProposalResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        proposal = db.scalar(
            select(ChangeProposal).where(ChangeProposal.id == proposal_id, ChangeProposal.work_id == work.id)
        )
        if proposal is None or proposal.status != "PENDING":
            raise _error("PROPOSAL_NOT_FOUND", "pending change proposal not found", status.HTTP_404_NOT_FOUND)
        data = _creative_data(work)
        if proposal.base_revision != _stage_revision(data, proposal.stage):
            proposal.status = "STALE"
            proposal.reviewed_by_user_id = owner.id
            proposal.reviewed_at = datetime.now(timezone.utc)
            proposal.rejection_reason = "the setting changed after submission"
            _audit(
                db,
                "CHANGE_PROPOSAL_STALE",
                actor=owner,
                metadata={"work_id": work.id, "proposal_id": proposal.id, "stage": proposal.stage},
            )
            db.commit()
            raise _error("PROPOSAL_BASE_CHANGED", "the setting changed after this proposal was submitted; submit it again", status.HTTP_409_CONFLICT)
        proposal.reviewed_by_user_id = owner.id
        proposal.reviewed_at = datetime.now(timezone.utc)
        if approved:
            content = _validate_creative(work.template_version, proposal.stage, proposal.content_json)
            _set_stage(data, proposal.stage, content, confirmed=True)
            setattr(data, f"{proposal.stage}_revision", _stage_revision(data, proposal.stage) + 1)
            proposal.status = "APPROVED"
            work.revision += 1
            invalidate_work_external_state(db, work.id)
            work.updated_at = datetime.now(timezone.utc)
            event_type = "CHANGE_PROPOSAL_APPROVED"
        else:
            proposal.status = "REJECTED"
            event_type = "CHANGE_PROPOSAL_REJECTED"
        _audit(db, event_type, actor=owner, metadata={"work_id": work.id, "proposal_id": proposal.id, "stage": proposal.stage})
        db.commit()
        return _proposal_response(proposal)

    @app.post("/api/v1/works/{work_id}/change-proposals/{proposal_id}/approve", response_model=ChangeProposalResponse)
    @app.post("/api/v1/works/{work_id}/proposals/{proposal_id}/approve", response_model=ChangeProposalResponse, include_in_schema=False)
    def approve_change_proposal(
        work_id: str,
        proposal_id: str,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> ChangeProposalResponse:
        return _review_proposal(work_id, proposal_id, request, owner, db, True)

    @app.post("/api/v1/works/{work_id}/change-proposals/{proposal_id}/reject", response_model=ChangeProposalResponse)
    @app.post("/api/v1/works/{work_id}/proposals/{proposal_id}/reject", response_model=ChangeProposalResponse, include_in_schema=False)
    def reject_change_proposal(
        work_id: str,
        proposal_id: str,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> ChangeProposalResponse:
        return _review_proposal(work_id, proposal_id, request, owner, db, False)

    @app.post("/api/v1/works/{work_id}/members/invitations", response_model=WorkMemberResponse, status_code=status.HTTP_201_CREATED)
    def invite_member(
        work_id: str,
        payload: InviteMemberRequest,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkMemberResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        target = _resolve_user_target(db, payload.user_id, payload.username)
        if not target.is_active:
            raise _error("ACCOUNT_DISABLED", "account is disabled", status.HTTP_409_CONFLICT)
        if target.id == owner.id:
            raise _error("MEMBER_ALREADY_EXISTS", "the owner is already a member", status.HTTP_409_CONFLICT)
        member = db.scalar(select(WorkMember).where(WorkMember.work_id == work.id, WorkMember.user_id == target.id))
        if member is not None and member.status == "ACCEPTED":
            raise _error("MEMBER_ALREADY_EXISTS", "user is already a member", status.HTTP_409_CONFLICT)
        if member is not None and member.status == "PENDING":
            raise _error("INVITATION_ALREADY_PENDING", "an invitation is already pending", status.HTTP_409_CONFLICT)
        now = datetime.now(timezone.utc)
        if member is None:
            member = WorkMember(work_id=work.id, user_id=target.id)
            db.add(member)
        member.role = payload.role
        member.status = "PENDING"
        member.invited_by_user_id = owner.id
        member.invited_at = now
        member.accepted_at = None
        member.revoked_at = None
        db.flush()
        _audit(
            db,
            "WORK_MEMBER_INVITED",
            actor=owner,
            target=target,
            metadata={"work_id": work.id, "member_id": member.id, "role": member.role},
        )
        db.commit()
        return _member_response(member)

    def _accept_member_invitation(work_id: str, user: User, db: Session, request: Request, member_user_id: str | None = None) -> WorkMemberResponse:
        _check_csrf(request)
        work = _get_work(db, work_id)
        if work.status == "TRASHED":
            raise _error("WORK_NOT_FOUND", "work not found", status.HTTP_404_NOT_FOUND)
        if member_user_id is not None and member_user_id != user.id:
            raise _error("WORK_ACCESS_DENIED", "only the invited user can accept the invitation", status.HTTP_403_FORBIDDEN)
        member = db.scalar(
            select(WorkMember).where(
                WorkMember.work_id == work.id,
                WorkMember.user_id == user.id,
                WorkMember.status == "PENDING",
            )
        )
        if member is None:
            raise _error("INVITATION_NOT_FOUND", "pending invitation not found", status.HTTP_404_NOT_FOUND)
        member.status = "ACCEPTED"
        member.accepted_at = datetime.now(timezone.utc)
        member.revoked_at = None
        _audit(
            db,
            "WORK_MEMBER_ACCEPTED",
            actor=user,
            target=user,
            metadata={"work_id": work.id, "member_id": member.id, "role": member.role},
        )
        db.commit()
        return _member_response(member)

    @app.post("/api/v1/works/{work_id}/members/accept", response_model=WorkMemberResponse)
    def accept_member_invitation(
        work_id: str,
        request: Request,
        user: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkMemberResponse:
        return _accept_member_invitation(work_id, user, db, request)

    @app.post("/api/v1/works/{work_id}/members/{member_user_id}/accept", response_model=WorkMemberResponse)
    def accept_member_invitation_for_user(
        work_id: str,
        member_user_id: str,
        request: Request,
        user: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkMemberResponse:
        return _accept_member_invitation(work_id, user, db, request, member_user_id)

    @app.post("/api/v1/works/{work_id}/members/{member_user_id}/revoke", response_model=WorkMemberResponse)
    def revoke_member(
        work_id: str,
        member_user_id: str,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkMemberResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        member = db.scalar(select(WorkMember).where(WorkMember.work_id == work.id, WorkMember.user_id == member_user_id))
        if member is None or member.role == "OWNER":
            raise _error("MEMBER_NOT_FOUND", "member not found", status.HTTP_404_NOT_FOUND)
        if member.status == "REVOKED":
            raise _error("MEMBER_NOT_FOUND", "member not found", status.HTTP_404_NOT_FOUND)
        member.status = "REVOKED"
        member.revoked_at = datetime.now(timezone.utc)
        _audit(
            db,
            "WORK_MEMBER_REVOKED",
            actor=owner,
            target=member.user,
            metadata={"work_id": work.id, "member_id": member.id},
        )
        db.commit()
        return _member_response(member)

    @app.patch("/api/v1/works/{work_id}/members/{member_user_id}", response_model=WorkMemberResponse)
    def change_member_role(
        work_id: str,
        member_user_id: str,
        payload: MemberRoleRequest,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkMemberResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        member = db.scalar(
            select(WorkMember).where(
                WorkMember.work_id == work.id,
                WorkMember.user_id == member_user_id,
                WorkMember.status.in_(["ACCEPTED", "PENDING"]),
            )
        )
        if member is None or member.role == "OWNER":
            raise _error("MEMBER_NOT_FOUND", "member not found", status.HTTP_404_NOT_FOUND)
        old_role = member.role
        member.role = payload.role
        _audit(
            db,
            "WORK_MEMBER_ROLE_CHANGED",
            actor=owner,
            target=member.user,
            metadata={"work_id": work.id, "member_id": member.id, "from_role": old_role, "to_role": member.role},
        )
        db.commit()
        return _member_response(member)

    @app.post("/api/v1/works/{work_id}/transfer-ownership", response_model=WorkResponse)
    def transfer_ownership(
        work_id: str,
        payload: TransferOwnershipRequest,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkResponse:
        _check_csrf(request)
        work, owner_member = _require_work_member(db, owner, work_id, {"OWNER"})
        target = _resolve_user_target(db, payload.user_id, payload.username)
        if not target.is_active:
            raise _error("ACCOUNT_DISABLED", "account is disabled", status.HTTP_409_CONFLICT)
        target_member = db.scalar(
            select(WorkMember).where(
                WorkMember.work_id == work.id,
                WorkMember.user_id == target.id,
                WorkMember.status == "ACCEPTED",
            )
        )
        if target_member is None or target_member.role == "OWNER":
            raise _error("OWNERSHIP_TARGET_INVALID", "ownership can only transfer to an active member", status.HTTP_409_CONFLICT)
        now = datetime.now(timezone.utc)
        work.owner_id = target.id
        work.updated_at = now
        owner_member.role = "EDITOR"
        target_member.role = "OWNER"
        _audit(
            db,
            "WORK_OWNERSHIP_TRANSFERRED",
            actor=owner,
            target=target,
            metadata={"work_id": work.id, "from_user_id": owner.id, "to_user_id": target.id},
        )
        db.commit()
        return _work_response(work)

    @app.post("/api/v1/works/{work_id}/trash", response_model=WorkResponse)
    def trash_work(
        work_id: str,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"})
        if work.status == "TRASHED":
            raise _error("WORK_ALREADY_TRASHED", "work is already in the trash", status.HTTP_409_CONFLICT)
        now = datetime.now(timezone.utc)
        work.status = "TRASHED"
        work.trashed_at = now
        work.updated_at = now
        work.revision += 1
        invalidate_work_external_state(db, work.id, cancel_jobs=True)
        _audit(db, "WORK_TRASHED", actor=owner, metadata={"work_id": work.id})
        db.commit()
        return _work_response(work)

    @app.post("/api/v1/works/{work_id}/restore", response_model=WorkResponse)
    def restore_work(
        work_id: str,
        request: Request,
        owner: Annotated[User, Depends(active_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> WorkResponse:
        _check_csrf(request)
        work, _ = _require_work_member(db, owner, work_id, {"OWNER"}, allow_trashed=True)
        if work.status != "TRASHED":
            raise _error("WORK_NOT_TRASHED", "work is not in the trash", status.HTTP_409_CONFLICT)
        if work.trashed_at is not None:
            trashed_at = work.trashed_at if work.trashed_at.tzinfo else work.trashed_at.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - trashed_at).total_seconds() > app_settings.trash_retention_days * 86400:
                raise _error("TRASH_RETENTION_EXPIRED", "the work can no longer be restored", status.HTTP_410_GONE)
        now = datetime.now(timezone.utc)
        work.status = "ACTIVE"
        work.trashed_at = None
        work.updated_at = now
        work.revision += 1
        _audit(db, "WORK_RESTORED", actor=owner, metadata={"work_id": work.id})
        db.commit()
        return _work_response(work)

    register_feature_routes(
        app,
        app_settings=app_settings,
        active_user=active_user,
        admin_user=admin_user,
        check_csrf=_check_csrf,
        require_work_member=_require_work_member,
        error=_error,
        audit=_audit,
    )

    @app.post("/api/v1/auth/login", response_model=LoginResponse)
    @app.post("/api/v1/login", response_model=LoginResponse, include_in_schema=False)
    def login(payload: LoginRequest, request: Request, response: Response, db: Annotated[Session, Depends(get_db)]) -> LoginResponse:
        _check_login_rate_limit(request, payload.username, app_settings)
        user = _find_user(db, payload.username)
        if user is None or not verify_password(user.password_hash, payload.password):
            _record_failed_login(request, payload.username)
            raise _error("INVALID_CREDENTIALS", "invalid username or password", status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            raise _error("ACCOUNT_DISABLED", "account is disabled", status.HTTP_403_FORBIDDEN)
        _clear_failed_logins(request, payload.username)
        token = new_session_token()
        db.add(SessionRecord(user_id=user.id, token_hash=session_token_hash(token), expires_at=session_expiry(app_settings.session_ttl_seconds)))
        _audit(db, "LOGIN_SUCCEEDED", target=user)
        db.commit()
        response.set_cookie(value=token, **_cookie_options(app_settings))
        response.set_cookie(
            key=app_settings.csrf_cookie_name,
            value=new_session_token(),
            httponly=False,
            secure=app_settings.cookie_secure,
            samesite="strict",
            max_age=app_settings.session_ttl_seconds,
            path="/",
        )
        return LoginResponse(user=_public_user(user), must_change_password=user.must_change_password)

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    @app.post("/api/v1/logout", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
    def logout(request: Request, response: Response, db: Annotated[Session, Depends(get_db)]) -> Response:
        _check_csrf(request)
        loaded = _load_session(request, db)
        if loaded:
            session, user = loaded
            session.revoked_at = datetime.now(timezone.utc)
            _audit(db, "LOGOUT", actor=user)
            db.commit()
        response.delete_cookie(app_settings.session_cookie_name, path="/")
        response.delete_cookie(app_settings.csrf_cookie_name, path="/")
        return response

    @app.get("/api/v1/auth/me", response_model=UserResponse)
    def me(user: Annotated[User, Depends(active_user)]) -> UserResponse:
        return _public_user(user)

    @app.post("/api/v1/auth/change-password", response_model=UserResponse)
    @app.post("/api/v1/change-password", response_model=UserResponse, include_in_schema=False)
    def change_password(payload: ChangePasswordRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        _check_csrf(request)
        user = current_user(request, db)
        if not verify_password(user.password_hash, payload.current_password):
            raise _error("INVALID_PASSWORD", "current password is invalid", status.HTTP_400_BAD_REQUEST)
        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = False
        _audit(db, "PASSWORD_CHANGED", actor=user, target=user)
        db.commit()
        return _public_user(user)

    @app.post("/api/v1/admin/bootstrap", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/setup/bootstrap", response_model=UserResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
    def bootstrap(payload: BootstrapRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        # Bootstrap is one-time, and production deployments can additionally require a setup secret.
        _check_csrf(request)
        if db.scalar(select(User).where(User.role == "ADMIN")) is not None:
            raise _error("BOOTSTRAP_COMPLETE", "an administrator already exists", status.HTTP_409_CONFLICT)
        if app_settings.environment == "production" and not app_settings.bootstrap_secret:
            raise _error("BOOTSTRAP_DISABLED", "production bootstrap requires a setup secret", status.HTTP_403_FORBIDDEN)
        if app_settings.bootstrap_secret and payload.bootstrap_token != app_settings.bootstrap_secret:
            raise _error("BOOTSTRAP_TOKEN_INVALID", "setup secret is invalid", status.HTTP_403_FORBIDDEN)
        user = User(username=payload.username, password_hash=hash_password(payload.password), role="ADMIN", must_change_password=True)
        db.add(user)
        try:
            db.flush()
            _audit(db, "ADMIN_BOOTSTRAPPED", target=user)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise _error("USERNAME_TAKEN", "username is already in use", status.HTTP_409_CONFLICT)
        return _public_user(user)

    @app.post("/api/v1/admin/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
    def create_user(payload: CreateUserRequest, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        # Cookie-authenticated mutations also accept non-browser clients without an Origin header.
        user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role, must_change_password=True)
        db.add(user)
        try:
            db.flush()
            _audit(db, "USER_CREATED", actor=actor, target=user, metadata={"role": user.role})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise _error("USERNAME_TAKEN", "username is already in use", status.HTTP_409_CONFLICT)
        return _public_user(user)

    @app.get("/api/v1/admin/users", response_model=list[UserResponse])
    def list_users(_: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> list[UserResponse]:
        return [_public_user(user) for user in db.scalars(select(User).order_by(User.created_at)).all()]

    @app.patch("/api/v1/admin/users/{user_id}/status", response_model=UserResponse)
    @app.patch("/api/v1/admin/users/{user_id}", response_model=UserResponse, include_in_schema=False)
    def set_user_status(user_id: str, payload: SetActiveRequest, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        target = _get_user_by_id(db, user_id)
        if target.id == actor.id and not payload.active:
            raise _error("SELF_DISABLE_FORBIDDEN", "an administrator cannot disable their own account", status.HTTP_400_BAD_REQUEST)
        if not payload.active and db.scalar(select(Work.id).where(Work.owner_id == target.id).limit(1)) is not None:
            raise _error("OWNER_TRANSFER_REQUIRED", "transfer work ownership before disabling this account", status.HTTP_409_CONFLICT)
        if target.role == "ADMIN" and not payload.active:
            active_admins = db.scalars(select(User.id).where(User.role == "ADMIN", User.is_active.is_(True))).all()
            if len(active_admins) <= 1:
                raise _error("LAST_ADMIN_FORBIDDEN", "the last active administrator cannot be disabled", status.HTTP_400_BAD_REQUEST)
        target.is_active = payload.active
        if not payload.active:
            now = datetime.now(timezone.utc)
            for session in target.sessions:
                session.revoked_at = now
        _audit(db, "USER_ENABLED" if payload.active else "USER_DISABLED", actor=actor, target=target)
        db.commit()
        return _public_user(target)

    @app.post("/api/v1/admin/users/{user_id}/disable", response_model=UserResponse, include_in_schema=False)
    def disable_user(user_id: str, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        return set_user_status(user_id, SetActiveRequest(active=False), actor, db)

    @app.post("/api/v1/admin/users/{user_id}/enable", response_model=UserResponse, include_in_schema=False)
    def enable_user(user_id: str, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        return set_user_status(user_id, SetActiveRequest(active=True), actor, db)

    @app.post("/api/v1/admin/users/{user_id}/reset-password", response_model=UserResponse)
    def reset_password(user_id: str, payload: ResetPasswordRequest, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        target = _get_user_by_id(db, user_id)
        target.password_hash = hash_password(payload.password)
        target.must_change_password = True
        now = datetime.now(timezone.utc)
        for session in target.sessions:
            session.revoked_at = now
        _audit(db, "PASSWORD_RESET", actor=actor, target=target)
        db.commit()
        return _public_user(target)

    @app.get("/api/v1/admin/audit", response_model=list[AuditResponse])
    def audit_logs(_: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> list[AuditResponse]:
        logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all()
        return [AuditResponse(id=log.id, event_type=log.event_type, actor_user_id=log.actor_user_id, target_user_id=log.target_user_id, metadata=log.metadata_json or {}, created_at=log.created_at) for log in logs]

    return app


app = create_app()

