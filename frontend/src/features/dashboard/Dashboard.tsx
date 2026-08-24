import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, type Project } from '../../api/client'
import { Button, Card, ErrorBanner } from '../../components/ui'

const STAGE_LABEL: Record<string, string> = {
  discovery: '发现问题',
  search: '检索文献',
  reading: '阅读管理',
  direction: '确定方向',
  review: '撰写综述',
  outline: '论文大纲',
  writing: '正文撰写',
  review_check: '润色检查',
  done: '已完成',
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([])
  const [title, setTitle] = useState('')
  const [discipline, setDiscipline] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setProjects(await api.listProjects())
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '无法连接后端，请确认服务已启动')
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function create(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    setBusy(true)
    try {
      await api.createProject(title.trim(), discipline.trim() || undefined)
      setTitle('')
      setDiscipline('')
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    if (!confirm('删除该项目及其全部文献？此操作不可恢复。')) return
    try {
      await api.deleteProject(id)
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900">我的论文项目</h1>
      <p className="mt-1 text-sm text-gray-500">每个项目独立管理文献库、研究方向与草稿。</p>

      {error && <div className="mt-4"><ErrorBanner message={error} onDismiss={() => setError(null)} /></div>}

      <form onSubmit={create} className="my-6 flex flex-wrap gap-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="项目标题，例如：图神经网络综述"
          aria-label="项目标题"
          className="min-w-64 flex-1 rounded border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <input
          value={discipline}
          onChange={(e) => setDiscipline(e.target.value)}
          placeholder="学科（可选）"
          aria-label="学科"
          className="w-40 rounded border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <Button type="submit" disabled={busy || !title.trim()}>
          创建项目
        </Button>
      </form>

      {projects.length === 0 && !error && (
        <p className="text-gray-400">还没有项目，创建一个开始吧。</p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {projects.map((p) => (
          <Card key={p.id}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <Link
                  to={`/projects/${p.id}`}
                  className="block truncate text-lg font-semibold text-blue-700 hover:underline"
                >
                  {p.title}
                </Link>
                <p className="mt-1 text-sm text-gray-500">
                  {p.discipline || '未指定学科'} · {STAGE_LABEL[p.stage] ?? p.stage}
                </p>
              </div>
              <Button variant="danger" onClick={() => remove(p.id)}>
                删除
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
