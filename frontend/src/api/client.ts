/**
 * 后端 API 客户端。
 * - 本地开发：Vite 代理 /api 到后端，BASE 默认为相对路径 /api/v1。
 * - 生产部署（前后端不同源）：构建时用 VITE_API_BASE 注入后端绝对地址，
 *   如 https://paper-assistant.fly.dev/api/v1。
 */
const BASE = (
  (import.meta.env.VITE_API_BASE as string | undefined) || '/api/v1'
).replace(/\/+$/, '')

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

export interface OutlineTemplate {
  key: string
  name: string
  description: string
}

export interface OutlineSection {
  id: string
  project_id: string
  parent_id: string | null
  title: string
  path: string
  type: string
  level: number
  order: number
  key_points: string[]
  est_words: number
  hint: string | null
  template: string | null
  word_count: number
  has_content: boolean
}

export interface ManuscriptSection {
  id: string
  outline_section_id: string
  title: string
  path: string
  level: number
  content: string
  word_count: number
  status: string
  ai_generated: boolean
  source_paper_ids: string[]
}

export type WriteActionName =
  | 'draft'
  | 'expand'
  | 'rewrite'
  | 'polish'
  | 'academic'
  | 'translate'
  | 'dedup'

export interface WriteActionResult {
  content: string
  action: string
  paper_ids: string[]
  invalid_citations: number[]
  applied: boolean
}

export interface QualityIssue {
  section: string
  kind: string
  detail: string
  severity: 'error' | 'warning' | 'info'
  suggestion: string | null
}

export interface QualityReport {
  issues: QualityIssue[]
  kind_counts: Record<string, number>
  word_count: number
  section_count: number
  empty_sections: number
  reference_count: number
  ai_generated_sections: number
  error_count: number
  warning_count: number
}

export interface TermTrend {
  term: string
  count: number
  recent_count: number
  trend: 'rising' | 'stable' | 'declining' | 'unknown'
  recent_share: number | null
}

export interface ResearchGap {
  statement: string
  reason: string | null
  signal: string
  difficulty: number
  evidence_paper_ids: string[]
  evidence_titles: string[]
}

export interface HotspotReport {
  total_papers: number
  papers_with_terms: number
  year_from: number | null
  year_to: number | null
  trends: TermTrend[]
  cooccurrence: { a: string; b: string; count: number }[]
  isolated_terms: string[]
  limitations: string[]
  gaps: ResearchGap[]
  seed_keywords: string[]
}

export interface StageStatus {
  key: string
  label: string
  done: boolean
  detail: string
}

export interface Progress {
  current_stage: string
  suggested_stage: string
  next_action: string
  completion: number
  stages: StageStatus[]
  paper_count: number
  parsed_paper_count: number
  summarized_count: number
  direction_count: number
  has_selected_direction: boolean
  review_count: number
  outline_section_count: number
  written_section_count: number
  total_word_count: number
  quality_error_count: number
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

  outlineTemplates: (projectId: string) =>
    request<OutlineTemplate[]>(`/projects/${projectId}/outline/templates`),

  listOutline: (projectId: string) =>
    request<OutlineSection[]>(`/projects/${projectId}/outline`),

  generateOutline: (projectId: string, body: { template?: string; replace?: boolean }) =>
    request<OutlineSection[]>(`/projects/${projectId}/outline/generate`, json('POST', body)),

  addOutlineSection: (
    projectId: string,
    body: { title: string; parent_id?: string | null; key_points?: string[] },
  ) => request<OutlineSection>(`/projects/${projectId}/outline`, json('POST', body)),

  updateOutlineSection: (
    projectId: string,
    sectionId: string,
    patch: { title?: string; key_points?: string[]; est_words?: number },
  ) =>
    request<OutlineSection>(
      `/projects/${projectId}/outline/${sectionId}`,
      json('PATCH', patch),
    ),

  deleteOutlineSection: (projectId: string, sectionId: string) =>
    request<void>(`/projects/${projectId}/outline/${sectionId}`, { method: 'DELETE' }),

  listManuscript: (projectId: string) =>
    request<ManuscriptSection[]>(`/projects/${projectId}/manuscript`),

  saveManuscriptSection: (
    projectId: string,
    sectionId: string,
    body: { content: string; status?: string },
  ) =>
    request<ManuscriptSection>(
      `/projects/${projectId}/manuscript/${sectionId}`,
      json('PUT', body),
    ),

  aiWrite: (
    projectId: string,
    sectionId: string,
    body: {
      action: WriteActionName
      selection?: string
      target_words?: number
      language?: string
      apply?: boolean
    },
  ) =>
    request<WriteActionResult>(
      `/projects/${projectId}/manuscript/${sectionId}/ai`,
      json('POST', body),
    ),

  qualityCheck: (projectId: string) =>
    request<QualityReport>(`/projects/${projectId}/manuscript/quality`),

  exportUrl: (projectId: string, format: string, disclosure = true) =>
    `${BASE}/projects/${projectId}/manuscript/export?format=${format}&disclosure=${disclosure}`,

  analyzeHotspot: (projectId: string, body: { seed_keywords?: string[]; n?: number }) =>
    request<HotspotReport>(`/projects/${projectId}/hotspot`, json('POST', body)),

  getProgress: (projectId: string) => request<Progress>(`/projects/${projectId}/progress`),
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
