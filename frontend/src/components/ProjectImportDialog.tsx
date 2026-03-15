import { useRef, useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useImportProject, useResolveImport } from '../hooks/useProjects';
import type {
  ImportResult,
  MergeDecisions,
  SchemaDecision,
  ImportResolveRequest,
} from '../types';

interface Props {
  onClose: () => void;
}

const TIER_LABELS: Record<number, string> = {
  1: 'Tier 1 (top)',
  2: 'Tier 2',
  3: 'Tier 3 (ignore)',
};

export function ProjectImportDialog({ onClose }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Collision-resolution state
  const [collisionAction, setCollisionAction] = useState<'merge' | 'rename'>('merge');
  const [newName, setNewName] = useState('');
  const [schemaDecisions, setSchemaDecisions] = useState<Record<number, SchemaDecision>>({});
  const [venueTierDecisions, setVenueTierDecisions] = useState<Record<number, number>>({});

  // Final resolved project
  const [resolvedProjectId, setResolvedProjectId] = useState<number | null>(null);

  const importMut = useImportProject();
  const resolveMut = useResolveImport();

  // Initialise conflict decisions when import result arrives
  useEffect(() => {
    if (!importResult?.merge_preview) return;

    const preview = importResult.merge_preview;
    const initialSchema: Record<number, SchemaDecision> = {};
    for (const conflict of preview.schema_conflicts) {
      initialSchema[conflict.source_schema_id] = {
        action: 'rename',
        new_name: `${conflict.source_schema_name} (imported)`,
      };
    }
    setSchemaDecisions(initialSchema);

    const initialVenue: Record<number, number> = {};
    for (const conflict of preview.venue_tier_conflicts) {
      initialVenue[conflict.venue_id] = conflict.target_tier;
    }
    setVenueTierDecisions(initialVenue);

    setNewName(importResult.project_name + ' (imported)');
  }, [importResult]);

  async function handleUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setError(null);
    setImporting(true);
    importMut.mutate(file, {
      onSuccess: (result) => {
        setImportResult(result);
        setImporting(false);
        // If no collision and no ambiguous: done
        if (!result.needs_project_decision && result.project_id) {
          setResolvedProjectId(result.project_id);
        }
      },
      onError: (err) => {
        setError(err instanceof Error ? err.message : 'Import failed');
        setImporting(false);
      },
    });
  }

  function handleBulkBestTier() {
    if (!importResult?.merge_preview) return;
    const updated: Record<number, number> = {};
    for (const conflict of importResult.merge_preview.venue_tier_conflicts) {
      updated[conflict.venue_id] = Math.min(conflict.source_tier, conflict.target_tier);
    }
    setVenueTierDecisions(updated);
  }

  function handleResolve() {
    if (!importResult?.temp_project_id) return;
    setError(null);

    const body: ImportResolveRequest =
      collisionAction === 'rename'
        ? { action: 'rename', new_name: newName }
        : {
            action: 'merge',
            target_project_id: importResult.existing_project_id ?? undefined,
            merge_decisions: buildMergeDecisions(),
          };

    resolveMut.mutate(
      { tempId: importResult.temp_project_id, body },
      {
        onSuccess: (project) => {
          setResolvedProjectId(project.id);
        },
        onError: (err) => {
          setError(err instanceof Error ? err.message : 'Resolution failed');
        },
      },
    );
  }

  function buildMergeDecisions(): MergeDecisions {
    const preview = importResult?.merge_preview;
    const filteredSchema: Record<number, SchemaDecision> = {};
    if (preview) {
      for (const conflict of preview.schema_conflicts) {
        const d = schemaDecisions[conflict.source_schema_id];
        if (d) filteredSchema[conflict.source_schema_id] = d;
      }
    }
    const filteredVenue: Record<number, number> = {};
    if (preview) {
      for (const conflict of preview.venue_tier_conflicts) {
        const chosen = venueTierDecisions[conflict.venue_id];
        if (chosen !== undefined && chosen !== conflict.target_tier) {
          filteredVenue[conflict.venue_id] = chosen;
        }
      }
    }
    return { schema_decisions: filteredSchema, venue_tier_decisions: filteredVenue };
  }

  const isResolvePending = resolveMut.isPending;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-base font-semibold text-gray-900">Import Project</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">

          {/* ---- Success: done ---- */}
          {resolvedProjectId !== null ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-green-700 bg-green-50 border border-green-200 rounded p-3">
                <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <span className="text-sm font-medium">Project imported successfully.</span>
              </div>
              {importResult && (
                <p className="text-sm text-gray-600">
                  {importResult.works_created} work{importResult.works_created !== 1 ? 's' : ''}{' '}
                  created,{' '}
                  {importResult.works_matched} work{importResult.works_matched !== 1 ? 's' : ''}{' '}
                  matched from existing library.
                  {' '}Auto-enrichment has been scheduled for seed works.
                </p>
              )}
              <Link
                to={`/projects/${resolvedProjectId}`}
                onClick={onClose}
                className="inline-block px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
              >
                Open project
              </Link>
            </div>
          ) : (
            <>
              {/* ---- Step 1: File upload (only if no result yet) ---- */}
              {importResult === null && (
                <div className="space-y-3">
                  <p className="text-sm text-gray-600">
                    Select a <code className="bg-gray-100 px-1 rounded">.zip</code> archive
                    exported from LitExplorer.
                  </p>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".zip"
                    className="block w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border file:border-gray-300 file:text-sm file:bg-white file:text-gray-700 hover:file:bg-gray-50"
                  />
                </div>
              )}

              {/* ---- Step 2: Import results ---- */}
              {importResult !== null && (
                <div className="space-y-4">
                  {/* Work counts */}
                  <div className="bg-blue-50 border border-blue-200 rounded p-3 text-sm text-blue-800">
                    <p>
                      <strong>{importResult.works_matched}</strong> work
                      {importResult.works_matched !== 1 ? 's' : ''} matched from your existing
                      library.{' '}
                      <strong>{importResult.works_created}</strong> new work
                      {importResult.works_created !== 1 ? 's' : ''} added.
                    </p>
                  </div>

                  {/* Ambiguous matches */}
                  {importResult.ambiguous_matches.length > 0 && (
                    <section>
                      <h3 className="text-sm font-semibold text-amber-800 mb-2">
                        {importResult.ambiguous_matches.length} possible duplicate
                        {importResult.ambiguous_matches.length !== 1 ? 's' : ''} detected
                      </h3>
                      <p className="text-xs text-gray-500 mb-2">
                        These works match by title+year but the first author could not be
                        confirmed. New works were created. Use the{' '}
                        <strong>Library → Sanitize</strong> tool to review and merge duplicates.
                      </p>
                      <ul className="space-y-1 text-xs text-gray-700 border border-amber-200 rounded p-2 bg-amber-50 max-h-36 overflow-y-auto">
                        {importResult.ambiguous_matches.map((m, i) => (
                          <li key={i} className="truncate">
                            <span className="font-medium">{m.incoming.title}</span>
                            {m.incoming.year && (
                              <span className="text-gray-500"> ({m.incoming.year})</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}

                  {/* Name collision: decision UI */}
                  {importResult.needs_project_decision && (
                    <section className="space-y-3">
                      <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-800">
                        A project named{' '}
                        <strong>&ldquo;{importResult.project_name}&rdquo;</strong> already exists.
                        Choose how to proceed:
                      </div>

                      <div className="space-y-2">
                        <label className="flex items-start gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name="collisionAction"
                            value="merge"
                            checked={collisionAction === 'merge'}
                            onChange={() => setCollisionAction('merge')}
                            className="mt-0.5"
                          />
                          <span className="text-sm text-gray-700">
                            Merge into the existing project
                          </span>
                        </label>
                        <label className="flex items-start gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name="collisionAction"
                            value="rename"
                            checked={collisionAction === 'rename'}
                            onChange={() => setCollisionAction('rename')}
                            className="mt-0.5"
                          />
                          <span className="text-sm text-gray-700">
                            Keep as a separate project with a new name
                          </span>
                        </label>
                      </div>

                      {/* Rename: name input */}
                      {collisionAction === 'rename' && (
                        <input
                          type="text"
                          value={newName}
                          onChange={(e) => setNewName(e.target.value)}
                          placeholder="New project name…"
                          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                        />
                      )}

                      {/* Merge: show preview + conflict controls */}
                      {collisionAction === 'merge' && importResult.merge_preview && (() => {
                        const preview = importResult.merge_preview!;
                        return (
                          <div className="border border-gray-200 rounded p-3 space-y-4">
                            {/* Topic lists */}
                            {preview.topic_list_merges.length > 0 && (
                              <section>
                                <h4 className="text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wide">
                                  Topic lists
                                </h4>
                                <ul className="space-y-1 text-sm text-gray-700">
                                  {preview.topic_list_merges.map((tl) => (
                                    <li key={tl.source_topic_list_id} className="flex items-center gap-2">
                                      <span>{tl.source_topic_list_name}</span>
                                      {tl.action === 'merge' ? (
                                        <span className="text-xs text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                                          merged into existing
                                        </span>
                                      ) : (
                                        <span className="text-xs text-green-600 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded">
                                          copied as-is
                                        </span>
                                      )}
                                    </li>
                                  ))}
                                </ul>
                              </section>
                            )}

                            {/* Schema conflicts */}
                            {preview.schema_conflicts.length > 0 && (
                              <section>
                                <h4 className="text-xs font-semibold text-gray-700 mb-2 uppercase tracking-wide">
                                  Schema conflicts
                                </h4>
                                <div className="space-y-3">
                                  {preview.schema_conflicts.map((conflict) => {
                                    const decision = schemaDecisions[conflict.source_schema_id] ?? {
                                      action: 'rename' as const,
                                      new_name: '',
                                    };
                                    return (
                                      <div key={conflict.source_schema_id} className="border border-gray-200 rounded p-2 text-sm">
                                        <p className="font-medium mb-1">&ldquo;{conflict.source_schema_name}&rdquo;</p>
                                        <div className="flex flex-col gap-1.5">
                                          <label className="flex items-center gap-2">
                                            <input
                                              type="radio"
                                              name={`schema-${conflict.source_schema_id}`}
                                              checked={decision.action === 'rename'}
                                              onChange={() =>
                                                setSchemaDecisions((prev) => ({
                                                  ...prev,
                                                  [conflict.source_schema_id]: {
                                                    action: 'rename',
                                                    new_name: prev[conflict.source_schema_id]?.new_name
                                                      ?? `${conflict.source_schema_name} (imported)`,
                                                  },
                                                }))
                                              }
                                            />
                                            <span>Rename to:</span>
                                            {decision.action === 'rename' && (
                                              <input
                                                type="text"
                                                value={decision.new_name ?? ''}
                                                onChange={(e) =>
                                                  setSchemaDecisions((prev) => ({
                                                    ...prev,
                                                    [conflict.source_schema_id]: {
                                                      action: 'rename',
                                                      new_name: e.target.value,
                                                    },
                                                  }))
                                                }
                                                className="flex-1 border border-gray-300 rounded px-2 py-0.5 text-sm"
                                                placeholder="New name…"
                                              />
                                            )}
                                          </label>
                                          <label className="flex items-center gap-2">
                                            <input
                                              type="radio"
                                              name={`schema-${conflict.source_schema_id}`}
                                              checked={decision.action === 'drop'}
                                              onChange={() =>
                                                setSchemaDecisions((prev) => ({
                                                  ...prev,
                                                  [conflict.source_schema_id]: { action: 'drop' },
                                                }))
                                              }
                                            />
                                            <span>Drop incoming schema</span>
                                          </label>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </section>
                            )}

                            {/* Venue tier conflicts */}
                            {preview.venue_tier_conflicts.length > 0 && (
                              <section>
                                <div className="flex items-center justify-between mb-2">
                                  <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
                                    Venue tier conflicts
                                  </h4>
                                  <button
                                    onClick={handleBulkBestTier}
                                    className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-50 text-gray-600"
                                  >
                                    Always keep best tier
                                  </button>
                                </div>
                                <table className="w-full text-xs border border-gray-200 rounded overflow-hidden">
                                  <thead className="bg-gray-50 text-gray-500 uppercase">
                                    <tr>
                                      <th className="text-left px-2 py-1.5">Venue</th>
                                      <th className="text-left px-2 py-1.5">Incoming</th>
                                      <th className="text-left px-2 py-1.5">Existing</th>
                                      <th className="text-left px-2 py-1.5">Keep</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-gray-100">
                                    {preview.venue_tier_conflicts.map((conflict) => {
                                      const chosen = venueTierDecisions[conflict.venue_id] ?? conflict.target_tier;
                                      return (
                                        <tr key={conflict.venue_id} className="hover:bg-gray-50">
                                          <td className="px-2 py-1.5 font-medium text-gray-900 max-w-[140px] truncate">
                                            {conflict.venue_name}
                                          </td>
                                          <td className="px-2 py-1.5 text-gray-600">
                                            {TIER_LABELS[conflict.source_tier] ?? conflict.source_tier}
                                          </td>
                                          <td className="px-2 py-1.5 text-gray-600">
                                            {TIER_LABELS[conflict.target_tier] ?? conflict.target_tier}
                                          </td>
                                          <td className="px-2 py-1.5">
                                            <select
                                              value={chosen}
                                              onChange={(e) =>
                                                setVenueTierDecisions((prev) => ({
                                                  ...prev,
                                                  [conflict.venue_id]: parseInt(e.target.value, 10),
                                                }))
                                              }
                                              className="border border-gray-300 rounded px-1.5 py-0.5 text-xs"
                                            >
                                              <option value={conflict.source_tier}>Incoming ({conflict.source_tier})</option>
                                              <option value={conflict.target_tier}>Existing ({conflict.target_tier})</option>
                                            </select>
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </section>
                            )}

                            {/* Informational counts */}
                            {(preview.ignored_work_overrides.length > 0 ||
                              preview.source_chat_session_count > 0 ||
                              preview.source_note_count > 0) && (
                              <div className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded p-2 space-y-0.5">
                                {preview.ignored_work_overrides.length > 0 && (
                                  <p>
                                    {preview.ignored_work_overrides.length} ignored work
                                    {preview.ignored_work_overrides.length !== 1 ? 's' : ''}{' '}
                                    will be un-ignored (seeds win).
                                  </p>
                                )}
                                {preview.source_note_count > 0 && (
                                  <p>{preview.source_note_count} note{preview.source_note_count !== 1 ? 's' : ''} will be copied.</p>
                                )}
                                {preview.source_chat_session_count > 0 && (
                                  <p>
                                    {preview.source_chat_session_count} chat session
                                    {preview.source_chat_session_count !== 1 ? 's' : ''} will be copied.
                                  </p>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </section>
                  )}
                </div>
              )}

              {/* Error */}
              {error && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">
                  {error}
                </p>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            {resolvedProjectId !== null ? 'Close' : 'Cancel'}
          </button>

          {resolvedProjectId === null && importResult === null && (
            <button
              onClick={handleUpload}
              disabled={importing}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {importing ? 'Importing…' : 'Import'}
            </button>
          )}

          {resolvedProjectId === null && importResult?.needs_project_decision && (
            <button
              onClick={handleResolve}
              disabled={
                isResolvePending ||
                (collisionAction === 'rename' && !newName.trim())
              }
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isResolvePending
                ? 'Processing…'
                : collisionAction === 'merge'
                  ? 'Merge into existing project'
                  : 'Create as separate project'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
