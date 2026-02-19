import type { WorkOut } from '../types';

function formatAuthor(work: WorkOut): string | null {
  if (!work.first_author_name) return null;
  const parts = work.first_author_name.trim().split(/\s+/);
  const lastName = parts[parts.length - 1];
  return work.author_count > 1 ? `${lastName} et al.` : lastName;
}

function venueColorClass(tier: number | null): string {
  if (tier === 1) return 'text-green-600';
  if (tier === 3) return 'text-red-500';
  return 'text-gray-500';
}

export default function WorkCard({
  work,
  isSelected,
  onClick,
}: {
  work: WorkOut;
  isSelected: boolean;
  onClick: () => void;
}) {
  const author = formatAuthor(work);

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-gray-100 hover:bg-gray-50 transition-colors ${
        isSelected ? 'bg-blue-50 border-l-2 border-l-blue-600' : ''
      }`}
    >
      <div className="text-sm font-medium text-gray-900 line-clamp-2">{work.title}</div>
      <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
        {author && <span>{author}</span>}
        {work.venue_display_name && (
          <>
            {author && <span className="text-gray-300">|</span>}
            <span className={`truncate max-w-[200px] ${venueColorClass(work.venue_tier)}`}>
              {work.venue_display_name}
            </span>
          </>
        )}
        {work.publication_year && (
          <>
            {(author || work.venue_display_name) && <span className="text-gray-300">|</span>}
            <span>{work.publication_year}</span>
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
