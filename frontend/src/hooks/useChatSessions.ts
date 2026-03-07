import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  clearChatMessages,
  deleteChatSession,
  getChatSession,
  getOrCreateAutoSession,
  listChatSessions,
  saveChatSession,
} from '../api';

export function useGetOrCreateAutoSession() {
  return useMutation({ mutationFn: getOrCreateAutoSession });
}

export function useListChatSessions(workId: number | null, projectId: number | null) {
  return useQuery({
    queryKey: ['chat-sessions', workId, projectId],
    queryFn: () => listChatSessions(workId, projectId),
    enabled: workId != null || projectId != null,
  });
}

export function useGetChatSession(sessionId: number | null) {
  return useQuery({
    queryKey: ['chat-session', sessionId],
    queryFn: () => getChatSession(sessionId!),
    enabled: sessionId != null,
  });
}

export function useSaveChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, title }: { sessionId: number; title: string }) =>
      saveChatSession(sessionId, title),
    onSuccess: (_data, { sessionId }) => {
      // Invalidate the session list so the new saved session appears in Load dropdown
      qc.invalidateQueries({ queryKey: ['chat-sessions'] });
      qc.invalidateQueries({ queryKey: ['chat-session', sessionId] });
    },
  });
}

export function useDeleteChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: number) => deleteChatSession(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['chat-sessions'] });
    },
  });
}

export function useClearChatMessages() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: number) => clearChatMessages(sessionId),
    onSuccess: (_data, sessionId) => {
      qc.invalidateQueries({ queryKey: ['chat-session', sessionId] });
    },
  });
}
