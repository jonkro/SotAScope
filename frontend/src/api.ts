import type {
  WorkOut,
  WorkDetail,
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
  AuthorOut,
  WorkLocationOut,
  TimelineResponse,
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

// ---- Venues ----

export function fetchVenues(params?: { offset?: number; limit?: number; q?: string }) {
  const sp = new URLSearchParams();
  if (params?.offset) sp.set('offset', String(params.offset));
  if (params?.limit) sp.set('limit', String(params.limit));
  if (params?.q) sp.set('q', params.q);
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

// ---- Enrichment ----

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
