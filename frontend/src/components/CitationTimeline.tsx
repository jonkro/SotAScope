import { useRef, useEffect, useState, useCallback } from 'react';
import * as d3 from 'd3';
import type { TimelineSeedWork, TimelineNeighborWork, TopicListOut, SeedCitation } from '../types';
import { computeImportanceScore, type FilterParams } from '../lib/timelineFilter';

interface CitationTimelineProps {
  seeds: TimelineSeedWork[];
  neighbors: TimelineNeighborWork[];  // pre-filtered by parent
  topicLists: TopicListOut[];
  seedCitations: SeedCitation[];
  selectedWorkId: number | null;
  onSelectWork: (workId: number | null) => void;
  decayStartYears: number;
  startYear: number | null;
  showBackward: boolean;
  showForward: boolean;
  tier1VenueIds: Set<number>;
}

interface DotDatum {
  id: number;
  title: string;
  year: number;
  type: 'seed' | 'backward' | 'forward';
  colors: string[];
  topicListIds: number[];
  connectedSeedIds: number[];
  venueId: number | null;
  citationCount: number | null;
  score: number;
  connectivity: number; // inter-seed citation connectivity (1 = base)
}

const MARGIN = { top: 20, right: 30, bottom: 56, left: 30 };
const BASE_RADIUS = 3;
const NEIGHBOR_OPACITY = 0.45;
const TIER1_STROKE = '#1f2937';
const TIER1_STROKE_WIDTH = 1.5;

export default function CitationTimeline({
  seeds,
  neighbors,
  topicLists,
  seedCitations,
  selectedWorkId,
  onSelectWork,
  decayStartYears,
  startYear,
  showBackward,
  showForward,
  tier1VenueIds,
}: CitationTimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 400 });

  // For click-to-cycle through overlapping dots
  const cycleStateRef = useRef<{ ids: number[]; index: number }>({ ids: [], index: -1 });

  const currentYear = new Date().getFullYear();
  const filterParams: FilterParams = {
    threshold: 0, decayStartYears, showBackward: true, showForward: true, currentYear,
  };

  // Build color lookup
  const tlColorMap = new Map(topicLists.map((tl) => [tl.id, tl.color]));

  // --- Compute inter-seed connectivity ---
  // For each seed, count how many other seeds it connects to via seedCitations,
  // controlled by the showBackward/showForward checkboxes.
  const seedConnectivity = new Map<number, number>();
  for (const s of seeds) {
    let connections = 0;
    for (const sc of seedCitations) {
      // "References" on → count seeds that cite this seed (this seed appears in their references)
      if (showBackward && sc.cited_seed_id === s.id) connections++;
      // "Cited by" on → count seeds that this seed cites (this seed references them)
      if (showForward && sc.citing_seed_id === s.id) connections++;
    }
    seedConnectivity.set(s.id, 1 + connections);
  }

  // Convert to DotDatum array, applying startYear filter
  const dots: DotDatum[] = [];

  for (const s of seeds) {
    if (s.publication_year == null) continue;
    if (startYear != null && s.publication_year < startYear) continue;
    const colors = s.topic_list_ids.map((id) => tlColorMap.get(id) ?? '#6b7280');
    const rawScore = computeImportanceScore(s.citation_count ?? 0, s.publication_year, filterParams);
    dots.push({
      id: s.id, title: s.title, year: s.publication_year, type: 'seed',
      colors, topicListIds: s.topic_list_ids, connectedSeedIds: [],
      venueId: s.venue_id, citationCount: s.citation_count,
      score: Math.log(1 + rawScore), connectivity: seedConnectivity.get(s.id) ?? 1,
    });
  }

  for (const n of neighbors) {
    if (n.publication_year == null) continue;
    if (startYear != null && n.publication_year < startYear) continue;
    const rawScore = computeImportanceScore(n.citation_count ?? 0, n.publication_year, filterParams);
    dots.push({
      id: n.id, title: n.title, year: n.publication_year, type: n.direction,
      colors: ['#9ca3af'], topicListIds: [], connectedSeedIds: n.connected_seed_ids,
      venueId: n.venue_id, citationCount: n.citation_count,
      score: Math.log(1 + rawScore), connectivity: 1,
    });
  }

  // Radius: sqrt scaling proportional to area
  const rScale = (connectivity: number) => BASE_RADIUS * Math.sqrt(connectivity);

  // Observe container size
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) {
        setDimensions({ width, height });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Render D3
  const render = useCallback(() => {
    const svg = d3.select(svgRef.current);
    const tooltip = d3.select(tooltipRef.current);
    const { width, height } = dimensions;
    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;

    svg.selectAll('*').remove();

    if (dots.length === 0 || innerW <= 0 || innerH <= 0) return;

    // --- X scale: publication year ---
    const years = dots.map((d) => d.year);
    const minYear = d3.min(years)! - 1;
    const maxYear = d3.max(years)! + 1;
    const xScale = d3.scaleLinear().domain([minYear, maxYear]).range([0, innerW]);

    // --- Y scale: importance score with log(1+x) transform (higher = top) ---
    const scores = dots.map((d) => d.score);
    const minScore = d3.min(scores) ?? 0;
    let maxScore = d3.max(scores) ?? 1;
    if (maxScore <= minScore) maxScore = minScore + 1;
    const yPadding = (maxScore - minScore) * 0.08;
    const yScale = d3.scaleLinear()
      .domain([Math.max(0, minScore - yPadding), maxScore + yPadding])
      .range([innerH, 0]);

    const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Background rect for click-to-deselect
    g.append('rect')
      .attr('width', innerW).attr('height', innerH)
      .attr('fill', 'transparent')
      .attr('cursor', 'default')
      .on('click', () => onSelectWork(null));

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${innerH})`)
      .call(d3.axisBottom(xScale).tickFormat(d3.format('d')))
      .selectAll('text')
      .attr('class', 'fill-gray-500 text-xs');

    // Y axis: thin line with upward arrow, no tick labels
    g.append('line')
      .attr('x1', 0).attr('y1', innerH)
      .attr('x2', 0).attr('y2', 4)
      .attr('stroke', '#9ca3af')
      .attr('stroke-width', 1);

    g.append('path')
      .attr('d', 'M-4,10 L0,0 L4,10')
      .attr('fill', 'none')
      .attr('stroke', '#9ca3af')
      .attr('stroke-width', 1.5);

    g.append('text')
      .attr('x', 8)
      .attr('y', 6)
      .attr('text-anchor', 'start')
      .attr('dominant-baseline', 'middle')
      .attr('class', 'fill-gray-400 text-xs')
      .text('more important');

    // --- Jitter within year groups ---
    const yearGroups = new Map<number, DotDatum[]>();
    for (const d of dots) {
      const arr = yearGroups.get(d.year) ?? [];
      arr.push(d);
      yearGroups.set(d.year, arr);
    }

    const dotPositions = new Map<number, { x: number; y: number }>();
    for (const [, items] of yearGroups) {
      const sorted = [...items].sort((a, b) => idHash(a.id) - idHash(b.id));
      sorted.forEach((d, i) => {
        // Jitter in year-space: ±0.4 years so papers never cross year boundaries
        const jitterYear = sorted.length > 1
          ? (i / (sorted.length - 1) - 0.5) * 0.64
          : 0;
        dotPositions.set(d.id, {
          x: xScale(d.year + jitterYear),
          y: Math.max(0, yScale(d.score)),
        });
      });
    }

    // --- Click-to-cycle handler for overlapping dots ---
    const HIT_RADIUS = 8; // px radius to find overlapping dots
    const handleDotClick = (clickedId: number) => {
      const clickedPos = dotPositions.get(clickedId);
      if (!clickedPos) { onSelectWork(clickedId); return; }

      // Find all dots within HIT_RADIUS of the clicked dot's position
      const nearby: number[] = [];
      for (const d of dots) {
        const pos = dotPositions.get(d.id);
        if (!pos) continue;
        const dx = pos.x - clickedPos.x;
        const dy = pos.y - clickedPos.y;
        if (Math.sqrt(dx * dx + dy * dy) <= HIT_RADIUS) {
          nearby.push(d.id);
        }
      }

      if (nearby.length <= 1) {
        cycleStateRef.current = { ids: [], index: -1 };
        onSelectWork(clickedId);
        return;
      }

      const prev = cycleStateRef.current;
      // Check if we're clicking the same cluster as before
      const sameCluster = prev.ids.length === nearby.length &&
        prev.ids.every((id) => nearby.includes(id));

      if (sameCluster) {
        const nextIndex = (prev.index + 1) % nearby.length;
        cycleStateRef.current = { ids: nearby, index: nextIndex };
        onSelectWork(nearby[nextIndex]);
      } else {
        cycleStateRef.current = { ids: nearby, index: 0 };
        onSelectWork(nearby[0]);
      }
    };

    // --- Selected dot connections ---
    const selectedConnections = new Set<number>();
    if (selectedWorkId != null) {
      selectedConnections.add(selectedWorkId);
      const sel = dots.find((d) => d.id === selectedWorkId);
      if (sel) {
        if (sel.type === 'seed') {
          for (const n of neighbors) {
            if (n.connected_seed_ids.includes(selectedWorkId)) {
              selectedConnections.add(n.id);
            }
          }
          for (const sc of seedCitations) {
            if (sc.citing_seed_id === selectedWorkId) selectedConnections.add(sc.cited_seed_id);
            if (sc.cited_seed_id === selectedWorkId) selectedConnections.add(sc.citing_seed_id);
          }
        } else {
          for (const sid of sel.connectedSeedIds) {
            selectedConnections.add(sid);
          }
        }
      }
    }

    // --- Draw lines ---
    const lineGroup = g.append('g').attr('class', 'citation-lines');

    for (const sc of seedCitations) {
      const from = dotPositions.get(sc.citing_seed_id);
      const to = dotPositions.get(sc.cited_seed_id);
      if (from && to) {
        const isHighlighted = selectedWorkId != null &&
          selectedConnections.has(sc.citing_seed_id) && selectedConnections.has(sc.cited_seed_id);
        lineGroup.append('line')
          .attr('x1', from.x).attr('y1', from.y)
          .attr('x2', to.x).attr('y2', to.y)
          .attr('stroke', isHighlighted ? '#6366f1' : '#d1d5db')
          .attr('stroke-width', isHighlighted ? 1.5 : 0.5)
          .attr('stroke-dasharray', isHighlighted ? 'none' : '2,2')
          .attr('opacity', isHighlighted ? 0.8 : 0.3);
      }
    }

    if (selectedWorkId != null) {
      const selPos = dotPositions.get(selectedWorkId);
      if (selPos) {
        for (const cid of selectedConnections) {
          if (cid === selectedWorkId) continue;
          const pos = dotPositions.get(cid);
          if (pos) {
            lineGroup.append('line')
              .attr('x1', selPos.x).attr('y1', selPos.y)
              .attr('x2', pos.x).attr('y2', pos.y)
              .attr('stroke', '#6366f1')
              .attr('stroke-width', 1)
              .attr('opacity', 0.4);
          }
        }
      }
    }

    // --- Draw dots ---
    const dotGroup = g.append('g').attr('class', 'dots');

    for (const d of dots) {
      const pos = dotPositions.get(d.id);
      if (!pos) continue;

      const isSelected = d.id === selectedWorkId;
      const isConnected = selectedConnections.has(d.id);
      const dimmed = selectedWorkId != null && !isConnected;
      const r = rScale(d.connectivity);
      const isTier1 = d.venueId != null && tier1VenueIds.has(d.venueId);

      // Stroke priority: selection > tier-1 > none
      const markerStroke = isSelected ? '#6366f1' : isTier1 ? TIER1_STROKE : 'none';
      const markerStrokeW = isSelected ? 2 : isTier1 ? TIER1_STROKE_WIDTH : 0;

      if (d.type === 'seed') {
        if (d.colors.length > 1) {
          // Multi-color seed: square with vertical color stripes
          const seedG = dotGroup.append('g').attr('transform', `translate(${pos.x},${pos.y})`);
          const stripeWidth = (2 * r) / d.colors.length;
          for (let i = 0; i < d.colors.length; i++) {
            seedG.append('rect')
              .attr('x', -r + i * stripeWidth).attr('y', -r)
              .attr('width', stripeWidth).attr('height', 2 * r)
              .attr('fill', d.colors[i])
              .attr('opacity', dimmed ? 0.2 : 1)
              .attr('cursor', 'pointer')
              .on('click', () => handleDotClick(d.id))
              .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
              .on('mouseleave', () => hideTooltip(tooltip));
          }
          // Tier-1 or selection outline for multi-color seeds
          if (markerStroke !== 'none') {
            seedG.append('rect')
              .attr('x', -r).attr('y', -r)
              .attr('width', 2 * r).attr('height', 2 * r)
              .attr('fill', 'none')
              .attr('stroke', markerStroke)
              .attr('stroke-width', markerStrokeW);
          }
          if (isSelected) {
            seedG.append('rect')
              .attr('x', -(r + 3)).attr('y', -(r + 3))
              .attr('width', (r + 3) * 2).attr('height', (r + 3) * 2)
              .attr('fill', 'none')
              .attr('stroke', '#6366f1')
              .attr('stroke-width', 2);
          }
        } else {
          // Single-color seed: filled square
          const color = d.colors[0] ?? '#6b7280';
          dotGroup.append('rect')
            .attr('x', pos.x - r).attr('y', pos.y - r)
            .attr('width', r * 2).attr('height', r * 2)
            .attr('fill', color)
            .attr('opacity', dimmed ? 0.2 : 1)
            .attr('stroke', markerStroke)
            .attr('stroke-width', markerStrokeW)
            .attr('cursor', 'pointer')
            .on('click', () => handleDotClick(d.id))
            .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
            .on('mouseleave', () => hideTooltip(tooltip));
        }
        // Connected-seed highlight ring (square outline)
        if (isConnected && !isSelected && selectedWorkId != null) {
          dotGroup.append('rect')
            .attr('x', pos.x - (r + 3)).attr('y', pos.y - (r + 3))
            .attr('width', (r + 3) * 2).attr('height', (r + 3) * 2)
            .attr('fill', 'none')
            .attr('stroke', '#6366f1')
            .attr('stroke-width', 1)
            .attr('opacity', 0.5);
        }
      } else {
        const color = d.colors[0] ?? '#9ca3af';
        const isForward = d.type === 'forward';

        if (isForward) {
          const size = r * 2;
          dotGroup.append('rect')
            .attr('x', pos.x - r)
            .attr('y', pos.y - r)
            .attr('width', size).attr('height', size)
            .attr('transform', `rotate(45,${pos.x},${pos.y})`)
            .attr('fill', color)
            .attr('opacity', dimmed ? 0.1 : NEIGHBOR_OPACITY)
            .attr('stroke', markerStroke)
            .attr('stroke-width', markerStrokeW)
            .attr('cursor', 'pointer')
            .on('click', () => handleDotClick(d.id))
            .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
            .on('mouseleave', () => hideTooltip(tooltip));
        } else {
          dotGroup.append('circle')
            .attr('cx', pos.x).attr('cy', pos.y)
            .attr('r', r)
            .attr('fill', color)
            .attr('opacity', dimmed ? 0.1 : NEIGHBOR_OPACITY)
            .attr('stroke', markerStroke)
            .attr('stroke-width', markerStrokeW)
            .attr('cursor', 'pointer')
            .on('click', () => handleDotClick(d.id))
            .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
            .on('mouseleave', () => hideTooltip(tooltip));
        }

        if (isConnected && !isSelected && selectedWorkId != null) {
          dotGroup.append('circle')
            .attr('cx', pos.x).attr('cy', pos.y)
            .attr('r', r + 3)
            .attr('fill', 'none')
            .attr('stroke', '#6366f1')
            .attr('stroke-width', 1)
            .attr('opacity', 0.5);
        }
      }
    }

    // --- Legend (below x-axis, never overlaps data) ---
    const legendItems: { shape: 'square' | 'circle' | 'diamond' | 'top-venue'; color: string; label: string }[] = [];
    for (const tl of topicLists) {
      legendItems.push({ shape: 'square', color: tl.color, label: tl.name });
    }
    legendItems.push({ shape: 'circle', color: '#9ca3af', label: 'Candidate' });
    legendItems.push({ shape: 'top-venue', color: 'none', label: 'Top venue' });

    const legendG = g.append('g')
      .attr('transform', `translate(0,${innerH + 38})`);

    const lr = BASE_RADIUS;
    let lx = 0;
    for (const item of legendItems) {
      const itemG = legendG.append('g').attr('transform', `translate(${lx},0)`);

      if (item.shape === 'square') {
        itemG.append('rect')
          .attr('x', -lr).attr('y', -lr)
          .attr('width', lr * 2).attr('height', lr * 2)
          .attr('fill', item.color);
      } else if (item.shape === 'circle') {
        itemG.append('circle')
          .attr('r', lr)
          .attr('fill', item.color)
          .attr('opacity', NEIGHBOR_OPACITY);
      } else if (item.shape === 'diamond') {
        itemG.append('rect')
          .attr('x', -lr).attr('y', -lr)
          .attr('width', lr * 2).attr('height', lr * 2)
          .attr('transform', 'rotate(45)')
          .attr('fill', item.color)
          .attr('opacity', NEIGHBOR_OPACITY);
      } else {
        // top-venue: hollow circle with tier-1 stroke
        itemG.append('circle')
          .attr('r', lr)
          .attr('fill', 'none')
          .attr('stroke', TIER1_STROKE)
          .attr('stroke-width', TIER1_STROKE_WIDTH);
      }

      const textEl = itemG.append('text')
        .attr('x', lr + 5).attr('y', 0)
        .attr('dominant-baseline', 'central')
        .attr('class', 'fill-gray-500 text-xs')
        .text(item.label);

      const textWidth = (textEl.node() as SVGTextElement).getComputedTextLength?.() ?? item.label.length * 6;
      lx += lr + 5 + textWidth + 16;
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dimensions, dots.length, selectedWorkId, seeds, neighbors, seedCitations, topicLists, decayStartYears, startYear, showBackward, showForward, tier1VenueIds]);

  useEffect(() => {
    render();
  }, [render]);

  return (
    <div ref={containerRef} className="relative w-full h-full min-h-[300px]">
      <svg ref={svgRef} width={dimensions.width} height={dimensions.height} />
      <div
        ref={tooltipRef}
        className="absolute pointer-events-none bg-gray-900 text-white text-xs rounded px-2 py-1 max-w-[280px] hidden z-50"
      />
      {dots.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-gray-400">
          No papers to display. Add works to topic lists and fetch their citations.
        </div>
      )}
    </div>
  );
}

function showTooltip(
  event: MouseEvent,
  d: DotDatum,
  tooltip: d3.Selection<HTMLDivElement | null, unknown, null, undefined>,
) {
  const label = d.type === 'seed' ? 'Seed' : d.type === 'backward' ? 'Reference' : 'Cited by';
  tooltip
    .style('left', `${event.offsetX + 12}px`)
    .style('top', `${event.offsetY - 10}px`)
    .classed('hidden', false)
    .html(
      `<div style="font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:260px">${escapeHtml(d.title)}</div>` +
      `<div>${d.year} &middot; ${label}` +
      (d.citationCount != null ? ` &middot; ${d.citationCount} cit.` : '') +
      (d.connectivity > 1 ? ` &middot; ${d.connectivity} connections` : '') +
      `</div>`,
    );
}

function hideTooltip(
  tooltip: d3.Selection<HTMLDivElement | null, unknown, null, undefined>,
) {
  tooltip.classed('hidden', true);
}

/** Stable hash: maps an integer ID to [0, 1) using Knuth multiplicative hash. */
function idHash(id: number): number {
  return ((id * 2654435761) >>> 0) / 0x100000000;
}

function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
