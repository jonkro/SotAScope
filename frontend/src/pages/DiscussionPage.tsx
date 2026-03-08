import { useState, useRef, useEffect, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
import {
  getChatSession,
  postLLMChat,
  createWorkNote,
  getExtractionSchemas,
  getExtractionSchema,
  createColumnFromProposal,
  createSchemaFromDiscussion,
  patchChatSession,
} from '../api';
import type { ChatMessage, ChatSessionOut, ExtractionSchema, TopicListOut, WorkPDFOut } from '../types';
import { parseProposals } from '../utils/proposalParser';
import type { ColumnProposal } from '../utils/proposalParser';
import { ColumnProposalCard, UserCancelledError } from '../components/ColumnProposalCard';

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
  // Width and border-r are provided by the parent wrapper — this component
  // only manages its own inner layout.
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
    <div className="flex flex-col flex-1 min-h-0 bg-gray-50">
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
// Assistant message — renders plain text or proposal cards depending on mode
// ---------------------------------------------------------------------------

function AssistantMessage({
  content,
  showProposals,
  saveNoteOpen,
  contextWorkIds,
  projectId,
  modelId,
  onSaveNote,
  onSaveNoteDone,
  onAcceptProposal,
  onRejectProposal,
}: {
  content: string;
  showProposals: boolean;
  saveNoteOpen: boolean;
  contextWorkIds: { work_id: number; title: string }[];
  projectId: number | null;
  modelId: string;
  onSaveNote: () => void;
  onSaveNoteDone: () => void;
  onAcceptProposal: (proposal: ColumnProposal) => Promise<void>;
  onRejectProposal: () => void;
}) {
  // Only parse when in extraction_schema mode — skip for general discussion
  const segments = showProposals ? parseProposals(content) : null;
  const hasProposals = segments?.some((s) => s.type === 'proposal') ?? false;

  return (
    <div className="flex justify-start">
      <div className={hasProposals ? 'w-full max-w-2xl' : 'max-w-[75%]'}>
        {hasProposals && segments ? (
          <div className="space-y-2">
            {segments.map((seg, i) =>
              seg.type === 'text' ? (
                <div
                  key={i}
                  className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-800 whitespace-pre-wrap"
                >
                  {seg.content}
                </div>
              ) : (
                <ColumnProposalCard
                  key={i}
                  proposal={seg.proposal}
                  onAccept={onAcceptProposal}
                  onReject={onRejectProposal}
                />
              ),
            )}
          </div>
        ) : (
          <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-800 whitespace-pre-wrap">
            {content}
          </div>
        )}

        {saveNoteOpen ? (
          <SaveNoteForm
            content={content}
            contextWorkIds={contextWorkIds}
            projectId={projectId}
            modelId={modelId}
            onDone={onSaveNoteDone}
          />
        ) : (
          <button
            onClick={onSaveNote}
            className="text-[10px] text-gray-400 hover:text-blue-600 mt-1 underline"
          >
            Save as note
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// New schema dialog — shown when first Accept fires with no existing schema
// ---------------------------------------------------------------------------

function NewSchemaDialog({
  isLoading,
  errorMsg,
  onSubmit,
  onCancel,
}: {
  isLoading: boolean;
  errorMsg: string;
  onSubmit: (title: string, description: string) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-sm mx-4">
        <div className="px-4 py-3 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">Name the new schema</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            This column will be the first entry in a new extraction schema.
          </p>
        </div>
        <div className="px-4 py-3 space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Schema title <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && title.trim()) onSubmit(title.trim(), description.trim());
              }}
              placeholder="e.g. Methodology review"
              autoFocus
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Description (optional)
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this schema for?"
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          {errorMsg && (
            <p className="text-xs text-red-600 bg-red-50 px-2 py-1 rounded border border-red-200">
              {errorMsg}
            </p>
          )}
        </div>
        <div className="px-4 py-3 border-t border-gray-200 flex gap-2 justify-end">
          <button
            onClick={onCancel}
            disabled={isLoading}
            className="px-3 py-1.5 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={() => onSubmit(title.trim(), description.trim())}
            disabled={!title.trim() || isLoading}
            className="px-3 py-1.5 text-xs text-white bg-indigo-600 rounded hover:bg-indigo-700 disabled:opacity-50"
          >
            {isLoading ? 'Creating…' : 'Create & Accept'}
          </button>
        </div>
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

type DiscussionMode = 'papers' | 'extraction_schema';

export default function DiscussionPage() {
  const { workId: workIdParam, projectId: projectIdParam } = useParams<{
    workId?: string;
    projectId?: string;
  }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

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

  // Extraction schemas for the schema dropdown (project mode only)
  const { data: schemas = [], isLoading: schemasLoading } = useQuery({
    queryKey: ['extraction', 'schemas', projectId],
    queryFn: () => getExtractionSchemas(projectId!),
    enabled: !isLibraryMode && projectId != null,
  });

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
  // Discussion mode (Papers vs Extraction Schema)
  // ---------------------------------------------------------------------------

  // localStorage keys for persisting mode + schema selection.
  const modeKey = !isLibraryMode && projectId != null
    ? `litexplorer:discuss:project:${projectId}:mode`
    : null;
  const contextIdKey = !isLibraryMode && projectId != null
    ? `litexplorer:discuss:project:${projectId}:context_id`
    : null;

  const [discussionMode, setDiscussionMode] = useState<DiscussionMode>('papers');
  // context_id: schema ID when in extraction_schema mode, null otherwise.
  const [contextId, setContextId] = useState<number | null>(null);

  // Schema detail for the summary card (loaded when an existing schema is selected)
  const { data: selectedSchemaDetail, isLoading: schemaDetailLoading } = useQuery({
    queryKey: ['extraction', 'schema', contextId],
    queryFn: () => getExtractionSchema(contextId!),
    enabled: !isLibraryMode && contextId != null && discussionMode === 'extraction_schema',
  });

  // Dropdown value derived from mode + contextId:
  //   ""      = General discussion (papers mode)
  //   "new"   = New schema (extraction_schema, contextId=null)
  //   "{id}"  = Existing schema (extraction_schema, contextId=id)
  const schemaDropdownValue =
    discussionMode === 'papers' ? '' : contextId != null ? String(contextId) : 'new';

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

    // Read stored mode + context_id; fall back to 'papers' / null.
    const storedMode: DiscussionMode = modeKey
      ? ((localStorage.getItem(modeKey) as DiscussionMode | null) ?? 'papers')
      : 'papers';
    const storedContextId: number | null = contextIdKey
      ? (() => { const v = localStorage.getItem(contextIdKey); return v != null ? Number(v) : null; })()
      : null;

    getOrCreateAuto.mutate(
      { work_id: workId, project_id: projectId, context_type: storedMode, context_id: storedContextId },
      {
        onSuccess: (session) => {
          // Restore mode and context_id from the session
          const restoredMode = (session.context_type as DiscussionMode) || 'papers';
          const restoredContextId = session.context_id ?? null;
          setDiscussionMode(restoredMode);
          setContextId(restoredContextId);
          if (modeKey) localStorage.setItem(modeKey, restoredMode);
          if (contextIdKey) {
            if (restoredContextId != null) localStorage.setItem(contextIdKey, String(restoredContextId));
            else localStorage.removeItem(contextIdKey);
          }

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
  // New-schema dialog state (for Accept on a "New schema" discussion)
  // ---------------------------------------------------------------------------

  // When context_id is null and the user accepts a proposal, we show a dialog
  // asking for a schema title.  The promise callbacks let the card await the result.
  const [newSchemaDialog, setNewSchemaDialog] = useState<{
    proposal: ColumnProposal;
    resolve: () => void;
    reject: (err: Error) => void;
  } | null>(null);
  const [newSchemaLoading, setNewSchemaLoading] = useState(false);
  const [newSchemaError, setNewSchemaError] = useState('');

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

  // Lock the paper selection and schema dropdown once the discussion has started; unlock on New Chat.
  const discussionActive = messages.some((m) => m.role === 'user' || m.role === 'assistant');

  const chatDisabled =
    chatMutation.isPending ||
    llmNotConfigured ||
    libraryModeNoContent;

  // Prompt preview
  const [showPromptPreview, setShowPromptPreview] = useState(false);

  const buildChatPromptPreview = (): string => {
    const parts: string[] = [];

    parts.push('━━━ System ━━━');
    if (discussionMode === 'extraction_schema') {
      if (contextId != null && selectedSchemaDetail) {
        parts.push(`[Schema discussion prompt for: "${selectedSchemaDetail.title}"]`);
      } else {
        parts.push('[Schema discussion prompt (designing a new schema)]');
      }
      parts.push('(Instructs the AI to help design extraction schema columns.)');
    } else {
      parts.push('You are a research assistant helping analyze academic literature.');
    }
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
    : discussionMode === 'extraction_schema'
    ? 'Describe the schema you want to build… (Ctrl+Enter to send)'
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

  // ---------------------------------------------------------------------------
  // Proposal Accept / Reject handlers
  // ---------------------------------------------------------------------------

  /**
   * Accept a column proposal from the LLM.
   *
   * - Existing schema (contextId set): POST the column directly, then invalidate.
   * - New schema (contextId null): open the NewSchemaDialog and wait for the user
   *   to provide a title. Resolves/rejects the card's promise accordingly.
   */
  const handleAcceptProposal = (proposal: ColumnProposal): Promise<void> => {
    if (contextId != null) {
      // Existing schema — add column directly
      return createColumnFromProposal(contextId, proposal).then(() => {
        queryClient.invalidateQueries({ queryKey: ['extraction', 'schema', contextId] });
        queryClient.invalidateQueries({ queryKey: ['extraction', 'schemas', projectId] });
      });
    }
    // New schema — open dialog and expose resolve/reject to the dialog handler
    return new Promise<void>((resolve, reject) => {
      setNewSchemaError('');
      setNewSchemaDialog({ proposal, resolve, reject });
    });
  };

  /** Called when the user submits the new-schema title form. */
  const handleCreateSchemaAndColumn = async (title: string, description: string) => {
    if (!newSchemaDialog) return;
    setNewSchemaLoading(true);
    setNewSchemaError('');
    try {
      const schema = await createSchemaFromDiscussion(title, description || null, projectId);
      await createColumnFromProposal(schema.id, newSchemaDialog.proposal);

      // Promote context from "new schema" to the real schema ID
      setContextId(schema.id);
      if (contextIdKey) localStorage.setItem(contextIdKey, String(schema.id));
      if (modeKey) localStorage.setItem(modeKey, 'extraction_schema');

      // Patch the auto-session so session-restore lands on the correct scope
      if (autoSessionId != null) {
        await patchChatSession(autoSessionId, { context_id: schema.id });
      }

      // Refresh caches so the dropdown and sidebar reflect the new schema
      queryClient.invalidateQueries({ queryKey: ['extraction', 'schemas', projectId] });
      queryClient.invalidateQueries({ queryKey: ['extraction', 'schema', schema.id] });

      newSchemaDialog.resolve();
      setNewSchemaDialog(null);
    } catch (e) {
      let msg = e instanceof Error ? e.message : String(e);
      try { const p = JSON.parse(msg) as { detail?: string }; msg = p.detail ?? msg; } catch { /* not JSON */ }
      setNewSchemaError(msg);
      // Don't close the dialog on API error — let user retry or cancel
    } finally {
      setNewSchemaLoading(false);
    }
  };

  /** Called when the user cancels the new-schema dialog. */
  const handleCancelNewSchema = () => {
    if (!newSchemaDialog) return;
    newSchemaDialog.reject(new UserCancelledError());
    setNewSchemaDialog(null);
    setNewSchemaError('');
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

  // Unified schema dropdown change handler.
  // value: "" = General discussion (papers), "new" = new schema, "{id}" = existing schema.
  // Disabled once discussionActive.
  const handleSchemaDropdownChange = (value: string) => {
    if (discussionActive) return;
    let newMode: DiscussionMode;
    let newContextId: number | null;
    if (value === '') {
      newMode = 'papers';
      newContextId = null;
    } else if (value === 'new') {
      newMode = 'extraction_schema';
      newContextId = null;
    } else {
      newMode = 'extraction_schema';
      newContextId = Number(value);
    }
    if (newMode === discussionMode && newContextId === contextId) return;
    setMessages([]);
    setSaveNoteForIdx(null);
    if (selKey) localStorage.removeItem(selKey);
    restoredSelectionRef.current = null;
    setDiscussionMode(newMode);
    setContextId(newContextId);
    if (modeKey) localStorage.setItem(modeKey, newMode);
    if (contextIdKey) {
      if (newContextId != null) localStorage.setItem(contextIdKey, String(newContextId));
      else localStorage.removeItem(contextIdKey);
    }
    getOrCreateAuto.mutate(
      { work_id: workId, project_id: projectId, context_type: newMode, context_id: newContextId },
      {
        onSuccess: (session) => {
          setAutoSessionId(session.id);
          if (session.messages.length > 0) {
            setMessages(
              session.messages.map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content }))
            );
            // Restore paper selection if we have a saved one for this scope.
            if (selKey) {
              const stored = localStorage.getItem(selKey);
              if (stored) {
                const storedIds = new Set<number>(JSON.parse(stored) as number[]);
                restoredSelectionRef.current = storedIds;
                setEntries((prev) =>
                  prev.map((e) => (e.pdfsLoaded ? { ...e, included: storedIds.has(e.work_id) } : e))
                );
              }
            }
          }
        },
      },
    );
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
        {/* Left panel: schema dropdown + paper selector (project mode only) */}
        {!isLibraryMode && (
          <div
            className="flex flex-col h-full border-r border-gray-200 shrink-0"
            style={{ minWidth: 260, maxWidth: 320 }}
          >
            {/* Compact schema / focus dropdown */}
            <div className="px-3 py-2 border-b border-gray-200 bg-white shrink-0">
              <label className="block text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1">
                Discussion focus
              </label>
              {discussionActive && (
                <p className="text-[10px] text-amber-600 mb-1">Locked · start a new chat to change</p>
              )}
              <select
                value={schemaDropdownValue}
                onChange={(e) => handleSchemaDropdownChange(e.target.value)}
                disabled={discussionActive || schemasLoading}
                className="w-full border border-gray-200 rounded px-2 py-1 text-xs bg-white focus:outline-none focus:ring-1 focus:ring-indigo-400 disabled:bg-gray-50 disabled:cursor-not-allowed"
              >
                <option value="">General discussion</option>
                {schemas.map((s: ExtractionSchema) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.title}
                  </option>
                ))}
                <option value="new">New schema</option>
              </select>
            </div>

            {/* Schema summary (only when an existing schema is selected) */}
            {discussionMode === 'extraction_schema' && (
              contextId != null ? (
                <div className="px-3 py-2 border-b border-gray-100 bg-indigo-50 shrink-0">
                  {schemaDetailLoading ? (
                    <p className="text-[10px] text-gray-400">Loading…</p>
                  ) : selectedSchemaDetail ? (
                    <div className="space-y-0.5">
                      <p className="text-xs font-medium text-indigo-800">{selectedSchemaDetail.title}</p>
                      {selectedSchemaDetail.description && (
                        <p className="text-[10px] text-indigo-600 leading-relaxed">
                          {selectedSchemaDetail.description.length > 80
                            ? selectedSchemaDetail.description.slice(0, 77) + '…'
                            : selectedSchemaDetail.description}
                        </p>
                      )}
                      {selectedSchemaDetail.columns.length > 0 && (
                        <p className="text-[10px] text-indigo-500">
                          {selectedSchemaDetail.columns.length} column{selectedSchemaDetail.columns.length !== 1 ? 's' : ''}
                          {': '}
                          {selectedSchemaDetail.columns.map((c) => c.name).join(', ').slice(0, 80)}
                        </p>
                      )}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="px-3 py-2 border-b border-gray-100 bg-amber-50 shrink-0">
                  <p className="text-[10px] text-amber-700 italic leading-relaxed">
                    Designing a new schema. Accepted column proposals will be saved to a new schema.
                  </p>
                </div>
              )
            )}

            {/* Paper selector — always visible */}
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
          </div>
        )}

        {/* Chat panel */}
        <div className="flex flex-col flex-1 min-w-0">

          {/* Context annotation — schema and/or papers being discussed */}
          {(isLibraryMode ? !!singleWork : true) && (
            <div className="px-4 py-2 border-b border-slate-200 bg-slate-50 text-xs text-gray-600 shrink-0 flex flex-wrap gap-x-3 gap-y-0.5 items-center">
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
              ) : !isLibraryMode ? (
                <>
                  {discussionMode === 'extraction_schema' && (
                    <span className="text-indigo-600 font-medium">
                      {contextId != null && selectedSchemaDetail
                        ? `Schema: ${selectedSchemaDetail.title}`
                        : 'New schema design'}
                    </span>
                  )}
                  {includedPapers.length > 0 ? (
                    <span>
                      <span className="font-medium text-gray-700">
                        {includedPapers.length} paper{includedPapers.length !== 1 ? 's' : ''}:
                      </span>
                      {' '}
                      {entries
                        .filter((e) => e.included)
                        .map((e) => `${e.title}${e.year ? ` (${e.year})` : ''}`)
                        .join(' · ')
                        .slice(0, 200) +
                        (entries.filter((e) => e.included).map((e) => `${e.title}${e.year ? ` (${e.year})` : ''}`).join(' · ').length > 200 ? '…' : '')}
                    </span>
                  ) : (
                    <span className="text-gray-400 italic">No papers selected</span>
                  )}
                </>
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
                  : discussionMode === 'extraction_schema'
                  ? 'Describe the extraction schema you want to build, or ask about selected papers.'
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
                  <AssistantMessage
                    content={msg.content}
                    showProposals={discussionMode === 'extraction_schema'}
                    saveNoteOpen={saveNoteForIdx === idx}
                    contextWorkIds={contextWorkIds}
                    projectId={projectId}
                    modelId={llmModelId}
                    onSaveNote={() => setSaveNoteForIdx(idx)}
                    onSaveNoteDone={() => setSaveNoteForIdx(null)}
                    onAcceptProposal={handleAcceptProposal}
                    onRejectProposal={() => { /* purely visual — card manages its own state */ }}
                  />
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

      {/* New schema dialog — shown when Accept fires with no existing schema */}
      {newSchemaDialog !== null && (
        <NewSchemaDialog
          isLoading={newSchemaLoading}
          errorMsg={newSchemaError}
          onSubmit={handleCreateSchemaAndColumn}
          onCancel={handleCancelNewSchema}
        />
      )}
    </div>
  );
}
