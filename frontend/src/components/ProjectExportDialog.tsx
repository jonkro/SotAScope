import { useState } from 'react';
import type { ExtractionSchema } from '../types';

interface Props {
  projectId: number;
  projectName: string;
  seedCount: number;
  schemas: ExtractionSchema[];
  onClose: () => void;
}

/** Mirrors the backend safe-name logic: keep alphanumeric and - _, replace rest with _. */
function toSafeFilename(name: string, ext: string): string {
  const safe = name.replace(/[^a-zA-Z0-9\-_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  return `${safe || 'project'}.${ext}`;
}

export default function ProjectExportDialog({
  projectId,
  projectName,
  seedCount,
  schemas,
  onClose,
}: Props) {
  const defaultFilename = toSafeFilename(projectName, 'zip');
  const [filename, setFilename] = useState(defaultFilename);
  const [includeFiles, setIncludeFiles] = useState(false);

  const handleExport = async () => {
    const url = includeFiles
      ? `/api/projects/${projectId}/export?include_files=true`
      : `/api/projects/${projectId}/export`;
    const res = await fetch(url);
    if (!res.ok) return;
    const blob = await res.blob();
    // Use a blob URL so the browser has no Content-Disposition header to
    // override the user-chosen filename set on the anchor's download attribute.
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename.trim() || defaultFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
    onClose();
  };

  const schemaCount = schemas.length;
  const resultCount = schemas.reduce(
    (sum, s) => sum + (s.columns?.length ?? 0),
    0,
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-gray-100">
          <h3 className="text-base font-semibold text-gray-900">Save Project</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4">
          {/* Filename input */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Filename</label>
            <input
              type="text"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              className="w-full px-2.5 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
              spellCheck={false}
            />
          </div>

          <p className="text-sm text-gray-600">
            The following will be packaged into a <code className="font-mono text-xs bg-gray-100 px-1 rounded">.zip</code> archive:
          </p>

          {/* Summary list */}
          <ul className="text-sm text-gray-700 space-y-1.5">
            <SummaryRow
              icon="📄"
              label="Seed papers"
              value={seedCount}
              suffix={seedCount === 1 ? 'paper' : 'papers'}
            />
            <SummaryRow
              icon="📋"
              label="Extraction schemas"
              value={schemaCount}
              suffix={schemaCount === 1 ? 'schema' : 'schemas'}
            />
            {resultCount > 0 && (
              <SummaryRow
                icon="🔬"
                label="Extraction columns"
                value={resultCount}
                suffix={resultCount === 1 ? 'column' : 'columns'}
              />
            )}
            <li className="flex items-start gap-2 text-gray-500">
              <span>📌</span>
              <span>Topic lists, venue tier overrides, chat sessions, work notes, citation edges</span>
            </li>
            <li className="flex items-start gap-2 text-gray-500">
              <span>📑</span>
              <span>BibTeX file for all seed papers (<code className="font-mono text-xs bg-gray-100 px-1 rounded">seeds.bib</code>)</span>
            </li>
          </ul>

          {/* Paper content checkbox */}
          <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={includeFiles}
                onChange={(e) => setIncludeFiles(e.target.checked)}
                className="mt-0.5 rounded"
              />
              <div>
                <span className="text-sm font-medium text-gray-700 block">
                  Include paper content (PDFs / extracted text)
                </span>
                <span className="text-xs text-gray-500 block mt-0.5">
                  Only papers with uploaded PDFs are included. Makes the archive larger.
                </span>
              </div>
            </label>
          </div>

          <p className="text-xs text-gray-400">
            Candidates (non-seed neighbors) are not saved — the importer will
            re-discover them by re-running enrichment on the seeds.
          </p>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-5 py-4 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function SummaryRow({
  icon,
  label,
  value,
  suffix,
}: {
  icon: string;
  label: string;
  value: number;
  suffix: string;
}) {
  return (
    <li className="flex items-center gap-2">
      <span>{icon}</span>
      <span className="text-gray-500">{label}:</span>
      <span className="font-medium">
        {value} {suffix}
      </span>
    </li>
  );
}
