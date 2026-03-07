import { useState, useMemo, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import {
  useExtractionSchemas,
  useExtractionSchema,
  useCreateExtractionSchema,
  useUpdateExtractionSchema,
  useDeleteExtractionSchema,
  useCreateExtractionColumn,
  useUpdateExtractionColumn,
  useDeleteExtractionColumn,
  useReorderExtractionColumns,
  useExtractionResults,
  useRunSingleExtraction,
  useAcceptExtractionNote,
  useEditExtractionNote,
} from '../hooks/useExtraction';
import { useTimeline } from '../hooks/useTimeline';
import { getExtractionPromptPreview } from '../api';
import type { ExtractionColumn, ExtractionSchema, ExtractionCellResult } from '../types';

// ---------------------------------------------------------------------------
// Provenance badge
// ---------------------------------------------------------------------------

function ProvenanceBadge({ provenance }: { provenance: string }) {
  const cls =
    provenance === 'ai_reviewed'
      ? 'bg-purple-100 text-purple-700'
      : provenance === 'user'
        ? 'bg-green-100 text-green-700'
        : 'bg-blue-100 text-blue-700';
  const label =
    provenance === 'ai_reviewed' ? 'reviewed' : provenance === 'user' ? 'user' : 'ai';
  return (
    <span className={`inline-block px-1.5 py-0.5 text-[10px] rounded font-medium leading-none ${cls}`}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Single extraction table cell
// ---------------------------------------------------------------------------

interface ExtractionCellProps {
  cell: ExtractionCellResult | null;
  workId: number;
  schemaId: number;
  column: ExtractionColumn;
  isRunningGlobal: boolean;
  onExtractSingle: (workId: number) => void;
}

function ExtractionCell({
  cell,
  workId,
  schemaId: _schemaId,
  column,
  isRunningGlobal,
  onExtractSingle,
}: ExtractionCellProps) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [showReasoning, setShowReasoning] = useState(false);

  const accept = useAcceptExtractionNote();
  const editNote = useEditExtractionNote();

  if (!cell) {
    return (
      <td className="px-3 py-2 text-center align-top border-r border-gray-100 last:border-r-0">
        <button
          onClick={() => onExtractSingle(workId)}
          disabled={isRunningGlobal}
          title="Extract this cell"
          className="text-xs text-gray-400 hover:text-blue-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          ⚡
        </button>
      </td>
    );
  }

  const { answer_note, reasoning_note } = cell;

  if (editing) {
    const hasAllowedValues = column.allowed_values && column.allowed_values.length > 0;

    const handleSave = () => {
      editNote.mutate({ workId, noteId: answer_note.id, content: editValue });
      setEditing(false);
    };

    return (
      <td className="px-3 py-2 align-top border-r border-gray-100 last:border-r-0 min-w-[180px]">
        {hasAllowedValues ? (
          <select
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            className="w-full text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {column.allowed_values!.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        ) : (
          <textarea
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            rows={3}
            className="w-full text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          />
        )}
        <div className="flex gap-1.5 mt-1.5">
          <button
            onClick={handleSave}
            disabled={editNote.isPending}
            className="px-2 py-0.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {editNote.isPending ? '…' : 'Save'}
          </button>
          <button
            onClick={() => setEditing(false)}
            className="px-2 py-0.5 text-xs border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </td>
    );
  }

  const truncated =
    answer_note.content.length > 80
      ? answer_note.content.slice(0, 80) + '…'
      : answer_note.content;

  return (
    <td className="px-3 py-2 align-top border-r border-gray-100 last:border-r-0 min-w-[160px] max-w-[240px]">
      <div className="text-xs text-gray-800 leading-snug mb-1" title={answer_note.content}>
        {truncated || <span className="text-gray-400 italic">empty</span>}
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <ProvenanceBadge provenance={answer_note.provenance} />
        {reasoning_note && (
          <button
            onClick={() => setShowReasoning((v) => !v)}
            title="Show/hide reasoning"
            className="text-[10px] text-gray-400 hover:text-gray-700 leading-none"
          >
            {showReasoning ? '▾ hide' : 'ⓘ'}
          </button>
        )}
        {answer_note.provenance === 'ai' && (
          <button
            onClick={() => accept.mutate({ workId, noteId: answer_note.id })}
            disabled={accept.isPending}
            title="Accept — mark as reviewed"
            className="text-[10px] text-green-600 hover:text-green-800 disabled:opacity-50 leading-none"
          >
            ✓
          </button>
        )}
        <button
          onClick={() => {
            setEditValue(answer_note.content);
            setEditing(true);
          }}
          title="Edit"
          className="text-[10px] text-blue-500 hover:text-blue-700 leading-none"
        >
          ✎
        </button>
      </div>
      {showReasoning && reasoning_note && (
        <div className="mt-1.5 p-2 bg-gray-50 rounded text-[11px] text-gray-600 leading-snug border border-gray-200">
          {reasoning_note.content}
        </div>
      )}
    </td>
  );
}

// ---------------------------------------------------------------------------
// Extraction run & review panel
// ---------------------------------------------------------------------------

interface ExtractionRunViewProps {
  schema: ExtractionSchema;
}

function ExtractionRunView({ schema }: ExtractionRunViewProps) {
  const projectId = schema.project_id;

  const { data: timeline, isLoading: timelineLoading } = useTimeline(projectId ?? 0);
  const seeds = useMemo(
    () =>
      (timeline?.seeds ?? []).slice().sort((a, b) =>
        (a.publication_year ?? 0) !== (b.publication_year ?? 0)
          ? (b.publication_year ?? 0) - (a.publication_year ?? 0)
          : a.title.localeCompare(b.title),
      ),
    [timeline],
  );

  const topicLists = useMemo(() => timeline?.topic_lists ?? [], [timeline]);
  const topicListColorMap = useMemo(
    () => new Map(topicLists.map((tl) => [tl.id, tl.color])),
    [topicLists],
  );

  // Work selection state
  const [searchQ, setSearchQ] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const initializedRef = useRef(false);

  // Default: select all seeds on first load
  useEffect(() => {
    if (!initializedRef.current && seeds.length > 0) {
      setSelectedIds(new Set(seeds.map((s) => s.id)));
      initializedRef.current = true;
    }
  }, [seeds]);

  const filteredSeeds = useMemo(() => {
    if (!searchQ.trim()) return seeds;
    const q = searchQ.toLowerCase();
    return seeds.filter((s) => s.title.toLowerCase().includes(q));
  }, [seeds, searchQ]);

  // Sort: selected papers float to top (preserving existing order within each group)
  const sortedFilteredSeeds = useMemo(() => {
    const selected = filteredSeeds.filter((s) => selectedIds.has(s.id));
    const unselected = filteredSeeds.filter((s) => !selectedIds.has(s.id));
    return [...selected, ...unselected];
  }, [filteredSeeds, selectedIds]);

  // Existing results
  const allSeedIds = useMemo(() => seeds.map((s) => s.id), [seeds]);
  const { data: resultsData, refetch: refetchResults } = useExtractionResults(
    schema.id,
    allSeedIds,
  );

  // Index cells by "workId:columnId"
  const cellsMap = useMemo(() => {
    const map = new Map<string, ExtractionCellResult>();
    for (const cell of resultsData?.cells ?? []) {
      map.set(`${cell.work_id}:${cell.column_id}`, cell);
    }
    return map;
  }, [resultsData]);

  // Check if any selected work already has notes
  const hasExistingNotes = useMemo(() => {
    if (!resultsData) return false;
    return resultsData.cells.some((c) => selectedIds.has(c.work_id));
  }, [resultsData, selectedIds]);

  // Extraction progress state
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractProgress, setExtractProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [extractErrors, setExtractErrors] = useState<{ workId: number; msg: string }[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);

  // Prompt preview state
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  const [promptPreview, setPromptPreview] = useState<{ system_text: string; user_message: string } | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);

  const runSingle = useRunSingleExtraction(schema.id);

  const doExtract = async (ids: number[]) => {
    setIsExtracting(true);
    setExtractProgress({ done: 0, total: ids.length });
    const errs: { workId: number; msg: string }[] = [];

    for (let i = 0; i < ids.length; i++) {
      try {
        await runSingle.mutateAsync(ids[i]);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        errs.push({ workId: ids[i], msg });
      }
      setExtractProgress({ done: i + 1, total: ids.length });
    }

    setExtractErrors(errs);
    setIsExtracting(false);
    refetchResults();
  };

  const handleExtractClick = () => {
    if (hasExistingNotes) {
      setShowConfirm(true);
    } else {
      doExtract(Array.from(selectedIds));
    }
  };

  const handleExtractSingle = async (workId: number) => {
    setIsExtracting(true);
    try {
      await runSingle.mutateAsync(workId);
    } catch {
      // shown via error state if needed
    }
    setIsExtracting(false);
    refetchResults();
  };

  const handleShowPrompt = async () => {
    // Pick the first selected paper, or the first seed if nothing is selected
    const firstId = Array.from(selectedIds)[0] ?? seeds[0]?.id;
    if (firstId == null) return;
    setPromptPreview(null);
    setPromptPreviewLoading(true);
    setShowPromptPreview(true);
    try {
      const data = await getExtractionPromptPreview(schema.id, firstId);
      setPromptPreview(data);
    } catch {
      setPromptPreview(null);
    } finally {
      setPromptPreviewLoading(false);
    }
  };

  const sortedColumns = useMemo(
    () => [...schema.columns].sort((a, b) => a.sort_order - b.sort_order),
    [schema.columns],
  );

  const toggleId = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelectedIds(new Set(filteredSeeds.map((s) => s.id)));
  const deselectAll = () => setSelectedIds(new Set());

  // Topic list bulk-select: indeterminate state via refs
  const tlCheckboxRefs = useRef<Map<number, HTMLInputElement>>(new Map());

  useEffect(() => {
    for (const tl of topicLists) {
      const el = tlCheckboxRefs.current.get(tl.id);
      if (!el) continue;
      const tlSeeds = seeds.filter((s) => s.topic_list_ids.includes(tl.id));
      const selectedCount = tlSeeds.filter((s) => selectedIds.has(s.id)).length;
      el.indeterminate = selectedCount > 0 && selectedCount < tlSeeds.length;
    }
  }, [selectedIds, topicLists, seeds]);

  const handleBulkTopicListToggle = (tlId: number) => {
    setSelectedIds((prev) => {
      const tlSeedIds = seeds
        .filter((s) => s.topic_list_ids.includes(tlId))
        .map((s) => s.id);
      const anySelected = tlSeedIds.some((id) => prev.has(id));
      const next = new Set(prev);
      if (anySelected) {
        for (const id of tlSeedIds) next.delete(id);
      } else {
        for (const id of tlSeedIds) next.add(id);
      }
      return next;
    });
  };

  // --- Render ---

  if (projectId == null) {
    return (
      <div className="p-6 text-sm text-gray-500">
        This schema is not associated with a project. To use Extract &amp; Review, create a
        project-scoped schema from a project page.
      </div>
    );
  }

  if (timelineLoading) {
    return <div className="p-6 text-sm text-gray-400">Loading project papers…</div>;
  }

  if (seeds.length === 0) {
    return (
      <div className="p-6 text-sm text-gray-500">
        No seed papers found in this project. Add papers to a topic list first.
      </div>
    );
  }

  if (sortedColumns.length === 0) {
    return (
      <div className="p-6 text-sm text-gray-500">
        This schema has no columns yet. Go to the{' '}
        <strong>Schema</strong> tab to add columns first.
      </div>
    );
  }

  const selectedCount = selectedIds.size;
  const isDone =
    !isExtracting && extractProgress !== null && extractProgress.done === extractProgress.total;

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Top action bar */}
      <div className="shrink-0 border-b border-gray-100 bg-gray-50 px-4 py-3 flex items-center gap-3 flex-wrap">
        <input
          type="text"
          value={searchQ}
          onChange={(e) => setSearchQ(e.target.value)}
          placeholder="Search papers…"
          className="border border-gray-300 rounded px-2.5 py-1.5 text-sm w-52 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={selectAll}
          className="text-xs text-blue-600 hover:underline"
        >
          Select all
        </button>
        <button
          onClick={deselectAll}
          className="text-xs text-gray-500 hover:underline"
        >
          Deselect all
        </button>
        <span className="text-xs text-gray-500">
          {selectedCount} of {seeds.length} selected
        </span>
        {/* Export buttons */}
        {allSeedIds.length > 0 && (
          <>
            <button
              onClick={() => {
                const ids = selectedIds.size > 0 ? Array.from(selectedIds) : allSeedIds;
                window.open(
                  `/api/extraction/schemas/${schema.id}/export?format=csv&work_ids=${ids.join(',')}`,
                );
              }}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-700"
              title="Export current selection as CSV"
            >
              Export CSV
            </button>
            <button
              onClick={() => {
                const ids = selectedIds.size > 0 ? Array.from(selectedIds) : allSeedIds;
                window.open(
                  `/api/extraction/schemas/${schema.id}/export?format=latex&work_ids=${ids.join(',')}`,
                );
              }}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-700"
              title="Export current selection as LaTeX table"
            >
              Export LaTeX
            </button>
          </>
        )}
        <div className="flex-1" />
        {isExtracting && extractProgress && (
          <span className="text-xs text-gray-500 flex items-center gap-1.5">
            <svg className="animate-spin h-3.5 w-3.5 text-blue-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            Processing {extractProgress.done} of {extractProgress.total}…
          </span>
        )}
        {isDone && extractErrors.length === 0 && (
          <span className="text-xs text-green-600">
            ✓ Done — {extractProgress!.done} paper{extractProgress!.done !== 1 ? 's' : ''} extracted
          </span>
        )}
        {isDone && extractErrors.length > 0 && (
          <span className="text-xs text-amber-600">
            Done with {extractErrors.length} error{extractErrors.length !== 1 ? 's' : ''}
          </span>
        )}
        <button
          onClick={handleShowPrompt}
          disabled={seeds.length === 0 || isExtracting}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          title="Preview the prompt that will be sent to the LLM (paper text replaced with placeholder)"
        >
          Show prompt
        </button>
        <button
          onClick={handleExtractClick}
          disabled={selectedCount === 0 || isExtracting}
          className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isExtracting ? 'Extracting…' : `Extract ${selectedCount > 0 ? selectedCount : ''} paper${selectedCount !== 1 ? 's' : ''} →`}
        </button>
      </div>

      {/* Topic list bulk-select row */}
      {topicLists.length > 0 && (
        <div className="shrink-0 border-b border-gray-100 bg-white px-4 py-2 flex flex-wrap gap-x-4 gap-y-1.5 items-center">
          <span className="text-[11px] text-gray-500 font-medium">Topic lists:</span>
          {topicLists.map((tl) => {
            const tlSeeds = seeds.filter((s) => s.topic_list_ids.includes(tl.id));
            const selectedCount = tlSeeds.filter((s) => selectedIds.has(s.id)).length;
            const allSelected = tlSeeds.length > 0 && selectedCount === tlSeeds.length;
            return (
              <label key={tl.id} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  ref={(el) => {
                    if (el) tlCheckboxRefs.current.set(tl.id, el);
                    else tlCheckboxRefs.current.delete(tl.id);
                  }}
                  checked={allSelected}
                  onChange={() => handleBulkTopicListToggle(tl.id)}
                  className="w-3.5 h-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
                />
                <span className="text-[11px] font-medium" style={{ color: tl.color }}>
                  {tl.name}
                </span>
                <span className="text-[11px] text-gray-400">
                  ({selectedCount}/{tlSeeds.length})
                </span>
              </label>
            );
          })}
        </div>
      )}

      {/* Error summary */}
      {extractErrors.length > 0 && (
        <div className="shrink-0 px-4 py-2 bg-red-50 border-b border-red-100 text-xs text-red-700">
          {extractErrors.map((e) => {
            const work = seeds.find((s) => s.id === e.workId);
            return (
              <div key={e.workId}>
                <strong>{work?.title ?? `Work #${e.workId}`}:</strong> {e.msg}
              </div>
            );
          })}
        </div>
      )}

      {/* Results table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm border-collapse">
          <thead className="bg-gray-50 sticky top-0 z-10">
            <tr>
              <th className="w-6 px-3 py-2 text-left border-b border-r border-gray-200">
                <input
                  type="checkbox"
                  checked={
                    filteredSeeds.length > 0 &&
                    filteredSeeds.every((s) => selectedIds.has(s.id))
                  }
                  onChange={(e) => (e.target.checked ? selectAll() : deselectAll())}
                  className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
                />
              </th>
              <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 border-b border-r border-gray-200 sticky left-0 bg-gray-50 z-20 min-w-[200px] max-w-[280px]">
                Paper
              </th>
              <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 border-b border-r border-gray-200 whitespace-nowrap">
                Year
              </th>
              {sortedColumns.map((col) => (
                <th
                  key={col.id}
                  className="px-3 py-2 text-left text-xs font-semibold text-gray-700 border-b border-r border-gray-200 last:border-r-0 min-w-[160px] max-w-[240px]"
                  title={col.prompt}
                >
                  {col.name}
                  {col.allowed_values && col.allowed_values.length > 0 && (
                    <span className="ml-1 font-normal text-gray-400">
                      ({col.allowed_values.length})
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sortedFilteredSeeds.map((seed) => (
              <tr
                key={seed.id}
                className={selectedIds.has(seed.id) ? 'bg-white' : 'bg-gray-50 opacity-60'}
              >
                <td className="px-3 py-2 text-center align-top border-r border-gray-100">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(seed.id)}
                    onChange={() => toggleId(seed.id)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
                  />
                </td>
                <td
                  className="p-0 align-top border-r border-gray-100 sticky left-0 bg-inherit z-[5] min-w-[200px] max-w-[280px]"
                >
                  <div className="flex" style={{ minHeight: '2.25rem' }}>
                    {seed.topic_list_ids
                      .map((id) => topicListColorMap.get(id))
                      .filter((c): c is string => Boolean(c))
                      .map((color, i) => (
                        <div
                          key={i}
                          style={{ width: 3, backgroundColor: color, alignSelf: 'stretch' }}
                        />
                      ))}
                    <div className="px-3 py-2 min-w-0 flex-1">
                      <p className="text-xs font-medium text-gray-900 line-clamp-2">{seed.title}</p>
                    </div>
                  </div>
                </td>
                <td className="px-3 py-2 align-top border-r border-gray-100 text-xs text-gray-500 whitespace-nowrap">
                  {seed.publication_year ?? '—'}
                </td>
                {sortedColumns.map((col) => (
                  <ExtractionCell
                    key={col.id}
                    cell={cellsMap.get(`${seed.id}:${col.id}`) ?? null}
                    workId={seed.id}
                    schemaId={schema.id}
                    column={col}
                    isRunningGlobal={isExtracting}
                    onExtractSingle={handleExtractSingle}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>

        {filteredSeeds.length === 0 && searchQ && (
          <div className="p-6 text-center text-sm text-gray-400">
            No papers match &ldquo;{searchQ}&rdquo;
          </div>
        )}
      </div>

      {/* Prompt preview modal */}
      {showPromptPreview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowPromptPreview(false)}
        >
          <div
            className="bg-white rounded-lg shadow-xl max-w-3xl w-full mx-4 max-h-[85vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between shrink-0">
              <div>
                <h3 className="text-base font-semibold text-gray-900">Prompt preview</h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Showing prompt for the first selected paper. Paper text replaced with placeholder.
                </p>
              </div>
              <button
                onClick={() => setShowPromptPreview(false)}
                className="text-gray-400 hover:text-gray-700 text-lg leading-none ml-4"
              >
                ✕
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {promptPreviewLoading ? (
                <p className="text-sm text-gray-400">Loading…</p>
              ) : promptPreview ? (
                <>
                  <div>
                    <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">System</p>
                    <pre className="text-xs font-mono whitespace-pre-wrap text-gray-700 bg-gray-50 rounded p-3 border border-gray-200 leading-relaxed">
                      {promptPreview.system_text}
                    </pre>
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">User message</p>
                    <pre className="text-xs font-mono whitespace-pre-wrap text-gray-700 bg-gray-50 rounded p-3 border border-gray-200 leading-relaxed">
                      {promptPreview.user_message}
                    </pre>
                  </div>
                </>
              ) : (
                <p className="text-sm text-red-500">Failed to load prompt preview.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Re-extraction confirmation */}
      {showConfirm && (
        <ConfirmDialog
          title="Re-extract papers?"
          message={`${resultsData?.cells.filter((c) => selectedIds.has(c.work_id)).length ?? 0} paper(s) already have extraction notes for this schema. Running extraction will overwrite AI-generated notes. User-reviewed notes will be preserved.`}
          confirmLabel="Extract"
          onConfirm={() => {
            setShowConfirm(false);
            doExtract(Array.from(selectedIds));
          }}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column form modal (create or edit a column)
// ---------------------------------------------------------------------------

interface ColumnFormModalProps {
  schemaId: number;
  initial?: ExtractionColumn;
  nextSortOrder: number;
  onClose: () => void;
}

function ColumnFormModal({ schemaId, initial, nextSortOrder, onClose }: ColumnFormModalProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [prompt, setPrompt] = useState(initial?.prompt ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [allowedValues, setAllowedValues] = useState<string[]>(initial?.allowed_values ?? []);
  const [tagInput, setTagInput] = useState('');

  const createCol = useCreateExtractionColumn(schemaId);
  const updateCol = useUpdateExtractionColumn(schemaId);
  const isPending = createCol.isPending || updateCol.isPending;

  const handleSave = async () => {
    if (!name.trim() || !prompt.trim()) return;
    const data = {
      name: name.trim(),
      prompt: prompt.trim(),
      description: description.trim() || null,
      allowed_values: allowedValues.length > 0 ? allowedValues : null,
    };
    if (initial) {
      await updateCol.mutateAsync({ columnId: initial.id, data });
    } else {
      await createCol.mutateAsync({ ...data, sort_order: nextSortOrder });
    }
    onClose();
  };

  const addTag = () => {
    const val = tagInput.trim();
    if (val && !allowedValues.includes(val)) {
      setAllowedValues([...allowedValues, val]);
    }
    setTagInput('');
  };

  const removeTag = (val: string) => setAllowedValues(allowedValues.filter((x) => x !== val));

  const inputCls =
    'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-lg max-w-lg w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-gray-900 mb-4">
          {initial ? 'Edit Column' : 'Add Column'}
        </h3>

        <div className="space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Research question"
              className={inputCls}
              autoFocus
            />
            <p className="mt-1 text-xs text-gray-500">
              Short label — becomes the column header in the extraction table.
            </p>
          </div>

          {/* Prompt */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Prompt <span className="text-red-500">*</span>
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="What question should the LLM answer for this column?"
              className={inputCls}
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="Additional context about what this column measures (optional)"
              className={inputCls}
            />
          </div>

          {/* Allowed values */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Allowed Values
            </label>
            <p className="mb-2 text-xs text-gray-500">
              Type a value and press Enter to add it as a chip. Leave empty for free-text
              responses.
            </p>
            {allowedValues.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {allowedValues.map((v) => (
                  <span
                    key={v}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-blue-100 text-blue-800"
                  >
                    {v}
                    <button
                      onClick={() => removeTag(v)}
                      className="text-blue-500 hover:text-blue-700 leading-none"
                      aria-label={`Remove ${v}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addTag();
                  }
                }}
                placeholder="Add a value…"
                className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={addTag}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Add
              </button>
            </div>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!name.trim() || !prompt.trim() || isPending}
            className="px-4 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isPending ? 'Saving…' : initial ? 'Save Changes' : 'Add Column'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Schema editor (title/description + column management + extract/review)
// ---------------------------------------------------------------------------

interface SchemaEditorProps {
  schemaId: number;
  onBack: () => void;
}

type EditorTab = 'schema' | 'review';

function SchemaEditor({ schemaId, onBack }: SchemaEditorProps) {
  const { data: schema, isLoading } = useExtractionSchema(schemaId);

  const [activeTab, setActiveTab] = useState<EditorTab>('schema');

  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const [descDraft, setDescDraft] = useState<string | null>(null);
  const [metaSaved, setMetaSaved] = useState(false);

  const [editColumn, setEditColumn] = useState<ExtractionColumn | null>(null);
  const [addingColumn, setAddingColumn] = useState(false);
  const [deleteColId, setDeleteColId] = useState<number | null>(null);

  const updateSchema = useUpdateExtractionSchema();
  const deleteCol = useDeleteExtractionColumn(schemaId);
  const reorder = useReorderExtractionColumns(schemaId);

  if (isLoading || !schema) {
    return <div className="p-6 text-sm text-gray-400">Loading schema…</div>;
  }

  const sortedColumns = [...schema.columns].sort((a, b) => a.sort_order - b.sort_order);

  const titleValue = titleDraft ?? schema.title;
  const descValue = descDraft ?? (schema.description ?? '');
  const hasMetaChanges =
    (titleDraft !== null && titleDraft !== schema.title) ||
    (descDraft !== null && descDraft !== (schema.description ?? ''));

  const handleSaveMeta = async () => {
    if (!titleValue.trim()) return;
    await updateSchema.mutateAsync({
      schemaId,
      data: {
        title: titleValue.trim(),
        description: descValue.trim() || null,
      },
    });
    setTitleDraft(null);
    setDescDraft(null);
    setMetaSaved(true);
    setTimeout(() => setMetaSaved(false), 2000);
  };

  const handleMoveUp = (idx: number) => {
    if (idx === 0) return;
    const ids = sortedColumns.map((c) => c.id);
    [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
    reorder.mutate(ids);
  };

  const handleMoveDown = (idx: number) => {
    if (idx === sortedColumns.length - 1) return;
    const ids = sortedColumns.map((c) => c.id);
    [ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]];
    reorder.mutate(ids);
  };

  const nextSortOrder = sortedColumns.length;

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0">
      <PageHeader title="Edit Extraction Schema">
        <button
          onClick={onBack}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
        >
          ← Back to schemas
        </button>
      </PageHeader>

      {/* Tab bar */}
      <div className="shrink-0 border-b border-gray-200 px-6 flex gap-0">
        {(['schema', 'review'] as EditorTab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            {tab === 'schema' ? 'Schema' : 'Extract & Review'}
          </button>
        ))}
      </div>

      {activeTab === 'schema' ? (
        <div className="p-6 max-w-2xl space-y-6 overflow-y-auto">
          {/* Metadata */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Title <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={titleValue}
                onChange={(e) => setTitleDraft(e.target.value)}
                placeholder="e.g. Study Design Analysis"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
              <textarea
                value={descValue}
                onChange={(e) => setDescDraft(e.target.value)}
                rows={3}
                placeholder="Describe the goal of this extraction schema (optional — sent to the LLM as context)"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-gray-500">
                This description is included in the LLM prompt as additional context.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={handleSaveMeta}
                disabled={!titleValue.trim() || !hasMetaChanges || updateSchema.isPending}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {updateSchema.isPending ? 'Saving…' : 'Save'}
              </button>
              {metaSaved && <span className="text-sm text-green-600">Saved</span>}
            </div>
          </div>

          {/* Columns */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
                Columns
                {sortedColumns.length > 0 && (
                  <span className="ml-1 font-normal text-gray-400">
                    ({sortedColumns.length})
                  </span>
                )}
              </h2>
              <button
                onClick={() => setAddingColumn(true)}
                className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
              >
                + Add Column
              </button>
            </div>

            {sortedColumns.length === 0 ? (
              <div className="border border-dashed border-gray-300 rounded-lg p-8 text-center text-sm text-gray-400">
                No columns yet. Add a column to define what the LLM should extract.
              </div>
            ) : (
              <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
                {sortedColumns.map((col, idx) => (
                  <div key={col.id} className="flex items-start gap-3 px-4 py-3">
                    {/* Reorder buttons */}
                    <div className="flex flex-col gap-0.5 shrink-0 pt-0.5">
                      <button
                        onClick={() => handleMoveUp(idx)}
                        disabled={idx === 0 || reorder.isPending}
                        className="p-0.5 text-gray-400 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label="Move up"
                      >
                        ▲
                      </button>
                      <button
                        onClick={() => handleMoveDown(idx)}
                        disabled={idx === sortedColumns.length - 1 || reorder.isPending}
                        className="p-0.5 text-gray-400 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label="Move down"
                      >
                        ▼
                      </button>
                    </div>

                    {/* Column info */}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900">{col.name}</p>
                      <p className="text-xs text-gray-500 truncate mt-0.5">
                        {col.prompt.length > 80 ? col.prompt.slice(0, 80) + '…' : col.prompt}
                      </p>
                      {col.allowed_values && col.allowed_values.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {col.allowed_values.map((v) => (
                            <span
                              key={v}
                              className="px-1.5 py-0.5 text-xs rounded bg-gray-100 text-gray-600"
                            >
                              {v}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={() => setEditColumn(col)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => setDeleteColId(col.id)}
                        className="text-xs text-red-500 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <ExtractionRunView schema={schema} />
      )}

      {/* Column form modal */}
      {(addingColumn || editColumn) && (
        <ColumnFormModal
          schemaId={schemaId}
          initial={editColumn ?? undefined}
          nextSortOrder={nextSortOrder}
          onClose={() => {
            setAddingColumn(false);
            setEditColumn(null);
          }}
        />
      )}

      {/* Delete column confirm */}
      {deleteColId != null && (
        <ConfirmDialog
          title="Delete column?"
          message="This will permanently delete the column and all its extraction results."
          confirmLabel="Delete"
          onConfirm={() => {
            deleteCol.mutate(deleteColId);
            setDeleteColId(null);
          }}
          onCancel={() => setDeleteColId(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// New schema form
// ---------------------------------------------------------------------------

interface NewSchemaFormProps {
  projectId: number;
  onCreated: (schemaId: number) => void;
  onCancel: () => void;
}

function NewSchemaForm({ projectId, onCreated, onCancel }: NewSchemaFormProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const createSchema = useCreateExtractionSchema();

  const handleCreate = async () => {
    if (!title.trim()) return;
    const schema = await createSchema.mutateAsync({
      title: title.trim(),
      description: description.trim() || null,
      project_id: projectId,
    });
    onCreated(schema.id);
  };

  const inputCls =
    'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <PageHeader title="New Extraction Schema">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
        >
          Cancel
        </button>
      </PageHeader>

      <div className="p-6 max-w-2xl space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Title <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Study Design Analysis"
            className={inputCls}
            autoFocus
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Describe the goal of this extraction schema (optional — sent to the LLM as context)"
            className={inputCls}
          />
          <p className="mt-1 text-xs text-gray-500">
            This description is included in the LLM prompt as additional context.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleCreate}
            disabled={!title.trim() || createSchema.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {createSchema.isPending ? 'Creating…' : 'Create Schema'}
          </button>
          <button
            onClick={onCancel}
            className="px-3 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
        {createSchema.error && (
          <p className="text-sm text-red-600">
            {createSchema.error instanceof Error
              ? createSchema.error.message
              : 'Failed to create schema'}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Schema list card
// ---------------------------------------------------------------------------

interface SchemaCardProps {
  schema: ExtractionSchema;
  onEdit: () => void;
  onDelete: () => void;
}

function SchemaCard({ schema, onEdit, onDelete }: SchemaCardProps) {
  const created = new Date(schema.created_at).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });

  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900">{schema.title}</h3>
          {schema.description && (
            <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{schema.description}</p>
          )}
          <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
            <span>
              {schema.columns.length} column{schema.columns.length !== 1 ? 's' : ''}
            </span>
            <span>Created {created}</span>
          </div>
          {schema.columns.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {schema.columns
                .slice()
                .sort((a, b) => a.sort_order - b.sort_order)
                .map((col) => (
                  <span
                    key={col.id}
                    className="px-2 py-0.5 text-xs rounded bg-gray-100 text-gray-600"
                  >
                    {col.name}
                  </span>
                ))}
            </div>
          )}
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={onEdit}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="px-3 py-1.5 text-sm text-red-600 border border-red-200 rounded hover:bg-red-50"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type View = { kind: 'list' } | { kind: 'new' } | { kind: 'editor'; schemaId: number };

export default function ExtractionSchemasPage() {
  const { projectId: pid } = useParams<{ projectId: string }>();
  const projectId = Number(pid);
  const navigate = useNavigate();

  const [view, setView] = useState<View>({ kind: 'list' });
  const [deleteSchemaId, setDeleteSchemaId] = useState<number | null>(null);

  const { data: schemas, isLoading } = useExtractionSchemas(projectId);
  const deleteSchema = useDeleteExtractionSchema();

  if (view.kind === 'new') {
    return (
      <NewSchemaForm
        projectId={projectId}
        onCreated={(id) => setView({ kind: 'editor', schemaId: id })}
        onCancel={() => setView({ kind: 'list' })}
      />
    );
  }

  if (view.kind === 'editor') {
    return (
      <SchemaEditor
        schemaId={view.schemaId}
        onBack={() => setView({ kind: 'list' })}
      />
    );
  }

  // List view
  return (
    <div className="flex-1">
      <PageHeader title="Extraction Schemas">
        <button
          onClick={() => navigate(`/projects/${projectId}`)}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
        >
          ← Back to project
        </button>
        <button
          onClick={() => setView({ kind: 'new' })}
          className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
        >
          New Table Schema
        </button>
      </PageHeader>

      <div className="p-6 max-w-2xl space-y-3">
        {isLoading && <p className="text-sm text-gray-400">Loading schemas…</p>}

        {!isLoading && schemas?.length === 0 && (
          <div className="border border-dashed border-gray-300 rounded-lg p-12 text-center">
            <p className="text-sm text-gray-500 mb-3">No extraction schemas yet.</p>
            <p className="text-xs text-gray-400 mb-4">
              Create a schema to define what structured information the LLM should extract from
              papers in this project.
            </p>
            <button
              onClick={() => setView({ kind: 'new' })}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
            >
              New Table Schema
            </button>
          </div>
        )}

        {schemas?.map((schema) => (
          <SchemaCard
            key={schema.id}
            schema={schema}
            onEdit={() => setView({ kind: 'editor', schemaId: schema.id })}
            onDelete={() => setDeleteSchemaId(schema.id)}
          />
        ))}
      </div>

      {deleteSchemaId != null && (
        <ConfirmDialog
          title="Delete schema?"
          message="This will permanently delete the schema and all its columns. Extraction notes generated from this schema will remain."
          confirmLabel="Delete"
          onConfirm={() => {
            deleteSchema.mutate(deleteSchemaId);
            setDeleteSchemaId(null);
          }}
          onCancel={() => setDeleteSchemaId(null)}
        />
      )}
    </div>
  );
}
