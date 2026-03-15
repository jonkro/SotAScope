import { useState, useMemo, useRef, useEffect } from 'react';
import type { TopicListOut, TimelineSeedWork } from '../types';

interface Props {
  projectId: number;
  projectName: string;
  topicLists: TopicListOut[];
  seeds: TimelineSeedWork[];
  onClose: () => void;
}

/** Mirrors the backend safe-name logic: keep alphanumeric and - _, replace rest with _. */
function toSafeFilename(name: string, ext: string): string {
  const safe = name.replace(/[^a-zA-Z0-9\-_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  return `${safe || 'project'}.${ext}`;
}

export default function BibTeXExportDialog({ projectId, projectName, topicLists, seeds, onClose }: Props) {
  const defaultFilename = toSafeFilename(projectName, 'bib');
  const [filename, setFilename] = useState(defaultFilename);

  // Build a map from work ID → seed for quick access
  const seedMap = useMemo(() => new Map(seeds.map((s) => [s.id, s])), [seeds]);

  // Build per-topic-list work ID arrays (a seed may appear in multiple lists)
  const worksByList = useMemo(() => {
    const m = new Map<number, number[]>();
    for (const tl of topicLists) {
      m.set(tl.id, []);
    }
    for (const seed of seeds) {
      for (const tlId of seed.topic_list_ids) {
        m.get(tlId)?.push(seed.id);
      }
    }
    return m;
  }, [topicLists, seeds]);

  // All unique work IDs across all lists
  const allWorkIds = useMemo(() => seeds.map((s) => s.id), [seeds]);

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set(allWorkIds));

  const allSelected = selectedIds.size === allWorkIds.length;
  const noneSelected = selectedIds.size === 0;

  // Indeterminate ref helper for the "Select all" checkbox
  const selectAllRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      const some = selectedIds.size > 0 && !allSelected;
      selectAllRef.current.indeterminate = some;
    }
  }, [selectedIds, allSelected]);

  const toggleAll = () => {
    setSelectedIds(allSelected ? new Set() : new Set(allWorkIds));
  };

  const toggleList = (tlId: number) => {
    const ids = worksByList.get(tlId) ?? [];
    const allInList = ids.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allInList) {
        ids.forEach((id) => next.delete(id));
      } else {
        ids.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const toggleWork = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleExport = () => {
    const ids = Array.from(selectedIds);
    const url =
      ids.length === allWorkIds.length
        ? `/api/projects/${projectId}/export/bibtex`
        : `/api/projects/${projectId}/export/bibtex?work_ids=${ids.join(',')}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = filename.trim() || defaultFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-lg w-full max-w-lg mx-4 flex flex-col max-h-[80vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-gray-100">
          <h3 className="text-base font-semibold text-gray-900">Export BibTeX</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        {/* Filename input */}
        <div className="px-5 pt-3 pb-2">
          <label className="block text-xs font-medium text-gray-600 mb-1">Filename</label>
          <input
            type="text"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            className="w-full px-2.5 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
            spellCheck={false}
          />
        </div>

        {/* Select all / none bar */}
        <div className="flex items-center gap-4 px-5 py-2 border-b border-gray-100 bg-gray-50">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              ref={selectAllRef}
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              className="rounded"
            />
            <span className="text-sm text-gray-700 font-medium">Select all</span>
          </label>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-xs text-gray-500 hover:text-gray-700 underline"
          >
            None
          </button>
          <span className="ml-auto text-xs text-gray-500">
            {selectedIds.size} / {allWorkIds.length} selected
          </span>
        </div>

        {/* Per-topic-list work list */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-4">
          {seeds.length === 0 ? (
            <p className="text-sm text-gray-500">No seed papers in this project.</p>
          ) : (
            topicLists.map((tl) => {
              const wids = worksByList.get(tl.id) ?? [];
              if (wids.length === 0) return null;
              const allInList = wids.every((id) => selectedIds.has(id));
              const someInList = wids.some((id) => selectedIds.has(id));

              return (
                <div key={tl.id}>
                  {/* Topic list header */}
                  <label className="flex items-center gap-2 cursor-pointer mb-1">
                    <TopicListCheckbox
                      checked={allInList}
                      indeterminate={!allInList && someInList}
                      onChange={() => toggleList(tl.id)}
                    />
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0"
                      style={{ backgroundColor: tl.color }}
                    />
                    <span className="text-sm font-medium text-gray-800">{tl.name}</span>
                    <span className="text-xs text-gray-400">({wids.length})</span>
                  </label>

                  {/* Works in list */}
                  <div className="ml-5 space-y-0.5">
                    {wids.map((wid) => {
                      const seed = seedMap.get(wid);
                      if (!seed) return null;
                      return (
                        <label key={wid} className="flex items-start gap-2 cursor-pointer py-0.5">
                          <input
                            type="checkbox"
                            checked={selectedIds.has(wid)}
                            onChange={() => toggleWork(wid)}
                            className="mt-0.5 rounded flex-shrink-0"
                          />
                          <span className="text-sm text-gray-700 leading-snug">
                            {seed.title}
                            {seed.publication_year != null && (
                              <span className="text-gray-400 ml-1">({seed.publication_year})</span>
                            )}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-5 py-4 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            disabled={noneSelected}
            className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Export {selectedIds.size > 0 ? `${selectedIds.size} paper${selectedIds.size !== 1 ? 's' : ''}` : ''}
          </button>
        </div>
      </div>
    </div>
  );
}

// Checkbox that supports indeterminate state via a ref
function TopicListCheckbox({
  checked,
  indeterminate,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return <input ref={ref} type="checkbox" checked={checked} onChange={onChange} className="rounded flex-shrink-0" />;
}
