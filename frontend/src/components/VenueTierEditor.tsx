import { useFields } from '../hooks/useFields';
import { useAddVenueField, useRemoveVenueField } from '../hooks/useVenueTiers';
import type { VenueFieldNested } from '../types';

export default function VenueFieldEditor({
  venueId,
  venueFields,
}: {
  venueId: number;
  venueFields: VenueFieldNested[];
}) {
  const { data: allFields } = useFields();
  const addField = useAddVenueField();
  const removeField = useRemoveVenueField();

  if (!allFields) return null;

  const assignedFieldIds = new Set(venueFields.map((vf) => vf.field_id));

  return (
    <div className="space-y-1">
      {allFields.map((field) => {
        const isAssigned = assignedFieldIds.has(field.id);
        return (
          <label key={field.id} className="flex items-center gap-2 text-xs text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={isAssigned}
              onChange={() => {
                if (isAssigned) {
                  removeField.mutate({ venueId, fieldId: field.id });
                } else {
                  addField.mutate({ venueId, fieldId: field.id });
                }
              }}
              className="rounded border-gray-300"
            />
            {field.name}
          </label>
        );
      })}
      {allFields.length === 0 && (
        <p className="text-xs text-gray-400">No fields defined. Create fields from the Fields tab.</p>
      )}
    </div>
  );
}
