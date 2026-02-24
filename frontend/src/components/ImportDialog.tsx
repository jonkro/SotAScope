import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { enrichDOI, enrichDOIBatch, importBibtex, resolveDOIBatch, searchImportCandidates } from '../api';
import type { DOIResolutionResult, SearchImportCandidate } from '../types';
import DOIResolutionDialog from './DOIResolutionDialog';
import SearchImportCandidateDialog from './SearchImportCandidateDialog';

type Tab = 'doi' | 'bibtex' | 'search';

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

export default function ImportDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('doi');
  const [doiInput, setDoiInput] = useState('');
  const [bibtexInput, setBibtexInput] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [doiResolutionResults, setDoiResolutionResults] = useState<DOIResolutionResult[] | null>(null);
  const [isResolving, setIsResolving] = useState(false);

  // Search tab state
  const [searchTitle, setSearchTitle] = useState('');
  const [searchAuthors, setSearchAuthors] = useState('');
  const [searchYear, setSearchYear] = useState('');
  const [searchCandidates, setSearchCandidates] = useState<SearchImportCandidate[] | null>(null);
  const [importedTitle, setImportedTitle] = useState<string | null>(null);

  const qc = useQueryClient();

  const doiMutation = useMutation({
    mutationFn: async () => {
      const dois = doiInput
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (dois.length === 0) throw new Error('Enter at least one DOI');
      if (dois.length === 1) {
        const r = await enrichDOI(dois[0]);
        return `Imported: "${r.work.title}"${r.cached ? ' (cached)' : ''}`;
      }
      const r = await enrichDOIBatch(dois);
      const parts: string[] = [];
      if (r.results.length > 0) parts.push(`${r.results.length} imported`);
      if (r.errors.length > 0) parts.push(`${r.errors.length} errors`);
      if (r.errors.length > 0) {
        parts.push('\nErrors:\n' + r.errors.map((e) => `  ${e.doi}: ${e.error}`).join('\n'));
      }
      return parts.join(', ');
    },
    onSuccess: (msg) => {
      setResult(msg);
      qc.invalidateQueries({ queryKey: ['works'] });
    },
    onError: (err) => setResult(formatError(err)),
  });

  const bibtexMutation = useMutation({
    mutationFn: () => {
      if (!bibtexInput.trim()) throw new Error('Paste BibTeX content');
      return importBibtex(bibtexInput);
    },
    onSuccess: async (data) => {
      let msg = `Imported ${data.imported} works, skipped ${data.skipped}`;
      qc.invalidateQueries({ queryKey: ['works'] });

      if (data.needs_doi_resolution.length > 0) {
        setIsResolving(true);
        setResult(msg + '\nResolving DOIs...');
        try {
          const results = await resolveDOIBatch(data.needs_doi_resolution);
          const autoResolved = results.filter((r) => r.auto_resolved_doi);
          const needsConfirm = results.filter((r) => r.candidates.length > 0 && !r.auto_resolved_doi);

          msg += `\n${autoResolved.length} DOIs auto-resolved`;
          if (needsConfirm.length > 0) {
            msg += `, ${needsConfirm.length} need confirmation`;
          }
          setResult(msg);
          qc.invalidateQueries({ queryKey: ['works'] });

          if (needsConfirm.length > 0) {
            setDoiResolutionResults(results);
          }
        } catch (err) {
          msg += `\nDOI resolution error: ${formatError(err)}`;
          setResult(msg);
        } finally {
          setIsResolving(false);
        }
      } else {
        setResult(msg);
      }
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

  const isPending = doiMutation.isPending || bibtexMutation.isPending || isResolving || searchMutation.isPending;

  function switchTab(t: Tab) {
    setTab(t);
    setResult(null);
    setImportedTitle(null);
  }

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
        <div
          className="bg-white rounded-lg shadow-lg w-full max-w-lg mx-4 flex flex-col max-h-[80vh]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-6 pt-5 pb-3">
            <h2 className="text-lg font-semibold text-gray-900">Import Works</h2>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
          </div>

          {/* Tabs */}
          <div className="flex border-b border-gray-200 px-6">
            <button
              onClick={() => switchTab('doi')}
              className={`px-4 py-2 text-sm font-medium border-b-2 ${
                tab === 'doi' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              DOI Import
            </button>
            <button
              onClick={() => switchTab('bibtex')}
              className={`px-4 py-2 text-sm font-medium border-b-2 ${
                tab === 'bibtex' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              BibTeX Import
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
                  DOIs (one per line, or comma-separated)
                </label>
                <textarea
                  rows={5}
                  value={doiInput}
                  onChange={(e) => setDoiInput(e.target.value)}
                  placeholder="10.1145/1234567.1234568&#10;10.1109/TNET.2020.1234567"
                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400"
                />
                <button
                  onClick={() => doiMutation.mutate()}
                  disabled={isPending}
                  className="mt-3 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                >
                  {doiMutation.isPending ? 'Importing...' : 'Import DOIs'}
                </button>
              </div>
            )}

            {tab === 'bibtex' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">BibTeX content</label>
                <textarea
                  rows={8}
                  value={bibtexInput}
                  onChange={(e) => setBibtexInput(e.target.value)}
                  placeholder="@article{key, ...}"
                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400"
                />
                <button
                  onClick={() => bibtexMutation.mutate()}
                  disabled={isPending}
                  className="mt-3 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                >
                  {bibtexMutation.isPending ? 'Importing...' : isResolving ? 'Resolving DOIs...' : 'Import BibTeX'}
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
                    placeholder="e.g. Attention Is All You Need"
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
                    placeholder="e.g. Vaswani Shazeer"
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
                    placeholder="e.g. 2017"
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
        </div>
      </div>

      {doiResolutionResults && (
        <DOIResolutionDialog
          results={doiResolutionResults}
          onClose={() => {
            setDoiResolutionResults(null);
            qc.invalidateQueries({ queryKey: ['works'] });
          }}
        />
      )}

      {searchCandidates !== null && (
        <SearchImportCandidateDialog
          candidates={searchCandidates}
          onClose={() => setSearchCandidates(null)}
          onImported={(title) => {
            setSearchCandidates(null);
            setImportedTitle(title);
          }}
        />
      )}
    </>
  );
}
