import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { enrichDOI, enrichDOIBatch, importBibtex } from '../api';

type Tab = 'doi' | 'bibtex';

export default function ImportDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('doi');
  const [doiInput, setDoiInput] = useState('');
  const [bibtexInput, setBibtexInput] = useState('');
  const [result, setResult] = useState<string | null>(null);
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
    onError: (err) => setResult(`Error: ${err instanceof Error ? err.message : String(err)}`),
  });

  const bibtexMutation = useMutation({
    mutationFn: () => {
      if (!bibtexInput.trim()) throw new Error('Paste BibTeX content');
      return importBibtex(bibtexInput);
    },
    onSuccess: (data) => {
      setResult(`Imported ${data.imported} works, skipped ${data.skipped}`);
      qc.invalidateQueries({ queryKey: ['works'] });
    },
    onError: (err) => setResult(`Error: ${err instanceof Error ? err.message : String(err)}`),
  });

  const isPending = doiMutation.isPending || bibtexMutation.isPending;

  return (
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
            onClick={() => { setTab('doi'); setResult(null); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 ${
              tab === 'doi' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            DOI Import
          </button>
          <button
            onClick={() => { setTab('bibtex'); setResult(null); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 ${
              tab === 'bibtex' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            BibTeX Import
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
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
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
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={() => bibtexMutation.mutate()}
                disabled={isPending}
                className="mt-3 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {bibtexMutation.isPending ? 'Importing...' : 'Import BibTeX'}
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
  );
}
