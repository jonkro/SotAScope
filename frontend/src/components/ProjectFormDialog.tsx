import { useState } from 'react';

export default function ProjectFormDialog({
  initial,
  onSubmit,
  onCancel,
}: {
  initial?: { name: string; description: string; owner: string };
  onSubmit: (data: { name: string; description?: string; owner?: string }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [owner, setOwner] = useState(initial?.owner ?? '');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    onSubmit({
      name: name.trim(),
      description: description.trim() || undefined,
      owner: owner.trim() || undefined,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onCancel}>
      <form
        onSubmit={handleSubmit}
        className="bg-white rounded-lg shadow-lg w-full max-w-md mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-gray-900 mb-4">
          {initial ? 'Edit Project' : 'New Project'}
        </h2>

        <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          autoFocus
        />

        <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />

        <label className="block text-sm font-medium text-gray-700 mb-1">Owner</label>
        <input
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          className="w-full px-3 py-2 text-sm border border-gray-300 rounded mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />

        <div className="flex justify-end gap-3">
          <button type="button" onClick={onCancel} className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50">
            Cancel
          </button>
          <button type="submit" className="px-3 py-1.5 text-sm text-white bg-blue-600 rounded hover:bg-blue-700">
            {initial ? 'Save' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  );
}
