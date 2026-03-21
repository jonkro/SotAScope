"""Project merge service.

Implements preview and non-destructive merge: content from the source project
is copied into the target project. The source project is left intact.

Copy checklist:
  TopicList (unique name)  → new TopicList row in target + copy TopicListWork rows
  TopicList (same name)    → copy missing TopicListWork rows into target list
  ProjectIgnoredWork       → copy rows (skip duplicates; seed wins over ignored)
  ProjectVenueTier         → copy source-only overrides; resolve conflicts on target row
  WorkNote (project-scoped)→ copy rows with project_id=target
  ExtractionSchema         → deep copy (+ ExtractionColumn rows); apply rename/drop decision;
                             returns source_id→new_id mapping used for ChatSession remapping
  ChatSession              → deep copy (+ ChatMessage rows); context_id remapped via schema map;
                             sessions whose schema was dropped get context_type reset to "papers"
"""

from __future__ import annotations

import logging

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from sotascope.models.chat import ChatMessage, ChatSession
from sotascope.models.extraction import ExtractionColumn, ExtractionSchema
from sotascope.models.library import Venue, WorkNote
from sotascope.models.project import (
    Project,
    ProjectIgnoredWork,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)
from sotascope.schemas.project_merge import (
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
                action="copy",
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


def _copy_schema(
    source_schema: ExtractionSchema,
    new_title: str,
    target_id: int,
    db: Session,
) -> int:
    """Create a deep copy of an ExtractionSchema (with all columns) in target project.

    Returns the new schema's ID so callers can remap context_id references.
    """
    new_schema = ExtractionSchema(
        project_id=target_id,
        title=new_title,
        description=source_schema.description,
    )
    db.add(new_schema)
    db.flush()
    for col in source_schema.columns:
        db.add(ExtractionColumn(
            schema_id=new_schema.id,
            name=col.name,
            prompt=col.prompt,
            description=col.description,
            allowed_values=col.allowed_values,
            sort_order=col.sort_order,
        ))
    return new_schema.id


def execute_merge(
    target_id: int,
    source_id: int,
    decisions: MergeDecisions,
    db: Session,
) -> Project:
    """Copy content from source project into target. Source is NOT modified or deleted."""

    # ------------------------------------------------------------------ a.
    # Topic lists
    # ------------------------------------------------------------------ a.

    # Capture source seed IDs before any writes (used later for seed-wins logic)
    source_seed_ids: set[int] = set(
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

    selected_ids: set[int] | None = (
        set(decisions.selected_topic_list_ids)
        if decisions.selected_topic_list_ids is not None
        else None
    )

    for sl in source_lists:
        if selected_ids is not None and sl.id not in selected_ids:
            continue
        source_works = db.scalars(
            select(TopicListWork).where(TopicListWork.topic_list_id == sl.id)
        ).all()
        if sl.name in target_list_by_name:
            # Same-name list: add missing works to target list (no changes to source)
            target_tl = target_list_by_name[sl.name]
            existing_in_target: set[int] = set(
                db.scalars(
                    select(TopicListWork.work_id).where(
                        TopicListWork.topic_list_id == target_tl.id
                    )
                ).all()
            )
            for tlw in source_works:
                if tlw.work_id not in existing_in_target:
                    db.add(TopicListWork(topic_list_id=target_tl.id, work_id=tlw.work_id))
        else:
            # Unique name: create a new list in target and copy memberships
            new_tl = TopicList(project_id=target_id, name=sl.name, color=sl.color)
            db.add(new_tl)
            db.flush()
            for tlw in source_works:
                db.add(TopicListWork(topic_list_id=new_tl.id, work_id=tlw.work_id))

    db.flush()

    # ------------------------------------------------------------------ b.
    # Seed-wins: remove ignore entries from target for source seeds
    # ------------------------------------------------------------------ b.
    if source_seed_ids:
        db.execute(
            sa_delete(ProjectIgnoredWork)
            .where(
                ProjectIgnoredWork.project_id == target_id,
                ProjectIgnoredWork.work_id.in_(source_seed_ids),
            )
            .execution_options(synchronize_session=False)
        )
        db.flush()

    # ------------------------------------------------------------------ c.
    # Ignored works: copy from source to target
    # ------------------------------------------------------------------ c.
    target_seed_ids: set[int] = set(
        db.scalars(
            select(TopicListWork.work_id)
            .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
            .where(TopicList.project_id == target_id)
        ).all()
    )
    target_ignored_ids: set[int] = set(
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
        if piw.work_id not in target_seed_ids and piw.work_id not in target_ignored_ids:
            db.add(ProjectIgnoredWork(project_id=target_id, work_id=piw.work_id))

    db.flush()

    # ------------------------------------------------------------------ d.
    # Extraction schemas: deep copy
    # ------------------------------------------------------------------ d.
    target_schemas = db.scalars(
        select(ExtractionSchema).where(ExtractionSchema.project_id == target_id)
    ).all()
    source_schemas = db.scalars(
        select(ExtractionSchema)
        .where(ExtractionSchema.project_id == source_id)
        .options(selectinload(ExtractionSchema.columns))
    ).all()
    target_schema_titles: set[str] = {s.title for s in target_schemas}

    # Maps source schema ID → new target schema ID (for ChatSession context remapping).
    # Source schemas that are dropped are absent from this map.
    schema_id_map: dict[int, int] = {}

    for ss in source_schemas:
        if ss.title in target_schema_titles:
            # Conflicting title: apply user decision (default: drop)
            decision = decisions.schema_decisions.get(ss.id)
            if decision is not None and decision.action == "rename":
                new_id = _copy_schema(ss, decision.new_name or f"{ss.title} (merged)", target_id, db)
                schema_id_map[ss.id] = new_id
            # else drop: don't copy (schema_id_map has no entry for ss.id)
        else:
            # No conflict: copy to target
            new_id = _copy_schema(ss, ss.title, target_id, db)
            schema_id_map[ss.id] = new_id

    db.flush()

    # ------------------------------------------------------------------ e.
    # Project venue tiers
    # ------------------------------------------------------------------ e.
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
            # Conflict: apply chosen tier to target row (source row stays in source)
            target_pvt = target_overrides[pvt.venue_id]
            chosen_tier = decisions.venue_tier_decisions.get(pvt.venue_id)
            if chosen_tier is not None:
                target_pvt.tier = chosen_tier
            # else: keep target's existing tier (no action)
        else:
            # Only source has this override: copy to target
            db.add(ProjectVenueTier(project_id=target_id, venue_id=pvt.venue_id, tier=pvt.tier))

    db.flush()

    # ------------------------------------------------------------------ f.
    # Work notes (project-scoped): copy to target
    # ------------------------------------------------------------------ f.
    source_notes = db.scalars(
        select(WorkNote).where(WorkNote.project_id == source_id)
    ).all()
    for note in source_notes:
        db.add(WorkNote(
            work_id=note.work_id,
            project_id=target_id,
            content=note.content,
            note_type=note.note_type,
            provenance=note.provenance,
            model_id=note.model_id,
            is_outdated=note.is_outdated,
        ))

    # ------------------------------------------------------------------ g.
    # Chat sessions: deep copy with context_id remapping
    # ------------------------------------------------------------------ g.
    source_sessions = db.scalars(
        select(ChatSession)
        .where(ChatSession.project_id == source_id)
        .options(selectinload(ChatSession.messages))
    ).all()
    for session in source_sessions:
        ctx_type = session.context_type
        ctx_id = session.context_id
        if ctx_type == "extraction_schema" and ctx_id is not None:
            if ctx_id in schema_id_map:
                # Remap to the new copy of the schema in target
                ctx_id = schema_id_map[ctx_id]
            else:
                # Schema was dropped: reset to generic papers mode
                ctx_type = "papers"
                ctx_id = None
        new_session = ChatSession(
            project_id=target_id,
            work_id=session.work_id,
            context_type=ctx_type,
            context_id=ctx_id,
            title=session.title,
            is_auto=session.is_auto,
        )
        db.add(new_session)
        db.flush()
        for msg in session.messages:
            db.add(ChatMessage(
                session_id=new_session.id,
                role=msg.role,
                content=msg.content,
            ))

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
