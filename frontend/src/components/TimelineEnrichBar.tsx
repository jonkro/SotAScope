import { useState, useEffect, useCallback } from 'react';
import type { TimelineSeedWork, BulkJobStatus } from '../types';
import {
  fetchBackwardCitationsEnrich,
  fetchForwardCitationsEnrich,
  fetchGrobidStatus,
  startBulkS2Fetch,
  startBulkGrobidExtract,
  fetchBulkJobStatus,
  cancelBulkJob,
} from '../api';
import { useQueryClient } from '@tanstack/react-query';

interface FetchError {
  workId: number;
  title: string;
  direction: 'references' | 'citing';
  reason: string;
}

function parseErrorReason(e: unknown): string {
  if (!(e instanceof Error)) return 'Unknown error';
  try {
    const parsed = JSON.parse(e.message);
    if (parsed.detail) return parsed.detail;
  } catch {
    // not JSON
  }
  return e.message;
}

function formatDuration(seconds: number): string {
  if (seconds >= 60) return `~${Math.round(seconds / 60)}min`;
  return `~${Math.round(seconds)}s`;
}

interface TimelineEnrichBarProps {
  seeds: TimelineSeedWork[];
  projectId: number;
  onSelectWork?: (workId: number) => void;
}

export default function TimelineEnrichBar({ seeds, projectId, onSelectWork }: TimelineEnrichBarProps) {
  const qc = useQueryClient();

  // Collapse/expand — persisted per project
  const lsKey = `litexplorer:project:${projectId}:enrichmentBarExpanded`;
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(lsKey) === 'true'; } catch { return false; }
  });

  const toggleExpanded = () => {
    setExpanded(prev => {
      const next = !prev;
      try { localStorage.setItem(lsKey, String(next)); } catch { /* ignore */ }
      return next;
    });
  };

  // Read GROBID status from cache — no new fetch
  const grobidStatus = qc.getQueryData<Awaited<ReturnType<typeof fetchGrobidStatus>>>(['grobid', 'status']);
  const grobidAvailable = grobidStatus?.available ?? false;

  // --- OA fetch state ---
  const [oaFetching, setOaFetching] = useState(false);
  const [oaProgress, setOaProgress] = useState<{ done: number; total: number } | null>(null);
  const [oaErrors, setOaErrors] = useState<FetchError[]>([]);

  // --- S2 bulk job state ---
  const [s2JobId, setS2JobId] = useState<string | null>(null);
  const [s2Job, setS2Job] = useState<BulkJobStatus | null>(null);
  const [s2RateLimitMsg, setS2RateLimitMsg] = useState<string | null>(null);

  // --- GROBID bulk job state ---
  const [grobidJobId, setGrobidJobId] = useState<string | null>(null);
  const [grobidJob, setGrobidJob] = useState<BulkJobStatus | null>(null);

  // Poll S2 bulk job
  useEffect(() => {
    if (!s2JobId) return;
    const interval = setInterval(async () => {
      try {
        const status = await fetchBulkJobStatus(s2JobId);
        setS2Job(status);
        if (status.status !== 'running') {
          clearInterval(interval);
          setS2JobId(null);
          if (status.status === 'rate_limited') {
            const done = status.rate_limited_at ?? status.done;
            setS2RateLimitMsg(
              `S2 rate limited after ${done}/${status.total} seeds. ` +
              `S2 typically requires ~1 hour to recover after a 429. You can retry later — ` +
              `already-fetched seeds will be skipped.`
            );
          }
          qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
        }
      } catch {
        clearInterval(interval);
        setS2JobId(null);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [s2JobId, projectId, qc]);

  // Poll GROBID bulk job
  useEffect(() => {
    if (!grobidJobId) return;
    const interval = setInterval(async () => {
      try {
        const status = await fetchBulkJobStatus(grobidJobId);
        setGrobidJob(status);
        if (status.status !== 'running') {
          clearInterval(interval);
          setGrobidJobId(null);
          qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
        }
      } catch {
        clearInterval(interval);
        setGrobidJobId(null);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [grobidJobId, projectId, qc]);

  // ---- Computed statistics ----
  const total = seeds.length;

  // OA stats: has_backward_citations = "OA refs cache exists (incl. empty)"
  const oaRefsFetched = seeds.filter(s => s.has_backward_citations).length;
  const oaRefsNoData = seeds.filter(s => s.backward_citations_no_oa_data).length;
  const oaCitingFetched = seeds.filter(s => s.has_forward_citations).length;
  const oaCitingNoData = seeds.filter(s => s.oa_forward_no_data).length;

  // S2 stats
  const s2RefsF = seeds.filter(s => s.s2_refs_fetched).length;
  const s2RefsNoData = seeds.filter(s => s.s2_refs_no_data).length;
  const s2CiteF = seeds.filter(s => s.s2_citing_fetched).length;
  const s2CiteNoData = seeds.filter(s => s.s2_citing_no_data).length;
  const s2NeedFetch = seeds.filter(s => !s.s2_refs_fetched || !s.s2_citing_fetched);
  const s2EstSeconds = s2NeedFetch.length * 2 * 1.1;

  // GROBID stats
  const seedsWithPdfs = seeds.filter(s => s.has_pdfs);
  const grobidExtracted = seeds.filter(s => s.grobid_fetched).length;
  const grobidNeedExtract = seedsWithPdfs.filter(s => !s.grobid_fetched);
  const grobidEstSeconds = grobidNeedExtract.length * 15;

  // Summary: "have references" = any source provided refs
  const seedsHaveRefs = seeds.filter(s =>
    (s.has_backward_citations && !s.backward_citations_no_oa_data) ||
    (s.s2_refs_fetched && !s.s2_refs_no_data) ||
    s.grobid_fetched
  ).length;
  const seedsHaveCiting = seeds.filter(s =>
    (s.has_forward_citations && !s.oa_forward_no_data) ||
    (s.s2_citing_fetched && !s.s2_citing_no_data)
  ).length;
  const allEnriched = seedsHaveRefs === total && seedsHaveCiting === total;

  // OA: seeds missing backward OR forward (eligible for OA bulk fetch)
  const oaNeedFetch = seeds.filter(s => !s.has_backward_citations || !s.has_forward_citations);

  // ---- Handlers ----

  const handleOaFetchAll = useCallback(async () => {
    setOaFetching(true);
    setOaErrors([]);
    setOaProgress({ done: 0, total: oaNeedFetch.length });
    const newErrors: FetchError[] = [];
    for (let i = 0; i < oaNeedFetch.length; i++) {
      const seed = oaNeedFetch[i];
      if (!seed.has_backward_citations) {
        try { await fetchBackwardCitationsEnrich(seed.id); }
        catch (e) { newErrors.push({ workId: seed.id, title: seed.title, direction: 'references', reason: parseErrorReason(e) }); }
      }
      if (!seed.has_forward_citations) {
        try { await fetchForwardCitationsEnrich(seed.id); }
        catch (e) { newErrors.push({ workId: seed.id, title: seed.title, direction: 'citing', reason: parseErrorReason(e) }); }
      }
      setOaProgress({ done: i + 1, total: oaNeedFetch.length });
    }
    setOaFetching(false);
    setOaProgress(null);
    setOaErrors(newErrors);
    qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
  }, [oaNeedFetch, projectId, qc]);

  const handleS2FetchAll = useCallback(async () => {
    setS2RateLimitMsg(null);
    try {
      const accepted = await startBulkS2Fetch(seeds.map(s => s.id));
      setS2JobId(accepted.job_id);
      setS2Job({ job_id: accepted.job_id, source: 'semantic_scholar', status: 'running', done: 0, total: s2NeedFetch.length, rate_limited_at: null, errors: [] });
    } catch (e) {
      setS2RateLimitMsg(`Failed to start S2 fetch: ${parseErrorReason(e)}`);
    }
  }, [seeds, s2NeedFetch.length]);

  const handleS2Cancel = useCallback(async () => {
    if (!s2JobId) return;
    try { await cancelBulkJob(s2JobId); } catch { /* ignore */ }
  }, [s2JobId]);

  const handleGrobidExtractAll = useCallback(async () => {
    try {
      const accepted = await startBulkGrobidExtract(seedsWithPdfs.map(s => s.id));
      setGrobidJobId(accepted.job_id);
      setGrobidJob({ job_id: accepted.job_id, source: 'grobid', status: 'running', done: 0, total: grobidNeedExtract.length, rate_limited_at: null, errors: [] });
    } catch (e) {
      alert(`Failed to start GROBID extraction: ${parseErrorReason(e)}`);
    }
  }, [seedsWithPdfs, grobidNeedExtract.length]);

  const handleGrobidCancel = useCallback(async () => {
    if (!grobidJobId) return;
    try { await cancelBulkJob(grobidJobId); } catch { /* ignore */ }
  }, [grobidJobId]);

  if (total === 0) return null;

  // ---- Render helpers ----

  const s2Running = s2Job?.status === 'running';
  const grobidRunning = grobidJob?.status === 'running';

  function s2ProgressLabel() {
    if (!s2Job) return null;
    const rem = (s2Job.total - s2Job.done) * 2 * 1.1;
    return `Fetching... ${s2Job.done}/${s2Job.total} seeds (≈${formatDuration(rem)} remaining)`;
  }

  function grobidProgressLabel() {
    if (!grobidJob) return null;
    const rem = (grobidJob.total - grobidJob.done) * 15;
    return `Extracting... ${grobidJob.done}/${grobidJob.total} seeds (≈${formatDuration(rem)} remaining)`;
  }

  return (
    <div className="px-4 py-2 bg-blue-50 border-b border-blue-100 text-xs">
      {/* Summary row — always visible */}
      <div className="flex items-center justify-between gap-2">
        <span className={allEnriched ? 'text-green-700' : 'text-amber-700'}>
          {allEnriched
            ? `✓ All ${total} seeds enriched`
            : `⚠ ${seedsHaveRefs}/${total} seeds have references · ${seedsHaveCiting}/${total} have citing papers`
          }
        </span>
        <button
          onClick={toggleExpanded}
          className="ml-auto shrink-0 text-blue-600 hover:text-blue-800 font-medium whitespace-nowrap"
        >
          {expanded ? '▴ Details' : '▾ Details'}
        </button>
      </div>

      {/* Expanded per-source rows */}
      {expanded && (
        <div className="mt-2 space-y-1.5">

          {/* OpenAlex row */}
          <div className="flex items-start gap-2 flex-wrap">
            <span className="font-medium text-gray-600 w-28 shrink-0">OpenAlex</span>
            <span className="text-gray-700 flex-1">
              References: {oaRefsFetched}/{total} fetched
              {oaRefsNoData > 0 && (
                <span className="text-amber-600 ml-1">({oaRefsNoData} no OA data)</span>
              )}
              {' · '}
              Citing: {oaCitingFetched}/{total} fetched
              {oaCitingNoData > 0 && (
                <span className="text-amber-600 ml-1">({oaCitingNoData} no OA data)</span>
              )}
            </span>
            {oaNeedFetch.length > 0 && (
              <button
                onClick={handleOaFetchAll}
                disabled={oaFetching}
                className="px-2 py-0.5 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
              >
                {oaFetching
                  ? `Fetching ${oaProgress?.done}/${oaProgress?.total}...`
                  : 'Fetch all'}
              </button>
            )}
            {oaNeedFetch.length === 0 && (
              <span className="text-green-600 shrink-0">✓</span>
            )}
          </div>

          {/* OA errors */}
          {oaErrors.length > 0 && (
            <div className="ml-28 mt-1 bg-red-50 border border-red-200 rounded p-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-red-700 font-medium">{oaErrors.length} fetch error{oaErrors.length !== 1 ? 's' : ''}</span>
                <button onClick={() => setOaErrors([])} className="text-red-400 hover:text-red-600 px-1">&times;</button>
              </div>
              <ul className="space-y-0.5">
                {oaErrors.map((err, i) => (
                  <li key={i} className="text-red-600">
                    <span className="font-medium">{err.title}</span> ({err.direction}): {err.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Semantic Scholar row */}
          <div className="flex items-start gap-2 flex-wrap">
            <span className="font-medium text-gray-600 w-28 shrink-0">Semantic Scholar</span>
            <span className="text-gray-700 flex-1">
              References: {s2RefsF}/{total} fetched
              {s2RefsNoData > 0 && (
                <span className="text-amber-600 ml-1">({s2RefsNoData} no S2 data)</span>
              )}
              {' · '}
              Citing: {s2CiteF}/{total} fetched
              {s2CiteNoData > 0 && (
                <span className="text-amber-600 ml-1">({s2CiteNoData} no S2 data)</span>
              )}
            </span>
            {s2Running ? (
              <div className="flex items-center gap-1 shrink-0">
                <span className="text-blue-600">{s2ProgressLabel()}</span>
                <button
                  onClick={handleS2Cancel}
                  className="px-2 py-0.5 text-xs font-medium border border-gray-300 rounded hover:bg-gray-100"
                >
                  Cancel
                </button>
              </div>
            ) : s2NeedFetch.length > 0 ? (
              <button
                onClick={handleS2FetchAll}
                className="px-2 py-0.5 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 shrink-0"
              >
                Fetch all ({formatDuration(s2EstSeconds)})
              </button>
            ) : (
              <span className="text-green-600 shrink-0">✓</span>
            )}
          </div>

          {/* S2 rate limit warning */}
          {s2RateLimitMsg && (
            <div className="ml-28 mt-1 bg-amber-50 border border-amber-200 rounded p-2 flex items-start gap-1">
              <span className="text-amber-700 flex-1">⚠ {s2RateLimitMsg}</span>
              <button onClick={() => setS2RateLimitMsg(null)} className="text-amber-400 hover:text-amber-600 px-1 shrink-0">&times;</button>
            </div>
          )}

          {/* GROBID row — only when GROBID is configured and available */}
          {grobidAvailable && seedsWithPdfs.length > 0 && (
            <div className="flex items-start gap-2 flex-wrap">
              <span className="font-medium text-gray-600 w-28 shrink-0">GROBID</span>
              <span className="text-gray-700 flex-1">
                References: {grobidExtracted}/{seedsWithPdfs.length} extracted
                <span className="text-gray-500 ml-1">({seedsWithPdfs.length} of {total} seeds have PDFs)</span>
              </span>
              {grobidRunning ? (
                <div className="flex items-center gap-1 shrink-0">
                  <span className="text-blue-600">{grobidProgressLabel()}</span>
                  <button
                    onClick={handleGrobidCancel}
                    className="px-2 py-0.5 text-xs font-medium border border-gray-300 rounded hover:bg-gray-100"
                  >
                    Cancel
                  </button>
                </div>
              ) : grobidNeedExtract.length > 0 ? (
                <button
                  onClick={handleGrobidExtractAll}
                  className="px-2 py-0.5 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 shrink-0"
                >
                  Extract all ({formatDuration(grobidEstSeconds)})
                </button>
              ) : (
                <span className="text-green-600 shrink-0">✓</span>
              )}
            </div>
          )}

          {/* GROBID hint for seeds with no OA data + has PDF + onSelectWork */}
          {(() => {
            const hintSeeds = grobidAvailable && onSelectWork
              ? seeds.filter(s => s.backward_citations_no_oa_data && s.has_pdfs && !s.grobid_fetched)
              : [];
            if (hintSeeds.length === 0) return null;
            return (
              <div className="ml-28 text-gray-500">
                Try GROBID for:{' '}
                {hintSeeds.map((s, i) => (
                  <span key={s.id}>
                    {i > 0 && ', '}
                    <button
                      onClick={() => onSelectWork!(s.id)}
                      className="underline hover:text-gray-700 focus:outline-none"
                      title={`Open "${s.title}" to run GROBID reference extraction`}
                    >
                      {s.title.length > 40 ? s.title.slice(0, 40) + '…' : s.title}
                    </button>
                  </span>
                ))}
              </div>
            );
          })()}

        </div>
      )}
    </div>
  );
}
