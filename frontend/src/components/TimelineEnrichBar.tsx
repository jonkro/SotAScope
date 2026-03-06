import { useState } from 'react';
import type { TimelineSeedWork } from '../types';
import { fetchBackwardCitationsEnrich, fetchForwardCitationsEnrich } from '../api';
import { useQueryClient } from '@tanstack/react-query';

interface FetchError {
  workId: number;
  title: string;
  direction: 'references' | 'citing';
  reason: string;
}

function parseErrorReason(e: unknown): string {
  if (!(e instanceof Error)) return 'Unknown error';
  // ApiError.message is raw response body, e.g. '{"detail":"Work 123 has no OpenAlex ID..."}'
  try {
    const parsed = JSON.parse(e.message);
    if (parsed.detail) return parsed.detail;
  } catch {
    // not JSON
  }
  return e.message;
}

interface TimelineEnrichBarProps {
  seeds: TimelineSeedWork[];
  projectId: number;
}

export default function TimelineEnrichBar({ seeds, projectId }: TimelineEnrichBarProps) {
  const qc = useQueryClient();
  const [fetching, setFetching] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [errors, setErrors] = useState<FetchError[]>([]);

  const seedsWithBwd = seeds.filter((s) => s.has_backward_citations).length;
  const seedsWithFwd = seeds.filter((s) => s.has_forward_citations).length;
  const bwdNoOaData = seeds.filter((s) => s.backward_citations_no_oa_data).length;
  // Seeds with actual OA reference data (fetched and non-empty)
  const bwdFetched = seedsWithBwd - bwdNoOaData;
  const total = seeds.length;

  const handleFetchAll = async () => {
    setFetching(true);
    setErrors([]);
    const toFetch = seeds.filter((s) => !s.has_backward_citations || !s.has_forward_citations);
    setProgress({ done: 0, total: toFetch.length });

    const newErrors: FetchError[] = [];

    for (let i = 0; i < toFetch.length; i++) {
      const seed = toFetch[i];
      if (!seed.has_backward_citations) {
        try {
          await fetchBackwardCitationsEnrich(seed.id);
        } catch (e) {
          newErrors.push({
            workId: seed.id,
            title: seed.title,
            direction: 'references',
            reason: parseErrorReason(e),
          });
        }
      }
      if (!seed.has_forward_citations) {
        try {
          await fetchForwardCitationsEnrich(seed.id);
        } catch (e) {
          newErrors.push({
            workId: seed.id,
            title: seed.title,
            direction: 'citing',
            reason: parseErrorReason(e),
          });
        }
      }
      setProgress({ done: i + 1, total: toFetch.length });
    }

    setFetching(false);
    setProgress(null);
    setErrors(newErrors);
    // Refresh timeline data
    qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
  };

  if (total === 0) return null;

  const allEnriched = seedsWithBwd === total && seedsWithFwd === total;

  return (
    <div className="px-4 py-2 bg-blue-50 border-b border-blue-100 text-xs">
      <div className="flex items-center gap-3">
        <span className="text-blue-700">
          References: {bwdFetched}/{total} works fetched
          {bwdNoOaData > 0 && (
            <span className="text-amber-600 ml-1">
              ({bwdNoOaData} {bwdNoOaData === 1 ? 'has' : 'have'} no OA data)
            </span>
          )}
          {' | '}
          Citing: {seedsWithFwd}/{total} works fetched
        </span>

        {!allEnriched && (
          <button
            onClick={handleFetchAll}
            disabled={fetching}
            className="px-2 py-0.5 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
            title="For each work missing references or citing papers, fetch them from OpenAlex"
          >
            {fetching
              ? `Fetching ${progress?.done}/${progress?.total}...`
              : 'Fetch all citations'}
          </button>
        )}

        {allEnriched && errors.length === 0 && (
          <span className="text-green-600 font-medium">All citations fetched</span>
        )}
      </div>

      {errors.length > 0 && (
        <div className="mt-2 bg-red-50 border border-red-200 rounded p-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-red-700 font-medium">
              {errors.length} fetch {errors.length === 1 ? 'error' : 'errors'}
            </span>
            <button
              onClick={() => setErrors([])}
              className="text-red-400 hover:text-red-600 px-1"
              title="Dismiss errors"
            >
              &times;
            </button>
          </div>
          <ul className="space-y-0.5">
            {errors.map((err, i) => (
              <li key={i} className="text-red-600">
                <span className="font-medium">{err.title}</span>
                {' '}({err.direction}): {err.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
