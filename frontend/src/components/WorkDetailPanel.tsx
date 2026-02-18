import { useWork, useForwardCitations, useBackwardCitations } from '../hooks/useWorks';
import { useFetchBackwardCitations, useFetchForwardCitations, useEnrichFromCrossref } from '../hooks/useEnrichment';
import type { CitationWorkBrief, TopicListOut } from '../types';

function CitationList({ title, items, isLoading }: { title: string; items?: CitationWorkBrief[]; isLoading: boolean }) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{title}</h4>
      {isLoading ? (
        <p className="text-xs text-gray-400">Loading...</p>
      ) : !items?.length ? (
        <p className="text-xs text-gray-400">None fetched yet</p>
      ) : (
        <ul className="space-y-1">
          {items.map((c) => (
            <li key={c.id} className="text-xs text-gray-700 leading-snug">
              {c.title} {c.publication_year && <span className="text-gray-400">({c.publication_year})</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface TimelineContext {
  direction: 'seed' | 'backward' | 'forward';
  connectedSeeds: { id: number; title: string; color: string }[];
  forwardCitationsFetchedAt: string | null;
}

export default function WorkDetailPanel({
  workId,
  onClose,
  topicLists,
  onAddToList,
  onMarkUninteresting,
  timelineContext,
}: {
  workId: number;
  onClose: () => void;
  topicLists?: TopicListOut[];
  onAddToList?: (topicListId: number) => void;
  onMarkUninteresting?: (workId: number) => void;
  timelineContext?: TimelineContext;
}) {
  const { data: work, isLoading } = useWork(workId);
  const fwd = useForwardCitations(workId);
  const bwd = useBackwardCitations(workId);

  const fetchBwd = useFetchBackwardCitations();
  const fetchFwd = useFetchForwardCitations();
  const crossref = useEnrichFromCrossref();

  if (isLoading || !work) {
    return (
      <div className="w-96 border-l border-gray-200 bg-white p-6">
        <p className="text-sm text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="w-96 border-l border-gray-200 bg-white flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-gray-200">
        <h3 className="text-sm font-semibold text-gray-900 leading-snug pr-2">{work.title}</h3>
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
              <span className="text-gray-800">{work.venue_name}</span>
            </>
          )}
          {work.doi && (
            <>
              <span className="text-gray-500">DOI</span>
              <a
                href={`https://doi.org/${work.doi}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline truncate"
              >
                {work.doi}
              </a>
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

        {/* Abstract */}
        {work.abstract && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Abstract</h4>
            <p className="text-xs text-gray-700 leading-relaxed">{work.abstract}</p>
          </div>
        )}

        {/* Locations */}
        {work.locations.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Locations</h4>
            <ul className="space-y-1">
              {work.locations.map((loc) => (
                <li key={loc.id} className="text-xs">
                  <a href={loc.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                    {loc.location_type}{loc.is_primary ? ' (primary)' : ''}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Enrich actions */}
        <div>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Actions</h4>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => fetchBwd.mutate(workId)}
              disabled={fetchBwd.isPending}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {fetchBwd.isPending ? 'Fetching...' : 'Fetch References'}
            </button>
            <button
              onClick={() => fetchFwd.mutate({ workId })}
              disabled={fetchFwd.isPending}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {fetchFwd.isPending ? 'Fetching...' : 'Fetch Citing Papers'}
            </button>
            {work.doi && (
              <button
                onClick={() => crossref.mutate(workId)}
                disabled={crossref.isPending}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                {crossref.isPending ? 'Enriching...' : 'Enrich from Crossref'}
              </button>
            )}
          </div>
          {fetchBwd.data && (
            <p className="text-xs text-green-600 mt-1">Fetched {fetchBwd.data.count} references</p>
          )}
          {fetchFwd.data && (
            <p className="text-xs text-green-600 mt-1">Fetched {fetchFwd.data.count} citing papers</p>
          )}
        </div>

        {/* Citations */}
        <CitationList title="References (backward)" items={bwd.data} isLoading={bwd.isLoading} />
        <CitationList title="Cited by (forward)" items={fwd.data} isLoading={fwd.isLoading} />

        {/* Timeline context */}
        {timelineContext && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              Timeline
            </h4>
            <p className="text-xs text-gray-600 mb-1">
              Role: <span className="font-medium capitalize">{timelineContext.direction}</span>
            </p>
            {timelineContext.connectedSeeds.length > 0 && (
              <div className="mt-1">
                <p className="text-xs text-gray-500 mb-1">Connected seeds:</p>
                <ul className="space-y-0.5">
                  {timelineContext.connectedSeeds.map((s) => (
                    <li key={s.id} className="flex items-center gap-1.5 text-xs text-gray-700">
                      <span
                        className="inline-block w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: s.color }}
                      />
                      <span className="truncate">{s.title}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {timelineContext.direction === 'seed' && timelineContext.forwardCitationsFetchedAt && (
              <p className="text-xs text-gray-400 mt-1">
                Forward citations last fetched: {new Date(timelineContext.forwardCitationsFetchedAt).toLocaleDateString()}
              </p>
            )}
          </div>
        )}

        {/* Add to topic list */}
        {topicLists && topicLists.length > 0 && onAddToList && (
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Add to Topic List</h4>
            <div className="flex flex-wrap gap-2">
              {topicLists.map((tl) => (
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
          </div>
        )}

        {/* Mark uninteresting (neighbors only) */}
        {onMarkUninteresting && timelineContext && (timelineContext.direction === 'backward' || timelineContext.direction === 'forward') && (
          <div>
            <button
              onClick={() => { onMarkUninteresting(workId); onClose(); }}
              className="px-3 py-1.5 text-xs font-medium text-red-600 border border-red-300 rounded hover:bg-red-50"
            >
              Mark uninteresting
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
