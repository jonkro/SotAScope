import { useState, useRef, useEffect, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { useWork } from '../hooks/useWorks';
import { useTimeline } from '../hooks/useTimeline';
import { useWorkPDFs } from '../hooks/useWorkPDFs';
import { useSettings, useLLMModels } from '../hooks/useSettings';
import { postLLMChat, createWorkNote } from '../api';
import type { ChatMessage, WorkPDFOut } from '../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PaperEntry {
  work_id: number;
  title: string;
  year: number | null;
  included: boolean;
  use_pdf: boolean;
  remark: string;
  remarkOpen: boolean;
  pdfs: WorkPDFOut[];
  pdfsLoaded: boolean;
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
    return <p className="text-xs text-green-600 mt-2">Note saved</p>;
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
                {w.title.length > 60 ? w.title.slice(0, 57) + '...' : w.title}
              </option>
            ))}
          </select>
        </div>
      )}
      {projectId != null && (
        <div className="flex gap-4">
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={scope === 'project'}
              onChange={() => setScope('project')}
            />
            Project
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={scope === 'general'}
              onChange={() => setScope('general')}
            />
            General
          </label>
        </div>
      )}
      {status === 'error' && (
        <p className="text-red-600">Error: {errorMsg}</p>
      )}
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={status === 'saving' || !noteContent.trim()}
          className="px-2 py-1 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {status === 'saving' ? 'Saving...' : 'Save note'}
        </button>
        <button
          onClick={onDone}
          className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
        >
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
  onChange,
}: {
  entry: PaperEntry;
  anthropicProvider: boolean;
  onChange: (updated: Partial<PaperEntry>) => void;
}) {
  const hasPdf = entry.pdfs.length > 0;
  const pdfDisabledReason = !anthropicProvider
    ? 'PDF vision requires Anthropic provider'
    : !hasPdf
    ? 'No PDF attached'
    : null;

  return (
    <div className="border border-gray-200 rounded p-2 space-y-1.5 text-xs">
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={entry.included}
          onChange={(e) => onChange({ included: e.target.checked })}
          className="mt-0.5 shrink-0"
        />
        <div className="flex-1 min-w-0">
          <p className="font-medium text-gray-800 truncate" title={entry.title}>
            {entry.title.length > 70 ? entry.title.slice(0, 67) + '...' : entry.title}
          </p>
          {entry.year != null && (
            <p className="text-gray-400">{entry.year}</p>
          )}
        </div>
        {/* Text/PDF toggle */}
        <span title={pdfDisabledReason ?? undefined}>
          <button
            onClick={() => pdfDisabledReason == null && onChange({ use_pdf: !entry.use_pdf })}
            disabled={pdfDisabledReason != null || !entry.included}
            className={`px-1.5 py-0.5 rounded border text-[10px] font-medium transition-colors ${
              entry.use_pdf
                ? 'bg-indigo-600 border-indigo-600 text-white'
                : 'bg-white border-gray-300 text-gray-600'
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {entry.use_pdf ? 'PDF' : 'Text'}
          </button>
        </span>
      </div>

      {/* Remark */}
      {entry.remarkOpen ? (
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
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paper context selector panel (project mode)
// ---------------------------------------------------------------------------

function PaperContextSelector({
  entries,
  anthropicProvider,
  onChangeEntry,
  onGlobalToggle,
}: {
  entries: PaperEntry[];
  anthropicProvider: boolean;
  onChangeEntry: (workId: number, updated: Partial<PaperEntry>) => void;
  onGlobalToggle: () => void;
}) {
  const includedEntries = entries.filter((e) => e.included);
  const allPdf = includedEntries.length > 0 && includedEntries.every((e) => e.use_pdf);
  const allText = includedEntries.every((e) => !e.use_pdf);
  const globalState: 'all-text' | 'all-pdf' | 'mixed' = allPdf
    ? 'all-pdf'
    : allText
    ? 'all-text'
    : 'mixed';

  const textPaperCount = includedEntries.filter((e) => !e.use_pdf).length;
  const tokenEstimate = textPaperCount > 0
    ? `~${Math.round(textPaperCount * 8000)} tokens estimated`
    : 'No text papers included';

  return (
    <div className="flex flex-col h-full border-r border-gray-200 bg-gray-50" style={{ minWidth: 260, maxWidth: 320 }}>
      <div className="p-3 border-b border-gray-200 flex items-center justify-between">
        <h2 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Context papers</h2>
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

      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {entries.length === 0 && (
          <p className="text-xs text-gray-400 p-2">No papers in this project yet.</p>
        )}
        {entries.map((e) => (
          <PaperRow
            key={e.work_id}
            entry={e}
            anthropicProvider={anthropicProvider}
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

  // LLM model list -- check if configured
  const canFetchModels = !!llmProvider && (!!sm['llm_api_key'] || !!sm['llm_base_url']);
  const { data: _modelsData } = useLLMModels(canFetchModels);
  const llmNotConfigured = !llmProvider || !llmModelId;

  // Library mode: load single work
  const { data: singleWork } = useWork(workId);
  const { data: singleWorkPdfs } = useWorkPDFs(workId);

  // Project mode: load timeline to get seeds
  const { data: timeline } = useTimeline(projectId ?? 0);

  // Paper entries state (project mode only)
  const [entries, setEntries] = useState<PaperEntry[]>([]);
  const [entriesInitialized, setEntriesInitialized] = useState(false);

  // Initialize entries from timeline (project mode)
  useEffect(() => {
    if (isLibraryMode || entriesInitialized || !timeline) return;
    const seeds = timeline.seeds.map((s): PaperEntry => ({
      work_id: s.id,
      title: s.title,
      year: s.publication_year,
      included: true,
      use_pdf: false,
      remark: '',
      remarkOpen: false,
      pdfs: [],
      pdfsLoaded: false,
    }));
    setEntries(seeds);
    setEntriesInitialized(true);
  }, [timeline, isLibraryMode, entriesInitialized]);

  // Fetch PDFs for each project-mode entry on demand
  const [pdfsByWork, setPdfsByWork] = useState<Record<number, WorkPDFOut[]>>({});
  useEffect(() => {
    if (isLibraryMode || entries.length === 0) return;
    const unloaded = entries.filter((e) => !e.pdfsLoaded);
    if (unloaded.length === 0) return;

    Promise.all(
      unloaded.map((e) =>
        fetch(`/api/works/${e.work_id}/pdfs`)
          .then((r) => r.ok ? r.json() : [])
          .then((pdfs: WorkPDFOut[]) => ({ work_id: e.work_id, pdfs }))
          .catch(() => ({ work_id: e.work_id, pdfs: [] as WorkPDFOut[] })),
      ),
    ).then((results) => {
      const map: Record<number, WorkPDFOut[]> = { ...pdfsByWork };
      for (const r of results) {
        map[r.work_id] = r.pdfs;
      }
      setPdfsByWork(map);
      setEntries((prev) =>
        prev.map((e) => ({
          ...e,
          pdfs: map[e.work_id] ?? e.pdfs,
          pdfsLoaded: true,
        })),
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries.length, isLibraryMode]);

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [saveNoteForIdx, setSaveNoteForIdx] = useState<number | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages]);

  // Chat mutation
  const chatMutation = useMutation({
    mutationFn: (vars: {
      papers: { work_id: number; use_pdf: boolean; remark?: string | null }[];
      history: { role: string; content: string }[];
      message: string;
    }) =>
      postLLMChat({
        project_id: projectId,
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
      .map((e) => ({
        work_id: e.work_id,
        use_pdf: e.use_pdf,
        remark: e.remark.trim() || null,
      }));
  }, [isLibraryMode, workId, entries]);

  const contextWorkIds = useMemo(() => {
    if (isLibraryMode && singleWork) {
      return [{ work_id: singleWork.id, title: singleWork.title }];
    }
    return entries
      .filter((e) => e.included)
      .map((e) => ({ work_id: e.work_id, title: e.title }));
  }, [isLibraryMode, singleWork, entries]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || chatMutation.isPending) return;

    const userMsg: ChatMessage = { role: 'user', content: text };
    const historyForApi = messages
      .filter((m) => m.role !== 'error')
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [...prev, userMsg]);
    setInput('');

    chatMutation.mutate(
      {
        papers: includedPapers,
        history: historyForApi,
        message: text,
      },
      {
        onSuccess: (data) => {
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: data.reply },
          ]);
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
          setMessages((prev) => [
            ...prev,
            { role: 'error', content: `Error: ${detail}` },
          ]);
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

  const handleClear = () => {
    if (messages.length > 0) {
      setConfirmClear(true);
    }
  };

  const handleChangeEntry = (workId: number, updated: Partial<PaperEntry>) => {
    setEntries((prev) =>
      prev.map((e) => (e.work_id === workId ? { ...e, ...updated } : e)),
    );
  };

  const handleGlobalToggle = () => {
    setEntries((prev) => {
      const includedEntries = prev.filter((e) => e.included);
      const allPdf = includedEntries.every((e) => e.use_pdf);
      const newUsePdf = !allPdf;
      return prev.map((e) => {
        if (!e.included) return e;
        const hasPdf = e.pdfs.length > 0;
        const canUsePdf = isAnthropicProvider && hasPdf;
        return { ...e, use_pdf: newUsePdf && canUsePdf };
      });
    });
  };

  const isPending = chatMutation.isPending;

  // Suppress unused variable warnings for hooks called for side effects
  void singleWorkPdfs;

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            Back
          </button>
          <h1 className="text-sm font-semibold text-gray-900">
            {isLibraryMode
              ? singleWork
                ? `Discussing: ${singleWork.title.length > 60 ? singleWork.title.slice(0, 57) + '...' : singleWork.title}`
                : 'Discussion'
              : 'Project discussion'}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              onClick={handleClear}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
            >
              New conversation
            </button>
          )}
        </div>
      </div>

      {/* LLM not configured banner */}
      {llmNotConfigured && (
        <div className="px-4 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-sm">
          <span className="text-amber-800">LLM not configured.</span>
          <Link to="/settings" className="text-amber-700 underline font-medium">
            Go to Settings
          </Link>
        </div>
      )}

      {/* Confirm clear */}
      {confirmClear && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 flex items-center gap-3 text-sm">
          <span className="text-red-700">Clear conversation history?</span>
          <button
            onClick={() => {
              setMessages([]);
              setSaveNoteForIdx(null);
              setConfirmClear(false);
            }}
            className="px-2 py-0.5 text-xs text-white bg-red-600 rounded hover:bg-red-700"
          >
            Clear
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
            anthropicProvider={isAnthropicProvider}
            onChangeEntry={handleChangeEntry}
            onGlobalToggle={handleGlobalToggle}
          />
        )}

        {/* Right panel: chat */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* Message thread */}
          <div ref={threadRef} className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <p className="text-sm text-gray-400 text-center mt-8">
                {llmNotConfigured
                  ? 'Configure an LLM provider in Settings to start chatting.'
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
                      {/* Save as note */}
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

            {/* Thinking indicator */}
            {isPending && (
              <div className="flex justify-start">
                <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-500 animate-pulse">
                  Thinking...
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
                disabled={isPending || llmNotConfigured}
                placeholder={
                  llmNotConfigured
                    ? 'Configure LLM in Settings first'
                    : 'Type a message... (Ctrl+Enter to send)'
                }
                rows={3}
                className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
              />
              <button
                onClick={handleSend}
                disabled={isPending || !input.trim() || llmNotConfigured}
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
    </div>
  );
}
