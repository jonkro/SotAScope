import { describe, expect, it } from 'vitest';
import { applyVisibilityThreshold } from './timelineFilter';
import type { TimelineNeighborWork } from '../types';

// ---------------------------------------------------------------------------
// applyVisibilityThreshold
// ---------------------------------------------------------------------------

function makeNeighbor(id: number, relevanceScore: number): TimelineNeighborWork {
  return {
    id,
    title: `Paper ${id}`,
    publication_year: 2010,
    citation_count: 0,
    citations_by_year: null,
    venue_id: null,
    venue_name: null,
    venue_tier: null,
    direction: 'backward',
    connected_seed_ids: [],
    has_citation_data: true,
    relevance_score: relevanceScore,
  } as unknown as TimelineNeighborWork;
}

describe('applyVisibilityThreshold', () => {
  it('returns all neighbors unchanged when count <= maxVisible', () => {
    const neighbors = Array.from({ length: 20 }, (_, i) => makeNeighbor(i, i));
    const result = applyVisibilityThreshold(neighbors, 30);
    expect(result.filtered).toHaveLength(20);
    expect(result.hiddenCount).toBe(0);
    expect(result.totalBeforeFilter).toBe(20);
  });

  it('keeps only top maxVisible by relevance_score when count > maxVisible', () => {
    const neighbors = Array.from({ length: 100 }, (_, i) => makeNeighbor(i, i));
    const result = applyVisibilityThreshold(neighbors, 30);
    expect(result.filtered).toHaveLength(30);
    expect(result.hiddenCount).toBe(70);
    expect(result.totalBeforeFilter).toBe(100);
  });

  it('top results have higher relevance_score than omitted results', () => {
    const neighbors = Array.from({ length: 100 }, (_, i) => makeNeighbor(i, i));
    const result = applyVisibilityThreshold(neighbors, 30);

    // id=99 has score=99, should be included; id=0 has score=0, should be excluded
    const includedIds = new Set(result.filtered.map((n) => n.id));
    expect(includedIds.has(99)).toBe(true);
    expect(includedIds.has(0)).toBe(false);
  });

  it('does not mutate the original array', () => {
    const neighbors = Array.from({ length: 10 }, (_, i) => makeNeighbor(i, 10 - i));
    const originalOrder = neighbors.map((n) => n.id);
    applyVisibilityThreshold(neighbors, 5);
    expect(neighbors.map((n) => n.id)).toEqual(originalOrder);
  });

  it('returns empty array when neighbors is empty', () => {
    const result = applyVisibilityThreshold([], 3000);
    expect(result.filtered).toHaveLength(0);
    expect(result.hiddenCount).toBe(0);
  });

  it('handles maxVisible=0 by returning empty filtered list', () => {
    const neighbors = Array.from({ length: 10 }, (_, i) => makeNeighbor(i, i));
    const result = applyVisibilityThreshold(neighbors, 0);
    expect(result.filtered).toHaveLength(0);
    expect(result.hiddenCount).toBe(10);
  });
});
