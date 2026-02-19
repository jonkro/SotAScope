import { useState } from 'react';
import { useConfirmDOI } from '../hooks/useEnrichment';
import type { DOIResolutionResult } from '../types';

interface Props {
  results: DOIResolutionResult[];
  onClose: () => void;
}

export default function DOIResolutionDialog({ results, onClose }: Props) {
  // Filter to only items that have candidates (need user confirmation)
  const needsConfirmation = results.filter((r) => r.candidates.length > 0 && !r.auto_resolved_doi);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [selectedDOI, setSelectedDOI] = useState<string | null>(null);
  const confirmMutation = useConfirmDOI();
  const [confirmedCount, setConfirmedCount] = useState(0);
  const [skippedCount, setSkippedCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  if (needsConfirmation.length === 0) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
          <p className="text-sm text-gray-700">No candidates need confirmation.</p>
          <button onClick={onClose} className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
            Close
          </button>
        </div>
      </div>
    );
  }

  if (currentIndex >= needsConfirmation.length) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">DOI Resolution Complete</h3>
          <p className="text-sm text-gray-700">
            {confirmedCount > 0 && <span className="text-green-600">{confirmedCount} confirmed. </span>}
            {skippedCount > 0 && <span className="text-gray-500">{skippedCount} skipped.</span>}
          </p>
          <button onClick={onClose} className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
            Close
          </button>
        </div>
      </div>
    );
  }

  const current = needsConfirmation[currentIndex];

  const handleApply = () => {
    if (!selectedDOI) return;
    setError(null);
    confirmMutation.mutate(
      { workId: current.work_id, doi: selectedDOI },
      {
        onSuccess: () => {
          setConfirmedCount((c) => c + 1);
          setSelectedDOI(null);
          setCurrentIndex((i) => i + 1);
          setError(null);
        },
        onError: (err) => {
          const msg = err instanceof Error ? err.message : String(err);
          // Try to extract JSON detail from API error body
          try {
            const parsed = JSON.parse(msg);
            setError(parsed.detail || msg);
          } catch {
            setError(msg);
          }
        },
      },
    );
  };

  const handleSkip = () => {
    setSkippedCount((c) => c + 1);
    setSelectedDOI(null);
    setCurrentIndex((i) => i + 1);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-lg shadow-lg w-full max-w-2xl mx-4 flex flex-col max-h-[80vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <h2 className="text-lg font-semibold text-gray-900">
            Confirm DOI Resolution
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        <div className="px-6 pb-2 text-xs text-gray-500">
          Work {currentIndex + 1} of {needsConfirmation.length} (ID: {current.work_id})
        </div>

        {/* Candidates */}
        <div className="flex-1 overflow-y-auto px-6 py-3 space-y-2">
          {current.candidates.map((c) => (
            <label
              key={c.doi}
              className={`flex items-start gap-3 p-3 border rounded cursor-pointer transition-colors ${
                selectedDOI === c.doi
                  ? 'border-blue-500 bg-blue-50'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <input
                type="radio"
                name="doi-candidate"
                value={c.doi}
                checked={selectedDOI === c.doi}
                onChange={() => setSelectedDOI(c.doi)}
                className="mt-1 shrink-0"
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900 leading-snug">{c.title}</p>
                <p className="text-xs text-gray-600 mt-0.5">
                  {c.authors.slice(0, 3).join(', ')}
                  {c.authors.length > 3 && ' et al.'}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1 text-xs text-gray-500">
                  {c.publication_year && <span>{c.publication_year}</span>}
                  {c.venue && <span className="truncate max-w-[200px]">{c.venue}</span>}
                  <span className="font-mono">{c.doi}</span>
                  <span className="text-gray-400">score: {c.score.toFixed(1)}</span>
                </div>
              </div>
            </label>
          ))}
        </div>

        {/* Error */}
        {error && (
          <div className="mx-6 mb-2 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-200">
          <button
            onClick={handleSkip}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            Skip
          </button>
          <button
            onClick={handleApply}
            disabled={!selectedDOI || confirmMutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {confirmMutation.isPending ? 'Applying...' : 'Apply DOI'}
          </button>
        </div>
      </div>
    </div>
  );
}
