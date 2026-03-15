import { useState, useEffect } from 'react';
import { useParams, useNavigate, useSearchParams, Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import ExtractionRunView from '../components/ExtractionRunView';
import {
  useExtractionSchemas,
  useExtractionSchema,
  useCreateExtractionSchema,
  useUpdateExtractionSchema,
  useDeleteExtractionSchema,
  useCreateExtractionColumn,
  useUpdateExtractionColumn,
  useDeleteExtractionColumn,
  useReorderExtractionColumns,
} from '../hooks/useExtraction';
import type { ExtractionColumn, ExtractionSchema } from '../types';

// ---------------------------------------------------------------------------
// Column form modal (create or edit a column)
// ---------------------------------------------------------------------------

interface ColumnFormModalProps {
  schemaId: number;
  initial?: ExtractionColumn;
  nextSortOrder: number;
  onClose: () => void;
}

function ColumnFormModal({ schemaId, initial, nextSortOrder, onClose }: ColumnFormModalProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [prompt, setPrompt] = useState(initial?.prompt ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [allowedValues, setAllowedValues] = useState<string[]>(initial?.allowed_values ?? []);
  const [tagInput, setTagInput] = useState('');

  const createCol = useCreateExtractionColumn(schemaId);
  const updateCol = useUpdateExtractionColumn(schemaId);
  const isPending = createCol.isPending || updateCol.isPending;

  const handleSave = async () => {
    if (!name.trim() || !prompt.trim()) return;
    const data = {
      name: name.trim(),
      prompt: prompt.trim(),
      description: description.trim() || null,
      allowed_values: allowedValues.length > 0 ? allowedValues : null,
    };
    if (initial) {
      await updateCol.mutateAsync({ columnId: initial.id, data });
    } else {
      await createCol.mutateAsync({ ...data, sort_order: nextSortOrder });
    }
    onClose();
  };

  const addTag = () => {
    const val = tagInput.trim();
    if (val && !allowedValues.includes(val)) {
      setAllowedValues([...allowedValues, val]);
    }
    setTagInput('');
  };

  const removeTag = (val: string) => setAllowedValues(allowedValues.filter((x) => x !== val));

  const inputCls =
    'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-lg max-w-lg w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-gray-900 mb-4">
          {initial ? 'Edit Column' : 'Add Column'}
        </h3>

        <div className="space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Research question"
              className={inputCls}
              autoFocus
            />
            <p className="mt-1 text-xs text-gray-500">
              Short label — becomes the column header in the extraction table.
            </p>
          </div>

          {/* Prompt */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Prompt <span className="text-red-500">*</span>
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="What question should the LLM answer for this column?"
              className={inputCls}
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="Additional context about what this column measures (optional)"
              className={inputCls}
            />
          </div>

          {/* Allowed values */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Allowed Values
            </label>
            <p className="mb-2 text-xs text-gray-500">
              Type a value and press Enter to add it as a chip. Leave empty for free-text
              responses.
            </p>
            {allowedValues.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {allowedValues.map((v) => (
                  <span
                    key={v}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-blue-100 text-blue-800"
                  >
                    {v}
                    <button
                      onClick={() => removeTag(v)}
                      className="text-blue-500 hover:text-blue-700 leading-none"
                      aria-label={`Remove ${v}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addTag();
                  }
                }}
                placeholder="Add a value…"
                className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={addTag}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Add
              </button>
            </div>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!name.trim() || !prompt.trim() || isPending}
            className="px-4 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isPending ? 'Saving…' : initial ? 'Save Changes' : 'Add Column'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Share button (icon-only, matches ProjectDetailPage)
// ---------------------------------------------------------------------------

function ShareButton() {
  const [copied, setCopied] = useState(false);
  const handleShare = () => {
    navigator.clipboard.writeText(window.location.href).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={handleShare}
      className="h-8 w-8 flex items-center justify-center border border-gray-300 rounded hover:bg-gray-50 text-gray-500"
      title={copied ? 'Link copied!' : 'Copy link'}
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      </svg>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Schema editor (title/description + column management + extract/review)
// ---------------------------------------------------------------------------

interface SchemaEditorProps {
  schemaId: number;
  projectId: number;
  onBack: () => void;
  initialTab?: EditorTab;
}

type EditorTab = 'schema' | 'review';

function SchemaEditor({ schemaId, projectId, onBack, initialTab }: SchemaEditorProps) {
  const navigate = useNavigate();
  const { data: schema, isLoading } = useExtractionSchema(schemaId);

  const [activeTab, setActiveTab] = useState<EditorTab>(initialTab ?? 'schema');

  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const [descDraft, setDescDraft] = useState<string | null>(null);
  const [metaSaved, setMetaSaved] = useState(false);

  const [editColumn, setEditColumn] = useState<ExtractionColumn | null>(null);
  const [addingColumn, setAddingColumn] = useState(false);
  const [deleteColId, setDeleteColId] = useState<number | null>(null);

  const updateSchema = useUpdateExtractionSchema();
  const deleteCol = useDeleteExtractionColumn(schemaId);
  const reorder = useReorderExtractionColumns(schemaId);

  if (isLoading || !schema) {
    return <div className="p-6 text-sm text-gray-400">Loading schema…</div>;
  }

  const sortedColumns = [...schema.columns].sort((a, b) => a.sort_order - b.sort_order);

  const titleValue = titleDraft ?? schema.title;
  const descValue = descDraft ?? (schema.description ?? '');
  const hasMetaChanges =
    (titleDraft !== null && titleDraft !== schema.title) ||
    (descDraft !== null && descDraft !== (schema.description ?? ''));

  const handleSaveMeta = async () => {
    if (!titleValue.trim()) return;
    await updateSchema.mutateAsync({
      schemaId,
      data: {
        title: titleValue.trim(),
        description: descValue.trim() || null,
      },
    });
    setTitleDraft(null);
    setDescDraft(null);
    setMetaSaved(true);
    setTimeout(() => setMetaSaved(false), 2000);
  };

  const handleMoveUp = (idx: number) => {
    if (idx === 0) return;
    const ids = sortedColumns.map((c) => c.id);
    [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
    reorder.mutate(ids);
  };

  const handleMoveDown = (idx: number) => {
    if (idx === sortedColumns.length - 1) return;
    const ids = sortedColumns.map((c) => c.id);
    [ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]];
    reorder.mutate(ids);
  };

  const nextSortOrder = sortedColumns.length;

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0">
      <PageHeader
        leftContent={
          <nav className="flex items-center gap-2">
            <Link
              to={`/projects/${projectId}`}
              className="text-sm text-gray-400 hover:text-gray-600"
            >
              ← Project
            </Link>
            <span className="text-sm text-gray-300">/</span>
            <button
              onClick={onBack}
              className="text-sm text-gray-400 hover:text-gray-600"
            >
              Schemas
            </button>
            <span className="text-sm text-gray-300">/</span>
            <h1 className="text-sm font-semibold text-gray-900">{schema.title}</h1>
          </nav>
        }
      >
        <button
          onClick={() => navigate(`/projects/${projectId}/discuss?focus=schema&schemaId=${schemaId}`)}
          className="px-3 py-1.5 text-sm border border-indigo-300 text-indigo-700 rounded hover:bg-indigo-50"
        >
          Refine with AI
        </button>
        <ShareButton />
      </PageHeader>

      {/* Tab bar */}
      <div className="shrink-0 border-b border-gray-200 px-6 flex gap-0">
        {(['schema', 'review'] as EditorTab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            {tab === 'schema' ? 'Schema' : 'Extract & Review'}
          </button>
        ))}
      </div>

      {activeTab === 'schema' ? (
        <div className="p-6 max-w-2xl space-y-6 overflow-y-auto">
          {/* Metadata */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Title <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={titleValue}
                onChange={(e) => setTitleDraft(e.target.value)}
                placeholder="e.g. Study Design Analysis"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
              <textarea
                value={descValue}
                onChange={(e) => setDescDraft(e.target.value)}
                rows={3}
                placeholder="Describe the goal of this extraction schema (optional — sent to the LLM as context)"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-gray-500">
                This description is included in the LLM prompt as additional context.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={handleSaveMeta}
                disabled={!titleValue.trim() || !hasMetaChanges || updateSchema.isPending}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {updateSchema.isPending ? 'Saving…' : 'Save'}
              </button>
              {metaSaved && <span className="text-sm text-green-600">Saved</span>}
            </div>
          </div>

          {/* Columns */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
                Columns
                {sortedColumns.length > 0 && (
                  <span className="ml-1 font-normal text-gray-400">
                    ({sortedColumns.length})
                  </span>
                )}
              </h2>
              <button
                onClick={() => setAddingColumn(true)}
                className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
              >
                + Add Column
              </button>
            </div>

            {sortedColumns.length === 0 ? (
              <div className="border border-dashed border-gray-300 rounded-lg p-8 text-center text-sm text-gray-400">
                No columns yet. Add a column to define what the LLM should extract.
              </div>
            ) : (
              <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
                {sortedColumns.map((col, idx) => (
                  <div key={col.id} className="flex items-start gap-3 px-4 py-3">
                    {/* Reorder buttons */}
                    <div className="flex flex-col gap-0.5 shrink-0 pt-0.5">
                      <button
                        onClick={() => handleMoveUp(idx)}
                        disabled={idx === 0 || reorder.isPending}
                        className="p-0.5 text-gray-400 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label="Move up"
                      >
                        ▲
                      </button>
                      <button
                        onClick={() => handleMoveDown(idx)}
                        disabled={idx === sortedColumns.length - 1 || reorder.isPending}
                        className="p-0.5 text-gray-400 hover:text-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label="Move down"
                      >
                        ▼
                      </button>
                    </div>

                    {/* Column info */}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900">{col.name}</p>
                      <p className="text-xs text-gray-500 truncate mt-0.5">
                        {col.prompt.length > 80 ? col.prompt.slice(0, 80) + '…' : col.prompt}
                      </p>
                      {col.allowed_values && col.allowed_values.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {col.allowed_values.map((v) => (
                            <span
                              key={v}
                              className="px-1.5 py-0.5 text-xs rounded bg-gray-100 text-gray-600"
                            >
                              {v}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={() => setEditColumn(col)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => setDeleteColId(col.id)}
                        className="text-xs text-red-500 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <ExtractionRunView key={schema.id} schema={schema} />
      )}

      {/* Column form modal */}
      {(addingColumn || editColumn) && (
        <ColumnFormModal
          schemaId={schemaId}
          initial={editColumn ?? undefined}
          nextSortOrder={nextSortOrder}
          onClose={() => {
            setAddingColumn(false);
            setEditColumn(null);
          }}
        />
      )}

      {/* Delete column confirm */}
      {deleteColId != null && (
        <ConfirmDialog
          title="Delete column?"
          message="This will permanently delete the column and all its extraction results."
          confirmLabel="Delete"
          onConfirm={() => {
            deleteCol.mutate(deleteColId);
            setDeleteColId(null);
          }}
          onCancel={() => setDeleteColId(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// New schema form
// ---------------------------------------------------------------------------

interface NewSchemaFormProps {
  projectId: number;
  onCreated: (schemaId: number) => void;
  onCancel: () => void;
}

function NewSchemaForm({ projectId, onCreated, onCancel }: NewSchemaFormProps) {
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const createSchema = useCreateExtractionSchema();

  const handleCreate = async () => {
    if (!title.trim()) return;
    const schema = await createSchema.mutateAsync({
      title: title.trim(),
      description: description.trim() || null,
      project_id: projectId,
    });
    onCreated(schema.id);
  };

  const inputCls =
    'w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500';

  return (
    <div className="flex-1 flex flex-col min-w-0">
      <PageHeader title="New Extraction Schema">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
        >
          Cancel
        </button>
      </PageHeader>

      <div className="p-6 max-w-2xl space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Title <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Study Design Analysis"
            className={inputCls}
            autoFocus
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Describe the goal of this extraction schema (optional — sent to the LLM as context)"
            className={inputCls}
          />
          <p className="mt-1 text-xs text-gray-500">
            This description is included in the LLM prompt as additional context.
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleCreate}
            disabled={!title.trim() || createSchema.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {createSchema.isPending ? 'Creating…' : 'Create Schema'}
          </button>
          <button
            onClick={() => navigate(`/projects/${projectId}/discuss?focus=new-schema`)}
            className="px-3 py-2 text-sm border border-indigo-300 text-indigo-700 rounded hover:bg-indigo-50"
          >
            Design with AI
          </button>
          <button
            onClick={onCancel}
            className="px-3 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
        {createSchema.error && (
          <p className="text-sm text-red-600">
            {createSchema.error instanceof Error
              ? createSchema.error.message
              : 'Failed to create schema'}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Schema list card
// ---------------------------------------------------------------------------

interface SchemaCardProps {
  schema: ExtractionSchema;
  onEdit: () => void;
  onDelete: () => void;
  isPinned: boolean;
  onTogglePin: () => void;
}

function SchemaCard({ schema, onEdit, onDelete, isPinned, onTogglePin }: SchemaCardProps) {
  const created = new Date(schema.created_at).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });

  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900">{schema.title}</h3>
          {schema.description && (
            <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{schema.description}</p>
          )}
          <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
            <span>
              {schema.columns.length} column{schema.columns.length !== 1 ? 's' : ''}
            </span>
            <span>Created {created}</span>
          </div>
          {schema.columns.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {schema.columns
                .slice()
                .sort((a, b) => a.sort_order - b.sort_order)
                .map((col) => (
                  <span
                    key={col.id}
                    className="px-2 py-0.5 text-xs rounded bg-gray-100 text-gray-600"
                  >
                    {col.name}
                  </span>
                ))}
            </div>
          )}
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={onTogglePin}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            {isPinned ? 'Unpin' : 'Pin to tabs'}
          </button>
          <button
            onClick={onEdit}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="px-3 py-1.5 text-sm text-red-600 border border-red-200 rounded hover:bg-red-50"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type View = { kind: 'list' } | { kind: 'new' } | { kind: 'editor'; schemaId: number };

function loadPromotedSchemaIds(projectId: number): number[] {
  try {
    const raw = localStorage.getItem(`litexplorer:project:${projectId}:promotedSchemas`);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export default function ExtractionSchemasPage() {
  const { projectId: pid } = useParams<{ projectId: string }>();
  const projectId = Number(pid);

  // Support ?schema={id} URL param to auto-open a specific schema editor
  const [searchParams, setSearchParams] = useSearchParams();
  const schemaIdFromUrl = searchParams.get('schema');

  const [view, setView] = useState<View>(() => {
    if (schemaIdFromUrl) {
      const id = parseInt(schemaIdFromUrl, 10);
      if (!isNaN(id)) return { kind: 'editor', schemaId: id };
    }
    return { kind: 'list' };
  });
  const [deleteSchemaId, setDeleteSchemaId] = useState<number | null>(null);

  // Promoted schema IDs — shared localStorage with ProjectDetailPage
  const [promotedSchemaIds, setPromotedSchemaIds] = useState<number[]>(
    () => loadPromotedSchemaIds(projectId),
  );
  const togglePromoted = (schemaId: number) => {
    setPromotedSchemaIds((prev) => {
      const next = prev.includes(schemaId)
        ? prev.filter((id) => id !== schemaId)
        : [...prev, schemaId];
      localStorage.setItem(
        `litexplorer:project:${projectId}:promotedSchemas`,
        JSON.stringify(next),
      );
      return next;
    });
  };

  // Sync view to URL params (replace, not push)
  useEffect(() => {
    if (view.kind === 'editor') {
      setSearchParams({ schema: String(view.schemaId) }, { replace: true });
    } else if (view.kind === 'list') {
      setSearchParams({}, { replace: true });
    }
  }, [view, setSearchParams]);

  const { data: schemas, isLoading } = useExtractionSchemas(projectId);
  const deleteSchema = useDeleteExtractionSchema();

  if (view.kind === 'new') {
    return (
      <NewSchemaForm
        projectId={projectId}
        onCreated={(id) => setView({ kind: 'editor', schemaId: id })}
        onCancel={() => setView({ kind: 'list' })}
      />
    );
  }

  if (view.kind === 'editor') {
    const viewParam = searchParams.get('view');
    return (
      <SchemaEditor
        schemaId={view.schemaId}
        projectId={projectId}
        onBack={() => setView({ kind: 'list' })}
        initialTab={viewParam === 'review' ? 'review' : undefined}
      />
    );
  }

  // List view
  return (
    <div className="flex-1">
      <PageHeader
        leftContent={
          <nav className="flex items-center gap-2">
            <Link
              to={`/projects/${projectId}`}
              className="text-sm text-gray-400 hover:text-gray-600"
            >
              ← Project
            </Link>
            <span className="text-sm text-gray-300">/</span>
            <h1 className="text-xl font-semibold text-gray-900">Extraction Schemas</h1>
          </nav>
        }
      >
        <button
          onClick={() => setView({ kind: 'new' })}
          className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
        >
          New Table Schema
        </button>
        <ShareButton />
      </PageHeader>

      <div className="p-6 max-w-2xl space-y-3">
        {isLoading && <p className="text-sm text-gray-400">Loading schemas…</p>}

        {!isLoading && schemas?.length === 0 && (
          <div className="border border-dashed border-gray-300 rounded-lg p-12 text-center">
            <p className="text-sm text-gray-500 mb-3">No extraction schemas yet.</p>
            <p className="text-xs text-gray-400 mb-4">
              Create a schema to define what structured information the LLM should extract from
              papers in this project.
            </p>
            <button
              onClick={() => setView({ kind: 'new' })}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700"
            >
              New Table Schema
            </button>
          </div>
        )}

        {schemas?.map((schema) => (
          <SchemaCard
            key={schema.id}
            schema={schema}
            onEdit={() => setView({ kind: 'editor', schemaId: schema.id })}
            onDelete={() => setDeleteSchemaId(schema.id)}
            isPinned={promotedSchemaIds.includes(schema.id)}
            onTogglePin={() => togglePromoted(schema.id)}
          />
        ))}
      </div>

      {deleteSchemaId != null && (
        <ConfirmDialog
          title="Delete schema?"
          message="This will permanently delete the schema and all its columns. Extraction notes generated from this schema will remain."
          confirmLabel="Delete"
          onConfirm={() => {
            deleteSchema.mutate(deleteSchemaId);
            setDeleteSchemaId(null);
          }}
          onCancel={() => setDeleteSchemaId(null)}
        />
      )}
    </div>
  );
}
