import { useState } from 'react';
import type { TopicListOut, TopicListDetail } from '../types';
import { useTopicList, useRemoveWorkFromTopicList } from '../hooks/useProjects';
import Badge from './Badge';

export default function TopicListCard({
  topicList,
  projectId,
  onEdit,
  onDelete,
  onSelectWork,
}: {
  topicList: TopicListOut;
  projectId: number;
  onEdit: () => void;
  onDelete: () => void;
  onSelectWork: (workId: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data: detail } = useTopicList(projectId, expanded ? topicList.id : null);
  const removeWork = useRemoveWorkFromTopicList();

  return (
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
        <button onClick={onEdit} className="text-xs text-gray-500 hover:text-gray-700">Edit</button>
        <button onClick={onDelete} className="text-xs text-red-500 hover:text-red-700">Delete</button>
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
  );
}
