import { useState, useEffect } from 'react';
import PageHeader from '../components/PageHeader';
import FolderBrowserDialog from '../components/FolderBrowserDialog';
import { useSettings, useUpdateSetting } from '../hooks/useSettings';
import { migratePDFStorage } from '../api';
import type { PDFMigrationResult } from '../types';

export default function SettingsPage() {
  const { data: settings, isLoading } = useSettings();
  const updateSetting = useUpdateSetting();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const [migrationResult, setMigrationResult] = useState<PDFMigrationResult | null>(null);

  // Sync drafts when settings load
  useEffect(() => {
    if (settings) {
      const initial: Record<string, string> = {};
      for (const s of settings) initial[s.key] = s.value;
      setDrafts(initial);
    }
  }, [settings]);

  const hasChanges = settings?.some((s) => drafts[s.key] !== s.value) ?? false;

  const handleSave = async () => {
    if (!settings) return;
    const changed = settings.filter((s) => drafts[s.key] !== s.value);

    // Handle PDF migration separately
    const pdfSetting = changed.find((s) => s.key === 'pdf_storage_path');
    const otherSettings = changed.filter((s) => s.key !== 'pdf_storage_path');

    for (const s of otherSettings) {
      await updateSetting.mutateAsync({ key: s.key, value: drafts[s.key] ?? '' });
    }

    if (pdfSetting) {
      setMigrating(true);
      setMigrationResult(null);
      try {
        const result = await migratePDFStorage(drafts['pdf_storage_path'] ?? '');
        setMigrationResult(result);
        // Refresh settings to reflect the new DB value
        updateSetting.reset();
      } catch (err) {
        setMigrationResult({
          old_path: '',
          new_path: drafts['pdf_storage_path'] ?? '',
          files_moved: 0,
          directories_moved: 0,
          errors: [err instanceof Error ? err.message : 'Migration failed'],
        });
      } finally {
        setMigrating(false);
      }
    }

    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (isLoading) {
    return (
      <div className="flex-1">
        <PageHeader title="Settings" />
        <div className="p-6 text-gray-500">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1">
      <PageHeader title="Settings" />
      <div className="p-6 max-w-xl space-y-6">
        {settings?.map((s) => (
          <div key={s.key}>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {s.key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
            </label>
            {s.description && (
              <p className="text-xs text-gray-500 mb-1">{s.description}</p>
            )}
            {s.key === 'pdf_storage_path' ? (
              <div className="flex gap-2">
                <input
                  type="text"
                  className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  value={drafts[s.key] ?? ''}
                  onChange={(e) => setDrafts((d) => ({ ...d, [s.key]: e.target.value }))}
                  placeholder="Not set"
                />
                <button
                  onClick={() => setBrowseOpen(true)}
                  className="px-3 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
                >
                  Browse
                </button>
              </div>
            ) : s.key === 'ssl_verify' ? (
              <div>
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    checked={(drafts[s.key] ?? 'true') === 'false'}
                    onChange={(e) =>
                      setDrafts((d) => ({ ...d, ssl_verify: e.target.checked ? 'false' : 'true' }))
                    }
                  />
                  <span className="text-sm text-gray-700">Disable SSL certificate verification</span>
                </label>
                <p className="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                  Only enable this if you are behind a corporate proxy that intercepts HTTPS traffic.
                  This exposes API calls to potential interception. The preferred fix is to install
                  your corporate CA certificate into the system trust store.
                </p>
              </div>
            ) : (
              <input
                type="text"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={drafts[s.key] ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, [s.key]: e.target.value }))}
                placeholder="Not set"
              />
            )}
          </div>
        ))}

        {/* Migration result banner */}
        {migrationResult && (
          <div
            className={`rounded px-4 py-3 text-sm ${
              migrationResult.errors.length > 0
                ? 'bg-amber-50 border border-amber-200 text-amber-800'
                : 'bg-green-50 border border-green-200 text-green-800'
            }`}
          >
            <p className="font-medium">
              PDF Storage Migration {migrationResult.errors.length > 0 ? '(with errors)' : 'Complete'}
            </p>
            <p>
              Moved {migrationResult.files_moved} file(s) and {migrationResult.directories_moved} director{migrationResult.directories_moved === 1 ? 'y' : 'ies'} from{' '}
              <code className="text-xs">{migrationResult.old_path}</code> to{' '}
              <code className="text-xs">{migrationResult.new_path}</code>
            </p>
            {migrationResult.errors.map((err, i) => (
              <p key={i} className="text-amber-700 mt-1">Error: {err}</p>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={!hasChanges || updateSetting.isPending || migrating}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {migrating ? 'Migrating PDFs...' : updateSetting.isPending ? 'Saving...' : 'Save'}
          </button>
          {saved && !migrating && <span className="text-sm text-green-600">Saved</span>}
        </div>
      </div>

      {browseOpen && (
        <FolderBrowserDialog
          initialPath={drafts['pdf_storage_path'] ?? ''}
          onSelect={(path) => {
            setDrafts((d) => ({ ...d, pdf_storage_path: path }));
            setBrowseOpen(false);
          }}
          onCancel={() => setBrowseOpen(false)}
        />
      )}
    </div>
  );
}
