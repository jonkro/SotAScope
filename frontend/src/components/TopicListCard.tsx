import { useState, useEffect } from 'react';
import type { TopicListOut, TopicListDetail } from '../types';
import { useTopicList, useRemoveWorkFromTopicList, useMergeTopicList } from '../hooks/useProjects';
import Badge from './Badge';

function MergeIntoDialog({
  sourceName,
  otherLists,
  onConfirm,
  onCancel,
  isPending,
}: {
  sourceName: string;
  otherLists: TopicListOut[];
  onConfirm: (targetId: number) => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const [targetId, setTargetId] = useState<number | ''>(
    otherLists.length > 0 ? otherLists[0].id : ''
  );

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={onCancel}>
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-sm p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-sm font-semibold text-gray-900 mb-3">Merge topic list</h2>
        <p className="text-xs text-gray-500 mb-3">
          All papers from &ldquo;{sourceName}&rdquo; will be added to the selected list. The
          original list will not be deleted.
        </p>
        <select
          value={targetId}
          onChange={(e) => setTargetId(parseInt(e.target.value, 10))}
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {otherLists.map((tl) => (
            <option key={tl.id} value={tl.id}>
              {tl.name}
            </option>
          ))}
        </select>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={isPending}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={() => targetId !== '' && onConfirm(targetId)}
            disabled={targetId === '' || isPending}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40"
          >
            {isPending ? 'Merging…' : 'Merge'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function TopicListCard({
  topicList,
  projectId,
  allTopicLists,
  onEdit,
  onDelete,
  onSelectWork,
  forceExpand,
}: {
  topicList: TopicListOut;
  projectId: number;
  allTopicLists: TopicListOut[];
  onEdit: () => void;
  onDelete: () => void;
  onSelectWork: (workId: number) => void;
  forceExpand?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showMergeDialog, setShowMergeDialog] = useState(false);
  const [mergeSuccess, setMergeSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (forceExpand) setExpanded(true);
  }, [forceExpand]);

  const { data: detail } = useTopicList(projectId, expanded ? topicList.id : null);
  const removeWork = useRemoveWorkFromTopicList();
  const mergeMutation = useMergeTopicList();

  const otherLists = allTopicLists.filter((tl) => tl.id !== topicList.id);
  const canMerge = otherLists.length > 0;

  function handleMergeConfirm(targetId: number) {
    mergeMutation.mutate(
      { projectId, targetId, sourceId: topicList.id },
      {
        onSuccess: (result) => {
          setShowMergeDialog(false);
          setMergeSuccess(
            `Added ${result.merged_count} paper${result.merged_count !== 1 ? 's' : ''}` +
              (result.skipped_duplicate_count > 0
                ? ` (${result.skipped_duplicate_count} already present)`
                : ''),
          );
          setTimeout(() => setMergeSuccess(null), 4000);
        },
      },
    );
  }

  return (
    <>
      <div className="border border-gray-200 rounded-lg">
        <div className="flex items-center gap-3 px-4 py-3">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-gray-400 hover:text-gray-600 text-sm w-5"
          >
            {expanded ? '\u25BC' : '\u25B6'}
          </button>
          <Badge label={topicList.name} color={topicList.color} />
          <span className="text-xs text-gray-400 ml-auto">
            {(detail as TopicListDetail | undefined)?.works?.length ?? '...'} works
          </span>
          {mergeSuccess && (
            <span className="text-xs text-green-600">{mergeSuccess}</span>
          )}
          <button onClick={onEdit} className="text-xs text-gray-500 hover:text-gray-700">
            edit
          </button>
          <span className="text-xs text-gray-300">·</span>
          {canMerge ? (
            <button
              onClick={() => setShowMergeDialog(true)}
              className="text-xs text-gray-500 hover:text-gray-700"
            >
              merge into
            </button>
          ) : (
            <span className="text-xs text-gray-300 cursor-default" title="No other topic lists to merge into">
              merge into
            </span>
          )}
          <span className="text-xs text-gray-300">·</span>
          <button onClick={onDelete} className="text-xs text-red-500 hover:text-red-700">
            delete
          </button>
        </div>

        {expanded && detail && (
          <div className="border-t border-gray-100 px-4 py-2">
            {!detail.works.length ? (
              <p className="text-xs text-gray-400 py-2">No works in this list yet.</p>
            ) : (
              <ul className="divide-y divide-gray-50">
                {detail.works.map((tw) => (
                  <li key={tw.id} className="flex items-center gap-2 py-2">
                    <button
                      onClick={() => onSelectWork(tw.work.id)}
                      className="flex-1 text-left text-sm text-gray-700 hover:text-blue-600 truncate"
                    >
                      {tw.work.title}
                    </button>
                    <span className="text-xs text-gray-400 shrink-0">{tw.work.publication_year}</span>
                    <button
                      onClick={() => removeWork.mutate({ projectId, topicListId: topicList.id, workId: tw.work.id })}
                      className="text-xs text-red-400 hover:text-red-600 shrink-0"
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {showMergeDialog && (
        <MergeIntoDialog
          sourceName={topicList.name}
          otherLists={otherLists}
          onConfirm={handleMergeConfirm}
          onCancel={() => setShowMergeDialog(false)}
          isPending={mergeMutation.isPending}
        />
      )}
    </>
  );
}
