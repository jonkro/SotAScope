import { useMutation, useQueryClient } from '@tanstack/react-query';
import { addVenueField, removeVenueField } from '../api';

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
