import type { ReactNode } from 'react'

const STATUS_STYLE: Record<string, string> = {
  ready: 'bg-green-100 text-green-800',
  metadata_only: 'bg-amber-100 text-amber-800',
  pending: 'bg-gray-100 text-gray-700',
  parsing: 'bg-blue-100 text-blue-800',
  failed: 'bg-red-100 text-red-800',
  queued: 'bg-gray-100 text-gray-700',
  running: 'bg-blue-100 text-blue-800',
  done: 'bg-green-100 text-green-800',
}

const STATUS_LABEL: Record<string, string> = {
  ready: '已解析',
  metadata_only: '仅元数据',
  pending: '等待解析',
  parsing: '解析中',
  failed: '解析失败',
  queued: '排队中',
  running: '运行中',
  done: '已完成',
}

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? 'bg-gray-100 text-gray-700'
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${style}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-gray-200 bg-white p-4 shadow-sm ${className}`}>
      {children}
    </div>
  )
}

export function Button({
  children,
  variant = 'primary',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const styles = {
    primary: 'bg-blue-600 text-white hover:bg-blue-700',
    ghost: 'border border-gray-300 text-gray-700 hover:bg-gray-50',
    danger: 'text-red-600 hover:bg-red-50',
  }[variant]
  return (
    <button
      {...props}
      className={`rounded px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 ${styles} ${props.className ?? ''}`}
    >
      {children}
    </button>
  )
}

export function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div
      role="alert"
      className="mb-4 flex items-start justify-between rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
    >
      <span>{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="关闭错误提示" className="ml-4 font-bold">
          ×
        </button>
      )}
    </div>
  )
}

export function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100)
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-gray-600">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div
        className="h-1.5 w-full rounded bg-gray-200"
        role="progressbar"
        aria-label={label}
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full rounded bg-blue-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
