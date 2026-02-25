import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchSettings, updateSetting, fetchLLMModels } from '../api';

export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });
}

export function useUpdateSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      updateSetting(key, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  });
}

export function useLLMModels(enabled: boolean) {
  return useQuery({
    queryKey: ['llm', 'models'],
    queryFn: fetchLLMModels,
    enabled,
    retry: false,
    staleTime: 0,
  });
}
