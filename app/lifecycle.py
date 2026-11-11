"""Process-internal lifecycle: trash retention maintenance and worker startup recovery.

Single-process / single-worker assumptions (see ADR-0012): the in-memory queue,
maintenance thread and process lock all rely on exactly one Uvicorn worker.
The database is the only persistent source of truth for job and lifecycle state.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.creative_calls import finish_conversation, release
from app.models import (
    AuditLog,
    ChangeProposal,
    CreativeConversation,
    ExternalConsent,
    ExportJob,
    GenerationJob,
    ModelConfiguration,
    ModelConfigVersion,
    ModelInvocationRecord,
    ScriptRelease,
    ScriptVersion,
    User,
    Work,
    WorkAIActivity,
    WorkCreativeData,
    WorkMember,
)

_lifecycle_lock = threading.RLock()
_tick_interval_seconds = 24 * 3600


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def trash_deadline(trashed_at: datetime | None, retention_days: int) -> datetime | None:
    base = aware(trashed_at)
    if base is None:
        return None
    return base + timedelta(days=retention_days)


def is_expired(trashed_at: datetime | None, retention_days: int, now: datetime | None = None) -> bool:
    """Restore, automatic sweep and manual purge share this single boundary:
    reaching the deadline (now >= deadline) is already expired."""
    deadline = trash_deadline(trashed_at, retention_days)
    if deadline is None:
        return False
    reference = aware(now) or datetime.now(timezone.utc)
    return reference >= deadline


@contextmanager
def lifecycle_guard():
    with _lifecycle_lock:
        yield


def _delete_work_rows(db, work_id: str) -> None:
    # Explicit dependency order; SQLite does not enforce foreign keys here.
    db.execute(update(ScriptVersion).where(ScriptVersion.work_id == work_id).values(parent_version_id=None))
    db.execute(delete(ExportJob).where(ExportJob.work_id == work_id))
    db.execute(delete(GenerationJob).where(GenerationJob.work_id == work_id))
    db.execute(delete(ScriptRelease).where(ScriptRelease.work_id == work_id))
    db.execute(delete(ScriptVersion).where(ScriptVersion.work_id == work_id))
    db.execute(delete(ExternalConsent).where(ExternalConsent.work_id == work_id))
    db.execute(delete(CreativeConversation).where(CreativeConversation.work_id == work_id))
    db.execute(delete(ModelInvocationRecord).where(ModelInvocationRecord.work_id == work_id))
    db.execute(delete(WorkAIActivity).where(WorkAIActivity.work_id == work_id))
    db.execute(delete(ChangeProposal).where(ChangeProposal.work_id == work_id))
    db.execute(delete(WorkCreativeData).where(WorkCreativeData.work_id == work_id))
    db.execute(delete(WorkMember).where(WorkMember.work_id == work_id))
    db.execute(delete(Work).where(Work.id == work_id))
    db.flush()


def purge_expired_works(session_factory, retention_days: int, *, actor_user_id: str | None = None, now: datetime | None = None) -> int:
    """Physically erase every TRASHED work past its deadline.

    Each work is confirmed and deleted inside its own transaction under the
    process lifecycle lock, so the sweep never races a concurrent restore.
    """
    purged = 0
    reference = aware(now) or datetime.now(timezone.utc)
    with lifecycle_guard():
        with session_factory() as db:
            candidates = db.scalars(
                select(Work.id).where(Work.status == "TRASHED", Work.trashed_at.is_not(None))
            ).all()
        for work_id in candidates:
            with session_factory() as db:
                work = db.scalar(
                    select(Work).where(Work.id == work_id).with_for_update().execution_options(populate_existing=True)
                )
                if work is None or work.status != "TRASHED":
                    continue
                if not is_expired(work.trashed_at, retention_days, reference):
                    continue
                trashed_at = aware(work.trashed_at)
                deadline = trash_deadline(work.trashed_at, retention_days)
                metadata = {
                    "work_id": work.id,
                    "trashed_at": trashed_at.isoformat() if trashed_at else None,
                    "deadline": deadline.isoformat() if deadline else None,
                    "retention_days": retention_days,
                }
                _delete_work_rows(db, work_id)
                db.add(AuditLog(event_type="WORK_PERMANENTLY_DELETED", actor_user_id=actor_user_id, metadata_json=metadata))
                db.commit()
                purged += 1
    return purged


def _maintenance_loop(session_factory, settings, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            purge_expired_works(session_factory, settings.trash_retention_days)
        except Exception:
            logging.getLogger(__name__).exception("trash maintenance sweep failed")
        # Event.wait returns immediately on stop; never busy-poll.
        stop_event.wait(_tick_interval_seconds)


def start_trash_maintenance(session_factory, settings) -> tuple[threading.Thread, threading.Event]:
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_maintenance_loop,
        args=(session_factory, settings, stop_event),
        name="mujian-trash-maintenance",
        daemon=True,
    )
    thread.start()
    return thread, stop_event


def _queued_job_is_valid(db, job: GenerationJob) -> str | None:
    """Return a stable failure code if the queued job must not resume, else None."""
    work = db.scalar(select(Work).where(Work.id == job.work_id).with_for_update().execution_options(populate_existing=True))
    if work is None or work.status != "ACTIVE":
        return "WORK_INACTIVE"
    requester = db.get(User, job.requested_by_user_id)
    member = db.scalar(
        select(WorkMember).where(
            WorkMember.work_id == job.work_id,
            WorkMember.user_id == job.requested_by_user_id,
            WorkMember.status == "ACCEPTED",
            WorkMember.role.in_(["OWNER", "EDITOR"]),
        )
    )
    if requester is None or not requester.is_active or member is None:
        return "WORK_ACCESS_DENIED"
    if work.revision != job.input_revision:
        return "REVISION_CONFLICT"
    if job.input_snapshot_json is not None and not isinstance(job.input_snapshot_json, dict):
        return "REVISION_CONFLICT"
    config = db.get(ModelConfigVersion, job.model_config_version_id)
    identity = db.get(ModelConfiguration, config.model_id) if config and config.model_id else None
    provider = config.provider if config else None
    if config is None or (identity is not None and not identity.enabled) or (identity is None and (provider is None or not provider.enabled)):
        return "MODEL_CONFIG_NOT_AVAILABLE"
    if job.consent_id is not None:
        consent = db.get(ExternalConsent, job.consent_id)
        if consent is None or consent.status != "ACTIVE":
            return "EXTERNAL_CONSENT_REQUIRED"
    return None


def recover_generation_state(session_factory) -> list[str]:
    """Synchronous startup recovery, run before the app serves traffic.

    RUNNING jobs become FAILED/WORKER_INTERRUPTED with their locks released;
    QUEUED jobs are revalidated and, if still valid, returned in enqueue order.
    A real database error propagates so startup fails fast rather than running
    in a partially-recovered state.
    """
    now = datetime.now(timezone.utc)
    enqueue_ids: list[str] = []
    with session_factory() as db:
        for job in db.scalars(select(GenerationJob).where(GenerationJob.status == "RUNNING")).all():
            job.status = "FAILED"
            job.error_code = "WORKER_INTERRUPTED"
            job.error_message = "generation did not complete; the previous draft is preserved"
            job.finished_at = now
            release(db, job.work_id, job.id)
        for row in db.scalars(select(CreativeConversation).where(CreativeConversation.status.in_(["QUEUED", "RUNNING"]))).all():
            finish_conversation(db, row, "INCOMPLETE", "WORKER_INTERRUPTED")
        db.commit()

    with session_factory() as db:
        queued = db.scalars(
            select(GenerationJob).where(GenerationJob.status == "QUEUED").order_by(GenerationJob.created_at, GenerationJob.id)
        ).all()
        for job in queued:
            failure = _queued_job_is_valid(db, job)
            if failure is None:
                enqueue_ids.append(job.id)
                continue
            job.status = "FAILED"
            job.error_code = failure
            job.error_message = "queued task failed pre-flight validation before it could resume"
            job.finished_at = now
            release(db, job.work_id, job.id)
        active_job_ids = set(db.scalars(select(GenerationJob.id).where(GenerationJob.status.in_(["QUEUED", "RUNNING"]))).all())
        active_chat_ids = set(db.scalars(select(CreativeConversation.id).where(CreativeConversation.status.in_(["QUEUED", "RUNNING"]))).all())
        active_refs = active_job_ids | active_chat_ids
        for activity in db.scalars(select(WorkAIActivity)).all():
            if activity.reference_id not in active_refs:
                db.delete(activity)
        db.commit()
    return enqueue_ids
