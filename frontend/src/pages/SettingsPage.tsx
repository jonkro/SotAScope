import { useState, useEffect } from 'react';
import PageHeader from '../components/PageHeader';
import { useSettings, useUpdateSetting } from '../hooks/useSettings';

export default function SettingsPage() {
  const { data: settings, isLoading } = useSettings();
  const updateSetting = useUpdateSetting();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

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
    for (const s of changed) {
      await updateSetting.mutateAsync({ key: s.key, value: drafts[s.key] ?? '' });
    }
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (isLoading) {
    return (
      <div className="flex-1">
        <PageHeader title="Settings" />
        <div className="p-6 text-gray-500">Loading…</div>
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
            <input
              type="text"
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={drafts[s.key] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, [s.key]: e.target.value }))}
              placeholder="Not set"
            />
          </div>
        ))}

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={!hasChanges || updateSetting.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {updateSetting.isPending ? 'Saving…' : 'Save'}
          </button>
          {saved && <span className="text-sm text-green-600">Saved</span>}
        </div>
      </div>
    </div>
  );
}
