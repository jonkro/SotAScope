import { useState } from 'react';
import {
  useProjectVenueTiers,
  useResetProjectVenueTier,
  useSetProjectVenueTier,
} from '../hooks/useVenueTiers';

const TIER_OPTIONS = [
  { value: 1, label: 'Top' },
  { value: 2, label: 'Regular' },
  { value: 3, label: 'Ignore' },
] as const;

interface Props {
  projectId: number;
}

export default function ProjectVenueTiersTab({ projectId }: Props) {
  const [filter, setFilter] = useState('');

  const { data: venues, isLoading } = useProjectVenueTiers(projectId);
  const setTier = useSetProjectVenueTier(projectId);
  const resetTier = useResetProjectVenueTier(projectId);

  if (isLoading) {
    return <p className="p-6 text-sm text-gray-400">Loading venue tiers…</p>;
  }

  if (!venues || venues.length === 0) {
    return (
      <div className="p-6">
        <p className="text-sm text-gray-500">
          No venues found in this project yet. Add papers to topic lists to populate the venue list.
        </p>
      </div>
    );
  }

  const q = filter.trim().toLowerCase();
  const filtered = q
    ? venues.filter((v) => v.all_names.some((n) => n.toLowerCase().includes(q)))
    : venues;

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl space-y-4">
        {/* Filter input */}
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter venues…"
          className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
        />

        <p className="text-xs text-gray-400">
          {filtered.length} venue{filtered.length !== 1 ? 's' : ''}
          {q && ` matching "${filter}"`}
        </p>

        {/* Venue rows */}
        <div className="space-y-1">
          {filtered.map((venue) => {
            const isLocal = venue.local_tier !== null;
            return (
              <div
                key={venue.venue_id}
                className="flex items-center gap-3 py-2 px-3 rounded hover:bg-gray-50 border border-transparent hover:border-gray-200"
              >
                {/* Venue name */}
                <span className="flex-1 text-sm text-gray-800 min-w-0 truncate" title={venue.venue_name}>
                  {venue.venue_name}
                </span>

                {/* Global / local badge */}
                <span
                  className={`text-xs px-1.5 py-0.5 rounded font-medium shrink-0 ${
                    isLocal
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  {isLocal ? 'local' : 'global'}
                </span>

                {/* Tier dropdown */}
                <select
                  value={venue.effective_tier}
                  onChange={(e) => {
                    const tier = Number(e.target.value);
                    setTier.mutate({ venueId: venue.venue_id, tier });
                  }}
                  className="text-xs border border-gray-300 rounded px-1.5 py-0.5 bg-white shrink-0 focus:outline-none focus:ring-1 focus:ring-blue-400"
                >
                  {TIER_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>

                {/* Reset button — only shown when local override exists */}
                {isLocal ? (
                  <button
                    onClick={() => resetTier.mutate({ venueId: venue.venue_id })}
                    title={`Reset to global (${TIER_OPTIONS.find((o) => o.value === venue.global_tier)?.label ?? venue.global_tier})`}
                    className="text-xs text-gray-400 hover:text-red-500 shrink-0 leading-none"
                  >
                    ✕
                  </button>
                ) : (
                  /* Placeholder to keep layout stable */
                  <span className="w-4 shrink-0" />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
