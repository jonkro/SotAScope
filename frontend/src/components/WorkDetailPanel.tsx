import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWork, useForwardCitations, useBackwardCitations, useDeleteWork } from '../hooks/useWorks';
import { useFetchBackwardCitations, useFetchForwardCitations, useEnrichFromCrossref, useResolveDOI } from '../hooks/useEnrichment';
import type { CitationWorkBrief, DOIResolutionResult, TopicListOut } from '../types';
import DOIResolutionDialog from './DOIResolutionDialog';

/* ------------------------------------------------------------------ */
/* Fold state                                                          */
/* ------------------------------------------------------------------ */

export interface PanelFoldState {
  abstract: boolean;
  locations: boolean;
  actions: boolean;
  references: boolean;
  citedBy: boolean;
}

export const DEFAULT_FOLD_STATE: PanelFoldState = {
  abstract: false,
  locations: false,
  actions: true,
  references: false,
  citedBy: false,
};

/* ------------------------------------------------------------------ */
/* SVG marker components matching timeline shapes                      */
/* ------------------------------------------------------------------ */

/** Seed marker: filled square, multi-color uses vertical stripes */
function SeedMarker({ colors }: { colors: string[] }) {
  const s = 8;
  if (colors.length <= 1) {
    return (
      <svg width={s} height={s} className="shrink-0 mt-0.5">
        <rect width={s} height={s} fill={colors[0] ?? '#6b7280'} />
      </svg>
    );
  }
  const sw = s / colors.length;
  return (
    <svg width={s} height={s} className="shrink-0 mt-0.5">
      {colors.map((c, i) => (
        <rect key={i} x={i * sw} width={sw} height={s} fill={c} />
      ))}
    </svg>
  );
}

/** Backward neighbor marker: grey circle (matches timeline references) */
function BackwardMarker() {
  return (
    <svg width={8} height={8} className="shrink-0 mt-0.5">
      <circle cx={4} cy={4} r={3.5} fill="#9ca3af" />
    </svg>
  );
}

/** Forward neighbor marker: grey diamond (matches timeline citing papers) */
function ForwardMarker() {
  return (
    <svg width={8} height={8} className="shrink-0 mt-0.5">
      <rect x={1.17} y={1.17} width={5.66} height={5.66} fill="#9ca3af" transform="rotate(45,4,4)" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Collapsible section wrapper                                         */
/* ------------------------------------------------------------------ */

function CollapsibleSection({
  sectionKey,
  title,
  defaultOpen = false,
  count,
  children,
  foldState,
  onFoldChange,
}: {
  sectionKey?: keyof PanelFoldState;
  title: string;
  defaultOpen?: boolean;
  count?: number;
  children: React.ReactNode;
  foldState?: PanelFoldState;
  onFoldChange?: (s: PanelFoldState) => void;
}) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);

  const isOpen = sectionKey && foldState
    ? foldState[sectionKey]
    : internalOpen;

  const toggle = () => {
    if (sectionKey && foldState && onFoldChange) {
      onFoldChange({ ...foldState, [sectionKey]: !isOpen });
    } else {
      setInternalOpen(!internalOpen);
    }
  };

  return (
    <div>
      <button
        onClick={toggle}
        className="flex items-center gap-1 w-full text-left"
      >
        <span className="text-[10px] text-gray-400">{isOpen ? '\u25BE' : '\u25B8'}</span>
        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          {title}
          {count != null && <span className="text-gray-400 ml-1 normal-case">({count})</span>}
        </h4>
      </button>
      {isOpen && <div className="mt-2">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Citation list with timeline markers and clickability                */
/* ------------------------------------------------------------------ */

function CitationList({
  sectionKey,
  title,
  direction,
  items,
  isLoading,
  seedColorMap,
  renderedWorkIds,
  ignoredWorkIds,
  onSelectWork,
  foldState,
  onFoldChange,
}: {
  sectionKey: keyof PanelFoldState;
  title: string;
  direction: 'backward' | 'forward';
  items?: CitationWorkBrief[];
  isLoading: boolean;
  seedColorMap?: Map<number, string[]>;
  renderedWorkIds?: Set<number>;
  ignoredWorkIds?: Set<number>;
  onSelectWork?: (workId: number) => void;
  foldState?: PanelFoldState;
  onFoldChange?: (s: PanelFoldState) => void;
}) {
  const [internalOpen, setInternalOpen] = useState(false);

  const isOpen = foldState ? foldState[sectionKey] : internalOpen;

  const toggle = () => {
    if (foldState && onFoldChange) {
      onFoldChange({ ...foldState, [sectionKey]: !isOpen });
    } else {
      setInternalOpen(!internalOpen);
    }
  };

  return (
    <div>
      <button
        onClick={toggle}
        className="flex items-center gap-1 w-full text-left"
      >
        <span className="text-[10px] text-gray-400">{isOpen ? '\u25BE' : '\u25B8'}</span>
        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          {title}
          {items && <span className="text-gray-400 ml-1 normal-case">({items.length})</span>}
        </h4>
      </button>
      {isOpen && (
        isLoading ? (
          <p className="text-xs text-gray-400 mt-2">Loading...</p>
        ) : !items?.length ? (
          <p className="text-xs text-gray-400 mt-2">None fetched yet</p>
        ) : (
          <ul className="space-y-1 mt-2">
            {items.map((c) => {
              const seedColors = seedColorMap?.get(c.id);
              const isRendered = renderedWorkIds?.has(c.id);
              const isIgnored = ignoredWorkIds?.has(c.id);
              const clickable = onSelectWork && (renderedWorkIds === undefined || isRendered);

              const label = (
                <>
                  {c.title}
                  {c.publication_year && (
                    <span className="text-gray-400"> ({c.publication_year})</span>
                  )}
                </>
              );

              // Marker: seed square > direction-specific neighbor shape > empty spacer
              let marker: React.ReactNode = <span className="inline-block w-2 shrink-0" />;
              if (seedColors) {
                marker = <SeedMarker colors={seedColors} />;
              } else if (isRendered) {
                marker = direction === 'backward' ? <BackwardMarker /> : <ForwardMarker />;
              }

              return (
                <li key={c.id} className="flex items-start gap-1.5 text-xs leading-snug">
                  {marker}
                  {clickable ? (
                    <button
                      onClick={() => onSelectWork(c.id)}
                      className={`text-left hover:text-blue-600 ${isIgnored ? 'text-gray-400 line-through' : 'text-gray-700'}`}
                    >
                      {label}
                    </button>
                  ) : (
                    <span className={isIgnored ? 'text-gray-400 line-through' : 'text-gray-700'}>
                      {label}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* TimelineContext type (kept for external callers)                     */
/* ------------------------------------------------------------------ */

interface TimelineContext {
  direction: 'seed' | 'backward' | 'forward';
  connectedSeeds: { id: number; title: string; color: string }[];
  forwardCitationsFetchedAt: string | null;
}

/* ------------------------------------------------------------------ */
/* Main panel                                                          */
/* ------------------------------------------------------------------ */

export default function WorkDetailPanel({
  workId,
  onClose,
  topicLists,
  onAddToList,
  onRemoveFromList,
  onMarkUninteresting,
  onEnrichComplete,
  onDelete,
  timelineContext,
  isAutoEnriching,
  seedColorMap,
  renderedWorkIds,
  ignoredWorkIds,
  onSelectWork,
  workTopicListIds,
  foldState,
  onFoldChange,
}: {
  workId: number;
  onClose: () => void;
  topicLists?: TopicListOut[];
  onAddToList?: (topicListId: number) => void;
  onRemoveFromList?: (topicListId: number) => void;
  onMarkUninteresting?: (workId: number) => void;
  onEnrichComplete?: () => void;
  onDelete?: () => void;
  timelineContext?: TimelineContext;
  isAutoEnriching?: boolean;
  seedColorMap?: Map<number, string[]>;
  renderedWorkIds?: Set<number>;
  ignoredWorkIds?: Set<number>;
  onSelectWork?: (workId: number) => void;
  workTopicListIds?: number[];
  foldState?: PanelFoldState;
  onFoldChange?: (s: PanelFoldState) => void;
}) {
  const { data: work, isLoading } = useWork(workId);
  const fwd = useForwardCitations(workId);
  const bwd = useBackwardCitations(workId);

  const fetchBwd = useFetchBackwardCitations();
  const fetchFwd = useFetchForwardCitations();
  const crossref = useEnrichFromCrossref();
  const resolveDOI = useResolveDOI();
  const [doiResolutionResults, setDoiResolutionResults] = useState<DOIResolutionResult[] | null>(null);
  const [resolveMsg, setResolveMsg] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteMutation = useDeleteWork();
  const navigate = useNavigate();

  if (isLoading || !work) {
    return (
      <div className="w-96 border-l border-gray-200 bg-white p-6">
        <p className="text-sm text-gray-400">Loading...</p>
      </div>
    );
  }

  const currentTlIds = new Set(workTopicListIds ?? []);
  const addableLists = topicLists?.filter((tl) => !currentTlIds.has(tl.id)) ?? [];
  const removableLists = topicLists?.filter((tl) => currentTlIds.has(tl.id)) ?? [];

  const roleBadge = timelineContext && (
    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${
      timelineContext.direction === 'seed'
        ? 'bg-blue-100 text-blue-700'
        : timelineContext.direction === 'backward'
        ? 'bg-amber-100 text-amber-700'
        : 'bg-green-100 text-green-700'
    }`}>
      {timelineContext.direction === 'seed' ? 'Seed' : timelineContext.direction === 'backward' ? 'Reference' : 'Citing'}
    </span>
  );

  return (
    <>
    <div className="w-96 border-l border-gray-200 bg-white flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-gray-200">
        <div className="pr-2 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900 leading-snug">{work.title}</h3>
          {roleBadge && <div className="mt-1">{roleBadge}</div>}
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 shrink-0">&times;</button>
      </div>

      <div className="flex-1 p-4 space-y-5 text-sm">
        {/* Authors */}
        {work.authors.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Authors</h4>
            <p className="text-gray-700">{work.authors.map((a) => a.author.name).join(', ')}</p>
          </div>
        )}

        {/* Metadata */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          {work.publication_year && (
            <>
              <span className="text-gray-500">Year</span>
              <span className="text-gray-800">{work.publication_year}</span>
            </>
          )}
          {work.venue_name && (
            <>
              <span className="text-gray-500">Venue</span>
              {work.venue_id ? (
                <button
                  onClick={() => navigate(`/venues?venue_id=${work.venue_id}`)}
                  className="text-blue-600 hover:underline text-left"
                >
                  {work.venue_name}
                </button>
              ) : (
                <span className="text-gray-800">{work.venue_name}</span>
              )}
            </>
          )}
          {work.doi && (
            <>
              <span className="text-gray-500">DOI</span>
              <span className="flex items-center gap-1">
                <a
                  href={`https://doi.org/${work.doi}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline truncate"
                >
                  {work.doi}
                </a>
                {work.doi_auto_resolved && (
                  <span className="text-[10px] text-amber-600 whitespace-nowrap">(auto-resolved)</span>
                )}
              </span>
            </>
          )}
          {work.arxiv_id && (
            <>
              <span className="text-gray-500">arXiv</span>
              <a
                href={`https://arxiv.org/abs/${work.arxiv_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                {work.arxiv_id}
              </a>
            </>
          )}
          {work.citation_count != null && (
            <>
              <span className="text-gray-500">Citations</span>
              <span className="text-gray-800">{work.citation_count}</span>
            </>
          )}
        </div>

        {/* Abstract (collapsible) */}
        {work.abstract && (
          <CollapsibleSection
            sectionKey="abstract"
            title="Abstract"
            foldState={foldState}
            onFoldChange={onFoldChange}
          >
            <p className="text-xs text-gray-700 leading-relaxed">{work.abstract}</p>
          </CollapsibleSection>
        )}

        {/* Locations (collapsible) */}
        {work.locations.length > 0 && (
          <CollapsibleSection
            sectionKey="locations"
            title="Locations"
            count={work.locations.length}
            foldState={foldState}
            onFoldChange={onFoldChange}
          >
            <ul className="space-y-1">
              {work.locations.map((loc) => {
                const linkText =
                  loc.location_type === 'venue' && (work.venue_display_name || work.venue_name)
                    ? (work.venue_display_name || work.venue_name)
                    : loc.location_type;
                return (
                  <li key={loc.id} className="text-xs">
                    <a href={loc.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                      {linkText}{loc.is_primary ? ' (primary)' : ''}
                    </a>
                  </li>
                );
              })}
            </ul>
          </CollapsibleSection>
        )}

        {/* Actions (collapsible, default open) */}
        <CollapsibleSection
          sectionKey="actions"
          title="Actions"
          defaultOpen
          foldState={foldState}
          onFoldChange={onFoldChange}
        >
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => fetchBwd.mutate(workId, { onSettled: onEnrichComplete })}
              disabled={fetchBwd.isPending || isAutoEnriching}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {fetchBwd.isPending ? 'Fetching...' : 'Fetch References'}
            </button>
            <button
              onClick={() => fetchFwd.mutate({ workId }, { onSettled: onEnrichComplete })}
              disabled={fetchFwd.isPending || isAutoEnriching}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {fetchFwd.isPending ? 'Fetching...' : 'Fetch Citing Papers'}
            </button>
            {work.doi && (
              <button
                onClick={() => crossref.mutate(workId, { onSettled: onEnrichComplete })}
                disabled={crossref.isPending || isAutoEnriching}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                {crossref.isPending ? 'Enriching...' : 'Enrich from Crossref'}
              </button>
            )}
            {!work.doi && (
              <button
                onClick={() => {
                  setResolveMsg(null);
                  resolveDOI.mutate(workId, {
                    onSuccess: (result) => {
                      if (result.auto_resolved_doi) {
                        setResolveMsg(`DOI resolved: ${result.auto_resolved_doi}`);
                        onEnrichComplete?.();
                      } else if (result.candidates.length > 0) {
                        setDoiResolutionResults([result]);
                      } else {
                        setResolveMsg('No DOI candidates found');
                      }
                    },
                    onError: (err) => {
                      setResolveMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
                    },
                  });
                }}
                disabled={resolveDOI.isPending || isAutoEnriching}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                {resolveDOI.isPending ? 'Resolving...' : 'Resolve DOI'}
              </button>
            )}
          </div>
          {isAutoEnriching && (
            <p className="text-xs text-blue-600 mt-1 animate-pulse">Auto-enriching references and citations...</p>
          )}
          {fetchBwd.data && (
            <p className="text-xs text-green-600 mt-1">Fetched {fetchBwd.data.count} references</p>
          )}
          {fetchFwd.data && (
            <p className="text-xs text-green-600 mt-1">Fetched {fetchFwd.data.count} citing papers</p>
          )}
          {resolveMsg && (
            <p className={`text-xs mt-1 ${resolveMsg.startsWith('Error') ? 'text-red-600' : 'text-green-600'}`}>
              {resolveMsg}
            </p>
          )}
          {timelineContext?.direction === 'seed' && timelineContext.forwardCitationsFetchedAt && (
            <p className="text-xs text-gray-400 mt-1">
              Forward citations last fetched: {new Date(timelineContext.forwardCitationsFetchedAt).toLocaleDateString()}
            </p>
          )}

          {/* Add to topic list (only lists the work is NOT on) */}
          {addableLists.length > 0 && onAddToList && (
            <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t border-gray-100">
              <span className="text-xs text-gray-500 w-full">Add to topic list:</span>
              {addableLists.map((tl) => (
                <button
                  key={tl.id}
                  onClick={() => onAddToList(tl.id)}
                  className="px-2 py-1 text-xs text-white rounded hover:opacity-80"
                  style={{ backgroundColor: tl.color }}
                >
                  {tl.name}
                </button>
              ))}
            </div>
          )}

          {/* Remove from topic list (only lists the work IS on) */}
          {removableLists.length > 0 && onRemoveFromList && (
            <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t border-gray-100">
              <span className="text-xs text-gray-500 w-full">Remove from topic list:</span>
              {removableLists.map((tl) => (
                <button
                  key={tl.id}
                  onClick={() => onRemoveFromList(tl.id)}
                  className="px-2 py-1 text-xs rounded border hover:opacity-80"
                  style={{ borderColor: tl.color, color: tl.color }}
                >
                  {tl.name}
                </button>
              ))}
            </div>
          )}

          {/* Mark uninteresting (neighbors only) */}
          {onMarkUninteresting && timelineContext && (timelineContext.direction === 'backward' || timelineContext.direction === 'forward') && (
            <div className="mt-3 pt-3 border-t border-gray-100">
              <button
                onClick={() => { onMarkUninteresting(workId); onClose(); }}
                className="px-2 py-1 text-xs font-medium text-red-600 border border-red-300 rounded hover:bg-red-50"
              >
                Mark uninteresting
              </button>
            </div>
          )}
        </CollapsibleSection>

        {/* References (backward) — collapsible with markers */}
        <CitationList
          sectionKey="references"
          title="References (backward)"
          direction="backward"
          items={bwd.data}
          isLoading={bwd.isLoading}
          seedColorMap={seedColorMap}
          renderedWorkIds={renderedWorkIds}
          ignoredWorkIds={ignoredWorkIds}
          onSelectWork={onSelectWork}
          foldState={foldState}
          onFoldChange={onFoldChange}
        />

        {/* Cited by (forward) — collapsible with markers */}
        <CitationList
          sectionKey="citedBy"
          title="Cited by (forward)"
          direction="forward"
          items={fwd.data}
          isLoading={fwd.isLoading}
          seedColorMap={seedColorMap}
          renderedWorkIds={renderedWorkIds}
          ignoredWorkIds={ignoredWorkIds}
          onSelectWork={onSelectWork}
          foldState={foldState}
          onFoldChange={onFoldChange}
        />

        {/* Delete from library (library context only) */}
        {onDelete && (
          <div>
            {!confirmDelete ? (
              <button
                onClick={() => setConfirmDelete(true)}
                className="px-3 py-1.5 text-xs font-medium text-red-600 border border-red-300 rounded hover:bg-red-50"
              >
                Delete from Library
              </button>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-red-600">
                  This will permanently remove this work and all its citations, topic list memberships, and other associations.
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      deleteMutation.mutate(workId, {
                        onSuccess: () => { onDelete(); onClose(); },
                      });
                    }}
                    disabled={deleteMutation.isPending}
                    className="px-3 py-1.5 text-xs font-medium text-white bg-red-600 rounded hover:bg-red-700 disabled:opacity-50"
                  >
                    {deleteMutation.isPending ? 'Deleting...' : 'Confirm Delete'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="px-3 py-1.5 text-xs font-medium text-gray-600 border border-gray-300 rounded hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>

    {doiResolutionResults && (
      <DOIResolutionDialog
        results={doiResolutionResults}
        onClose={() => {
          setDoiResolutionResults(null);
          onEnrichComplete?.();
        }}
      />
    )}
    </>
  );
}
