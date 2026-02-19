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
