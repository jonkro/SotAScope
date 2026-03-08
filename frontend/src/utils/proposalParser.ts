/**
 * Parses an LLM message string into alternating plain-text and column-proposal
 * segments. Matches the ```column-proposal fenced-block format emitted by the
 * schema discussion system prompt (see litexplorer/services/schema_discussion.py).
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

// Matches ```column-proposal … ``` or end-of-string if closing fence is missing.
const PROPOSAL_FENCE_RE = /```column-proposal\s*\n([\s\S]*?)(?:```|$)/g;

/**
 * Split `message` into alternating text and column-proposal segments.
 *
 * Rules (mirrors Python parse_column_proposals):
 * - Leading/trailing whitespace inside each fenced block is stripped.
 * - Missing closing fence is accepted.
 * - Invalid JSON blocks are emitted verbatim as plain text.
 * - Proposals without a non-empty `name` or `prompt` are emitted as plain text.
 * - `description` defaults to "" when absent.
 * - `allowed_values` defaults to null when absent or not an array.
 *
 * When `message` contains no proposal fences, returns a single text segment.
 */
export function parseProposals(message: string): MessageSegment[] {
  const segments: MessageSegment[] = [];
  let lastIndex = 0;

  // Reset lastIndex before iterating (regex is module-level with /g flag).
  PROPOSAL_FENCE_RE.lastIndex = 0;

  for (const match of message.matchAll(PROPOSAL_FENCE_RE)) {
    const start = match.index!;
    const end = start + match[0].length;

    // Plain text before this fence block
    if (start > lastIndex) {
      const text = message.slice(lastIndex, start);
      if (text.trim()) {
        segments.push({ type: 'text', content: text });
      }
    }

    const blockContent = match[1].trim();
    let parsed = false;

    try {
      const data: unknown = JSON.parse(blockContent);
      if (data !== null && typeof data === 'object' && !Array.isArray(data)) {
        const obj = data as Record<string, unknown>;
        const name = String(obj['name'] ?? '').trim();
        const prompt = String(obj['prompt'] ?? '').trim();

        if (name && prompt) {
          const description = String(obj['description'] ?? '').trim();

          let allowed_values: string[] | null = null;
          if (Array.isArray(obj['allowed_values'])) {
            allowed_values = obj['allowed_values'].map(String);
          }

          segments.push({
            type: 'proposal',
            proposal: { name, prompt, description, allowed_values },
          });
          parsed = true;
        }
      }
    } catch {
      // Invalid JSON — fall through to raw text
    }

    if (!parsed) {
      // Emit the raw fenced block as plain text so the user can see it
      segments.push({ type: 'text', content: match[0] });
    }

    lastIndex = end;
  }

  // Remaining text after the last fence block
  if (lastIndex < message.length) {
    const text = message.slice(lastIndex);
    if (text.trim()) {
      segments.push({ type: 'text', content: text });
    }
  }

  // No fence blocks found — return the whole message as a single text segment
  if (segments.length === 0) {
    return [{ type: 'text', content: message }];
  }

  return segments;
}
