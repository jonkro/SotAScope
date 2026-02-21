import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchWorkNotes, createWorkNote, updateWorkNote, deleteWorkNote, fetchProjectNotes } from '../api';

export function useWorkNotes(workId: number, projectId?: number) {
  return useQuery({
    queryKey: ['workNotes', workId, projectId],
    queryFn: () => fetchWorkNotes(workId, projectId),
  });
}

export function useCreateWorkNote(workId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { content: string; note_type?: string | null; project_id?: number | null }) =>
      createWorkNote(workId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workNotes', workId] });
      qc.invalidateQueries({ queryKey: ['projectNotes'] });
    },
  });
}

export function useUpdateWorkNote(workId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ noteId, data }: { noteId: number; data: { content?: string; note_type?: string | null; is_outdated?: boolean } }) =>
      updateWorkNote(workId, noteId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workNotes', workId] });
      qc.invalidateQueries({ queryKey: ['projectNotes'] });
    },
  });
}

export function useDeleteWorkNote(workId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: number) => deleteWorkNote(workId, noteId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workNotes', workId] });
      qc.invalidateQueries({ queryKey: ['projectNotes'] });
    },
  });
}

export function useProjectNotes(projectId: number) {
  return useQuery({
    queryKey: ['projectNotes', projectId],
    queryFn: () => fetchProjectNotes(projectId),
  });
}
