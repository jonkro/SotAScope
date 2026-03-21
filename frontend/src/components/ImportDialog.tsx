import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { enrichDOI, enrichDOIBatch, searchImportCandidates } from '../api';
import type { SearchImportCandidate, TopicListOut, WorkOut } from '../types';
import SearchImportCandidateDialog from './SearchImportCandidateDialog';

type Tab = 'doi' | 'search';

function formatError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (
    msg.includes('SSL_CERTIFICATE_ERROR') ||
    msg.includes('CERTIFICATE_VERIFY_FAILED') ||
    msg.toLowerCase().includes('ssl certificate')
  ) {
    return (
      'SSL certificate error — this may be caused by a corporate proxy. ' +
      'You can disable SSL verification in Settings, or install your corporate CA certificate.'
    );
  }
  // Extract detail from JSON API error bodies
  try {
    const parsed = JSON.parse(msg);
    if (parsed.detail) return parsed.detail;
  } catch {
    // not JSON — fall through
  }
  return `Error: ${msg}`;
}

interface Props {
  onClose: () => void;
  /** When provided, shows a topic list assignment step after each successful import. */
  projectTopicLists?: TopicListOut[];
  /** Called for each (topicListId, workId) pair the user selects; handles assignment + enrichment. */
  onAddToTopicList?: (topicListId: number, workId: number) => void;
}

export default function ImportDialog({ onClose, projectTopicLists, onAddToTopicList }: Props) {
  const [tab, setTab] = useState<Tab>('doi');
  const [doiInput, setDoiInput] = useState('');
  const [result, setResult] = useState<string | null>(null);
  // Search tab state
  const [searchTitle, setSearchTitle] = useState('');
  const [searchAuthors, setSearchAuthors] = useState('');
  const [searchYear, setSearchYear] = useState('');
  const [searchCandidates, setSearchCandidates] = useState<SearchImportCandidate[] | null>(null);
  const [importedTitle, setImportedTitle] = useState<string | null>(null);

  // Project context: assign step
  const [showAssign, setShowAssign] = useState(false);
  const [assignWorkIds, setAssignWorkIds] = useState<number[]>([]);
  const [selectedTopicListIds, setSelectedTopicListIds] = useState<Set<number>>(new Set());

  const qc = useQueryClient();

  const inProjectContext = !!projectTopicLists && !!onAddToTopicList;

  function maybeTransitionToAssign(workIds: number[]) {
    if (inProjectContext && workIds.length > 0) {
      setAssignWorkIds(workIds);
      setShowAssign(true);
    }
  }

  const doiMutation = useMutation({
    mutationFn: async () => {
      const dois = doiInput
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (dois.length === 0) throw new Error('Enter at least one DOI');
      if (dois.length === 1) {
        const r = await enrichDOI(dois[0]);
        return {
          msg: `Imported: "${r.work.title}"${r.cached ? ' (cached)' : ''}`,
          workIds: [r.work.id],
        };
      }
      const r = await enrichDOIBatch(dois);
      const parts: string[] = [];
      if (r.results.length > 0) parts.push(`${r.results.length} imported`);
      if (r.errors.length > 0) parts.push(`${r.errors.length} errors`);
      if (r.errors.length > 0) {
        parts.push('\nErrors:\n' + r.errors.map((e) => `  ${e.doi}: ${e.error}`).join('\n'));
      }
      return { msg: parts.join(', '), workIds: r.results.map((res) => res.work.id) };
    },
    onSuccess: ({ msg, workIds }) => {
      setResult(msg);
      qc.invalidateQueries({ queryKey: ['works'] });
      maybeTransitionToAssign(workIds);
    },
    onError: (err) => setResult(formatError(err)),
  });

  const searchMutation = useMutation({
    mutationFn: () => {
      if (!searchTitle.trim()) throw new Error('Enter a title');
      const yearNum = searchYear ? parseInt(searchYear, 10) : undefined;
      return searchImportCandidates({
        title: searchTitle,
        authors: searchAuthors || undefined,
        year: yearNum,
      });
    },
    onSuccess: (data) => {
      setSearchCandidates(data.candidates);
      setImportedTitle(null);
    },
    onError: (err) => setResult(formatError(err)),
  });

  const isPending = doiMutation.isPending || searchMutation.isPending;

  function switchTab(t: Tab) {
    setTab(t);
    setResult(null);
    setImportedTitle(null);
  }

  function handleAssign() {
    for (const tlId of selectedTopicListIds) {
      for (const wId of assignWorkIds) {
        onAddToTopicList!(tlId, wId);
      }
    }
    onClose();
  }

  function toggleTopicList(tlId: number, checked: boolean) {
    setSelectedTopicListIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(tlId); else next.delete(tlId);
      return next;
    });
  }

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div
          className="bg-white rounded-lg shadow-lg w-full max-w-lg mx-4 flex flex-col max-h-[80vh]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-6 pt-5 pb-3">
            <h2 className="text-lg font-semibold text-gray-900">
              {showAssign ? 'Add to Topic Lists' : 'Import Works'}
            </h2>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
          </div>

          {showAssign ? (
            /* ---- Assign step ---- */
            <div className="flex-1 overflow-y-auto px-6 py-4">
              <p className="text-sm text-gray-600 mb-4">
                {assignWorkIds.length} paper{assignWorkIds.length !== 1 ? 's' : ''} imported.
                Select topic lists to add {assignWorkIds.length !== 1 ? 'them' : 'it'} to:
              </p>
              {projectTopicLists!.length === 0 ? (
                <p className="text-sm text-gray-400 italic">No topic lists in this project yet.</p>
              ) : (
                <div className="space-y-1">
                  {projectTopicLists!.map((tl) => (
                    <label
                      key={tl.id}
                      className="flex items-center gap-3 px-3 py-2 rounded hover:bg-gray-50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedTopicListIds.has(tl.id)}
                        onChange={(e) => toggleTopicList(tl.id, e.target.checked)}
                        className="rounded"
                      />
                      <span
                        className="w-3 h-3 rounded-sm shrink-0 border border-black/10"
                        style={{ backgroundColor: tl.color }}
                      />
                      <span className="text-sm text-gray-700">{tl.name}</span>
                    </label>
                  ))}
                </div>
              )}
              <div className="flex items-center gap-3 mt-6">
                <button
                  onClick={handleAssign}
                  disabled={selectedTopicListIds.size === 0}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                >
                  {selectedTopicListIds.size > 0
                    ? `Add to ${selectedTopicListIds.size} list${selectedTopicListIds.size !== 1 ? 's' : ''}`
                    : 'Add to selected lists'}
                </button>
                <button
                  onClick={onClose}
                  className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
                >
                  Skip
                </button>
              </div>
            </div>
          ) : (
            /* ---- Normal import step ---- */
            <>
              {/* Tabs */}
              <div className="flex border-b border-gray-200 px-6">
                <button
                  onClick={() => switchTab('doi')}
                  className={`px-4 py-2 text-sm font-medium border-b-2 ${
                    tab === 'doi' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
                  }`}
                >
                  DOI / arXiv
                </button>
                <button
                  onClick={() => switchTab('search')}
                  className={`px-4 py-2 text-sm font-medium border-b-2 ${
                    tab === 'search' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
                  }`}
                >
                  Search by Title
                </button>
              </div>

              <div className="flex-1 overflow-y-auto px-6 py-4">
                {tab === 'doi' && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      DOIs / arXiv IDs (one per line, or comma-separated)
                    </label>
                    <textarea
                      rows={5}
                      value={doiInput}
                      onChange={(e) => setDoiInput(e.target.value)}
                      placeholder="10.1145/1234567.1234568&#10;2301.12345&#10;arXiv:2402.03300v3"
                      className="w-full px-3 py-2 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400"
                    />
                    <p className="mt-1.5 text-xs text-gray-500">
                      DOIs and arXiv IDs can be freely mixed. The{' '}
                      <code className="font-mono bg-gray-100 px-0.5 rounded">arXiv:</code> prefix and
                      version suffixes (v1, v2, …) are stripped automatically.
                    </p>
                    <button
                      onClick={() => doiMutation.mutate()}
                      disabled={isPending}
                      className="mt-3 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                      {doiMutation.isPending ? 'Importing...' : 'Import'}
                    </button>
                  </div>
                )}

                {tab === 'search' && (
                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Title <span className="text-red-500">*</span>
                      </label>
                      <input
                        type="text"
                        value={searchTitle}
                        onChange={(e) => setSearchTitle(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter' && searchTitle.trim()) searchMutation.mutate(); }}
                        placeholder="e.g. Deep Residual Learning for Image Recognition"
                        className="w-full px-3 py-2 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Authors <span className="text-gray-400 font-normal">(optional)</span>
                      </label>
                      <input
                        type="text"
                        value={searchAuthors}
                        onChange={(e) => setSearchAuthors(e.target.value)}
                        placeholder="e.g. He Zhang Ren Sun"
                        className="w-full px-3 py-2 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Year <span className="text-gray-400 font-normal">(optional)</span>
                      </label>
                      <input
                        type="number"
                        value={searchYear}
                        onChange={(e) => setSearchYear(e.target.value)}
                        placeholder="e.g. 2016"
                        min={1900}
                        max={2100}
                        className="w-full px-3 py-2 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400"
                      />
                    </div>
                    {importedTitle && (
                      <div className="p-3 bg-green-50 border border-green-200 rounded text-sm text-green-700">
                        Imported: &ldquo;{importedTitle}&rdquo;
                      </div>
                    )}
                    <button
                      onClick={() => searchMutation.mutate()}
                      disabled={isPending || !searchTitle.trim()}
                      className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                      {searchMutation.isPending ? 'Searching...' : 'Search'}
                    </button>
                  </div>
                )}

                {result && (
                  <pre className="mt-4 p-3 bg-gray-50 border border-gray-200 rounded text-sm text-gray-700 whitespace-pre-wrap">
                    {result}
                  </pre>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {searchCandidates !== null && (
        <SearchImportCandidateDialog
          candidates={searchCandidates}
          onClose={() => setSearchCandidates(null)}
          onImported={(work: WorkOut) => {
            setSearchCandidates(null);
            setImportedTitle(work.title);
            maybeTransitionToAssign([work.id]);
          }}
        />
      )}
    </>
  );
}
