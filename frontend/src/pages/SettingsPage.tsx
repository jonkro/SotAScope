import { useState, useEffect, useRef } from 'react';
import { useQueryClient, useQuery } from '@tanstack/react-query';
import PageHeader from '../components/PageHeader';
import FolderBrowserDialog from '../components/FolderBrowserDialog';
import { useSettings, useUpdateSetting, useLLMModels } from '../hooks/useSettings';
import { migratePDFStorage, fetchGrobidStatus, startGrobid, backfillVenues } from '../api';
import type { PDFMigrationResult } from '../types';

// Keys managed by dedicated sub-sections — excluded from the generic save loop.
const LLM_KEYS = new Set([
  'llm_provider',
  'llm_api_key',
  'llm_model_id',
  'llm_base_url',
  'llm_system_prompt_prefix',
]);
const SECTION_KEYS = new Set([...LLM_KEYS, 'grobid_url']);

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
  const [systemPromptDraft, setSystemPromptDraft] = useState('');
  const initialized = useRef(false);

  useEffect(() => {
    if (!initialized.current && settings.length > 0) {
      setApiKeyDraft(sm['llm_api_key'] ?? '');
      setBaseUrlDraft(sm['llm_base_url'] ?? '');
      setModelIdDraft(sm['llm_model_id'] ?? '');
      setSystemPromptDraft(sm['llm_system_prompt_prefix'] ?? '');
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

  const systemPromptDescription = settings.find((s) => s.key === 'llm_system_prompt_prefix')?.description;

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
          <option value="openai">openai-compatible</option>
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
        <p className="mt-0.5 text-xs text-gray-400">
          For Ollama, use <code>http://host:11434/v1</code>
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

      {/* LLM System Prompt Prefix */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          LLM System Prompt Prefix
        </label>
        {systemPromptDescription && (
          <p className="text-xs text-gray-500 mb-1">{systemPromptDescription}</p>
        )}
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          rows={3}
          value={systemPromptDraft}
          onChange={(e) => setSystemPromptDraft(e.target.value)}
          onBlur={() => saveSetting('llm_system_prompt_prefix', systemPromptDraft)}
          placeholder="Not set"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GROBID configuration sub-section
// ---------------------------------------------------------------------------

function GrobidConfigSection({ saveSetting }: { saveSetting: (key: string, value: string) => Promise<void> }) {
  const { data: settings = [] } = useSettings();
  const sm = Object.fromEntries(settings.map((s) => [s.key, s.value]));

  const [urlDraft, setUrlDraft] = useState('');
  const initialized = useRef(false);

  useEffect(() => {
    if (!initialized.current && settings.length > 0) {
      setUrlDraft(sm['grobid_url'] ?? '');
      initialized.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  // ok: true = green, false = red, 'pending' = blue (starting up)
  const [testStatus, setTestStatus] = useState<{ ok: boolean | 'pending'; message: string } | null>(null);
  // Track last known availability to decide whether to show the Start button.
  const [lastAvailable, setLastAvailable] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);

  // Guard against state updates after the component unmounts (user navigated away).
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (testStatus?.ok === true) {
      const t = setTimeout(() => setTestStatus(null), 3000);
      return () => clearTimeout(t);
    }
  }, [testStatus]);

  const { refetch: refetchStatus } = useQuery({
    queryKey: ['grobid', 'status'],
    queryFn: fetchGrobidStatus,
    enabled: false,
  });

  const handleUrlBlur = async () => {
    await saveSetting('grobid_url', urlDraft);
  };

  const runTestConnection = async () => {
    setTestStatus(null);
    const result = await refetchStatus();
    const available = result.data?.available ?? false;
    setLastAvailable(available);
    if (available) {
      setTestStatus({ ok: true, message: 'Connected' });
    } else {
      setTestStatus({ ok: false, message: 'Not available' });
    }
  };

  const handleTestConnection = async () => {
    await runTestConnection();
  };

  const handleStart = async () => {
    setStarting(true);
    setTestStatus({ ok: 'pending', message: 'Starting GROBID…' });
    try {
      const result = await startGrobid();
      if (!result.success) {
        if (mountedRef.current) {
          setTestStatus({ ok: false, message: result.message });
        }
        return;
      }
      // Poll every 3 s, up to 10 attempts (30 s total).
      for (let i = 0; i < 10; i++) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        if (!mountedRef.current) return; // user navigated away — GROBID still starts on the server
        const statusResult = await refetchStatus();
        if (!mountedRef.current) return;
        if (statusResult.data?.available) {
          setLastAvailable(true);
          setTestStatus({ ok: true, message: 'Connected' });
          return;
        }
      }
      // Still not up after 30 s — leave the Start button visible.
      if (mountedRef.current) {
        setLastAvailable(false);
        setTestStatus({ ok: false, message: 'Not responding yet — try "Test connection" again shortly.' });
      }
    } finally {
      if (mountedRef.current) setStarting(false);
    }
  };

  const grobidUrlSaved = sm['grobid_url'] ?? '';
  // Show Start button only when a URL is configured and the last test showed unavailable.
  const showStartButton = !!grobidUrlSaved && lastAvailable === false;

  const inputCls =
    'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div className="border-t border-gray-200 pt-6 space-y-5">
      <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
        GROBID (Reference Extraction)
      </h2>

      {/* GROBID URL */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">GROBID URL</label>
        <input
          type="text"
          className={inputCls}
          value={urlDraft}
          onChange={(e) => setUrlDraft(e.target.value)}
          onBlur={handleUrlBlur}
          placeholder="http://localhost:8070"
        />
        <p className="mt-1 text-xs text-gray-500">
          GROBID extracts references from PDFs locally. Install with Docker:{' '}
          <code className="bg-gray-100 px-1 py-0.5 rounded text-gray-700">
            docker run -d --name grobid -p 8070:8070 grobid/grobid:0.8.2-crf
          </code>
        </p>
      </div>

      {/* Test connection + Start */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleTestConnection}
          disabled={starting}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Test connection
        </button>
        {showStartButton && (
          <button
            onClick={handleStart}
            disabled={starting}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {starting ? 'Starting…' : 'Start'}
          </button>
        )}
        {testStatus && (
          <span
            className={`text-sm ${
              testStatus.ok === true
                ? 'text-green-600'
                : testStatus.ok === 'pending'
                ? 'text-blue-600'
                : 'text-red-600'
            }`}
          >
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
  const [backfillStatus, setBackfillStatus] = useState<{ loading: boolean; message: string | null }>({
    loading: false,
    message: null,
  });

  // Sync drafts when settings load (excludes keys managed by dedicated sub-sections).
  useEffect(() => {
    if (settings) {
      const initial: Record<string, string> = {};
      for (const s of settings) {
        if (!SECTION_KEYS.has(s.key)) initial[s.key] = s.value;
      }
      setDrafts(initial);
    }
  }, [settings]);

  // Only track changes in generic settings.
  const hasChanges = settings?.some((s) => !SECTION_KEYS.has(s.key) && drafts[s.key] !== s.value) ?? false;

  const handleSave = async () => {
    if (!settings) return;
    const changed = settings.filter(
      (s) => !SECTION_KEYS.has(s.key) && drafts[s.key] !== s.value,
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

  const handleBackfillVenues = async () => {
    setBackfillStatus({ loading: true, message: null });
    try {
      const result = await backfillVenues();
      setBackfillStatus({ loading: false, message: result.message });
    } catch (err) {
      setBackfillStatus({
        loading: false,
        message: err instanceof Error ? err.message : 'Backfill failed',
      });
    }
  };

  // Passed down to sub-sections for individual saves.
  const saveSetting = async (key: string, value: string) => {
    await updateSetting.mutateAsync({ key, value });
  };

  const sm = Object.fromEntries((settings ?? []).map((s) => [s.key, s.value]));

  const inputCls =
    'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

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

        {/* ── Section 1: Local Storage ── */}
        <div className="space-y-5">
          <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
            Local Storage
          </h2>

          {/* PDF Storage Path */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              PDF Storage Path
            </label>
            {settings?.find((s) => s.key === 'pdf_storage_path')?.description && (
              <p className="text-xs text-gray-500 mb-1">
                {settings.find((s) => s.key === 'pdf_storage_path')!.description}
              </p>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={drafts['pdf_storage_path'] ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, pdf_storage_path: e.target.value }))}
                placeholder="Not set"
              />
              <button
                onClick={() => setBrowseOpen(true)}
                className="px-3 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Browse
              </button>
            </div>
          </div>
        </div>

        {/* ── Section 2: External Data Sources ── */}
        <div className="border-t border-gray-200 pt-6 space-y-5">
          <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
            External Data Sources
          </h2>

          {/* API Contact Email */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              API Contact Email
            </label>
            {settings?.find((s) => s.key === 'api_contact_email')?.description && (
              <p className="text-xs text-gray-500 mb-1">
                {settings.find((s) => s.key === 'api_contact_email')!.description}
              </p>
            )}
            <input
              type="text"
              className={inputCls}
              value={drafts['api_contact_email'] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, api_contact_email: e.target.value }))}
              placeholder="Not set"
            />
          </div>

          {/* S2 API Key */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              S2 API Key
            </label>
            {settings?.find((s) => s.key === 's2_api_key')?.description && (
              <p className="text-xs text-gray-500 mb-1">
                {settings.find((s) => s.key === 's2_api_key')!.description}
              </p>
            )}
            <input
              type="text"
              className={inputCls}
              value={drafts['s2_api_key'] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, s2_api_key: e.target.value }))}
              placeholder="Not set"
            />
          </div>

          {/* SSL Verify */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              SSL Verify
            </label>
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                checked={(drafts['ssl_verify'] ?? sm['ssl_verify'] ?? 'true') === 'false'}
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

          {/* Backfill Venues from OpenAlex Cache */}
          <div>
            <div className="flex items-center gap-3">
              <button
                onClick={handleBackfillVenues}
                disabled={backfillStatus.loading}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {backfillStatus.loading ? 'Backfilling…' : 'Backfill Venues from OpenAlex Cache'}
              </button>
              {backfillStatus.message && (
                <span className="text-sm text-gray-600">{backfillStatus.message}</span>
              )}
            </div>
            <p className="mt-1 text-xs text-gray-500">
              Scans cached OpenAlex data to populate missing venue assignments. No external API
              calls are made.
            </p>
          </div>
        </div>

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

        {/* Save button covers Local Storage + External Data Sources */}
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

        {/* ── Section 3: GROBID ── */}
        <GrobidConfigSection saveSetting={saveSetting} />

        {/* ── Section 4: LLM Configuration ── */}
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
