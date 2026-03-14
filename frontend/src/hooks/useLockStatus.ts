import { useQuery } from '@tanstack/react-query';
import { fetchLockStatus } from '../api';

/**
 * Polls the work lock registry.
 *
 * - Polls every 2 s when any locks are active.
 * - Polls every 10 s when idle (no active locks).
 *
 * Returns helpers for checking individual work lock state.
 */
export function useLockStatus() {
  const { data } = useQuery({
    queryKey: ['works', 'lock-status'],
    queryFn: fetchLockStatus,
    refetchInterval: (query) => {
      const locks = query.state.data?.locks ?? {};
      return Object.keys(locks).length > 0 ? 2000 : 10000;
    },
  });

  const locks = new Map<number, string>();
  if (data?.locks) {
    for (const [key, val] of Object.entries(data.locks)) {
      locks.set(Number(key), val);
    }
  }

  const isLocked = (workId: number) => locks.has(workId);
  const lockReason = (workId: number) => locks.get(workId) ?? null;

  return { locks, isLocked, lockReason };
}
