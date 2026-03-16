// ---- Works ----

export interface WorkOut {
  id: number;
  doi: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  semantic_scholar_id: string | null;
  title: string;
  abstract: string | null;
  publication_year: number | null;
  venue_id: number | null;
  bibtex_key: string | null;
  bibtex_entry: string | null;
  citation_count: number | null;
  doi_auto_resolved: boolean | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  first_author_name: string | null;
  author_count: number;
  venue_display_name: string | null;
  venue_tier: number | null;
  doi_aliases: string[];
}

export interface SemanticScholarEnrichResult {
  work: WorkOut;
  new_references: number;
  existing_references: number;
  raw_references: number;
  new_citing: number;
  existing_citing: number;
  raw_citing: number;
}

export interface GrobidEnrichResult {
  new_count: number;
  existing_count: number;
  failed_count: number;
  total_extracted: number;
}

export interface WorkLocationOut {
  id: number;
  location_type: string;
  url: string;
  is_primary: boolean;
}

export interface AuthorBrief {
  id: number;
  name: string;
}

export interface AuthorOut {
  id: number;
  name: string;
  openalex_id: string | null;
}

export interface WorkAuthorOut {
  author: AuthorBrief;
  position: number | null;
}

export interface WorkDetail extends WorkOut {
  venue_name: string | null;
  locations: WorkLocationOut[];
  authors: WorkAuthorOut[];
  doi_aliases: string[];
}

export interface CitationWorkBrief {
  id: number;
  doi: string | null;
  title: string;
  publication_year: number | null;
  citation_count: number | null;
}

export interface CitationListResponse {
  items: CitationWorkBrief[];
  total_count: number;
}

export interface BibtexImportResult {
  imported: number;
  skipped: number;
  works: WorkOut[];
  needs_doi_resolution: number[];
}

export interface DuplicateGroup {
  reason: string;
  works: WorkOut[];
}

export interface WorkPDFOut {
  id: number;
  work_id: number;
  filename: string;
  is_primary: boolean;
  created_at: string;
  extraction_status: 'ready' | 'failed' | 'pending';
}

// ---- Venues ----

export interface VenueOut {
  id: number;
  name: string;
  dblp_id: string | null;
  openalex_id: string | null;
  issn: string | null;
  publisher: string | null;
  venue_type: string | null;
  tier: number;
  field_display: string | null;
  work_count: number;
}

export interface VenueAliasOut {
  id: number;
  venue_id: number;
  alias: string;
  sort_order: number;
}

export interface VenueFieldNested {
  id: number;
  field_id: number;
  field_name: string | null;
}

export interface VenueDetail extends VenueOut {
  aliases: VenueAliasOut[];
  fields: VenueFieldNested[];
}

// ---- Fields ----

export interface FieldOut {
  id: number;
  name: string;
  venue_count: number;
}

// ---- Projects ----

export interface ProjectOut {
  id: number;
  name: string;
  description: string | null;
  owner: string | null;
  created_at: string;
  updated_at: string;
}

export interface TopicListOut {
  id: number;
  project_id: number;
  name: string;
  color: string;
  created_at: string;
}

export interface TopicListWorkOut {
  id: number;
  work_id: number;
  added_at: string;
  work: WorkOut;
}

export interface TopicListDetail extends TopicListOut {
  works: TopicListWorkOut[];
}

export interface ProjectIgnoredWorkOut {
  id: number;
  work_id: number;
  work: WorkOut;
}

export interface ProjectDetail extends ProjectOut {
  topic_lists: TopicListOut[];
  ignored_works: ProjectIgnoredWorkOut[];
}

export interface ProjectVenueTierOut {
  venue_id: number;
  venue_name: string;
  all_names: string[];
  global_tier: number;
  local_tier: number | null;
  effective_tier: number;
}

// ---- Project Merge ----

export interface TopicListMergeInfo {
  source_topic_list_id: number;
  source_topic_list_name: string;
  action: 'merge' | 'copy';
  target_topic_list_id: number | null;
}

export interface SchemaConflictInfo {
  source_schema_id: number;
  source_schema_name: string;
  target_schema_id: number;
  target_schema_name: string;
}

export interface VenueTierConflictInfo {
  venue_id: number;
  venue_name: string;
  source_tier: number;
  target_tier: number;
}

export interface MergePreview {
  topic_list_merges: TopicListMergeInfo[];
  schema_conflicts: SchemaConflictInfo[];
  venue_tier_conflicts: VenueTierConflictInfo[];
  ignored_work_overrides: number[];
  source_chat_session_count: number;
  source_note_count: number;
}

export interface SchemaDecision {
  action: 'rename' | 'drop';
  new_name?: string;
}

export interface MergeDecisions {
  schema_decisions: Record<number, SchemaDecision>;
  venue_tier_decisions: Record<number, number>;
}

// ---- Project Import ----

export interface AmbiguousMatchWork {
  title: string;
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  bibtex_key: string | null;
}

export interface AmbiguousMatch {
  incoming: AmbiguousMatchWork;
  candidates: AmbiguousMatchWork[];
}

export interface PendingVenueAlias {
  venue_id: number;
  venue_name: string;
  alias: string;
}

export interface AliasDecision {
  venue_id: number;
  alias: string;
  accepted: boolean;
}

export interface ImportResult {
  project_id: number | null;
  temp_project_id: number | null;
  project_name: string;
  works_created: number;
  works_matched: number;
  ambiguous_matches: AmbiguousMatch[];
  needs_project_decision: boolean;
  existing_project_id: number | null;
  merge_preview: MergePreview | null;
  pending_venue_aliases: PendingVenueAlias[];
  needs_alias_decision: boolean;
}

export interface ImportResolveRequest {
  action: 'merge' | 'rename';
  target_project_id?: number | null;
  new_name?: string | null;
  merge_decisions?: MergeDecisions;
}

// ---- Timeline ----

export interface CitationsByYearEntry {
  year: number;
  cited_by_count: number;
}

export interface TimelineSeedWork {
  id: number;
  doi: string | null;
  arxiv_id: string | null;
  title: string;
  publication_year: number | null;
  venue_id: number | null;
  citation_count: number | null;
  citations_by_year: CitationsByYearEntry[] | null;
  topic_list_ids: number[];
  has_backward_citations: boolean;
  has_forward_citations: boolean;
  forward_citations_fetched_at: string | null;
  backward_citations_no_oa_data: boolean;
  oa_forward_no_data: boolean;
  has_pdfs: boolean;
  // Semantic Scholar enrichment status
  s2_refs_fetched: boolean;
  s2_refs_no_data: boolean;
  s2_citing_fetched: boolean;
  s2_citing_no_data: boolean;
  // GROBID extraction status
  grobid_fetched: boolean;
}

export interface BulkJobAccepted {
  job_id: string;
  message: string;
}

export interface BulkJobStatus {
  job_id: string;
  source: string;
  status: 'running' | 'completed' | 'cancelled' | 'rate_limited';
  done: number;
  total: number;
  rate_limited_at: number | null;
  errors: string[];
}

export interface TimelineNeighborWork {
  id: number;
  doi: string | null;
  arxiv_id: string | null;
  title: string;
  publication_year: number | null;
  venue_id: number | null;
  citation_count: number | null;
  citations_by_year: CitationsByYearEntry[] | null;
  direction: 'backward' | 'forward';
  connected_seed_ids: number[];
  has_citation_data: boolean;
}

export interface SeedCitation {
  citing_seed_id: number;
  cited_seed_id: number;
}

export interface TimelineResponse {
  seeds: TimelineSeedWork[];
  neighbors: TimelineNeighborWork[];
  topic_lists: TopicListOut[];
  tier1_venue_ids: number[];
  ignored_venue_ids: number[];
  seed_citations: SeedCitation[];
}

// ---- Notes ----

export interface WorkNote {
  id: number;
  work_id: number;
  project_id: number | null;
  content: string;
  note_type: string | null;
  provenance: string;
  model_id: string | null;
  is_outdated: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectNote extends WorkNote {
  work_title: string;
  work_publication_year: number | null;
}

// ---- Settings ----

export interface SettingOut {
  key: string;
  value: string;
  description: string | null;
}

// ---- LLM ----

export interface LLMModelsResult {
  models: string[];
  error?: string;
}

// ---- Filesystem ----

export interface BrowseResult {
  current_path: string;
  parent_path: string | null;
  directories: string[];
}

export interface PDFMigrationResult {
  old_path: string;
  new_path: string;
  files_moved: number;
  directories_moved: number;
  errors: string[];
}

// ---- Enrichment ----

export interface EnrichDOIResult {
  work: WorkOut;
  source: string | null;
  cached: boolean | null;
  identifier_type: 'doi' | 'arxiv';
}

export interface EnrichDOIBatchResult {
  results: EnrichDOIResult[];
  errors: { doi: string; error: string }[];
}

export interface CitationResult {
  works: CitationWorkBrief[];
  count: number;
  cached: boolean | null;
  fetched_at: string | null;
  raw_count: number;  // total items OA returned; 0 = OA has no reference list for this paper
}

export interface CrossrefEnrichResult {
  work: WorkOut;
  venue_issn: string | null;
  venue_publisher: string | null;
}

// ---- Search Import ----

export interface SearchImportCandidate {
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  semantic_scholar_id: string | null;
  source: string;
  score: number;
}

export interface SearchImportCandidatesResult {
  candidates: SearchImportCandidate[];
}

// ---- DOI Resolution ----

export interface DOICandidate {
  doi: string;
  title: string;
  authors: string[];
  publication_year: number | null;
  venue: string | null;
  score: number;
}

export interface DOIResolutionResult {
  work_id: number;
  auto_resolved_doi: string | null;
  candidates: DOICandidate[];
}

export interface DOIInfoResult {
  doi: string;
  title: string | null;
  year: number | null;
  found: boolean;
}

// ---- LLM Chat ----

export interface ChatMessage {
  role: 'user' | 'assistant' | 'error';
  content: string;
}

// ---- Chat Sessions ----

export interface ChatSessionMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ChatSessionOut {
  id: number;
  work_id: number | null;
  project_id: number | null;
  title: string | null;
  is_auto: boolean;
  context_type: string;
  context_id: number | null;
  message_count: number;
  created_at: string;
  updated_at: string;
  messages: ChatSessionMessage[];
}

// ---- Extraction ----

export interface ExtractionColumn {
  id: number;
  schema_id: number;
  name: string;
  prompt: string;
  description: string | null;
  allowed_values: string[] | null;
  sort_order: number;
  created_at: string;
}

export interface ExtractionSchema {
  id: number;
  project_id: number | null;
  title: string;
  description: string | null;
  is_promoted: boolean;
  created_at: string;
  updated_at: string;
  columns: ExtractionColumn[];
}

export interface ExtractionColumnResult {
  column_id: number;
  column_name: string;
  answer: string;
  reasoning: string;
  note: WorkNote;
}

export interface ExtractionWorkResult {
  work_id: number;
  columns: ExtractionColumnResult[];
  parsing_method: string;
}

export interface ExtractionBatchRequest {
  work_ids: number[];
  re_evaluate_edited?: boolean;
}

export interface ExtractionBatchResult {
  results: ExtractionWorkResult[];
  errors: { work_id: number; error: string }[];
}

export interface ExtractionCellResult {
  work_id: number;
  column_id: number;
  answer_note: WorkNote;
  reasoning_note: WorkNote | null;
  proposal?: { content: string; note_id: number; model_id: string | null } | null;
}

export interface ExtractionResultsResponse {
  cells: ExtractionCellResult[];
}

// ---- Async background operations ----

/** Returned by enrichment endpoints that now run as background tasks (202 Accepted) */
export interface BackgroundAccepted {
  message: string;
  work_id: number;
}

/** Returned by extraction run endpoints (202 Accepted) */
export interface ExtractionJobAccepted {
  job_id: string;
  message: string;
}

export interface ExtractionJobProgress {
  total: number;
  completed: number;
  failed: number;
}

export interface ExtractionJobWorkStatus {
  status: 'pending' | 'running' | 'done' | 'failed';
  error?: string;
}

export interface ExtractionJobStatus {
  job_id: string;
  schema_id: number;
  status: 'running' | 'completed';
  progress: ExtractionJobProgress;
  works: Record<string, ExtractionJobWorkStatus>;
}

/** Lock status response from GET /api/works/lock-status */
export interface LockStatusResponse {
  locks: Record<string, string>;
}
