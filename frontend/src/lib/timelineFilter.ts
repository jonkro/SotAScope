import type { TimelineNeighborWork } from '../types';

export interface FilterParams {
  threshold: number;
  decayStartYears: number;
  showBackward: boolean;
  showForward: boolean;
  currentYear: number;
}

export function computeImportanceScore(
  citationCount: number,
  publicationYear: number,
  params: FilterParams,
): number {
  const age = Math.max(params.currentYear - publicationYear, 1);
  const baseScore = citationCount / age;
  const decay = age > params.decayStartYears ? params.decayStartYears / age : 1.0;
  return baseScore * decay;
}

export function filterNeighbors(
  neighbors: TimelineNeighborWork[],
  tier1VenueIds: Set<number>,
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

    // Tier-1 venues always included
    if (n.venue_id != null && tier1VenueIds.has(n.venue_id)) return true;

    // Score-based filtering
    const score = computeImportanceScore(
      n.citation_count ?? 0,
      n.publication_year,
      params,
    );
    return score >= params.threshold;
  });
}
