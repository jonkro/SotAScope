import { useState, useRef, useCallback, useEffect } from 'react';
import { useDuplicates, useMergeWorks } from '../hooks/useWorks';
import type { DuplicateGroup, WorkOut } from '../types';

interface Props {
  onClose: () => void;
}

function WorkCard({
  work,
  isSelected,
  onSelect,
}: {
  work: WorkOut;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <label
      className={`flex items-start gap-3 p-3 border rounded cursor-pointer transition-colors ${
        isSelected ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'
      }`}
    >
      <input
        type="radio"
        name="merge-target"
        checked={isSelected}
        onChange={onSelect}
        className="mt-1 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-gray-900 leading-snug">{work.title}</p>
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1 text-xs text-gray-500">
          {work.publication_year && <span>{work.publication_year}</span>}
          {work.doi && <span className="font-mono truncate max-w-[200px]">{work.doi}</span>}
          {work.arxiv_id && <span>arXiv: {work.arxiv_id}</span>}
          {work.openalex_id && <span>OA: {work.openalex_id}</span>}
          {work.citation_count != null && <span>{work.citation_count} citations</span>}
          {work.bibtex_key && <span>key: {work.bibtex_key}</span>}
        </div>
      </div>
    </label>
  );
}

export default function SanitizeDialog({ onClose }: Props) {
  const { data: groups, isLoading, error: fetchError } = useDuplicates(true);
  const mergeMutation = useMergeWorks();

  // Snapshot groups on first successful load so the list stays stable
  const snapshotRef = useRef<DuplicateGroup[] | null>(null);
  if (groups && !snapshotRef.current) {
    snapshotRef.current = groups;
  }

  const [currentIndex, setCurrentIndex] = useState(0);
  const [selectedWorkId, setSelectedWorkId] = useState<number | null>(null);
  const [mergedCount, setMergedCount] = useState(0);
  const [skippedCount, setSkippedCount] = useState(0);
  const [dismissedCount, setDismissedCount] = useState(0);
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [isMerging, setIsMerging] = useState(false);
  // Track work IDs that have been deleted via merge so we can filter them
  // out of subsequent groups
  const [mergedAwayIds, setMergedAwayIds] = useState<Set<number>>(new Set());

  const allGroups = snapshotRef.current ?? [];

  // Compute the "live" view of the current group: filter out already-merged works
  const getLiveWorks = useCallback(
    (group: DuplicateGroup): WorkOut[] =>
      group.works.filter((w) => !mergedAwayIds.has(w.id)),
    [mergedAwayIds],
  );

  // Auto-skip groups that have been fully resolved by earlier merges
  useEffect(() => {
    if (isMerging || allGroups.length === 0) return;
    let idx = currentIndex;
    while (idx < allGroups.length) {
      const live = getLiveWorks(allGroups[idx]);
      if (live.length >= 2) break;
      // This group is already resolved — auto-skip it
      idx++;
      setSkippedCount((c) => c + 1);
    }
    if (idx !== currentIndex) {
      setCurrentIndex(idx);
      setSelectedWorkId(null);
      setMergeError(null);
    }
  }, [currentIndex, allGroups, getLiveWorks, isMerging]);

  if (isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
          <p className="text-sm text-gray-400">Scanning for duplicates...</p>
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
          <p className="text-sm text-red-600">Error scanning duplicates: {String(fetchError)}</p>
          <button onClick={onClose} className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
            Close
          </button>
        </div>
      </div>
    );
  }

  if (allGroups.length === 0) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">No Duplicates Found</h3>
          <p className="text-sm text-gray-700">Your library looks clean.</p>
          <button onClick={onClose} className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
            Close
          </button>
        </div>
      </div>
    );
  }

  // Summary screen
  if (currentIndex >= allGroups.length) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">Sanitization Complete</h3>
          <div className="text-sm text-gray-700 space-y-1">
            {mergedCount > 0 && <p className="text-green-600">{mergedCount} group{mergedCount !== 1 ? 's' : ''} merged.</p>}
            {dismissedCount > 0 && <p className="text-gray-500">{dismissedCount} dismissed.</p>}
            {skippedCount > 0 && <p className="text-gray-400">{skippedCount} already resolved by earlier merges.</p>}
          </div>
          <button onClick={onClose} className="mt-4 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700">
            Close
          </button>
        </div>
      </div>
    );
  }

  const currentGroup = allGroups[currentIndex];
  const liveWorks = getLiveWorks(currentGroup);
  const effectiveSelected = selectedWorkId ?? liveWorks[0]?.id ?? null;

  const handleMerge = async () => {
    if (effectiveSelected === null) return;
    setMergeError(null);
    setIsMerging(true);

    const sourcesToMerge = liveWorks.filter((w) => w.id !== effectiveSelected);
    const newlyMergedIds = new Set<number>();
    try {
      for (const source of sourcesToMerge) {
        await mergeMutation.mutateAsync({ targetId: effectiveSelected, sourceId: source.id });
        newlyMergedIds.add(source.id);
      }
      setMergedAwayIds((prev) => new Set([...prev, ...newlyMergedIds]));
      setMergedCount((c) => c + 1);
      setSelectedWorkId(null);
      setCurrentIndex((i) => i + 1);
    } catch (err) {
      // Record any partial progress
      if (newlyMergedIds.size > 0) {
        setMergedAwayIds((prev) => new Set([...prev, ...newlyMergedIds]));
      }
      const msg = err instanceof Error ? err.message : String(err);
      try {
        const parsed = JSON.parse(msg);
        setMergeError(parsed.detail || msg);
      } catch {
        setMergeError(msg);
      }
    } finally {
      setIsMerging(false);
    }
  };

  const handleDismiss = () => {
    setDismissedCount((c) => c + 1);
    setSelectedWorkId(null);
    setMergeError(null);
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
          <h2 className="text-lg font-semibold text-gray-900">Sanitize Library</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        <div className="px-6 pb-2 text-xs text-gray-500">
          Group {currentIndex + 1} of {allGroups.length} &mdash; {currentGroup.reason}
        </div>
        <p className="px-6 pb-3 text-xs text-gray-500">
          Select the work to keep. All others will be merged into it.
        </p>

        {/* Work cards */}
        <div className="flex-1 overflow-y-auto px-6 py-3 space-y-2">
          {liveWorks.map((w) => (
            <WorkCard
              key={w.id}
              work={w}
              isSelected={effectiveSelected === w.id}
              onSelect={() => setSelectedWorkId(w.id)}
            />
          ))}
        </div>

        {/* Error */}
        {mergeError && (
          <div className="mx-6 mb-2 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
            {mergeError}
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-200">
          <button
            onClick={handleDismiss}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            Dismiss
          </button>
          <button
            onClick={handleMerge}
            disabled={isMerging}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {isMerging ? 'Merging...' : 'Merge'}
          </button>
        </div>
      </div>
    </div>
  );
}
