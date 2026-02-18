// ---- Works ----

export interface WorkOut {
  id: number;
  doi: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  title: string;
  abstract: string | null;
  publication_year: number | null;
  venue_id: number | null;
  bibtex_key: string | null;
  bibtex_entry: string | null;
  pdf_path: string | null;
  citation_count: number | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
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
}

export interface VenueAliasOut {
  id: number;
  venue_id: number;
  alias: string;
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

export interface TimelineSeedWork {
  id: number;
  doi: string | null;
  arxiv_id: string | null;
  title: string;
  publication_year: number | null;
  venue_id: number | null;
  citation_count: number | null;
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
