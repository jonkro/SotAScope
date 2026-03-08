import { useState } from 'react';
import type { ColumnProposal } from '../utils/proposalParser';

// ---------------------------------------------------------------------------
// Sentinel error class — thrown when the user cancels a "new schema" dialog.
// The card checks for this to suppress the error message in that case.
// ---------------------------------------------------------------------------

export class UserCancelledError extends Error {
  constructor() {
    super('cancelled');
    this.name = 'UserCancelledError';
  }
}

// ---------------------------------------------------------------------------
// Small helper: labelled field row
// ---------------------------------------------------------------------------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-0.5">
        {label}
      </p>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card state machine
// ---------------------------------------------------------------------------

type CardState = 'pending' | 'editing' | 'saving' | 'accepted' | 'rejected';

// ---------------------------------------------------------------------------
// ColumnProposalCard
// ---------------------------------------------------------------------------

export interface ColumnProposalCardProps {
  /** Initial proposal values from the LLM response. */
  proposal: ColumnProposal;
  /**
   * Called when the user clicks Accept. Must return a Promise:
   * - Resolves → card transitions to 'accepted'.
   * - Rejects with UserCancelledError → card returns to 'pending' silently.
   * - Rejects with other error → card returns to 'pending' with an error message.
   */
  onAccept: (proposal: ColumnProposal) => Promise<void>;
  /**
   * Called when the user clicks Reject (purely visual — no API call).
   */
  onReject: () => void;
}

export function ColumnProposalCard({ proposal, onAccept, onReject }: ColumnProposalCardProps) {
  const [cardState, setCardState] = useState<CardState>('pending');
  const [error, setError] = useState('');

  // Editable copies of the proposal fields
  const [name, setName] = useState(proposal.name);
  const [prompt, setPrompt] = useState(proposal.prompt);
  const [description, setDescription] = useState(proposal.description);
  const [allowedValuesStr, setAllowedValuesStr] = useState(
    proposal.allowed_values?.join(', ') ?? '',
  );

  /** Build the current proposal from local state for the onAccept callback. */
  const buildCurrentProposal = (): ColumnProposal => ({
    name: name.trim(),
    prompt: prompt.trim(),
    description: description.trim(),
    allowed_values: allowedValuesStr.trim()
      ? allowedValuesStr
          .split(',')
          .map((v) => v.trim())
          .filter(Boolean)
      : null,
  });

  // ------------------------------------------------------------------
  // Accepted — compact green confirmation
  // ------------------------------------------------------------------
  if (cardState === 'accepted') {
    return (
      <div className="border border-green-200 rounded-lg px-3 py-2 bg-green-50 flex items-center gap-2 text-sm">
        <span className="text-green-600 text-base leading-none">✓</span>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-green-800 truncate">{name}</p>
          <p className="text-[10px] text-green-600">Column added to schema</p>
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Rejected — collapsed, dimmed
  // ------------------------------------------------------------------
  if (cardState === 'rejected') {
    return (
      <div className="border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-xs text-gray-400 italic opacity-50">
        Column proposal &ldquo;{name}&rdquo; rejected
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Saving — spinner in place of the card body
  // ------------------------------------------------------------------
  if (cardState === 'saving') {
    return (
      <div className="border border-indigo-200 rounded-lg overflow-hidden shadow-sm opacity-70">
        <div className="px-3 py-2 bg-indigo-100 border-b border-indigo-200 flex items-center gap-2">
          <span className="inline-block w-3.5 h-3.5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-xs text-indigo-700">Adding column…</span>
        </div>
        <div className="px-3 py-2 bg-indigo-50">
          <p className="text-xs font-semibold text-gray-700 truncate">{name}</p>
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Pending / Editing
  // ------------------------------------------------------------------
  const isEditing = cardState === 'editing';

  const handleAccept = async () => {
    setError('');
    setCardState('saving');
    try {
      await onAccept(buildCurrentProposal());
      setCardState('accepted');
    } catch (e) {
      // Suppress error display for user-initiated cancellation
      if (!(e instanceof UserCancelledError)) {
        let msg = e instanceof Error ? e.message : String(e);
        // Try to extract a JSON `detail` field from API error bodies
        try {
          const parsed = JSON.parse(msg) as { detail?: string };
          msg = parsed.detail ?? msg;
        } catch {
          // not JSON
        }
        setError(msg);
      }
      setCardState('pending');
    }
  };

  const handleReject = () => {
    onReject();
    setCardState('rejected');
  };

  const toggleEdit = () => setCardState(isEditing ? 'pending' : 'editing');

  return (
    <div className="border border-indigo-200 rounded-lg overflow-hidden shadow-sm">
      {/* Card header */}
      <div className="px-3 py-2 bg-indigo-100 border-b border-indigo-200 flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-indigo-800 tracking-wide">
          Column proposal
        </span>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={toggleEdit}
            className="px-2 py-0.5 text-[10px] border border-indigo-300 rounded text-indigo-700 hover:bg-indigo-200 transition-colors"
          >
            {isEditing ? 'Done' : 'Edit'}
          </button>
          <button
            onClick={handleAccept}
            disabled={!name.trim() || !prompt.trim()}
            className="px-2 py-0.5 text-[10px] text-white bg-green-600 rounded hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Accept
          </button>
          <button
            onClick={handleReject}
            className="px-2 py-0.5 text-[10px] border border-gray-300 rounded text-gray-500 hover:bg-gray-100 transition-colors"
          >
            Reject
          </button>
        </div>
      </div>

      {/* Inline error (API rejection) */}
      {error && (
        <div className="px-3 py-1.5 bg-red-50 border-b border-red-200 text-[10px] text-red-700">
          {error}
        </div>
      )}

      {/* Card body */}
      <div className="px-3 py-2.5 bg-indigo-50 space-y-2.5">
        <Field label="Name">
          {isEditing ? (
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Short column name"
              className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
          ) : (
            <p className="text-xs font-semibold text-gray-800">
              {name || <em className="text-gray-400">—</em>}
            </p>
          )}
        </Field>

        <Field label="Prompt">
          {isEditing ? (
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="The exact question posed to the LLM for each paper"
              rows={3}
              className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400 resize-y"
            />
          ) : (
            <p className="text-xs text-gray-700 whitespace-pre-wrap">{prompt}</p>
          )}
        </Field>

        {(description || isEditing) && (
          <Field label="Description">
            {isEditing ? (
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What this column captures and why it is useful"
                className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            ) : (
              <p className="text-xs text-gray-600">{description}</p>
            )}
          </Field>
        )}

        <Field label="Allowed values">
          {isEditing ? (
            <input
              type="text"
              value={allowedValuesStr}
              onChange={(e) => setAllowedValuesStr(e.target.value)}
              placeholder="Comma-separated (leave empty for free-form text)"
              className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
          ) : (
            <p className="text-xs text-gray-600">
              {allowedValuesStr.trim() ? (
                allowedValuesStr
                  .split(',')
                  .map((v) => v.trim())
                  .filter(Boolean)
                  .map((v, i) => (
                    <span
                      key={i}
                      className="inline-block bg-indigo-100 text-indigo-700 rounded px-1.5 py-0.5 text-[10px] mr-1 mb-1"
                    >
                      {v}
                    </span>
                  ))
              ) : (
                <em className="text-gray-400">Free-form text</em>
              )}
            </p>
          )}
        </Field>
      </div>
    </div>
  );
}
