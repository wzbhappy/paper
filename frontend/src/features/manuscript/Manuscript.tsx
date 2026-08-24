import { useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  type ManuscriptSection,
  type QualityReport,
  type WriteActionName,
} from '../../api/client'
import { Button, Card, ErrorBanner } from '../../components/ui'

const SELECTION_ACTIONS: { key: WriteActionName; label: string }[] = [
  { key: 'expand', label: '扩写' },
  { key: 'rewrite', label: '改写' },
  { key: 'polish', label: '润色' },
  { key: 'academic', label: '学术化' },
  { key: 'dedup', label: '降重' },
  { key: 'translate', label: '翻译' },
]

const ISSUE_LABEL: Record<string, string> = {
  out_of_range: '引用越界',
  unused_reference: '未被引用',
  empty_section: '章节为空',
  missing_citation: '缺少引用',
  term_inconsistency: '术语不统一',
  duplicate_sentence: '重复句',
  informal_language: '口语化',
  long_sentence: '长句',
  heading_jump: '层级跳跃',
  numbering_gap: '编号不连续',
  numbering_start: '编号起始',
}

const SEVERITY_STYLE: Record<string, string> = {
  error: 'text-red-700',
  warning: 'text-amber-800',
  info: 'text-gray-600',
}

export default function Manuscript({ projectId }: { projectId: string }) {
  const [sections, setSections] = useState<ManuscriptSection[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [quality, setQuality] = useState<QualityReport | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const active = sections.find((s) => s.outline_section_id === activeId) ?? null

  async function load(keepActive = true) {
    try {
      const list = await api.listManuscript(projectId)
      setSections(list)
      setError(null)
      if (!keepActive || !activeId) {
        const first = list[0]?.outline_section_id ?? null
        setActiveId(first)
        setDraft(list[0]?.content ?? '')
        setDirty(false)
      } else {
        const current = list.find((s) => s.outline_section_id === activeId)
        if (current && !dirty) setDraft(current.content)
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载正文失败')
    }
  }

  useEffect(() => {
    load(false)
  }, [projectId])

  function switchSection(sectionId: string) {
    if (dirty && !confirm('当前章节有未保存的修改，切换将丢失。继续？')) return
    setActiveId(sectionId)
    const target = sections.find((s) => s.outline_section_id === sectionId)
    setDraft(target?.content ?? '')
    setDirty(false)
    setNotice(null)
  }

  async function save() {
    if (!activeId) return
    setBusy('save')
    try {
      await api.saveManuscriptSection(projectId, activeId, { content: draft })
      setDirty(false)
      setNotice('已保存。')
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '保存失败')
    } finally {
      setBusy(null)
    }
  }

  async function generateDraft() {
    if (!activeId) return
    if (draft.trim() && !confirm('该章节已有内容，生成初稿会覆盖。继续？')) return
    setBusy('draft')
    setError(null)
    try {
      const result = await api.aiWrite(projectId, activeId, {
        action: 'draft',
        apply: true,
      })
      setDraft(result.content)
      setDirty(false)
      setNotice(
        result.invalid_citations.length
          ? `已生成，但剥离了 ${result.invalid_citations.length} 处编造引用，请复核。`
          : '初稿已生成。',
      )
      await load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '生成失败')
    } finally {
      setBusy(null)
    }
  }

  async function runSelectionAction(action: WriteActionName) {
    if (!activeId) return
    const el = textareaRef.current
    if (!el) return
    const start = el.selectionStart
    const end = el.selectionEnd
    const selection = draft.slice(start, end)
    if (!selection.trim()) {
      setError('请先在正文中选中要处理的文字。')
      return
    }

    setBusy(action)
    setError(null)
    try {
      const result = await api.aiWrite(projectId, activeId, {
        action,
        selection,
        language: action === 'translate' ? '英文' : '中文',
      })
      // 用返回结果替换选中区间
      setDraft(draft.slice(0, start) + result.content + draft.slice(end))
      setDirty(true)
      setNotice(`${action} 完成，请检查后保存。`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '处理失败')
    } finally {
      setBusy(null)
    }
  }

  async function checkQuality() {
    setBusy('quality')
    try {
      setQuality(await api.qualityCheck(projectId))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '检查失败')
    } finally {
      setBusy(null)
    }
  }

  if (sections.length === 0) {
    return (
      <div>
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
        <p className="text-gray-400">
          还没有大纲。请先到「论文大纲」标签生成大纲，正文按章节撰写。
        </p>
      </div>
    )
  }

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="mb-4 flex flex-wrap gap-2">
        <Button variant="ghost" onClick={checkQuality} disabled={busy !== null}>
          质量检查
        </Button>
        <a
          href={api.exportUrl(projectId, 'markdown')}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          导出 Markdown
        </a>
        <a
          href={api.exportUrl(projectId, 'latex')}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          导出 LaTeX
        </a>
        <a
          href={api.exportUrl(projectId, 'docx')}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          导出 Word
        </a>
        <a
          href={api.exportUrl(projectId, 'bibtex')}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          导出 BibTeX
        </a>
      </div>

      {quality && (
        <Card className="mb-4">
          <h3 className="mb-2 font-semibold text-gray-900">质量检查</h3>
          <p className="text-sm text-gray-600">
            {quality.section_count} 个章节（{quality.empty_sections} 个为空） ·{' '}
            {quality.word_count} 字 · {quality.reference_count} 条参考文献 ·{' '}
            {quality.ai_generated_sections} 节含 AI 生成内容
          </p>
          <p className="mt-1 text-sm">
            <span className={quality.error_count ? 'text-red-700' : 'text-green-700'}>
              {quality.error_count} 个严重问题
            </span>
            <span className="mx-2 text-gray-300">|</span>
            <span className="text-amber-800">{quality.warning_count} 个警告</span>
          </p>

          {quality.issues.length === 0 ? (
            <p className="mt-2 text-sm text-green-700">未发现问题。</p>
          ) : (
            <ul className="mt-3 grid gap-1.5 text-sm">
              {quality.issues.map((issue, i) => (
                <li key={i} className={SEVERITY_STYLE[issue.severity] ?? 'text-gray-700'}>
                  <span className="font-medium">
                    [{ISSUE_LABEL[issue.kind] ?? issue.kind}]
                  </span>{' '}
                  {issue.section}：{issue.detail}
                  {issue.suggestion && (
                    <span className="text-gray-500"> — {issue.suggestion}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <nav aria-label="章节列表" className="grid content-start gap-1">
          {sections.map((section) => (
            <button
              key={section.outline_section_id}
              onClick={() => switchSection(section.outline_section_id)}
              aria-current={section.outline_section_id === activeId}
              className={`rounded px-3 py-2 text-left text-sm ${
                section.outline_section_id === activeId
                  ? 'bg-blue-50 font-medium text-blue-800'
                  : 'text-gray-700 hover:bg-gray-100'
              }`}
              style={{ paddingLeft: `${0.75 + (section.level - 1) * 0.75}rem` }}
            >
              <span className="block truncate">{section.title}</span>
              <span className="text-xs text-gray-400">
                {section.word_count > 0 ? `${section.word_count} 字` : '未开始'}
                {section.ai_generated ? ' · AI' : ''}
              </span>
            </button>
          ))}
        </nav>

        <Card>
          {active && (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="font-semibold text-gray-900">{active.title}</h3>
                  <p className="text-xs text-gray-500">{active.path}</p>
                </div>
                <div className="flex gap-2">
                  <Button onClick={generateDraft} disabled={busy !== null}>
                    {busy === 'draft' ? '生成中…' : 'AI 生成初稿'}
                  </Button>
                  <Button onClick={save} disabled={busy !== null || !dirty}>
                    {busy === 'save' ? '保存中…' : '保存'}
                  </Button>
                </div>
              </div>

              <div className="mb-3 flex flex-wrap gap-1.5">
                {SELECTION_ACTIONS.map((item) => (
                  <Button
                    key={item.key}
                    variant="ghost"
                    onClick={() => runSelectionAction(item.key)}
                    disabled={busy !== null}
                  >
                    {busy === item.key ? '处理中…' : item.label}
                  </Button>
                ))}
                <span className="self-center text-xs text-gray-500">
                  先在正文中选中文字，再点上面的按钮
                </span>
              </div>

              {notice && (
                <p role="status" className="mb-3 text-sm text-blue-700">
                  {notice}
                </p>
              )}

              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value)
                  setDirty(true)
                }}
                placeholder="在此撰写正文，或点击「AI 生成初稿」。引用格式为 [1]、[2,3]，编号对应本节可引用文献。"
                aria-label={`${active.title} 正文`}
                className="h-96 w-full rounded border border-gray-300 p-3 text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500"
              />

              <div className="mt-2 flex justify-between text-xs text-gray-500">
                <span>
                  {draft.length} 字{dirty ? ' · 未保存' : ''}
                </span>
                {active.source_paper_ids.length > 0 && (
                  <span>{active.source_paper_ids.length} 篇引用文献</span>
                )}
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}
