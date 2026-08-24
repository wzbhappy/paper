import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Paper, type Project } from '../../api/client'
import { ErrorBanner } from '../../components/ui'
import Directions from '../direction/Directions'
import Hotspot from '../hotspot/Hotspot'
import Library from '../library/Library'
import Manuscript from '../manuscript/Manuscript'
import Outline from '../outline/Outline'
import StageGuide from '../progress/StageGuide'
import Review from '../review/Review'
import Search from '../search/Search'

type Tab =
  | 'search'
  | 'library'
  | 'hotspot'
  | 'direction'
  | 'review'
  | 'outline'
  | 'manuscript'

const TABS: [Tab, string][] = [
  ['search', '检索文献'],
  ['library', '文献库'],
  ['hotspot', '研究热点'],
  ['direction', '研究方向'],
  ['review', '文献综述'],
  ['outline', '论文大纲'],
  ['manuscript', '正文撰写'],
]

/** 建议阶段 → 应跳转的标签 */
const STAGE_TO_TAB: Record<string, Tab> = {
  discovery: 'search',
  search: 'search',
  reading: 'library',
  direction: 'direction',
  review: 'review',
  outline: 'outline',
  writing: 'manuscript',
  review_check: 'manuscript',
  done: 'manuscript',
}

export default function ProjectDetail() {
  const { projectId = '' } = useParams()
  const [project, setProject] = useState<Project | null>(null)
  const [papers, setPapers] = useState<Paper[]>([])
  const [tab, setTab] = useState<Tab>('library')
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const load = useCallback(async () => {
    if (!projectId) return
    try {
      const [p, list] = await Promise.all([
        api.getProject(projectId),
        api.listPapers(projectId),
      ])
      setProject(p)
      setPapers(list)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载项目失败')
    }
  }, [projectId])

  useEffect(() => {
    load()
  }, [load, tab])

  const bump = useCallback(() => {
    setRefreshKey((k) => k + 1)
    load()
  }, [load])

  const readyPapers = papers.filter((p) => p.status === 'ready').length

  return (
    <div>
      <Link to="/" className="text-sm text-blue-600 hover:underline">
        ← 返回项目列表
      </Link>

      {error && (
        <div className="mt-4">
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <h1 className="mt-2 text-2xl font-bold text-gray-900">{project?.title ?? '加载中…'}</h1>
      {project && (
        <p className="mt-1 mb-6 text-sm text-gray-500">
          {project.discipline || '未指定学科'} · 共 {papers.length} 篇文献，已解析 {readyPapers} 篇
        </p>
      )}

      <StageGuide
        projectId={projectId}
        refreshKey={refreshKey}
        onJump={(stage) => setTab(STAGE_TO_TAB[stage] ?? 'library')}
      />

      <nav className="flex flex-wrap gap-1 border-b border-gray-200" role="tablist">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
              tab === key
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="mt-6">
        {tab === 'search' && <Search projectId={projectId} onImported={bump} />}
        {tab === 'library' && <Library projectId={projectId} />}
        {tab === 'hotspot' && <Hotspot projectId={projectId} papers={papers.length} />}
        {tab === 'direction' && (
          <Directions projectId={projectId} readyPapers={readyPapers} />
        )}
        {tab === 'review' && <Review projectId={projectId} papers={papers.length} />}
        {tab === 'outline' && <Outline projectId={projectId} onChanged={bump} />}
        {tab === 'manuscript' && <Manuscript projectId={projectId} />}
      </div>
    </div>
  )
}
