"""Project merge service.

Implements preview and absorptive merge: source project is absorbed into
target project and then deleted.

FK checklist (all must be repointed to target before source is deleted):
  TopicList          project_id → target
  TopicListWork      topic_list_id → target list  (for same-name merges)
  ProjectIgnoredWork project_id → target  (or deleted when seed wins)
  ProjectVenueTier   project_id → target  (or deleted when conflict resolved)
  WorkNote           project_id → target
  ChatSession        project_id → target
  ExtractionSchema   project_id → target  (or deleted on "drop" decision)
"""

from __future__ import annotations

import logging

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session, selectinload

from litexplorer.models.chat import ChatSession
from litexplorer.models.extraction import ExtractionSchema
from litexplorer.models.library import Venue, WorkNote
from litexplorer.models.project import (
    Project,
    ProjectIgnoredWork,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)
from litexplorer.schemas.project_merge import (
    MergeDecisions,
    MergePreview,
    SchemaConflictInfo,
    TopicListMergeInfo,
    VenueTierConflictInfo,
)

logger = logging.getLogger(__name__)


def _preferred_name(venue: Venue) -> str:
    """Return preferred display name: first alias by sort_order, else canonical name."""
    if venue.aliases:
        return venue.aliases[0].alias
    return venue.name


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def merge_preview(target_id: int, source_id: int, db: Session) -> MergePreview:
    """Compute a preview of what merging source into target would do."""

    # --- a. Topic list merges ---
    target_lists = db.scalars(
        select(TopicList).where(TopicList.project_id == target_id)
    ).all()
    source_lists = db.scalars(
        select(TopicList).where(TopicList.project_id == source_id)
    ).all()
    target_list_by_name = {tl.name: tl for tl in target_lists}

    topic_list_merges: list[TopicListMergeInfo] = []
    for sl in sorted(source_lists, key=lambda x: x.name):
        if sl.name in target_list_by_name:
            tl = target_list_by_name[sl.name]
            topic_list_merges.append(TopicListMergeInfo(
                source_topic_list_id=sl.id,
                source_topic_list_name=sl.name,
                action="merge",
                target_topic_list_id=tl.id,
            ))
        else:
            topic_list_merges.append(TopicListMergeInfo(
                source_topic_list_id=sl.id,
                source_topic_list_name=sl.name,
                action="move",
                target_topic_list_id=None,
            ))

    # --- b. Schema conflicts (same title in both projects) ---
    target_schemas = db.scalars(
        select(ExtractionSchema).where(ExtractionSchema.project_id == target_id)
    ).all()
    source_schemas = db.scalars(
        select(ExtractionSchema).where(ExtractionSchema.project_id == source_id)
    ).all()
    target_schema_by_name = {s.title: s for s in target_schemas}

    schema_conflicts: list[SchemaConflictInfo] = []
    for ss in sorted(source_schemas, key=lambda x: x.title):
        if ss.title in target_schema_by_name:
            ts = target_schema_by_name[ss.title]
            schema_conflicts.append(SchemaConflictInfo(
                source_schema_id=ss.id,
                source_schema_name=ss.title,
                target_schema_id=ts.id,
                target_schema_name=ts.title,
            ))

    # --- c. Venue tier conflicts (both projects override same venue with different tiers) ---
    source_overrides = db.scalars(
        select(ProjectVenueTier).where(ProjectVenueTier.project_id == source_id)
    ).all()
    target_overrides_map: dict[int, int] = {
        row.venue_id: row.tier
        for row in db.scalars(
            select(ProjectVenueTier).where(ProjectVenueTier.project_id == target_id)
        ).all()
    }

    conflict_venue_ids: set[int] = {
        pvt.venue_id
        for pvt in source_overrides
        if pvt.venue_id in target_overrides_map
        and target_overrides_map[pvt.venue_id] != pvt.tier
    }

    venue_name_map: dict[int, str] = {}
    if conflict_venue_ids:
        venues = db.scalars(
            select(Venue)
            .where(Venue.id.in_(conflict_venue_ids))
            .options(selectinload(Venue.aliases))
        ).all()
        venue_name_map = {v.id: _preferred_name(v) for v in venues}

    venue_tier_conflicts: list[VenueTierConflictInfo] = []
    for pvt in sorted(source_overrides, key=lambda x: x.venue_id):
        if pvt.venue_id in conflict_venue_ids:
            venue_tier_conflicts.append(VenueTierConflictInfo(
                venue_id=pvt.venue_id,
                venue_name=venue_name_map.get(pvt.venue_id, str(pvt.venue_id)),
                source_tier=pvt.tier,
                target_tier=target_overrides_map[pvt.venue_id],
            ))

    # --- d. Ignored-vs-seed overrides ---
    source_ignored_ids: set[int] = set(
        db.scalars(
            select(ProjectIgnoredWork.work_id).where(
                ProjectIgnoredWork.project_id == source_id
            )
        ).all()
    )
    target_ignored_ids: set[int] = set(
        db.scalars(
            select(ProjectIgnoredWork.work_id).where(
                ProjectIgnoredWork.project_id == target_id
            )
        ).all()
    )
    target_seed_ids: set[int] = set(
        db.scalars(
            select(TopicListWork.work_id)
            .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
            .where(TopicList.project_id == target_id)
        ).all()
    )
    source_seed_ids: set[int] = set(
        db.scalars(
            select(TopicListWork.work_id)
            .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
            .where(TopicList.project_id == source_id)
        ).all()
    )
    # Work IDs that would be auto-resolved (seed wins over ignored)
    ignored_work_overrides: list[int] = sorted(
        (source_ignored_ids & target_seed_ids) | (target_ignored_ids & source_seed_ids)
    )

    # --- e. Informational counts ---
    source_chat_session_count: int = (
        db.scalar(
            select(func.count(ChatSession.id)).where(
                ChatSession.project_id == source_id
            )
        )
        or 0
    )
    source_note_count: int = (
        db.scalar(
            select(func.count(WorkNote.id)).where(WorkNote.project_id == source_id)
        )
        or 0
    )

    return MergePreview(
        topic_list_merges=topic_list_merges,
        schema_conflicts=schema_conflicts,
        venue_tier_conflicts=venue_tier_conflicts,
        ignored_work_overrides=ignored_work_overrides,
        source_chat_session_count=source_chat_session_count,
        source_note_count=source_note_count,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def execute_merge(
    target_id: int,
    source_id: int,
    decisions: MergeDecisions,
    db: Session,
) -> Project:
    """Absorb source project into target and delete source.

    Execution order mirrors the FK checklist so all repoints happen before
    the final CASCADE delete of the source project.
    """

    # ------------------------------------------------------------------ a.
    # Topic lists
    # ------------------------------------------------------------------ a.

    # Capture source seed IDs before step a moves topic lists to target
    pre_merge_source_seed_ids: set[int] = set(
        db.scalars(
            select(TopicListWork.work_id)
            .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
            .where(TopicList.project_id == source_id)
        ).all()
    )

    target_lists = db.scalars(
        select(TopicList).where(TopicList.project_id == target_id)
    ).all()
    source_lists = db.scalars(
        select(TopicList).where(TopicList.project_id == source_id)
    ).all()
    target_list_by_name = {tl.name: tl for tl in target_lists}

    for sl in source_lists:
        if sl.name in target_list_by_name:
            # Same-name list: merge memberships into target list
            target_tl = target_list_by_name[sl.name]
            existing_in_target: set[int] = set(
                db.scalars(
                    select(TopicListWork.work_id).where(
                        TopicListWork.topic_list_id == target_tl.id
                    )
                ).all()
            )
            source_works = db.scalars(
                select(TopicListWork).where(TopicListWork.topic_list_id == sl.id)
            ).all()
            for tlw in source_works:
                if tlw.work_id not in existing_in_target:
                    tlw.topic_list_id = target_tl.id
                else:
                    db.delete(tlw)
            # sl itself (now empty) will be deleted by CASCADE when source is removed
        else:
            # Unique name: move ownership to target
            sl.project_id = target_id

    db.flush()

    # ------------------------------------------------------------------ b.
    # Ignored works  (seed wins over ignored)
    # ------------------------------------------------------------------ b.

    # Re-query target seeds after topic list repoints (step a may have moved source lists)
    target_seed_ids: set[int] = set(
        db.scalars(
            select(TopicListWork.work_id)
            .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
            .where(TopicList.project_id == target_id)
        ).all()
    )

    # Seeds from source win: delete any conflicting ignore entries in target.
    # Use pre-merge source seeds since unique-name lists were already moved to target in step a.
    if pre_merge_source_seed_ids:
        db.execute(
            sa_delete(ProjectIgnoredWork)
            .where(
                ProjectIgnoredWork.project_id == target_id,
                ProjectIgnoredWork.work_id.in_(pre_merge_source_seed_ids),
            )
            .execution_options(synchronize_session=False)
        )
        db.flush()

    # Now move / prune source's ignored works
    target_ignored_now: set[int] = set(
        db.scalars(
            select(ProjectIgnoredWork.work_id).where(
                ProjectIgnoredWork.project_id == target_id
            )
        ).all()
    )
    source_ignored = db.scalars(
        select(ProjectIgnoredWork).where(ProjectIgnoredWork.project_id == source_id)
    ).all()
    for piw in source_ignored:
        if piw.work_id in target_seed_ids:
            # Seed in target wins: drop source's ignore entry
            db.delete(piw)
        elif piw.work_id in target_ignored_now:
            # Already ignored in target: drop duplicate
            db.delete(piw)
        else:
            # Move to target
            piw.project_id = target_id

    db.flush()

    # ------------------------------------------------------------------ c.
    # Extraction schemas
    # ------------------------------------------------------------------ c.
    target_schemas = db.scalars(
        select(ExtractionSchema).where(ExtractionSchema.project_id == target_id)
    ).all()
    source_schemas = db.scalars(
        select(ExtractionSchema).where(ExtractionSchema.project_id == source_id)
    ).all()
    target_schema_titles: set[str] = {s.title for s in target_schemas}

    for ss in source_schemas:
        if ss.title in target_schema_titles:
            # Conflicting title: apply user decision (default: drop)
            decision = decisions.schema_decisions.get(ss.id)
            if decision is not None and decision.action == "rename":
                ss.title = decision.new_name or f"{ss.title} (merged)"
                ss.project_id = target_id
            else:
                # drop: CASCADE deletes ExtractionColumns; WorkNotes are unaffected
                db.delete(ss)
        else:
            # No conflict: move to target
            ss.project_id = target_id

    db.flush()

    # ------------------------------------------------------------------ d.
    # Project venue tiers
    # ------------------------------------------------------------------ d.
    target_overrides: dict[int, ProjectVenueTier] = {
        row.venue_id: row
        for row in db.scalars(
            select(ProjectVenueTier).where(ProjectVenueTier.project_id == target_id)
        ).all()
    }
    source_overrides_list = db.scalars(
        select(ProjectVenueTier).where(ProjectVenueTier.project_id == source_id)
    ).all()

    for pvt in source_overrides_list:
        if pvt.venue_id in target_overrides:
            # Conflict (or same tier): apply chosen tier to target, delete source row
            target_pvt = target_overrides[pvt.venue_id]
            chosen_tier = decisions.venue_tier_decisions.get(pvt.venue_id)
            if chosen_tier is not None:
                target_pvt.tier = chosen_tier
            # Always delete source's duplicate row
            db.delete(pvt)
        else:
            # Only source has this override: move row to target
            pvt.project_id = target_id

    db.flush()

    # ------------------------------------------------------------------ e.
    # Work notes (project-scoped)
    # ------------------------------------------------------------------ e.
    db.execute(
        sa_update(WorkNote)
        .where(WorkNote.project_id == source_id)
        .values(project_id=target_id)
        .execution_options(synchronize_session=False)
    )

    # ------------------------------------------------------------------ f.
    # Chat sessions
    # ------------------------------------------------------------------ f.
    db.execute(
        sa_update(ChatSession)
        .where(ChatSession.project_id == source_id)
        .values(project_id=target_id)
        .execution_options(synchronize_session=False)
    )

    db.flush()

    # ------------------------------------------------------------------ g.
    # Delete source project (CASCADE cleans up any remaining owned rows)
    # ------------------------------------------------------------------ g.
    source = db.get(Project, source_id)
    if source:
        db.delete(source)

    db.commit()

    # Reload target with relationships
    from sqlalchemy.orm import joinedload

    target = db.scalars(
        select(Project)
        .where(Project.id == target_id)
        .options(
            joinedload(Project.topic_lists),
            joinedload(Project.ignored_work_associations).joinedload(
                ProjectIgnoredWork.work
            ),
        )
    ).unique().one()
    return target
