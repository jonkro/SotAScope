import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useWork, useForwardCitations, useBackwardCitations, useDeleteWork } from '../hooks/useWorks';
import { useFetchBackwardCitations, useFetchForwardCitations, useEnrichFromCrossref, useEnrichFromSemanticScholar, useResolveDOI } from '../hooks/useEnrichment';
import { useUpdateWork, useAddWorkDOIAlias, useRemoveWorkDOIAlias } from '../hooks/useWorks';
import { useWorkPDFs, useUploadWorkPDF, useSetWorkPDFPrimary, useDeleteWorkPDF, useExtractWorkPDFText } from '../hooks/useWorkPDFs';
import { useWorkNotes, useCreateWorkNote, useUpdateWorkNote, useDeleteWorkNote } from '../hooks/useWorkNotes';
import { serveWorkPDFUrl, workPDFTextUrl, getDOIInfo } from '../api';
import type { CitationWorkBrief, DOIResolutionResult, TopicListOut, WorkNote } from '../types';
import DOIResolutionDialog from './DOIResolutionDialog';
import ConfirmDialog from './ConfirmDialog';

/* ------------------------------------------------------------------ */
/* Fold state                                                          */
/* ------------------------------------------------------------------ */

export interface PanelFoldState {
  abstract: boolean;
  locations: boolean;
  notes: boolean;
  pdfs: boolean;
  actions: boolean;
  references: boolean;
  citedBy: boolean;
}

export const DEFAULT_FOLD_STATE: PanelFoldState = {
  abstract: false,
  locations: false,
  notes: false,
  pdfs: true,
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
/* Title similarity helper (for DOI verification warning)             */
/* ------------------------------------------------------------------ */

function normTitle(t: string): string[] {
  return t.toLowerCase().replace(/[^a-z0-9]/g, ' ').split(/\s+/).filter(Boolean);
}

/** Returns a 0–1 Jaccard similarity between two title strings. */
function titleSimilarity(a: string, b: string): number {
  const wa = new Set(normTitle(a));
  const wb = new Set(normTitle(b));
  if (wa.size === 0 && wb.size === 0) return 1;
  let intersection = 0;
  for (const w of wa) if (wb.has(w)) intersection++;
  return intersection / (wa.size + wb.size - intersection);
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
                      className={`text-left cursor-pointer hover:text-blue-600 hover:underline ${isIgnored ? 'text-gray-400 line-through' : 'text-gray-700'}`}
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
  projectId,
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
  projectId?: number;
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
  const enrichSS = useEnrichFromSemanticScholar();
  const updateWork = useUpdateWork();
  const resolveDOI = useResolveDOI();
  const pdfsQuery = useWorkPDFs(workId);
  const uploadPDF = useUploadWorkPDF();
  const setPrimaryPDF = useSetWorkPDFPrimary();
  const removePDF = useDeleteWorkPDF();
  const extractText = useExtractWorkPDFText();
  const [extractErrors, setExtractErrors] = useState<Record<number, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pdfToRemove, setPdfToRemove] = useState<{ id: number; filename: string } | null>(null);

  const notesQuery = useWorkNotes(workId, projectId);
  const createNote = useCreateWorkNote(workId);
  const updateNote = useUpdateWorkNote(workId);
  const deleteNote = useDeleteWorkNote(workId);
  const [addingNote, setAddingNote] = useState(false);
  const [newNoteContent, setNewNoteContent] = useState('');
  const [newNoteType, setNewNoteType] = useState('');
  const [newNoteScope, setNewNoteScope] = useState<'general' | 'project'>('general');
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editNoteType, setEditNoteType] = useState('');
  const [noteToDelete, setNoteToDelete] = useState<WorkNote | null>(null);

  const [doiResolutionResults, setDoiResolutionResults] = useState<DOIResolutionResult[] | null>(null);
  const [resolveMsg, setResolveMsg] = useState<string | null>(null);
  const [ssEnrichMsg, setSsEnrichMsg] = useState<string | null>(null);
  const [editSsId, setEditSsId] = useState(false);
  const [ssIdDraft, setSsIdDraft] = useState('');
  const [editDoi, setEditDoi] = useState(false);
  const [doiDraft, setDoiDraft] = useState('');
  // Fetch remote title for the drafted DOI to show a similarity warning
  const doiLookupEnabled = editDoi && doiDraft.trim().length > 5;
  const { data: doiInfo, isFetching: doiInfoFetching } = useQuery({
    queryKey: ['doi-info', doiDraft.trim()],
    queryFn: () => getDOIInfo(doiDraft.trim()),
    enabled: doiLookupEnabled,
    staleTime: 5 * 60 * 1000,
  });
  const addDOIAlias = useAddWorkDOIAlias();
  const removeDOIAlias = useRemoveWorkDOIAlias();
  const [addingDOIAlias, setAddingDOIAlias] = useState(false);
  const [doiAliasDraft, setDoiAliasDraft] = useState('');
  // Fetch remote title for the alias draft
  const doiAliasLookupEnabled = addingDOIAlias && doiAliasDraft.trim().length > 5;
  const { data: doiAliasInfo, isFetching: doiAliasInfoFetching } = useQuery({
    queryKey: ['doi-info', doiAliasDraft.trim()],
    queryFn: () => getDOIInfo(doiAliasDraft.trim()),
    enabled: doiAliasLookupEnabled,
    staleTime: 5 * 60 * 1000,
  });
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
          <>
            <span className="text-gray-500">DOI</span>
            {editDoi ? (
              <span className="flex flex-col gap-1">
                <span className="flex items-center gap-1">
                  <input
                    type="text"
                    value={doiDraft}
                    onChange={(e) => setDoiDraft(e.target.value)}
                    placeholder="e.g. 10.1234/example"
                    className="flex-1 border border-gray-300 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                    autoFocus
                  />
                  <button
                    onClick={() => {
                      updateWork.mutate(
                        { workId, data: { doi: doiDraft.trim() || null } },
                        { onSettled: () => setEditDoi(false) },
                      );
                    }}
                    disabled={updateWork.isPending}
                    className="text-xs text-blue-600 hover:underline disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditDoi(false)}
                    className="text-xs text-gray-500 hover:underline"
                  >
                    Cancel
                  </button>
                </span>
                {/* Title similarity warning */}
                {doiLookupEnabled && !doiInfoFetching && doiInfo && work && (() => {
                  if (!doiInfo.found) {
                    return (
                      <span className="text-[10px] text-red-600">
                        DOI not found on OpenAlex or Crossref.
                      </span>
                    );
                  }
                  if (doiInfo.title) {
                    const sim = titleSimilarity(work.title, doiInfo.title);
                    if (sim < 0.7) {
                      return (
                        <span className="text-[10px] text-amber-700 leading-tight">
                          ⚠ Remote title differs significantly ({Math.round(sim * 100)}% match):<br />
                          <em>{doiInfo.title}</em>
                        </span>
                      );
                    }
                    return (
                      <span className="text-[10px] text-green-700">
                        ✓ Title matches: <em>{doiInfo.title}</em>
                      </span>
                    );
                  }
                  return null;
                })()}
                {doiLookupEnabled && doiInfoFetching && (
                  <span className="text-[10px] text-gray-400">Looking up DOI…</span>
                )}
              </span>
            ) : (
              <span className="flex flex-col gap-0.5">
                {/* Primary DOI */}
                <span className="flex items-center gap-1">
                  {work.doi ? (
                    <>
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
                    </>
                  ) : (
                    <span className="text-gray-400 text-xs">Not set</span>
                  )}
                  <button
                    onClick={() => { setDoiDraft(work.doi ?? ''); setEditDoi(true); }}
                    className="text-[10px] text-gray-400 hover:text-blue-600 underline ml-1"
                  >
                    {work.doi ? 'Edit' : 'Set'}
                  </button>
                </span>
                {/* Secondary DOIs */}
                {work.doi_aliases?.map((alias) => (
                  <span key={alias} className="flex items-center gap-1">
                    <a
                      href={`https://doi.org/${alias}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline truncate text-xs"
                    >
                      {alias}
                    </a>
                    <button
                      onClick={() => removeDOIAlias.mutate({ workId, doi: alias })}
                      disabled={removeDOIAlias.isPending}
                      className="text-[10px] text-red-400 hover:text-red-600 ml-1 disabled:opacity-50"
                      title="Remove this DOI"
                    >
                      ×
                    </button>
                  </span>
                ))}
                {/* Add DOI alias form or button */}
                {addingDOIAlias ? (
                  <span className="flex flex-col gap-1 mt-0.5">
                    <span className="flex items-center gap-1">
                      <input
                        type="text"
                        value={doiAliasDraft}
                        onChange={(e) => setDoiAliasDraft(e.target.value)}
                        placeholder="e.g. 10.1234/example"
                        className="flex-1 border border-gray-300 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                        autoFocus
                      />
                      <button
                        onClick={() => {
                          addDOIAlias.mutate(
                            { workId, doi: doiAliasDraft.trim() },
                            { onSettled: () => { setAddingDOIAlias(false); setDoiAliasDraft(''); } },
                          );
                        }}
                        disabled={addDOIAlias.isPending || !doiAliasDraft.trim()}
                        className="text-xs text-blue-600 hover:underline disabled:opacity-50"
                      >
                        Add
                      </button>
                      <button
                        onClick={() => { setAddingDOIAlias(false); setDoiAliasDraft(''); }}
                        className="text-xs text-gray-500 hover:underline"
                      >
                        Cancel
                      </button>
                    </span>
                    {/* Title similarity warning for alias */}
                    {doiAliasLookupEnabled && !doiAliasInfoFetching && doiAliasInfo && work && (() => {
                      if (!doiAliasInfo.found) {
                        return (
                          <span className="text-[10px] text-red-600">
                            DOI not found on OpenAlex or Crossref.
                          </span>
                        );
                      }
                      if (doiAliasInfo.title) {
                        const sim = titleSimilarity(work.title, doiAliasInfo.title);
                        if (sim < 0.7) {
                          return (
                            <span className="text-[10px] text-amber-700 leading-tight">
                              ⚠ Remote title differs ({Math.round(sim * 100)}% match):<br />
                              <em>{doiAliasInfo.title}</em>
                            </span>
                          );
                        }
                        return (
                          <span className="text-[10px] text-green-700">
                            ✓ Title matches: <em>{doiAliasInfo.title}</em>
                          </span>
                        );
                      }
                      return null;
                    })()}
                    {doiAliasLookupEnabled && doiAliasInfoFetching && (
                      <span className="text-[10px] text-gray-400">Looking up DOI…</span>
                    )}
                  </span>
                ) : (
                  <button
                    onClick={() => { setDoiAliasDraft(''); setAddingDOIAlias(true); }}
                    className="text-[10px] text-gray-400 hover:text-blue-600 text-left"
                  >
                    + Add DOI
                  </button>
                )}
              </span>
            )}
          </>
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
          <>
            <span className="text-gray-500">Semantic Scholar</span>
            {editSsId ? (
              <span className="flex items-center gap-1">
                <input
                  type="text"
                  value={ssIdDraft}
                  onChange={(e) => setSsIdDraft(e.target.value)}
                  placeholder="Paste paper ID"
                  className="flex-1 border border-gray-300 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                  autoFocus
                />
                <button
                  onClick={() => {
                    updateWork.mutate(
                      { workId, data: { semantic_scholar_id: ssIdDraft.trim() || null } },
                      { onSettled: () => setEditSsId(false) },
                    );
                  }}
                  disabled={updateWork.isPending}
                  className="text-xs text-blue-600 hover:underline disabled:opacity-50"
                >
                  Save
                </button>
                <button
                  onClick={() => setEditSsId(false)}
                  className="text-xs text-gray-500 hover:underline"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <span className="flex items-center gap-1">
                {work.semantic_scholar_id ? (
                  <a
                    href={`https://www.semanticscholar.org/paper/${work.semantic_scholar_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline text-xs truncate"
                  >
                    {work.semantic_scholar_id}
                  </a>
                ) : (
                  <span className="text-gray-400 text-xs">Not set</span>
                )}
                <button
                  onClick={() => { setSsIdDraft(work.semantic_scholar_id ?? ''); setEditSsId(true); }}
                  className="text-[10px] text-gray-400 hover:text-blue-600 underline ml-1"
                >
                  {work.semantic_scholar_id ? 'Edit' : 'Set'}
                </button>
              </span>
            )}
          </>
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

        {/* Notes (collapsible) */}
        <CollapsibleSection
          sectionKey="notes"
          title="Notes"
          count={notesQuery.data?.length}
          foldState={foldState}
          onFoldChange={onFoldChange}
        >
          {/* Add note button / form */}
          {addingNote ? (
            <div className="mb-3 space-y-2 border border-gray-200 rounded p-2">
              <textarea
                value={newNoteContent}
                onChange={(e) => setNewNoteContent(e.target.value)}
                placeholder="Write a note..."
                autoFocus
                rows={3}
                className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
              />
              <input
                type="text"
                value={newNoteType}
                onChange={(e) => setNewNoteType(e.target.value)}
                placeholder="Note label (optional, e.g. key insight, limitation)"
                className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {projectId != null && (
                <div className="flex gap-3 text-xs">
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      checked={newNoteScope === 'general'}
                      onChange={() => setNewNoteScope('general')}
                    />
                    General note
                  </label>
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      checked={newNoteScope === 'project'}
                      onChange={() => setNewNoteScope('project')}
                    />
                    Project note
                  </label>
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    if (!newNoteContent.trim()) return;
                    createNote.mutate({
                      content: newNoteContent.trim(),
                      note_type: newNoteType.trim() || null,
                      project_id: newNoteScope === 'project' && projectId != null ? projectId : null,
                    }, {
                      onSuccess: () => {
                        setAddingNote(false);
                        setNewNoteContent('');
                        setNewNoteType('');
                        setNewNoteScope('general');
                      },
                    });
                  }}
                  disabled={!newNoteContent.trim() || createNote.isPending}
                  className="px-2 py-1 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                >
                  {createNote.isPending ? 'Saving...' : 'Save'}
                </button>
                <button
                  onClick={() => { setAddingNote(false); setNewNoteContent(''); setNewNoteType(''); setNewNoteScope('general'); }}
                  className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setAddingNote(true)}
              className="mb-3 text-xs text-blue-600 hover:text-blue-800"
            >
              + Add note
            </button>
          )}

          {notesQuery.isLoading && <p className="text-xs text-gray-400">Loading...</p>}

          {notesQuery.data && (() => {
            const projectNotes = notesQuery.data.filter((n) => n.project_id != null);
            const generalNotes = notesQuery.data.filter((n) => n.project_id == null);
            const hasGroups = projectNotes.length > 0 && generalNotes.length > 0;

            const renderNote = (note: WorkNote) => {
              const isEditing = editingNoteId === note.id;

              if (isEditing) {
                return (
                  <div key={note.id} className="border border-blue-200 rounded p-2 space-y-2">
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      rows={3}
                      autoFocus
                      className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                    />
                    <input
                      type="text"
                      value={editNoteType}
                      onChange={(e) => setEditNoteType(e.target.value)}
                      placeholder="Note label (optional)"
                      className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          updateNote.mutate({
                            noteId: note.id,
                            data: {
                              content: editContent.trim(),
                              note_type: editNoteType.trim() || null,
                            },
                          }, {
                            onSuccess: () => setEditingNoteId(null),
                          });
                        }}
                        disabled={!editContent.trim() || updateNote.isPending}
                        className="px-2 py-1 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                      >
                        Save
                      </button>
                      <button
                        onClick={() => setEditingNoteId(null)}
                        className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                );
              }

              return (
                <div
                  key={note.id}
                  className={`border rounded p-2 ${note.is_outdated ? 'border-gray-200 bg-gray-50 opacity-60' : 'border-gray-200'}`}
                >
                  <div className="flex items-start justify-between gap-1">
                    <p className={`text-xs text-gray-700 leading-relaxed whitespace-pre-wrap flex-1 ${note.is_outdated ? 'line-through' : ''}`}>
                      {note.content}
                    </p>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        onClick={() => {
                          setEditingNoteId(note.id);
                          setEditContent(note.content);
                          setEditNoteType(note.note_type ?? '');
                        }}
                        className="text-[10px] text-gray-400 hover:text-blue-600 underline"
                      >
                        edit
                      </button>
                      <button
                        onClick={() => setNoteToDelete(note)}
                        className="text-gray-400 hover:text-red-600 text-sm leading-none"
                        title="Delete"
                      >
                        &times;
                      </button>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                    {note.note_type && (
                      <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded font-medium">
                        {note.note_type}
                      </span>
                    )}
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      note.provenance === 'user'
                        ? 'bg-green-100 text-green-700'
                        : note.provenance === 'ai'
                        ? 'bg-blue-100 text-blue-700'
                        : 'bg-teal-100 text-teal-700'
                    }`}>
                      {note.provenance === 'user' ? 'User' : note.provenance === 'ai' ? 'AI' : 'AI reviewed'}
                    </span>
                    {note.model_id && (
                      <span className="text-[10px] text-gray-400">{note.model_id}</span>
                    )}
                    <button
                      onClick={() => updateNote.mutate({ noteId: note.id, data: { is_outdated: !note.is_outdated } })}
                      className={`text-[10px] px-1 py-0.5 rounded border ${
                        note.is_outdated
                          ? 'border-amber-300 text-amber-600 hover:bg-amber-50'
                          : 'border-gray-200 text-gray-400 hover:bg-gray-50'
                      }`}
                      title={note.is_outdated ? 'Mark as current' : 'Mark as outdated'}
                    >
                      {note.is_outdated ? 'outdated' : 'mark outdated'}
                    </button>
                    <span className="text-[10px] text-gray-400 ml-auto">
                      {new Date(note.updated_at).toLocaleDateString()}
                    </span>
                  </div>
                </div>
              );
            };

            const renderGroup = (notes: WorkNote[], label?: string) => {
              const current = notes.filter((n) => !n.is_outdated);
              const outdated = notes.filter((n) => n.is_outdated);
              return (
                <div>
                  {label && <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">{label}</p>}
                  <div className="space-y-2">
                    {current.map(renderNote)}
                    {outdated.map(renderNote)}
                  </div>
                </div>
              );
            };

            if (notesQuery.data.length === 0) {
              return <p className="text-xs text-gray-400">No notes yet</p>;
            }

            return (
              <div className="space-y-3">
                {hasGroups ? (
                  <>
                    {renderGroup(projectNotes, 'Project notes')}
                    {renderGroup(generalNotes, 'General notes')}
                  </>
                ) : (
                  renderGroup(notesQuery.data)
                )}
              </div>
            );
          })()}
        </CollapsibleSection>

        {/* PDFs (collapsible, default open) */}
        <CollapsibleSection
          sectionKey="pdfs"
          title="PDFs"
          count={pdfsQuery.data?.length}
          defaultOpen
          foldState={foldState}
          onFoldChange={onFoldChange}
        >
          {pdfsQuery.data && pdfsQuery.data.length > 0 && (
            <ul className="space-y-2 mb-2">
              {pdfsQuery.data.map((pdf) => {
                const isExtracting = extractText.isPending && extractText.variables?.pdfId === pdf.id;
                const extractErr = extractErrors[pdf.id];
                return (
                  <li key={pdf.id} className="text-xs">
                    <div className="flex items-center gap-2">
                      <a
                        href={serveWorkPDFUrl(workId, pdf.id)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline truncate"
                      >
                        {pdf.filename}
                      </a>
                      {/* Extraction status badge */}
                      {pdf.extraction_status === 'ready' && (
                        <span className="text-[10px] px-1 py-0.5 bg-green-100 text-green-700 rounded font-medium shrink-0">
                          Text ready
                        </span>
                      )}
                      {pdf.extraction_status === 'failed' && (
                        <span className="text-[10px] px-1 py-0.5 bg-red-100 text-red-700 rounded font-medium shrink-0">
                          Extraction failed
                        </span>
                      )}
                      {pdf.extraction_status === 'pending' && (
                        <span className="text-[10px] px-1 py-0.5 bg-gray-100 text-gray-500 rounded font-medium shrink-0">
                          No text
                        </span>
                      )}
                      {/* Primary badge / button */}
                      {pdf.is_primary ? (
                        <span className="text-[10px] px-1 py-0.5 bg-blue-100 text-blue-700 rounded font-medium shrink-0">
                          Primary
                        </span>
                      ) : (
                        <button
                          onClick={() => setPrimaryPDF.mutate({ workId, pdfId: pdf.id })}
                          className="text-[10px] text-gray-400 hover:text-blue-600 shrink-0"
                        >
                          Set primary
                        </button>
                      )}
                      {/* Extract / re-extract controls */}
                      <span className="ml-auto flex items-center gap-1 shrink-0">
                        {pdf.extraction_status === 'ready' && (
                          <>
                            <a
                              href={workPDFTextUrl(workId, pdf.id)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[10px] text-blue-600 hover:underline"
                            >
                              View text
                            </a>
                            <button
                              onClick={() => {
                                setExtractErrors((prev) => { const next = { ...prev }; delete next[pdf.id]; return next; });
                                extractText.mutate({ workId, pdfId: pdf.id }, {
                                  onError: (err) => setExtractErrors((prev) => ({ ...prev, [pdf.id]: err instanceof Error ? err.message : 'Unknown error' })),
                                });
                              }}
                              disabled={isExtracting}
                              title="Re-extract text"
                              className="text-gray-400 hover:text-blue-600 disabled:opacity-50"
                            >
                              {isExtracting ? (
                                <span className="inline-block w-3 h-3 border border-gray-400 border-t-transparent rounded-full animate-spin" />
                              ) : (
                                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
                                </svg>
                              )}
                            </button>
                          </>
                        )}
                        {(pdf.extraction_status === 'failed' || pdf.extraction_status === 'pending') && (
                          <button
                            onClick={() => {
                              setExtractErrors((prev) => { const next = { ...prev }; delete next[pdf.id]; return next; });
                              extractText.mutate({ workId, pdfId: pdf.id }, {
                                onError: (err) => setExtractErrors((prev) => ({ ...prev, [pdf.id]: err instanceof Error ? err.message : 'Unknown error' })),
                              });
                            }}
                            disabled={isExtracting}
                            className="text-[10px] border border-gray-300 rounded px-1 py-0.5 hover:bg-gray-50 disabled:opacity-50"
                          >
                            {isExtracting ? 'Extracting…' : 'Extract text'}
                          </button>
                        )}
                        <button
                          onClick={() => setPdfToRemove({ id: pdf.id, filename: pdf.filename })}
                          className="text-gray-400 hover:text-red-600"
                          title="Remove PDF"
                        >
                          &times;
                        </button>
                      </span>
                    </div>
                    {extractErr && (
                      <p className="text-red-600 mt-0.5 pl-0">Extraction failed: {extractErr}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                uploadPDF.mutate({ workId, file });
              }
              e.target.value = '';
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadPDF.isPending}
            className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
          >
            {uploadPDF.isPending ? 'Uploading...' : 'Attach PDF'}
          </button>
          {uploadPDF.isError && (
            <p className="text-xs text-red-600 mt-1">
              Upload failed: {uploadPDF.error instanceof Error ? uploadPDF.error.message : 'Unknown error'}
            </p>
          )}
        </CollapsibleSection>

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
              onClick={() => navigate(`/works/${workId}/discuss`)}
              className="px-2 py-1 text-xs border border-indigo-300 text-indigo-700 rounded hover:bg-indigo-50"
            >
              Discuss
            </button>
            <button
              onClick={() => fetchBwd.mutate(workId, { onSettled: onEnrichComplete })}
              disabled={fetchBwd.isPending || isAutoEnriching}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {fetchBwd.isPending ? 'Fetching...' : 'Fetch references (OA)'}
            </button>
            <button
              onClick={() => fetchFwd.mutate({ workId }, { onSettled: onEnrichComplete })}
              disabled={fetchFwd.isPending || isAutoEnriching}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {fetchFwd.isPending ? 'Fetching...' : 'Fetch citing papers (OA)'}
            </button>
            {(work.doi || work.semantic_scholar_id) && (
              <button
                onClick={() => {
                  setSsEnrichMsg(null);
                  enrichSS.mutate({ workId, direction: 'backward' }, {
                    onSuccess: (result) => {
                      if (result.raw_references === 0) {
                        setSsEnrichMsg('S2 has no reference list for this paper');
                      } else if (result.new_references === 0) {
                        setSsEnrichMsg(`All ${result.existing_references} references already in library`);
                      } else {
                        setSsEnrichMsg(
                          `Added ${result.new_references} new references` +
                          (result.existing_references > 0
                            ? ` (${result.existing_references} already existed)`
                            : '')
                        );
                      }
                      onEnrichComplete?.();
                    },
                    onError: (err) => {
                      setSsEnrichMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
                    },
                  });
                }}
                disabled={enrichSS.isPending || isAutoEnriching}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                {enrichSS.isPending ? 'Fetching...' : 'Fetch references (S2)'}
              </button>
            )}
            {(work.doi || work.semantic_scholar_id) && (
              <button
                onClick={() => {
                  setSsEnrichMsg(null);
                  enrichSS.mutate({ workId, direction: 'forward' }, {
                    onSuccess: (result) => {
                      if (result.raw_citing === 0) {
                        setSsEnrichMsg('S2 has no citing papers for this paper');
                      } else if (result.new_citing === 0) {
                        setSsEnrichMsg(`All ${result.existing_citing} citing papers already in library`);
                      } else {
                        setSsEnrichMsg(
                          `Added ${result.new_citing} new citing papers` +
                          (result.existing_citing > 0
                            ? ` (${result.existing_citing} already existed)`
                            : '')
                        );
                      }
                      onEnrichComplete?.();
                    },
                    onError: (err) => {
                      setSsEnrichMsg(`Error: ${err instanceof Error ? err.message : String(err)}`);
                    },
                  });
                }}
                disabled={enrichSS.isPending || isAutoEnriching}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                {enrichSS.isPending ? 'Fetching...' : 'Fetch citing papers (S2)'}
              </button>
            )}
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
                {resolveDOI.isPending ? 'Resolving...' : 'Resolve DOI (CrossRef)'}
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
          {ssEnrichMsg && (
            <p className={`text-xs mt-1 ${ssEnrichMsg.startsWith('Error') ? 'text-red-600' : 'text-green-600'}`}>
              {ssEnrichMsg}
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

    {pdfToRemove && (
      <ConfirmDialog
        title="Remove PDF"
        message={`Remove "${pdfToRemove.filename}"? The file will be moved to an orphaned folder, not permanently deleted.`}
        onCancel={() => setPdfToRemove(null)}
        onConfirm={() => {
          removePDF.mutate({ workId, pdfId: pdfToRemove.id });
          setPdfToRemove(null);
        }}
      />
    )}

    {noteToDelete && (
      <ConfirmDialog
        title="Delete Note"
        message="Are you sure you want to permanently delete this note?"
        confirmLabel="Delete"
        onCancel={() => setNoteToDelete(null)}
        onConfirm={() => {
          deleteNote.mutate(noteToDelete.id);
          setNoteToDelete(null);
        }}
      />
    )}
    </>
  );
}
