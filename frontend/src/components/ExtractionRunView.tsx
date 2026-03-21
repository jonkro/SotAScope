import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { useQueries } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import ConfirmDialog from './ConfirmDialog';
import { OnboardingHintSequence } from './OnboardingHint';
import WorkDetailPanel, { DEFAULT_FOLD_STATE, type PanelFoldState } from './WorkDetailPanel';
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
  useSaveExtractionSelection,
} from '../hooks/useExtraction';
import { useTimeline } from '../hooks/useTimeline';
import { getExtractionPromptPreview, fetchWorkPDFs, pasteExtraction } from '../api';
import type { ExtractionColumn, ExtractionSchema, ExtractionCellResult, PasteExtractionResult } from '../types';

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
          : provenance === 'external_ai'
            ? 'bg-sky-100 text-sky-700'
            : 'bg-blue-100 text-blue-700';
  const label =
    provenance === 'ai_reviewed'
      ? 'reviewed'
      : provenance === 'user'
        ? 'user'
        : provenance === 'ai_proposal'
          ? 'proposal'
          : provenance === 'external_ai'
            ? 'ext. ai'
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
  onPasteJson: (workId: number) => void;
  cellHeight?: number;
}

function ExtractionCell({
  cell,
  workId,
  schemaId,
  column,
  isRunningGlobal,
  noText = false,
  onExtractSingle,
  onPasteJson,
  cellHeight,
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
      <td className="px-3 py-2 align-top border-r border-gray-100 last:border-r-0">
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

  // Empty cell: show sparkle (AI) + pencil (manual) + clipboard (paste) icons
  // noText papers: sparkle hidden, pencil and clipboard always enabled
  if (!cell) {
    return (
      <td
        className="px-3 py-2 text-center align-top border-r border-gray-100 last:border-r-0"
        style={cellHeight !== undefined ? { height: cellHeight, overflow: 'hidden' } : undefined}
      >
        <div className="flex items-center justify-center gap-2">
          {!noText && (
            <button
              onClick={() => onExtractSingle(workId)}
              disabled={isRunningGlobal}
              title="Extract with LLM"
              className="text-xs text-gray-400 hover:text-blue-600 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              ✦
            </button>
          )}
          <button
            onClick={() => {
              setEditValue(hasAllowedValues ? column.allowed_values![0] : '');
              setEditing(true);
            }}
            disabled={noText ? false : isRunningGlobal}
            title={noText ? 'Fill manually (upload a PDF to enable AI extraction)' : 'Fill manually'}
            className="text-xs text-gray-400 hover:text-green-600 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            ✎
          </button>
          <button
            onClick={() => onPasteJson(workId)}
            title="Paste extraction JSON"
            className="text-xs text-gray-400 hover:text-sky-600"
          >
            📋
          </button>
        </div>
      </td>
    );
  }

  const { answer_note, reasoning_note, proposal } = cell;

  return (
    <td
      className="px-3 py-2 align-top border-r border-gray-100 last:border-r-0 overflow-hidden"
      style={cellHeight !== undefined ? { height: cellHeight } : undefined}
    >
      <div className="text-xs text-gray-800 leading-snug mb-1 break-words">
        {answer_note.content || <span className="text-gray-400 italic">empty</span>}
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
        {(answer_note.provenance === 'ai' || answer_note.provenance === 'external_ai') && (
          <button
            onClick={() => onPasteJson(workId)}
            title="Paste extraction JSON"
            className="text-[10px] text-sky-500 hover:text-sky-700 leading-none"
          >
            📋
          </button>
        )}
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
// Column resize helpers
// ---------------------------------------------------------------------------

const DEFAULT_COL_WIDTHS: Record<string, number> = { title: 280, year: 80 };
const DEFAULT_EXTRACTION_COL_WIDTH = 180;

function loadColWidths(schemaId: number): Record<string, number> {
  try {
    const raw = localStorage.getItem(`litexplorer:schema:${schemaId}:columnWidths`);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function loadRowHeights(schemaId: number): Record<number, number> {
  try {
    const raw = localStorage.getItem(`litexplorer:schema:${schemaId}:rowHeights`);
    if (!raw) return {};
    const parsed: Record<string, number> = JSON.parse(raw);
    const out: Record<number, number> = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === 'number') out[Number(k)] = v;
    }
    return out;
  } catch {
    return {};
  }
}

// ---------------------------------------------------------------------------
// Paste JSON dialog
// ---------------------------------------------------------------------------

interface PasteJsonDialogProps {
  schema: ExtractionSchema;
  workId: number;
  workTitle: string;
  existingCells: Map<string, ExtractionCellResult>;
  sortedColumns: ExtractionColumn[];
  onClose: () => void;
  onSuccess: () => void;
}

function PasteJsonDialog({
  schema,
  workId,
  workTitle,
  existingCells,
  sortedColumns,
  onClose,
  onSuccess,
}: PasteJsonDialogProps) {
  const [jsonText, setJsonText] = useState('');
  const [preview, setPreview] = useState<{
    filled: string[];
    skipped: string[];
    notFound: string[];
  } | null>(null);
  const [parseError, setParseError] = useState('');
  const [parsed, setParsed] = useState<object | null>(null);
  const [isPasting, setIsPasting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const handlePreview = () => {
    setParseError('');
    setPreview(null);
    setParsed(null);

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(jsonText.trim()) as Record<string, unknown>;
    } catch {
      setParseError('Invalid JSON — check for missing commas or unmatched brackets.');
      return;
    }

    setParsed(data);

    // Unwrap optional "columns" wrapper
    const colData: Record<string, unknown> =
      Object.keys(data).length === 1 &&
      'columns' in data &&
      typeof data['columns'] === 'object' &&
      data['columns'] !== null
        ? (data['columns'] as Record<string, unknown>)
        : data;

    const colNameMap = new Map(sortedColumns.map((c) => [c.name.trim().toLowerCase(), c]));

    const filled: string[] = [];
    const skipped: string[] = [];
    const notFound: string[] = [];

    for (const rawName of Object.keys(colData)) {
      const col = colNameMap.get(rawName.trim().toLowerCase());
      if (!col) {
        notFound.push(rawName);
        continue;
      }
      const existing = existingCells.get(`${workId}:${col.id}`);
      if (
        existing &&
        (existing.answer_note.provenance === 'user' ||
          existing.answer_note.provenance === 'ai_reviewed')
      ) {
        skipped.push(col.name);
      } else {
        filled.push(col.name);
      }
    }

    setPreview({ filled, skipped, notFound });
  };

  const handleConfirm = async () => {
    if (!parsed) return;
    setIsPasting(true);
    try {
      const result: PasteExtractionResult = await pasteExtraction(schema.id, workId, parsed);
      const parts = [`${result.filled.length} filled`];
      if (result.skipped.length) parts.push(`${result.skipped.length} skipped`);
      if (result.not_found.length) parts.push(`${result.not_found.length} not found`);
      setSuccessMsg(`Done: ${parts.join(', ')}`);
      onSuccess();
      setTimeout(onClose, 1500);
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'Paste failed');
    } finally {
      setIsPasting(false);
    }
  };

  const canConfirm =
    !!parsed && !isPasting && !successMsg && (preview === null || preview.filled.length > 0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between shrink-0">
          <div>
            <h3 className="text-base font-semibold text-gray-900">Paste extraction JSON</h3>
            <p className="text-xs text-gray-500 mt-0.5 truncate max-w-lg" title={workTitle}>
              {workTitle}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-lg leading-none ml-4"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <p className="text-xs text-gray-500">
            Paste the JSON returned by an external LLM. Accepted formats:
            {' '}
            <code className="bg-gray-100 px-1 rounded font-mono">
              {'{"columns": {"Col": {"answer": "...", "reasoning": "..."}}}'}
            </code>
            {' '}or flat{' '}
            <code className="bg-gray-100 px-1 rounded font-mono">
              {'{"Col": {"answer": "..."}}'}
            </code>.
            Column names are matched case-insensitively. Cells with user or reviewed values are
            not overwritten.
          </p>

          <textarea
            value={jsonText}
            onChange={(e) => {
              setJsonText(e.target.value);
              setPreview(null);
              setParseError('');
              setParsed(null);
            }}
            rows={10}
            placeholder={'{\n  "columns": {\n    "Column Name": {\n      "answer": "...",\n      "reasoning": "..."\n    }\n  }\n}'}
            className="w-full text-xs font-mono border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-y"
          />

          {parseError && <p className="text-xs text-red-600">{parseError}</p>}

          {preview && (
            <div className="space-y-1.5 text-xs border border-gray-200 rounded p-3 bg-gray-50">
              {preview.filled.length > 0 && (
                <div>
                  <span className="font-medium text-green-700">
                    Will fill ({preview.filled.length}):
                  </span>{' '}
                  <span className="text-gray-700">{preview.filled.join(', ')}</span>
                </div>
              )}
              {preview.skipped.length > 0 && (
                <div>
                  <span className="font-medium text-amber-700">
                    Skipped — has user/reviewed value ({preview.skipped.length}):
                  </span>{' '}
                  <span className="text-gray-700">{preview.skipped.join(', ')}</span>
                </div>
              )}
              {preview.notFound.length > 0 && (
                <div>
                  <span className="font-medium text-red-600">
                    Not matched to any column ({preview.notFound.length}):
                  </span>{' '}
                  <span className="text-gray-700">{preview.notFound.join(', ')}</span>
                </div>
              )}
              {preview.filled.length === 0 && preview.skipped.length === 0 && (
                <p className="text-gray-500 italic">
                  No column names matched the schema. Check that JSON keys match column names.
                </p>
              )}
            </div>
          )}

          {successMsg && <p className="text-xs text-green-700 font-medium">{successMsg}</p>}
        </div>

        <div className="px-5 py-4 border-t border-gray-200 flex justify-end gap-2 shrink-0">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handlePreview}
            disabled={!jsonText.trim()}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Preview
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isPasting ? 'Pasting…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
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

export default function ExtractionRunView({
  schema,
  readOnlyPaperSelection = false,
}: ExtractionRunViewProps) {
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

  // PDF availability via React Query — auto-updates when PDFs are uploaded/deleted
  const pdfQueriesResult = useQueries({
    queries: seeds.map((s) => ({
      queryKey: ['works', s.id, 'pdfs'] as const,
      queryFn: () => fetchWorkPDFs(s.id),
      staleTime: 60_000,
    })),
  });

  const seedPdfsLoaded =
    seeds.length === 0 || pdfQueriesResult.every((q) => q.isFetched || q.isError);

  // Stable key: changes only when any PDF query fetches new data
  const pdfDataKey = pdfQueriesResult.map((q) => q.dataUpdatedAt).join(',');

  const seedsWithText = useMemo(() => {
    const set = new Set<number>();
    pdfQueriesResult.forEach((q, i) => {
      const pdfs: WorkPDFOut[] = q.data ?? [];
      if (pdfs.some((p) => p.extraction_status === 'ready')) {
        set.add(seeds[i].id);
      }
    });
    return set;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pdfDataKey, seeds]);

  // Paper detail side panel
  const [panelWorkId, setPanelWorkId] = useState<number | null>(null);
  const [panelFoldState, setPanelFoldState] = useState<PanelFoldState>(DEFAULT_FOLD_STATE);

  // Refs for onboarding hints
  const showPromptBtnRef = useRef<HTMLButtonElement>(null);
  const extractBtnRef = useRef<HTMLButtonElement>(null);

  // Work selection state
  const [searchQ, setSearchQ] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isDirty, setIsDirty] = useState(false);
  const initializedRef = useRef(false);

  // Initialize selection from DB (schema.selected_work_ids) or default to seeds-with-text
  useEffect(() => {
    if (!initializedRef.current && seedPdfsLoaded && seeds.length > 0) {
      const allSeedIdSet = new Set(seeds.map((s) => s.id));
      if (schema.selected_work_ids !== null && schema.selected_work_ids !== undefined) {
        // Restore from DB, filtering to valid seed IDs
        setSelectedIds(new Set(schema.selected_work_ids.filter((id) => allSeedIdSet.has(id))));
      } else {
        // No saved selection — default to seeds with extracted text
        setSelectedIds(new Set(seeds.filter((s) => seedsWithText.has(s.id)).map((s) => s.id)));
      }
      setIsDirty(false);
      initializedRef.current = true;
    }
  }, [seedPdfsLoaded, seeds, seedsWithText, schema.selected_work_ids]);

  // Save selection mutation
  const saveSelection = useSaveExtractionSelection(schema.id);

  const handleSaveSelection = useCallback(async () => {
    await saveSelection.mutateAsync(Array.from(selectedIds));
    setIsDirty(false);
  }, [saveSelection, selectedIds]);

  const filteredSeeds = useMemo(() => {
    if (!searchQ.trim()) return seeds;
    const q = searchQ.toLowerCase();
    return seeds.filter((s) => s.title.toLowerCase().includes(q));
  }, [seeds, searchQ]);

  // Sort: selected float to top, then unselected-with-text, then no-text
  const sortedFilteredSeeds = useMemo(() => {
    if (readOnlyPaperSelection) {
      // In read-only mode, show only the papers the user selected (saved in DB)
      return filteredSeeds.filter((s) => selectedIds.has(s.id));
    }
    const selected = filteredSeeds.filter((s) => selectedIds.has(s.id));
    const unselectedWithText = filteredSeeds.filter(
      (s) => !selectedIds.has(s.id) && seedsWithText.has(s.id),
    );
    const noText = filteredSeeds.filter((s) => !selectedIds.has(s.id) && !seedsWithText.has(s.id));
    return [...selected, ...unselectedWithText, ...noText];
  }, [filteredSeeds, selectedIds, seedsWithText, readOnlyPaperSelection]);

  // Seeds with extracted text — used for export fallback and extract count
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

  // Paste JSON dialog state
  const [pasteTarget, setPasteTarget] = useState<{ workId: number; title: string } | null>(null);

  const handlePasteJson = useCallback((workId: number) => {
    const seed = seeds.find((s) => s.id === workId);
    if (!seed) return;
    setPasteTarget({ workId, title: seed.title });
  }, [seeds]);

  // Prompt preview state
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  const [promptPreview, setPromptPreview] = useState<{ system_text: string; user_message: string } | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);

  // Subset of selected papers that actually have extracted text — these are the ones AI can process
  const extractableSelectedIds = useMemo(
    () => Array.from(selectedIds).filter((id) => seedsWithText.has(id)),
    [selectedIds, seedsWithText],
  );

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
      doExtract(extractableSelectedIds, reEvaluateEdited);
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

  const markDirty = useCallback(() => {
    if (initializedRef.current) setIsDirty(true);
  }, []);

  const toggleId = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    markDirty();
  };

  const selectAll = () => {
    setSelectedIds(new Set(filteredSeeds.map((s) => s.id)));
    markDirty();
  };
  const deselectAll = () => {
    setSelectedIds(new Set());
    markDirty();
  };

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
    markDirty();
  };

  // ---- Column resize (Part 1) ----
  const [colWidths, setColWidths] = useState<Record<string, number>>(() =>
    loadColWidths(schema.id),
  );

  const getColW = (key: string): number =>
    colWidths[key] ?? DEFAULT_COL_WIDTHS[key] ?? DEFAULT_EXTRACTION_COL_WIDTH;

  const handleColResizeStart = useCallback(
    (e: React.PointerEvent<Element>, key: string, currentWidth: number) => {
      e.preventDefault();
      // Use document-level listeners rather than setPointerCapture on the handle
      // element. setPointerCapture is unreliable for elements inside position:sticky
      // containers in Safari, and inside overflow:hidden containers in Chrome —
      // attaching to document bypasses both issues.

      const startX = e.clientX;
      const startWidth = currentWidth;
      let latestWidth = startWidth;

      document.body.style.cursor = 'col-resize';

      const onMove = (ev: PointerEvent) => {
        latestWidth = Math.max(60, startWidth + ev.clientX - startX);
        setColWidths((prev) => ({ ...prev, [key]: latestWidth }));
      };

      const onEnd = () => {
        setColWidths((prev) => {
          const next = { ...prev, [key]: latestWidth };
          try {
            localStorage.setItem(
              `litexplorer:schema:${schema.id}:columnWidths`,
              JSON.stringify(next),
            );
          } catch { /* quota exceeded */ }
          return next;
        });
        document.body.style.cursor = '';
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onEnd);
        document.removeEventListener('pointercancel', onEnd);
      };

      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onEnd);
      document.addEventListener('pointercancel', onEnd);
    },
    [schema.id],
  );

  // ---- Row resize ----
  const [rowHeights, setRowHeights] = useState<Record<number, number>>(() =>
    loadRowHeights(schema.id),
  );
  const trRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());

  const handleRowResizeStart = useCallback(
    (e: React.PointerEvent<HTMLDivElement>, workId: number, currentHeight: number) => {
      e.preventDefault();
      // Same document-level listener pattern as handleColResizeStart —
      // pointer capture on the handle element is clipped by overflow:hidden on
      // the parent <td> in Chrome, causing pointermove events to stop firing.

      const startY = e.clientY;
      const startHeight = currentHeight;
      let latestHeight = startHeight;

      document.body.style.cursor = 'row-resize';

      const onMove = (ev: PointerEvent) => {
        latestHeight = Math.max(32, startHeight + ev.clientY - startY);
        setRowHeights((prev) => ({ ...prev, [workId]: latestHeight }));
      };

      const onEnd = () => {
        setRowHeights((prev) => {
          const next = { ...prev, [workId]: latestHeight };
          try {
            localStorage.setItem(
              `litexplorer:schema:${schema.id}:rowHeights`,
              JSON.stringify(next),
            );
          } catch { /* quota exceeded */ }
          return next;
        });
        document.body.style.cursor = '';
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onEnd);
        document.removeEventListener('pointercancel', onEnd);
      };

      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onEnd);
      document.addEventListener('pointercancel', onEnd);
    },
    [schema.id],
  );

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
            Edit →
          </Link>
        ) : (
          <>Go to the <strong>Schema</strong> tab to add columns first.</>
        )}
      </div>
    );
  }

  const selectableCount = seeds.filter((s) => seedsWithText.has(s.id)).length;
  const selectedCount = selectedIds.size;
  const extractableCount = extractableSelectedIds.length;
  const isDone =
    !isExtracting && extractProgress !== null && extractProgress.done === extractProgress.total;

  return (
    <div className="flex-1 flex min-h-0">
    <div className="flex-1 flex flex-col min-h-0 min-w-0">
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
            Edit →
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
            {selectedCount} of {seeds.length} selected
            {seedPdfsLoaded && selectableCount < seeds.length && (
              <span className="ml-1 text-gray-400">
                ({selectableCount} with text)
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
            ref={showPromptBtnRef}
            onClick={handleShowPrompt}
            disabled={seeds.length === 0 || isExtracting}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Preview the prompt that will be sent to the LLM (paper text replaced with placeholder)"
          >
            Show prompt
          </button>
          <button
            ref={extractBtnRef}
            onClick={handleExtractClick}
            disabled={extractableCount === 0 || isExtracting}
            className="px-3 py-1.5 text-sm border border-indigo-300 text-indigo-700 rounded hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isExtracting
              ? 'Extracting…'
              : extractableCount < selectedCount
                ? `Extract ${extractableCount} of ${selectedCount} paper${selectedCount !== 1 ? 's' : ''} →`
                : `Extract ${selectedCount} paper${selectedCount !== 1 ? 's' : ''} →`}
          </button>
          <div className="w-px h-5 bg-gray-200 shrink-0" />
          <button
            onClick={handleSaveSelection}
            disabled={!isDirty || saveSelection.isPending}
            className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Save paper selection for all users"
          >
            {saveSelection.isPending ? 'Saving…' : isDirty ? 'Save •' : 'Save'}
          </button>
        </div>
      )}

      {/* Topic list bulk-select row — hidden in read-only mode */}
      {!readOnlyPaperSelection && topicLists.length > 0 && (
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
        <table className="text-sm border-collapse" style={{ tableLayout: 'fixed' }}>
          <colgroup>
            {!readOnlyPaperSelection && <col style={{ width: 40 }} />}
            <col style={{ width: getColW('title') }} />
            <col style={{ width: getColW('year') }} />
            {sortedColumns.map((col) => (
              <col key={col.id} style={{ width: getColW(`col_${col.id}`) }} />
            ))}
          </colgroup>
          <thead className="bg-gray-50">
            <tr>
              {/* Checkbox column — hidden in read-only mode */}
              {!readOnlyPaperSelection && (
                <th className="px-3 py-2 text-left border-b border-r border-gray-200 sticky top-0 bg-gray-50 z-10">
                  <input
                    type="checkbox"
                    checked={filteredSeeds.length > 0 && filteredSeeds.every((s) => selectedIds.has(s.id))}
                    onChange={(e) => (e.target.checked ? selectAll() : deselectAll())}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
                  />
                </th>
              )}
              {/* Safari WebKit bug: pointer events do not fire inside a sticky <thead>
                  section element. Fix: move sticky positioning from <thead> to
                  individual <th> cells — Safari handles sticky <th>/<td> correctly
                  (same reason the row resize handle inside sticky <td> works fine).
                  The th-level onPointerDown fallback with edge detection provides
                  defense-in-depth; stopPropagation on handle divs prevents Chrome
                  from double-firing when the handle captures the event first. */}
              <th
                className="px-3 py-2 text-left text-xs font-semibold text-gray-700 border-b border-r border-gray-200 sticky top-0 left-0 bg-gray-50 z-20"
                onPointerDown={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  if (r.right - e.clientX <= 8) handleColResizeStart(e, 'title', getColW('title'));
                }}
              >
                {/* -mx-3 -my-2 px-3 py-2: expands wrapper to cover the full cell box
                    (content + padding), so right-0 on the handle aligns with the
                    cell's right border. */}
                <div className="relative -mx-3 -my-2 px-3 py-2">
                  <span className="block pr-2 truncate">Paper</span>
                  <div
                    className="absolute inset-y-0 right-0 w-2 cursor-col-resize hover:bg-indigo-200/60 select-none touch-none"
                    onPointerDown={(e) => { e.stopPropagation(); handleColResizeStart(e, 'title', getColW('title')); }}
                  />
                </div>
              </th>
              <th
                className="px-3 py-2 text-left text-xs font-semibold text-gray-700 border-b border-r border-gray-200 sticky top-0 bg-gray-50 z-10"
                onPointerDown={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  if (r.right - e.clientX <= 8) handleColResizeStart(e, 'year', getColW('year'));
                }}
              >
                <div className="relative -mx-3 -my-2 px-3 py-2">
                  <span className="block pr-2 truncate">Year</span>
                  <div
                    className="absolute inset-y-0 right-0 w-2 cursor-col-resize hover:bg-indigo-200/60 select-none touch-none"
                    onPointerDown={(e) => { e.stopPropagation(); handleColResizeStart(e, 'year', getColW('year')); }}
                  />
                </div>
              </th>
              {sortedColumns.map((col) => (
                <th
                  key={col.id}
                  className="px-3 py-2 text-left text-xs font-semibold text-gray-700 border-b border-r border-gray-200 last:border-r-0 sticky top-0 bg-gray-50 z-10"
                  title={col.prompt}
                  onPointerDown={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    if (r.right - e.clientX <= 8) handleColResizeStart(e, `col_${col.id}`, getColW(`col_${col.id}`));
                  }}
                >
                  <div className="relative -mx-3 -my-2 px-3 py-2">
                    <span className="block pr-2 truncate">
                      {col.name}
                      {col.allowed_values && col.allowed_values.length > 0 && (
                        <span className="ml-1 font-normal text-gray-400">
                          ({col.allowed_values.length})
                        </span>
                      )}
                    </span>
                    <div
                      className="absolute inset-y-0 right-0 w-2 cursor-col-resize hover:bg-indigo-200/60 select-none touch-none"
                      onPointerDown={(e) => { e.stopPropagation(); handleColResizeStart(e, `col_${col.id}`, getColW(`col_${col.id}`)); }}
                    />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {sortedFilteredSeeds.map((seed) => {
              const hasText = seedsWithText.has(seed.id);
              const noText = seedPdfsLoaded && !hasText;
              const rowH = rowHeights[seed.id];
              return (
              <tr
                key={seed.id}
                ref={(el) => { if (el) trRefs.current.set(seed.id, el); else trRefs.current.delete(seed.id); }}
                style={rowH !== undefined ? { height: rowH } : undefined}
                className={
                  (readOnlyPaperSelection || selectedIds.has(seed.id))
                    ? 'bg-white'
                    : 'bg-gray-50 opacity-60'
                }
              >
                {/* Checkbox cell — hidden in read-only mode */}
                {!readOnlyPaperSelection && (
                  <td
                    className="px-3 py-2 text-center align-top border-r border-gray-100"
                    style={rowH !== undefined ? { height: rowH, overflow: 'hidden' } : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={selectedIds.has(seed.id)}
                      onChange={() => toggleId(seed.id)}
                      className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 cursor-pointer"
                    />
                  </td>
                )}
                {/* Title td — sticky, holds the row-resize handle */}
                <td
                  className="p-0 align-top border-r border-gray-100 sticky left-0 bg-inherit z-[5] overflow-hidden relative"
                  style={rowH !== undefined ? { height: rowH } : undefined}
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
                      <button
                        onClick={() => setPanelWorkId(prev => prev === seed.id ? null : seed.id)}
                        className="text-xs font-medium text-left text-gray-900 break-words hover:text-blue-600 cursor-pointer"
                      >
                        {seed.title}
                      </button>
                      {noText && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-400 rounded shrink-0 leading-none mt-0.5">
                          No text
                        </span>
                      )}
                    </div>
                  </div>
                  {/* Row resize handle at bottom of title cell */}
                  <div
                    className="absolute bottom-0 left-0 right-0 h-1.5 cursor-row-resize hover:bg-indigo-200/60 select-none touch-none z-10"
                    onPointerDown={(e) => {
                      const current = rowH ?? trRefs.current.get(seed.id)?.offsetHeight ?? 60;
                      handleRowResizeStart(e, seed.id, current);
                    }}
                  />
                </td>
                <td
                  className="px-3 py-2 align-top border-r border-gray-100 text-xs text-gray-500 whitespace-nowrap"
                  style={rowH !== undefined ? { height: rowH, overflow: 'hidden' } : undefined}
                >
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
                    onPasteJson={handlePasteJson}
                    cellHeight={rowH}
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

      {/* Paste JSON dialog */}
      {pasteTarget && (
        <PasteJsonDialog
          schema={schema}
          workId={pasteTarget.workId}
          workTitle={pasteTarget.title}
          existingCells={cellsMap}
          sortedColumns={sortedColumns}
          onClose={() => setPasteTarget(null)}
          onSuccess={refetchResults}
        />
      )}

      {/* Re-extraction confirmation */}
      {showConfirm && (
        <ConfirmDialog
          title="Re-extract papers?"
          message={`${resultsData?.cells.filter((c) => selectedIds.has(c.work_id)).length ?? 0} paper(s) already have extraction notes for this schema. Running extraction will overwrite AI-generated notes. User-reviewed notes will be preserved.`}
          confirmLabel="Extract"
          onConfirm={() => {
            setShowConfirm(false);
            doExtract(extractableSelectedIds, reEvaluateEdited);
          }}
          onCancel={() => setShowConfirm(false)}
        />
      )}

    </div>

    {/* Paper detail side panel — flex sibling, same layout as timeline view.
        The panel takes space from the right; the table shrinks visually but
        keeps its full natural width and stays horizontally scrollable. */}
    {panelWorkId !== null && (
      <WorkDetailPanel
        workId={panelWorkId}
        onClose={() => setPanelWorkId(null)}
        projectId={projectId ?? undefined}
        topicLists={topicLists}
        foldState={panelFoldState}
        onFoldChange={setPanelFoldState}
      />
    )}

    {/* Onboarding hints for the extraction view (only when not read-only) */}
    {!readOnlyPaperSelection && (
      <OnboardingHintSequence
        hints={[
          {
            anchorRef: extractBtnRef,
            storageKey: 'litexplorer:onboarding:extraction-schemas:extract-btn',
            text: 'Run AI extraction on all selected papers at once.',
            placement: 'bottom',
          },
          {
            anchorRef: showPromptBtnRef,
            storageKey: 'litexplorer:onboarding:extraction-schemas:show-prompt',
            text: 'Copy this prompt to use with any external LLM.',
            placement: 'bottom',
          },
        ]}
      />
    )}
    </div>
  );
}
