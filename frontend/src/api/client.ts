/** 后端 API 客户端。Vite 代理 /api 到后端，无需配置 base URL。 */

const BASE = '/api/v1'

export interface Project {
  id: string
  title: string
  discipline: string | null
  stage: string
  created_at: string
}

export interface PaperSummary {
  one_line?: string | null
  problem?: string | null
  method?: string | null
  dataset?: string | null
  metrics?: Record<string, string>
  conclusion?: string | null
  limitations?: string[]
  future_work?: string[]
  key_terms?: string[]
}

export interface Paper {
  id: string
  project_id: string
  title: string | null
  authors: string | null
  abstract: string | null
  year: number | null
  doi: string | null
  venue: string | null
  citation_count: number | null
  url: string | null
  pdf_url: string | null
  source: string
  status: 'pending' | 'parsing' | 'ready' | 'failed' | 'metadata_only'
  error: string | null
  chunk_count: number
  summary: PaperSummary | null
  bibtex: string | null
  tags: string[] | null
  has_pdf: boolean
  created_at: string
}

export interface Direction {
  id: string
  project_id: string
  statement: string
  gap: string | null
  innovation: string | null
  method_sketch: string | null
  feasibility: number
  novelty: number
  evidence_paper_ids: string[] | null
  evidence_titles: string[] | null
  selected: boolean
  feedback: string | null
  created_at: string
}

export interface Job {
  id: string
  project_id: string
  type: string
  status: 'queued' | 'running' | 'done' | 'failed'
  progress: number
  result: Record<string, unknown> | null
  error: string | null
}

export interface SearchResultItem {
  title: string
  source: string
  source_id: string | null
  authors: string[]
  abstract: string | null
  year: number | null
  doi: string | null
  arxiv_id: string | null
  venue: string | null
  citation_count: number | null
  url: string | null
  pdf_url: string | null
  references: string[]
  already_in_library: boolean
}

export interface SearchResponse {
  query: string
  expanded_queries: string[]
  keywords: string[]
  results: SearchResultItem[]
}

export interface ReviewSection {
  title: string
  content: string
  paper_ids: string[]
  invalid_citations: number[]
}

export interface Review {
  id: string
  project_id: string
  organization: string
  sections: ReviewSection[]
  markdown: string | null
  bibtex: string | null
  word_count: number
  invalid_citation_count: number
  created_at: string
}

export interface GraphStats {
  node_count: number
  edge_count: number
  most_cited: { key: string; citations: number; title: string | null }[]
  available: boolean
  error: string | null
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, init)
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // 响应非 JSON，保留默认信息
    }
    throw new ApiError(detail, resp.status)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  listProjects: () => request<Project[]>('/projects'),

  createProject: (title: string, discipline?: string) =>
    request<Project>('/projects', json('POST', { title, discipline: discipline || null })),

  getProject: (id: string) => request<Project>(`/projects/${id}`),

  updateProject: (id: string, patch: Partial<Pick<Project, 'title' | 'discipline' | 'stage'>>) =>
    request<Project>(`/projects/${id}`, json('PATCH', patch)),

  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),

  listPapers: (projectId: string) => request<Paper[]>(`/projects/${projectId}/papers`),

  uploadPaper: (projectId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Paper>(`/projects/${projectId}/papers`, { method: 'POST', body: form })
  },

  reparsePaper: (projectId: string, paperId: string) =>
    request<Job>(`/projects/${projectId}/papers/${paperId}/parse`, { method: 'POST' }),

  deletePaper: (projectId: string, paperId: string) =>
    request<void>(`/projects/${projectId}/papers/${paperId}`, { method: 'DELETE' }),

  listDirections: (projectId: string) =>
    request<Direction[]>(`/projects/${projectId}/directions`),

  generateDirections: (projectId: string, body: { n: number; intent?: string; replace?: boolean }) =>
    request<Job>(`/projects/${projectId}/directions/generate`, json('POST', body)),

  updateDirection: (
    projectId: string,
    directionId: string,
    patch: { selected?: boolean; feedback?: string },
  ) => request<Direction>(`/projects/${projectId}/directions/${directionId}`, json('PATCH', patch)),

  getJob: (projectId: string, jobId: string) =>
    request<Job>(`/projects/${projectId}/jobs/${jobId}`),

  search: (
    projectId: string,
    body: {
      query: string
      sources?: string[]
      limit?: number
      year_from?: number
      year_to?: number
      expand?: boolean
    },
  ) => request<SearchResponse>(`/projects/${projectId}/search`, json('POST', body)),

  importPapers: (projectId: string, items: SearchResultItem[]) =>
    request<{ imported: number; skipped: number; paper_ids: string[] }>(
      `/projects/${projectId}/search/import`,
      json('POST', { items }),
    ),

  generateReview: (
    projectId: string,
    body: { organization?: string; words_per_section?: number },
  ) => request<Job>(`/projects/${projectId}/review/generate`, json('POST', body)),

  latestReview: (projectId: string) => request<Review>(`/projects/${projectId}/review/latest`),

  saveReview: (projectId: string, reviewId: string, markdown: string) =>
    request<Review>(`/projects/${projectId}/review/${reviewId}`, json('PUT', { markdown })),

  graphStats: (projectId: string) => request<GraphStats>(`/projects/${projectId}/graph/stats`),
}

/** 轮询任务直到完成或失败。 */
export async function waitForJob(
  projectId: string,
  jobId: string,
  onProgress?: (job: Job) => void,
  intervalMs = 1200,
  timeoutMs = 300_000,
): Promise<Job> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const job = await api.getJob(projectId, jobId)
    onProgress?.(job)
    if (job.status === 'done' || job.status === 'failed') return job
    if (Date.now() > deadline) throw new Error('任务超时')
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
