import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchVenues, fetchVenue, updateVenue, addVenueAlias, deleteVenueAlias } from '../api';

export function useVenues(params?: { offset?: number; limit?: number; q?: string }) {
  return useQuery({
    queryKey: ['venues', params],
    queryFn: () => fetchVenues(params),
  });
}

export function useVenue(venueId: number | null) {
  return useQuery({
    queryKey: ['venues', venueId],
    queryFn: () => fetchVenue(venueId!),
    enabled: venueId !== null,
  });
}

export function useUpdateVenue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId, data }: { venueId: number; data: Record<string, unknown> }) =>
      updateVenue(venueId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['venues'] }),
  });
}

export function useAddVenueAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId, alias }: { venueId: number; alias: string }) => addVenueAlias(venueId, alias),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['venues'] }),
  });
}

export function useDeleteVenueAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ venueId, aliasId }: { venueId: number; aliasId: number }) => deleteVenueAlias(venueId, aliasId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['venues'] }),
  });
}
