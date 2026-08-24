import { useState } from 'react'
import { api, ApiError, type SearchResultItem } from '../../api/client'
import { Button, Card, ErrorBanner } from '../../components/ui'

const SOURCES = [
  { key: 'arxiv', label: 'arXiv' },
  { key: 'semantic_scholar', label: 'Semantic Scholar' },
  { key: 'crossref', label: 'Crossref' },
]

export default function Search({
  projectId,
  onImported,
}: {
  projectId: string
  onImported?: () => void
}) {
  const [query, setQuery] = useState('')
  const [sources, setSources] = useState<string[]>(SOURCES.map((s) => s.key))
  const [yearFrom, setYearFrom] = useState('')
  const [results, setResults] = useState<SearchResultItem[]>([])
  const [expanded, setExpanded] = useState<string[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [searching, setSearching] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  function itemKey(item: SearchResultItem, index: number) {
    return item.doi || item.source_id || `${item.source}-${index}`
  }

  async function runSearch(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setSearching(true)
    setError(null)
    setNotice(null)
    try {
      const resp = await api.search(projectId, {
        query: query.trim(),
        sources: sources.length ? sources : undefined,
        limit: 20,
        year_from: yearFrom ? Number(yearFrom) : undefined,
      })
      setResults(resp.results)
      setExpanded(resp.expanded_queries)
      setSelected(new Set())
      if (resp.results.length === 0) setNotice('没有找到结果，试试换个说法或放宽年份限制。')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '检索失败')
    } finally {
      setSearching(false)
    }
  }

  function toggle(key: string) {
    const next = new Set(selected)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setSelected(next)
  }

  function selectAllNew() {
    const next = new Set<string>()
    results.forEach((item, i) => {
      if (!item.already_in_library) next.add(itemKey(item, i))
    })
    setSelected(next)
  }

  async function importSelected() {
    const items = results.filter((item, i) => selected.has(itemKey(item, i)))
    if (!items.length) return
    setImporting(true)
    setError(null)
    try {
      const resp = await api.importPapers(projectId, items)
      setNotice(`已导入 ${resp.imported} 篇${resp.skipped ? `，跳过重复 ${resp.skipped} 篇` : ''}。`)
      // 重新标记已入库状态
      setResults((prev) =>
        prev.map((item, i) =>
          selected.has(itemKey(item, i)) ? { ...item, already_in_library: true } : item,
        ),
      )
      setSelected(new Set())
      onImported?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '导入失败')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <Card className="mb-6">
        <form onSubmit={runSearch}>
          <div className="flex flex-wrap gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="检索需求，例如：图神经网络在引文分析中的应用"
              aria-label="检索需求"
              className="min-w-64 flex-1 rounded border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              value={yearFrom}
              onChange={(e) => setYearFrom(e.target.value.replace(/\D/g, ''))}
              placeholder="起始年份"
              aria-label="起始年份"
              className="w-28 rounded border border-gray-300 px-3 py-2"
            />
            <Button type="submit" disabled={searching || !query.trim()}>
              {searching ? '检索中…' : '检索'}
            </Button>
          </div>

          <div className="mt-3 flex flex-wrap gap-4">
            {SOURCES.map((s) => (
              <label key={s.key} className="flex items-center gap-1.5 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={sources.includes(s.key)}
                  onChange={(e) =>
                    setSources((prev) =>
                      e.target.checked ? [...prev, s.key] : prev.filter((x) => x !== s.key),
                    )
                  }
                />
                {s.label}
              </label>
            ))}
          </div>
        </form>

        {expanded.length > 0 && (
          <p className="mt-3 border-t border-gray-100 pt-3 text-xs text-gray-500">
            实际检索式：{expanded.join(' / ')}
          </p>
        )}
      </Card>

      {notice && (
        <p role="status" className="mb-4 rounded border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
          {notice}
        </p>
      )}

      {results.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Button variant="ghost" onClick={selectAllNew}>
            全选未入库
          </Button>
          <Button onClick={importSelected} disabled={importing || selected.size === 0}>
            {importing ? '导入中…' : `导入选中（${selected.size}）`}
          </Button>
          <span className="text-sm text-gray-500">共 {results.length} 条结果</span>
        </div>
      )}

      <div className="grid gap-3">
        {results.map((item, index) => {
          const key = itemKey(item, index)
          return (
            <Card key={key}>
              <div className="flex gap-3">
                <input
                  type="checkbox"
                  className="mt-1.5 shrink-0"
                  checked={selected.has(key)}
                  disabled={item.already_in_library}
                  onChange={() => toggle(key)}
                  aria-label={`选择 ${item.title}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold text-gray-900">{item.title}</h3>
                    {item.already_in_library && (
                      <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                        已在库中
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-gray-500">
                    {item.authors.slice(0, 4).join(', ') || '作者未知'}
                    {item.authors.length > 4 ? ' 等' : ''}
                    {item.year ? ` · ${item.year}` : ''}
                    {item.venue ? ` · ${item.venue}` : ''}
                    {` · ${item.source}`}
                    {item.citation_count != null ? ` · 被引 ${item.citation_count}` : ''}
                  </p>
                  {item.abstract && (
                    <p className="mt-2 line-clamp-3 text-sm text-gray-700">{item.abstract}</p>
                  )}
                  <div className="mt-2 flex gap-3 text-xs">
                    {item.url && (
                      <a href={item.url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                        原文链接
                      </a>
                    )}
                    {item.pdf_url && (
                      <a href={item.pdf_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                        PDF
                      </a>
                    )}
                  </div>
                </div>
              </div>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
