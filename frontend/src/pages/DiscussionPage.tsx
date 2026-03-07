import { useState, useRef, useEffect, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { useWork } from '../hooks/useWorks';
import { useTimeline } from '../hooks/useTimeline';
import { useWorkPDFs } from '../hooks/useWorkPDFs';
import { useSettings, useLLMModels } from '../hooks/useSettings';
import {
  useGetOrCreateAutoSession,
  useListChatSessions,
  useSaveChatSession,
  useDeleteChatSession,
  useClearChatMessages,
} from '../hooks/useChatSessions';
import { getChatSession, postLLMChat, createWorkNote } from '../api';
import type { ChatMessage, ChatSessionOut, TopicListOut, WorkPDFOut } from '../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PaperEntry {
  work_id: number;
  title: string;
  year: number | null;
  topic_list_ids: number[];
  /** Whether this paper has at least one PDF with extraction_status='ready'. */
  canInclude: boolean;
  /** Unknown until pdfsLoaded — starts false, set after PDFs fetch. */
  pdfsLoaded: boolean;
  included: boolean;
  use_pdf: boolean;
  remark: string;
  remarkOpen: boolean;
  pdfs: WorkPDFOut[];
}

// ---------------------------------------------------------------------------
// Save-as-note inline form (per assistant message)
// ---------------------------------------------------------------------------

function SaveNoteForm({
  content,
  contextWorkIds,
  projectId,
  modelId,
  onDone,
}: {
  content: string;
  contextWorkIds: { work_id: number; title: string }[];
  projectId: number | null;
  modelId: string;
  onDone: () => void;
}) {
  const [noteContent, setNoteContent] = useState(content);
  const [noteType, setNoteType] = useState('');
  const [scope, setScope] = useState<'general' | 'project'>(
    projectId != null ? 'project' : 'general',
  );
  const [selectedWorkId, setSelectedWorkId] = useState<number>(
    contextWorkIds[0]?.work_id ?? 0,
  );
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const handleSave = async () => {
    if (!noteContent.trim() || selectedWorkId === 0) return;
    setStatus('saving');
    try {
      await createWorkNote(selectedWorkId, {
        content: noteContent.trim(),
        note_type: noteType.trim() || null,
        project_id: scope === 'project' && projectId != null ? projectId : null,
        provenance: 'ai',
        model_id: modelId || null,
      });
      setStatus('saved');
      setTimeout(() => { setStatus('idle'); onDone(); }, 1500);
    } catch (e) {
      setStatus('error');
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error');
    }
  };

  if (status === 'saved') {
    return <p className="text-xs text-green-600 mt-2">Note saved ✓</p>;
  }

  return (
    <div className="mt-2 border border-gray-200 rounded p-2 bg-gray-50 space-y-2 text-xs">
      <textarea
        value={noteContent}
        onChange={(e) => setNoteContent(e.target.value)}
        rows={3}
        className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 resize-y"
      />
      <input
        type="text"
        value={noteType}
        onChange={(e) => setNoteType(e.target.value)}
        placeholder="Note label (optional)"
        className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
      />
      {contextWorkIds.length > 1 && (
        <div>
          <label className="text-gray-500 mb-0.5 block">Paper</label>
          <select
            value={selectedWorkId}
            onChange={(e) => setSelectedWorkId(Number(e.target.value))}
            className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none"
          >
            {contextWorkIds.map((w) => (
              <option key={w.work_id} value={w.work_id}>
                {w.title.length > 60 ? w.title.slice(0, 57) + '…' : w.title}
              </option>
            ))}
          </select>
        </div>
      )}
      {projectId != null && (
        <div className="flex gap-4">
          <label className="flex items-center gap-1">
            <input type="radio" checked={scope === 'project'} onChange={() => setScope('project')} />
            Project
          </label>
          <label className="flex items-center gap-1">
            <input type="radio" checked={scope === 'general'} onChange={() => setScope('general')} />
            General
          </label>
        </div>
      )}
      {status === 'error' && <p className="text-red-600">Error: {errorMsg}</p>}
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={status === 'saving' || !noteContent.trim()}
          className="px-2 py-1 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {status === 'saving' ? 'Saving…' : 'Save note'}
        </button>
        <button onClick={onDone} className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single paper row in the context selector
// ---------------------------------------------------------------------------

function PaperRow({
  entry,
  anthropicProvider,
  topicListColorMap,
  locked,
  onChange,
}: {
  entry: PaperEntry;
  anthropicProvider: boolean;
  topicListColorMap: Map<number, string>;
  locked: boolean;
  onChange: (updated: Partial<PaperEntry>) => void;
}) {
  const noContent = entry.pdfsLoaded && !entry.canInclude;
  const loading = !entry.pdfsLoaded;

  const pdfToggleDisabledReason = !anthropicProvider
    ? 'PDF vision requires Anthropic provider'
    : !entry.pdfs.length
    ? 'No PDF attached'
    : null;

  const colorBars = entry.topic_list_ids
    .map((id) => topicListColorMap.get(id))
    .filter((c): c is string => c != null);

  return (
    <div className={`border rounded overflow-hidden text-xs ${noContent ? 'border-gray-100 bg-gray-50 opacity-60' : 'border-gray-200'}`}>
      <div className="flex">
        {/* Topic list color bars */}
        {colorBars.length > 0 && (
          <div className="flex shrink-0">
            {colorBars.map((color, i) => (
              <div key={i} style={{ width: 3, backgroundColor: color }} />
            ))}
          </div>
        )}
        {/* Main content */}
        <div className="flex-1 p-2 space-y-1.5 min-w-0">
          <div className="flex items-start gap-2">
            <span title={noContent ? 'No extracted text — upload and extract a PDF first' : locked ? 'Start a new chat to change paper selection' : undefined}>
              <input
                type="checkbox"
                checked={entry.included}
                disabled={noContent || loading || locked}
                onChange={(e) => onChange({ included: e.target.checked })}
                className="mt-0.5 shrink-0 disabled:cursor-not-allowed"
              />
            </span>
            <div className="flex-1 min-w-0">
              <p className={`font-medium truncate ${noContent ? 'text-gray-400' : 'text-gray-800'}`} title={entry.title}>
                {entry.title.length > 70 ? entry.title.slice(0, 67) + '…' : entry.title}
              </p>
              {entry.year != null && <p className="text-gray-400">{entry.year}</p>}
            </div>
            {loading ? (
              <span className="text-[10px] text-gray-400 shrink-0">Loading…</span>
            ) : noContent ? (
              <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-400 rounded shrink-0">
                No text
              </span>
            ) : (
              <span title={pdfToggleDisabledReason ?? undefined}>
                <button
                  onClick={() => pdfToggleDisabledReason == null && onChange({ use_pdf: !entry.use_pdf })}
                  disabled={pdfToggleDisabledReason != null || !entry.included}
                  className={`px-1.5 py-0.5 rounded border text-[10px] font-medium transition-colors ${
                    entry.use_pdf
                      ? 'bg-indigo-600 border-indigo-600 text-white'
                      : 'bg-white border-gray-300 text-gray-600'
                  } disabled:opacity-40 disabled:cursor-not-allowed`}
                >
                  {entry.use_pdf ? 'PDF' : 'Text'}
                </button>
              </span>
            )}
          </div>

          {!noContent && !loading && (
            entry.remarkOpen ? (
              <div className="pl-5">
                <textarea
                  value={entry.remark}
                  onChange={(e) => onChange({ remark: e.target.value })}
                  placeholder="Optional instruction for this paper (e.g. focus on the evaluation)"
                  rows={2}
                  className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
                />
                <button
                  onClick={() => onChange({ remarkOpen: false })}
                  className="text-[10px] text-gray-400 hover:text-gray-600 mt-0.5"
                >
                  Hide note
                </button>
              </div>
            ) : (
              <button
                onClick={() => onChange({ remarkOpen: true })}
                className="pl-5 text-[10px] text-gray-400 hover:text-blue-600"
              >
                + Add note
              </button>
            )
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paper context selector panel (project mode)
// ---------------------------------------------------------------------------

function PaperContextSelector({
  entries,
  topicLists,
  topicListColorMap,
  anthropicProvider,
  locked,
  onChangeEntry,
  onGlobalToggle,
  onBulkTopicListToggle,
}: {
  entries: PaperEntry[];
  topicLists: TopicListOut[];
  topicListColorMap: Map<number, string>;
  anthropicProvider: boolean;
  locked: boolean;
  onChangeEntry: (workId: number, updated: Partial<PaperEntry>) => void;
  onGlobalToggle: () => void;
  onBulkTopicListToggle: (tlId: number) => void;
}) {
  const includedEntries = entries.filter((e) => e.included);
  const allPdf = includedEntries.length > 0 && includedEntries.every((e) => e.use_pdf);
  const allText = includedEntries.length === 0 || includedEntries.every((e) => !e.use_pdf);
  const globalState: 'all-text' | 'all-pdf' | 'mixed' = allPdf ? 'all-pdf' : allText ? 'all-text' : 'mixed';

  const textPaperCount = includedEntries.filter((e) => !e.use_pdf).length;
  const tokenEstimate = textPaperCount > 0
    ? `~${Math.round(textPaperCount * 8000)} tokens estimated`
    : includedEntries.length === 0
    ? 'No papers selected'
    : 'No text papers included';

  const allLoaded = entries.length > 0 && entries.every((e) => e.pdfsLoaded);
  const availableCount = entries.filter((e) => e.canInclude).length;

  // Fix 1: sort selected papers to top, then alphabetical within each group
  const sortedEntries = useMemo(() => {
    const included = entries.filter((e) => e.included).sort((a, b) => a.title.localeCompare(b.title));
    const notIncluded = entries.filter((e) => !e.included).sort((a, b) => a.title.localeCompare(b.title));
    return [...included, ...notIncluded];
  }, [entries]);

  // Fix 3: indeterminate state for topic list checkboxes
  const tlCheckboxRefs = useRef<Map<number, HTMLInputElement>>(new Map());

  useEffect(() => {
    for (const tl of topicLists) {
      const el = tlCheckboxRefs.current.get(tl.id);
      if (!el) continue;
      const tlEntries = entries.filter((e) => e.topic_list_ids.includes(tl.id));
      const selectedCount = tlEntries.filter((e) => e.included).length;
      el.indeterminate = selectedCount > 0 && selectedCount < tlEntries.length;
    }
  }, [entries, topicLists]);

  return (
    <div className="flex flex-col h-full border-r border-gray-200 bg-gray-50" style={{ minWidth: 260, maxWidth: 320 }}>
      <div className="p-3 border-b border-gray-200 flex items-center justify-between">
        <div>
          <h2 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Context papers</h2>
          {locked ? (
            <p className="text-[10px] text-amber-600 mt-0.5">Locked · start a new chat to change</p>
          ) : allLoaded && availableCount < entries.length ? (
            <p className="text-[10px] text-gray-400 mt-0.5">
              {entries.length - availableCount} paper{entries.length - availableCount !== 1 ? 's' : ''} have no extracted text
            </p>
          ) : null}
        </div>
        <button
          onClick={onGlobalToggle}
          title={globalState === 'all-pdf' ? 'Switch all to Text' : 'Switch all to PDF'}
          disabled={!anthropicProvider}
          className={`px-1.5 py-0.5 rounded border text-[10px] font-medium transition-colors ${
            globalState === 'all-pdf'
              ? 'bg-indigo-600 border-indigo-600 text-white'
              : globalState === 'mixed'
              ? 'bg-amber-50 border-amber-300 text-amber-700'
              : 'bg-white border-gray-300 text-gray-600'
          } disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          {globalState === 'all-pdf' ? 'All PDF' : globalState === 'mixed' ? 'Mixed' : 'All Text'}
        </button>
      </div>

      {/* Fix 3: topic list bulk-select checkboxes */}
      {topicLists.length > 0 && (
        <div className="px-2 py-1.5 border-b border-gray-200 flex flex-wrap gap-x-3 gap-y-1">
          {topicLists.map((tl) => {
            const tlEntries = entries.filter((e) => e.topic_list_ids.includes(tl.id));
            const selectedCount = tlEntries.filter((e) => e.included).length;
            const allSelected = tlEntries.length > 0 && selectedCount === tlEntries.length;
            return (
              <label key={tl.id} className={`flex items-center gap-1 ${locked ? 'cursor-default' : 'cursor-pointer'}`} title={tl.name}>
                <input
                  type="checkbox"
                  ref={(el) => {
                    if (el) tlCheckboxRefs.current.set(tl.id, el);
                    else tlCheckboxRefs.current.delete(tl.id);
                  }}
                  checked={allSelected}
                  disabled={locked}
                  onChange={() => onBulkTopicListToggle(tl.id)}
                  className="w-3 h-3 shrink-0 disabled:cursor-not-allowed"
                />
                <span
                  className="text-[10px] font-medium truncate max-w-[72px]"
                  style={{ color: tl.color }}
                >
                  {tl.name}
                </span>
              </label>
            );
          })}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {sortedEntries.length === 0 && (
          <p className="text-xs text-gray-400 p-2">No papers in this project yet.</p>
        )}
        {sortedEntries.map((e) => (
          <PaperRow
            key={e.work_id}
            entry={e}
            anthropicProvider={anthropicProvider}
            topicListColorMap={topicListColorMap}
            locked={locked}
            onChange={(updated) => onChangeEntry(e.work_id, updated)}
          />
        ))}
      </div>

      <div className="px-3 py-2 border-t border-gray-200">
        <p className="text-[10px] text-gray-400">{tokenEstimate}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Load session modal
// ---------------------------------------------------------------------------

function LoadSessionModal({
  sessions,
  onLoad,
  onClose,
  onDelete,
}: {
  sessions: ChatSessionOut[];
  onLoad: (session: ChatSessionOut) => void;
  onClose: () => void;
  onDelete: (sessionId: number) => void;
}) {
  const saved = sessions.filter((s) => !s.is_auto);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4 flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">Load saved conversation</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-lg leading-none">×</button>
        </div>
        <div className="overflow-y-auto flex-1 divide-y divide-gray-100">
          {saved.length === 0 && (
            <p className="text-sm text-gray-400 text-center py-8">No saved conversations yet.</p>
          )}
          {saved.map((s) => (
            <div key={s.id} className="flex items-center justify-between px-4 py-3 hover:bg-gray-50">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">{s.title}</p>
                <p className="text-[11px] text-gray-400">
                  {s.message_count} message{s.message_count !== 1 ? 's' : ''}
                  {' · '}
                  {new Date(s.updated_at).toLocaleDateString()}
                </p>
              </div>
              <div className="flex items-center gap-2 ml-3 shrink-0">
                <button
                  onClick={() => onLoad(s)}
                  className="px-2 py-1 text-xs text-white bg-indigo-600 rounded hover:bg-indigo-700"
                >
                  Load
                </button>
                <button
                  onClick={() => onDelete(s.id)}
                  className="px-2 py-1 text-xs border border-red-200 text-red-600 rounded hover:bg-red-50"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="px-4 py-3 border-t border-gray-200">
          <button onClick={onClose} className="px-3 py-1.5 text-xs border border-gray-300 rounded hover:bg-gray-50">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main DiscussionPage
// ---------------------------------------------------------------------------

export default function DiscussionPage() {
  const { workId: workIdParam, projectId: projectIdParam } = useParams<{
    workId?: string;
    projectId?: string;
  }>();
  const navigate = useNavigate();

  const isLibraryMode = workIdParam != null;
  const workId = workIdParam != null ? Number(workIdParam) : null;
  const projectId = projectIdParam != null ? Number(projectIdParam) : null;

  // Settings
  const { data: settings = [] } = useSettings();
  const sm = useMemo(
    () => Object.fromEntries(settings.map((s) => [s.key, s.value])),
    [settings],
  );
  const llmProvider = sm['llm_provider'] ?? '';
  const llmModelId = sm['llm_model_id'] ?? '';
  const isAnthropicProvider = llmProvider === 'anthropic';
  const canFetchModels = !!llmProvider && (!!sm['llm_api_key'] || !!sm['llm_base_url']);
  useLLMModels(canFetchModels);
  const llmNotConfigured = !llmProvider || !llmModelId;

  // Library mode: single work + its PDFs
  const { data: singleWork } = useWork(workId);
  const { data: singleWorkPdfs } = useWorkPDFs(workId);

  const singleWorkPdfsLoaded = singleWorkPdfs !== undefined;
  const libraryModeHasContent = isLibraryMode && singleWorkPdfsLoaded
    && singleWorkPdfs.some((p) => p.extraction_status === 'ready');
  const libraryModeNoContent = isLibraryMode && singleWorkPdfsLoaded && !libraryModeHasContent;

  // Project mode: load timeline to get seeds
  const { data: timeline } = useTimeline(projectId ?? 0);

  // Paper entries state (project mode only)
  const [entries, setEntries] = useState<PaperEntry[]>([]);
  const [entriesInitialized, setEntriesInitialized] = useState(false);

  useEffect(() => {
    if (isLibraryMode || entriesInitialized || !timeline) return;
    const seeds = timeline.seeds.map((s): PaperEntry => ({
      work_id: s.id,
      title: s.title,
      year: s.publication_year,
      topic_list_ids: s.topic_list_ids,
      canInclude: false,
      pdfsLoaded: false,
      included: false,
      use_pdf: false,
      remark: '',
      remarkOpen: false,
      pdfs: [],
    }));
    setEntries(seeds);
    setEntriesInitialized(true);
  }, [timeline, isLibraryMode, entriesInitialized]);

  useEffect(() => {
    if (isLibraryMode || entries.length === 0) return;
    const unloaded = entries.filter((e) => !e.pdfsLoaded);
    if (unloaded.length === 0) return;

    Promise.all(
      unloaded.map((e) =>
        fetch(`/api/works/${e.work_id}/pdfs`)
          .then((r) => (r.ok ? r.json() : []))
          .then((pdfs: WorkPDFOut[]) => ({ work_id: e.work_id, pdfs }))
          .catch(() => ({ work_id: e.work_id, pdfs: [] as WorkPDFOut[] })),
      ),
    ).then((results) => {
      const map: Record<number, WorkPDFOut[]> = {};
      for (const r of results) {
        map[r.work_id] = r.pdfs;
      }
      const restored = restoredSelectionRef.current;
      setEntries((prev) =>
        prev.map((e) => {
          if (!(e.work_id in map)) return e;
          const pdfs = map[e.work_id];
          const canInclude = pdfs.some((p) => p.extraction_status === 'ready');
          // If a stored selection exists (returning to a locked discussion),
          // honour it; otherwise auto-select all papers that have text.
          const included = restored != null ? restored.has(e.work_id) : canInclude;
          return { ...e, pdfs, pdfsLoaded: true, canInclude, included };
        }),
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries.length, isLibraryMode]);

  // ---------------------------------------------------------------------------
  // Session persistence
  // ---------------------------------------------------------------------------

  const [autoSessionId, setAutoSessionId] = useState<number | null>(null);
  const sessionInitialized = useRef(false);

  // localStorage key for persisting paper selection for this project discussion.
  const selKey = !isLibraryMode && projectId != null
    ? `litexplorer:discuss:project:${projectId}:sel`
    : null;

  // When the auto-session is restored with messages, we need to honour the saved
  // paper selection instead of auto-selecting everything.  This ref bridges the
  // async race between session restore and PDF loading (whichever arrives second
  // wins: the ref is checked in both places).
  const restoredSelectionRef = useRef<Set<number> | null>(null);

  const getOrCreateAuto = useGetOrCreateAutoSession();

  // The scope identifier: workId for library mode, projectId for project mode.
  // The effect re-runs when this becomes non-null (params parsed by React Router).
  const scopeKey = isLibraryMode ? workId : projectId;

  // On mount (once the scope key is known), get or create the auto-session and restore messages.
  useEffect(() => {
    if (sessionInitialized.current) return;
    if (scopeKey == null) return; // wait for route params to resolve
    sessionInitialized.current = true;

    getOrCreateAuto.mutate(
      { work_id: workId, project_id: projectId },
      {
        onSuccess: (session) => {
          setAutoSessionId(session.id);
          // Restore messages from the session
          if (session.messages.length > 0) {
            setMessages(
              session.messages.map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content }))
            );
            // Restore paper selection if we have a stored one for this scope.
            // The ref ensures the PDF-loading effect (which may run later) also
            // sees the stored selection instead of auto-selecting everything.
            if (selKey) {
              const stored = localStorage.getItem(selKey);
              if (stored) {
                const storedIds = new Set<number>(JSON.parse(stored) as number[]);
                restoredSelectionRef.current = storedIds;
                // Apply immediately in case PDFs are already loaded.
                setEntries((prev) =>
                  prev.map((e) => (e.pdfsLoaded ? { ...e, included: storedIds.has(e.work_id) } : e))
                );
              }
            }
          }
        },
      },
    );
  // Re-run only when the scope key becomes known (stable after first render).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  // ---------------------------------------------------------------------------
  // Chat state
  // ---------------------------------------------------------------------------

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [saveNoteForIdx, setSaveNoteForIdx] = useState<number | null>(null);

  // "New Chat" confirm dialog
  const [confirmClear, setConfirmClear] = useState(false);

  // Save dialog
  const [showSavePrompt, setShowSavePrompt] = useState(false);
  const [saveTitle, setSaveTitle] = useState('');

  // Load modal
  const [showLoadModal, setShowLoadModal] = useState(false);

  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages]);

  // ---------------------------------------------------------------------------
  // Session mutations
  // ---------------------------------------------------------------------------

  const saveMutation = useSaveChatSession();
  const deleteMutation = useDeleteChatSession();
  const clearMutation = useClearChatMessages();
  const { data: sessionList, refetch: refetchSessions } = useListChatSessions(workId, projectId);

  const chatMutation = useMutation({
    mutationFn: (vars: {
      papers: { work_id: number; use_pdf: boolean; remark?: string | null }[];
      history: { role: string; content: string }[];
      message: string;
    }) =>
      postLLMChat({
        project_id: projectId,
        session_id: autoSessionId,
        papers: vars.papers,
        history: vars.history,
        message: vars.message,
      }),
  });

  const includedPapers = useMemo(() => {
    if (isLibraryMode) {
      return workId != null ? [{ work_id: workId, use_pdf: false, remark: null }] : [];
    }
    return entries
      .filter((e) => e.included)
      .map((e) => ({ work_id: e.work_id, use_pdf: e.use_pdf, remark: e.remark.trim() || null }));
  }, [isLibraryMode, workId, entries]);

  const contextWorkIds = useMemo(() => {
    if (isLibraryMode && singleWork) {
      return [{ work_id: singleWork.id, title: singleWork.title }];
    }
    return entries.filter((e) => e.included).map((e) => ({ work_id: e.work_id, title: e.title }));
  }, [isLibraryMode, singleWork, entries]);

  const noPapersSelected = !isLibraryMode && includedPapers.length === 0;

  // Lock the paper selection once the discussion has started; unlock on New Chat.
  const discussionActive = messages.some((m) => m.role === 'user' || m.role === 'assistant');

  const chatDisabled =
    chatMutation.isPending ||
    llmNotConfigured ||
    libraryModeNoContent ||
    noPapersSelected;

  // Prompt preview
  const [showPromptPreview, setShowPromptPreview] = useState(false);

  const buildChatPromptPreview = (): string => {
    const parts: string[] = [];

    parts.push('━━━ System ━━━');
    parts.push('You are a research assistant helping analyze academic literature.');
    parts.push('');

    // Context papers with content placeholders
    const papers = isLibraryMode && singleWork
      ? [{ title: singleWork.title, year: singleWork.publication_year as number | null, use_pdf: false, remark: '' }]
      : entries.filter((e) => e.included);

    if (papers.length > 0) {
      parts.push('━━━ Context ━━━');
      for (const p of papers) {
        const yearStr = p.year != null ? String(p.year) : 'n.d.';
        parts.push(`--- Paper: ${p.title} (${yearStr}) ---`);
        if ('remark' in p && (p as PaperEntry).remark?.trim()) {
          parts.push((p as PaperEntry).remark.trim());
        }
        parts.push(p.use_pdf ? `[PDF of "${p.title}"]` : `[Text of "${p.title}"]`);
      }
      parts.push('');
    }

    // Conversation history (truncated per message for readability)
    const history = messages.filter((m) => m.role === 'user' || m.role === 'assistant');
    if (history.length > 0) {
      parts.push('━━━ Conversation history ━━━');
      for (const msg of history) {
        const preview = msg.content.length > 300
          ? msg.content.slice(0, 297) + '…'
          : msg.content;
        parts.push(`${msg.role === 'user' ? 'User' : 'Assistant'}: ${preview}`);
      }
      parts.push('');
    }

    parts.push('━━━ Current message ━━━');
    parts.push(input.trim() || '(your message here)');

    return parts.join('\n');
  };

  const inputPlaceholder = llmNotConfigured
    ? 'Configure LLM in Settings first'
    : libraryModeNoContent
    ? 'No extracted text — upload and extract a PDF first'
    : noPapersSelected
    ? 'No papers with extracted text selected'
    : 'Type a message… (Ctrl+Enter to send)';

  const handleSend = () => {
    const text = input.trim();
    if (!text || chatDisabled) return;

    // Persist the paper selection the first time a message is sent in this discussion
    if (!isLibraryMode && !discussionActive && selKey) {
      const selectedIds = entries.filter((e) => e.included).map((e) => e.work_id);
      localStorage.setItem(selKey, JSON.stringify(selectedIds));
    }

    const userMsg: ChatMessage = { role: 'user', content: text };
    const historyForApi = messages
      .filter((m) => m.role !== 'error')
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [...prev, userMsg]);
    setInput('');

    chatMutation.mutate(
      { papers: includedPapers, history: historyForApi, message: text },
      {
        onSuccess: (data) => {
          setMessages((prev) => [...prev, { role: 'assistant', content: data.reply }]);
        },
        onError: (err) => {
          const raw = err instanceof Error ? err.message : String(err);
          let detail = raw;
          try {
            const parsed = JSON.parse(raw);
            detail = parsed.detail ?? raw;
          } catch {
            // keep raw
          }
          setMessages((prev) => [...prev, { role: 'error', content: `Error: ${detail}` }]);
        },
      },
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  const topicLists = timeline?.topic_lists ?? [];

  const topicListColorMap = useMemo(
    () => new Map(topicLists.map((tl) => [tl.id, tl.color])),
    [topicLists],
  );

  const handleChangeEntry = (wid: number, updated: Partial<PaperEntry>) => {
    setEntries((prev) => prev.map((e) => (e.work_id === wid ? { ...e, ...updated } : e)));
  };

  const handleBulkTopicListToggle = (tlId: number) => {
    setEntries((prev) => {
      const anySelected = prev.some((e) => e.topic_list_ids.includes(tlId) && e.included);
      return prev.map((e) => {
        if (!e.topic_list_ids.includes(tlId)) return e;
        // Deselect all → unselect; None selected → select canInclude papers
        if (anySelected) return { ...e, included: false };
        return { ...e, included: e.canInclude ? true : e.included };
      });
    });
  };

  const handleGlobalToggle = () => {
    setEntries((prev) => {
      const included = prev.filter((e) => e.included && e.canInclude);
      const allPdf = included.length > 0 && included.every((e) => e.use_pdf);
      const newUsePdf = !allPdf;
      return prev.map((e) => {
        if (!e.included || !e.canInclude) return e;
        const canUsePdf = isAnthropicProvider && e.pdfs.length > 0;
        return { ...e, use_pdf: newUsePdf && canUsePdf };
      });
    });
  };

  // New Chat — clears messages from the auto-session
  const handleNewChat = () => {
    setMessages([]);
    setSaveNoteForIdx(null);
    setConfirmClear(false);
    // Clear persisted selection so the next discussion starts with a fresh slate
    if (selKey) localStorage.removeItem(selKey);
    restoredSelectionRef.current = null;
    if (autoSessionId != null) {
      clearMutation.mutate(autoSessionId);
    }
  };

  // Save conversation as a named snapshot
  const handleSave = () => {
    const title = saveTitle.trim();
    if (!title || autoSessionId == null) return;
    saveMutation.mutate(
      { sessionId: autoSessionId, title },
      {
        onSuccess: () => {
          setShowSavePrompt(false);
          setSaveTitle('');
          refetchSessions();
        },
      },
    );
  };

  // Load a saved session — replace local messages
  const handleLoadSession = async (session: ChatSessionOut) => {
    try {
      const full = await getChatSession(session.id);
      setMessages(
        full.messages.map((m) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
        }))
      );
      setSaveNoteForIdx(null);
      setShowLoadModal(false);
    } catch {
      // silently ignore load errors
    }
  };

  // Delete a saved session
  const handleDeleteSession = (sessionId: number) => {
    deleteMutation.mutate(sessionId, { onSuccess: () => refetchSessions() });
  };

  const isPending = chatMutation.isPending;
  const hasSavedSessions = (sessionList ?? []).some((s) => !s.is_auto);

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(isLibraryMode ? '/library' : `/projects/${projectId}`)}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            ← Back
          </button>
          <h1 className="text-sm font-semibold text-gray-900">
            {isLibraryMode
              ? singleWork
                ? `Discussing: ${singleWork.title.length > 60 ? singleWork.title.slice(0, 57) + '…' : singleWork.title}`
                : 'Discussion'
              : 'Project discussion'}
          </h1>
        </div>

        {/* Session toolbar */}
        <div className="flex items-center gap-2">
          {/* Save button */}
          {messages.some((m) => m.role !== 'error') && autoSessionId != null && (
            <div className="relative">
              {showSavePrompt ? (
                <div className="flex items-center gap-1">
                  <input
                    type="text"
                    value={saveTitle}
                    onChange={(e) => setSaveTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') { setShowSavePrompt(false); setSaveTitle(''); } }}
                    placeholder="Conversation title…"
                    autoFocus
                    className="border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500 w-40"
                  />
                  <button
                    onClick={handleSave}
                    disabled={!saveTitle.trim() || saveMutation.isPending}
                    className="px-2 py-1 text-xs text-white bg-indigo-600 rounded hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {saveMutation.isPending ? '…' : 'Save'}
                  </button>
                  <button
                    onClick={() => { setShowSavePrompt(false); setSaveTitle(''); }}
                    className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setShowSavePrompt(true)}
                  className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                >
                  Save
                </button>
              )}
            </div>
          )}

          {/* Load button */}
          <button
            onClick={() => { refetchSessions(); setShowLoadModal(true); }}
            className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
          >
            Load{hasSavedSessions ? ' ▾' : ''}
          </button>

          {/* New Chat button */}
          {messages.length > 0 && (
            <button
              onClick={() => setConfirmClear(true)}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
            >
              New Chat
            </button>
          )}
        </div>
      </div>

      {/* LLM not configured banner */}
      {llmNotConfigured && (
        <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-sm">
          <span className="text-amber-800">LLM not configured.</span>
          <Link to="/settings" className="text-amber-700 underline font-medium">Go to Settings</Link>
        </div>
      )}

      {/* Library mode: no extracted text warning */}
      {libraryModeNoContent && (
        <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-sm">
          <span className="text-amber-800">
            This paper has no extracted text. To discuss it, attach a PDF and extract its text (via the paper panel → PDFs → Extract text).
          </span>
        </div>
      )}

      {/* Confirm clear */}
      {confirmClear && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 flex items-center gap-3 text-sm">
          <span className="text-red-700">Start a new conversation? The current one will be lost unless saved.</span>
          <button
            onClick={handleNewChat}
            className="px-2 py-0.5 text-xs text-white bg-red-600 rounded hover:bg-red-700"
          >
            New Chat
          </button>
          <button
            onClick={() => setConfirmClear(false)}
            className="px-2 py-0.5 text-xs border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 min-h-0">
        {/* Left panel: context selector (project mode only) */}
        {!isLibraryMode && (
          <PaperContextSelector
            entries={entries}
            topicLists={topicLists}
            topicListColorMap={topicListColorMap}
            anthropicProvider={isAnthropicProvider}
            locked={discussionActive}
            onChangeEntry={handleChangeEntry}
            onGlobalToggle={handleGlobalToggle}
            onBulkTopicListToggle={handleBulkTopicListToggle}
          />
        )}

        {/* Chat panel */}
        <div className="flex flex-col flex-1 min-w-0">

          {/* Context annotation — paper(s) being discussed */}
          {(isLibraryMode ? !!singleWork : true) && (
            <div className="px-4 py-2 border-b border-slate-200 bg-slate-50 text-xs text-gray-600 shrink-0">
              {isLibraryMode && singleWork ? (
                <span>
                  <span className="font-medium text-gray-800">
                    {singleWork.title.length > 80 ? singleWork.title.slice(0, 77) + '…' : singleWork.title}
                  </span>
                  {singleWork.first_author_name && (
                    <span className="text-gray-500">
                      {' · '}{singleWork.first_author_name}{singleWork.author_count > 1 ? ' et al.' : ''}
                    </span>
                  )}
                  {singleWork.publication_year && (
                    <span className="text-gray-500"> · {singleWork.publication_year}</span>
                  )}
                </span>
              ) : !isLibraryMode && includedPapers.length > 0 ? (
                <span>
                  <span className="font-medium text-gray-700">
                    {includedPapers.length} paper{includedPapers.length !== 1 ? 's' : ''} selected:
                  </span>
                  {' '}
                  {entries
                    .filter((e) => e.included)
                    .map((e) => `${e.title}${e.year ? ` (${e.year})` : ''}`)
                    .join(' · ')
                    .slice(0, 200) +
                    (entries.filter((e) => e.included).map((e) => `${e.title}${e.year ? ` (${e.year})` : ''}`).join(' · ').length > 200 ? '…' : '')}
                </span>
              ) : !isLibraryMode ? (
                <span className="text-gray-400 italic">No papers selected</span>
              ) : null}
            </div>
          )}

          <div ref={threadRef} className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <p className="text-sm text-gray-400 text-center mt-8">
                {llmNotConfigured
                  ? 'Configure an LLM provider in Settings to start chatting.'
                  : libraryModeNoContent
                  ? 'No extracted text available for this paper.'
                  : noPapersSelected
                  ? 'No papers with extracted text are available in this project.'
                  : 'Start the conversation below.'}
              </p>
            )}

            {messages.map((msg, idx) => (
              <div key={idx}>
                {msg.role === 'user' ? (
                  <div className="flex justify-end">
                    <div className="max-w-[75%] bg-blue-600 text-white rounded-lg px-3 py-2 text-sm whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </div>
                ) : msg.role === 'error' ? (
                  <div className="flex justify-start">
                    <div className="max-w-[75%] bg-red-50 border border-red-200 text-red-800 rounded-lg px-3 py-2 text-sm whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  <div className="flex justify-start">
                    <div className="max-w-[75%]">
                      <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-800 whitespace-pre-wrap">
                        {msg.content}
                      </div>
                      {saveNoteForIdx === idx ? (
                        <SaveNoteForm
                          content={msg.content}
                          contextWorkIds={contextWorkIds}
                          projectId={projectId}
                          modelId={llmModelId}
                          onDone={() => setSaveNoteForIdx(null)}
                        />
                      ) : (
                        <button
                          onClick={() => setSaveNoteForIdx(idx)}
                          className="text-[10px] text-gray-400 hover:text-blue-600 mt-1 underline"
                        >
                          Save as note
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {isPending && (
              <div className="flex justify-start">
                <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-500 animate-pulse">
                  Thinking…
                </div>
              </div>
            )}
          </div>

          {/* Input area */}
          <div className="border-t border-gray-200 p-3 shrink-0">
            <div className="flex gap-2 items-end">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={chatDisabled}
                placeholder={inputPlaceholder}
                rows={3}
                className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
              />
              <button
                onClick={() => setShowPromptPreview(true)}
                title="Preview the prompt sent to the LLM (paper content replaced with placeholder)"
                className="px-3 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50 text-gray-600 self-end"
              >
                Show prompt
              </button>
              <button
                onClick={handleSend}
                disabled={chatDisabled || !input.trim()}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 self-end"
              >
                {isPending ? (
                  <span className="inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  'Send'
                )}
              </button>
            </div>
            <p className="text-[10px] text-gray-400 mt-1">Ctrl+Enter to send</p>
          </div>
        </div>
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
                  Paper content replaced with placeholder. History truncated at 300 chars per message.
                </p>
              </div>
              <button
                onClick={() => setShowPromptPreview(false)}
                className="text-gray-400 hover:text-gray-700 text-lg leading-none ml-4"
              >
                ✕
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5">
              <pre className="text-xs font-mono whitespace-pre-wrap text-gray-700 leading-relaxed">
                {buildChatPromptPreview()}
              </pre>
            </div>
          </div>
        </div>
      )}

      {/* Load session modal */}
      {showLoadModal && (
        <LoadSessionModal
          sessions={sessionList ?? []}
          onLoad={handleLoadSession}
          onClose={() => setShowLoadModal(false)}
          onDelete={handleDeleteSession}
        />
      )}
    </div>
  );
}
