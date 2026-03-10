import { describe, expect, it } from 'vitest';
import { parseProposals } from './proposalParser';
import type { ColumnProposal, MessageSegment } from './proposalParser';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function proposals(segs: MessageSegment[]): ColumnProposal[] {
  return segs.filter((s): s is { type: 'proposal'; proposal: ColumnProposal } => s.type === 'proposal').map((s) => s.proposal);
}

function hasProposal(segs: MessageSegment[]): boolean {
  return segs.some((s) => s.type === 'proposal');
}

// ---------------------------------------------------------------------------
// Existing strategy smoke-tests (strategies 1–3)
// ---------------------------------------------------------------------------

describe('strategy 1 – column-proposal fenced block', () => {
  it('parses a single fenced block and interleaves text', () => {
    const msg =
      'Here is my suggestion:\n\n' +
      '```column-proposal\n' +
      '{"name": "Method", "prompt": "What method?", "description": "ML method", "allowed_values": null}\n' +
      '```\n\nMore text.';
    const segs = parseProposals(msg);
    expect(hasProposal(segs)).toBe(true);
    expect(proposals(segs)).toHaveLength(1);
    expect(proposals(segs)[0].name).toBe('Method');
  });
});

describe('strategy 2 – generic json fenced block', () => {
  it('parses a ```json fenced block', () => {
    const msg =
      'Suggestion:\n```json\n' +
      '{"name": "Sample Size", "prompt": "How many?", "description": "Count", "allowed_values": null}\n' +
      '```';
    const segs = parseProposals(msg);
    expect(hasProposal(segs)).toBe(true);
    expect(proposals(segs)[0].name).toBe('Sample Size');
  });
});

describe('strategy 3 – bare JSON in prose', () => {
  it('parses a bare JSON object', () => {
    const msg = 'Add this: {"name": "Accuracy", "prompt": "What accuracy?", "description": "Metric", "allowed_values": null}';
    const segs = parseProposals(msg);
    expect(hasProposal(segs)).toBe(true);
    expect(proposals(segs)[0].name).toBe('Accuracy');
  });
});

// ---------------------------------------------------------------------------
// Strategy 4 – markdown table
// ---------------------------------------------------------------------------

describe('strategy 4 – markdown table', () => {
  // Real-world fixture produced by GPT-OSS:120B
  const TABLE_FIXTURE = [
    '| # | Column (what to list) | Why it helps to differentiate the two papers |',
    '|---|-----------------------|----------------------------------------------|',
    '| 1 | **Reference** (authors, year, venue) | Basic bibliographic identifier. |',
    '| 2 | **Model name** | *MOIRAI-MoE* vs. *MOIRAI-MoE (with token-clustering gating)* |',
    '| 3 | **Model family** (e.g., Decoder-only MoE, Encoder-decoder Transformer) | One paper introduces a **decoder-only MoE** architecture. |',
    '| 4 | **Number of experts** (and expert size) | The MoE paper varies the number of experts. |',
  ].join('\n');

  it('extracts exactly 4 proposals from the real-world fixture', () => {
    const segs = parseProposals(TABLE_FIXTURE);
    expect(proposals(segs)).toHaveLength(4);
  });

  it('names are clean strings without bold **markers**', () => {
    const names = proposals(parseProposals(TABLE_FIXTURE)).map((p) => p.name);
    for (const name of names) {
      expect(name).not.toContain('**');
    }
    expect(names).toEqual(['Reference', 'Model name', 'Model family', 'Number of experts']);
  });

  it('strips trailing parentheticals from names', () => {
    const names = proposals(parseProposals(TABLE_FIXTURE)).map((p) => p.name);
    for (const name of names) {
      expect(name).not.toContain('(');
    }
  });

  it('populates description from the "Why" column', () => {
    const ps = proposals(parseProposals(TABLE_FIXTURE));
    expect(ps[0].description).toContain('bibliographic');
    expect(ps[3].description).toContain('experts');
  });

  it('sets prompt equal to description when no separate prompt column exists', () => {
    const ps = proposals(parseProposals(TABLE_FIXTURE));
    for (const p of ps) {
      expect(p.prompt).toBe(p.description);
    }
  });

  it('sets allowed_values to null for all rows', () => {
    const ps = proposals(parseProposals(TABLE_FIXTURE));
    for (const p of ps) {
      expect(p.allowed_values).toBeNull();
    }
  });

  it('includes the full message as a text segment', () => {
    const segs = parseProposals(TABLE_FIXTURE);
    const textSegs = segs.filter((s) => s.type === 'text');
    expect(textSegs.length).toBeGreaterThanOrEqual(1);
  });

  it('ignores a table whose header has no column/name cell', () => {
    const msg = [
      '| A | B | C |',
      '|---|---|---|',
      '| x | y | z |',
    ].join('\n');
    expect(hasProposal(parseProposals(msg))).toBe(false);
  });

  it('handles a table without a description column — prompt defaults to name', () => {
    const msg = [
      '| Column | Notes |',
      '|--------|-------|',
      '| **Latency** | some note |',
    ].join('\n');
    const ps = proposals(parseProposals(msg));
    expect(ps).toHaveLength(1);
    expect(ps[0].name).toBe('Latency');
    // No desc col matched (header is "Notes" not why/description/purpose)
    // → prompt falls back to name
    expect(ps[0].prompt).toBe('Latency');
  });

  it('skips rows where the name cell is empty or only punctuation', () => {
    const msg = [
      '| # | Column | Why |',
      '|---|--------|-----|',
      '| 1 | **Valid** | reason |',
      '|   |         |       |',
    ].join('\n');
    const ps = proposals(parseProposals(msg));
    expect(ps).toHaveLength(1);
    expect(ps[0].name).toBe('Valid');
  });

  it('strips italic markers from description cells', () => {
    const ps = proposals(parseProposals(TABLE_FIXTURE));
    // Row 2 description: "*MOIRAI-MoE* vs. *MOIRAI-MoE (with token-clustering gating)*"
    // After stripping: "MOIRAI-MoE vs. MOIRAI-MoE (with token-clustering gating)"
    expect(ps[1].description).not.toContain('*');
  });
});

// ---------------------------------------------------------------------------
// Strategy 5 – numbered/bulleted list with bold headings
// ---------------------------------------------------------------------------

describe('strategy 5 – bold heading list', () => {
  it('parses numbered list items with bold name and colon separator', () => {
    const msg = [
      '1. **Model type**: What type of model architecture is used?',
      '2. **Dataset size**: How large is the training dataset?',
      '3. **Evaluation metric**: What primary metric is reported?',
    ].join('\n');
    const ps = proposals(parseProposals(msg));
    expect(ps).toHaveLength(3);
    expect(ps[0].name).toBe('Model type');
    expect(ps[1].name).toBe('Dataset size');
    expect(ps[2].name).toBe('Evaluation metric');
  });

  it('parses bullet items with em-dash separator', () => {
    const msg = [
      '- **Method** — What ML method does the paper use?',
      '- **Baseline** — What baselines are compared?',
    ].join('\n');
    const ps = proposals(parseProposals(msg));
    expect(ps).toHaveLength(2);
    expect(ps[0].name).toBe('Method');
    expect(ps[1].name).toBe('Baseline');
  });

  it('parses asterisk bullet items', () => {
    const msg = '* **Key finding**: What is the main result reported by the paper?';
    const ps = proposals(parseProposals(msg));
    expect(ps).toHaveLength(1);
    expect(ps[0].name).toBe('Key finding');
  });

  it('sets description and prompt from the text after the separator', () => {
    const msg = '1. **Sample Size**: How many participants are included in the study?';
    const ps = proposals(parseProposals(msg));
    expect(ps[0].description).toBe('How many participants are included in the study?');
    expect(ps[0].prompt).toBe(ps[0].description);
  });

  it('sets allowed_values to null', () => {
    const msg = '- **Method** — What approach does the paper take?';
    const ps = proposals(parseProposals(msg));
    expect(ps[0].allowed_values).toBeNull();
  });

  it('ignores lines without bold heading pattern', () => {
    const msg = [
      'Here are some columns to consider:',
      '- Method: not bold, should be ignored',
      '- **Valid column** — This one has bold.',
      '  Some indented text that does not match.',
    ].join('\n');
    const ps = proposals(parseProposals(msg));
    expect(ps).toHaveLength(1);
    expect(ps[0].name).toBe('Valid column');
  });

  it('returns full message as a text segment alongside proposals', () => {
    const msg = '1. **Approach**: What approach is taken?';
    const segs = parseProposals(msg);
    const textSegs = segs.filter((s) => s.type === 'text');
    expect(textSegs.length).toBeGreaterThanOrEqual(1);
    expect(hasProposal(segs)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Strategy priority — earlier strategies win
// ---------------------------------------------------------------------------

describe('strategy priority', () => {
  it('fenced block wins over a table in the same message', () => {
    const msg =
      '```column-proposal\n' +
      '{"name": "From fence", "prompt": "Fence prompt?", "description": "", "allowed_values": null}\n' +
      '```\n\n' +
      '| Column | Why |\n' +
      '|--------|-----|\n' +
      '| **From table** | table reason |\n';
    const ps = proposals(parseProposals(msg));
    expect(ps[0].name).toBe('From fence');
  });

  it('returns a single text segment when nothing is parseable', () => {
    const msg = 'Just a conversational response with no proposals.';
    const segs = parseProposals(msg);
    expect(segs).toHaveLength(1);
    expect(segs[0].type).toBe('text');
    expect(hasProposal(segs)).toBe(false);
  });
});
