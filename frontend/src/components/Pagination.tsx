export default function Pagination({
  offset,
  limit,
  count,
  onChange,
}: {
  offset: number;
  limit: number;
  count: number;
  onChange: (offset: number) => void;
}) {
  const hasPrev = offset > 0;
  const hasNext = count === limit; // if we got a full page, there might be more

  if (!hasPrev && !hasNext) return null;

  return (
    <div className="flex items-center gap-3 py-3">
      <button
        disabled={!hasPrev}
        onClick={() => onChange(Math.max(0, offset - limit))}
        className="px-3 py-1 text-sm border border-gray-300 rounded disabled:opacity-40 hover:bg-gray-50"
      >
        Previous
      </button>
      <span className="text-sm text-gray-500">
        {offset + 1}&ndash;{offset + count}
      </span>
      <button
        disabled={!hasNext}
        onClick={() => onChange(offset + limit)}
        className="px-3 py-1 text-sm border border-gray-300 rounded disabled:opacity-40 hover:bg-gray-50"
      >
        Next
      </button>
    </div>
  );
}
