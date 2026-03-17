import { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import * as d3 from 'd3';
import type { TimelineSeedWork, TimelineNeighborWork, TopicListOut, SeedCitation } from '../types';
import { computeCitationCount } from '../lib/timelineFilter';

interface CitationTimelineProps {
  seeds: TimelineSeedWork[];
  neighbors: TimelineNeighborWork[];  // pre-filtered by parent
  topicLists: TopicListOut[];
  seedCitations: SeedCitation[];
  selectedWorkId: number | null;
  onSelectWork: (workId: number | null) => void;
  citationsSinceYears: number | null;
  startYear: number | null;
  showBackward: boolean;
  showForward: boolean;
  tier1VenueIds: Set<number>;
  hops: number;
  activeTopicListIds: Set<number>;
  onToggleTopicList: (id: number) => void;
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
  hasCitationData: boolean;
}

const MARGIN = { top: 20, right: 30, bottom: 36, left: 50 };
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
  citationsSinceYears,
  startYear,
  showBackward,
  showForward,
  tier1VenueIds,
  hops,
  activeTopicListIds,
  onToggleTopicList,
}: CitationTimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgWrapperRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 400 });

  // Keep onSelectWork in a ref to avoid stale closures in D3 event handlers
  const onSelectWorkRef = useRef(onSelectWork);
  useEffect(() => { onSelectWorkRef.current = onSelectWork; });

  // For click-to-cycle through overlapping dots
  const cycleStateRef = useRef<{ ids: number[]; index: number }>({ ids: [], index: -1 });

  // Refs shared between the two render effects
  const dotPositionsRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const dotsMapRef = useRef<Map<number, DotDatum>>(new Map());
  // Stable SVG layer refs set by renderData, read by renderSelection
  const khopLinesGRef = useRef<SVGGElement | null>(null);
  const selectionOverlayGRef = useRef<SVGGElement | null>(null);
  // Track previous selectedWorkId to fire ripple only when selection changes
  const prevSelectedWorkIdRef = useRef<number | null>(null);
  // Ref to latest renderSelection — lets renderData call it after a full SVG rebuild
  // without creating a circular useCallback dependency.
  const renderSelectionRef = useRef<() => void>(() => {});

  // Memoize dots: combines color lookup, seed connectivity, and dot building
  const dots = useMemo(() => {
    const tlColorMap = new Map(topicLists.map((tl) => [tl.id, tl.color]));

    const seedConnectivity = new Map<number, number>();
    for (const s of seeds) {
      let connections = 0;
      for (const sc of seedCitations) {
        if (showBackward && sc.cited_seed_id === s.id) connections++;
        if (showForward && sc.citing_seed_id === s.id) connections++;
      }
      seedConnectivity.set(s.id, 1 + connections);
    }

    const result: DotDatum[] = [];
    for (const s of seeds) {
      if (s.publication_year == null) continue;
      if (startYear != null && s.publication_year < startYear) continue;
      const activeIds = s.topic_list_ids.filter((id) => activeTopicListIds.has(id));
      const colors = (activeIds.length > 0 ? activeIds : s.topic_list_ids)
        .map((id) => tlColorMap.get(id) ?? '#6b7280');
      const citCount = computeCitationCount(s.citation_count, s.citations_by_year, citationsSinceYears);
      result.push({
        id: s.id, title: s.title, year: s.publication_year, type: 'seed',
        colors, topicListIds: s.topic_list_ids, connectedSeedIds: [],
        venueId: s.venue_id, citationCount: citCount,
        score: Math.log1p(citCount), connectivity: seedConnectivity.get(s.id) ?? 1,
        hasCitationData: true,
      });
    }
    for (const n of neighbors) {
      if (n.publication_year == null) continue;
      if (startYear != null && n.publication_year < startYear) continue;
      const citCount = computeCitationCount(n.citation_count, n.citations_by_year, citationsSinceYears);
      result.push({
        id: n.id, title: n.title, year: n.publication_year, type: n.direction,
        colors: ['#9ca3af'], topicListIds: [], connectedSeedIds: n.connected_seed_ids,
        venueId: n.venue_id, citationCount: citCount,
        score: Math.log1p(citCount), connectivity: 1,
        hasCitationData: n.has_citation_data ?? true,
      });
    }
    return result;
  }, [seeds, neighbors, topicLists, seedCitations, citationsSinceYears, startYear, showBackward, showForward, activeTopicListIds]);

  // Build adjacency map over rendered dots (recomputed when dot set changes, not on click)
  const adjacencyMap = useMemo(() => {
    const dotIdSet = new Set(dots.map((d) => d.id));
    const adj = new Map<number, Set<number>>();
    const addEdge = (a: number, b: number) => {
      if (!dotIdSet.has(a) || !dotIdSet.has(b)) return;
      if (!adj.has(a)) adj.set(a, new Set());
      if (!adj.has(b)) adj.set(b, new Set());
      adj.get(a)!.add(b);
      adj.get(b)!.add(a);
    };
    for (const sc of seedCitations) {
      addEdge(sc.citing_seed_id, sc.cited_seed_id);
    }
    for (const d of dots) {
      if (d.type !== 'seed') {
        for (const sid of d.connectedSeedIds) {
          addEdge(d.id, sid);
        }
      }
    }
    return adj;
  }, [dots, seedCitations]);

  // K-hop BFS from selected node
  const kHopResult = useMemo(() => {
    const empty = {
      nodeHops: new Map<number, number>(),
      edges: [] as { from: number; to: number; hop: number }[],
      intermediateNodes: new Set<number>(),
    };
    if (selectedWorkId == null || !adjacencyMap.has(selectedWorkId)) return empty;

    const nodeHops = new Map<number, number>();
    const edges: { from: number; to: number; hop: number }[] = [];
    const visitedEdges = new Set<string>();
    const edgeKey = (a: number, b: number) => (a < b ? `${a}-${b}` : `${b}-${a}`);

    nodeHops.set(selectedWorkId, 0);
    let frontier = [selectedWorkId];
    for (let hop = 1; hop <= hops; hop++) {
      const nextFrontier: number[] = [];
      for (const nodeId of frontier) {
        const nbrs = adjacencyMap.get(nodeId);
        if (!nbrs) continue;
        for (const nbr of nbrs) {
          const key = edgeKey(nodeId, nbr);
          if (!visitedEdges.has(key)) {
            visitedEdges.add(key);
            edges.push({ from: nodeId, to: nbr, hop });
          }
          if (!nodeHops.has(nbr)) {
            nodeHops.set(nbr, hop);
            nextFrontier.push(nbr);
          }
        }
      }
      frontier = nextFrontier;
    }

    // Intermediate nodes: on a path to farther nodes (hop 1..k-1 with a neighbor at higher hop)
    const intermediateNodes = new Set<number>();
    for (const [nodeId, hop] of nodeHops) {
      if (hop === 0) continue;
      const nbrs = adjacencyMap.get(nodeId);
      if (!nbrs) continue;
      for (const nbr of nbrs) {
        const nbrHop = nodeHops.get(nbr);
        if (nbrHop != null && nbrHop > hop) {
          intermediateNodes.add(nodeId);
          break;
        }
      }
    }

    return { nodeHops, edges, intermediateNodes };
  }, [selectedWorkId, hops, adjacencyMap]);

  // Radius: sqrt scaling proportional to area
  const rScale = (connectivity: number) => BASE_RADIUS * Math.sqrt(connectivity);

  // Observe svg wrapper size (excludes the legend div above it)
  useEffect(() => {
    const el = svgWrapperRef.current;
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

  // ---------------------------------------------------------------------------
  // Effect 1 — DATA BINDING: rebuild SVG structure when data or dimensions change.
  // Does NOT include selection state (selectedWorkId / kHopResult / tier1VenueIds).
  // Clicking a dot does NOT trigger this effect.
  // ---------------------------------------------------------------------------
  const renderData = useCallback(() => {
    const svg = d3.select(svgRef.current);
    const tooltip = d3.select(tooltipRef.current);
    const { width, height } = dimensions;
    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;

    svg.selectAll('*').remove();
    khopLinesGRef.current = null;
    selectionOverlayGRef.current = null;

    // SVG root handles deselection for clicks anywhere in the chart (incl. axis margins)
    svg.on('click', () => onSelectWorkRef.current(null));
    // Always hide tooltip on re-render to prevent lingering after element is destroyed
    tooltip.classed('hidden', true);

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
    const yPadding = maxScore * 0.08;
    const yScale = d3.scaleLinear()
      .domain([0, maxScore + yPadding])
      .range([innerH, 0]);

    const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`);

    // Background rect — sets cursor and provides a hit target; deselection handled by SVG root
    g.append('rect')
      .attr('width', innerW).attr('height', innerH)
      .attr('fill', 'transparent')
      .attr('cursor', 'default');

    // X axis — integer year ticks only, thinned when zoomed out
    const visibleMinYear = Math.ceil(xScale.domain()[0]);
    const visibleMaxYear = Math.floor(xScale.domain()[1]);
    const yearSpan = visibleMaxYear - visibleMinYear + 1;
    const maxTicks = Math.max(2, Math.floor(innerW / 50));
    const step = Math.max(1, Math.ceil(yearSpan / maxTicks));
    const yearTicks: number[] = [];
    // Align to multiples of step for clean labels (e.g. every 5 or 10 years)
    const firstTick = Math.ceil(visibleMinYear / step) * step;
    for (let y = firstTick; y <= visibleMaxYear; y += step) yearTicks.push(y);
    g.append('g')
      .attr('transform', `translate(0,${innerH})`)
      .call(d3.axisBottom(xScale).tickValues(yearTicks).tickFormat(d3.format('d')))
      .selectAll('text')
      .attr('class', 'fill-gray-500 text-xs');

    // Y axis: log1p-scaled citation count with tick labels
    // Generate tick values at powers of 10: 0, 1, 10, 100, 1000, ...
    const rawMaxScore = d3.max(dots.map((d) => d.citationCount ?? 0)) ?? 0;
    const yTickValues = [0];
    let tickVal = 1;
    while (tickVal <= rawMaxScore * 1.2) {
      yTickValues.push(tickVal);
      tickVal *= 10;
    }
    // Add one more tick beyond the data range for headroom
    if (yTickValues[yTickValues.length - 1] < rawMaxScore) {
      yTickValues.push(tickVal);
    }

    const yAxis = d3.axisLeft(yScale)
      .tickValues(yTickValues.map((v) => Math.log1p(v)))
      .tickFormat((_d, i) => String(yTickValues[i]));

    g.append('g')
      .call(yAxis)
      .selectAll('text')
      .attr('class', 'fill-gray-500 text-xs');

    // Y axis label
    g.append('text')
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerH / 2)
      .attr('y', -MARGIN.left + 12)
      .attr('text-anchor', 'middle')
      .attr('class', 'fill-gray-400 text-xs')
      .text('Citations');

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

    // Persist dot positions and map for use by renderSelection
    dotPositionsRef.current = dotPositions;
    dotsMapRef.current = new Map(dots.map((d) => [d.id, d]));

    // --- Click-to-cycle handler for overlapping dots ---
    const HIT_RADIUS = 8; // px radius to find overlapping dots
    const handleDotClick = (event: MouseEvent, clickedId: number) => {
      // Stop propagation so the SVG root's deselect handler doesn't fire
      event.stopPropagation();

      const clickedPos = dotPositions.get(clickedId);
      if (!clickedPos) { onSelectWorkRef.current(clickedId); return; }

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
        onSelectWorkRef.current(clickedId);
        return;
      }

      const prev = cycleStateRef.current;
      // Check if we're clicking the same cluster as before
      const sameCluster = prev.ids.length === nearby.length &&
        prev.ids.every((id) => nearby.includes(id));

      if (sameCluster) {
        const nextIndex = (prev.index + 1) % nearby.length;
        cycleStateRef.current = { ids: nearby, index: nextIndex };
        onSelectWorkRef.current(nearby[nextIndex]);
      } else {
        cycleStateRef.current = { ids: nearby, index: 0 };
        onSelectWorkRef.current(nearby[0]);
      }
    };

    // --- Static seed-to-seed lines ---
    const staticLinesG = g.append('g').attr('class', 'static-lines');
    for (const sc of seedCitations) {
      const from = dotPositions.get(sc.citing_seed_id);
      const to = dotPositions.get(sc.cited_seed_id);
      if (from && to) {
        staticLinesG.append('line')
          .attr('x1', from.x).attr('y1', from.y)
          .attr('x2', to.x).attr('y2', to.y)
          .attr('stroke', '#d1d5db')
          .attr('stroke-width', 0.5)
          .attr('stroke-dasharray', '2,2')
          .attr('opacity', 0.3);
      }
    }

    // Stable layer for k-hop edges (populated by renderSelection)
    khopLinesGRef.current = g.append('g').attr('class', 'khop-lines').node();

    // --- Draw dots ---
    const dotGroup = g.append('g').attr('class', 'dots');

    for (const d of dots) {
      const pos = dotPositions.get(d.id);
      if (!pos) continue;

      const r = rScale(d.connectivity);

      if (d.type === 'seed') {
        if (d.colors.length > 1) {
          // Multi-color seed: <g> wrapper with vertical stripe rects
          const seedG = dotGroup.append('g')
            .attr('class', 'dot-marker dot-mc-seed')
            .attr('data-work-id', String(d.id))
            .attr('transform', `translate(${pos.x},${pos.y})`);

          const stripeWidth = (2 * r) / d.colors.length;
          for (let i = 0; i < d.colors.length; i++) {
            seedG.append('rect')
              .attr('class', 'dot-stripe')
              .attr('x', -r + i * stripeWidth).attr('y', -r)
              .attr('width', stripeWidth).attr('height', 2 * r)
              .attr('fill', d.colors[i])
              .attr('opacity', 1)
              .attr('cursor', 'pointer')
              .on('click', (event: MouseEvent) => handleDotClick(event, d.id))
              .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
              .on('mouseleave', () => hideTooltip(tooltip));
          }

          // Pre-created border outline for tier1/selection stroke (hidden by default)
          seedG.append('rect')
            .attr('class', 'dot-border-outline')
            .attr('x', -r).attr('y', -r)
            .attr('width', 2 * r).attr('height', 2 * r)
            .attr('fill', 'none')
            .attr('stroke', 'none')
            .attr('stroke-width', 0)
            .attr('pointer-events', 'none');

          // Pre-created outer selection ring (shown only when selected)
          seedG.append('rect')
            .attr('class', 'dot-selected-ring')
            .attr('x', -(r + 3)).attr('y', -(r + 3))
            .attr('width', (r + 3) * 2).attr('height', (r + 3) * 2)
            .attr('fill', 'none')
            .attr('stroke', 'none')
            .attr('stroke-width', 2)
            .attr('pointer-events', 'none');
        } else {
          // Single-color seed: filled square
          const color = d.colors[0] ?? '#6b7280';
          dotGroup.append('rect')
            .attr('class', 'dot-marker')
            .attr('data-work-id', String(d.id))
            .attr('x', pos.x - r).attr('y', pos.y - r)
            .attr('width', r * 2).attr('height', r * 2)
            .attr('fill', color)
            .attr('opacity', 1)
            .attr('stroke', 'none')
            .attr('stroke-width', 0)
            .attr('cursor', 'pointer')
            .on('click', (event: MouseEvent) => handleDotClick(event, d.id))
            .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
            .on('mouseleave', () => hideTooltip(tooltip));
        }
      } else {
        const color = d.colors[0] ?? '#9ca3af';
        const isForward = d.type === 'forward';

        if (isForward) {
          const size = r * 2;
          dotGroup.append('rect')
            .attr('class', 'dot-marker')
            .attr('data-work-id', String(d.id))
            .attr('x', pos.x - r)
            .attr('y', pos.y - r)
            .attr('width', size).attr('height', size)
            .attr('transform', `rotate(45,${pos.x},${pos.y})`)
            .attr('fill', color)
            .attr('opacity', NEIGHBOR_OPACITY)
            .attr('stroke', 'none')
            .attr('stroke-width', 0)
            .attr('cursor', 'pointer')
            .on('click', (event: MouseEvent) => handleDotClick(event, d.id))
            .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
            .on('mouseleave', () => hideTooltip(tooltip));
        } else {
          // Backward neighbor: hollow (no OA data) or solid (has OA data)
          const isHollow = !d.hasCitationData;
          dotGroup.append('circle')
            .attr('class', 'dot-marker')
            .attr('data-work-id', String(d.id))
            .attr('cx', pos.x).attr('cy', pos.y)
            .attr('r', r)
            .attr('fill', isHollow ? 'none' : color)
            .attr('opacity', NEIGHBOR_OPACITY)
            .attr('stroke', isHollow ? '#9ca3af' : 'none')
            .attr('stroke-width', isHollow ? 1.5 : 0)
            .attr('cursor', 'pointer')
            .on('click', (event: MouseEvent) => handleDotClick(event, d.id))
            .on('mouseenter', (event: MouseEvent) => showTooltip(event, d, tooltip))
            .on('mouseleave', () => hideTooltip(tooltip));
        }
      }
    }

    // Stable layer for selection overlays (connection rings, ripple) populated by renderSelection
    selectionOverlayGRef.current = g.append('g').attr('class', 'selection-overlay').node();

    // Re-apply selection state immediately after a full SVG rebuild (e.g. triggered by resize
    // when WorkDetailPanel mounts). Without this, edges drawn by renderSelection are wiped
    // by svg.selectAll('*').remove() above and renderSelection's own effect doesn't re-fire
    // because its deps (selectedWorkId / kHopResult / tier1VenueIds) didn't change.
    renderSelectionRef.current();

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dimensions, dots, seedCitations]);

  // ---------------------------------------------------------------------------
  // Effect 2 — SELECTION STYLE: update visual attributes on existing DOM elements.
  // Runs on every click (selectedWorkId change) — does NOT create or remove main dot elements.
  // Only redraws k-hop lines and connection rings (cheap overlays).
  //
  // NOTE: If performance is still insufficient after auto-filter + this render refactor,
  // the next step would be canvas rendering for candidate markers (seeds stay as SVG).
  // See the HTML5 Canvas + D3 binding pattern for mixed SVG/canvas approaches.
  // ---------------------------------------------------------------------------
  const renderSelection = useCallback(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;

    const dotPositions = dotPositionsRef.current;
    const dotsMap = dotsMapRef.current;
    const khopLinesG = khopLinesGRef.current;
    const selectionOverlayG = selectionOverlayGRef.current;

    if (!khopLinesG || !selectionOverlayG) return; // renderData hasn't run yet

    // --- Update opacity and stroke on all existing dot markers ---
    d3.select(svgEl).selectAll<Element, unknown>('.dot-marker').each(function () {
      const el = d3.select(this);
      const workId = parseInt(el.attr('data-work-id'), 10);
      const dot = dotsMap.get(workId);
      if (!dot) return;

      const isSelected = workId === selectedWorkId;
      const hopDist = kHopResult.nodeHops.get(workId);
      const isConnected = hopDist != null;
      const dimmed = selectedWorkId != null && !isConnected;
      const isTier1 = dot.venueId != null && tier1VenueIds.has(dot.venueId);

      if ((this as SVGElement).tagName === 'g') {
        // Multi-color seed group: dim individual stripes, update pre-created border/ring
        el.selectAll<SVGRectElement, unknown>('.dot-stripe')
          .attr('opacity', dimmed ? 0.2 : 1);

        const stroke = isSelected ? '#6366f1' : isTier1 ? TIER1_STROKE : 'none';
        const strokeW = isSelected ? 2 : isTier1 ? TIER1_STROKE_WIDTH : 0;
        el.select('.dot-border-outline')
          .attr('stroke', stroke)
          .attr('stroke-width', strokeW);

        el.select('.dot-selected-ring')
          .attr('stroke', isSelected ? '#6366f1' : 'none');
      } else {
        // Single-color seed rect, backward circle, or forward rect
        const isSeed = dot.type === 'seed';
        const isHollow = dot.type === 'backward' && !dot.hasCitationData;
        const baseOpacity = isSeed ? 1 : NEIGHBOR_OPACITY;
        const dimmOpacity = isSeed ? 0.2 : 0.1;
        el.attr('opacity', dimmed ? dimmOpacity : baseOpacity);

        const stroke = isSelected ? '#6366f1'
          : isTier1 ? TIER1_STROKE
          : isHollow ? '#9ca3af' : 'none';
        const strokeW = isSelected ? 2
          : isTier1 ? TIER1_STROKE_WIDTH
          : isHollow ? 1.5 : 0;
        el.attr('stroke', stroke).attr('stroke-width', strokeW);
      }
    });

    // --- Redraw k-hop edges ---
    const khopSel = d3.select(khopLinesG);
    khopSel.selectAll('*').remove();
    for (const edge of kHopResult.edges) {
      const from = dotPositions.get(edge.from);
      const to = dotPositions.get(edge.to);
      if (from && to) {
        const isDirect = edge.hop === 1;
        khopSel.append('line')
          .attr('x1', from.x).attr('y1', from.y)
          .attr('x2', to.x).attr('y2', to.y)
          .attr('stroke', '#6366f1')
          .attr('stroke-width', isDirect ? 1.5 : 1)
          .attr('stroke-dasharray', isDirect ? 'none' : '4,3')
          .attr('opacity', isDirect ? 0.7 : 0.35);
      }
    }

    // --- Redraw connection rings in selection-overlay ---
    const overlaySel = d3.select(selectionOverlayG);
    overlaySel.selectAll('*').remove();

    if (selectedWorkId != null) {
      for (const [workId, hopDist] of kHopResult.nodeHops) {
        if (hopDist === 0) continue; // skip the selected dot itself (gets its own stroke via above)
        const dot = dotsMap.get(workId);
        const pos = dotPositions.get(workId);
        if (!dot || !pos) continue;

        const isIntermediate = kHopResult.intermediateNodes.has(workId);
        const ringStroke = isIntermediate ? '#d97706' : '#6366f1';
        const ringOpacity = hopDist === 1 ? 0.5 : 0.3;
        const r = rScale(dot.connectivity);

        if (dot.type === 'seed') {
          overlaySel.append('rect')
            .attr('x', pos.x - (r + 3)).attr('y', pos.y - (r + 3))
            .attr('width', (r + 3) * 2).attr('height', (r + 3) * 2)
            .attr('fill', 'none')
            .attr('stroke', ringStroke)
            .attr('stroke-width', isIntermediate ? 1.5 : 1)
            .attr('opacity', ringOpacity)
            .attr('pointer-events', 'none');
        } else {
          overlaySel.append('circle')
            .attr('cx', pos.x).attr('cy', pos.y)
            .attr('r', r + 3)
            .attr('fill', 'none')
            .attr('stroke', ringStroke)
            .attr('stroke-width', isIntermediate ? 1.5 : 1)
            .attr('opacity', ringOpacity)
            .attr('pointer-events', 'none');
        }
      }
    }

    // --- Ripple on newly selected dot ---
    const selectionChanged = selectedWorkId !== prevSelectedWorkIdRef.current;
    prevSelectedWorkIdRef.current = selectedWorkId;

    if (selectedWorkId != null && selectionChanged) {
      const selPos = dotPositions.get(selectedWorkId);
      const selDot = dotsMap.get(selectedWorkId);
      if (selPos && selDot) {
        const r = rScale(selDot.connectivity);
        overlaySel.append('circle')
          .attr('cx', selPos.x)
          .attr('cy', selPos.y)
          .attr('r', r + 2)
          .attr('fill', 'none')
          .attr('stroke', '#6366f1')
          .attr('stroke-width', 2)
          .attr('opacity', 0.7)
          .attr('pointer-events', 'none')
          .transition()
          .duration(650)
          .ease(d3.easeCubicOut)
          .attr('r', r + 18)
          .attr('opacity', 0)
          .remove();
      }
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedWorkId, kHopResult, tier1VenueIds]);

  // Keep renderSelectionRef current. Defined before renderData's effect so React runs it
  // first within the same commit — renderData() can then call renderSelectionRef.current()
  // with the latest closure.
  useEffect(() => { renderSelectionRef.current = renderSelection; });

  useEffect(() => {
    renderData();
  }, [renderData]);

  useEffect(() => {
    renderSelection();
  }, [renderSelection]);

  return (
    <div ref={containerRef} className="w-full h-full min-h-[300px] flex flex-col">
      {/* HTML legend — always rendered from the full topic list array, never filtered */}
      <div
        className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500 pb-1"
        style={{ paddingLeft: MARGIN.left }}
      >
        {topicLists.map((tl) => (
          <button
            key={tl.id}
            onClick={() => onToggleTopicList(tl.id)}
            className="flex items-center gap-1.5 cursor-pointer hover:text-gray-700 transition-opacity"
            style={{ opacity: activeTopicListIds.has(tl.id) ? 1 : 0.4 }}
          >
            <span
              className="inline-block shrink-0"
              style={{ width: 8, height: 8, background: tl.color }}
            />
            {tl.name}
          </button>
        ))}
        {/* Static (non-togglable) legend items */}
        <span className="flex items-center gap-1.5">
          <svg width="10" height="10" style={{ flexShrink: 0 }}>
            <circle cx="5" cy="5" r="4" fill="#9ca3af" opacity={NEIGHBOR_OPACITY} />
          </svg>
          Candidate
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="10" height="10" style={{ flexShrink: 0 }}>
            <circle cx="5" cy="5" r="4" fill="none" stroke={TIER1_STROKE} strokeWidth={TIER1_STROKE_WIDTH} />
          </svg>
          Top venue
        </span>
      </div>

      {/* SVG wrapper — ResizeObserver targets this so the legend height is excluded */}
      <div ref={svgWrapperRef} className="relative flex-1 min-h-0 overflow-hidden">
        <svg ref={svgRef} width={dimensions.width} height={dimensions.height} style={{ display: 'block' }} />
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
    </div>
  );
}

function showTooltip(
  event: MouseEvent,
  d: DotDatum,
  tooltip: d3.Selection<HTMLDivElement | null, unknown, null, undefined>,
) {
  const label = d.type === 'seed' ? 'Seed' : d.type === 'backward' ? 'Reference' : 'Cited by';

  const tooltipEl = tooltip.node();
  const containerRect = tooltipEl?.parentElement?.getBoundingClientRect();
  const containerLeft = containerRect?.left ?? 0;
  const containerTop = containerRect?.top ?? 0;

  // Position relative to the container div (which is position:relative)
  const xInContainer = event.clientX - containerLeft;
  const yInContainer = event.clientY - containerTop;

  // Flip to the left of the cursor if the tooltip would overflow the viewport right edge
  const TOOLTIP_WIDTH = 280;
  const OFFSET = 12;
  const fitsRight = event.clientX + OFFSET + TOOLTIP_WIDTH <= window.innerWidth;
  const left = fitsRight ? xInContainer + OFFSET : xInContainer - TOOLTIP_WIDTH - OFFSET;

  tooltip
    .style('left', `${left}px`)
    .style('top', `${yInContainer - 10}px`)
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
