import { useState, useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import Pagination from '../components/Pagination';
import EmptyState from '../components/EmptyState';
import VenueFieldEditor from '../components/VenueTierEditor';
import { useVenues, useVenue, useUpdateVenue, useAddVenueAlias, useDeleteVenueAlias, useReorderVenueAliases } from '../hooks/useVenues';
import { useCreateField } from '../hooks/useFields';

const PAGE_SIZE = 30;

const TIER_OPTIONS = [
  { value: '1', label: 'Top' },
  { value: '2', label: 'Regular' },
  { value: '3', label: 'Ignore' },
] as const;

function VenueRow({ venueId, onCollapse, onUpdateTier }: { venueId: number; onCollapse: () => void; onUpdateTier: (tier: number) => void }) {
  const { data: venue } = useVenue(venueId);
  const updateVenueM = useUpdateVenue();
  const addAlias = useAddVenueAlias();
  const deleteAlias = useDeleteVenueAlias();
  const reorderAliases = useReorderVenueAliases();
  const [newAlias, setNewAlias] = useState('');
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');

  const handleMoveAlias = (index: number, direction: -1 | 1) => {
    if (!venue) return;
    const ids = venue.aliases.map((a) => a.id);
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= ids.length) return;
    [ids[index], ids[targetIndex]] = [ids[targetIndex], ids[index]];
    reorderAliases.mutate({ venueId: venue.id, aliasIds: ids });
  };

  const handleStartEdit = () => {
    if (!venue) return;
    setNameDraft(venue.name);
    setEditingName(true);
  };

  const handleSaveName = () => {
    if (!venue || !nameDraft.trim() || nameDraft.trim() === venue.name) {
      setEditingName(false);
      return;
    }
    updateVenueM.mutate(
      { venueId: venue.id, data: { name: nameDraft.trim() } },
      { onSuccess: () => setEditingName(false) },
    );
  };

  if (!venue) return <tr><td colSpan={5} className="px-4 py-2 text-xs text-gray-400">Loading...</td></tr>;

  return (
    <>
      <tr className="bg-blue-50">
        <td className="px-4 py-2" colSpan={5}>
          <div className="flex items-start justify-between">
            <div>
              {editingName ? (
                <div className="flex items-center gap-2">
                  <input
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSaveName();
                      if (e.key === 'Escape') setEditingName(false);
                    }}
                    autoFocus
                    className="font-medium text-sm text-gray-900 border border-blue-400 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-blue-500 w-96"
                  />
                  <button onClick={handleSaveName} className="text-xs text-blue-600 hover:text-blue-800">Save</button>
                  <button onClick={() => setEditingName(false)} className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
                </div>
              ) : (
                <div className="font-medium text-sm text-gray-900 group flex items-center gap-1">
                  {venue.name}
                  <button
                    onClick={handleStartEdit}
                    className="text-gray-400 hover:text-gray-700 opacity-0 group-hover:opacity-100 text-xs"
                    title="Rename venue"
                  >
                    &#9998;
                  </button>
                </div>
              )}
              <div className="text-xs text-gray-500 mt-1 space-x-3">
                {venue.venue_type && <span>Type: {venue.venue_type}</span>}
                {venue.publisher && <span>Publisher: {venue.publisher}</span>}
                {venue.issn && <span>ISSN: {venue.issn}</span>}
                {venue.dblp_id && <span>DBLP: {venue.dblp_id}</span>}
                <span>
                  Papers in library:{' '}
                  {venue.work_count > 0 ? (
                    <Link to={`/library?venue_id=${venue.id}`} className="text-blue-600 hover:underline">
                      {venue.work_count}
                    </Link>
                  ) : (
                    '0'
                  )}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={String(venue.tier)}
                onChange={(e) => onUpdateTier(Number(e.target.value))}
                className={`px-2 py-1 text-sm border rounded cursor-pointer focus:outline-none focus:ring-1 focus:ring-blue-500 ${
                  venue.tier === 1
                    ? 'border-green-400 bg-green-50 text-green-700'
                    : venue.tier === 3
                      ? 'border-red-300 bg-red-50 text-red-600'
                      : 'border-gray-300 bg-white text-gray-700'
                }`}
              >
                {TIER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <button onClick={onCollapse} className="text-xs text-gray-500 hover:text-gray-700">Collapse</button>
            </div>
          </div>

          {/* Aliases */}
          <div className="mt-3">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">
              Aliases <span className="font-normal normal-case">(first = preferred)</span>
            </h4>
            {venue.aliases.length > 0 ? (
              <div className="flex flex-wrap gap-1 mb-2">
                {venue.aliases.map((a, i) => (
                  <span key={a.id} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-gray-100 rounded">
                    {i === 0 && <span className="text-amber-500" title="Preferred alias">&#9733;</span>}
                    {a.alias}
                    <button
                      onClick={() => handleMoveAlias(i, -1)}
                      disabled={i === 0}
                      className="text-gray-400 hover:text-gray-700 disabled:opacity-25 disabled:cursor-default"
                      title="Move up"
                    >
                      &#9650;
                    </button>
                    <button
                      onClick={() => handleMoveAlias(i, 1)}
                      disabled={i === venue.aliases.length - 1}
                      className="text-gray-400 hover:text-gray-700 disabled:opacity-25 disabled:cursor-default"
                      title="Move down"
                    >
                      &#9660;
                    </button>
                    <button
                      onClick={() => deleteAlias.mutate({ venueId: venue.id, aliasId: a.id })}
                      className="text-gray-400 hover:text-red-500"
                    >
                      &times;
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400 mb-2">No aliases</p>
            )}
            <div className="flex gap-2">
              <input
                value={newAlias}
                onChange={(e) => setNewAlias(e.target.value)}
                placeholder="Add alias..."
                className="px-2 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newAlias.trim()) {
                    addAlias.mutate({ venueId: venue.id, alias: newAlias.trim() });
                    setNewAlias('');
                  }
                }}
              />
              <button
                onClick={() => {
                  if (newAlias.trim()) {
                    addAlias.mutate({ venueId: venue.id, alias: newAlias.trim() });
                    setNewAlias('');
                  }
                }}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
              >
                Add
              </button>
            </div>
          </div>

          {/* Fields */}
          <div className="mt-3">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">Fields</h4>
            <VenueFieldEditor venueId={venue.id} venueFields={venue.fields} />
          </div>
        </td>
      </tr>
    </>
  );
}

export default function VenuesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [newFieldName, setNewFieldName] = useState('');

  const createField = useCreateField();
  const updateVenue = useUpdateVenue();

  // venue_id param: filter to single venue (expanded)
  const venueIdParam = searchParams.get('venue_id');
  const venueIdFilter = venueIdParam ? Number(venueIdParam) : null;

  // Fetch the filtered venue's detail when filtering by venue_id
  const { data: filteredVenue } = useVenue(venueIdFilter);

  const clearVenueFilter = useCallback(() => {
    setSearchParams({}, { replace: true });
    setExpandedId(null);
  }, [setSearchParams]);

  const handleSearch = useCallback((v: string) => {
    setSearch(v);
    setOffset(0);
  }, []);

  const { data: venues, isLoading } = useVenues({
    offset,
    limit: PAGE_SIZE,
    q: search || undefined,
  });

  // When filtering by venue_id, show only that venue (always expanded)
  const displayVenues = venueIdFilter && filteredVenue
    ? [filteredVenue]
    : venues;
  const effectiveExpandedId = venueIdFilter ?? expandedId;

  return (
    <div className="flex flex-col h-screen">
      <PageHeader title="Venues">
        <div className="flex items-center gap-1.5">
          <input
            value={newFieldName}
            onChange={(e) => setNewFieldName(e.target.value)}
            placeholder="New field..."
            className="border border-gray-300 rounded px-2 py-1 text-sm w-32"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && newFieldName.trim()) {
                createField.mutate(newFieldName.trim(), {
                  onSuccess: () => setNewFieldName(''),
                });
              }
            }}
          />
          <button
            onClick={() => {
              if (newFieldName.trim()) {
                createField.mutate(newFieldName.trim(), {
                  onSuccess: () => setNewFieldName(''),
                });
              }
            }}
            className="px-2 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Add Field
          </button>
        </div>
        {!venueIdFilter && (
          <SearchInput value={search} onChange={handleSearch} placeholder="Search venues..." />
        )}
      </PageHeader>

      {venueIdFilter && filteredVenue && (
        <div className="px-4 py-2 bg-blue-50 border-b border-blue-100 flex items-center gap-2">
          <span className="text-xs text-blue-700">
            Filtered by venue: <strong>{filteredVenue.name}</strong>
          </span>
          <button
            onClick={clearVenueFilter}
            className="text-xs text-blue-600 hover:text-blue-800 underline"
          >
            Clear filter
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {isLoading && !venueIdFilter ? (
          <p className="p-6 text-sm text-gray-400">Loading...</p>
        ) : !displayVenues?.length ? (
          <EmptyState message="No venues yet. Venues are created automatically when importing works." />
        ) : (
          <table className="w-full">
            <thead className="sticky top-0 bg-white border-b border-gray-200">
              <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Type</th>
                <th className="px-4 py-2">Field</th>
                <th className="px-4 py-2">Papers</th>
                <th className="px-4 py-2">Tier</th>
              </tr>
            </thead>
            <tbody>
              {displayVenues.map((v) =>
                effectiveExpandedId === v.id ? (
                  <VenueRow
                    key={v.id}
                    venueId={v.id}
                    onCollapse={venueIdFilter ? clearVenueFilter : () => setExpandedId(null)}
                    onUpdateTier={(tier) => updateVenue.mutate({ venueId: v.id, data: { tier } })}
                  />
                ) : (
                  <tr
                    key={v.id}
                    onClick={() => setExpandedId(v.id)}
                    className="border-b border-gray-50 hover:bg-gray-50 cursor-pointer"
                  >
                    <td className="px-4 py-2 text-sm text-gray-900">{v.name}</td>
                    <td className="px-4 py-2 text-sm text-gray-500">{v.venue_type ?? '-'}</td>
                    <td className="px-4 py-2 text-sm text-gray-500">{v.field_display ?? '-'}</td>
                    <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
                      {v.work_count > 0 ? (
                        <Link
                          to={`/library?venue_id=${v.id}`}
                          className="text-sm text-blue-600 hover:underline"
                        >
                          {v.work_count}
                        </Link>
                      ) : (
                        <span className="text-sm text-gray-400">0</span>
                      )}
                    </td>
                    <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
                      <select
                        value={String(v.tier)}
                        onChange={(e) => {
                          updateVenue.mutate({ venueId: v.id, data: { tier: Number(e.target.value) } });
                        }}
                        className={`px-2 py-1 text-sm border rounded cursor-pointer focus:outline-none focus:ring-1 focus:ring-blue-500 ${
                          v.tier === 1
                            ? 'border-green-400 bg-green-50 text-green-700'
                            : v.tier === 3
                              ? 'border-red-300 bg-red-50 text-red-600'
                              : 'border-gray-300 bg-white text-gray-700'
                        }`}
                      >
                        {TIER_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        )}

        {!venueIdFilter && venues && (
          <div className="px-4">
            <Pagination offset={offset} limit={PAGE_SIZE} count={venues.length} onChange={setOffset} />
          </div>
        )}
      </div>
    </div>
  );
}
