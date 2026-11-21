"""Shared confirmation-chain rules for API, workers and startup recovery."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChangeProposal, CreativeConfirmationEvent, Work, WorkCreativeData, User

STAGES = ("brief", "characters", "outline")
UPSTREAM = {"brief": (), "characters": ("brief",), "outline": ("brief", "characters")}
DOWNSTREAM = {"brief": ("characters", "outline"), "characters": ("outline",), "outline": ()}
STAGE_NAMES = {"brief": "创作简报", "characters": "人物", "outline": "故事大纲"}


def stage_status(data: WorkCreativeData, stage: str) -> str:
    states = data.stage_status_json or {}
    value = states.get(stage)
    if value in {"DRAFT", "CONFIRMED", "NEEDS_RECONFIRMATION"}:
        return value
    return "CONFIRMED" if getattr(data, f"{stage}_confirmed_json") is not None else "DRAFT"


def stage_dependencies_current(data: WorkCreativeData, stage: str) -> bool:
    dependencies = (data.confirmation_dependencies_json or {}).get(stage, {})
    event_ids = data.confirmation_event_ids_json or {}
    return all(
        stage_status(data, upstream) == "CONFIRMED"
        and getattr(data, f"{upstream}_confirmed_json") is not None
        and stage_dependencies_current(data, upstream)
        and dependencies.get(upstream)
        and dependencies[upstream] == event_ids.get(upstream)
        for upstream in UPSTREAM[stage]
    )


def upstream_stages_confirmed(data: WorkCreativeData, stage: str) -> bool:
    return all(
        stage_status(data, upstream) == "CONFIRMED"
        and getattr(data, f"{upstream}_confirmed_json") is not None
        and stage_dependencies_current(data, upstream)
        for upstream in UPSTREAM[stage]
    )


def confirmation_chain_error(data: WorkCreativeData | None) -> str | None:
    if data is None:
        return "all creative stages must be confirmed before generation"
    for stage in STAGES:
        if stage_status(data, stage) != "CONFIRMED" or getattr(data, f"{stage}_confirmed_json") is None:
            return "all creative stages must be confirmed before generation"
        if not stage_dependencies_current(data, stage):
            # Legacy baselines have explicit synthetic event ids too. Missing references
            # therefore indicate a broken chain rather than an unknown old history.
            return "creative confirmation dependencies are no longer valid"
    return None


def proposal_dependencies(data: WorkCreativeData, stage: str) -> dict[str, str]:
    event_ids = data.confirmation_event_ids_json or {}
    return {upstream: event_ids[upstream] for upstream in UPSTREAM[stage] if event_ids.get(upstream)}


def proposal_dependencies_current(data: WorkCreativeData, stage: str, captured: dict[str, str] | None) -> bool:
    captured = captured or {}
    return stage_dependencies_current(data, stage) and captured == proposal_dependencies(data, stage)


def record_confirmation(
    db: Session,
    work: Work,
    data: WorkCreativeData,
    stage: str,
    content,
    actor: User,
    *,
    source: str = "OWNER_CONFIRMATION",
) -> CreativeConfirmationEvent:
    event_id = str(uuid4())
    event = CreativeConfirmationEvent(
        id=event_id,
        work_id=work.id,
        stage=stage,
        event_type="CONFIRMED",
        content_json=deepcopy(content),
        source=source,
        actor_user_id=actor.id,
        confirmed_at=datetime.now(timezone.utc),
        dependencies_json={
            upstream: (data.confirmation_event_ids_json or {}).get(upstream, "")
            for upstream in UPSTREAM[stage]
        },
    )
    db.add(event)
    setattr(data, f"{stage}_confirmed_json", deepcopy(content))
    data.stage_status_json = {**(data.stage_status_json or {}), stage: "CONFIRMED"}
    data.confirmation_source_json = {**(data.confirmation_source_json or {}), stage: source}
    data.confirmation_event_ids_json = {**(data.confirmation_event_ids_json or {}), stage: event_id}
    data.confirmation_dependencies_json = {
        **(data.confirmation_dependencies_json or {}),
        stage: deepcopy(event.dependencies_json),
    }
    return event


def record_legacy_baselines(db: Session, work_id: str, data: WorkCreativeData) -> None:
    """Create explicit compatibility markers when restoring a pre-R4 snapshot."""
    states = {stage: "DRAFT" for stage in STAGES}
    sources = {}
    event_ids = {}
    dependencies = {}
    for stage in STAGES:
        content = getattr(data, f"{stage}_confirmed_json")
        if content is None:
            continue
        event_id = str(uuid4())
        deps = {upstream: event_ids[upstream] for upstream in UPSTREAM[stage] if upstream in event_ids}
        valid = all(upstream in deps for upstream in UPSTREAM[stage])
        db.add(CreativeConfirmationEvent(
            id=event_id,
            work_id=work_id,
            stage=stage,
            event_type="LEGACY_BASELINE",
            content_json=deepcopy(content),
            source="LEGACY_BACKUP_BASELINE",
            actor_user_id=None,
            confirmed_at=None,
            dependencies_json=deps,
            reason="DEPENDENCY_UNVERIFIED" if valid else "LEGACY_DEPENDENCY_UNVERIFIED",
        ))
        sources[stage] = "LEGACY_BACKUP_BASELINE"
        dependencies[stage] = deps
        if valid:
            states[stage] = "CONFIRMED"
            event_ids[stage] = event_id
        else:
            states[stage] = "NEEDS_RECONFIRMATION"
            if getattr(data, f"{stage}_json") is None:
                setattr(data, f"{stage}_json", deepcopy(content))
            setattr(data, f"{stage}_confirmed_json", None)
    data.stage_status_json = states
    data.confirmation_source_json = sources
    data.confirmation_event_ids_json = event_ids
    data.confirmation_dependencies_json = dependencies
    data.confirmation_history_complete = False
    data.proposal_history_complete = False


def invalidate_downstream(
    db: Session,
    work: Work,
    data: WorkCreativeData,
    upstream_stage: str,
    trigger_event: CreativeConfirmationEvent,
    actor: User,
) -> list[str]:
    changed: list[str] = []
    states = dict(data.stage_status_json or {})
    for stage in DOWNSTREAM[upstream_stage]:
        old_confirmed = getattr(data, f"{stage}_confirmed_json")
        if old_confirmed is not None and stage_status(data, stage) == "CONFIRMED":
            if getattr(data, f"{stage}_json") is None:
                setattr(data, f"{stage}_json", deepcopy(old_confirmed))
            db.add(
                CreativeConfirmationEvent(
                    work_id=work.id,
                    stage=stage,
                    event_type="INVALIDATED",
                    content_json=deepcopy(old_confirmed),
                    source="UPSTREAM_CONFIRMATION_CHANGED",
                    actor_user_id=None,
                    confirmed_at=None,
                    dependencies_json=deepcopy((data.confirmation_dependencies_json or {}).get(stage, {})),
                    reason="UPSTREAM_CONFIRMATION_CHANGED",
                    trigger_event_id=trigger_event.id,
                )
            )
            setattr(data, f"{stage}_confirmed_json", None)
            setattr(data, f"{stage}_revision", getattr(data, f"{stage}_revision") + 1)
            states[stage] = "NEEDS_RECONFIRMATION"
            data.confirmation_source_json = {
                **(data.confirmation_source_json or {}),
                stage: "UPSTREAM_CONFIRMATION_CHANGED",
            }
            changed.append(stage)
        pending = db.scalars(
            select(ChangeProposal).where(
                ChangeProposal.work_id == work.id,
                ChangeProposal.stage == stage,
                ChangeProposal.status == "PENDING",
            )
        ).all()
        for proposal in pending:
            proposal.status = "STALE"
            proposal.stale_reason = "UPSTREAM_CONFIRMATION_CHANGED"
            proposal.rejection_reason = None
            db.add(
                CreativeConfirmationEvent(
                    work_id=work.id,
                    stage=stage,
                    event_type="PROPOSAL_STALE",
                    content_json=deepcopy(proposal.content_json),
                    source="UPSTREAM_CONFIRMATION_CHANGED",
                    actor_user_id=None,
                    confirmed_at=None,
                    dependencies_json=deepcopy(proposal.dependencies_json or {}),
                    reason="UPSTREAM_CONFIRMATION_CHANGED",
                    trigger_event_id=trigger_event.id,
                )
            )
    data.stage_status_json = states
    return changed
