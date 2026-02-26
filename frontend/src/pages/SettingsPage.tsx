import { useState, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import PageHeader from '../components/PageHeader';
import FolderBrowserDialog from '../components/FolderBrowserDialog';
import { useSettings, useUpdateSetting, useLLMModels } from '../hooks/useSettings';
import { migratePDFStorage } from '../api';
import type { PDFMigrationResult } from '../types';

// Keys managed by the dedicated LLM section — excluded from the generic loop.
const LLM_KEYS = new Set(['llm_provider', 'llm_api_key', 'llm_model_id', 'llm_base_url']);

// ---------------------------------------------------------------------------
// LLM configuration sub-section
// ---------------------------------------------------------------------------

interface LLMConfigSectionProps {
  saveSetting: (key: string, value: string) => Promise<void>;
}

function LLMConfigSection({ saveSetting }: LLMConfigSectionProps) {
  const qc = useQueryClient();
  const { data: settings = [] } = useSettings();

  // Fast key→value map; recomputed on every render (settings changes are infrequent).
  const sm = Object.fromEntries(settings.map((s) => [s.key, s.value]));
  const llmProvider = sm['llm_provider'] ?? '';
  const llmModelId = sm['llm_model_id'] ?? '';

  // Text-input drafts — initialised once from settings, not overwritten by re-fetches.
  const [apiKeyDraft, setApiKeyDraft] = useState('');
  const [baseUrlDraft, setBaseUrlDraft] = useState('');
  const [modelIdDraft, setModelIdDraft] = useState('');
  const initialized = useRef(false);

  useEffect(() => {
    if (!initialized.current && settings.length > 0) {
      setApiKeyDraft(sm['llm_api_key'] ?? '');
      setBaseUrlDraft(sm['llm_base_url'] ?? '');
      setModelIdDraft(sm['llm_model_id'] ?? '');
      initialized.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  // Model list query — enabled only when there is something to authenticate with:
  // an API key for cloud providers, or a base URL for local servers (key optional there).
  const canFetchModels = !!llmProvider && (!!sm['llm_api_key'] || !!sm['llm_base_url']);
  const {
    data: modelsData,
    isLoading: modelsLoading,
    refetch: refetchModels,
  } = useLLMModels(canFetchModels);

  // Test-connection status (auto-clears success messages after 3 s).
  const [testStatus, setTestStatus] = useState<{ ok: boolean; message: string } | null>(null);
  useEffect(() => {
    if (testStatus?.ok) {
      const t = setTimeout(() => setTestStatus(null), 3000);
      return () => clearTimeout(t);
    }
  }, [testStatus]);

  // ---- handlers ----

  const handleProviderChange = async (value: string) => {
    setModelIdDraft('');
    setTestStatus(null);
    await saveSetting('llm_provider', value);
    await saveSetting('llm_model_id', '');
    qc.invalidateQueries({ queryKey: ['llm', 'models'] });
  };

  const handleBaseUrlBlur = async () => {
    await saveSetting('llm_base_url', baseUrlDraft);
    if (llmProvider) {
      qc.invalidateQueries({ queryKey: ['llm', 'models'] });
    }
  };

  const handleModelIdSelect = async (value: string) => {
    await saveSetting('llm_model_id', value);
  };

  const handleModelIdFreeTextBlur = async () => {
    await saveSetting('llm_model_id', modelIdDraft);
  };

  const handleTestConnection = async () => {
    setTestStatus(null);
    const result = await refetchModels();
    if (result.data) {
      if (result.data.error) {
        setTestStatus({ ok: false, message: result.data.error });
      } else {
        const n = result.data.models.length;
        setTestStatus({ ok: true, message: `Connected — ${n} model${n === 1 ? '' : 's'} available` });
      }
    }
  };

  // ---- model picker ----

  function renderModelPicker() {
    const inputCls =
      'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

    if (!llmProvider) {
      return (
        <select disabled className={`${inputCls} bg-gray-50 text-gray-400 cursor-not-allowed`}>
          <option>Select a provider first</option>
        </select>
      );
    }

    if (!canFetchModels) {
      return (
        <select disabled className={`${inputCls} bg-gray-50 text-gray-400 cursor-not-allowed`}>
          <option>Enter an API key above to load models</option>
        </select>
      );
    }

    if (modelsLoading) {
      return (
        <div className="flex items-center gap-2 h-9 px-3 border border-gray-300 rounded bg-gray-50">
          <svg
            className="animate-spin h-4 w-4 text-blue-500 shrink-0"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span className="text-sm text-gray-500">Loading models…</span>
        </div>
      );
    }

    if (modelsData?.error) {
      return (
        <>
          <input
            type="text"
            className={inputCls}
            value={modelIdDraft}
            onChange={(e) => setModelIdDraft(e.target.value)}
            onBlur={handleModelIdFreeTextBlur}
            placeholder="Enter model ID manually"
          />
          <p className="mt-1 text-xs text-red-600">{modelsData.error}</p>
        </>
      );
    }

    if (modelsData && modelsData.models.length === 0) {
      return (
        <>
          <input
            type="text"
            className={inputCls}
            value={modelIdDraft}
            onChange={(e) => setModelIdDraft(e.target.value)}
            onBlur={handleModelIdFreeTextBlur}
            placeholder="Enter model ID manually"
          />
          <p className="mt-1 text-xs text-gray-500">
            No models returned — enter model ID manually
          </p>
        </>
      );
    }

    // Success: populated dropdown.
    return (
      <select className={inputCls} value={llmModelId} onChange={(e) => handleModelIdSelect(e.target.value)}>
        <option value="">Select a model…</option>
        {modelsData?.models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    );
  }

  // ---- render ----

  return (
    <div className="border-t border-gray-200 pt-6 space-y-5">
      <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
        LLM Configuration
      </h2>

      {/* Provider */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
        <select
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={llmProvider}
          onChange={(e) => handleProviderChange(e.target.value)}
        >
          <option value="">Not configured</option>
          <option value="anthropic">anthropic</option>
          <option value="openai">openai</option>
        </select>
      </div>

      {/* API Key */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          API Key{' '}
          <span className="font-normal text-gray-500">(Optional for local servers)</span>
        </label>
        <input
          type="password"
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={apiKeyDraft}
          onChange={(e) => setApiKeyDraft(e.target.value)}
          onBlur={() => saveSetting('llm_api_key', apiKeyDraft)}
          placeholder="sk-…"
          autoComplete="off"
        />
      </div>

      {/* Base URL */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
        <input
          type="text"
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={baseUrlDraft}
          onChange={(e) => setBaseUrlDraft(e.target.value)}
          onBlur={handleBaseUrlBlur}
          placeholder="http://localhost:11434/v1"
        />
        <p className="mt-1 text-xs text-gray-500">
          Optional. Overrides the provider's default endpoint. Use this for local inference
          servers (Ollama, vLLM, LM Studio). Leave blank for Anthropic or OpenAI cloud.
        </p>
      </div>

      {/* Model */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
        {renderModelPicker()}
      </div>

      {/* PDF vision note — Anthropic only */}
      {llmProvider === 'anthropic' && (
        <p className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-2">
          PDF vision input supported for Anthropic models
        </p>
      )}

      {/* Test connection */}
      <div className="flex items-center gap-3">
        <button
          disabled={!canFetchModels}
          onClick={handleTestConnection}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Test connection
        </button>
        {testStatus && (
          <span className={`text-sm ${testStatus.ok ? 'text-green-600' : 'text-red-600'}`}>
            {testStatus.message}
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main settings page
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { data: settings, isLoading } = useSettings();
  const updateSetting = useUpdateSetting();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const [migrationResult, setMigrationResult] = useState<PDFMigrationResult | null>(null);

  // Sync drafts when settings load (excludes LLM keys — managed by LLMConfigSection).
  useEffect(() => {
    if (settings) {
      const initial: Record<string, string> = {};
      for (const s of settings) {
        if (!LLM_KEYS.has(s.key)) initial[s.key] = s.value;
      }
      setDrafts(initial);
    }
  }, [settings]);

  // Only track changes in generic (non-LLM) settings.
  const hasChanges = settings?.some((s) => !LLM_KEYS.has(s.key) && drafts[s.key] !== s.value) ?? false;

  const handleSave = async () => {
    if (!settings) return;
    const changed = settings.filter(
      (s) => !LLM_KEYS.has(s.key) && drafts[s.key] !== s.value,
    );

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

  // Passed down to LLMConfigSection for individual saves.
  const saveSetting = async (key: string, value: string) => {
    await updateSetting.mutateAsync({ key, value });
  };

  // Generic settings to display in the main loop (LLM keys excluded).
  const genericSettings = (settings ?? []).filter((s) => !LLM_KEYS.has(s.key));

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
        {genericSettings.map((s) => (
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
              Moved {migrationResult.files_moved} file(s) and{' '}
              {migrationResult.directories_moved} director
              {migrationResult.directories_moved === 1 ? 'y' : 'ies'} from{' '}
              <code className="text-xs">{migrationResult.old_path}</code> to{' '}
              <code className="text-xs">{migrationResult.new_path}</code>
            </p>
            {migrationResult.errors.map((err, i) => (
              <p key={i} className="text-amber-700 mt-1">
                Error: {err}
              </p>
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

        {/* LLM configuration — per-field save (no Save button) */}
        <LLMConfigSection saveSetting={saveSetting} />
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
