import { useState, useCallback, useEffect, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import EmptyState from '../components/EmptyState';
import TopicListCard from '../components/TopicListCard';
import TopicListFormDialog from '../components/TopicListFormDialog';
import WorkDetailPanel, { DEFAULT_FOLD_STATE, type PanelFoldState } from '../components/WorkDetailPanel';
import ConfirmDialog from '../components/ConfirmDialog';
import CitationTimeline from '../components/CitationTimeline';
import TimelineControls, { type CandidateFilter } from '../components/TimelineControls';
import TimelineEnrichBar from '../components/TimelineEnrichBar';
import {
  useProject, useCreateTopicList, useUpdateTopicList, useDeleteTopicList, useAddWorkToTopicList,
  useAddIgnoredWork, useRemoveIgnoredWork, useRemoveWorkFromTopicList,
} from '../hooks/useProjects';
import { useQueryClient } from '@tanstack/react-query';
import { useWorks } from '../hooks/useWorks';
import { useTimeline } from '../hooks/useTimeline';
import { fetchBackwardCitationsEnrich, fetchForwardCitationsEnrich, enrichFromCrossref } from '../api';
import { filterNeighbors } from '../lib/timelineFilter';
import { useProjectNotes } from '../hooks/useWorkNotes';
import { updateWorkNote, deleteWorkNote } from '../api';
import type { TimelineNeighborWork, ProjectNote } from '../types';

type ActiveTab = 'timeline' | 'lists' | 'notes';

interface ProjectViewSettings {
  activeTab?: ActiveTab;
  citationsSinceYears?: number | null;
  showBackward?: boolean;
  showForward?: boolean;
  startYear?: number | null;
  candidateFilter?: CandidateFilter;
  hops?: number;
  inactiveTopicListIds?: number[];
}

function loadProjectSettings(projectId: number): ProjectViewSettings {
  try {
    const raw = localStorage.getItem(`litexplorer:project:${projectId}:view`);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export default function ProjectDetailPage() {
  const { projectId: pid } = useParams<{ projectId: string }>();
  const projectId = Number(pid);
  const navigate = useNavigate();

  const { data: project, isLoading } = useProject(projectId);

  // Load persisted settings for this project
  const saved = useMemo(() => loadProjectSettings(projectId), [projectId]);

  // Tab state
  const [activeTab, setActiveTab] = useState<ActiveTab>(saved.activeTab ?? 'timeline');

  // Timeline filter state
  const [citationsSinceYears, setCitationsSinceYears] = useState<number | null>(
    saved.citationsSinceYears !== undefined ? saved.citationsSinceYears : null,
  );
  const [showBackward, setShowBackward] = useState(saved.showBackward ?? true);
  const [showForward, setShowForward] = useState(saved.showForward ?? true);
  const [startYear, setStartYear] = useState<number | null>(saved.startYear ?? null);
  const [candidateFilter, setCandidateFilter] = useState<CandidateFilter>(saved.candidateFilter ?? 'all');
  const [hops, setHops] = useState(saved.hops ?? 1);
  const [inactiveTopicListIds, setInactiveTopicListIds] = useState<Set<number>>(
    () => new Set(saved.inactiveTopicListIds ?? []),
  );

  // Persist view settings to localStorage
  useEffect(() => {
    const settings: ProjectViewSettings = {
      activeTab, citationsSinceYears, showBackward, showForward,
      startYear, candidateFilter, hops,
      inactiveTopicListIds: [...inactiveTopicListIds],
    };
    localStorage.setItem(`litexplorer:project:${projectId}:view`, JSON.stringify(settings));
  }, [projectId, activeTab, citationsSinceYears, showBackward, showForward, startYear, candidateFilter, hops, inactiveTopicListIds]);

  // Shared state
  const [showCreateList, setShowCreateList] = useState(false);
  const [editList, setEditList] = useState<{ id: number; name: string; color: string } | null>(null);
  const [deleteListId, setDeleteListId] = useState<number | null>(null);
  const [selectedWorkId, setSelectedWorkId] = useState<number | null>(null);

  // Detail panel fold state (persists across paper selections within this project)
  const [panelFoldState, setPanelFoldState] = useState<PanelFoldState>({ ...DEFAULT_FOLD_STATE });

  // Notes tab state
  const [notesSortBy, setNotesSortBy] = useState<'paper' | 'label'>('paper');
  const projectNotes = useProjectNotes(projectId);
  const [noteToDeleteId, setNoteToDeleteId] = useState<{ workId: number; noteId: number } | null>(null);

  // Auto-enrichment tracking
  const [enrichingWorkIds, setEnrichingWorkIds] = useState<Set<number>>(new Set());

  // Work search for adding to lists
  const [showWorkSearch, setShowWorkSearch] = useState<number | null>(null);
  const [workSearch, setWorkSearch] = useState('');
  const { data: searchedWorks } = useWorks({
    q: workSearch || undefined,
    limit: 10,
  });

  // Timeline data
  const qc = useQueryClient();
  const { data: timeline } = useTimeline(projectId);

  const handleEnrichComplete = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
  }, [qc, projectId]);

  const createList = useCreateTopicList();
  const updateList = useUpdateTopicList();
  const deleteList = useDeleteTopicList();
  const addWork = useAddWorkToTopicList();
  const addIgnored = useAddIgnoredWork();
  const removeIgnored = useRemoveIgnoredWork();
  const removeWork = useRemoveWorkFromTopicList();

  const handleAddWorkToList = useCallback((topicListId: number, workId: number) => {
    addWork.mutate({ projectId, topicListId, workId }, {
      onSuccess: async () => {
        setEnrichingWorkIds((prev) => new Set(prev).add(workId));
        try {
          // Auto-enrich: fetch references, citing papers, and crossref in parallel
          await Promise.allSettled([
            fetchBackwardCitationsEnrich(workId).catch(() => {}),
            fetchForwardCitationsEnrich(workId).catch(() => {}),
            enrichFromCrossref(workId).catch(() => {}),
          ]);
          qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
          qc.invalidateQueries({ queryKey: ['works', workId] });
        } finally {
          setEnrichingWorkIds((prev) => {
            const next = new Set(prev);
            next.delete(workId);
            return next;
          });
        }
      },
    });
  }, [addWork, projectId, qc]);

  const handleWorkSearch = useCallback((v: string) => setWorkSearch(v), []);

  // Filter neighbors
  const tier1Set = useMemo(
    () => new Set(timeline?.tier1_venue_ids ?? []),
    [timeline?.tier1_venue_ids],
  );

  const ignoredSet = useMemo(
    () => new Set(timeline?.ignored_venue_ids ?? []),
    [timeline?.ignored_venue_ids],
  );

  // IDs of topic lists that are currently active
  const activeTopicListIds = useMemo(() => {
    if (!timeline) return new Set<number>();
    return new Set(
      timeline.topic_lists.map((tl) => tl.id).filter((id) => !inactiveTopicListIds.has(id)),
    );
  }, [timeline, inactiveTopicListIds]);

  // Seeds that have at least one active topic list
  const activeSeedIds = useMemo(() => {
    if (!timeline) return new Set<number>();
    return new Set(
      timeline.seeds
        .filter((s) => s.topic_list_ids.some((id) => activeTopicListIds.has(id)))
        .map((s) => s.id),
    );
  }, [timeline, activeTopicListIds]);

  // Seeds filtered to active only
  const filteredSeeds = useMemo(() => {
    if (!timeline) return [];
    if (inactiveTopicListIds.size === 0) return timeline.seeds;
    return timeline.seeds.filter((s) => activeSeedIds.has(s.id));
  }, [timeline, activeSeedIds, inactiveTopicListIds]);

  // Seed citations between active seeds only
  const filteredSeedCitations = useMemo(() => {
    if (!timeline) return [];
    if (inactiveTopicListIds.size === 0) return timeline.seed_citations;
    return timeline.seed_citations.filter(
      (sc) => activeSeedIds.has(sc.citing_seed_id) && activeSeedIds.has(sc.cited_seed_id),
    );
  }, [timeline, activeSeedIds, inactiveTopicListIds]);

  // Compute year range from all timeline data (seeds + neighbors)
  const yearRange = useMemo(() => {
    if (!timeline) return { min: null, max: null };
    const years: number[] = [];
    for (const s of timeline.seeds) {
      if (s.publication_year != null) years.push(s.publication_year);
    }
    for (const n of timeline.neighbors) {
      if (n.publication_year != null) years.push(n.publication_year);
    }
    if (years.length === 0) return { min: null, max: null };
    return { min: Math.min(...years), max: Math.max(...years) };
  }, [timeline]);

  const filteredNeighbors: TimelineNeighborWork[] = useMemo(() => {
    if (!timeline || candidateFilter === 'none') return [];
    let result = filterNeighbors(timeline.neighbors, ignoredSet, {
      showBackward,
      showForward,
    });
    if (candidateFilter === 'top-venues') {
      result = result.filter((n) => n.venue_id != null && tier1Set.has(n.venue_id));
    }
    if (inactiveTopicListIds.size > 0) {
      result = result.filter((n) => n.connected_seed_ids.some((sid) => activeSeedIds.has(sid)));
    }
    return result;
  }, [timeline, tier1Set, ignoredSet, showBackward, showForward, candidateFilter, activeSeedIds, inactiveTopicListIds]);

  // Seed color map: seed work ID → array of topic list colors (active TLs only)
  const seedColorMap = useMemo(() => {
    if (!timeline) return new Map<number, string[]>();
    const tlColorMap = new Map(timeline.topic_lists.map((tl) => [tl.id, tl.color]));
    const map = new Map<number, string[]>();
    for (const seed of timeline.seeds) {
      const colors = seed.topic_list_ids
        .filter((tlId) => activeTopicListIds.has(tlId))
        .map((tlId) => tlColorMap.get(tlId))
        .filter((c): c is string => c != null);
      map.set(seed.id, colors.length > 0 ? colors : ['#6b7280']);
    }
    return map;
  }, [timeline, activeTopicListIds]);

  const handleToggleTopicList = useCallback((tlId: number) => {
    setInactiveTopicListIds((prev) => {
      const next = new Set(prev);
      if (next.has(tlId)) next.delete(tlId); else next.add(tlId);
      return next;
    });
  }, []);

  // All work IDs rendered in the timeline (seeds + filtered neighbors)
  const renderedWorkIds = useMemo(() => {
    const ids = new Set<number>();
    if (timeline) {
      for (const s of timeline.seeds) ids.add(s.id);
    }
    for (const n of filteredNeighbors) ids.add(n.id);
    return ids;
  }, [timeline, filteredNeighbors]);

  // Ignored work IDs for this project
  const ignoredWorkIds = useMemo(
    () => new Set(project?.ignored_works.map((iw) => iw.work_id) ?? []),
    [project?.ignored_works],
  );

  // Topic list IDs for the currently selected work
  const workTopicListIds = useMemo(() => {
    if (!timeline || selectedWorkId == null) return [];
    const seed = timeline.seeds.find((s) => s.id === selectedWorkId);
    return seed?.topic_list_ids ?? [];
  }, [timeline, selectedWorkId]);

  const handleRemoveFromList = useCallback((topicListId: number) => {
    if (selectedWorkId == null) return;
    removeWork.mutate({ projectId, topicListId, workId: selectedWorkId }, {
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
        qc.invalidateQueries({ queryKey: ['projects', projectId] });
      },
    });
  }, [removeWork, projectId, selectedWorkId, qc]);

  // Timeline context for WorkDetailPanel
  const timelineContext = useMemo(() => {
    if (!timeline || selectedWorkId == null) return undefined;
    const seed = timeline.seeds.find((s) => s.id === selectedWorkId);
    if (seed) {
      return {
        direction: 'seed' as const,
        connectedSeeds: [] as { id: number; title: string; color: string }[],
        forwardCitationsFetchedAt: seed.forward_citations_fetched_at,
      };
    }
    const neighbor = timeline.neighbors.find((n) => n.id === selectedWorkId);
    if (neighbor) {
      const tlColorMap = new Map(timeline.topic_lists.map((tl) => [tl.id, tl.color]));
      const connected = neighbor.connected_seed_ids
        .map((sid) => {
          const s = timeline.seeds.find((x) => x.id === sid);
          if (!s) return null;
          const color = s.topic_list_ids.length > 0
            ? (tlColorMap.get(s.topic_list_ids[0]) ?? '#6b7280')
            : '#6b7280';
          return { id: s.id, title: s.title, color };
        })
        .filter((x): x is { id: number; title: string; color: string } => x != null);
      return {
        direction: neighbor.direction as 'backward' | 'forward',
        connectedSeeds: connected,
        forwardCitationsFetchedAt: null,
      };
    }
    return undefined;
  }, [timeline, selectedWorkId]);

  if (isLoading || !project) {
    return <p className="p-6 text-sm text-gray-400">Loading...</p>;
  }

  return (
    <div className="flex h-screen">
      <div className="flex-1 flex flex-col min-w-0">
        <PageHeader title={project.name}>
          <button
            onClick={() => navigate('/projects')}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            To project overview
          </button>
          <button
            onClick={() => navigate(`/projects/${projectId}/discuss`)}
            className="px-3 py-1.5 text-sm font-medium text-indigo-700 border border-indigo-300 rounded hover:bg-indigo-50"
          >
            Discuss
          </button>
          <button
            onClick={() => setShowCreateList(true)}
            className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
          >
            New Topic List
          </button>
        </PageHeader>

        {project.description && (
          <p className="px-6 pt-3 text-sm text-gray-500">{project.description}</p>
        )}

        {/* Tab switcher */}
        <div className="flex border-b border-gray-200 px-6">
          <button
            onClick={() => setActiveTab('timeline')}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              activeTab === 'timeline'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Timeline
          </button>
          <button
            onClick={() => setActiveTab('lists')}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              activeTab === 'lists'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Topic Lists
          </button>
          <button
            onClick={() => setActiveTab('notes')}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
              activeTab === 'notes'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Notes
            {projectNotes.data && projectNotes.data.length > 0 && (
              <span className="ml-1 text-xs text-gray-400">({projectNotes.data.length})</span>
            )}
          </button>
        </div>

        {activeTab === 'timeline' && (
          <div className="flex-1 flex flex-col min-h-0">
            {timeline && (
              <TimelineEnrichBar seeds={timeline.seeds} projectId={projectId} />
            )}
            <TimelineControls
              citationsSinceYears={citationsSinceYears}
              onCitationsSinceYearsChange={setCitationsSinceYears}
              showBackward={showBackward}
              onShowBackwardChange={setShowBackward}
              showForward={showForward}
              onShowForwardChange={setShowForward}
              startYear={startYear}
              onStartYearChange={setStartYear}
              minYear={yearRange.min}
              maxYear={yearRange.max}
              totalNeighbors={timeline?.neighbors.length ?? 0}
              filteredNeighbors={filteredNeighbors.length}
              candidateFilter={candidateFilter}
              onCandidateFilterChange={setCandidateFilter}
              hops={hops}
              onHopsChange={setHops}
            />
            <div className="flex-1 min-h-0">
              <CitationTimeline
                seeds={filteredSeeds}
                neighbors={filteredNeighbors}
                topicLists={timeline?.topic_lists ?? project.topic_lists}
                seedCitations={filteredSeedCitations}
                selectedWorkId={selectedWorkId}
                onSelectWork={setSelectedWorkId}
                citationsSinceYears={citationsSinceYears}
                startYear={startYear}
                showBackward={showBackward}
                showForward={showForward}
                tier1VenueIds={tier1Set}
                hops={hops}
                activeTopicListIds={activeTopicListIds}
                onToggleTopicList={handleToggleTopicList}
              />
            </div>
          </div>
        )}

        {activeTab === 'lists' && (
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {!project.topic_lists.length ? (
              <EmptyState message="No topic lists yet.">
                <button
                  onClick={() => setShowCreateList(true)}
                  className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
                >
                  Create a topic list
                </button>
              </EmptyState>
            ) : (
              project.topic_lists.map((tl) => (
                <div key={tl.id}>
                  <TopicListCard
                    topicList={tl}
                    projectId={projectId}
                    onEdit={() => setEditList({ id: tl.id, name: tl.name, color: tl.color })}
                    onDelete={() => setDeleteListId(tl.id)}
                    onSelectWork={(wid) => setSelectedWorkId(wid)}
                  />
                  <div className="mt-2 ml-8">
                    {showWorkSearch === tl.id ? (
                      <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
                        <div className="flex items-center gap-2 mb-2">
                          <SearchInput value={workSearch} onChange={handleWorkSearch} placeholder="Search library..." />
                          <button
                            onClick={() => { setShowWorkSearch(null); setWorkSearch(''); }}
                            className="text-xs text-gray-500 hover:text-gray-700"
                          >
                            Cancel
                          </button>
                        </div>
                        {searchedWorks?.map((w) => (
                          <button
                            key={w.id}
                            onClick={() => {
                              handleAddWorkToList(tl.id, w.id);
                            }}
                            className="w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-white rounded"
                          >
                            {w.title} <span className="text-gray-400">({w.publication_year})</span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowWorkSearch(tl.id)}
                        className="text-xs text-blue-600 hover:text-blue-800"
                      >
                        + Add works
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}

            {/* Ignored papers */}
            {project.ignored_works.length > 0 && (
              <div className="mt-6 pt-6 border-t border-gray-200">
                <h3 className="text-sm font-semibold text-gray-700 mb-3">
                  Ignored Papers ({project.ignored_works.length})
                </h3>
                <ul className="space-y-2">
                  {project.ignored_works.map((iw) => (
                    <li key={iw.id} className="flex items-center justify-between gap-2 text-sm text-gray-600">
                      <span className="truncate">
                        {iw.work.title}
                        {iw.work.publication_year && (
                          <span className="text-gray-400 ml-1">({iw.work.publication_year})</span>
                        )}
                      </span>
                      <button
                        onClick={() => removeIgnored.mutate({ projectId, workId: iw.work_id })}
                        className="shrink-0 text-xs text-gray-400 hover:text-gray-600"
                        title="Restore paper"
                      >
                        &times;
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {activeTab === 'notes' && (
          <div className="flex-1 overflow-y-auto p-6">
            {/* Sort controls */}
            <div className="flex items-center gap-3 mb-4">
              <span className="text-xs text-gray-500">Sort by:</span>
              <button
                onClick={() => setNotesSortBy('paper')}
                className={`text-xs px-2 py-1 rounded ${
                  notesSortBy === 'paper' ? 'bg-blue-100 text-blue-700 font-medium' : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                Paper
              </button>
              <button
                onClick={() => setNotesSortBy('label')}
                className={`text-xs px-2 py-1 rounded ${
                  notesSortBy === 'label' ? 'bg-blue-100 text-blue-700 font-medium' : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                Note label
              </button>
            </div>

            {projectNotes.isLoading && <p className="text-sm text-gray-400">Loading...</p>}

            {projectNotes.data && projectNotes.data.length === 0 && (
              <p className="text-sm text-gray-400">No notes yet. Add notes from the paper detail panel.</p>
            )}

            {projectNotes.data && projectNotes.data.length > 0 && (() => {
              const qc2 = qc;
              const notes = [...projectNotes.data];

              if (notesSortBy === 'paper') {
                notes.sort((a, b) => a.work_title.localeCompare(b.work_title) || a.created_at.localeCompare(b.created_at));
              } else {
                notes.sort((a, b) => (a.note_type ?? '').localeCompare(b.note_type ?? '') || a.created_at.localeCompare(b.created_at));
              }

              // Group notes
              type Group = { key: string; notes: ProjectNote[] };
              const groups: Group[] = [];
              const groupMap = new Map<string, ProjectNote[]>();

              for (const note of notes) {
                const key = notesSortBy === 'paper'
                  ? `${note.work_id}:${note.work_title}`
                  : (note.note_type || '(no label)');
                if (!groupMap.has(key)) {
                  groupMap.set(key, []);
                  groups.push({ key, notes: groupMap.get(key)! });
                }
                groupMap.get(key)!.push(note);
              }

              return (
                <div className="space-y-6">
                  {groups.map((group) => (
                    <div key={group.key}>
                      <h3 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
                        {notesSortBy === 'paper' ? (
                          <>
                            <button
                              onClick={() => {
                                setSelectedWorkId(group.notes[0].work_id);
                                setActiveTab('timeline');
                              }}
                              className="hover:text-blue-600 text-left"
                            >
                              {group.notes[0].work_title}
                            </button>
                            {group.notes[0].work_publication_year && (
                              <span className="text-gray-400 font-normal">({group.notes[0].work_publication_year})</span>
                            )}
                          </>
                        ) : (
                          <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs font-medium">
                            {group.key}
                          </span>
                        )}
                      </h3>
                      <div className="space-y-2 ml-2">
                        {group.notes.map((note) => (
                          <div
                            key={note.id}
                            className={`border rounded p-3 ${note.is_outdated ? 'border-gray-200 bg-gray-50 opacity-60' : 'border-gray-200'}`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <p className={`text-sm text-gray-700 leading-relaxed whitespace-pre-wrap flex-1 ${note.is_outdated ? 'line-through' : ''}`}>
                                {note.content}
                              </p>
                              <div className="flex items-center gap-2 shrink-0">
                                <button
                                  onClick={() => {
                                    updateWorkNote(note.work_id, note.id, { is_outdated: !note.is_outdated }).then(() => {
                                      qc2.invalidateQueries({ queryKey: ['projectNotes'] });
                                      qc2.invalidateQueries({ queryKey: ['workNotes', note.work_id] });
                                    });
                                  }}
                                  className={`text-[10px] px-1 py-0.5 rounded border ${
                                    note.is_outdated
                                      ? 'border-amber-300 text-amber-600 hover:bg-amber-50'
                                      : 'border-gray-200 text-gray-400 hover:bg-gray-50'
                                  }`}
                                >
                                  {note.is_outdated ? 'outdated' : 'mark outdated'}
                                </button>
                                <button
                                  onClick={() => setNoteToDeleteId({ workId: note.work_id, noteId: note.id })}
                                  className="text-gray-400 hover:text-red-600 text-sm leading-none"
                                  title="Delete"
                                >
                                  &times;
                                </button>
                              </div>
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                              {notesSortBy === 'label' && (
                                <button
                                  onClick={() => {
                                    setSelectedWorkId(note.work_id);
                                    setActiveTab('timeline');
                                  }}
                                  className="text-[10px] text-blue-600 hover:underline"
                                >
                                  {note.work_title}
                                </button>
                              )}
                              {notesSortBy === 'paper' && note.note_type && (
                                <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded font-medium">
                                  {note.note_type}
                                </span>
                              )}
                              {note.project_id != null ? (
                                <span className="text-[10px] px-1.5 py-0.5 bg-purple-100 text-purple-700 rounded font-medium">Project</span>
                              ) : (
                                <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded font-medium">General</span>
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
                              <span className="text-[10px] text-gray-400 ml-auto">
                                {new Date(note.updated_at).toLocaleDateString()}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        )}
      </div>

      {selectedWorkId !== null && (
        <WorkDetailPanel
          key={selectedWorkId}
          workId={selectedWorkId}
          onClose={() => setSelectedWorkId(null)}
          projectId={projectId}
          topicLists={project.topic_lists}
          onAddToList={(tlId) => handleAddWorkToList(tlId, selectedWorkId)}
          onRemoveFromList={handleRemoveFromList}
          onMarkUninteresting={(wid) => addIgnored.mutate({ projectId, workId: wid })}
          onEnrichComplete={handleEnrichComplete}
          timelineContext={activeTab === 'timeline' ? timelineContext : undefined}
          isAutoEnriching={enrichingWorkIds.has(selectedWorkId)}
          seedColorMap={activeTab === 'timeline' ? seedColorMap : undefined}
          renderedWorkIds={activeTab === 'timeline' ? renderedWorkIds : undefined}
          ignoredWorkIds={ignoredWorkIds}
          onSelectWork={setSelectedWorkId}
          workTopicListIds={workTopicListIds}
          foldState={panelFoldState}
          onFoldChange={setPanelFoldState}
        />
      )}

      {showCreateList && (
        <TopicListFormDialog
          onCancel={() => setShowCreateList(false)}
          onSubmit={(data) => {
            createList.mutate({ projectId, data }, { onSuccess: () => setShowCreateList(false) });
          }}
        />
      )}

      {editList && (
        <TopicListFormDialog
          initial={{ name: editList.name, color: editList.color }}
          onCancel={() => setEditList(null)}
          onSubmit={(data) => {
            updateList.mutate(
              { projectId, topicListId: editList.id, data },
              { onSuccess: () => setEditList(null) },
            );
          }}
        />
      )}

      {deleteListId !== null && (
        <ConfirmDialog
          title="Delete topic list"
          message="This will remove the topic list and all its work associations."
          onCancel={() => setDeleteListId(null)}
          onConfirm={() => {
            deleteList.mutate(
              { projectId, topicListId: deleteListId },
              { onSuccess: () => setDeleteListId(null) },
            );
          }}
        />
      )}

      {noteToDeleteId && (
        <ConfirmDialog
          title="Delete Note"
          message="Are you sure you want to permanently delete this note?"
          confirmLabel="Delete"
          onCancel={() => setNoteToDeleteId(null)}
          onConfirm={() => {
            deleteWorkNote(noteToDeleteId.workId, noteToDeleteId.noteId).then(() => {
              qc.invalidateQueries({ queryKey: ['projectNotes'] });
              qc.invalidateQueries({ queryKey: ['workNotes', noteToDeleteId.workId] });
              setNoteToDeleteId(null);
            });
          }}
        />
      )}
    </div>
  );
}
