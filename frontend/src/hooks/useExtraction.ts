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
  runExtraction,
  runBatchExtraction,
  fetchExtractionJob,
  getExtractionResults,
  updateWorkNote,
  manualFillExtractionCell,
  dismissExtractionProposal,
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

export function useExtractionResults(schemaId: number | undefined, workIds: number[]) {
  const key = workIds.slice().sort((a, b) => a - b).join(',');
  return useQuery({
    queryKey: ['extraction', 'results', schemaId, key],
    queryFn: () => getExtractionResults(schemaId!, workIds),
    enabled: schemaId != null && workIds.length > 0,
  });
}

export function useRunBatchExtraction(schemaId: number) {
  return useMutation({
    mutationFn: ({
      workIds,
      reEvaluateEdited = false,
    }: {
      workIds: number[];
      reEvaluateEdited?: boolean;
    }) => runBatchExtraction(schemaId, workIds, reEvaluateEdited),
    // Results are invalidated by the caller once the job completes
  });
}

export function useRunSingleExtraction(schemaId: number) {
  return useMutation({
    mutationFn: (workId: number) => runExtraction(schemaId, workId),
    // Results are invalidated by the caller once the job completes
  });
}

/**
 * Polls the status of an extraction job.
 * Polling stops automatically when the job status is "completed".
 */
export function useExtractionJob(jobId: string | null) {
  return useQuery({
    queryKey: ['extraction', 'job', jobId],
    queryFn: () => fetchExtractionJob(jobId!),
    enabled: jobId != null,
    refetchInterval: (query) => {
      const job = query.state.data;
      if (!job || job.status === 'completed') return false;
      return 2000;
    },
  });
}

export function useAcceptExtractionNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, noteId }: { workId: number; noteId: number }) =>
      updateWorkNote(workId, noteId, { provenance: 'ai_reviewed' }),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['extraction', 'results'] });
      qc.invalidateQueries({ queryKey: ['workNotes', workId] });
    },
  });
}

/**
 * Accept an ai_proposal by:
 * 1. Overwriting the existing answer note with the proposal content + ai_reviewed provenance
 * 2. Deleting the proposal note
 *
 * This avoids the bug where accepting the proposal note's provenance leaves two
 * non-proposal notes for the same cell, causing non-deterministic results.
 */
export function useAcceptExtractionProposal(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      workId,
      answerNoteId,
      columnId,
      proposalContent,
    }: {
      workId: number;
      answerNoteId: number;
      columnId: number;
      proposalContent: string;
    }) => {
      await updateWorkNote(workId, answerNoteId, {
        content: proposalContent,
        provenance: 'ai_reviewed',
      });
      await dismissExtractionProposal(schemaId, columnId, workId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'results', schemaId] });
    },
  });
}

export function useEditExtractionNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ workId, noteId, content }: { workId: number; noteId: number; content: string }) =>
      updateWorkNote(workId, noteId, { content }),
    onSuccess: (_data, { workId }) => {
      qc.invalidateQueries({ queryKey: ['extraction', 'results'] });
      qc.invalidateQueries({ queryKey: ['workNotes', workId] });
    },
  });
}

export function useManualFillExtractionCell(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      columnId,
      workId,
      content,
    }: {
      columnId: number;
      workId: number;
      content: string;
    }) => manualFillExtractionCell(schemaId, columnId, workId, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'results', schemaId] });
    },
  });
}

export function useDismissExtractionProposal(schemaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ columnId, workId }: { columnId: number; workId: number }) =>
      dismissExtractionProposal(schemaId, columnId, workId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['extraction', 'results', schemaId] });
    },
  });
}
