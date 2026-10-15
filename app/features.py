"""Versioned scripts, direct model calls and durable MVP jobs."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.creative_calls import available_config, generation_snapshot, occupy, release, activity_response, ensure_editable, cancel_calls
from app.conversations import conversation_response as _conversation_response, register_conversation_routes
from app.config import Settings
from app.crypto import encrypt_bytes, decrypt_bytes, encrypt_secret, decrypt_secret
from app.db import get_db
from app.models import (
    AuditLog,
    BackupRecord,
    CreativeConversation,
    ExternalConsent,
    ExportJob,
    GenerationJob,
    ModelConfigVersion,
    ModelConfiguration,
    ModelInvocationRecord,
    ModelProvider,
    ScriptRelease,
    ScriptVersion,
    ScriptTemplate,
    ScriptTemplateVersion,
    User,
    Work,
    WorkCreativeData,
    WorkMember,
)
from app.model_adapters import ModelAdapterError, adapter_for, check_cancel
from app.script_generation import allowed_blocks, validate_script, validate_target, replace_target
from app.worker import GenerationWorker
from app.schemas import (
    ConversationResponse,
    DraftUpdateRequest,
    ExportJobResponse,
    ExportRequest,
    GenerationJobRequest,
    GenerationJobResponse,
    ModelConfigCreateRequest,
    ModelConfigUpdateRequest,
    ModelConfigResponse,
    ReleaseCreateRequest,
    ScriptReleaseResponse,
    ScriptVersionResponse,
    VersionCompareResponse,
    VersionCreateRequest,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _error(factory: Callable[[str, str, int], Exception], code: str, message: str, http_status: int):
    return factory(code, message, http_status)


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_script_content(content: Any) -> dict[str, Any]:
    return validate_script(content)


def _encrypt_secret(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    return encrypt_secret(value, settings.external_encryption_key)


def _crypt_payload(payload: bytes, settings: Settings) -> bytes:
    return encrypt_bytes(payload, settings.external_encryption_key)


def _decrypt_payload(payload: bytes, settings: Settings) -> bytes:
    return decrypt_bytes(payload, settings.external_encryption_key)


def _config_response(config: ModelConfigVersion) -> ModelConfigResponse:
    return ModelConfigResponse(
        id=config.id,
        model_id=config.model_id or config.id,
        display_name=config.display_name or config.model_name,
        adapter=config.adapter or config.provider.adapter,
        enabled=config.model.enabled if config.model else config.provider.enabled,
        base_url=config.endpoint,
        version=config.version,
        model_name=config.model_name,
        parameters=config.parameters_json or {},
        status=config.status,
        created_at=config.created_at,
    )


def _version_response(version: ScriptVersion) -> ScriptVersionResponse:
    return ScriptVersionResponse(
        id=version.id,
        work_id=version.work_id,
        source=version.source,
        author_user_id=version.author_user_id,
        parent_version_id=version.parent_version_id,
        content=version.content_json,
        content_hash=version.content_hash,
        work_revision=version.work_revision,
        created_at=version.created_at,
    )


def _release_response(release: ScriptRelease) -> ScriptReleaseResponse:
    return ScriptReleaseResponse(
        id=release.id,
        work_id=release.work_id,
        version_id=release.version_id,
        released_by_user_id=release.released_by_user_id,
        is_current=release.is_current,
        created_at=release.created_at,
        version=_version_response(release.version),
    )


def _job_response(job: GenerationJob) -> GenerationJobResponse:
    return GenerationJobResponse(
        id=job.id,
        work_id=job.work_id,
        requested_by_user_id=job.requested_by_user_id,
        idempotency_key=job.idempotency_key,
        input_revision=job.input_revision,
        model_config_version_id=job.model_config_version_id,
        context_summary=(job.input_snapshot_json or {}).get("summary"),
        target_type=job.target_type,
        target_path=job.target_path_json or {},
        status=job.status,
        retry_count=job.retry_count,
        error_code=job.error_code,
        error_message=job.error_message,
        result_version_id=job.result_version_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )



def _export_response(job: ExportJob, request: Request) -> ExportJobResponse:
    token = None
    if job.download_token_hash and job.download_expires_at:
        token = request.url_for("download_export", export_id=job.id)
    return ExportJobResponse(
        id=job.id,
        work_id=job.work_id,
        release_id=job.release_id,
        format=job.format,
        status=job.status,
        download_url=str(token) if token else None,
        download_expires_at=job.download_expires_at,
        error_code=job.error_code,
        created_at=job.created_at,
    )


def _script_lines(content: Any, heading: str = "") -> list[str]:
    lines: list[str] = []
    if heading:
        lines.append(heading)
    if not isinstance(content, dict):
        return lines + [str(content)]
    episodes = content.get("episodes")
    if isinstance(episodes, list):
        for episode_index, episode in enumerate(episodes, 1):
            if not isinstance(episode, dict):
                continue
            number = episode.get("number", episode_index)
            lines.append(f"## 第{number}集")
            if episode.get("title"):
                lines.append(str(episode["title"]))
            for scene_index, scene in enumerate(episode.get("scenes", []), 1):
                if not isinstance(scene, dict):
                    continue
                scene_number = scene.get("number", scene_index)
                heading_value = scene.get("heading") or scene.get("location") or "场景"
                lines.append(f"### 场景 {scene_number}：{heading_value}")
                for fragment_index, fragment in enumerate(scene.get("fragments", []), 1):
                    if not isinstance(fragment, dict):
                        continue
                    lines.append(f"#### 片段 {fragment.get('id', fragment_index)}")
                    for block in fragment.get("blocks", []):
                        if not isinstance(block, dict):
                            continue
                        block_type = str(block.get("type", "TEXT"))
                        text = block.get("text", "")
                        lines.append(f"**{block_type}** {text}".rstrip())
    else:
        for key, value in content.items():
            lines.append(f"### {key}")
            lines.append(str(value))
    return lines


def render_markdown(work: Work, content: dict[str, Any]) -> str:
    title = f"# {work.title}"
    return "\n\n".join([title, "\n".join(_script_lines(content))]) + "\n"


def render_docx(work: Work, content: dict[str, Any]) -> bytes:
    # Minimal standards-compliant DOCX package, keeping the renderer dependency-free.
    paragraphs = [work.title] + _script_lines(content)
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{_xml_escape(line)}</w:t></w:r></w:p>" for line in paragraphs)
    document = f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>{body}<w:sectPr/></w:body></w:document>"
    content_types = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>"
    rels = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/></Relationships>"
    word_rels = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>"
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/_rels/document.xml.rels", word_rels)
        archive.writestr("word/document.xml", document)
    return stream.getvalue()


def _xml_escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def invalidate_work_external_state(db: Session, work_id: str, *, cancel_jobs: bool = False) -> None:
    for consent in db.scalars(select(ExternalConsent).where(ExternalConsent.work_id == work_id, ExternalConsent.status == "ACTIVE")).all():
        consent.status = "REVOKED"
        consent.revoked_at = _now()
    if cancel_jobs:
        cancel_calls(db, work_id=work_id, code="WORK_TRASHED")
        for job in db.scalars(select(GenerationJob).where(GenerationJob.work_id == work_id, GenerationJob.status.in_(["QUEUED", "RUNNING"]))).all():
            job.status = "CANCELED"
            job.error_code = "WORK_TRASHED"
            job.finished_at = _now()
        for export in db.scalars(select(ExportJob).where(ExportJob.work_id == work_id, ExportJob.status == "SUCCEEDED")).all():
            export.status = "FAILED"
            export.error_code = "WORK_TRASHED"
            export.download_token_hash = None
            export.download_expires_at = _now()


def _backup_rows(db, model, fields):
    return [{name: (getattr(row, name).isoformat() if isinstance(getattr(row, name), datetime) else getattr(row, name)) for name in fields} for row in db.scalars(select(model)).all()]


def _restore_values(item):
    values = dict(item)
    for key in ("created_at", "updated_at", "started_at", "finished_at", "revoked_at"):
        if values.get(key):
            values[key] = datetime.fromisoformat(values[key])
    return values


def register_feature_routes(
    app: FastAPI,
    *,
    app_settings: Settings,
    active_user,
    admin_user,
    check_csrf,
    require_work_member,
    error,
    audit,
) -> None:
    """Register the remaining bounded contexts on the existing FastAPI app."""

    def _require(db: Session, user: User, work_id: str, roles: set[str] | None = None, *, allow_trashed: bool = False):
        return require_work_member(db, user, work_id, roles, allow_trashed=allow_trashed)

    def execute_queued(job_id: str):
        with app.state.session_factory() as worker_db:
            try:
                job = worker_db.get(GenerationJob, job_id)
                if job is not None and job.status == "QUEUED":
                    run_generation_job(worker_db, job)
                    worker_db.commit()
            except Exception:
                worker_db.rollback()
                job = worker_db.get(GenerationJob, job_id)
                if job is not None and job.status in {"QUEUED", "RUNNING"}:
                    job.status = "FAILED"
                    job.error_code = "WORKER_ERROR"
                    job.error_message = "generation worker could not complete the job"
                    job.finished_at = _now()
                    release(worker_db, job.work_id, job.id)
                    worker_db.commit()

    app.state.generation_worker = GenerationWorker(execute_queued)

    @app.get("/api/v1/works/{work_id}/draft")
    @app.get("/api/v1/works/{work_id}/script/draft", include_in_schema=False)
    def get_draft(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return {"work_id": work.id, "content": work.draft_json or {}, "revision": work.revision}

    @app.get("/api/v1/works/{work_id}/workspace")
    def get_workspace(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, member = _require(db, user, work_id)
        conversations = db.scalars(select(CreativeConversation).where(CreativeConversation.work_id == work.id).order_by(CreativeConversation.created_at)).all()
        versions = db.scalars(select(ScriptVersion).where(ScriptVersion.work_id == work.id).order_by(ScriptVersion.created_at)).all()
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.work_id == work.id).order_by(GenerationJob.created_at)).all()
        releases = db.scalars(select(ScriptRelease).where(ScriptRelease.work_id == work.id).order_by(ScriptRelease.created_at)).all()
        return {
            "work": {"id": work.id, "title": work.title, "revision": work.revision, "status": work.status, "role": member.role},
            "creative": {"brief": work.creative_data.brief_json if work.creative_data else None, "characters": work.creative_data.characters_json if work.creative_data else None, "outline": work.creative_data.outline_json if work.creative_data else None, "confirmed": {stage: getattr(work.creative_data, f"{stage}_confirmed_json") for stage in ("brief", "characters", "outline")} if work.creative_data else {}},
            "conversations": [_conversation_response(item).model_dump() for item in conversations],
            "draft": work.draft_json or {},
            "versions": [_version_response(item).model_dump() for item in versions],
            "generation_jobs": [_job_response(item).model_dump() for item in jobs],
            "ai_activity": activity_response(db, work.id),
            "releases": [_release_response(item).model_dump() for item in releases],
            "permissions": {"can_edit": member.role in {"OWNER", "EDITOR"}, "can_confirm": member.role == "OWNER", "can_publish": member.role == "OWNER", "can_export": member.role == "OWNER"},
        }

    @app.patch("/api/v1/works/{work_id}/draft")
    @app.put("/api/v1/works/{work_id}/draft", include_in_schema=False)
    def update_draft(work_id: str, payload: DraftUpdateRequest, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, member = _require(db, user, work_id, {"OWNER", "EDITOR"})
        try:
            ensure_editable(db, work.id)
        except ModelAdapterError as exc:
            raise error(exc.code, str(exc), 409)
        if payload.expected_revision is not None and payload.expected_revision != work.revision:
            raise error("REVISION_CONFLICT", "work revision has changed", status.HTTP_409_CONFLICT)
        try:
            content = deepcopy(validate_script(payload.content, allowed_blocks(work)))
        except ValueError as exc:
            raise error(str(exc), "script content must use episode, scene, fragment and block structure", status.HTTP_422_UNPROCESSABLE_CONTENT)
        changed = db.execute(update(Work).where(Work.id == work.id, Work.revision == work.revision, Work.status == "ACTIVE").values(draft_json=content, revision=work.revision + 1, updated_at=_now()))
        if changed.rowcount != 1:
            raise error("REVISION_CONFLICT", "work revision has changed", status.HTTP_409_CONFLICT)
        db.refresh(work)
        invalidate_work_external_state(db, work.id)
        audit(db, "WORK_DRAFT_AUTOSAVED" if payload.autosave else "WORK_DRAFT_UPDATED", actor=user, metadata={"work_id": work.id, "revision": work.revision})
        db.commit()
        return {"work_id": work.id, "content": work.draft_json, "revision": work.revision}

    def _create_version(db: Session, work: Work, user: User, content: dict[str, Any], source: str, parent_version_id: str | None = None, expected_revision: int | None = None) -> ScriptVersion:
        if source != "GENERATION":
            try:
                ensure_editable(db, work.id)
            except ModelAdapterError as exc:
                raise error(exc.code, str(exc), 409)
        if expected_revision is not None and expected_revision != work.revision:
            raise error("REVISION_CONFLICT", "work revision has changed", status.HTTP_409_CONFLICT)
        try:
            content = validate_script(content, allowed_blocks(work))
        except ValueError as exc:
            raise error(str(exc), "script content must use episode, scene, fragment and block structure", status.HTTP_422_UNPROCESSABLE_CONTENT)
        previous_revision = work.revision
        changed = db.execute(update(Work).where(Work.id == work.id, Work.revision == previous_revision, Work.status == "ACTIVE").values(draft_json=deepcopy(content), revision=previous_revision + 1, updated_at=_now()))
        if changed.rowcount != 1:
            raise error("REVISION_CONFLICT", "work revision has changed", status.HTTP_409_CONFLICT)
        db.refresh(work)
        invalidate_work_external_state(db, work.id)
        version = ScriptVersion(work_id=work.id, source=source, author_user_id=user.id, parent_version_id=parent_version_id, content_json=deepcopy(content), content_hash=_json_hash(content), work_revision=work.revision)
        db.add(version)
        db.flush()
        audit(db, "SCRIPT_VERSION_CREATED", actor=user, metadata={"work_id": work.id, "version_id": version.id, "source": source})
        return version

    @app.post("/api/v1/works/{work_id}/versions", response_model=ScriptVersionResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/works/{work_id}/script/save", response_model=ScriptVersionResponse, include_in_schema=False)
    def create_version(work_id: str, payload: VersionCreateRequest, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER"})
        content = deepcopy(payload.content if payload.content is not None else (work.draft_json or {}))
        parent = db.scalar(select(ScriptVersion).where(ScriptVersion.work_id == work.id).order_by(ScriptVersion.created_at.desc()))
        version = _create_version(db, work, user, content, "MANUAL_SAVE", parent.id if parent else None, payload.expected_revision)
        db.commit()
        return _version_response(version)

    @app.get("/api/v1/works/{work_id}/versions", response_model=list[ScriptVersionResponse])
    def list_versions(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return [_version_response(item) for item in db.scalars(select(ScriptVersion).where(ScriptVersion.work_id == work.id).order_by(ScriptVersion.created_at)).all()]

    @app.get("/api/v1/works/{work_id}/versions/compare", response_model=VersionCompareResponse)
    @app.get("/api/v1/works/{work_id}/versions/{from_version}/compare/{to_version}", response_model=VersionCompareResponse, include_in_schema=False)
    def compare_versions_static(work_id: str, from_version: str, to_version: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        left = db.scalar(select(ScriptVersion).where(ScriptVersion.id == from_version, ScriptVersion.work_id == work.id))
        right = db.scalar(select(ScriptVersion).where(ScriptVersion.id == to_version, ScriptVersion.work_id == work.id))
        if left is None or right is None:
            raise error("VERSION_NOT_FOUND", "script version not found", status.HTTP_404_NOT_FOUND)
        return VersionCompareResponse(from_version=_version_response(left), to_version=_version_response(right), changed=left.content_hash != right.content_hash)

    @app.get("/api/v1/works/{work_id}/versions/{version_id}", response_model=ScriptVersionResponse)
    def get_version(work_id: str, version_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        version = db.scalar(select(ScriptVersion).where(ScriptVersion.id == version_id, ScriptVersion.work_id == work.id))
        if version is None:
            raise error("VERSION_NOT_FOUND", "script version not found", status.HTTP_404_NOT_FOUND)
        return _version_response(version)

    @app.get("/api/v1/works/{work_id}/versions/compare", response_model=VersionCompareResponse)
    def compare_versions(work_id: str, from_version: str, to_version: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        left = db.scalar(select(ScriptVersion).where(ScriptVersion.id == from_version, ScriptVersion.work_id == work.id))
        right = db.scalar(select(ScriptVersion).where(ScriptVersion.id == to_version, ScriptVersion.work_id == work.id))
        if left is None or right is None:
            raise error("VERSION_NOT_FOUND", "script version not found", status.HTTP_404_NOT_FOUND)
        return VersionCompareResponse(from_version=_version_response(left), to_version=_version_response(right), changed=left.content_hash != right.content_hash)

    @app.post("/api/v1/works/{work_id}/versions/{version_id}/restore", response_model=ScriptVersionResponse)
    def restore_version(work_id: str, version_id: str, request: Request, expected_revision: int | None = None, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER"})
        source = db.scalar(select(ScriptVersion).where(ScriptVersion.id == version_id, ScriptVersion.work_id == work.id))
        if source is None:
            raise error("VERSION_NOT_FOUND", "script version not found", status.HTTP_404_NOT_FOUND)
        version = _create_version(db, work, user, source.content_json, "RESTORE", source.id, expected_revision)
        db.commit()
        return _version_response(version)

    @app.get("/api/v1/works/{work_id}/releases", response_model=list[ScriptReleaseResponse])
    def list_releases(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return [_release_response(item) for item in db.scalars(select(ScriptRelease).where(ScriptRelease.work_id == work.id).order_by(ScriptRelease.created_at)).all()]

    @app.post("/api/v1/works/{work_id}/releases", response_model=ScriptReleaseResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/works/{work_id}/publish", response_model=ScriptReleaseResponse, include_in_schema=False)
    def publish_version(work_id: str, payload: ReleaseCreateRequest, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER"})
        version = db.scalar(select(ScriptVersion).where(ScriptVersion.id == payload.version_id, ScriptVersion.work_id == work.id))
        if version is None:
            raise error("VERSION_NOT_FOUND", "script version not found", status.HTTP_404_NOT_FOUND)
        for existing in db.scalars(select(ScriptRelease).where(ScriptRelease.work_id == work.id, ScriptRelease.is_current.is_(True))).all():
            existing.is_current = False
        release = ScriptRelease(work_id=work.id, version_id=version.id, released_by_user_id=user.id, is_current=True)
        db.add(release)
        db.flush()
        audit(db, "SCRIPT_RELEASE_PUBLISHED", actor=user, metadata={"work_id": work.id, "release_id": release.id, "version_id": version.id})
        db.commit()
        return _release_response(release)

    # Direct model management. Provider rows remain archival foreign-key targets.
    @app.post("/api/v1/admin/model-configs", response_model=ModelConfigResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/admin/model-configurations", response_model=ModelConfigResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
    def create_model_config(payload: ModelConfigCreateRequest, request: Request, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        check_csrf(request)
        provider = ModelProvider(slug=uuid4().hex, name=payload.display_name or payload.model_name,
            adapter=payload.adapter, enabled=payload.enabled, created_by_user_id=actor.id)
        identity = ModelConfiguration(enabled=payload.enabled)
        db.add_all([provider, identity])
        db.flush()
        credential_ciphertext = _encrypt_secret(payload.api_key, app_settings)
        config = ModelConfigVersion(provider_id=provider.id, model_id=identity.id, model=identity, version=1,
            display_name=payload.display_name or payload.model_name, adapter=payload.adapter,
            model_name=payload.model_name, endpoint=payload.base_url, parameters_json=payload.parameters,
            credential_ciphertext=credential_ciphertext, created_by_user_id=actor.id)
        db.add(config)
        db.flush()
        audit(db, "MODEL_CONFIG_VERSION_CREATED", actor=actor, metadata={"provider_id": provider.id, "config_id": config.id, "version": config.version})
        db.commit()
        return _config_response(config)

    @app.get("/api/v1/admin/model-configs", response_model=list[ModelConfigResponse])
    def list_model_configs(actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        return [_config_response(item) for item in db.scalars(select(ModelConfigVersion).order_by(ModelConfigVersion.created_at)).all()]

    @app.patch("/api/v1/admin/model-configs/{config_id}", response_model=ModelConfigResponse)
    @app.put("/api/v1/admin/model-configs/{config_id}", response_model=ModelConfigResponse, include_in_schema=False)
    def update_model_config(config_id: str, payload: ModelConfigUpdateRequest, request: Request, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        check_csrf(request)
        previous = db.get(ModelConfigVersion, config_id)
        if previous is None:
            raise error("MODEL_CONFIG_NOT_FOUND", "model configuration not found", status.HTTP_404_NOT_FOUND)
        if previous.model is None:
            previous.model = ModelConfiguration(enabled=previous.provider.enabled)
            db.add(previous.model)
            db.flush()
        identity = db.scalar(select(ModelConfiguration).where(ModelConfiguration.id == previous.model_id).with_for_update().execution_options(populate_existing=True))
        previous = db.scalar(select(ModelConfigVersion).where(ModelConfigVersion.id == config_id).with_for_update().execution_options(populate_existing=True))
        if previous.status != "ACTIVE":
            raise error("MODEL_CONFIG_NOT_AVAILABLE", "edit the active model configuration", status.HTTP_409_CONFLICT)
        if payload.enabled is not None:
            previous.model.enabled = payload.enabled
            # Compatibility bridge; new invocations use the stable model state.
            previous.provider.enabled = payload.enabled
        version_fields = payload.model_fields_set - {"enabled"}
        if not version_fields:
            db.commit()
            if payload.enabled is False:
                # Commit availability before taking work locks. In-flight commits
                # recheck it; this order cannot deadlock against work -> model.
                cancel_calls(db, model_id=identity.id, code="MODEL_CONFIG_NOT_AVAILABLE")
                db.commit()
            return _config_response(previous)
        secret = payload.api_key
        config = ModelConfigVersion(provider_id=previous.provider_id, model_id=previous.model.id, model=previous.model,
            version=previous.version + 1, display_name=payload.display_name or previous.display_name or previous.model_name,
            adapter=payload.adapter or previous.adapter or previous.provider.adapter,
            model_name=payload.model_name or previous.model_name,
            endpoint=payload.base_url if "base_url" in payload.model_fields_set else previous.endpoint,
            parameters_json=payload.parameters if payload.parameters is not None else (previous.parameters_json or {}),
            credential_ciphertext=_encrypt_secret(secret, app_settings) if secret is not None else previous.credential_ciphertext,
            created_by_user_id=actor.id)
        previous.status = "RETIRED"
        for consent in db.scalars(select(ExternalConsent).where(ExternalConsent.model_config_version_id == previous.id, ExternalConsent.status == "ACTIVE")).all():
            consent.status = "REVOKED"
            consent.revoked_at = _now()
        db.add(config)
        db.flush()
        audit(db, "MODEL_CONFIG_VERSION_CREATED", actor=actor, metadata={"provider_id": previous.provider_id, "config_id": config.id, "version": config.version, "replaced_config_id": previous.id})
        db.commit()
        if payload.enabled is False:
            cancel_calls(db, model_id=identity.id, code="MODEL_CONFIG_NOT_AVAILABLE")
            db.commit()
        return _config_response(config)

    @app.delete("/api/v1/admin/model-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_model_config(config_id: str, request: Request, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        check_csrf(request)
        current = db.get(ModelConfigVersion, config_id)
        if current is None:
            raise error("MODEL_CONFIG_NOT_FOUND", "model configuration not found", status.HTTP_404_NOT_FOUND)
        identity = db.scalar(
            select(ModelConfiguration)
            .where(ModelConfiguration.id == current.model_id)
            .with_for_update()
        ) if current.model_id else None
        if identity is not None:
            identity.enabled = False
            versions = db.scalars(
                select(ModelConfigVersion)
                .where(ModelConfigVersion.model_id == identity.id)
                .with_for_update()
            ).all()
        else:
            versions = [current]
        if current.provider is not None:
            current.provider.enabled = False
        for version in versions:
            version.status = "DELETED"
            for consent in db.scalars(
                select(ExternalConsent).where(
                    ExternalConsent.model_config_version_id == version.id,
                    ExternalConsent.status == "ACTIVE",
                )
            ).all():
                consent.status = "REVOKED"
                consent.revoked_at = _now()
        audit(
            db,
            "MODEL_CONFIG_DELETED",
            actor=actor,
            metadata={"model_id": current.model_id, "config_id": current.id},
        )
        db.commit()
        if identity is not None:
            cancel_calls(db, model_id=identity.id, code="MODEL_CONFIG_NOT_AVAILABLE")
            db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/admin/model-configs/{config_id}", response_model=ModelConfigResponse)
    def get_model_config(config_id: str, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        config = db.get(ModelConfigVersion, config_id)
        if config is None:
            raise error("MODEL_CONFIG_NOT_FOUND", "model configuration not found", status.HTTP_404_NOT_FOUND)
        return _config_response(config)

    @app.get("/api/v1/model-configs", response_model=list[ModelConfigResponse])
    def list_enabled_model_configs(user: User = Depends(active_user), db: Session = Depends(get_db)):
        query = select(ModelConfigVersion).join(ModelConfiguration).where(ModelConfiguration.enabled.is_(True), ModelConfigVersion.status == "ACTIVE")
        return [_config_response(item) for item in db.scalars(query.order_by(ModelConfigVersion.created_at)).all()]

    register_conversation_routes(app, settings=app_settings, active_user=active_user, check_csrf=check_csrf, require_member=_require, error=error)

    @app.get("/api/v1/works/{work_id}/ai-activity")
    def get_ai_activity(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        _require(db, user, work_id)
        return activity_response(db, work_id)

    @app.get("/api/v1/works/{work_id}/conversations", response_model=list[ConversationResponse])
    def list_conversations(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return [_conversation_response(item) for item in db.scalars(select(CreativeConversation).where(CreativeConversation.work_id == work.id).order_by(CreativeConversation.created_at)).all()]

    @app.get("/api/v1/works/{work_id}/model-invocations")
    def list_model_invocations(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return [
            {"id": item.id, "work_id": item.work_id, "provider_id": item.provider_id, "model_config_version_id": item.model_config_version_id, "status": item.status, "usage": item.usage_json or {}, "duration_ms": item.duration_ms, "error_code": item.error_code, "trace_id": item.trace_id, "created_at": item.created_at}
            for item in db.scalars(select(ModelInvocationRecord).where(ModelInvocationRecord.work_id == work.id).order_by(ModelInvocationRecord.created_at)).all()
        ]

    def run_generation_job(db: Session, job: GenerationJob, user: User | None = None) -> GenerationJob:
        # Starting and canceling a queued job share the work lock. Refresh only
        # after acquiring it so a worker can never revive a canceled task.
        db.scalar(select(Work).where(Work.id == job.work_id).with_for_update())
        db.refresh(job)
        work = db.get(Work, job.work_id)
        if job.status != "QUEUED":
            return job
        if work is None or work.status != "ACTIVE":
            job.status = "CANCELED"
            job.error_code = "WORK_TRASHED"
            job.finished_at = _now()
            release(db, job.work_id, job.id)
            return job
        if work.revision != job.input_revision:
            job.status = "FAILED"
            job.error_code = "REVISION_CONFLICT"
            job.finished_at = _now()
            release(db, job.work_id, job.id)
            return job
        config = db.get(ModelConfigVersion, job.model_config_version_id)
        provider = config.provider if config else None
        started = time.monotonic()
        try:
            requester = db.get(User, job.requested_by_user_id)
            if requester is None or not requester.is_active:
                raise ModelAdapterError("WORK_ACCESS_DENIED", "requesting account is unavailable")
            _require(db, requester, work.id, {"OWNER", "EDITOR"})
            available_config(db, job.model_config_version_id, new_call=False)
            validate_target(work, job.target_type, job.target_path_json)
            if job.input_snapshot_json is None:
                job.input_snapshot_json = generation_snapshot(db, work, GenerationJobRequest(
                    target_type=job.target_type, target_path=job.target_path_json, instruction=job.instruction,
                    model_config_version_id=job.model_config_version_id), app_settings.creative_context_max_characters)
            messages = deepcopy(job.input_snapshot_json["messages"])
            adapter = adapter_for(config.adapter or provider.adapter, endpoint=config.endpoint, credential=decrypt_secret(config.credential_ciphertext, app_settings.external_encryption_key))
            model, parameters = config.model_name, deepcopy(config.parameters_json or {})
            job.status = "RUNNING"
            job.started_at = _now()
            job_id = job.id
            db.commit()
            # Network I/O never holds a work/database lock.
            db.rollback()
            class CancelToken:
                @property
                def cancelled(self):
                    if app.state.generation_worker.stopping.is_set():
                        return True
                    with app.state.session_factory() as state_db:
                        record = state_db.get(GenerationJob, job_id)
                        if record is None or record.status == "CANCELED":
                            return True
                        snapshot = state_db.get(Work, record.work_id)
                        if snapshot is None or snapshot.status != "ACTIVE":
                            return True
                        if snapshot.revision != record.input_revision:
                            raise ModelAdapterError("REVISION_CONFLICT", "work changed during generation")
                        requester = state_db.get(User, record.requested_by_user_id)
                        member = state_db.scalar(select(WorkMember).where(WorkMember.work_id == snapshot.id, WorkMember.user_id == record.requested_by_user_id, WorkMember.status == "ACCEPTED", WorkMember.role.in_(["OWNER", "EDITOR"])))
                        if requester is None or not requester.is_active or member is None:
                            raise ModelAdapterError("WORK_ACCESS_DENIED", "requesting account is unavailable")
                        available_config(state_db, record.model_config_version_id, new_call=False)
                        return False
            result = None
            for attempt in range(3):
                try:
                    token = CancelToken()
                    check_cancel(token)
                    result = adapter.invoke(messages=messages, model=model, parameters=parameters, timeout=app_settings.model_timeout_seconds, cancel_token=token)
                    break
                except ModelAdapterError as exc:
                    if not exc.retryable or attempt == 2:
                        raise
                    if app.state.generation_worker.stopping.wait(0.25 * 2 ** attempt):
                        raise ModelAdapterError("CANCELED", "generation worker is stopping")
                    db.refresh(job)
                    if job.status == "CANCELED":
                        return job
                    job.retry_count += 1
                    db.commit()
            db.expire_all()
            job = db.get(GenerationJob, job_id)
            work = db.scalar(select(Work).where(Work.id == job.work_id).with_for_update())
            if job.status == "CANCELED" or work.status != "ACTIVE":
                return job
            if work.revision != job.input_revision:
                raise ModelAdapterError("REVISION_CONFLICT", "work changed during generation")
            requester = db.get(User, job.requested_by_user_id)
            if not requester.is_active:
                raise ModelAdapterError("WORK_ACCESS_DENIED", "requesting account is unavailable")
            _require(db, requester, work.id, {"OWNER", "EDITOR"})
            available_config(db, job.model_config_version_id, new_call=False, lock=True)
            if result.stop_reason in {"length", "max_tokens"}:
                raise ModelAdapterError("SCRIPT_STRUCTURE_INVALID", "model output was truncated")
            content = replace_target(work, job.target_type, job.target_path_json, result.text)
            claimed = db.execute(update(GenerationJob).where(GenerationJob.id == job.id, GenerationJob.status == "RUNNING").values(status="SUCCEEDED"))
            if claimed.rowcount != 1:
                db.rollback()
                return db.get(GenerationJob, job.id)
            invocation = ModelInvocationRecord(work_id=work.id, provider_id=provider.id, model_config_version_id=config.id, status="SUCCEEDED", usage_json=result.usage, duration_ms=int((time.monotonic() - started) * 1000), trace_id=uuid4().hex)
            db.add(invocation)
            version = _create_version(db, work, requester, content, "GENERATION", expected_revision=job.input_revision)
            job.result_version_id = version.id
            job.status = "SUCCEEDED"
            job.finished_at = _now()
            audit(db, "GENERATION_JOB_SUCCEEDED", actor=requester, metadata={"work_id": work.id, "job_id": job.id, "version_id": version.id})
        except Exception as exc:
            job_id = job.id
            db.rollback()
            job = db.get(GenerationJob, job_id)
            if job is None:
                return job
            code = exc.code if isinstance(exc, ModelAdapterError) else "WORK_ACCESS_DENIED" if getattr(exc, "status_code", None) == 403 else "REVISION_CONFLICT" if getattr(exc, "status_code", None) == 409 else "MODEL_CONFIG_NOT_AVAILABLE"
            if job.status != "CANCELED":
                job.status = "CANCELED" if code == "CANCELED" else "FAILED"
                job.error_code = code
                job.error_message = "generation did not complete; the previous draft is preserved"
                job.finished_at = _now()
            if provider and config:
                db.add(ModelInvocationRecord(work_id=job.work_id, provider_id=provider.id, model_config_version_id=config.id, status=job.status, usage_json={}, duration_ms=int((time.monotonic() - started) * 1000), error_code=code, trace_id=uuid4().hex))
        if job.status not in {"QUEUED", "RUNNING"}:
            release(db, job.work_id, job.id)
        return job

    @app.post("/api/v1/works/{work_id}/generation-jobs", response_model=GenerationJobResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/works/{work_id}/generate", response_model=GenerationJobResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
    def create_generation_job(work_id: str, payload: GenerationJobRequest, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER", "EDITOR"})
        idempotency_key = payload.idempotency_key or request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key") or uuid4().hex
        request_hash = _json_hash(payload.model_dump(exclude={"idempotency_key", "auto_run"}))
        existing = db.scalar(select(GenerationJob).where(GenerationJob.work_id == work.id, GenerationJob.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.requested_by_user_id != user.id or ((existing.input_snapshot_json or {}).get("request_hash") not in {None, request_hash}):
                raise error("IDEMPOTENCY_CONFLICT", "该发送标识已用于另一请求。", 409)
            return _job_response(existing)
        if payload.expected_revision is not None and payload.expected_revision != work.revision:
            raise error("REVISION_CONFLICT", "work revision has changed", 409)
        data = work.creative_data
        if data is None or any(getattr(data, f"{stage}_confirmed_json") is None for stage in ("brief", "characters", "outline")):
            raise error("SETTINGS_NOT_CONFIRMED", "all creative stages must be confirmed before generation", status.HTTP_409_CONFLICT)
        required_path = {"WORK": (), "EPISODE": ("episode",), "SCENE": ("episode", "scene"), "FRAGMENT": ("episode", "scene", "fragment")}[payload.target_type]
        if any(key not in payload.target_path for key in required_path):
            raise error("INVALID_GENERATION_TARGET", "generation target does not match the requested scope", status.HTTP_422_UNPROCESSABLE_CONTENT)
        config_id = payload.model_config_version_id
        try:
            config = available_config(db, config_id, lock=True)
            validate_target(work, payload.target_type, payload.target_path)
            snapshot = generation_snapshot(db, work, payload, app_settings.creative_context_max_characters)
        except ModelAdapterError as exc:
            raise error(exc.code, str(exc), 409 if exc.code == "MODEL_CONFIG_NOT_AVAILABLE" else 422)
        active = db.scalar(select(GenerationJob).where(GenerationJob.work_id == work.id, GenerationJob.status.in_(["QUEUED", "RUNNING"])))
        if active is not None:
            raise error("GENERATION_ALREADY_RUNNING", "only one generation job can run for a work", status.HTTP_409_CONFLICT)
        snapshot["request_hash"] = request_hash
        job = GenerationJob(work_id=work.id, requested_by_user_id=user.id, idempotency_key=idempotency_key, input_revision=work.revision if payload.input_revision is None else payload.input_revision, target_type=payload.target_type, target_path_json=payload.target_path, instruction=payload.instruction, model_config_version_id=config_id, input_snapshot_json=snapshot)
        if job.input_revision != work.revision:
            raise error("REVISION_CONFLICT", "generation input revision must match the current work revision", status.HTTP_409_CONFLICT)
        db.add(job)
        try:
            db.flush()
            occupy(db, work.id, "GENERATION", job.id, user.id)
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(GenerationJob).where(GenerationJob.work_id == work_id, GenerationJob.idempotency_key == idempotency_key))
            if existing is not None:
                if existing.requested_by_user_id != user.id or (existing.input_snapshot_json or {}).get("request_hash") not in {None, request_hash}:
                    raise error("IDEMPOTENCY_CONFLICT", "该发送标识已用于另一请求。", 409)
                return _job_response(existing)
            raise error("AI_ALREADY_RUNNING", "作品已有成员正在调用模型，请稍后发送。", status.HTTP_409_CONFLICT)
        audit(db, "GENERATION_JOB_CREATED", actor=user, metadata={"work_id": work.id, "job_id": job.id, "target_type": job.target_type})
        db.commit()
        if payload.auto_run:
            app.state.generation_worker.enqueue(job.id)
        return _job_response(job)

    @app.get("/api/v1/works/{work_id}/generation-jobs", response_model=list[GenerationJobResponse])
    def list_generation_jobs(work_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return [_job_response(item) for item in db.scalars(select(GenerationJob).where(GenerationJob.work_id == work.id).order_by(GenerationJob.created_at)).all()]

    @app.get("/api/v1/works/{work_id}/generation-jobs/{job_id}", response_model=GenerationJobResponse)
    def get_generation_job(work_id: str, job_id: str, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.work_id == work.id))
        if job is None:
            raise error("GENERATION_JOB_NOT_FOUND", "generation job not found", status.HTTP_404_NOT_FOUND)
        return _job_response(job)

    @app.post("/api/v1/works/{work_id}/generation-jobs/{job_id}/run", response_model=GenerationJobResponse)
    def run_job(work_id: str, job_id: str, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER", "EDITOR"})
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.work_id == work.id))
        if job is None:
            raise error("GENERATION_JOB_NOT_FOUND", "generation job not found", status.HTTP_404_NOT_FOUND)
        db.rollback()
        with app.state.generation_worker.execution_lock:
            run_generation_job(db, job, user)
            db.commit()
        return _job_response(job)

    @app.post("/api/v1/works/{work_id}/generation-jobs/{job_id}/cancel", response_model=GenerationJobResponse)
    def cancel_job(work_id: str, job_id: str, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER", "EDITOR"})
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.work_id == work.id))
        if job is None:
            raise error("GENERATION_JOB_NOT_FOUND", "generation job not found", status.HTTP_404_NOT_FOUND)
        if job.status in {"SUCCEEDED", "FAILED", "CANCELED"}:
            raise error("GENERATION_JOB_NOT_ACTIVE", "generation job is already complete", status.HTTP_409_CONFLICT)
        changed = db.execute(update(GenerationJob).where(GenerationJob.id == job.id, GenerationJob.status.in_(["QUEUED", "RUNNING"])).values(status="CANCELED", finished_at=_now(), error_code="CANCELED_BY_USER"))
        if changed.rowcount != 1:
            raise error("GENERATION_JOB_NOT_ACTIVE", "generation job is already complete", status.HTTP_409_CONFLICT)
        db.refresh(job)
        release(db, work.id, job.id)
        audit(db, "GENERATION_JOB_CANCELED", actor=user, metadata={"work_id": work.id, "job_id": job.id})
        db.commit()
        return _job_response(job)

    @app.post("/api/v1/works/{work_id}/generation-jobs/{job_id}/retry", response_model=GenerationJobResponse)
    def retry_job(work_id: str, job_id: str, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER", "EDITOR"})
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.work_id == work.id))
        if job is None:
            raise error("GENERATION_JOB_NOT_FOUND", "generation job not found", status.HTTP_404_NOT_FOUND)
        if job.status != "FAILED":
            raise error("GENERATION_JOB_NOT_RETRYABLE", "only failed generation jobs can be retried", status.HTTP_409_CONFLICT)
        if job.error_code in {"AUTHENTICATION_ERROR", "INVALID_PARAMETERS", "CONTENT_POLICY_ERROR", "REVISION_CONFLICT", "WORK_TRASHED"}:
            raise error("GENERATION_JOB_NOT_RETRYABLE", "this generation failure cannot be retried", status.HTTP_409_CONFLICT)
        if work.revision != job.input_revision:
            raise error("REVISION_CONFLICT", "work changed after the generation request", status.HTTP_409_CONFLICT)
        if db.scalar(select(GenerationJob.id).where(GenerationJob.work_id == work.id, GenerationJob.id != job.id, GenerationJob.status.in_(["QUEUED", "RUNNING"]))):
            raise error("AI_ALREADY_RUNNING", "作品已有成员正在调用模型，请稍后发送。", status.HTTP_409_CONFLICT)
        try:
            available_config(db, job.model_config_version_id, new_call=False, lock=True)
            occupy(db, work.id, "GENERATION", job.id, user.id)
        except ModelAdapterError as exc:
            raise error(exc.code, str(exc), 409)
        except IntegrityError:
            db.rollback()
            raise error("AI_ALREADY_RUNNING", "作品已有模型调用。", 409)
        job.status = "QUEUED"
        job.retry_count += 1
        job.error_code = None
        job.error_message = None
        job.finished_at = None
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise error("AI_ALREADY_RUNNING", "作品已有成员正在调用模型，请稍后发送。", status.HTTP_409_CONFLICT)
        app.state.generation_worker.enqueue(job.id)
        return _job_response(job)

    @app.post("/api/v1/works/{work_id}/exports", response_model=ExportJobResponse, status_code=status.HTTP_201_CREATED)
    def create_export(work_id: str, payload: ExportRequest, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        check_csrf(request)
        work, _ = _require(db, user, work_id, {"OWNER"})
        release_query = select(ScriptRelease).where(ScriptRelease.work_id == work.id)
        if payload.release_id:
            release_query = release_query.where(ScriptRelease.id == payload.release_id)
        else:
            release_query = release_query.where(ScriptRelease.is_current.is_(True))
        release = db.scalar(release_query.order_by(ScriptRelease.created_at.desc()))
        if release is None:
            raise error("RELEASE_REQUIRED", "publish a script release before exporting", status.HTTP_409_CONFLICT)
        token = secrets.token_urlsafe(32)
        content = render_markdown(work, release.version.content_json) if payload.format == "markdown" else base64.b64encode(render_docx(work, release.version.content_json)).decode()
        job = ExportJob(work_id=work.id, release_id=release.id, format=payload.format, content=content, download_token_hash=hashlib.sha256(token.encode()).hexdigest(), download_expires_at=_now() + timedelta(seconds=app_settings.download_ttl_seconds), created_by_user_id=user.id)
        db.add(job)
        db.flush()
        audit(db, "SCRIPT_EXPORT_CREATED", actor=user, metadata={"work_id": work.id, "export_id": job.id, "release_id": release.id, "format": job.format})
        db.commit()
        response = _export_response(job, request)
        if response.download_url:
            response.download_url = f"{response.download_url}?token={token}"
        return response

    @app.get("/api/v1/works/{work_id}/exports", response_model=list[ExportJobResponse])
    def list_exports(work_id: str, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        work, _ = _require(db, user, work_id)
        return [_export_response(item, request) for item in db.scalars(select(ExportJob).where(ExportJob.work_id == work.id).order_by(ExportJob.created_at)).all()]

    @app.post("/api/v1/admin/backups")
    @app.post("/api/v1/admin/backup", include_in_schema=False)
    def create_backup(request: Request, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        check_csrf(request)
        backup_dir = Path(app_settings.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "format": 2,
            "created_at": _now().isoformat(),
            "users": [{"id": item.id, "username": item.username, "password_hash": item.password_hash, "role": item.role, "is_active": item.is_active, "must_change_password": item.must_change_password} for item in db.scalars(select(User)).all()],
            "templates": [{"id": item.id, "slug": item.slug, "name": item.name, "status": item.status} for item in db.scalars(select(ScriptTemplate)).all()],
            "template_versions": [{"id": item.id, "template_id": item.template_id, "version": item.version, "status": item.status, "definition": item.definition_json} for item in db.scalars(select(ScriptTemplateVersion)).all()],
            "works": [{"id": work.id, "title": work.title, "owner_id": work.owner_id, "template_version_id": work.template_version_id, "settings": work.settings_json, "status": work.status, "revision": work.revision, "draft": work.draft_json} for work in db.scalars(select(Work)).all()],
            "versions": [{"id": item.id, "work_id": item.work_id, "source": item.source, "author_user_id": item.author_user_id, "parent_version_id": item.parent_version_id, "content": item.content_json, "content_hash": item.content_hash, "work_revision": item.work_revision} for item in db.scalars(select(ScriptVersion)).all()],
            "releases": [{"id": item.id, "work_id": item.work_id, "version_id": item.version_id, "released_by_user_id": item.released_by_user_id, "is_current": item.is_current} for item in db.scalars(select(ScriptRelease)).all()],
            "members": [{"work_id": item.work_id, "user_id": item.user_id, "role": item.role, "status": item.status, "invited_at": item.invited_at.isoformat() if item.invited_at else None, "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None, "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None} for item in db.scalars(select(WorkMember)).all()],
            "creative": [{"work_id": item.work_id, "brief": item.brief_json, "characters": item.characters_json, "outline": item.outline_json, "brief_confirmed": item.brief_confirmed_json, "characters_confirmed": item.characters_confirmed_json, "outline_confirmed": item.outline_confirmed_json, "brief_revision": item.brief_revision, "characters_revision": item.characters_revision, "outline_revision": item.outline_revision} for item in db.scalars(select(WorkCreativeData)).all()],
            "providers": [{"id": item.id, "slug": item.slug, "name": item.name, "adapter": item.adapter, "enabled": item.enabled, "retention": item.retention_description, "deletion": item.deletion_description, "incident": item.incident_description} for item in db.scalars(select(ModelProvider)).all()],
            "model_configurations": [{"id": item.id, "enabled": item.enabled} for item in db.scalars(select(ModelConfiguration)).all()],
            "model_configs": [{"id": item.id, "model_id": item.model_id, "provider_id": item.provider_id, "version": item.version, "display_name": item.display_name, "adapter": item.adapter, "model_name": item.model_name, "endpoint": item.endpoint, "parameters": item.parameters_json, "status": item.status} for item in db.scalars(select(ModelConfigVersion)).all()],
            "conversations": [{"id": item.id, "work_id": item.work_id, "user_id": item.user_id, "user_message": item.user_message, "assistant_message": item.assistant_message, "status": item.status, "model_config_version_id": item.model_config_version_id, "invocation_id": item.invocation_id, "input_revision": item.input_revision, "input_snapshot_json": item.input_snapshot_json, "idempotency_key": item.idempotency_key, "request_hash": item.request_hash, "error_code": item.error_code, "created_at": item.created_at.isoformat() if item.created_at else None} for item in db.scalars(select(CreativeConversation)).all()],
        }
        snapshot["invocations"] = _backup_rows(db, ModelInvocationRecord, ["id", "work_id", "provider_id", "model_config_version_id", "status", "usage_json", "duration_ms", "error_code", "trace_id", "created_at"])
        snapshot["consents"] = _backup_rows(db, ExternalConsent, ["id", "work_id", "input_revision", "model_config_version_id", "data_categories_json", "status", "granted_by_user_id", "created_at", "revoked_at"])
        snapshot["generation_jobs"] = _backup_rows(db, GenerationJob, ["id", "work_id", "requested_by_user_id", "idempotency_key", "input_revision", "target_type", "target_path_json", "instruction", "model_config_version_id", "consent_id", "input_snapshot_json", "status", "retry_count", "error_code", "error_message", "result_version_id", "created_at", "updated_at", "started_at", "finished_at"])
        raw = _crypt_payload(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(), app_settings)
        digest = hashlib.sha256(raw).hexdigest()
        path = backup_dir / f"backup-{int(time.time())}-{digest[:12]}.json"
        path.write_bytes(raw)
        record = BackupRecord(path=str(path), checksum=digest, created_by_user_id=actor.id)
        db.add(record)
        audit(db, "BACKUP_CREATED", actor=actor, metadata={"backup_id": record.id, "checksum": digest})
        db.commit()
        return {"id": record.id, "path": str(path), "checksum": digest, "created_at": record.created_at}

    @app.get("/api/v1/admin/backups")
    def list_backups(actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        return [{"id": item.id, "checksum": item.checksum, "created_at": item.created_at, "available": Path(item.path).exists()} for item in db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc())).all()]

    @app.post("/api/v1/admin/backups/{backup_id}/restore")
    @app.post("/api/v1/admin/backup/{backup_id}/restore", include_in_schema=False)
    def restore_backup(backup_id: str, request: Request, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        check_csrf(request)
        record = db.get(BackupRecord, backup_id)
        if record is None or not Path(record.path).exists():
            raise error("BACKUP_NOT_FOUND", "backup not found", status.HTTP_404_NOT_FOUND)
        raw = Path(record.path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.checksum:
            raise error("BACKUP_CHECKSUM_INVALID", "backup checksum does not match", status.HTTP_409_CONFLICT)
        try:
            snapshot = json.loads(_decrypt_payload(raw, app_settings))
        except (ValueError, json.JSONDecodeError):
            raise error("BACKUP_DECRYPT_FAILED", "backup cannot be decrypted", status.HTTP_409_CONFLICT)
        cancel_calls(db, code="RESTORE_RESET")
        for item in snapshot.get("users", []):
            if db.get(User, item.get("id")) is None and db.scalar(select(User).where(User.username == item.get("username"))) is None:
                db.add(User(id=item["id"], username=item["username"], password_hash=item.get("password_hash", ""), role=item.get("role", "WRITER"), is_active=item.get("is_active", True), must_change_password=item.get("must_change_password", True)))
        db.flush()
        # Restore template bindings before works, including across fresh databases.
        template_ids = {}
        for item in snapshot.get("templates", []):
            template = db.get(ScriptTemplate, item["id"]) or db.scalar(select(ScriptTemplate).where(ScriptTemplate.slug == item["slug"]))
            if template is None:
                template = ScriptTemplate(id=item["id"], slug=item["slug"], name=item["name"], status=item.get("status", "ACTIVE"))
                db.add(template)
            template_ids[item["id"]] = template.id
        db.flush()
        for item in snapshot.get("template_versions", []):
            if db.get(ScriptTemplateVersion, item["id"]) is not None:
                continue
            template_id = template_ids.get(item["template_id"], item["template_id"])
            template = db.get(ScriptTemplate, template_id)
            if template is None:
                continue
            existing = db.scalars(select(ScriptTemplateVersion).where(ScriptTemplateVersion.template_id == template_id)).all()
            number = max([version.version for version in existing] + [0]) + 1
            state = "ARCHIVED" if existing else item.get("status", "ARCHIVED")
            db.add(ScriptTemplateVersion(id=item["id"], template_id=template_id, version=number, status=state, definition_json=item["definition"]))
            db.flush()
        # Runtime state is never revived. Revisions advance to reject stale clients.
        restored_work_ids = []
        for item in snapshot.get("works", []):
            work = db.get(Work, item["id"])
            if work is None and db.get(User, item.get("owner_id")) is not None:
                work = Work(id=item["id"], title=item.get("title", "Restored work"), owner_id=item["owner_id"], template_version_id=item.get("template_version_id"), settings_json=item.get("settings") or {}, status=item.get("status", "ACTIVE"), revision=item.get("revision", 0), draft_json=item.get("draft") or {})
                db.add(work)
            if work is not None:
                work.title = item.get("title", work.title)
                work.settings_json = item.get("settings") or work.settings_json
                work.status = item.get("status", work.status)
                work.owner_id = item.get("owner_id", work.owner_id)
                work.template_version_id = item.get("template_version_id", work.template_version_id)
                work.revision = max(work.revision, item.get("revision", 0)) + 1
                work.updated_at = _now()
                restored_work_ids.append(work.id)
                work.draft_json = item.get("draft") or {}
        db.flush()
        for item in snapshot.get("creative", []):
            data = db.scalar(select(WorkCreativeData).where(WorkCreativeData.work_id == item.get("work_id")))
            if data is None and db.get(Work, item.get("work_id")) is not None:
                data = WorkCreativeData(work_id=item["work_id"])
                db.add(data)
            if data is not None:
                data.brief_json = item.get("brief")
                data.characters_json = item.get("characters")
                data.outline_json = item.get("outline")
                data.brief_confirmed_json = item.get("brief_confirmed")
                data.characters_confirmed_json = item.get("characters_confirmed")
                data.outline_confirmed_json = item.get("outline_confirmed")
                data.brief_revision = item.get("brief_revision", data.brief_revision)
                data.characters_revision = item.get("characters_revision", data.characters_revision)
                data.outline_revision = item.get("outline_revision", data.outline_revision)
        db.flush()
        for item in snapshot.get("providers", []):
            provider = db.get(ModelProvider, item.get("id"))
            if provider is None:
                provider = db.scalar(select(ModelProvider).where(ModelProvider.slug == item.get("slug")))
            if provider is None:
                provider = ModelProvider(id=item["id"], slug=item["slug"], name=item.get("name", item["slug"]), adapter=item.get("adapter", "mock"), enabled=False, retention_description=item.get("retention"), deletion_description=item.get("deletion"), incident_description=item.get("incident"))
                db.add(provider)
        db.flush()
        for item in snapshot.get("model_configurations", []):
            if db.get(ModelConfiguration, item.get("id")) is None:
                db.add(ModelConfiguration(id=item["id"], enabled=False))
        db.flush()
        for item in snapshot.get("model_configs", []):
            if db.get(ModelConfigVersion, item.get("id")) is None and db.get(ModelProvider, item.get("provider_id")) is not None:
                model_id = item.get("model_id")
                if model_id is None:
                    model_id = str(uuid4())
                    db.add(ModelConfiguration(id=model_id, enabled=False))
                    db.flush()
                db.add(ModelConfigVersion(id=item["id"], model_id=model_id, provider_id=item["provider_id"], version=item.get("version", 1), display_name=item.get("display_name") or item.get("model_name", "restored"), adapter=item.get("adapter"), model_name=item.get("model_name", "restored"), endpoint=item.get("endpoint"), parameters_json=item.get("parameters") or {}, status=item.get("status", "RETIRED")))
        db.flush()
        for item in snapshot.get("versions", []):
            if db.get(ScriptVersion, item["id"]) is None and db.get(Work, item.get("work_id")) is not None:
                db.add(ScriptVersion(id=item["id"], work_id=item["work_id"], source=item.get("source", "RESTORE"), author_user_id=item.get("author_user_id"), parent_version_id=item.get("parent_version_id"), content_json=item.get("content") or {}, content_hash=item.get("content_hash") or _json_hash(item.get("content") or {}), work_revision=item.get("work_revision", 0)))
        db.flush()
        for member in db.scalars(select(WorkMember).where(WorkMember.work_id.in_(restored_work_ids))).all():
            member.status = "REVOKED"
        db.flush()
        for item in snapshot.get("members", []):
            if db.get(User, item.get("user_id")) is None or db.get(Work, item.get("work_id")) is None:
                continue
            member = db.scalar(select(WorkMember).where(WorkMember.work_id == item["work_id"], WorkMember.user_id == item["user_id"]))
            if member is None:
                member = WorkMember(work_id=item["work_id"], user_id=item["user_id"], role=item.get("role", "VIEWER"), status=item.get("status", "ACCEPTED"))
                db.add(member)
            member.role = item.get("role", "VIEWER")
            member.status = item.get("status", "ACCEPTED")
        db.flush()
        for release in db.scalars(select(ScriptRelease).where(ScriptRelease.work_id.in_(restored_work_ids))).all():
            release.is_current = False
        db.flush()
        for item in snapshot.get("releases", []):
            if db.get(ScriptRelease, item["id"]) is None and db.get(Work, item.get("work_id")) is not None and db.get(ScriptVersion, item.get("version_id")) is not None and db.get(User, item.get("released_by_user_id")) is not None:
                db.add(ScriptRelease(id=item["id"], work_id=item["work_id"], version_id=item["version_id"], released_by_user_id=item["released_by_user_id"], is_current=item.get("is_current", False)))
            else:
                release = db.get(ScriptRelease, item["id"])
                if release is not None:
                    release.is_current = item.get("is_current", False)
        db.flush()
        for item in snapshot.get("consents", []):
            if db.get(ExternalConsent, item["id"]) is None:
                values = _restore_values(item)
                values["status"] = "REVOKED"
                values["revoked_at"] = _now()
                db.add(ExternalConsent(**values))
        db.flush()
        for item in snapshot.get("invocations", []):
            if db.get(ModelInvocationRecord, item["id"]) is None:
                values = _restore_values(item)
                if values["status"] in {"QUEUED", "RUNNING"}:
                    values.update(status="INCOMPLETE", error_code="RESTORE_RESET")
                db.add(ModelInvocationRecord(**values))
        db.flush()
        for item in snapshot.get("generation_jobs", []):
            if db.get(GenerationJob, item["id"]) is None:
                values = _restore_values(item)
                if values["status"] in {"QUEUED", "RUNNING"}:
                    values.update(status="CANCELED", error_code="RESTORE_RESET", finished_at=_now())
                db.add(GenerationJob(**values))
        db.flush()
        for item in snapshot.get("conversations", []):
            if db.get(CreativeConversation, item["id"]) is None and db.get(Work, item["work_id"]) is not None and db.get(User, item["user_id"]) is not None:
                values = {key: item.get(key) for key in ("id", "work_id", "user_id", "user_message", "assistant_message", "model_config_version_id", "invocation_id", "input_revision", "input_snapshot_json", "idempotency_key", "request_hash", "error_code")}
                values["status"] = item.get("status", "SUCCEEDED")
                if values["status"] in {"QUEUED", "RUNNING"}:
                    values.update(status="INCOMPLETE", error_code="RESTORE_RESET")
                if item.get("created_at"):
                    values["created_at"] = datetime.fromisoformat(item["created_at"])
                if values["model_config_version_id"] is None and values["invocation_id"]:
                    invocation = db.get(ModelInvocationRecord, values["invocation_id"])
                    values["model_config_version_id"] = invocation.model_config_version_id if invocation else None
                db.add(CreativeConversation(**values))
        for export in db.scalars(select(ExportJob)).all():
            export.download_token_hash = None
            export.download_expires_at = _now()
        for job in db.scalars(select(GenerationJob).where(GenerationJob.status.in_(["QUEUED", "RUNNING"]))).all():
            job.status = "CANCELED"
            job.error_code = "RESTORE_RESET"
            job.finished_at = _now()
        for consent in db.scalars(select(ExternalConsent).where(ExternalConsent.status == "ACTIVE")).all():
            consent.status = "REVOKED"
            consent.revoked_at = _now()
        audit(db, "BACKUP_RESTORED", actor=actor, metadata={"backup_id": record.id, "works": len(snapshot.get("works", []))})
        db.commit()
        return {"id": record.id, "status": "VERIFIED", "restored_works": len(snapshot.get("works", [])), "runtime_state_reset": True}

    @app.post("/api/v1/admin/trash/purge")
    def purge_expired_trash(request: Request, actor: User = Depends(admin_user), db: Session = Depends(get_db)):
        check_csrf(request)
        cutoff = _now() - timedelta(days=app_settings.trash_retention_days)
        purged = 0
        for work in db.scalars(select(Work).where(Work.status == "TRASHED", Work.trashed_at.is_not(None))).all():
            trashed_at = _aware(work.trashed_at)
            if trashed_at is not None and trashed_at < cutoff:
                audit(db, "WORK_PERMANENTLY_DELETED", actor=actor, metadata={"work_id": work.id})
                db.delete(work)
                purged += 1
        db.commit()
        return {"purged": purged}

    @app.get("/api/v1/exports/{export_id}/download", name="download_export")
    def download_export(export_id: str, token: str, request: Request, user: User = Depends(active_user), db: Session = Depends(get_db)):
        job = db.get(ExportJob, export_id)
        if job is None:
            raise error("EXPORT_NOT_FOUND", "export not found", status.HTTP_404_NOT_FOUND)
        work, _ = _require(db, user, job.work_id)
        if _aware(job.download_expires_at) is None or _aware(job.download_expires_at) < _now() or job.download_token_hash != hashlib.sha256(token.encode()).hexdigest():
            raise error("DOWNLOAD_LINK_EXPIRED", "download link is expired", status.HTTP_410_GONE)
        if job.format == "markdown":
            filename = quote(f"{work.title}.md")
            return Response(content=job.content or "", media_type="text/markdown", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})
        filename = quote(f"{work.title}.docx")
        return Response(content=base64.b64decode(job.content or ""), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})
