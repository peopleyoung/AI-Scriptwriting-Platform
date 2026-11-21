"""Track confirmation provenance, dependency chains and stale proposal causes.

Revision ID: 0010_creative_confirmation_history
Revises: 0009_generation_retry_lineage
"""
from uuid import uuid4
import logging

from alembic import op
import sqlalchemy as sa

revision = "0010_creative_confirmation_history"
down_revision = "0009_generation_retry_lineage"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")
STAGES = ("brief", "characters", "outline")


def upgrade():
    op.add_column("work_creative_data", sa.Column("stage_status_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("work_creative_data", sa.Column("confirmation_source_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("work_creative_data", sa.Column("confirmation_event_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("work_creative_data", sa.Column("confirmation_dependencies_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("work_creative_data", sa.Column("confirmation_history_complete", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("work_creative_data", sa.Column("proposal_history_complete", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("change_proposals", sa.Column("stale_reason", sa.String(length=64), nullable=True))
    op.add_column("change_proposals", sa.Column("dependencies_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table(
        "creative_confirmation_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("work_id", sa.String(length=36), sa.ForeignKey("works.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("dependencies_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("trigger_event_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_creative_confirmation_events_work_id", "creative_confirmation_events", ["work_id"])
    op.create_index(
        "ix_creative_confirmation_events_work_stage",
        "creative_confirmation_events",
        ["work_id", "stage", "recorded_at"],
    )

    bind = op.get_bind()
    creative = sa.Table("work_creative_data", sa.MetaData(), autoload_with=bind)
    proposals = sa.Table("change_proposals", sa.MetaData(), autoload_with=bind)
    history = sa.Table("creative_confirmation_events", sa.MetaData(), autoload_with=bind)
    works = sa.Table("works", sa.MetaData(), autoload_with=bind)
    now = sa.func.now()
    counts = {"works": 0, "legacy_baselines": 0, "needs_reconfirmation": 0, "stale_proposals": 0}

    for row in bind.execute(sa.select(creative)).mappings():
        counts["works"] += 1
        original = {stage: row[f"{stage}_confirmed_json"] for stage in STAGES}
        draft = {stage: row[f"{stage}_json"] for stage in STAGES}
        states = {stage: "DRAFT" for stage in STAGES}
        sources = {}
        event_ids = {}
        dependencies = {}
        for stage in STAGES:
            content = original[stage]
            if content is None:
                continue
            event_id = str(uuid4())
            upstream = {"brief": (), "characters": ("brief",), "outline": ("brief", "characters")}[stage]
            deps = {name: event_ids[name] for name in upstream if states.get(name) == "CONFIRMED" and name in event_ids}
            valid = all(name in deps for name in upstream)
            bind.execute(
                history.insert().values(
                    id=event_id,
                    work_id=row["work_id"],
                    stage=stage,
                    event_type="LEGACY_BASELINE",
                    content_json=content,
                    source="LEGACY_BASELINE",
                    actor_user_id=None,
                    confirmed_at=None,
                    dependencies_json=deps,
                    reason="DEPENDENCY_UNVERIFIED" if valid else "LEGACY_DEPENDENCY_UNVERIFIED",
                    recorded_at=now,
                )
            )
            sources[stage] = "LEGACY_BASELINE"
            dependencies[stage] = deps
            counts["legacy_baselines"] += 1
            if valid:
                states[stage] = "CONFIRMED"
                event_ids[stage] = event_id
            else:
                states[stage] = "NEEDS_RECONFIRMATION"
                counts["needs_reconfirmation"] += 1
                if draft[stage] is None:
                    draft[stage] = content
                bind.execute(history.insert().values(
                    id=str(uuid4()),
                    work_id=row["work_id"],
                    stage=stage,
                    event_type="INVALIDATED",
                    content_json=content,
                    source="LEGACY_DEPENDENCY_UNVERIFIED",
                    actor_user_id=None,
                    confirmed_at=None,
                    dependencies_json=deps,
                    reason="LEGACY_DEPENDENCY_UNVERIFIED",
                    recorded_at=now,
                ))

        values = {
            "stage_status_json": states,
            "confirmation_source_json": sources,
            "confirmation_event_ids_json": event_ids,
            "confirmation_dependencies_json": dependencies,
            "proposal_history_complete": True,
            "confirmation_history_complete": False,
        }
        for stage in STAGES:
            if states[stage] == "NEEDS_RECONFIRMATION":
                values[f"{stage}_confirmed_json"] = None
                values[f"{stage}_json"] = draft[stage]
                values[f"{stage}_revision"] = row[f"{stage}_revision"] + 1
        bind.execute(creative.update().where(creative.c.work_id == row["work_id"]).values(**values))
        if any(state == "NEEDS_RECONFIRMATION" for state in states.values()):
            bind.execute(
                works.update().where(works.c.id == row["work_id"]).values(
                    revision=works.c.revision + 1,
                    updated_at=now,
                )
            )

    for proposal in bind.execute(sa.select(proposals)).mappings():
        if proposal["status"] != "PENDING":
            continue
        reason = None
        if proposal["stage"] in {"characters", "outline"}:
            reason = "LEGACY_DEPENDENCY_UNVERIFIED"
        else:
            brief_state = bind.execute(
                sa.select(creative.c.brief_revision, creative.c.brief_confirmed_json).where(creative.c.work_id == proposal["work_id"])
            ).one_or_none()
            if brief_state is None or brief_state.brief_confirmed_json is None or proposal["base_revision"] != brief_state.brief_revision:
                reason = "BASE_UNVERIFIED"
        if reason:
            bind.execute(
                proposals.update()
                .where(proposals.c.id == proposal["id"], proposals.c.status == "PENDING")
                .values(status="STALE", stale_reason=reason)
            )
            bind.execute(history.insert().values(
                id=str(uuid4()),
                work_id=proposal["work_id"],
                stage=proposal["stage"],
                event_type="PROPOSAL_STALE",
                content_json=proposal["content_json"],
                source=reason,
                actor_user_id=None,
                confirmed_at=None,
                dependencies_json={},
                reason=reason,
                recorded_at=now,
            ))
            counts["stale_proposals"] += 1

    log.info("R4 legacy migration classification: %s", counts)


def downgrade():
    op.drop_index("ix_creative_confirmation_events_work_stage", table_name="creative_confirmation_events")
    op.drop_index("ix_creative_confirmation_events_work_id", table_name="creative_confirmation_events")
    op.drop_table("creative_confirmation_events")
    with op.batch_alter_table("change_proposals") as batch:
        batch.drop_column("dependencies_json")
        batch.drop_column("stale_reason")
    with op.batch_alter_table("work_creative_data") as batch:
        batch.drop_column("proposal_history_complete")
        batch.drop_column("confirmation_history_complete")
        batch.drop_column("confirmation_dependencies_json")
        batch.drop_column("confirmation_event_ids_json")
        batch.drop_column("confirmation_source_json")
        batch.drop_column("stage_status_json")
