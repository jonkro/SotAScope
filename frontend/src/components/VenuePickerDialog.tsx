import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchVenues } from '../api';

export default function VenuePickerDialog({
  currentVenueId,
  onSelect,
  onClose,
}: {
  currentVenueId: number | null;
  onSelect: (venueId: number | null) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState('');
  const [debounced, setDebounced] = useState('');

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 200);
    return () => clearTimeout(t);
  }, [search]);

  const { data: venues, isLoading } = useQuery({
    queryKey: ['venues', 'picker', debounced],
    queryFn: () => fetchVenues({ q: debounced || undefined, limit: 20, sort_by: 'work_count', sort_dir: 'desc' }),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-lg w-80 mx-4 p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-gray-900 mb-3">Set venue</h3>
        <input
          autoFocus
          type="text"
          placeholder="Search venues…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm mb-2 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <div className="max-h-60 overflow-y-auto space-y-0.5">
          {currentVenueId && (
            <button
              className="w-full text-left px-2 py-1.5 text-xs text-red-600 hover:bg-red-50 rounded"
              onClick={() => onSelect(null)}
            >
              Clear venue
            </button>
          )}
          {isLoading && (
            <p className="text-xs text-gray-400 px-2 py-2">Loading…</p>
          )}
          {venues?.map((v) => (
            <button
              key={v.id}
              className={`w-full text-left px-2 py-1.5 text-xs rounded hover:bg-blue-50 ${
                v.id === currentVenueId ? 'bg-blue-50 font-medium' : ''
              }`}
              onClick={() => onSelect(v.id)}
            >
              {v.name}
              {v.work_count > 0 && (
                <span className="ml-1 text-gray-400">({v.work_count})</span>
              )}
            </button>
          ))}
          {!isLoading && venues?.length === 0 && (
            <p className="text-xs text-gray-400 px-2 py-2">No venues found</p>
          )}
        </div>
        <div className="mt-3 flex justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
