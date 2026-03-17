import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchWorks, fetchWork, createWork, updateWork, deleteWork, importBibtex,
         fetchForwardCitations, fetchBackwardCitations, mergeWorks, fetchDuplicates,
         addWorkDOIAlias, removeWorkDOIAlias } from '../api';

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

export function useForwardCitations(
  workId: number | null,
  params?: { offset?: number; limit?: number; sort?: string },
) {
  return useQuery({
    queryKey: ['works', workId, 'citations', 'forward', params],
    queryFn: () => fetchForwardCitations(workId!, params),
    enabled: workId !== null,
    placeholderData: (prev) => prev,
  });
}

export function useBackwardCitations(
  workId: number | null,
  params?: { offset?: number; limit?: number; sort?: string },
) {
  return useQuery({
    queryKey: ['works', workId, 'citations', 'backward', params],
    queryFn: () => fetchBackwardCitations(workId!, params),
    enabled: workId !== null,
    placeholderData: (prev) => prev,
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

export function useAddWorkDOIAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, doi }: { workId: number; doi: string }) =>
      addWorkDOIAlias(workId, doi),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId] });
    },
  });
}

export function useRemoveWorkDOIAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, doi }: { workId: number; doi: string }) =>
      removeWorkDOIAlias(workId, doi),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['works', workId] });
    },
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

export function useMergeWorks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ targetId, sourceId }: { targetId: number; sourceId: number }) =>
      mergeWorks(targetId, sourceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['works'] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useDuplicates(enabled: boolean) {
  return useQuery({
    queryKey: ['works', 'duplicates'],
    queryFn: fetchDuplicates,
    enabled,
  });
}
