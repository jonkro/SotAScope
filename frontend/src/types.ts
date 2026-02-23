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
}

export interface SemanticScholarEnrichResult {
  work: WorkOut;
  new_references: number;
  existing_references: number;
  new_citing: number;
  existing_citing: number;
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
}

export interface CitationWorkBrief {
  id: number;
  doi: string | null;
  title: string;
  publication_year: number | null;
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
}

export interface CrossrefEnrichResult {
  work: WorkOut;
  venue_issn: string | null;
  venue_publisher: string | null;
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
