"""Tests for project merge: preview and non-destructive merge execution.

Bare-imports ensure all tables exist in the in-memory test DB even when
this file is run in isolation (see CLAUDE.md "Table isolation pitfall").
"""

# --- ensure tables are created in the in-memory DB ---
from litexplorer.models.chat import ChatSession  # noqa: F401
from litexplorer.models.extraction import ExtractionSchema  # noqa: F401
from litexplorer.models.library import Citation, Venue, Work, WorkNote  # noqa: F401
from litexplorer.models.project import (
    Project,
    ProjectIgnoredWork,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)

import pytest
from sqlalchemy import func, select

from litexplorer.services.project_merge import execute_merge, merge_preview
from litexplorer.schemas.project_merge import MergeDecisions, SchemaDecision


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _project(db, name="P") -> Project:
    p = Project(name=name)
    db.add(p)
    db.flush()
    return p


def _topic_list(db, project: Project, name="TL", color="#3b82f6") -> TopicList:
    tl = TopicList(project_id=project.id, name=name, color=color)
    db.add(tl)
    db.flush()
    return tl


def _work(db, title="Paper") -> Work:
    w = Work(title=title)
    db.add(w)
    db.flush()
    return w


def _seed(db, tl: TopicList, work: Work) -> TopicListWork:
    assoc = TopicListWork(topic_list_id=tl.id, work_id=work.id)
    db.add(assoc)
    db.flush()
    return assoc


def _ignored(db, project: Project, work: Work) -> ProjectIgnoredWork:
    piw = ProjectIgnoredWork(project_id=project.id, work_id=work.id)
    db.add(piw)
    db.flush()
    return piw


def _schema(db, project: Project, title="Schema A") -> ExtractionSchema:
    s = ExtractionSchema(project_id=project.id, title=title)
    db.add(s)
    db.flush()
    return s


def _venue(db, name="NeurIPS", tier=2) -> Venue:
    v = Venue(name=name, tier=tier)
    db.add(v)
    db.flush()
    return v


def _venue_tier(db, project: Project, venue: Venue, tier: int) -> ProjectVenueTier:
    pvt = ProjectVenueTier(project_id=project.id, venue_id=venue.id, tier=tier)
    db.add(pvt)
    db.flush()
    return pvt


def _note(db, work: Work, project: Project, content="note") -> WorkNote:
    n = WorkNote(work_id=work.id, project_id=project.id, content=content)
    db.add(n)
    db.flush()
    return n


def _chat_session(db, project: Project) -> ChatSession:
    s = ChatSession(project_id=project.id, context_type="papers")
    db.add(s)
    db.flush()
    return s


# ---------------------------------------------------------------------------
# Preview tests
# ---------------------------------------------------------------------------

class TestMergePreview:

    def test_same_name_topic_list_reported_as_merge(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _topic_list(db_session, target, "Reading List")
        _topic_list(db_session, source, "Reading List")
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert len(preview.topic_list_merges) == 1
        m = preview.topic_list_merges[0]
        assert m.action == "merge"
        assert m.source_topic_list_name == "Reading List"
        assert m.target_topic_list_id is not None

    def test_unique_topic_list_reported_as_copy(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _topic_list(db_session, source, "Only in Source")
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert len(preview.topic_list_merges) == 1
        assert preview.topic_list_merges[0].action == "copy"
        assert preview.topic_list_merges[0].target_topic_list_id is None

    def test_schema_conflict_detected(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _schema(db_session, target, "Table 1")
        _schema(db_session, source, "Table 1")
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert len(preview.schema_conflicts) == 1
        sc = preview.schema_conflicts[0]
        assert sc.source_schema_name == "Table 1"
        assert sc.target_schema_name == "Table 1"

    def test_no_schema_conflict_when_names_differ(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _schema(db_session, target, "Table A")
        _schema(db_session, source, "Table B")
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert preview.schema_conflicts == []

    def test_venue_tier_conflict_detected(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        venue = _venue(db_session, "ICML")
        _venue_tier(db_session, target, venue, tier=1)
        _venue_tier(db_session, source, venue, tier=3)
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert len(preview.venue_tier_conflicts) == 1
        vc = preview.venue_tier_conflicts[0]
        assert vc.venue_id == venue.id
        assert vc.target_tier == 1
        assert vc.source_tier == 3

    def test_same_venue_tier_no_conflict(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        venue = _venue(db_session, "ICML")
        _venue_tier(db_session, target, venue, tier=1)
        _venue_tier(db_session, source, venue, tier=1)
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert preview.venue_tier_conflicts == []

    def test_ignored_work_override_reported(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session, "Important Paper")
        tl_target = _topic_list(db_session, target, "Main")
        _seed(db_session, tl_target, work)    # seed in target
        _ignored(db_session, source, work)   # ignored in source
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert work.id in preview.ignored_work_overrides

    def test_informational_counts(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session)
        _note(db_session, work, source)
        _note(db_session, work, source)
        _chat_session(db_session, source)
        db_session.commit()

        preview = merge_preview(target.id, source.id, db_session)
        assert preview.source_note_count == 2
        assert preview.source_chat_session_count == 1


# ---------------------------------------------------------------------------
# Execute merge tests
# ---------------------------------------------------------------------------

class TestExecuteMerge:

    def test_source_project_intact_after_merge(self, client, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{target.id}/merge/{source.id}", json={}
        )
        assert resp.status_code == 200

        db_session.expire_all()
        assert db_session.get(Project, source.id) is not None

    def test_returns_target_project(self, client, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{target.id}/merge/{source.id}", json={}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == target.id
        assert data["name"] == "Target"

    def test_merge_self_returns_400(self, client, db_session):
        project = _project(db_session)
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project.id}/merge/{project.id}", json={}
        )
        assert resp.status_code == 400

    def test_nonexistent_target_returns_404(self, client, db_session):
        source = _project(db_session)
        db_session.commit()

        resp = client.post(f"/api/projects/99999/merge/{source.id}", json={})
        assert resp.status_code == 404

    def test_nonexistent_source_returns_404(self, client, db_session):
        target = _project(db_session)
        db_session.commit()

        resp = client.post(f"/api/projects/{target.id}/merge/99999", json={})
        assert resp.status_code == 404

    # --- Topic lists ---

    def test_unique_topic_list_copied_to_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session)
        tl_source = _topic_list(db_session, source, "Unique List")
        _seed(db_session, tl_source, work)
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Source list is unchanged
        assert db_session.get(TopicList, tl_source.id).project_id == source.id
        # A new copy exists in target with the same name
        target_tl = db_session.scalars(
            select(TopicList).where(
                TopicList.project_id == target.id,
                TopicList.name == "Unique List",
            )
        ).one_or_none()
        assert target_tl is not None
        # And the work is seeded in the new copy
        tlw = db_session.scalars(
            select(TopicListWork).where(
                TopicListWork.topic_list_id == target_tl.id,
                TopicListWork.work_id == work.id,
            )
        ).one_or_none()
        assert tlw is not None

    def test_same_name_list_memberships_merged_no_duplicates(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work_shared = _work(db_session, "Shared")
        work_source_only = _work(db_session, "Source Only")

        tl_target = _topic_list(db_session, target, "Main")
        tl_source = _topic_list(db_session, source, "Main")
        _seed(db_session, tl_target, work_shared)
        _seed(db_session, tl_source, work_shared)       # duplicate
        _seed(db_session, tl_source, work_source_only)  # unique
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        works_in_target = set(
            db_session.scalars(
                select(TopicListWork.work_id).where(
                    TopicListWork.topic_list_id == tl_target.id
                )
            ).all()
        )
        assert work_shared.id in works_in_target
        assert work_source_only.id in works_in_target
        assert len(works_in_target) == 2  # no duplicates

    def test_source_topic_list_intact_after_merge(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        tl_target = _topic_list(db_session, target, "Main")
        tl_source = _topic_list(db_session, source, "Main")
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Both lists still exist; source list is unchanged
        assert db_session.get(TopicList, tl_source.id) is not None
        assert db_session.get(TopicList, tl_target.id) is not None

    # --- Ignored works ---

    def test_ignored_in_source_moved_to_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session, "Boring Paper")
        _ignored(db_session, source, work)
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        piw = db_session.scalars(
            select(ProjectIgnoredWork).where(
                ProjectIgnoredWork.project_id == target.id,
                ProjectIgnoredWork.work_id == work.id,
            )
        ).one_or_none()
        assert piw is not None

    def test_seed_in_target_wins_over_ignored_in_source(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session, "Good Paper")
        tl_target = _topic_list(db_session, target, "Main")
        _seed(db_session, tl_target, work)    # seed in target
        _ignored(db_session, source, work)   # ignored in source → should be dropped
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Paper must NOT appear as ignored in target
        piw = db_session.scalars(
            select(ProjectIgnoredWork).where(
                ProjectIgnoredWork.project_id == target.id,
                ProjectIgnoredWork.work_id == work.id,
            )
        ).one_or_none()
        assert piw is None

    def test_seed_in_source_wins_over_ignored_in_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session, "Good Paper")
        tl_source = _topic_list(db_session, source, "Main")
        _seed(db_session, tl_source, work)   # seed in source
        _ignored(db_session, target, work)   # ignored in target → should be removed
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        piw = db_session.scalars(
            select(ProjectIgnoredWork).where(
                ProjectIgnoredWork.project_id == target.id,
                ProjectIgnoredWork.work_id == work.id,
            )
        ).one_or_none()
        assert piw is None

    def test_duplicate_ignored_not_doubled(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session, "Boring Paper")
        _ignored(db_session, target, work)
        _ignored(db_session, source, work)
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        count = len(
            db_session.scalars(
                select(ProjectIgnoredWork).where(
                    ProjectIgnoredWork.project_id == target.id,
                    ProjectIgnoredWork.work_id == work.id,
                )
            ).all()
        )
        assert count == 1

    # --- Extraction schemas ---

    def test_non_conflicting_schema_copied_to_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        s = _schema(db_session, source, "Unique Schema")
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Source schema stays in source
        assert db_session.get(ExtractionSchema, s.id).project_id == source.id
        # A copy exists in target with the same title
        copy = db_session.scalars(
            select(ExtractionSchema).where(
                ExtractionSchema.project_id == target.id,
                ExtractionSchema.title == "Unique Schema",
            )
        ).one_or_none()
        assert copy is not None

    def test_schema_conflict_drop_decision(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        s_target = _schema(db_session, target, "Table 1")
        s_source = _schema(db_session, source, "Table 1")
        db_session.commit()

        decisions = MergeDecisions(
            schema_decisions={s_source.id: SchemaDecision(action="drop")}
        )
        execute_merge(target.id, source.id, decisions, db_session)

        db_session.expire_all()
        # Source schema stays in source (not deleted)
        assert db_session.get(ExtractionSchema, s_source.id).project_id == source.id
        # Target schema untouched; no extra copy created
        assert db_session.get(ExtractionSchema, s_target.id) is not None
        count = db_session.scalar(
            select(func.count(ExtractionSchema.id)).where(
                ExtractionSchema.project_id == target.id
            )
        )
        assert count == 1

    def test_schema_conflict_rename_decision(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _schema(db_session, target, "Table 1")
        s_source = _schema(db_session, source, "Table 1")
        db_session.commit()

        decisions = MergeDecisions(
            schema_decisions={
                s_source.id: SchemaDecision(action="rename", new_name="Table 1 (source)")
            }
        )
        execute_merge(target.id, source.id, decisions, db_session)

        db_session.expire_all()
        # Source schema stays in source with original title
        assert db_session.get(ExtractionSchema, s_source.id).title == "Table 1"
        # A renamed copy exists in target
        copy = db_session.scalars(
            select(ExtractionSchema).where(
                ExtractionSchema.project_id == target.id,
                ExtractionSchema.title == "Table 1 (source)",
            )
        ).one_or_none()
        assert copy is not None

    def test_schema_conflict_defaults_to_drop(self, db_session):
        """No decision provided for a conflict → drop (don't copy the incoming schema)."""
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _schema(db_session, target, "Table 1")
        s_source = _schema(db_session, source, "Table 1")
        db_session.commit()

        # Empty decisions — no explicit decision for the conflicting schema
        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Source schema stays in source (not deleted)
        assert db_session.get(ExtractionSchema, s_source.id).project_id == source.id
        # No extra copy created in target
        count = db_session.scalar(
            select(func.count(ExtractionSchema.id)).where(
                ExtractionSchema.project_id == target.id
            )
        )
        assert count == 1

    # --- Venue tiers ---

    def test_non_conflicting_venue_tier_moved_to_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        venue = _venue(db_session, "CVPR")
        _venue_tier(db_session, source, venue, tier=1)
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        pvt = db_session.scalars(
            select(ProjectVenueTier).where(
                ProjectVenueTier.project_id == target.id,
                ProjectVenueTier.venue_id == venue.id,
            )
        ).one_or_none()
        assert pvt is not None
        assert pvt.tier == 1

    def test_venue_tier_conflict_chosen_tier_applied(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        venue = _venue(db_session, "ICLR")
        _venue_tier(db_session, target, venue, tier=2)
        _venue_tier(db_session, source, venue, tier=3)
        db_session.commit()

        decisions = MergeDecisions(venue_tier_decisions={venue.id: 1})
        execute_merge(target.id, source.id, decisions, db_session)

        db_session.expire_all()
        # Target gets the chosen tier
        target_pvt = db_session.scalars(
            select(ProjectVenueTier).where(
                ProjectVenueTier.project_id == target.id,
                ProjectVenueTier.venue_id == venue.id,
            )
        ).one_or_none()
        assert target_pvt is not None
        assert target_pvt.tier == 1  # chosen tier applied

        # Source row still exists (non-destructive)
        source_pvt = db_session.scalars(
            select(ProjectVenueTier).where(
                ProjectVenueTier.project_id == source.id,
                ProjectVenueTier.venue_id == venue.id,
            )
        ).one_or_none()
        assert source_pvt is not None
        assert source_pvt.tier == 3  # source unchanged

    def test_venue_tier_conflict_no_decision_keeps_target_tier(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        venue = _venue(db_session, "ICLR")
        _venue_tier(db_session, target, venue, tier=2)
        _venue_tier(db_session, source, venue, tier=3)
        db_session.commit()

        # No decision → target tier preserved
        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        pvt = db_session.scalars(
            select(ProjectVenueTier).where(
                ProjectVenueTier.project_id == target.id,
                ProjectVenueTier.venue_id == venue.id,
            )
        ).one_or_none()
        assert pvt is not None
        assert pvt.tier == 2  # target's original tier kept

    # --- Work notes ---

    def test_project_scoped_notes_copied_to_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        work = _work(db_session)
        note = _note(db_session, work, source, "Some observation")
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Original note still belongs to source
        assert db_session.get(WorkNote, note.id).project_id == source.id
        # A copy exists in target with the same content
        copy = db_session.scalars(
            select(WorkNote).where(
                WorkNote.project_id == target.id,
                WorkNote.work_id == work.id,
                WorkNote.content == "Some observation",
            )
        ).one_or_none()
        assert copy is not None

    # --- Chat sessions ---

    def test_chat_sessions_copied_to_target(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        session = _chat_session(db_session, source)
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        # Original session stays in source
        assert db_session.get(ChatSession, session.id).project_id == source.id
        # A copy exists in target
        copy = db_session.scalars(
            select(ChatSession).where(ChatSession.project_id == target.id)
        ).one_or_none()
        assert copy is not None

    def test_chat_session_schema_context_remapped(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        schema = _schema(db_session, source, "My Schema")
        session = ChatSession(project_id=source.id, context_type="extraction_schema", context_id=schema.id)
        db_session.add(session)
        db_session.flush()
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        copy = db_session.scalars(
            select(ChatSession).where(ChatSession.project_id == target.id)
        ).one_or_none()
        assert copy is not None
        assert copy.context_type == "extraction_schema"
        # context_id must point to the new schema copy in target, not the original
        assert copy.context_id != schema.id
        new_schema = db_session.get(ExtractionSchema, copy.context_id)
        assert new_schema is not None
        assert new_schema.project_id == target.id

    def test_chat_session_dropped_schema_reset_to_papers(self, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        # Same-name schema in both → conflict; default decision is drop
        _schema(db_session, target, "Table 1")
        schema = _schema(db_session, source, "Table 1")
        session = ChatSession(project_id=source.id, context_type="extraction_schema", context_id=schema.id)
        db_session.add(session)
        db_session.flush()
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        copy = db_session.scalars(
            select(ChatSession).where(ChatSession.project_id == target.id)
        ).one_or_none()
        assert copy is not None
        assert copy.context_type == "papers"
        assert copy.context_id is None

    # --- Full FK verification ---

    def test_all_categories_copied_to_target(self, db_session):
        """Integration test: every category is copied into target; source stays intact."""
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")

        # Topic list (unique name) + seed work
        work = _work(db_session, "Paper")
        tl = _topic_list(db_session, source, "My List")
        _seed(db_session, tl, work)

        # Ignored work
        ignored_work = _work(db_session, "Boring")
        _ignored(db_session, source, ignored_work)

        # Schema
        _schema(db_session, source, "My Schema")

        # Venue tier
        venue = _venue(db_session, "CVPR")
        _venue_tier(db_session, source, venue, tier=1)

        # Note
        note = _note(db_session, work, source)

        # Chat session
        session = _chat_session(db_session, source)

        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)
        db_session.expire_all()

        # Source project still exists
        assert db_session.get(Project, source.id) is not None

        # Topic list: source unchanged, copy exists in target
        assert db_session.get(TopicList, tl.id).project_id == source.id
        assert db_session.scalars(
            select(TopicList).where(
                TopicList.project_id == target.id, TopicList.name == "My List"
            )
        ).one_or_none() is not None

        # Ignored work copied to target
        piw = db_session.scalars(
            select(ProjectIgnoredWork).where(
                ProjectIgnoredWork.project_id == target.id,
                ProjectIgnoredWork.work_id == ignored_work.id,
            )
        ).one_or_none()
        assert piw is not None

        # Schema copy exists in target
        assert db_session.scalars(
            select(ExtractionSchema).where(
                ExtractionSchema.project_id == target.id,
                ExtractionSchema.title == "My Schema",
            )
        ).one_or_none() is not None

        # Venue tier copied to target
        pvt = db_session.scalars(
            select(ProjectVenueTier).where(
                ProjectVenueTier.project_id == target.id,
                ProjectVenueTier.venue_id == venue.id,
            )
        ).one_or_none()
        assert pvt is not None

        # Note copy exists in target; original stays in source
        assert db_session.get(WorkNote, note.id).project_id == source.id
        assert db_session.scalars(
            select(WorkNote).where(
                WorkNote.project_id == target.id, WorkNote.work_id == work.id
            )
        ).one_or_none() is not None

        # Chat session original stays in source; a copy exists in target
        assert db_session.get(ChatSession, session.id).project_id == source.id
        assert db_session.scalars(
            select(ChatSession).where(ChatSession.project_id == target.id)
        ).one_or_none() is not None


# ---------------------------------------------------------------------------
# Merge-preview API tests
# ---------------------------------------------------------------------------

class TestMergePreviewAPI:

    def test_preview_endpoint_returns_200(self, client, db_session):
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        db_session.commit()

        resp = client.get(f"/api/projects/{target.id}/merge-preview/{source.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "topic_list_merges" in data
        assert "schema_conflicts" in data
        assert "venue_tier_conflicts" in data

    def test_preview_self_returns_400(self, client, db_session):
        project = _project(db_session)
        db_session.commit()

        resp = client.get(
            f"/api/projects/{project.id}/merge-preview/{project.id}"
        )
        assert resp.status_code == 400

    def test_preview_nonexistent_target_returns_404(self, client, db_session):
        source = _project(db_session)
        db_session.commit()

        resp = client.get(f"/api/projects/99999/merge-preview/{source.id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Selective topic-list merge tests (Part A)
# ---------------------------------------------------------------------------

class TestSelectiveTopicListMerge:

    def test_selected_lists_are_copied(self, db_session):
        """Only the topic lists whose IDs are in selected_topic_list_ids get merged."""
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        w1 = _work(db_session, "Paper 1")
        w2 = _work(db_session, "Paper 2")
        w3 = _work(db_session, "Paper 3")
        tl1 = _topic_list(db_session, source, "List A")
        tl2 = _topic_list(db_session, source, "List B")
        tl3 = _topic_list(db_session, source, "List C")
        _seed(db_session, tl1, w1)
        _seed(db_session, tl2, w2)
        _seed(db_session, tl3, w3)
        db_session.commit()

        # Select only List A and List C (skip List B)
        decisions = MergeDecisions(selected_topic_list_ids=[tl1.id, tl3.id])
        execute_merge(target.id, source.id, decisions, db_session)

        db_session.expire_all()
        target_lists = db_session.scalars(
            select(TopicList).where(TopicList.project_id == target.id)
        ).all()
        target_names = {tl.name for tl in target_lists}
        assert "List A" in target_names
        assert "List C" in target_names
        assert "List B" not in target_names

    def test_unselected_list_not_copied(self, db_session):
        """A topic list excluded from selection is not copied to the target."""
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        tl1 = _topic_list(db_session, source, "Keep")
        tl2 = _topic_list(db_session, source, "Skip")
        db_session.commit()

        decisions = MergeDecisions(selected_topic_list_ids=[tl1.id])
        execute_merge(target.id, source.id, decisions, db_session)

        db_session.expire_all()
        skipped = db_session.scalars(
            select(TopicList).where(
                TopicList.project_id == target.id,
                TopicList.name == "Skip",
            )
        ).one_or_none()
        assert skipped is None

    def test_none_selected_topic_list_ids_merges_all(self, db_session):
        """When selected_topic_list_ids is None, all topic lists are merged (default behavior)."""
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        _topic_list(db_session, source, "List A")
        _topic_list(db_session, source, "List B")
        _topic_list(db_session, source, "List C")
        db_session.commit()

        execute_merge(target.id, source.id, MergeDecisions(), db_session)

        db_session.expire_all()
        target_names = {
            tl.name
            for tl in db_session.scalars(
                select(TopicList).where(TopicList.project_id == target.id)
            ).all()
        }
        assert target_names == {"List A", "List B", "List C"}

    def test_selective_merge_via_api(self, client, db_session):
        """API accepts selected_topic_list_ids and merges only selected lists."""
        target = _project(db_session, "Target")
        source = _project(db_session, "Source")
        tl1 = _topic_list(db_session, source, "Wanted")
        _topic_list(db_session, source, "Unwanted")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{target.id}/merge/{source.id}",
            json={"selected_topic_list_ids": [tl1.id]},
        )
        assert resp.status_code == 200

        db_session.expire_all()
        target_names = {
            tl.name
            for tl in db_session.scalars(
                select(TopicList).where(TopicList.project_id == target.id)
            ).all()
        }
        assert "Wanted" in target_names
        assert "Unwanted" not in target_names


# ---------------------------------------------------------------------------
# Intra-project topic-list merge endpoint tests (Part B)
# ---------------------------------------------------------------------------

class TestMergeTopicListsEndpoint:

    def test_works_copied_to_target_list(self, client, db_session):
        """Works from the source list are added to the target list."""
        project = _project(db_session)
        w1 = _work(db_session, "Paper in source only")
        tl_target = _topic_list(db_session, project, "Target List")
        tl_source = _topic_list(db_session, project, "Source List")
        _seed(db_session, tl_source, w1)
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project.id}/topic-lists/{tl_target.id}/merge/{tl_source.id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["merged_count"] == 1
        assert data["skipped_duplicate_count"] == 0

        db_session.expire_all()
        tlw = db_session.scalars(
            select(TopicListWork).where(
                TopicListWork.topic_list_id == tl_target.id,
                TopicListWork.work_id == w1.id,
            )
        ).one_or_none()
        assert tlw is not None

    def test_source_list_is_unchanged(self, client, db_session):
        """Source topic list and its memberships are not modified."""
        project = _project(db_session)
        w1 = _work(db_session, "Paper")
        tl_target = _topic_list(db_session, project, "Target")
        tl_source = _topic_list(db_session, project, "Source")
        _seed(db_session, tl_source, w1)
        db_session.commit()

        client.post(
            f"/api/projects/{project.id}/topic-lists/{tl_target.id}/merge/{tl_source.id}"
        )

        db_session.expire_all()
        assert db_session.get(TopicList, tl_source.id) is not None
        source_works = db_session.scalars(
            select(TopicListWork).where(TopicListWork.topic_list_id == tl_source.id)
        ).all()
        assert len(source_works) == 1

    def test_duplicate_works_skipped(self, client, db_session):
        """Works already in the target list count as skipped, not added twice."""
        project = _project(db_session)
        w_shared = _work(db_session, "Shared")
        w_unique = _work(db_session, "Unique")
        tl_target = _topic_list(db_session, project, "Target")
        tl_source = _topic_list(db_session, project, "Source")
        _seed(db_session, tl_target, w_shared)
        _seed(db_session, tl_source, w_shared)
        _seed(db_session, tl_source, w_unique)
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project.id}/topic-lists/{tl_target.id}/merge/{tl_source.id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["merged_count"] == 1
        assert data["skipped_duplicate_count"] == 1

        db_session.expire_all()
        count = len(
            db_session.scalars(
                select(TopicListWork).where(
                    TopicListWork.topic_list_id == tl_target.id,
                    TopicListWork.work_id == w_shared.id,
                )
            ).all()
        )
        assert count == 1  # no duplicate

    def test_merge_self_returns_400(self, client, db_session):
        project = _project(db_session)
        tl = _topic_list(db_session, project, "List")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project.id}/topic-lists/{tl.id}/merge/{tl.id}"
        )
        assert resp.status_code == 400

    def test_source_wrong_project_returns_404(self, client, db_session):
        """Source topic list must belong to the same project."""
        project_a = _project(db_session, "A")
        project_b = _project(db_session, "B")
        tl_target = _topic_list(db_session, project_a, "Target")
        tl_other = _topic_list(db_session, project_b, "Other")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project_a.id}/topic-lists/{tl_target.id}/merge/{tl_other.id}"
        )
        assert resp.status_code == 404

    def test_nonexistent_target_returns_404(self, client, db_session):
        project = _project(db_session)
        tl_source = _topic_list(db_session, project, "Source")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project.id}/topic-lists/99999/merge/{tl_source.id}"
        )
        assert resp.status_code == 404

    def test_empty_source_list_returns_zero_counts(self, client, db_session):
        project = _project(db_session)
        tl_target = _topic_list(db_session, project, "Target")
        tl_source = _topic_list(db_session, project, "Empty Source")
        db_session.commit()

        resp = client.post(
            f"/api/projects/{project.id}/topic-lists/{tl_target.id}/merge/{tl_source.id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["merged_count"] == 0
        assert data["skipped_duplicate_count"] == 0
