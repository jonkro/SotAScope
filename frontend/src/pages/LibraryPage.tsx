import { useState, useCallback } from 'react';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import Pagination from '../components/Pagination';
import EmptyState from '../components/EmptyState';
import WorkCard from '../components/WorkCard';
import WorkDetailPanel from '../components/WorkDetailPanel';
import { useWorks } from '../hooks/useWorks';

const PAGE_SIZE = 30;

export default function LibraryPage() {
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [selectedWorkId, setSelectedWorkId] = useState<number | null>(null);

  const handleSearch = useCallback((v: string) => {
    setSearch(v);
    setOffset(0);
  }, []);

  const { data: works, isLoading } = useWorks({
    offset,
    limit: PAGE_SIZE,
    q: search || undefined,
  });

  return (
    <div className="flex h-screen">
      <div className="flex-1 flex flex-col min-w-0">
        <PageHeader title="Library">
          <SearchInput value={search} onChange={handleSearch} placeholder="Search works..." />
        </PageHeader>

        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <p className="p-6 text-sm text-gray-400">Loading...</p>
          ) : !works?.length ? (
            <EmptyState message="No works in the library yet.">
              <p className="text-sm text-gray-400">Import papers using the Import button in the sidebar.</p>
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
          workId={selectedWorkId}
          onClose={() => setSelectedWorkId(null)}
        />
      )}
    </div>
  );
}
