"""Read-only counts for the R4 legacy confirmation/proposal migration."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

from sqlalchemy import MetaData, create_engine, select


def main() -> int:
    url = os.getenv("APP_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("Set APP_DATABASE_URL or DATABASE_URL to the database to inspect.", file=sys.stderr)
        return 2

    engine = create_engine(url, pool_pre_ping=True)
    metadata = MetaData()
    try:
        metadata.reflect(engine, only=["works", "work_creative_data", "change_proposals"])
        works = metadata.tables["works"]
        creative = metadata.tables["work_creative_data"]
        proposals = metadata.tables["change_proposals"]
        stage_counts: Counter[str] = Counter()
        work_counts: Counter[str] = Counter()
        proposal_counts: Counter[str] = Counter()

        with engine.connect() as connection:
            columns = set(creative.c.keys())
            has_r4_state = "stage_status_json" in columns
            requested_columns = [
                works.c.id,
                creative.c.work_id,
                *(getattr(creative.c, f"{stage}_confirmed_json") for stage in ("brief", "characters", "outline")),
                creative.c.brief_revision,
            ]
            if has_r4_state:
                requested_columns.append(creative.c.stage_status_json)
            if "confirmation_event_ids_json" in columns:
                requested_columns.append(creative.c.confirmation_event_ids_json)
            rows = connection.execute(
                select(*requested_columns).select_from(works.outerjoin(creative, creative.c.work_id == works.c.id))
            ).mappings().all()
            rows_by_work = {row["id"]: row for row in rows}
            for row in rows:
                content = {stage: row.get(f"{stage}_confirmed_json") for stage in ("brief", "characters", "outline")}
                present = {stage: content[stage] is not None for stage in content}
                if row.get("work_id") is None:
                    states = {stage: "DRAFT" for stage in content}
                    work_counts["NO_CREATIVE_DATA_DRAFT"] += 1
                elif has_r4_state:
                    stored = row.get("stage_status_json") or {}
                    states = {stage: stored.get(stage, "DRAFT") for stage in content}
                    work_counts["R4_CURRENT_STATE"] += 1
                else:
                    states = {
                        "brief": "CONFIRMED" if present["brief"] else "DRAFT",
                        "characters": "CONFIRMED" if present["characters"] and present["brief"] else "NEEDS_RECONFIRMATION" if present["characters"] else "DRAFT",
                        "outline": "CONFIRMED" if present["outline"] and present["brief"] and present["characters"] else "NEEDS_RECONFIRMATION" if present["outline"] else "DRAFT",
                    }
                    complete = all(present.values())
                    broken = any(states[stage] == "NEEDS_RECONFIRMATION" for stage in states)
                    work_counts["LEGACY_COMPLETE_BASELINE" if complete else "LEGACY_BROKEN_DEPENDENCY" if broken else "LEGACY_PREFIX_OR_DRAFT"] += 1
                for stage, state in states.items():
                    stage_counts[f"{stage}:{state}"] += 1

            proposal_columns = set(proposals.c.keys())
            for proposal in connection.execute(select(proposals)).mappings():
                status = proposal["status"]
                stage = proposal["stage"]
                if status != "PENDING":
                    continue
                if "stale_reason" in proposal_columns and proposal.get("stale_reason"):
                    reason = proposal["stale_reason"]
                elif stage in {"characters", "outline"}:
                    data = rows_by_work.get(proposal["work_id"])
                    if has_r4_state and "dependencies_json" in proposal_columns and data is not None:
                        stored_states = data.get("stage_status_json") or {}
                        event_ids = data.get("confirmation_event_ids_json") or {}
                        captured = proposal.get("dependencies_json") or {}
                        upstream = ("brief",) if stage == "characters" else ("brief", "characters")
                        expected = {name: event_ids[name] for name in upstream if event_ids.get(name)}
                        current = (
                            len(expected) == len(upstream)
                            and all(stored_states.get(name) == "CONFIRMED" for name in upstream)
                            and captured == expected
                        )
                        reason = "RETAIN_PENDING" if current else "UPSTREAM_CONFIRMATION_CHANGED" if captured else "LEGACY_DEPENDENCY_UNVERIFIED"
                    else:
                        reason = "LEGACY_DEPENDENCY_UNVERIFIED"
                else:
                    data = rows_by_work.get(proposal["work_id"])
                    reason = "BASE_UNVERIFIED" if data is None or data["brief_confirmed_json"] is None or proposal["base_revision"] != data["brief_revision"] else "RETAIN_PENDING"
                proposal_counts[f"{stage}:{reason}"] += 1

        print(json.dumps({
            "read_only": True,
            "works_by_class": dict(sorted(work_counts.items())),
            "stages_by_state": dict(sorted(stage_counts.items())),
            "pending_proposals_by_stage_and_reason": dict(sorted(proposal_counts.items())),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
