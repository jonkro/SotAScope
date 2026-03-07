import type {
  WorkOut,
  WorkDetail,
  WorkPDFOut,
  BibtexImportResult,
  CitationWorkBrief,
  VenueOut,
  VenueDetail,
  VenueAliasOut,
  VenueFieldNested,
  FieldOut,
  ProjectOut,
  ProjectDetail,
  ProjectIgnoredWorkOut,
  TopicListDetail,
  TopicListWorkOut,
  EnrichDOIResult,
  EnrichDOIBatchResult,
  CitationResult,
  CrossrefEnrichResult,
  SemanticScholarEnrichResult,
  SearchImportCandidatesResult,
  DOIResolutionResult,
  DOIInfoResult,
  AuthorOut,
  WorkLocationOut,
  TimelineResponse,
  SettingOut,
  LLMModelsResult,
  WorkNote,
  ProjectNote,
  BrowseResult,
  PDFMigrationResult,
  ExtractionSchema,
  ExtractionColumn,
  ExtractionWorkResult,
  ExtractionBatchResult,
  ExtractionResultsResponse,
} from './types';

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---- Works ----

export function fetchWorks(params: {
  offset?: number;
  limit?: number;
  q?: string;
  venue_id?: number;
  year?: number;
}) {
  const sp = new URLSearchParams();
  if (params.offset) sp.set('offset', String(params.offset));
  if (params.limit) sp.set('limit', String(params.limit));
  if (params.q) sp.set('q', params.q);
  if (params.venue_id) sp.set('venue_id', String(params.venue_id));
  if (params.year) sp.set('year', String(params.year));
  return apiFetch<WorkOut[]>(`/api/works?${sp}`);
}

export function fetchWork(workId: number) {
  return apiFetch<WorkDetail>(`/api/works/${workId}`);
}

export function createWork(data: Record<string, unknown>) {
  return apiFetch<WorkDetail>('/api/works', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateWork(workId: number, data: Record<string, unknown>) {
  return apiFetch<WorkDetail>(`/api/works/${workId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function addWorkDOIAlias(workId: number, doi: string) {
  return apiFetch<string[]>(`/api/works/${workId}/doi-aliases`, {
    method: 'POST',
    body: JSON.stringify({ doi }),
  });
}

export function removeWorkDOIAlias(workId: number, doi: string) {
  return apiFetch<string[]>(`/api/works/${workId}/doi-aliases`, {
    method: 'DELETE',
    body: JSON.stringify({ doi }),
  });
}

export function deleteWork(workId: number) {
  return apiFetch<void>(`/api/works/${workId}`, { method: 'DELETE' });
}

export function addWorkLocation(workId: number, data: { location_type: string; url: string; is_primary: boolean }) {
  return apiFetch<WorkLocationOut>(`/api/works/${workId}/locations`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function deleteWorkLocation(workId: number, locationId: number) {
  return apiFetch<void>(`/api/works/${workId}/locations/${locationId}`, { method: 'DELETE' });
}

export function addWorkAuthor(workId: number, data: { author_id: number; position?: number }) {
  return apiFetch<AuthorOut>(`/api/works/${workId}/authors`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function removeWorkAuthor(workId: number, authorId: number) {
  return apiFetch<void>(`/api/works/${workId}/authors/${authorId}`, { method: 'DELETE' });
}

export function fetchForwardCitations(workId: number, params?: { offset?: number; limit?: number }) {
  const sp = new URLSearchParams();
  if (params?.offset) sp.set('offset', String(params.offset));
  if (params?.limit) sp.set('limit', String(params.limit));
  return apiFetch<CitationWorkBrief[]>(`/api/works/${workId}/citations/forward?${sp}`);
}

export function fetchBackwardCitations(workId: number, params?: { offset?: number; limit?: number }) {
  const sp = new URLSearchParams();
  if (params?.offset) sp.set('offset', String(params.offset));
  if (params?.limit) sp.set('limit', String(params.limit));
  return apiFetch<CitationWorkBrief[]>(`/api/works/${workId}/citations/backward?${sp}`);
}

export function importBibtex(bibtex: string) {
  return apiFetch<BibtexImportResult>('/api/works/import/bibtex', {
    method: 'POST',
    body: JSON.stringify({ bibtex }),
  });
}

export function mergeWorks(targetId: number, sourceId: number) {
  return apiFetch<WorkDetail>(`/api/works/${targetId}/merge/${sourceId}`, {
    method: 'POST',
  });
}

export function fetchDuplicates() {
  return apiFetch<import('./types').DuplicateGroup[]>('/api/works/duplicates');
}

// ---- Work PDFs ----

export function fetchWorkPDFs(workId: number) {
  return apiFetch<WorkPDFOut[]>(`/api/works/${workId}/pdfs`);
}

export async function uploadWorkPDF(workId: number, file: File): Promise<WorkPDFOut> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`/api/works/${workId}/pdfs`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body);
  }
  return res.json();
}

export function setWorkPDFPrimary(workId: number, pdfId: number) {
  return apiFetch<WorkPDFOut>(`/api/works/${workId}/pdfs/${pdfId}/set-primary`, {
    method: 'PATCH',
  });
}

export function deleteWorkPDF(workId: number, pdfId: number) {
  return apiFetch<void>(`/api/works/${workId}/pdfs/${pdfId}`, { method: 'DELETE' });
}

export function serveWorkPDFUrl(workId: number, pdfId: number): string {
  return `/api/works/${workId}/pdfs/${pdfId}/file`;
}

export function extractWorkPDFText(workId: number, pdfId: number) {
  return apiFetch<{ status: string; char_count: number }>(
    `/api/works/${workId}/pdfs/${pdfId}/extract-text`,
    { method: 'POST' },
  );
}

export function workPDFTextUrl(workId: number, pdfId: number): string {
  return `/api/works/${workId}/pdfs/${pdfId}/text`;
}

export function fetchWorkPDFFromSources(workId: number) {
  return apiFetch<WorkPDFOut>(`/api/works/${workId}/pdfs/fetch`, { method: 'POST' });
}

// ---- Venues ----

export function fetchVenues(params?: { offset?: number; limit?: number; q?: string; sort_by?: string; sort_dir?: string }) {
  const sp = new URLSearchParams();
  if (params?.offset) sp.set('offset', String(params.offset));
  if (params?.limit) sp.set('limit', String(params.limit));
  if (params?.q) sp.set('q', params.q);
  if (params?.sort_by) sp.set('sort_by', params.sort_by);
  if (params?.sort_dir) sp.set('sort_dir', params.sort_dir);
  return apiFetch<VenueOut[]>(`/api/venues?${sp}`);
}

export function fetchVenue(venueId: number) {
  return apiFetch<VenueDetail>(`/api/venues/${venueId}`);
}

export function createVenue(data: Record<string, unknown>) {
  return apiFetch<VenueDetail>('/api/venues', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateVenue(venueId: number, data: Record<string, unknown>) {
  return apiFetch<VenueDetail>(`/api/venues/${venueId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteVenue(venueId: number) {
  return apiFetch<void>(`/api/venues/${venueId}`, { method: 'DELETE' });
}

export function addVenueAlias(venueId: number, alias: string) {
  return apiFetch<VenueAliasOut>(`/api/venues/${venueId}/aliases`, {
    method: 'POST',
    body: JSON.stringify({ alias }),
  });
}

export function deleteVenueAlias(venueId: number, aliasId: number) {
  return apiFetch<void>(`/api/venues/${venueId}/aliases/${aliasId}`, { method: 'DELETE' });
}

export function reorderVenueAliases(venueId: number, aliasIds: number[]) {
  return apiFetch<VenueAliasOut[]>(`/api/venues/${venueId}/aliases/reorder`, {
    method: 'POST',
    body: JSON.stringify({ alias_ids: aliasIds }),
  });
}

// ---- Fields ----

export function fetchFields() {
  return apiFetch<FieldOut[]>('/api/fields');
}

export function createField(name: string) {
  return apiFetch<FieldOut>('/api/fields', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function deleteField(fieldId: number) {
  return apiFetch<void>(`/api/fields/${fieldId}`, { method: 'DELETE' });
}

// ---- Venue Fields ----

export function addVenueField(venueId: number, fieldId: number) {
  return apiFetch<VenueFieldNested>(`/api/venues/${venueId}/fields`, {
    method: 'POST',
    body: JSON.stringify({ field_id: fieldId }),
  });
}

export function removeVenueField(venueId: number, fieldId: number) {
  return apiFetch<void>(`/api/venues/${venueId}/fields/${fieldId}`, { method: 'DELETE' });
}

// ---- Projects ----

export function fetchProjects(params?: { offset?: number; limit?: number; q?: string }) {
  const sp = new URLSearchParams();
  if (params?.offset) sp.set('offset', String(params.offset));
  if (params?.limit) sp.set('limit', String(params.limit));
  if (params?.q) sp.set('q', params.q);
  return apiFetch<ProjectOut[]>(`/api/projects?${sp}`);
}

export function fetchProject(projectId: number) {
  return apiFetch<ProjectDetail>(`/api/projects/${projectId}`);
}

export function createProject(data: { name: string; description?: string; owner?: string }) {
  return apiFetch<ProjectDetail>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateProject(projectId: number, data: Record<string, unknown>) {
  return apiFetch<ProjectDetail>(`/api/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteProject(projectId: number) {
  return apiFetch<void>(`/api/projects/${projectId}`, { method: 'DELETE' });
}

// ---- Topic Lists ----

export function fetchTopicLists(projectId: number) {
  return apiFetch<import('./types').TopicListOut[]>(`/api/projects/${projectId}/topic-lists`);
}

export function fetchTopicList(projectId: number, topicListId: number) {
  return apiFetch<TopicListDetail>(`/api/projects/${projectId}/topic-lists/${topicListId}`);
}

export function createTopicList(projectId: number, data: { name: string; color: string }) {
  return apiFetch<TopicListDetail>(`/api/projects/${projectId}/topic-lists`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateTopicList(projectId: number, topicListId: number, data: Record<string, unknown>) {
  return apiFetch<TopicListDetail>(`/api/projects/${projectId}/topic-lists/${topicListId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteTopicList(projectId: number, topicListId: number) {
  return apiFetch<void>(`/api/projects/${projectId}/topic-lists/${topicListId}`, { method: 'DELETE' });
}

export function addWorkToTopicList(projectId: number, topicListId: number, workId: number) {
  return apiFetch<TopicListWorkOut>(`/api/projects/${projectId}/topic-lists/${topicListId}/works`, {
    method: 'POST',
    body: JSON.stringify({ work_id: workId }),
  });
}

export function removeWorkFromTopicList(projectId: number, topicListId: number, workId: number) {
  return apiFetch<void>(`/api/projects/${projectId}/topic-lists/${topicListId}/works/${workId}`, {
    method: 'DELETE',
  });
}

// ---- Ignored Works ----

export function addIgnoredWork(projectId: number, workId: number) {
  return apiFetch<ProjectIgnoredWorkOut>(`/api/projects/${projectId}/ignored-works`, {
    method: 'POST',
    body: JSON.stringify({ work_id: workId }),
  });
}

export function removeIgnoredWork(projectId: number, workId: number) {
  return apiFetch<void>(`/api/projects/${projectId}/ignored-works/${workId}`, {
    method: 'DELETE',
  });
}

// ---- Timeline ----

export function fetchTimeline(projectId: number) {
  return apiFetch<TimelineResponse>(`/api/projects/${projectId}/timeline`);
}

// ---- Notes ----

export function fetchWorkNotes(workId: number, projectId?: number) {
  const sp = new URLSearchParams();
  if (projectId != null) sp.set('project_id', String(projectId));
  return apiFetch<WorkNote[]>(`/api/works/${workId}/notes?${sp}`);
}

export function createWorkNote(workId: number, data: { content: string; note_type?: string | null; project_id?: number | null; provenance?: string; model_id?: string | null }) {
  return apiFetch<WorkNote>(`/api/works/${workId}/notes`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateWorkNote(workId: number, noteId: number, data: { content?: string; note_type?: string | null; is_outdated?: boolean; provenance?: string }) {
  return apiFetch<WorkNote>(`/api/works/${workId}/notes/${noteId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteWorkNote(workId: number, noteId: number) {
  return apiFetch<void>(`/api/works/${workId}/notes/${noteId}`, { method: 'DELETE' });
}

export function fetchProjectNotes(projectId: number) {
  return apiFetch<ProjectNote[]>(`/api/projects/${projectId}/notes`);
}

// ---- Settings ----

export function fetchSettings() {
  return apiFetch<SettingOut[]>('/api/settings');
}

export function updateSetting(key: string, value: string) {
  return apiFetch<SettingOut>(`/api/settings/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    body: JSON.stringify({ value }),
  });
}

export function fetchLLMModels() {
  return apiFetch<LLMModelsResult>('/api/llm/models');
}

// ---- Filesystem ----

export function browseDirectory(path?: string) {
  const sp = new URLSearchParams();
  if (path) sp.set('path', path);
  return apiFetch<BrowseResult>(`/api/filesystem/browse?${sp}`);
}

export function createDirectory(path: string) {
  return apiFetch<{ path: string }>('/api/filesystem/mkdir', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

export function migratePDFStorage(newPath: string) {
  return apiFetch<PDFMigrationResult>('/api/settings/pdf_storage_path/migrate', {
    method: 'POST',
    body: JSON.stringify({ new_path: newPath }),
  });
}

// ---- Enrichment ----

export function getDOIInfo(doi: string) {
  return apiFetch<DOIInfoResult>(`/api/enrich/doi/info?doi=${encodeURIComponent(doi)}`);
}

export function enrichDOI(doi: string) {
  return apiFetch<EnrichDOIResult>('/api/enrich/doi', {
    method: 'POST',
    body: JSON.stringify({ doi }),
  });
}

export function enrichDOIBatch(dois: string[]) {
  return apiFetch<EnrichDOIBatchResult>('/api/enrich/doi/batch', {
    method: 'POST',
    body: JSON.stringify({ dois }),
  });
}

export function fetchBackwardCitationsEnrich(workId: number) {
  return apiFetch<CitationResult>(`/api/enrich/works/${workId}/citations/backward`, {
    method: 'POST',
  });
}

export function fetchForwardCitationsEnrich(workId: number, forceRefresh = false) {
  const sp = forceRefresh ? '?force_refresh=true' : '';
  return apiFetch<CitationResult>(`/api/enrich/works/${workId}/citations/forward${sp}`, {
    method: 'POST',
  });
}

export function enrichFromCrossref(workId: number) {
  return apiFetch<CrossrefEnrichResult>(`/api/enrich/works/${workId}/crossref`, {
    method: 'POST',
  });
}

export function enrichFromSemanticScholar(workId: number, direction: 'both' | 'backward' | 'forward' = 'both') {
  return apiFetch<SemanticScholarEnrichResult>(
    `/api/enrich/works/${workId}/semantic-scholar?direction=${direction}`,
    { method: 'POST' },
  );
}

// ---- DOI Resolution ----

export function resolveDOI(workId: number) {
  return apiFetch<DOIResolutionResult>(`/api/enrich/works/${workId}/resolve-doi`, {
    method: 'POST',
  });
}

export function confirmDOI(workId: number, doi: string) {
  return apiFetch<WorkOut>(`/api/enrich/works/${workId}/confirm-doi`, {
    method: 'POST',
    body: JSON.stringify({ doi }),
  });
}

export function resolveDOIBatch(workIds: number[]) {
  return apiFetch<DOIResolutionResult[]>('/api/enrich/works/resolve-doi/batch', {
    method: 'POST',
    body: JSON.stringify({ work_ids: workIds }),
  });
}

export function searchImportCandidates(data: { title: string; authors?: string; year?: number }) {
  return apiFetch<SearchImportCandidatesResult>('/api/enrich/search-import/candidates', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function searchImportConfirm(data: { doi?: string | null; semantic_scholar_id?: string | null }) {
  return apiFetch<EnrichDOIResult>('/api/enrich/search-import/confirm', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function postLLMChat(data: {
  project_id?: number | null;
  session_id?: number | null;
  papers: { work_id: number; use_pdf: boolean; remark?: string | null }[];
  history: { role: string; content: string }[];
  message: string;
}) {
  return apiFetch<{ reply: string }>('/api/llm/chat', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// ---- Chat Sessions ----

export function getOrCreateAutoSession(data: { work_id: number | null; project_id: number | null }) {
  return apiFetch<import('./types').ChatSessionOut>('/api/chat/sessions/auto', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listChatSessions(workId: number | null, projectId: number | null) {
  const params = new URLSearchParams();
  if (workId != null) params.set('work_id', String(workId));
  if (projectId != null) params.set('project_id', String(projectId));
  return apiFetch<import('./types').ChatSessionOut[]>(`/api/chat/sessions?${params}`);
}

export function getChatSession(sessionId: number) {
  return apiFetch<import('./types').ChatSessionOut>(`/api/chat/sessions/${sessionId}`);
}

export function saveChatSession(sessionId: number, title: string) {
  return apiFetch<import('./types').ChatSessionOut>(`/api/chat/sessions/${sessionId}/save`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export function deleteChatSession(sessionId: number) {
  return apiFetch<void>(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' });
}

export function clearChatMessages(sessionId: number) {
  return apiFetch<{ cleared: boolean }>(`/api/chat/sessions/${sessionId}/messages`, {
    method: 'DELETE',
  });
}

// ---- Extraction ----

export function getExtractionSchemas(projectId?: number) {
  const sp = new URLSearchParams();
  if (projectId != null) sp.set('project_id', String(projectId));
  return apiFetch<ExtractionSchema[]>(`/api/extraction/schemas?${sp}`);
}

export function getExtractionSchema(id: number) {
  return apiFetch<ExtractionSchema>(`/api/extraction/schemas/${id}`);
}

export function createExtractionSchema(data: {
  title: string;
  description?: string | null;
  project_id?: number | null;
}) {
  return apiFetch<ExtractionSchema>('/api/extraction/schemas', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateExtractionSchema(id: number, data: { title?: string; description?: string | null }) {
  return apiFetch<ExtractionSchema>(`/api/extraction/schemas/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function deleteExtractionSchema(id: number) {
  return apiFetch<void>(`/api/extraction/schemas/${id}`, { method: 'DELETE' });
}

export function createExtractionColumn(
  schemaId: number,
  data: {
    name: string;
    prompt: string;
    description?: string | null;
    allowed_values?: string[] | null;
    sort_order?: number;
  },
) {
  return apiFetch<ExtractionColumn>(`/api/extraction/schemas/${schemaId}/columns`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateExtractionColumn(
  columnId: number,
  data: {
    name?: string;
    prompt?: string;
    description?: string | null;
    allowed_values?: string[] | null;
    sort_order?: number;
  },
) {
  return apiFetch<ExtractionColumn>(`/api/extraction/columns/${columnId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function deleteExtractionColumn(columnId: number) {
  return apiFetch<void>(`/api/extraction/columns/${columnId}`, { method: 'DELETE' });
}

export function reorderExtractionColumns(schemaId: number, columnIds: number[]) {
  return apiFetch<ExtractionColumn[]>(`/api/extraction/schemas/${schemaId}/columns/reorder`, {
    method: 'PUT',
    body: JSON.stringify({
      columns: columnIds.map((id, idx) => ({ column_id: id, sort_order: idx })),
    }),
  });
}

export function runExtraction(schemaId: number, workId: number) {
  return apiFetch<ExtractionWorkResult>(
    `/api/extraction/schemas/${schemaId}/extract/${workId}`,
    { method: 'POST' },
  );
}

export function runBatchExtraction(schemaId: number, workIds: number[]) {
  return apiFetch<ExtractionBatchResult>(`/api/extraction/schemas/${schemaId}/extract`, {
    method: 'POST',
    body: JSON.stringify({ work_ids: workIds }),
  });
}

export function getExtractionResults(schemaId: number, workIds: number[]) {
  const sp = new URLSearchParams();
  if (workIds.length > 0) sp.set('work_ids', workIds.join(','));
  return apiFetch<ExtractionResultsResponse>(`/api/extraction/schemas/${schemaId}/results?${sp}`);
}
