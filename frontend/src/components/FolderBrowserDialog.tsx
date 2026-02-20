import { useState, useEffect } from 'react';
import { browseDirectory, createDirectory } from '../api';
import type { BrowseResult } from '../types';

export default function FolderBrowserDialog({
  initialPath,
  onSelect,
  onCancel,
}: {
  initialPath: string;
  onSelect: (path: string) => void;
  onCancel: () => void;
}) {
  const [browse, setBrowse] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');

  const navigateTo = (path?: string) => {
    setLoading(true);
    setError(null);
    browseDirectory(path)
      .then((result) => {
        setBrowse(result);
        setCreatingFolder(false);
        setNewFolderName('');
      })
      .catch((err) => setError(err.message || 'Failed to browse directory'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    navigateTo(initialPath || undefined);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreateFolder = () => {
    if (!newFolderName.trim() || !browse) return;
    const fullPath = browse.current_path + '/' + newFolderName.trim();
    setLoading(true);
    createDirectory(fullPath)
      .then((result) => navigateTo(result.path))
      .catch((err) => setError(err.message || 'Failed to create directory'))
      .finally(() => setLoading(false));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onCancel}>
      <div
        className="bg-white rounded-lg shadow-lg w-full max-w-lg mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h3 className="text-lg font-semibold text-gray-900">Select Folder</h3>
          <button
            onClick={onCancel}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        {/* Current path + Up button */}
        <div className="flex items-center gap-2 px-4 py-2 bg-gray-50 border-b">
          <div className="flex-1 text-sm font-mono text-gray-700 truncate">
            {browse?.current_path ?? '...'}
          </div>
          <button
            onClick={() => browse?.parent_path && navigateTo(browse.parent_path)}
            disabled={!browse?.parent_path || loading}
            className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Up
          </button>
        </div>

        {/* Directory listing */}
        <div className="max-h-[300px] overflow-y-auto px-4 py-2">
          {loading && !browse && (
            <p className="text-sm text-gray-500 py-4 text-center">Loading...</p>
          )}
          {error && (
            <p className="text-sm text-red-600 py-2">{error}</p>
          )}
          {browse && browse.directories.length === 0 && !loading && (
            <p className="text-sm text-gray-400 py-4 text-center">No subdirectories</p>
          )}
          {browse?.directories.map((dir) => (
            <button
              key={dir}
              onClick={() => navigateTo(browse.current_path + '/' + dir)}
              disabled={loading}
              className="w-full flex items-center gap-2 px-2 py-1.5 text-sm text-left rounded hover:bg-blue-50 disabled:opacity-50"
            >
              <span className="text-yellow-500">&#128193;</span>
              <span>{dir}/</span>
            </button>
          ))}
        </div>

        {/* New folder inline */}
        <div className="px-4 py-2 border-t">
          {creatingFolder ? (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateFolder()}
                placeholder="Folder name"
                autoFocus
                className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={handleCreateFolder}
                disabled={!newFolderName.trim() || loading}
                className="px-2 py-1 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
              >
                Create
              </button>
              <button
                onClick={() => { setCreatingFolder(false); setNewFolderName(''); }}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setCreatingFolder(true)}
              disabled={loading}
              className="text-sm text-blue-600 hover:text-blue-800 disabled:opacity-50"
            >
              + New Folder
            </button>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-4 py-3 border-t">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={() => browse && onSelect(browse.current_path)}
            disabled={!browse || loading}
            className="px-3 py-1.5 text-sm text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            Select
          </button>
        </div>
      </div>
    </div>
  );
}
