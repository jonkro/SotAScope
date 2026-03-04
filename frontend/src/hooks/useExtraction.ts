import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getExtractionSchemas,
  getExtractionSchema,
  createExtractionSchema,
  updateExtractionSchema,
  deleteExtractionSchema,
  createExtractionColumn,
  updateExtractionColumn,
  deleteExtractionColumn,
  reorderExtractionColumns,
} from '../api';

export function useExtractionSchemas(projectId?: number) {
  return useQuery({
    queryKey: ['extraction', 'schemas', projectId],
    queryFn: () => getExtractionSchemas(projectId),
  });
}

export function useExtractionSchema(schemaId: number | undefined) {
  return useQuery({
    queryKey: ['extraction', 'schema', schemaId],
    queryFn: () => getExtractionSchema(schemaId!),
    enabled: schemaId != null,
  });
}

export function useCreateExtractionSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { title: string; description?: string | null; project_id?: number | null }) =>
      createExtractionSchema(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
    },
  });
}

export function useUpdateExtractionSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      schemaId,
      data,
    }: {
      schemaId: number;
      data: { title?: string; description?: string | null };
    }) => updateExtractionSchema(schemaId, data),
    onSuccess: (schema) => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
      qc.invalidateQueries({ queryKey: ['extraction', 'schema', schema.id] });
    },
  });
}

export function useDeleteExtractionSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (schemaId: number) => deleteExtractionSchema(schemaId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
    },
  });
}

export function useCreateExtractionColumn(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      name: string;
      prompt: string;
      description?: string | null;
      allowed_values?: string[] | null;
      sort_order?: number;
    }) => createExtractionColumn(schemaId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schema', schemaId] });
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
    },
  });
}

export function useUpdateExtractionColumn(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      columnId,
      data,
    }: {
      columnId: number;
      data: {
        name?: string;
        prompt?: string;
        description?: string | null;
        allowed_values?: string[] | null;
      };
    }) => updateExtractionColumn(columnId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schema', schemaId] });
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
    },
  });
}

export function useDeleteExtractionColumn(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (columnId: number) => deleteExtractionColumn(columnId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schema', schemaId] });
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
    },
  });
}

export function useReorderExtractionColumns(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (columnIds: number[]) => reorderExtractionColumns(schemaId, columnIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'schema', schemaId] });
      qc.invalidateQueries({ queryKey: ['extraction', 'schemas'] });
    },
  });
}
