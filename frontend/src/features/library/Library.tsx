import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Paper } from '../../api/client'
import { Button, Card, ErrorBanner, StatusBadge } from '../../components/ui'

/** 有文献处于 pending/parsing 时自动轮询，直到全部落定。 */
function usePolling(active: boolean, fn: () => void, intervalMs = 2000) {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => ref.current(), intervalMs)
    return () => clearInterval(timer)
  }, [active, intervalMs])
}

export default function Library({ projectId }: { projectId: string }) {
  const [papers, setPapers] = useState<Paper[]>([])
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function load() {
    try {
      setPapers(await api.listPapers(projectId))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载文献失败')
    }
  }

  useEffect(() => {
    load()
  }, [projectId])

  const pending = papers.some((p) => p.status === 'pending' || p.status === 'parsing')
  usePolling(pending, load)

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setUploading(true)
    setError(null)
    const failures: string[] = []
    for (const file of Array.from(files)) {
      try {
        await api.uploadPaper(projectId, file)
      } catch (e) {
        failures.push(`${file.name}: ${e instanceof ApiError ? e.message : '上传失败'}`)
      }
    }
    if (failures.length) setError(failures.join('；'))
    setUploading(false)
    if (fileRef.current) fileRef.current.value = ''
    await load()
  }

  async function reparse(paperId: string) {
    try {
      await api.reparsePaper(projectId, paperId)
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '重新解析失败')
    }
  }

  async function remove(paperId: string) {
    if (!confirm('从文献库中删除该文献？')) return
    try {
      await api.deletePaper(projectId, paperId)
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  const readyCount = papers.filter((p) => p.status === 'ready').length

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="cursor-pointer rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
          {uploading ? '上传中…' : '上传 PDF'}
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            multiple
            className="hidden"
            disabled={uploading}
            onChange={(e) => upload(e.target.files)}
          />
        </label>
        <span className="text-sm text-gray-500">
          共 {papers.length} 篇，已解析 {readyCount} 篇
          {pending && ' · 解析进行中，页面会自动刷新'}
        </span>
      </div>

      {papers.length === 0 && (
        <p className="text-gray-400">文献库为空。上传 PDF 后会自动解析、生成摘要并建立索引。</p>
      )}

      <div className="grid gap-3">
        {papers.map((p) => (
          <Card key={p.id}>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-semibold text-gray-900">{p.title || '（标题待解析）'}</h3>
                  <StatusBadge status={p.status} />
                </div>
                <p className="mt-1 truncate text-sm text-gray-500">
                  {p.authors || '作者未知'}
                  {p.year ? ` · ${p.year}` : ''}
                  {p.venue ? ` · ${p.venue}` : ''}
                  {p.citation_count != null ? ` · 被引 ${p.citation_count}` : ''}
                  {p.chunk_count > 0 ? ` · ${p.chunk_count} 个索引片段` : ''}
                </p>
                {p.status === 'metadata_only' && (
                  <p className="mt-1 text-sm text-gray-500">
                    仅有元数据。
                    {p.pdf_url ? (
                      <>
                        {' '}
                        <a
                          href={p.pdf_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-600 hover:underline"
                        >
                          下载 PDF
                        </a>
                        {' '}后上传可获得全文摘要与索引。
                      </>
                    ) : (
                      ' 上传 PDF 可获得全文摘要与索引。'
                    )}
                  </p>
                )}
                {p.error && <p className="mt-1 text-sm text-red-600">{p.error}</p>}
                {p.summary?.one_line && (
                  <p className="mt-2 text-sm text-gray-700">{p.summary.one_line}</p>
                )}

                {expanded === p.id && p.summary && (
                  <dl className="mt-3 grid gap-2 border-t border-gray-100 pt-3 text-sm">
                    {p.summary.problem && (
                      <div>
                        <dt className="font-medium text-gray-600">问题</dt>
                        <dd className="text-gray-800">{p.summary.problem}</dd>
                      </div>
                    )}
                    {p.summary.method && (
                      <div>
                        <dt className="font-medium text-gray-600">方法</dt>
                        <dd className="text-gray-800">{p.summary.method}</dd>
                      </div>
                    )}
                    {p.summary.dataset && (
                      <div>
                        <dt className="font-medium text-gray-600">数据集</dt>
                        <dd className="text-gray-800">{p.summary.dataset}</dd>
                      </div>
                    )}
                    {p.summary.metrics && Object.keys(p.summary.metrics).length > 0 && (
                      <div>
                        <dt className="font-medium text-gray-600">指标</dt>
                        <dd className="text-gray-800">
                          {Object.entries(p.summary.metrics)
                            .map(([k, v]) => `${k}: ${v}`)
                            .join('，')}
                        </dd>
                      </div>
                    )}
                    {!!p.summary.limitations?.length && (
                      <div>
                        <dt className="font-medium text-gray-600">局限</dt>
                        <dd className="text-gray-800">{p.summary.limitations.join('；')}</dd>
                      </div>
                    )}
                    {!!p.summary.key_terms?.length && (
                      <div className="flex flex-wrap gap-1">
                        {p.summary.key_terms.map((t) => (
                          <span key={t} className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </dl>
                )}
              </div>

              <div className="flex shrink-0 flex-col gap-1">
                {p.summary && (
                  <Button variant="ghost" onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                    {expanded === p.id ? '收起' : '详情'}
                  </Button>
                )}
                {p.has_pdf && (
                  <Button variant="ghost" onClick={() => reparse(p.id)}>
                    重新解析
                  </Button>
                )}
                <Button variant="danger" onClick={() => remove(p.id)}>
                  删除
                </Button>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
