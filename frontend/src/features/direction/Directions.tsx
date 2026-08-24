import { useEffect, useState } from 'react'
import { api, ApiError, waitForJob, type Direction } from '../../api/client'
import { Button, Card, ErrorBanner, ScoreBar } from '../../components/ui'

export default function Directions({
  projectId,
  readyPapers,
}: {
  projectId: string
  readyPapers: number
}) {
  const [directions, setDirections] = useState<Direction[]>([])
  const [intent, setIntent] = useState('')
  const [n, setN] = useState(3)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  async function load() {
    try {
      setDirections(await api.listDirections(projectId))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载方向失败')
    }
  }

  useEffect(() => {
    load()
  }, [projectId])

  async function generate() {
    setError(null)
    setStatus('提交任务…')
    try {
      const job = await api.generateDirections(projectId, {
        n,
        intent: intent.trim() || undefined,
        replace: true,
      })
      const final = await waitForJob(projectId, job.id, (j) =>
        setStatus(j.status === 'running' ? `生成中… ${Math.round(j.progress * 100)}%` : '排队中…'),
      )
      if (final.status === 'failed') {
        setError(final.error || '生成失败')
      }
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '生成失败')
    } finally {
      setStatus(null)
    }
  }

  async function select(id: string) {
    try {
      await api.updateDirection(projectId, id, { selected: true })
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  const disabled = readyPapers === 0 || status !== null

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <Card className="mb-6">
        <h3 className="mb-3 font-semibold text-gray-900">生成研究方向</h3>
        {readyPapers === 0 ? (
          <p className="text-sm text-amber-700">
            需要先在「文献库」上传并解析至少一篇 PDF，方向建议会基于这些文献生成。
          </p>
        ) : (
          <p className="mb-3 text-sm text-gray-500">
            将基于项目内 {readyPapers} 篇已解析文献分析研究空白。
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <input
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="初步意向（可选），例如：想做小样本场景"
            aria-label="研究意向"
            className="min-w-64 flex-1 rounded border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <select
            value={n}
            onChange={(e) => setN(Number(e.target.value))}
            aria-label="生成数量"
            className="rounded border border-gray-300 px-3 py-2"
          >
            {[1, 3, 5].map((v) => (
              <option key={v} value={v}>
                {v} 个方向
              </option>
            ))}
          </select>
          <Button onClick={generate} disabled={disabled}>
            {status ?? '生成方向'}
          </Button>
        </div>
      </Card>

      {directions.length === 0 && <p className="text-gray-400">还没有方向建议。</p>}

      <div className="grid gap-4">
        {directions.map((d) => (
          <Card key={d.id} className={d.selected ? 'ring-2 ring-blue-500' : ''}>
            <div className="flex items-start justify-between gap-4">
              <h3 className="font-semibold text-gray-900">{d.statement}</h3>
              {d.selected ? (
                <span className="shrink-0 rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">
                  已采纳
                </span>
              ) : (
                <Button variant="ghost" onClick={() => select(d.id)} className="shrink-0">
                  采纳
                </Button>
              )}
            </div>

            <dl className="mt-3 grid gap-2 text-sm">
              {d.gap && (
                <div>
                  <dt className="font-medium text-gray-600">研究空白</dt>
                  <dd className="text-gray-800">{d.gap}</dd>
                </div>
              )}
              {d.innovation && (
                <div>
                  <dt className="font-medium text-gray-600">创新点</dt>
                  <dd className="text-gray-800">{d.innovation}</dd>
                </div>
              )}
              {d.method_sketch && (
                <div>
                  <dt className="font-medium text-gray-600">技术路线</dt>
                  <dd className="text-gray-800">{d.method_sketch}</dd>
                </div>
              )}
            </dl>

            <div className="mt-3 grid max-w-sm gap-2">
              <ScoreBar label="可行性" value={d.feasibility} />
              <ScoreBar label="新颖性" value={d.novelty} />
            </div>

            {!!d.evidence_titles?.length && (
              <div className="mt-3 border-t border-gray-100 pt-3">
                <p className="mb-1 text-xs font-medium text-gray-600">支撑文献</p>
                <ul className="list-inside list-disc text-sm text-gray-700">
                  {d.evidence_titles.map((t, i) => (
                    <li key={`${d.id}-${i}`}>{t}</li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}
