import type { TimelineNeighborWork, CitationsByYearEntry } from '../types';

export interface FilterParams {
  showBackward: boolean;
  showForward: boolean;
}

export interface VisibilityThresholdResult {
  filtered: TimelineNeighborWork[];
  totalBeforeFilter: number;
  hiddenCount: number;
}

/**
 * Keep only the top `maxVisible` neighbors by relevance score.
 * If the list is already within the limit, returns it unchanged (hiddenCount=0).
 */
export function applyVisibilityThreshold(
  neighbors: TimelineNeighborWork[],
  maxVisible: number,
): VisibilityThresholdResult {
  const total = neighbors.length;
  if (total <= maxVisible) {
    return { filtered: neighbors, totalBeforeFilter: total, hiddenCount: 0 };
  }
  const scored = [...neighbors].sort((a, b) => b.relevance_score - a.relevance_score);
  return {
    filtered: scored.slice(0, maxVisible),
    totalBeforeFilter: total,
    hiddenCount: total - maxVisible,
  };
}

/**
 * Compute the citation count for a work given a "citations since" window.
 *
 * - If citationsSinceYears is null: return the all-time citation_count.
 * - If citationsSinceYears is a number N: sum cited_by_count from
 *   citations_by_year entries where year >= currentYear - N.
 * - If citations_by_year is null, fall back to citation_count.
 */
export function computeCitationCount(
  citationCount: number | null,
  citationsByYear: CitationsByYearEntry[] | null,
  citationsSinceYears: number | null,
): number {
  if (citationsSinceYears == null || citationsByYear == null) {
    return citationCount ?? 0;
  }
  const cutoff = new Date().getFullYear() - citationsSinceYears;
  let sum = 0;
  for (const entry of citationsByYear) {
    if (entry.year >= cutoff) {
      sum += entry.cited_by_count;
    }
  }
  return sum;
}

export function filterNeighbors(
  neighbors: TimelineNeighborWork[],
  ignoredVenueIds: Set<number>,
  params: FilterParams,
): TimelineNeighborWork[] {
  return neighbors.filter((n) => {
    // Direction filter
    if (n.direction === 'backward' && !params.showBackward) return false;
    if (n.direction === 'forward' && !params.showForward) return false;

    // Must have a year to display on timeline
    if (n.publication_year == null) return false;

    // Ignored venues are always excluded
    if (n.venue_id != null && ignoredVenueIds.has(n.venue_id)) return false;

    return true;
  });
}
