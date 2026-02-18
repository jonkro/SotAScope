import { useQuery } from '@tanstack/react-query';
import { fetchTimeline } from '../api';

export function useTimeline(projectId: number) {
  return useQuery({
    queryKey: ['projects', projectId, 'timeline'],
    queryFn: () => fetchTimeline(projectId),
  });
}
