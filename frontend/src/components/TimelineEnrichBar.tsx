import { useState } from 'react';
import type { TimelineSeedWork } from '../types';
import { fetchBackwardCitationsEnrich, fetchForwardCitationsEnrich } from '../api';
import { useQueryClient } from '@tanstack/react-query';

interface TimelineEnrichBarProps {
  seeds: TimelineSeedWork[];
  projectId: number;
}

export default function TimelineEnrichBar({ seeds, projectId }: TimelineEnrichBarProps) {
  const qc = useQueryClient();
  const [fetching, setFetching] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);

  const seedsWithBwd = seeds.filter((s) => s.has_backward_citations).length;
  const seedsWithFwd = seeds.filter((s) => s.has_forward_citations).length;
  const total = seeds.length;

  const handleFetchAll = async () => {
    setFetching(true);
    const toFetch = seeds.filter((s) => !s.has_backward_citations || !s.has_forward_citations);
    setProgress({ done: 0, total: toFetch.length });

    for (let i = 0; i < toFetch.length; i++) {
      const seed = toFetch[i];
      try {
        if (!seed.has_backward_citations) {
          await fetchBackwardCitationsEnrich(seed.id);
        }
        if (!seed.has_forward_citations) {
          await fetchForwardCitationsEnrich(seed.id);
        }
      } catch {
        // Continue on failure — partial enrichment is fine
      }
      setProgress({ done: i + 1, total: toFetch.length });
    }

    setFetching(false);
    setProgress(null);
    // Refresh timeline data
    qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
  };

  if (total === 0) return null;

  const allEnriched = seedsWithBwd === total && seedsWithFwd === total;

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-blue-50 border-b border-blue-100 text-xs">
      <span className="text-blue-700">
        References: {seedsWithBwd}/{total} seeds fetched
        {' | '}
        Citing: {seedsWithFwd}/{total} seeds fetched
      </span>

      {!allEnriched && (
        <button
          onClick={handleFetchAll}
          disabled={fetching}
          className="px-2 py-0.5 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {fetching
            ? `Fetching ${progress?.done}/${progress?.total}...`
            : 'Fetch all citations'}
        </button>
      )}

      {allEnriched && (
        <span className="text-green-600 font-medium">All citations fetched</span>
      )}
    </div>
  );
}
