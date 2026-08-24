import { useState } from 'react'
import { api, ApiError, type HotspotReport } from '../../api/client'
import { Button, Card, ErrorBanner, ScoreBar } from '../../components/ui'

const TREND_STYLE: Record<string, string> = {
  rising: 'bg-green-100 text-green-800',
  stable: 'bg-gray-100 text-gray-700',
  declining: 'bg-amber-100 text-amber-800',
  unknown: 'bg-gray-100 text-gray-500',
}

const TREND_LABEL: Record<string, string> = {
  rising: '上升',
  stable: '平稳',
  declining: '下降',
  unknown: '未知',
}

const SIGNAL_LABEL: Record<string, string> = {
  rising_isolated: '热度上升但缺少交叉研究',
  declining_unresolved: '热度下降但问题未解决',
  missing_intersection: '活跃主题之间缺少交叉',
  limitation_cluster: '多篇文献报告同类局限',
}

export default function Hotspot({
  projectId,
  papers,
}: {
  projectId: string
  papers: number
}) {
  const [report, setReport] = useState<HotspotReport | null>(null)
  const [keywords, setKeywords] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function analyze() {
    setBusy(true)
    setError(null)
    try {
      const seeds = keywords
        .split(/[,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      setReport(await api.analyzeHotspot(projectId, { seed_keywords: seeds, n: 3 }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '分析失败')
    } finally {
      setBusy(false)
    }
  }

  const maxCount = report?.trends.reduce((m, t) => Math.max(m, t.count), 1) ?? 1

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <Card className="mb-6">
        <h3 className="mb-3 font-semibold text-gray-900">研究热点与空白分析</h3>
        {papers === 0 ? (
          <p className="text-sm text-amber-700">
            需要先通过「检索文献」导入文献。分析基于文献的结构化摘要，上传 PDF 可提升关键词质量。
          </p>
        ) : (
          <p className="mb-3 text-sm text-gray-500">
            基于 {papers} 篇文献统计关键词趋势与共现，并推断研究空白。
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <input
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
            placeholder="种子关键词（可选，逗号分隔）"
            aria-label="种子关键词"
            className="min-w-64 flex-1 rounded border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <Button onClick={analyze} disabled={busy || papers === 0}>
            {busy ? '分析中…' : report ? '重新分析' : '开始分析'}
          </Button>
        </div>
      </Card>

      {!report && <p className="text-gray-400">还没有分析结果。</p>}

      {report && (
        <div className="grid gap-4">
          <Card>
            <h3 className="mb-2 font-semibold text-gray-900">概况</h3>
            <p className="text-sm text-gray-600">
              {report.total_papers} 篇文献，其中 {report.papers_with_terms} 篇含关键术语
              {report.year_from && report.year_to
                ? ` · 年份跨度 ${report.year_from}–${report.year_to}`
                : ''}
            </p>
            {report.papers_with_terms < report.total_papers && (
              <p className="mt-1 text-xs text-amber-700">
                部分文献缺少结构化摘要，上传 PDF 后重新分析可提升准确度。
              </p>
            )}
          </Card>

          {report.trends.length > 0 && (
            <Card>
              <h3 className="mb-3 font-semibold text-gray-900">关键词趋势</h3>
              <ul className="grid gap-2">
                {report.trends.slice(0, 15).map((t) => (
                  <li key={t.term} className="flex items-center gap-3">
                    <span className="w-40 shrink-0 truncate text-sm text-gray-800">
                      {t.term}
                    </span>
                    <div className="h-2 flex-1 rounded bg-gray-100">
                      <div
                        className="h-full rounded bg-blue-500"
                        style={{ width: `${(t.count / maxCount) * 100}%` }}
                      />
                    </div>
                    <span className="w-10 shrink-0 text-right text-xs text-gray-500">
                      {t.count}
                    </span>
                    <span
                      className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${
                        TREND_STYLE[t.trend] ?? TREND_STYLE.unknown
                      }`}
                    >
                      {TREND_LABEL[t.trend] ?? t.trend}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {report.cooccurrence.length > 0 && (
            <Card>
              <h3 className="mb-2 font-semibold text-gray-900">高频共现主题</h3>
              <ul className="flex flex-wrap gap-2">
                {report.cooccurrence.slice(0, 12).map((p) => (
                  <li
                    key={`${p.a}-${p.b}`}
                    className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-700"
                  >
                    {p.a} + {p.b} · {p.count}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {report.isolated_terms.length > 0 && (
            <Card>
              <h3 className="mb-2 font-semibold text-gray-900">缺少交叉研究的主题</h3>
              <p className="mb-2 text-xs text-gray-500">
                这些主题有一定热度但很少与其他主题同时出现，可能是尚未被充分交叉研究的方向。
              </p>
              <ul className="flex flex-wrap gap-2">
                {report.isolated_terms.slice(0, 15).map((term) => (
                  <li
                    key={term}
                    className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800"
                  >
                    {term}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {report.gaps.length > 0 && (
            <Card>
              <h3 className="mb-3 font-semibold text-gray-900">识别出的研究空白</h3>
              <div className="grid gap-4">
                {report.gaps.map((gap, i) => (
                  <div key={i} className="border-t border-gray-100 pt-3 first:border-0 first:pt-0">
                    <h4 className="font-medium text-gray-900">{gap.statement}</h4>
                    {gap.reason && (
                      <p className="mt-1 text-sm text-gray-700">{gap.reason}</p>
                    )}
                    <p className="mt-1 text-xs text-gray-500">
                      数据信号：{SIGNAL_LABEL[gap.signal] ?? gap.signal}
                    </p>
                    <div className="mt-2 max-w-xs">
                      <ScoreBar label="攻克难度" value={gap.difficulty} />
                    </div>
                    {gap.evidence_titles.length > 0 && (
                      <div className="mt-2">
                        <p className="mb-1 text-xs font-medium text-gray-600">支撑文献</p>
                        <ul className="list-inside list-disc text-sm text-gray-700">
                          {gap.evidence_titles.map((title, j) => (
                            <li key={j}>{title}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          )}

          {report.gaps.length === 0 && report.total_papers < 3 && (
            <Card>
              <p className="text-sm text-amber-700">
                文献数量不足（少于 3 篇），暂不进行研究空白推断——数据太少的推断没有参考价值。
              </p>
            </Card>
          )}

          {report.limitations.length > 0 && (
            <Card>
              <h3 className="mb-2 font-semibold text-gray-900">文献报告的局限</h3>
              <ul className="list-inside list-disc text-sm text-gray-700">
                {report.limitations.slice(0, 10).map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
