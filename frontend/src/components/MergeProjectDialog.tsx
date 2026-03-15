import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useProjects } from '../hooks/useProjects';
import { useMergePreview, useMergeProject } from '../hooks/useProjects';
import type { SchemaDecision, MergeDecisions } from '../types';

interface Props {
  targetProjectId: number;
  targetProjectName: string;
  onClose: () => void;
}

const TIER_LABELS: Record<number, string> = { 1: 'Tier 1 (top)', 2: 'Tier 2', 3: 'Tier 3 (ignore)' };

export function MergeProjectDialog({ targetProjectId, targetProjectName, onClose }: Props) {
  const navigate = useNavigate();
  const { data: projects } = useProjects();
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Schema decisions: keyed by source_schema_id
  const [schemaDecisions, setSchemaDecisions] = useState<Record<number, SchemaDecision>>({});
  // Venue tier decisions: keyed by venue_id
  const [venueTierDecisions, setVenueTierDecisions] = useState<Record<number, number>>({});

  const sourceProjects = projects?.filter((p) => p.id !== targetProjectId) ?? [];
  const sourceProject = sourceProjects.find((p) => p.id === sourceId);

  const preview = useMergePreview(targetProjectId, sourceId);
  const merge = useMergeProject();

  // When preview loads, initialize decisions
  useEffect(() => {
    if (!preview.data || !sourceProject) return;

    const initialSchema: Record<number, SchemaDecision> = {};
    for (const conflict of preview.data.schema_conflicts) {
      initialSchema[conflict.source_schema_id] = {
        action: 'rename',
        new_name: `${conflict.source_schema_name} (from ${sourceProject.name})`,
      };
    }
    setSchemaDecisions(initialSchema);

    const initialVenue: Record<number, number> = {};
    for (const conflict of preview.data.venue_tier_conflicts) {
      // Default: keep target tier (no entry needed; absent = keep target)
      initialVenue[conflict.venue_id] = conflict.target_tier;
    }
    setVenueTierDecisions(initialVenue);
  }, [preview.data, sourceProject]);

  function handleBulkBestTier() {
    if (!preview.data) return;
    const updated: Record<number, number> = {};
    for (const conflict of preview.data.venue_tier_conflicts) {
      updated[conflict.venue_id] = Math.min(conflict.source_tier, conflict.target_tier);
    }
    setVenueTierDecisions(updated);
  }

  function handleMerge() {
    if (!sourceId) return;
    setError(null);

    // Only include schema decisions for actual conflicts; filter out non-conflicts
    const filteredSchema: Record<number, SchemaDecision> = {};
    if (preview.data) {
      for (const conflict of preview.data.schema_conflicts) {
        const decision = schemaDecisions[conflict.source_schema_id];
        if (decision) filteredSchema[conflict.source_schema_id] = decision;
      }
    }

    // venue tier decisions: only send non-default (differ from target)
    const filteredVenue: Record<number, number> = {};
    if (preview.data) {
      for (const conflict of preview.data.venue_tier_conflicts) {
        const chosen = venueTierDecisions[conflict.venue_id];
        if (chosen !== undefined && chosen !== conflict.target_tier) {
          filteredVenue[conflict.venue_id] = chosen;
        }
      }
    }

    const decisions: MergeDecisions = {
      schema_decisions: filteredSchema,
      venue_tier_decisions: filteredVenue,
    };

    merge.mutate(
      { targetId: targetProjectId, sourceId, decisions },
      {
        onSuccess: () => {
          onClose();
          navigate(`/projects/${targetProjectId}`);
        },
        onError: (err) => {
          setError(err instanceof Error ? err.message : 'Merge failed');
        },
      },
    );
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-base font-semibold text-gray-900">
            Merge another project into &ldquo;{targetProjectName}&rdquo;
          </h2>
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

          {/* Step 1: Source project selection */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Copy content from project
            </label>
            <select
              value={sourceId ?? ''}
              onChange={(e) => {
                const val = e.target.value;
                setSourceId(val ? parseInt(val, 10) : null);
                setError(null);
              }}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">— select a project —</option>
              {sourceProjects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Step 2: Preview */}
          {sourceId !== null && (
            <>
              {preview.isLoading && (
                <p className="text-sm text-gray-400">Loading preview…</p>
              )}

              {preview.error && (
                <p className="text-sm text-red-600">
                  Failed to load preview:{' '}
                  {preview.error instanceof Error ? preview.error.message : 'Unknown error'}
                </p>
              )}

              {preview.data && (
                <div className="space-y-5">
                  {/* Topic lists */}
                  <section>
                    <h3 className="text-sm font-semibold text-gray-800 mb-2">Topic lists</h3>
                    {preview.data.topic_list_merges.length === 0 ? (
                      <p className="text-xs text-gray-500">No topic lists in source project.</p>
                    ) : (
                      <ul className="space-y-1 text-sm text-gray-700">
                        {preview.data.topic_list_merges.map((tl) => (
                          <li key={tl.source_topic_list_id} className="flex items-center gap-2">
                            <span className="font-medium">{tl.source_topic_list_name}</span>
                            {tl.action === 'merge' ? (
                              <span className="text-xs text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                                merged into existing list
                              </span>
                            ) : (
                              <span className="text-xs text-green-600 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded">
                                copied as-is
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  {/* Schema conflicts */}
                  {preview.data.schema_conflicts.length > 0 && (
                    <section>
                      <h3 className="text-sm font-semibold text-gray-800 mb-2">
                        Extraction schema conflicts
                        <span className="ml-1 text-xs font-normal text-gray-500">
                          (same name in both projects)
                        </span>
                      </h3>
                      <div className="space-y-3">
                        {preview.data.schema_conflicts.map((conflict) => {
                          const decision = schemaDecisions[conflict.source_schema_id] ?? {
                            action: 'rename' as const,
                            new_name: '',
                          };
                          return (
                            <div key={conflict.source_schema_id} className="border border-gray-200 rounded p-3 text-sm">
                              <p className="font-medium text-gray-900 mb-2">
                                &ldquo;{conflict.source_schema_name}&rdquo;
                              </p>
                              <div className="flex flex-col gap-2">
                                <label className="flex items-center gap-2 cursor-pointer">
                                  <input
                                    type="radio"
                                    name={`schema-${conflict.source_schema_id}`}
                                    checked={decision.action === 'rename'}
                                    onChange={() =>
                                      setSchemaDecisions((prev) => ({
                                        ...prev,
                                        [conflict.source_schema_id]: {
                                          action: 'rename',
                                          new_name:
                                            prev[conflict.source_schema_id]?.new_name ??
                                            `${conflict.source_schema_name} (from ${sourceProject?.name ?? 'source'})`,
                                        },
                                      }))
                                    }
                                  />
                                  <span className="text-gray-700">Rename to:</span>
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
                                      className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                                      placeholder="New name…"
                                    />
                                  )}
                                </label>
                                <label className="flex items-center gap-2 cursor-pointer">
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
                                  <span className="text-gray-700">Drop incoming schema</span>
                                </label>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}

                  {/* Venue tier conflicts */}
                  {preview.data.venue_tier_conflicts.length > 0 && (
                    <section>
                      <div className="flex items-center justify-between mb-2">
                        <h3 className="text-sm font-semibold text-gray-800">
                          Venue tier conflicts
                        </h3>
                        <button
                          onClick={handleBulkBestTier}
                          className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-50 text-gray-600"
                        >
                          Always keep best (lowest) tier
                        </button>
                      </div>
                      <table className="w-full text-sm border border-gray-200 rounded overflow-hidden">
                        <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                          <tr>
                            <th className="text-left px-3 py-2">Venue</th>
                            <th className="text-left px-3 py-2">Source tier</th>
                            <th className="text-left px-3 py-2">Target tier</th>
                            <th className="text-left px-3 py-2">Keep</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {preview.data.venue_tier_conflicts.map((conflict) => {
                            const chosen = venueTierDecisions[conflict.venue_id] ?? conflict.target_tier;
                            return (
                              <tr key={conflict.venue_id} className="hover:bg-gray-50">
                                <td className="px-3 py-2 font-medium text-gray-900 max-w-[200px] truncate">
                                  {conflict.venue_name}
                                </td>
                                <td className="px-3 py-2 text-gray-600">
                                  {TIER_LABELS[conflict.source_tier] ?? `Tier ${conflict.source_tier}`}
                                </td>
                                <td className="px-3 py-2 text-gray-600">
                                  {TIER_LABELS[conflict.target_tier] ?? `Tier ${conflict.target_tier}`}
                                </td>
                                <td className="px-3 py-2">
                                  <select
                                    value={chosen}
                                    onChange={(e) =>
                                      setVenueTierDecisions((prev) => ({
                                        ...prev,
                                        [conflict.venue_id]: parseInt(e.target.value, 10),
                                      }))
                                    }
                                    className="border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                                  >
                                    <option value={conflict.source_tier}>Source ({conflict.source_tier})</option>
                                    <option value={conflict.target_tier}>Target ({conflict.target_tier})</option>
                                  </select>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </section>
                  )}

                  {/* Informational: seed/ignored overrides */}
                  {preview.data.ignored_work_overrides.length > 0 && (
                    <section className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-800">
                      <strong>{preview.data.ignored_work_overrides.length} work{preview.data.ignored_work_overrides.length !== 1 ? 's' : ''}</strong>{' '}
                      will be un-ignored because they are seeds in the source project (seeds win over ignored).
                    </section>
                  )}

                  {/* Informational: chat sessions and notes */}
                  {(preview.data.source_chat_session_count > 0 || preview.data.source_note_count > 0) && (
                    <section className="bg-blue-50 border border-blue-200 rounded p-3 text-sm text-blue-800">
                      {preview.data.source_note_count > 0 && (
                        <p>{preview.data.source_note_count} project-scoped note{preview.data.source_note_count !== 1 ? 's' : ''} will be copied to this project.</p>
                      )}
                      {preview.data.source_chat_session_count > 0 && (
                        <p>{preview.data.source_chat_session_count} chat session{preview.data.source_chat_session_count !== 1 ? 's' : ''} will remain in the source project (not copied).</p>
                      )}
                    </section>
                  )}
                </div>
              )}
            </>
          )}

          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleMerge}
            disabled={
              sourceId === null ||
              preview.isLoading ||
              preview.error !== null ||
              merge.isPending
            }
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {merge.isPending ? 'Merging…' : 'Merge projects'}
          </button>
        </div>
      </div>
    </div>
  );
}
