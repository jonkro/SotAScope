import { useMutation, useQueryClient } from '@tanstack/react-query';
import { enrichDOI, enrichDOIBatch, fetchBackwardCitationsEnrich,
         fetchForwardCitationsEnrich, enrichFromCrossref } from '../api';

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
    },
  });
}

export function useEnrichFromCrossref() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: enrichFromCrossref,
    onSuccess: (_data, workId) => {
      qc.invalidateQueries({ queryKey: ['works', workId] });
    },
  });
}
