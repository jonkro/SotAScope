import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchFields, createField } from '../api';

export function useFields() {
  return useQuery({
    queryKey: ['fields'],
    queryFn: fetchFields,
  });
}

export function useCreateField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createField,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fields'] }),
  });
}
