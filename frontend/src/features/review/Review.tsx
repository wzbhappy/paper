import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  waitForJob,
  type GraphStats,
  type Review as ReviewType,
} from '../../api/client'
import { Button, Card, ErrorBanner } from '../../components/ui'

const ORGANIZATIONS = [
  { key: 'topic', label: '按主题' },
  { key: 'timeline', label: '按时间线' },
  { key: 'method', label: '按方法学' },
]

export default function Review({ projectId, papers }: { projectId: string; papers: number }) {
  const [review, setReview] = useState<ReviewType | null>(null)
  const [graph, setGraph] = useState<GraphStats | null>(null)
  const [organization, setOrganization] = useState('topic')
  const [words, setWords] = useState(400)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')

  async function load() {
    try {
      const latest = await api.latestReview(projectId)
      setReview(latest)
      setDraft(latest.markdown ?? '')
    } catch (e) {
      // 还没生成过综述是正常状态
      if (!(e instanceof ApiError && e.status === 404)) {
        setError(e instanceof ApiError ? e.message : '加载综述失败')
      }
      setReview(null)
    }
    try {
      setGraph(await api.graphStats(projectId))
    } catch {
      setGraph(null)
    }
  }

  useEffect(() => {
    load()
  }, [projectId])

  async function generate() {
    setError(null)
    setStatus('提交任务…')
    try {
      const job = await api.generateReview(projectId, {
        organization,
        words_per_section: words,
      })
      const final = await waitForJob(projectId, job.id, (j) =>
        setStatus(j.status === 'running' ? `生成中… ${Math.round(j.progress * 100)}%` : '排队中…'),
      )
      if (final.status === 'failed') setError(final.error || '生成失败')
      await load()
      setEditing(false)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '生成失败')
    } finally {
      setStatus(null)
    }
  }

  async function save() {
    if (!review) return
    try {
      const updated = await api.saveReview(projectId, review.id, draft)
      setReview(updated)
      setEditing(false)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '保存失败')
    }
  }

  function download() {
    if (!review?.markdown) return
    const blob = new Blob([review.markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'literature-review.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <Card className="mb-6">
        <h3 className="mb-3 font-semibold text-gray-900">生成文献综述</h3>
        {papers === 0 ? (
          <p className="text-sm text-amber-700">
            需要先通过「检索」导入文献，或在「文献库」上传 PDF。
          </p>
        ) : (
          <p className="mb-3 text-sm text-gray-500">
            将基于 {papers} 篇文献生成。引用关系会用于自动划分小节。
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <select
            value={organization}
            onChange={(e) => setOrganization(e.target.value)}
            aria-label="组织方式"
            className="rounded border border-gray-300 px-3 py-2 text-sm"
          >
            {ORGANIZATIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            value={words}
            onChange={(e) => setWords(Number(e.target.value))}
            aria-label="每节字数"
            className="rounded border border-gray-300 px-3 py-2 text-sm"
          >
            {[200, 400, 800].map((w) => (
              <option key={w} value={w}>
                每节约 {w} 字
              </option>
            ))}
          </select>
          <Button onClick={generate} disabled={papers === 0 || status !== null}>
            {status ?? (review ? '重新生成' : '生成综述')}
          </Button>
        </div>
      </Card>

      {graph && (
        <Card className="mb-6">
          <h3 className="mb-2 font-semibold text-gray-900">引用图谱</h3>
          {!graph.available ? (
            <p className="text-sm text-amber-700">
              图谱服务不可用，综述将退化为单一小节。{graph.error}
            </p>
          ) : (
            <>
              <p className="text-sm text-gray-600">
                {graph.node_count} 个节点 · {graph.edge_count} 条引用边
              </p>
              {graph.most_cited.length > 0 && (
                <div className="mt-3">
                  <p className="mb-1 text-xs font-medium text-gray-600">被引最多</p>
                  <ol className="list-inside list-decimal text-sm text-gray-700">
                    {graph.most_cited.slice(0, 5).map((item) => (
                      <li key={item.key}>
                        {item.title || item.key}（{item.citations} 次）
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </>
          )}
        </Card>
      )}

      {!review && <p className="text-gray-400">还没有综述草稿。</p>}

      {review && (
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm text-gray-500">
                {review.sections.length} 个小节 · 约 {review.word_count} 字
              </p>
              {review.invalid_citation_count > 0 && (
                <p className="mt-1 text-sm text-amber-700">
                  已剥离 {review.invalid_citation_count} 处模型编造的引用标记，建议人工复核。
                </p>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={download}>
                下载 Markdown
              </Button>
              {editing ? (
                <>
                  <Button onClick={save}>保存</Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setDraft(review.markdown ?? '')
                      setEditing(false)
                    }}
                  >
                    取消
                  </Button>
                </>
              ) : (
                <Button variant="ghost" onClick={() => setEditing(true)}>
                  编辑
                </Button>
              )}
            </div>
          </div>

          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="综述正文"
              className="h-96 w-full rounded border border-gray-300 p-3 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          ) : (
            <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-4 text-sm leading-relaxed text-gray-800">
              {review.markdown}
            </pre>
          )}
        </Card>
      )}
    </div>
  )
}
