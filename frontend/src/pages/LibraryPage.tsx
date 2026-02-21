import { useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import Pagination from '../components/Pagination';
import EmptyState from '../components/EmptyState';
import WorkCard from '../components/WorkCard';
import WorkDetailPanel, { DEFAULT_FOLD_STATE, type PanelFoldState } from '../components/WorkDetailPanel';
import ImportDialog from '../components/ImportDialog';
import SanitizeDialog from '../components/SanitizeDialog';
import { useWorks } from '../hooks/useWorks';

const PAGE_SIZE = 30;

export default function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [selectedWorkId, setSelectedWorkId] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [sanitizeOpen, setSanitizeOpen] = useState(false);
  const [panelFoldState, setPanelFoldState] = useState<PanelFoldState>({ ...DEFAULT_FOLD_STATE });

  const venueIdParam = searchParams.get('venue_id');
  const venueIdFilter = venueIdParam ? Number(venueIdParam) : undefined;

  const handleSearch = useCallback((v: string) => {
    setSearch(v);
    setOffset(0);
  }, []);

  const clearVenueFilter = useCallback(() => {
    setSearchParams({}, { replace: true });
    setOffset(0);
  }, [setSearchParams]);

  const { data: works, isLoading } = useWorks({
    offset,
    limit: PAGE_SIZE,
    q: search || undefined,
    venue_id: venueIdFilter,
  });

  // Derive venue name from first result (all share the same venue when filtered)
  const venueFilterName = venueIdFilter && works?.length ? works[0].venue_display_name : null;

  return (
    <div className="flex h-screen">
      <div className="flex-1 flex flex-col min-w-0">
        <PageHeader title="Library">
          <SearchInput value={search} onChange={handleSearch} placeholder="Search title, authors, or venue..." />
          <button
            onClick={() => setSanitizeOpen(true)}
            className="px-3 py-1.5 text-sm font-medium text-gray-700 border border-gray-300 rounded hover:bg-gray-50"
          >
            Sanitize Library
          </button>
          <button
            onClick={() => setImportOpen(true)}
            className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
          >
            Import
          </button>
        </PageHeader>

        {venueIdFilter && (
          <div className="px-4 py-2 bg-blue-50 border-b border-blue-100 flex items-center gap-2">
            <span className="text-xs text-blue-700">
              Filtered by venue: <strong>{venueFilterName ?? `#${venueIdFilter}`}</strong>
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
          {isLoading ? (
            <p className="p-6 text-sm text-gray-400">Loading...</p>
          ) : !works?.length ? (
            <EmptyState message="No works in the library yet.">
              <p className="text-sm text-gray-400">Import papers using the Import button above.</p>
            </EmptyState>
          ) : (
            <div>
              {works.map((w) => (
                <WorkCard
                  key={w.id}
                  work={w}
                  isSelected={selectedWorkId === w.id}
                  onClick={() => setSelectedWorkId(w.id === selectedWorkId ? null : w.id)}
                />
              ))}
            </div>
          )}

          {works && (
            <div className="px-4">
              <Pagination offset={offset} limit={PAGE_SIZE} count={works.length} onChange={setOffset} />
            </div>
          )}
        </div>
      </div>

      {selectedWorkId !== null && (
        <WorkDetailPanel
          key={selectedWorkId}
          workId={selectedWorkId}
          onClose={() => setSelectedWorkId(null)}
          onDelete={() => setSelectedWorkId(null)}
          onSelectWork={setSelectedWorkId}
          foldState={panelFoldState}
          onFoldChange={setPanelFoldState}
        />
      )}

      {importOpen && <ImportDialog onClose={() => setImportOpen(false)} />}
      {sanitizeOpen && <SanitizeDialog onClose={() => setSanitizeOpen(false)} />}
    </div>
  );
}
