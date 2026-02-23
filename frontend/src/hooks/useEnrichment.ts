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
    onSuccess: (_data, workId) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'citations'] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useFetchForwardCitations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, forceRefresh }: { workId: number; forceRefresh?: boolean }) =>
      fetchForwardCitationsEnrich(workId, forceRefresh),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'citations'] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useEnrichFromCrossref() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: enrichFromCrossref,
    onSuccess: (_data, workId) => {
      qc.invalidateQueries({ queryKey: ['works', workId] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useEnrichFromSemanticScholar() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: enrichFromSemanticScholar,
    onSuccess: (_data, workId) => {
      qc.invalidateQueries({ queryKey: ['works', workId, 'citations'] });
      qc.invalidateQueries({ queryKey: ['works', workId] });
      qc.invalidateQueries({ queryKey: ['projects'] });
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
