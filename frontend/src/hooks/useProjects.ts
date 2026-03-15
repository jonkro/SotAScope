import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchProjects, fetchProject, createProject, updateProject, deleteProject,
  fetchTopicList, createTopicList, updateTopicList, deleteTopicList,
  addWorkToTopicList, removeWorkFromTopicList,
  addIgnoredWork, removeIgnoredWork,
  fetchMergePreview, mergeProject,
  importProjectZip, resolveImportCollision,
} from '../api';
import type { MergeDecisions, ImportResolveRequest } from '../types';

export function useProjects(params?: { offset?: number; limit?: number; q?: string }) {
  return useQuery({
    queryKey: ['projects', params],
    queryFn: () => fetchProjects(params),
  });
}

export function useProject(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId],
    queryFn: () => fetchProject(projectId!),
    enabled: projectId !== null,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createProject,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data: Record<string, unknown> }) =>
      updateProject(projectId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteProject,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// ---- Topic Lists ----

export function useTopicList(projectId: number, topicListId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'topic-lists', topicListId],
    queryFn: () => fetchTopicList(projectId, topicListId!),
    enabled: topicListId !== null,
  });
}

export function useCreateTopicList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, data }: { projectId: number; data: { name: string; color: string } }) =>
      createTopicList(projectId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useUpdateTopicList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, topicListId, data }: { projectId: number; topicListId: number; data: Record<string, unknown> }) =>
      updateTopicList(projectId, topicListId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useDeleteTopicList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, topicListId }: { projectId: number; topicListId: number }) =>
      deleteTopicList(projectId, topicListId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useAddWorkToTopicList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, topicListId, workId }: { projectId: number; topicListId: number; workId: number }) =>
      addWorkToTopicList(projectId, topicListId, workId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useRemoveWorkFromTopicList() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, topicListId, workId }: { projectId: number; topicListId: number; workId: number }) =>
      removeWorkFromTopicList(projectId, topicListId, workId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// ---- Project Merge ----

export function useMergePreview(targetId: number, sourceId: number | null) {
  return useQuery({
    queryKey: ['projects', targetId, 'merge-preview', sourceId],
    queryFn: () => fetchMergePreview(targetId, sourceId!),
    enabled: sourceId !== null,
  });
}

export function useMergeProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ targetId, sourceId, decisions }: { targetId: number; sourceId: number; decisions: MergeDecisions }) =>
      mergeProject(targetId, sourceId, decisions),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// ---- Ignored Works ----

export function useAddIgnoredWork() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, workId }: { projectId: number; workId: number }) =>
      addIgnoredWork(projectId, workId),
    onSuccess: (_data, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['projects', projectId] });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
    },
  });
}

export function useRemoveIgnoredWork() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, workId }: { projectId: number; workId: number }) =>
      removeIgnoredWork(projectId, workId),
    onSuccess: (_data, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['projects', projectId] });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
    },
  });
}

// ---- Project Import ----

export function useImportProject() {
  return useMutation({
    mutationFn: (file: File) => importProjectZip(file),
  });
}

export function useResolveImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tempId, body }: { tempId: number; body: ImportResolveRequest }) =>
      resolveImportCollision(tempId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}
