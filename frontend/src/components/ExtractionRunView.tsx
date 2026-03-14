import { useState, useMemo, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import ConfirmDialog from './ConfirmDialog';
import type { WorkPDFOut } from '../types';
import {
  useExtractionResults,
  useRunBatchExtraction,
  useRunSingleExtraction,
  useExtractionJob,
  useAcceptExtractionNote,
  useAcceptExtractionProposal,
  useEditExtractionNote,
  useManualFillExtractionCell,
  useDismissExtractionProposal,
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
        : provenance === 'ai_proposal'
          ? 'bg-amber-100 text-amber-700'
          : 'bg-blue-100 text-blue-700';
  const label =
    provenance === 'ai_reviewed'
      ? 'reviewed'
      : provenance === 'user'
        ? 'user'
        : provenance === 'ai_proposal'
          ? 'proposal'
          : 'ai';
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
  noText?: boolean;
  onExtractSingle: (workId: number) => void;
}

function ExtractionCell({
  cell,
  workId,
  schemaId,
  column,
  isRunningGlobal,
  noText = false,
  onExtractSingle,
}: ExtractionCellProps) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [showReasoning, setShowReasoning] = useState(false);
  const [showProposal, setShowProposal] = useState(false);

  const accept = useAcceptExtractionNote();
  const acceptProposal = useAcceptExtractionProposal(schemaId);
  const editNote = useEditExtractionNote();
  const manualFill = useManualFillExtractionCell(schemaId);
  const dismissProposal = useDismissExtractionProposal(schemaId);

  const hasAllowedValues = column.allowed_values && column.allowed_values.length > 0;

  // Shared inline editor (used for both "pencil on empty" and "Edit proposal")
  if (editing) {
    const isManual = !cell; // if cell is null, this is a manual fill on empty cell

    const handleSave = () => {
      if (isManual) {
        manualFill.mutate(
          { columnId: column.id, workId, content: editValue },
          { onSuccess: () => setEditing(false) },
        );
      } else {
        editNote.mutate({ workId, noteId: cell!.answer_note.id, content: editValue });
        setEditing(false);
      }
    };

    const isSaving = manualFill.isPending || editNote.isPending;

    return (
      <td className="px-3 py-2 align-top border-r border-gray-100 last:border-r-0 min-w-[180px]">
        {hasAllowedValues ? (
          <select
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            autoFocus
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
            onKeyDown={(e) => {
              if (e.key === 'Escape') setEditing(false);
            }}
            rows={3}
            autoFocus
            className="w-full text-xs border border-gray-300 rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          />
        )}
        <div className="flex gap-1.5 mt-1.5">
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="px-2 py-0.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {isSaving ? '…' : 'Save'}
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

  // Empty cell: show sparkle + pencil icons
  if (!cell) {
    return (
      <td className="px-3 py-2 text-center align-top border-r border-gray-100 last:border-r-0">
        {!noText && (
          <div className="flex items-center justify-center gap-2">
            <button
              onClick={() => onExtractSingle(workId)}
              disabled={isRunningGlobal}
              title="Extract with LLM"
              className="text-xs text-gray-400 hover:text-blue-600 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              ✦
            </button>
            <button
              onClick={() => {
                setEditValue(hasAllowedValues ? column.allowed_values![0] : '');
                setEditing(true);
              }}
              disabled={isRunningGlobal}
              title="Fill manually"
              className="text-xs text-gray-400 hover:text-green-600 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              ✎
            </button>
          </div>
        )}
      </td>
    );
  }

  const { answer_note, reasoning_note, proposal } = cell;

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
        {proposal && (
          <button
            onClick={() => setShowProposal((v) => !v)}
            title="AI suggestion pending review"
            className="text-[10px] text-amber-600 hover:text-amber-800 leading-none font-medium"
          >
            💡
          </button>
        )}
      </div>
      {showReasoning && reasoning_note && (
        <div className="mt-1.5 p-2 bg-gray-50 rounded text-[11px] text-gray-600 leading-snug border border-gray-200">
          {reasoning_note.content}
        </div>
      )}
      {showProposal && proposal && (
        <div className="mt-1.5 p-2 bg-amber-50 rounded border border-amber-200 text-[11px] text-gray-700 leading-snug space-y-2">
          <p className="font-medium text-amber-700 text-[10px] uppercase tracking-wide">AI suggestion</p>
          <p className="text-xs">{proposal.content}</p>
          <div className="flex gap-1.5 flex-wrap">
            <button
              onClick={() => {
                acceptProposal.mutate(
                  {
                    workId,
                    answerNoteId: answer_note.id,
                    columnId: column.id,
                    proposalContent: proposal.content,
                  },
                  { onSuccess: () => setShowProposal(false) },
                );
              }}
              disabled={acceptProposal.isPending}
              className="px-2 py-0.5 text-[11px] text-white bg-green-600 rounded hover:bg-green-700 disabled:opacity-50"
            >
              {acceptProposal.isPending ? '…' : 'Accept'}
            </button>
            <button
              onClick={() => {
                setEditValue(proposal.content);
                setShowProposal(false);
                setEditing(true);
              }}
              className="px-2 py-0.5 text-[11px] border border-gray-300 rounded hover:bg-gray-50"
            >
              Edit
            </button>
            <button
              onClick={() => {
                dismissProposal.mutate(
                  { columnId: column.id, workId },
                  { onSuccess: () => setShowProposal(false) },
                );
              }}
              disabled={dismissProposal.isPending}
              className="px-2 py-0.5 text-[11px] text-red-600 border border-red-200 rounded hover:bg-red-50 disabled:opacity-50"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
    </td>
  );
}

// ---------------------------------------------------------------------------
// Extraction run & review panel
// ---------------------------------------------------------------------------

export interface ExtractionRunViewProps {
  schema: ExtractionSchema;
  /**
   * When true, hides paper selection controls (checkboxes, bulk-select,
   * extract button) and shows a read-only view of results. Cell interactions
   * (accept, edit, manual fill, proposals) still work normally.
   */
  readOnlyPaperSelection?: boolean;
}

export default function ExtractionRunView({ schema, readOnlyPaperSelection = false }: ExtractionRunViewProps) {
  const projectId = schema.project_id;
  const lsKey = `litexplorer:project:${projectId}:schema:${schema.id}:selection`;

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

  // PDF availability: track which seeds have extraction_status='ready' PDF
  const [seedPdfsLoaded, setSeedPdfsLoaded] = useState(false);
  const [seedsWithText, setSeedsWithText] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (seeds.length === 0) return;
    setSeedPdfsLoaded(false);
    Promise.all(
      seeds.map((s) =>
        fetch(`/api/works/${s.id}/pdfs`)
          .then((r) => (r.ok ? r.json() : []))
          .then((pdfs: WorkPDFOut[]) => ({
            id: s.id,
            hasText: pdfs.some((p) => p.extraction_status === 'ready'),
          }))
          .catch(() => ({ id: s.id, hasText: false })),
      ),
    ).then((results) => {
      const withText = new Set(results.filter((r) => r.hasText).map((r) => r.id));
      setSeedsWithText(withText);
      setSeedPdfsLoaded(true);
    });
  }, [seeds.length]); // re-run only when seed count changes

  // Work selection state
  const [searchQ, setSearchQ] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const initializedRef = useRef(false);

  // Restore selection from localStorage, or default to seeds-with-text on first visit
  useEffect(() => {
    if (!initializedRef.current && seedPdfsLoaded && seeds.length > 0) {
      const allSeedIdSet = new Set(seeds.map((s) => s.id));
      const stored = localStorage.getItem(lsKey);
      if (stored) {
        try {
          const storedIds = JSON.parse(stored) as number[];
          setSelectedIds(new Set(storedIds.filter((id) => allSeedIdSet.has(id))));
        } catch {
          setSelectedIds(new Set(seeds.filter((s) => seedsWithText.has(s.id)).map((s) => s.id)));
        }
      } else {
        setSelectedIds(new Set(seeds.filter((s) => seedsWithText.has(s.id)).map((s) => s.id)));
      }
      initializedRef.current = true;
    }
  }, [seedPdfsLoaded, seeds, seedsWithText, lsKey]);

  // Persist selection to localStorage after initialization
  useEffect(() => {
    if (!initializedRef.current) return;
    localStorage.setItem(lsKey, JSON.stringify(Array.from(selectedIds)));
  }, [selectedIds, lsKey]);

  const filteredSeeds = useMemo(() => {
    if (!searchQ.trim()) return seeds;
    const q = searchQ.toLowerCase();
    return seeds.filter((s) => s.title.toLowerCase().includes(q));
  }, [seeds, searchQ]);

  // Sort: selected float to top, then unselected-with-text, then no-text
  const sortedFilteredSeeds = useMemo(() => {
    if (readOnlyPaperSelection) {
      // In read-only mode, show only the papers the user selected (persisted in localStorage)
      return filteredSeeds.filter((s) => selectedIds.has(s.id));
    }
    const selected = filteredSeeds.filter((s) => selectedIds.has(s.id));
    const unselectedWithText = filteredSeeds.filter(
      (s) => !selectedIds.has(s.id) && seedsWithText.has(s.id),
    );
    const noText = filteredSeeds.filter((s) => !seedsWithText.has(s.id));
    return [...selected, ...unselectedWithText, ...noText];
  }, [filteredSeeds, selectedIds, seedsWithText, readOnlyPaperSelection]);

  // Seeds that have (or will have) extracted text — the "selectable" set
  const selectableIds = useMemo(
    () => seeds.filter((s) => seedsWithText.has(s.id)).map((s) => s.id),
    [seeds, seedsWithText],
  );

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
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [extractProgress, setExtractProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [extractErrors, setExtractErrors] = useState<{ workId: number; msg: string }[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);
  const [reEvaluateEdited, setReEvaluateEdited] = useState(false);

  // Prompt preview state
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  const [promptPreview, setPromptPreview] = useState<{ system_text: string; user_message: string } | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);

  const runBatch = useRunBatchExtraction(schema.id);
  const runSingle = useRunSingleExtraction(schema.id);
  const jobQuery = useExtractionJob(activeJobId);

  // Track job progress via polling
  useEffect(() => {
    const job = jobQuery.data;
    if (!job) return;
    if (job.status === 'running') {
      setExtractProgress({ done: job.progress.completed, total: job.progress.total });
    } else if (job.status === 'completed') {
      setExtractProgress({ done: job.progress.completed, total: job.progress.total });
      const errs = Object.entries(job.works)
        .filter(([, w]) => w.status === 'failed')
        .map(([wid, w]) => ({ workId: Number(wid), msg: w.error ?? 'Extraction failed' }));
      setExtractErrors(errs);
      setIsExtracting(false);
      setActiveJobId(null);
      refetchResults();
    }
  }, [jobQuery.data, refetchResults]);

  const doExtract = async (ids: number[], reEval = false) => {
    setIsExtracting(true);
    setExtractProgress({ done: 0, total: ids.length });
    setExtractErrors([]);
    try {
      const accepted = await runBatch.mutateAsync({ workIds: ids, reEvaluateEdited: reEval });
      setActiveJobId(accepted.job_id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      let detail = msg;
      try { detail = JSON.parse(msg).detail ?? msg; } catch { /* not JSON */ }
      setExtractErrors(ids.map((id) => ({ workId: id, msg: detail })));
      setIsExtracting(false);
    }
  };

  const handleExtractClick = () => {
    if (hasExistingNotes) {
      setShowConfirm(true);
    } else {
      doExtract(Array.from(selectedIds), reEvaluateEdited);
    }
  };

  const handleExtractSingle = async (workId: number) => {
    setIsExtracting(true);
    try {
      const accepted = await runSingle.mutateAsync(workId);
      setActiveJobId(accepted.job_id);
    } catch {
      setIsExtracting(false);
    }
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
    if (!seedsWithText.has(id)) return; // no text — not selectable
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () =>
    setSelectedIds(new Set(filteredSeeds.filter((s) => seedsWithText.has(s.id)).map((s) => s.id)));
  const deselectAll = () => setSelectedIds(new Set());

  // Topic list bulk-select: indeterminate state via refs
  const tlCheckboxRefs = useRef<Map<number, HTMLInputElement>>(new Map());

  useEffect(() => {
    for (const tl of topicLists) {
      const el = tlCheckboxRefs.current.get(tl.id);
      if (!el) continue;
      const tlSeeds = seeds.filter((s) => s.topic_list_ids.includes(tl.id) && seedsWithText.has(s.id));
      const selectedCount = tlSeeds.filter((s) => selectedIds.has(s.id)).length;
      el.indeterminate = selectedCount > 0 && selectedCount < tlSeeds.length;
    }
  }, [selectedIds, topicLists, seeds, seedsWithText]);

  const handleBulkTopicListToggle = (tlId: number) => {
    setSelectedIds((prev) => {
      const tlSeedIds = seeds
        .filter((s) => s.topic_list_ids.includes(tlId) && seedsWithText.has(s.id))
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
        This schema has no columns yet.{' '}
        {readOnlyPaperSelection ? (
          <Link
            to={`/projects/${projectId}/extraction?schema=${schema.id}`}
            className="text-blue-600 hover:underline"
          >
            Edit in full view →
          </Link>
        ) : (
          <>Go to the <strong>Schema</strong> tab to add columns first.</>
        )}
      </div>
    );
  }

  const selectableCount = seeds.filter((s) => seedsWithText.has(s.id)).length;
  const selectedCount = selectedIds.size;
  const isDone =
    !isExtracting && extractProgress !== null && extractProgress.done === extractProgress.total;

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Read-only mode: show export bar with edit link */}
      {readOnlyPaperSelection ? (
        <div className="shrink-0 border-b border-gray-100 bg-gray-50 px-4 py-2 flex items-center gap-3">
          {selectedIds.size > 0 && (
            <>
              <button
                onClick={() => {
                  window.open(
                    `/api/extraction/schemas/${schema.id}/export?format=csv&work_ids=${Array.from(selectedIds).join(',')}`,
                  );
                }}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-700"
                title="Export as CSV"
              >
                Export CSV
              </button>
              <button
                onClick={() => {
                  window.open(
                    `/api/extraction/schemas/${schema.id}/export?format=latex&work_ids=${Array.from(selectedIds).join(',')}`,
                  );
                }}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-700"
                title="Export as LaTeX table"
              >
                Export LaTeX
              </button>
            </>
          )}
          <div className="flex-1" />
          <Link
            to={`/projects/${projectId}/extraction?schema=${schema.id}`}
            className="text-xs text-blue-600 hover:underline font-medium"
          >
            Edit in full view →
          </Link>
        </div>
      ) : (
        /* Full mode: top action bar */
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
            {selectedCount} of {selectableCount} selected
            {seedPdfsLoaded && selectableCount < seeds.length && (
              <span className="ml-1 text-gray-400">
                ({seeds.length - selectableCount} without extracted text)
              </span>
            )}
          </span>
          {/* Export buttons */}
          {allSeedIds.length > 0 && (
            <>
              <button
                onClick={() => {
                  const ids = selectedIds.size > 0 ? Array.from(selectedIds) : selectableIds;
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
                  const ids = selectedIds.size > 0 ? Array.from(selectedIds) : selectableIds;
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
              {activeJobId
                ? `Extracting: ${extractProgress.done}/${extractProgress.total} done`
                : `Queuing ${extractProgress.total} paper${extractProgress.total !== 1 ? 's' : ''}…`}
            </span>
          )}
          {isDone && extractErrors.length === 0 && (
            <span className="text-xs text-green-600">
              ✓ Done — {extractProgress!.done} paper{extractProgress!.done !== 1 ? 's' : ''} extracted
            </span>
          )}
          {isDone && extractErrors.length > 0 && (
            <span className="text-xs text-amber-600">
              Extraction complete — {extractErrors.length} of {extractProgress!.total} failed
            </span>
          )}
          <label
            className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer select-none"
            title="When checked, the LLM will also process cells you've manually edited. Results appear as proposals you can accept or dismiss."
          >
            <input
              type="checkbox"
              checked={reEvaluateEdited}
              onChange={(e) => setReEvaluateEdited(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
            />
            Re-evaluate edited cells
            <span
              className="text-gray-400 cursor-help"
              title="When checked, the LLM will also process cells you've manually edited. Results appear as proposals you can accept or dismiss."
            >
              ⓘ
            </span>
          </label>
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
      )}

      {/* Topic list bulk-select row — hidden in read-only mode */}
      {!readOnlyPaperSelection && topicLists.length > 0 && (
        <div className="shrink-0 border-b border-gray-100 bg-white px-4 py-2 flex flex-wrap gap-x-4 gap-y-1.5 items-center">
          <span className="text-[11px] text-gray-500 font-medium">Topic lists:</span>
          {topicLists.map((tl) => {
            const tlSeeds = seeds.filter((s) => s.topic_list_ids.includes(tl.id) && seedsWithText.has(s.id));
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
              {/* Checkbox column — hidden in read-only mode */}
              {!readOnlyPaperSelection && (
                <th className="w-6 px-3 py-2 text-left border-b border-r border-gray-200">
                  <input
                    type="checkbox"
                    checked={
                      filteredSeeds.some((s) => seedsWithText.has(s.id)) &&
                      filteredSeeds.filter((s) => seedsWithText.has(s.id)).every((s) => selectedIds.has(s.id))
                    }
                    onChange={(e) => (e.target.checked ? selectAll() : deselectAll())}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
                  />
                </th>
              )}
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
            {sortedFilteredSeeds.map((seed) => {
              const hasText = seedsWithText.has(seed.id);
              const noText = seedPdfsLoaded && !hasText;
              return (
              <tr
                key={seed.id}
                className={
                  noText
                    ? 'bg-gray-50 opacity-50'
                    : (readOnlyPaperSelection || selectedIds.has(seed.id))
                    ? 'bg-white'
                    : 'bg-gray-50 opacity-60'
                }
              >
                {/* Checkbox cell — hidden in read-only mode */}
                {!readOnlyPaperSelection && (
                  <td className="px-3 py-2 text-center align-top border-r border-gray-100">
                    <span title={noText ? 'No extracted text — upload and extract a PDF first' : undefined}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(seed.id)}
                        disabled={noText}
                        onChange={() => toggleId(seed.id)}
                        className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer disabled:cursor-not-allowed"
                      />
                    </span>
                  </td>
                )}
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
                    <div className="px-3 py-2 min-w-0 flex-1 flex items-start justify-between gap-2">
                      <p className="text-xs font-medium text-gray-900 line-clamp-2">{seed.title}</p>
                      {noText && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-400 rounded shrink-0 leading-none mt-0.5">
                          No text
                        </span>
                      )}
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
                    noText={noText}
                    onExtractSingle={handleExtractSingle}
                  />
                ))}
              </tr>
              );
            })}
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
            doExtract(Array.from(selectedIds), reEvaluateEdited);
          }}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}
