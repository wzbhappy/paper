import { useEffect, useState } from 'react'
import { api, ApiError, type Progress } from '../../api/client'
import { Card, ErrorBanner } from '../../components/ui'

/** 阶段引导条：显示整体进度与建议的下一步。 */
export default function StageGuide({
  projectId,
  refreshKey,
  onJump,
}: {
  projectId: string
  refreshKey?: number
  onJump?: (stage: string) => void
}) {
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const data = await api.getProgress(projectId)
        if (!cancelled) {
          setProgress(data)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : '加载进展失败')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [projectId, refreshKey])

  if (error) return <ErrorBanner message={error} onDismiss={() => setError(null)} />
  if (!progress) return null

  const pct = Math.round(progress.completion * 100)

  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3">
            <h3 className="font-semibold text-gray-900">写作进展</h3>
            <span className="text-sm text-gray-500">{pct}%</span>
          </div>
          <div
            className="mt-2 h-2 w-full max-w-md rounded bg-gray-200"
            role="progressbar"
            aria-label="整体完成度"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="h-full rounded bg-blue-600" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-2 text-sm text-gray-700">
            <span className="font-medium">下一步：</span>
            {progress.next_action}
          </p>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="shrink-0 text-sm text-blue-600 hover:underline"
          aria-expanded={expanded}
        >
          {expanded ? '收起阶段清单' : '展开阶段清单'}
        </button>
      </div>

      {expanded && (
        <ol className="mt-4 grid gap-2 border-t border-gray-100 pt-3">
          {progress.stages.map((stage) => (
            <li key={stage.key} className="flex items-start gap-2 text-sm">
              <span
                aria-hidden
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] ${
                  stage.done
                    ? 'bg-green-500 text-white'
                    : stage.key === progress.suggested_stage
                      ? 'bg-blue-500 text-white'
                      : 'bg-gray-200 text-gray-500'
                }`}
              >
                {stage.done ? '✓' : ''}
              </span>
              <div className="min-w-0">
                {onJump ? (
                  <button
                    onClick={() => onJump(stage.key)}
                    className={
                      stage.key === progress.suggested_stage
                        ? 'font-medium text-blue-800 hover:underline'
                        : 'text-gray-800 hover:underline'
                    }
                  >
                    {stage.label}
                  </button>
                ) : (
                  <span
                    className={
                      stage.key === progress.suggested_stage
                        ? 'font-medium text-blue-800'
                        : 'text-gray-800'
                    }
                  >
                    {stage.label}
                  </span>
                )}
                <span className="ml-2 text-gray-500">{stage.detail}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}
