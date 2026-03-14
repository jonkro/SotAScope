"""Pydantic schemas for project merge preview and execution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class TopicListMergeInfo(BaseModel):
    source_topic_list_id: int
    source_topic_list_name: str
    action: Literal["merge", "move"]
    # set when action == "merge" (existing same-name list in target)
    target_topic_list_id: int | None = None


class SchemaConflictInfo(BaseModel):
    source_schema_id: int
    source_schema_name: str
    target_schema_id: int
    target_schema_name: str


class VenueTierConflictInfo(BaseModel):
    venue_id: int
    venue_name: str
    source_tier: int
    target_tier: int


class MergePreview(BaseModel):
    topic_list_merges: list[TopicListMergeInfo]
    schema_conflicts: list[SchemaConflictInfo]
    venue_tier_conflicts: list[VenueTierConflictInfo]
    # Work IDs ignored in one project but seed in the other (auto-resolved, seed wins)
    ignored_work_overrides: list[int]
    source_chat_session_count: int
    source_note_count: int


# ---------------------------------------------------------------------------
# Decisions (body of POST merge)
# ---------------------------------------------------------------------------


class SchemaDecision(BaseModel):
    action: Literal["rename", "drop"]
    new_name: str | None = None  # required when action == "rename"


class MergeDecisions(BaseModel):
    # Keyed by source_schema_id; absent keys for conflicting schemas default to "drop"
    schema_decisions: dict[int, SchemaDecision] = {}
    # Keyed by venue_id; absent keys keep target's existing tier
    venue_tier_decisions: dict[int, int] = {}
