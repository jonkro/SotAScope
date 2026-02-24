import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { searchImportConfirm } from '../api';
import type { SearchImportCandidate } from '../types';

interface Props {
  candidates: SearchImportCandidate[];
  onClose: () => void;
  onImported: (title: string) => void;
}

export default function SearchImportCandidateDialog({ candidates, onClose, onImported }: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const importMutation = useMutation({
    mutationFn: (candidate: SearchImportCandidate) =>
      searchImportConfirm({ doi: candidate.doi, semantic_scholar_id: candidate.semantic_scholar_id }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['works'] });
      onImported(data.work.title);
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    },
  });

  const handleImport = () => {
    if (selectedIdx === null) return;
    setError(null);
    importMutation.mutate(candidates[selectedIdx]);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-lg shadow-lg w-full max-w-2xl mx-4 flex flex-col max-h-[80vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <h2 className="text-lg font-semibold text-gray-900">Select a paper to import</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        {candidates.length === 0 ? (
          <div className="px-6 py-8 text-center text-sm text-gray-500">No candidates found.</div>
        ) : (
          <div className="flex-1 overflow-y-auto px-6 py-3 space-y-2">
            {candidates.map((c, idx) => (
              <label
                key={idx}
                className={`flex items-start gap-3 p-3 border rounded cursor-pointer transition-colors ${
                  selectedIdx === idx
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <input
                  type="radio"
                  name="search-candidate"
                  checked={selectedIdx === idx}
                  onChange={() => setSelectedIdx(idx)}
                  className="mt-1 shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 leading-snug">{c.title}</p>
                  <p className="text-xs text-gray-600 mt-0.5">
                    {c.authors.slice(0, 3).join(', ')}
                    {c.authors.length > 3 && ' et al.'}
                  </p>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 mt-1 text-xs text-gray-500">
                    {c.year && <span>{c.year}</span>}
                    {c.venue && <span className="truncate max-w-[200px]">{c.venue}</span>}
                    {c.doi && <span className="font-mono">{c.doi}</span>}
                    <span
                      className={`px-1.5 py-0.5 rounded font-medium ${
                        c.source === 'crossref'
                          ? 'bg-green-100 text-green-700'
                          : 'bg-purple-100 text-purple-700'
                      }`}
                    >
                      {c.source === 'crossref' ? 'Crossref' : 'Semantic Scholar'}
                    </span>
                    <span className="text-gray-400">score: {c.score.toFixed(1)}</span>
                  </div>
                </div>
              </label>
            ))}
          </div>
        )}

        {error && (
          <div className="mx-6 mb-2 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            Cancel
          </button>
          <button
            onClick={handleImport}
            disabled={selectedIdx === null || importMutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {importMutation.isPending ? 'Importing...' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}
