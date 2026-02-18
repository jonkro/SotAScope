import type { WorkOut } from '../types';

export default function WorkCard({
  work,
  isSelected,
  onClick,
}: {
  work: WorkOut;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-gray-100 hover:bg-gray-50 transition-colors ${
        isSelected ? 'bg-blue-50 border-l-2 border-l-blue-600' : ''
      }`}
    >
      <div className="text-sm font-medium text-gray-900 line-clamp-2">{work.title}</div>
      <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
        {work.publication_year && <span>{work.publication_year}</span>}
        {work.doi && (
          <>
            <span className="text-gray-300">|</span>
            <span className="truncate max-w-[200px]">{work.doi}</span>
          </>
        )}
        {work.citation_count != null && (
          <>
            <span className="text-gray-300">|</span>
            <span>{work.citation_count} cit.</span>
          </>
        )}
      </div>
    </button>
  );
}
