import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addVenueField,
  fetchProjectVenueTiers,
  removeVenueField,
  resetProjectVenueTier,
  setProjectVenueTier,
} from '../api';

// ---- Global venue-field associations ----

export function useAddVenueField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId, fieldId }: { venueId: number; fieldId: number }) =>
      addVenueField(venueId, fieldId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['venues'] });
    },
  });
}

export function useRemoveVenueField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId, fieldId }: { venueId: number; fieldId: number }) =>
      removeVenueField(venueId, fieldId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['venues'] });
    },
  });
}

// ---- Per-project venue tiers ----

export function useProjectVenueTiers(projectId: number) {
  return useQuery({
    queryKey: ['projects', projectId, 'venue-tiers'],
    queryFn: () => fetchProjectVenueTiers(projectId),
    enabled: projectId > 0,
  });
}

export function useSetProjectVenueTier(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId, tier }: { venueId: number; tier: number }) =>
      setProjectVenueTier(projectId, venueId, tier),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'venue-tiers'] });
      // Invalidate timeline so tier changes are reflected immediately
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
    },
  });
}

export function useResetProjectVenueTier(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId }: { venueId: number }) =>
      resetProjectVenueTier(projectId, venueId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'venue-tiers'] });
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'timeline'] });
    },
  });
}
