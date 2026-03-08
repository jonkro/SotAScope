/**
 * Parses an LLM message string into alternating plain-text and column-proposal
 * segments. Mirrors the multi-strategy parser in
 * litexplorer/services/schema_discussion.py.
 *
 * Strategies tried in order:
 * 1. ```column-proposal fenced blocks (canonical format)
 * 2. Generic code fences (```json, bare ```, etc.) with proposal fields
 * 3. Bare JSON objects/arrays embedded in prose
 *
 * Pre-processing: <think>/<thinking>/<reasoning>/<scratchpad> tags are stripped.
 */

export interface ColumnProposal {
  name: string;
  prompt: string;
  description: string;
  allowed_values: string[] | null;
}

export type MessageSegment =
  | { type: 'text'; content: string }
  | { type: 'proposal'; proposal: ColumnProposal };

// ---------------------------------------------------------------------------
// Pre-processing helpers
// ---------------------------------------------------------------------------

const THINKING_TAGS_RE =
  /<(?:think|thinking|reasoning|scratchpad)>[\s\S]*?<\/(?:think|thinking|reasoning|scratchpad)>/gi;

function stripThinkingTags(text: string): string {
  return text.replace(THINKING_TAGS_RE, '').trim();
}

// ---------------------------------------------------------------------------
// Field-name normalization (mirrors Python _FIELD_ALIASES)
// ---------------------------------------------------------------------------

const FIELD_ALIASES: Record<string, string> = {
  // name
  name: 'name', title: 'name', column_name: 'name', column: 'name',
  // prompt
  prompt: 'prompt', question: 'prompt', extraction_prompt: 'prompt',
  query: 'prompt', llm_prompt: 'prompt',
  // description
  description: 'description', desc: 'description',
  explanation: 'description', details: 'description',
  // allowed_values
  allowed_values: 'allowed_values', values: 'allowed_values',
  options: 'allowed_values', choices: 'allowed_values',
  valid_values: 'allowed_values', allowed: 'allowed_values',
};

const NULL_LIKE = new Set([
  'null', 'none', 'n/a', 'na', 'free-form', 'freeform', 'free form', '',
]);

function normalizeFieldName(raw: string): string | undefined {
  return FIELD_ALIASES[raw.toLowerCase().trim().replace(/\s+/g, '_')];
}

function normalizeAllowedValues(value: unknown): string[] | null {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) {
    const items = value.map(String);
    return items.length > 0 ? items : null;
  }
  if (typeof value === 'string') {
    const s = value.trim();
    if (NULL_LIKE.has(s.toLowerCase())) return null;
    const parts = s.split(',').map((p) => p.trim()).filter(Boolean);
    return parts.length > 0 ? parts : null;
  }
  return null;
}

function looksLikeProposal(obj: Record<string, unknown>): boolean {
  const canonicalKeys = Object.keys(obj).map(normalizeFieldName).filter(Boolean);
  return canonicalKeys.includes('name') && canonicalKeys.includes('prompt');
}

function extractProposalFromObj(obj: Record<string, unknown>): ColumnProposal | null {
  const normalized: Record<string, unknown> = {};
  for (const [rawKey, rawVal] of Object.entries(obj)) {
    const canonical = normalizeFieldName(rawKey);
    if (canonical && !(canonical in normalized)) {
      normalized[canonical] = rawVal;
    }
  }
  const name = String(normalized['name'] ?? '').trim();
  const prompt = String(normalized['prompt'] ?? '').trim();
  if (!name || !prompt) return null;

  return {
    name,
    prompt,
    description: String(normalized['description'] ?? '').trim(),
    allowed_values: normalizeAllowedValues(normalized['allowed_values'] ?? null),
  };
}

function parseProposalsFromJsonText(text: string): ColumnProposal[] {
  let data: unknown;
  try {
    data = JSON.parse(text.trim());
  } catch {
    return [];
  }

  const results: ColumnProposal[] = [];

  if (data !== null && typeof data === 'object' && !Array.isArray(data)) {
    const obj = data as Record<string, unknown>;
    // Check for wrapper keys
    for (const key of ['proposals', 'columns', 'column_proposals']) {
      if (Array.isArray(obj[key])) {
        for (const item of obj[key] as unknown[]) {
          if (item !== null && typeof item === 'object' && !Array.isArray(item)) {
            const itemObj = item as Record<string, unknown>;
            if (looksLikeProposal(itemObj)) {
              const p = extractProposalFromObj(itemObj);
              if (p) results.push(p);
            }
          }
        }
        return results;
      }
    }
    // Try as a single proposal
    if (looksLikeProposal(obj)) {
      const p = extractProposalFromObj(obj);
      if (p) results.push(p);
    }
  } else if (Array.isArray(data)) {
    for (const item of data) {
      if (item !== null && typeof item === 'object' && !Array.isArray(item)) {
        const itemObj = item as Record<string, unknown>;
        if (looksLikeProposal(itemObj)) {
          const p = extractProposalFromObj(itemObj);
          if (p) results.push(p);
        }
      }
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Balanced-bracket extractor
// ---------------------------------------------------------------------------

function findBalanced(
  text: string,
  openChar: string,
  closeChar: string,
  start: number,
): string | null {
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    if (text[i] === openChar) depth++;
    else if (text[i] === closeChar) {
      depth--;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Strategy regexes
// ---------------------------------------------------------------------------

// Strategy 1: ```column-proposal … ``` or end-of-string
const PROPOSAL_FENCE_RE = /```column-proposal\s*\n([\s\S]*?)(?:```|$)/g;

// Strategy 2: any code fence that is NOT column-proposal (json, bare ```, etc.)
// Uses negative lookahead to skip column-proposal blocks.
const GENERIC_FENCE_RE = /```(?!column-proposal)(\w*)\s*\n([\s\S]*?)```/g;

// ---------------------------------------------------------------------------
// Per-strategy parsers
// ---------------------------------------------------------------------------

function strategy1(text: string): MessageSegment[] {
  const segments: MessageSegment[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(PROPOSAL_FENCE_RE)) {
    const start = match.index!;
    const end = start + match[0].length;

    if (start > lastIndex) {
      const slice = text.slice(lastIndex, start);
      if (slice.trim()) segments.push({ type: 'text', content: slice });
    }

    const blockContent = match[1].trim();
    let proposal: ColumnProposal | null = null;
    try {
      const data: unknown = JSON.parse(blockContent);
      if (data !== null && typeof data === 'object' && !Array.isArray(data)) {
        proposal = extractProposalFromObj(data as Record<string, unknown>);
      }
    } catch {
      // Invalid JSON — emit as plain text
    }

    if (proposal) {
      segments.push({ type: 'proposal', proposal });
    } else {
      segments.push({ type: 'text', content: match[0] });
    }

    lastIndex = end;
  }

  if (lastIndex < text.length) {
    const remaining = text.slice(lastIndex);
    if (remaining.trim()) segments.push({ type: 'text', content: remaining });
  }

  return segments;
}

function strategy2(text: string): MessageSegment[] {
  const segments: MessageSegment[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(GENERIC_FENCE_RE)) {
    const start = match.index!;
    const end = start + match[0].length;
    const proposals = parseProposalsFromJsonText(match[2]);
    if (proposals.length === 0) continue;

    if (start > lastIndex) {
      const slice = text.slice(lastIndex, start);
      if (slice.trim()) segments.push({ type: 'text', content: slice });
    }
    for (const p of proposals) {
      segments.push({ type: 'proposal', proposal: p });
    }
    lastIndex = end;
  }

  if (lastIndex < text.length) {
    const remaining = text.slice(lastIndex);
    if (remaining.trim()) segments.push({ type: 'text', content: remaining });
  }

  return segments;
}

function strategy3(text: string): MessageSegment[] {
  // Try arrays first — they may encode multiple proposals in one block
  let pos = 0;
  while (pos < text.length) {
    const idx = text.indexOf('[', pos);
    if (idx === -1) break;
    const candidate = findBalanced(text, '[', ']', idx);
    if (candidate) {
      const proposals = parseProposalsFromJsonText(candidate);
      if (proposals.length > 0) {
        const segments: MessageSegment[] = [];
        const before = text.slice(0, idx);
        const after = text.slice(idx + candidate.length);
        if (before.trim()) segments.push({ type: 'text', content: before });
        for (const p of proposals) segments.push({ type: 'proposal', proposal: p });
        if (after.trim()) segments.push({ type: 'text', content: after });
        return segments;
      }
    }
    pos = idx + 1;
  }

  // Fall back to individual objects
  const found: Array<{ proposal: ColumnProposal; start: number; end: number }> = [];
  pos = 0;
  while (pos < text.length) {
    const idx = text.indexOf('{', pos);
    if (idx === -1) break;
    const candidate = findBalanced(text, '{', '}', idx);
    if (candidate) {
      const proposals = parseProposalsFromJsonText(candidate);
      for (const p of proposals) {
        found.push({ proposal: p, start: idx, end: idx + candidate.length });
      }
      pos = idx + candidate.length;
    } else {
      pos = idx + 1;
    }
  }

  if (found.length === 0) return [];

  const segments: MessageSegment[] = [];
  let lastEnd = 0;
  for (const { proposal, start, end } of found) {
    if (start > lastEnd) {
      const slice = text.slice(lastEnd, start);
      if (slice.trim()) segments.push({ type: 'text', content: slice });
    }
    segments.push({ type: 'proposal', proposal });
    lastEnd = end;
  }
  if (lastEnd < text.length) {
    const remaining = text.slice(lastEnd);
    if (remaining.trim()) segments.push({ type: 'text', content: remaining });
  }

  return segments;
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/**
 * Split `message` into alternating text and column-proposal segments.
 *
 * Strategies tried in order (mirrors Python parse_column_proposals):
 * 1. ```column-proposal fenced blocks (canonical format)
 * 2. Generic code fences (```json, bare ```, etc.) with proposal-shaped JSON
 * 3. Bare JSON objects/arrays embedded in prose
 *
 * Pre-processing: thinking tags are stripped before any strategy is attempted.
 *
 * Field-name normalization: "question" → prompt, "title" → name,
 * "values"/"options"/"choices" → allowed_values, etc.
 *
 * When `message` contains no parseable proposals, returns a single text segment.
 */
export function parseProposals(message: string): MessageSegment[] {
  // Pre-process: strip thinking tags
  const text = stripThinkingTags(message);

  // Strategy 1: column-proposal fenced blocks
  {
    const segs = strategy1(text);
    if (segs.some((s) => s.type === 'proposal')) return segs;
  }

  // Strategy 2: generic fenced blocks (```json, etc.)
  {
    const segs = strategy2(text);
    if (segs.some((s) => s.type === 'proposal')) return segs;
  }

  // Strategy 3: bare JSON objects/arrays
  {
    const segs = strategy3(text);
    if (segs.some((s) => s.type === 'proposal')) return segs;
  }

  // No proposals found
  const displayText = text.trim() ? text : message;
  return [{ type: 'text', content: displayText }];
}
