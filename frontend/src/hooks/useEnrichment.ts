import { useMutation, useQueryClient } from '@tanstack/react-query';
import { enrichDOI, enrichDOIBatch, fetchBackwardCitationsEnrich,
         fetchForwardCitationsEnrich, enrichFromCrossref,
         enrichFromSemanticScholar, resolveDOI, confirmDOI } from '../api';

export function useEnrichDOI() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: enrichDOI,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['works'] }),
  });
}

export function useEnrichDOIBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: enrichDOIBatch,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['works'] }),
  });
}

export function useFetchBackwardCitations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fetchBackwardCitationsEnrich,
    onSuccess: () => {
      // Kick the lock-status poller into fast mode — data will refresh when lock clears
      qc.invalidateQueries({ queryKey: ['works', 'lock-status'] });
    },
  });
}

export function useFetchForwardCitations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, forceRefresh }: { workId: number; forceRefresh?: boolean }) =>
      fetchForwardCitationsEnrich(workId, forceRefresh),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['works', 'lock-status'] });
    },
  });
}

export function useEnrichFromCrossref() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: enrichFromCrossref,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['works', 'lock-status'] });
    },
  });
}

export function useEnrichFromSemanticScholar() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, direction }: { workId: number; direction?: 'both' | 'backward' | 'forward' }) =>
      enrichFromSemanticScholar(workId, direction ?? 'both'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['works', 'lock-status'] });
    },
  });
}

export function useResolveDOI() {
  return useMutation({
    mutationFn: resolveDOI,
  });
}

export function useConfirmDOI() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, doi }: { workId: number; doi: string }) => confirmDOI(workId, doi),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['works'] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}
