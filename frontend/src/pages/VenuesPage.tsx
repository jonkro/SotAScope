import { useState, useCallback } from 'react';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import Pagination from '../components/Pagination';
import EmptyState from '../components/EmptyState';
import VenueFieldEditor from '../components/VenueTierEditor';
import { useVenues, useVenue, useUpdateVenue, useAddVenueAlias, useDeleteVenueAlias } from '../hooks/useVenues';
import { useCreateField } from '../hooks/useFields';

const PAGE_SIZE = 30;

const TIER_OPTIONS = [
  { value: '1', label: 'Top' },
  { value: '2', label: 'Regular' },
  { value: '3', label: 'Ignore' },
] as const;

function VenueRow({ venueId, onCollapse }: { venueId: number; onCollapse: () => void }) {
  const { data: venue } = useVenue(venueId);
  const addAlias = useAddVenueAlias();
  const deleteAlias = useDeleteVenueAlias();
  const [newAlias, setNewAlias] = useState('');

  if (!venue) return <tr><td colSpan={4} className="px-4 py-2 text-xs text-gray-400">Loading...</td></tr>;

  return (
    <>
      <tr className="bg-blue-50">
        <td className="px-4 py-2" colSpan={4}>
          <div className="flex items-start justify-between">
            <div>
              <div className="font-medium text-sm text-gray-900">{venue.name}</div>
              <div className="text-xs text-gray-500 mt-1 space-x-3">
                {venue.venue_type && <span>Type: {venue.venue_type}</span>}
                {venue.publisher && <span>Publisher: {venue.publisher}</span>}
                {venue.issn && <span>ISSN: {venue.issn}</span>}
                {venue.dblp_id && <span>DBLP: {venue.dblp_id}</span>}
              </div>
            </div>
            <button onClick={onCollapse} className="text-xs text-gray-500 hover:text-gray-700">Collapse</button>
          </div>

          {/* Aliases */}
          <div className="mt-3">
            <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">Aliases</h4>
            {venue.aliases.length > 0 ? (
              <div className="flex flex-wrap gap-1 mb-2">
                {venue.aliases.map((a) => (
                  <span key={a.id} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-gray-100 rounded">
                    {a.alias}
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
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [newFieldName, setNewFieldName] = useState('');

  const createField = useCreateField();
  const updateVenue = useUpdateVenue();

  const handleSearch = useCallback((v: string) => {
    setSearch(v);
    setOffset(0);
  }, []);

  const { data: venues, isLoading } = useVenues({
    offset,
    limit: PAGE_SIZE,
    q: search || undefined,
  });

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
        <SearchInput value={search} onChange={handleSearch} placeholder="Search venues..." />
      </PageHeader>

      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="p-6 text-sm text-gray-400">Loading...</p>
        ) : !venues?.length ? (
          <EmptyState message="No venues yet. Venues are created automatically when importing works." />
        ) : (
          <table className="w-full">
            <thead className="sticky top-0 bg-white border-b border-gray-200">
              <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Type</th>
                <th className="px-4 py-2">DBLP ID</th>
                <th className="px-4 py-2">Tier</th>
              </tr>
            </thead>
            <tbody>
              {venues.map((v) =>
                expandedId === v.id ? (
                  <VenueRow key={v.id} venueId={v.id} onCollapse={() => setExpandedId(null)} />
                ) : (
                  <tr
                    key={v.id}
                    onClick={() => setExpandedId(v.id)}
                    className="border-b border-gray-50 hover:bg-gray-50 cursor-pointer"
                  >
                    <td className="px-4 py-2 text-sm text-gray-900">{v.name}</td>
                    <td className="px-4 py-2 text-sm text-gray-500">{v.venue_type ?? '-'}</td>
                    <td className="px-4 py-2 text-sm text-gray-500">{v.dblp_id ?? '-'}</td>
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

        {venues && (
          <div className="px-4">
            <Pagination offset={offset} limit={PAGE_SIZE} count={venues.length} onChange={setOffset} />
          </div>
        )}
      </div>
    </div>
  );
}
