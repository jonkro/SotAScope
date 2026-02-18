import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchWorks, fetchWork, createWork, updateWork, deleteWork, importBibtex,
         fetchForwardCitations, fetchBackwardCitations } from '../api';

export function useWorks(params: { offset?: number; limit?: number; q?: string; venue_id?: number; year?: number }) {
  return useQuery({
    queryKey: ['works', params],
    queryFn: () => fetchWorks(params),
  });
}

export function useWork(workId: number | null) {
  return useQuery({
    queryKey: ['works', workId],
    queryFn: () => fetchWork(workId!),
    enabled: workId !== null,
  });
}

export function useForwardCitations(workId: number | null) {
  return useQuery({
    queryKey: ['works', workId, 'citations', 'forward'],
    queryFn: () => fetchForwardCitations(workId!),
    enabled: workId !== null,
  });
}

export function useBackwardCitations(workId: number | null) {
  return useQuery({
    queryKey: ['works', workId, 'citations', 'backward'],
    queryFn: () => fetchBackwardCitations(workId!),
    enabled: workId !== null,
  });
}

export function useCreateWork() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createWork,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['works'] }),
  });
}

export function useUpdateWork() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, data }: { workId: number; data: Record<string, unknown> }) => updateWork(workId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['works'] }),
  });
}

export function useDeleteWork() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteWork,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['works'] }),
  });
}

export function useImportBibtex() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: importBibtex,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['works'] }),
  });
}
