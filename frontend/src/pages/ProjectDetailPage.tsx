import { useState, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import SearchInput from '../components/SearchInput';
import EmptyState from '../components/EmptyState';
import TopicListCard from '../components/TopicListCard';
import TopicListFormDialog from '../components/TopicListFormDialog';
import WorkDetailPanel from '../components/WorkDetailPanel';
import ConfirmDialog from '../components/ConfirmDialog';
import CitationTimeline from '../components/CitationTimeline';
import TimelineControls, { type CandidateFilter } from '../components/TimelineControls';
import TimelineEnrichBar from '../components/TimelineEnrichBar';
import {
  useProject, useCreateTopicList, useUpdateTopicList, useDeleteTopicList, useAddWorkToTopicList,
  useAddIgnoredWork, useRemoveIgnoredWork,
} from '../hooks/useProjects';
import { useQueryClient } from '@tanstack/react-query';
import { useWorks } from '../hooks/useWorks';
import { useTimeline } from '../hooks/useTimeline';
import { fetchBackwardCitationsEnrich, fetchForwardCitationsEnrich, enrichFromCrossref } from '../api';
import { filterNeighbors } from '../lib/timelineFilter';
import type { TimelineNeighborWork } from '../types';

type ActiveTab = 'timeline' | 'lists';

export default function ProjectDetailPage() {
  const { projectId: pid } = useParams<{ projectId: string }>();
  const projectId = Number(pid);
  const navigate = useNavigate();

  const { data: project, isLoading } = useProject(projectId);

  // Tab state
  const [activeTab, setActiveTab] = useState<ActiveTab>('timeline');

  // Timeline filter state
  const [threshold, setThreshold] = useState(0.5);
  const [decayStartYears, setDecayStartYears] = useState(5);
  const [showBackward, setShowBackward] = useState(true);
  const [showForward, setShowForward] = useState(true);
  const [startYear, setStartYear] = useState<number | null>(null);
  const [candidateFilter, setCandidateFilter] = useState<CandidateFilter>('all');
  const [hops, setHops] = useState(1);

  // Shared state
  const [showCreateList, setShowCreateList] = useState(false);
  const [editList, setEditList] = useState<{ id: number; name: string; color: string } | null>(null);
  const [deleteListId, setDeleteListId] = useState<number | null>(null);
  const [selectedWorkId, setSelectedWorkId] = useState<number | null>(null);

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
    let result = filterNeighbors(timeline.neighbors, tier1Set, ignoredSet, {
      threshold,
      decayStartYears,
      showBackward,
      showForward,
      currentYear: new Date().getFullYear(),
    });
    if (candidateFilter === 'top-venues') {
      result = result.filter((n) => n.venue_id != null && tier1Set.has(n.venue_id));
    }
    return result;
  }, [timeline, tier1Set, ignoredSet, threshold, decayStartYears, showBackward, showForward, candidateFilter]);

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
            Back
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
        </div>

        {activeTab === 'timeline' && (
          <div className="flex-1 flex flex-col min-h-0">
            {timeline && (
              <TimelineEnrichBar seeds={timeline.seeds} projectId={projectId} />
            )}
            <TimelineControls
              threshold={threshold}
              onThresholdChange={setThreshold}
              decayStartYears={decayStartYears}
              onDecayStartYearsChange={setDecayStartYears}
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
                seeds={timeline?.seeds ?? []}
                neighbors={filteredNeighbors}
                topicLists={timeline?.topic_lists ?? project.topic_lists}
                seedCitations={timeline?.seed_citations ?? []}
                selectedWorkId={selectedWorkId}
                onSelectWork={setSelectedWorkId}
                decayStartYears={decayStartYears}
                startYear={startYear}
                showBackward={showBackward}
                showForward={showForward}
                tier1VenueIds={tier1Set}
                hops={hops}
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
      </div>

      {selectedWorkId !== null && (
        <WorkDetailPanel
          workId={selectedWorkId}
          onClose={() => setSelectedWorkId(null)}
          topicLists={project.topic_lists}
          onAddToList={(tlId) => handleAddWorkToList(tlId, selectedWorkId)}
          onMarkUninteresting={(wid) => addIgnored.mutate({ projectId, workId: wid })}
          onEnrichComplete={handleEnrichComplete}
          timelineContext={activeTab === 'timeline' ? timelineContext : undefined}
          isAutoEnriching={enrichingWorkIds.has(selectedWorkId)}
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
    </div>
  );
}
