export type CandidateFilter = 'all' | 'top-venues' | 'none';

interface TimelineControlsProps {
  threshold: number;
  onThresholdChange: (v: number) => void;
  decayStartYears: number;
  onDecayStartYearsChange: (v: number) => void;
  showBackward: boolean;
  onShowBackwardChange: (v: boolean) => void;
  showForward: boolean;
  onShowForwardChange: (v: boolean) => void;
  startYear: number | null;
  onStartYearChange: (v: number | null) => void;
  minYear: number | null;
  maxYear: number | null;
  totalNeighbors: number;
  filteredNeighbors: number;
  candidateFilter: CandidateFilter;
  onCandidateFilterChange: (v: CandidateFilter) => void;
  hops: number;
  onHopsChange: (v: number) => void;
}

export default function TimelineControls({
  threshold,
  onThresholdChange,
  decayStartYears,
  onDecayStartYearsChange,
  showBackward,
  onShowBackwardChange,
  showForward,
  onShowForwardChange,
  startYear,
  onStartYearChange,
  minYear,
  maxYear,
  totalNeighbors,
  filteredNeighbors,
  candidateFilter,
  onCandidateFilterChange,
  hops,
  onHopsChange,
}: TimelineControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-4 px-4 py-2 bg-gray-50 border-b border-gray-200 text-xs">
      {/* Threshold slider */}
      <label className="flex items-center gap-1.5">
        <span className="text-gray-500">Threshold</span>
        <input
          type="range"
          min={0}
          max={10}
          step={0.1}
          value={threshold}
          onChange={(e) => onThresholdChange(Number(e.target.value))}
          className="w-20"
        />
        <span className="text-gray-700 w-8 text-right">{threshold.toFixed(1)}</span>
      </label>

      {/* Decay start */}
      <label className="flex items-center gap-1.5">
        <span className="text-gray-500">Decay after</span>
        <input
          type="range"
          min={1}
          max={20}
          step={1}
          value={decayStartYears}
          onChange={(e) => onDecayStartYearsChange(Number(e.target.value))}
          className="w-16"
        />
        <span className="text-gray-700">{decayStartYears}y</span>
      </label>

      {/* Direction checkboxes */}
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          checked={showBackward}
          onChange={(e) => onShowBackwardChange(e.target.checked)}
        />
        <span className="text-gray-600">References</span>
      </label>
      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          checked={showForward}
          onChange={(e) => onShowForwardChange(e.target.checked)}
        />
        <span className="text-gray-600">Cited by</span>
      </label>

      {/* Candidate filter */}
      <span className="text-gray-300">|</span>
      <label className="flex items-center gap-1.5">
        <span className="text-gray-500">Candidates</span>
        <select
          value={candidateFilter}
          onChange={(e) => onCandidateFilterChange(e.target.value as CandidateFilter)}
          className="border border-gray-300 rounded px-1.5 py-0.5 text-xs text-gray-700 bg-white"
        >
          <option value="all">All</option>
          <option value="top-venues">Top venues</option>
          <option value="none">None</option>
        </select>
      </label>

      {/* Hops */}
      <label className="flex items-center gap-1.5">
        <span className="text-gray-500">Hops</span>
        <div className="inline-flex rounded border border-gray-300 overflow-hidden">
          {[1, 2, 3].map((v) => (
            <button
              key={v}
              onClick={() => onHopsChange(v)}
              className={`px-2 py-0.5 text-xs ${
                hops === v
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }${v > 1 ? ' border-l border-gray-300' : ''}`}
            >
              {v}
            </button>
          ))}
        </div>
      </label>

      {/* Start year */}
      {minYear != null && maxYear != null && (
        <label className="flex items-center gap-1.5">
          <span className="text-gray-500">From</span>
          <input
            type="range"
            min={minYear}
            max={maxYear}
            step={1}
            value={startYear ?? minYear}
            onChange={(e) => {
              const v = Number(e.target.value);
              onStartYearChange(v <= minYear ? null : v);
            }}
            className="w-24"
          />
          <span className="text-gray-700 w-10">{startYear ?? minYear}</span>
        </label>
      )}

      {/* Stats */}
      <span className="text-gray-400 ml-auto">
        Showing {filteredNeighbors} of {totalNeighbors} candidates
      </span>
    </div>
  );
}
