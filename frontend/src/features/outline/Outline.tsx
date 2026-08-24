import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  type OutlineSection,
  type OutlineTemplate,
} from '../../api/client'
import { Button, Card, ErrorBanner } from '../../components/ui'

export default function Outline({
  projectId,
  onChanged,
}: {
  projectId: string
  onChanged?: () => void
}) {
  const [sections, setSections] = useState<OutlineSection[]>([])
  const [templates, setTemplates] = useState<OutlineTemplate[]>([])
  const [template, setTemplate] = useState('imrad')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [addingUnder, setAddingUnder] = useState<string | null>(null)
  const [newTitle, setNewTitle] = useState('')

  async function load() {
    try {
      const [list, tpl] = await Promise.all([
        api.listOutline(projectId),
        api.outlineTemplates(projectId),
      ])
      setSections(list)
      setTemplates(tpl)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载大纲失败')
    }
  }

  useEffect(() => {
    load()
  }, [projectId])

  async function generate() {
    if (
      sections.length > 0 &&
      !confirm('重新生成会删除现有大纲及其下已写的正文，确定继续？')
    ) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.generateOutline(projectId, { template, replace: true })
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '生成大纲失败')
    } finally {
      setBusy(false)
    }
  }

  async function saveTitle(sectionId: string) {
    if (!editTitle.trim()) return
    try {
      await api.updateOutlineSection(projectId, sectionId, { title: editTitle.trim() })
      setEditing(null)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '重命名失败')
    }
  }

  async function addChild(parentId: string | null) {
    if (!newTitle.trim()) return
    try {
      await api.addOutlineSection(projectId, {
        title: newTitle.trim(),
        parent_id: parentId,
      })
      setNewTitle('')
      setAddingUnder(null)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '新增章节失败')
    }
  }

  async function remove(section: OutlineSection) {
    const hint = section.has_content
      ? '该章节已有正文，删除会一并丢失。确定删除？'
      : '删除该章节及其子章节？'
    if (!confirm(hint)) return
    try {
      await api.deleteOutlineSection(projectId, section.id)
      await load()
      onChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  // 后端已按层级排序，这里按 path 组装阅读顺序
  const ordered = [...sections].sort((a, b) => a.path.localeCompare(b.path, 'zh'))
  const totalWords = sections.reduce((sum, s) => sum + s.word_count, 0)
  const totalEst = sections.reduce((sum, s) => sum + s.est_words, 0)

  return (
    <div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <Card className="mb-6">
        <h3 className="mb-3 font-semibold text-gray-900">生成论文大纲</h3>
        <p className="mb-3 text-sm text-gray-500">
          章节骨架由模板固定，AI 只填写每节要点。若已采纳研究方向，会据此定制要点。
        </p>
        <div className="flex flex-wrap gap-2">
          <select
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            aria-label="大纲模板"
            className="rounded border border-gray-300 px-3 py-2 text-sm"
          >
            {templates.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
          <Button onClick={generate} disabled={busy}>
            {busy ? '生成中…' : sections.length ? '重新生成' : '生成大纲'}
          </Button>
        </div>
        {templates.find((t) => t.key === template) && (
          <p className="mt-2 text-xs text-gray-500">
            {templates.find((t) => t.key === template)!.description}
          </p>
        )}
      </Card>

      {sections.length === 0 ? (
        <p className="text-gray-400">还没有大纲。</p>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <span className="text-sm text-gray-500">
              {sections.length} 个章节 · 已写 {totalWords} / 预计 {totalEst} 字
            </span>
            <Button variant="ghost" onClick={() => setAddingUnder('__root__')}>
              新增顶层章节
            </Button>
          </div>

          {addingUnder === '__root__' && (
            <Card className="mb-3">
              <div className="flex gap-2">
                <input
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="章节标题"
                  aria-label="新章节标题"
                  className="flex-1 rounded border border-gray-300 px-3 py-1.5 text-sm"
                />
                <Button onClick={() => addChild(null)}>添加</Button>
                <Button variant="ghost" onClick={() => setAddingUnder(null)}>
                  取消
                </Button>
              </div>
            </Card>
          )}

          <div className="grid gap-2">
            {ordered.map((section) => (
              <Card key={section.id} className={section.level > 1 ? 'ml-6' : ''}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    {editing === section.id ? (
                      <div className="flex gap-2">
                        <input
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          aria-label="章节标题"
                          className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm"
                        />
                        <Button onClick={() => saveTitle(section.id)}>保存</Button>
                        <Button variant="ghost" onClick={() => setEditing(null)}>
                          取消
                        </Button>
                      </div>
                    ) : (
                      <h4
                        className={
                          section.level === 1
                            ? 'font-semibold text-gray-900'
                            : 'text-sm font-medium text-gray-800'
                        }
                      >
                        {section.title}
                        <span className="ml-2 text-xs font-normal text-gray-400">
                          {section.word_count > 0
                            ? `${section.word_count} / ${section.est_words} 字`
                            : `预计 ${section.est_words} 字`}
                        </span>
                      </h4>
                    )}

                    {section.key_points.length > 0 && (
                      <ul className="mt-2 list-inside list-disc text-sm text-gray-600">
                        {section.key_points.map((point, i) => (
                          <li key={`${section.id}-${i}`}>{point}</li>
                        ))}
                      </ul>
                    )}

                    {addingUnder === section.id && (
                      <div className="mt-3 flex gap-2">
                        <input
                          value={newTitle}
                          onChange={(e) => setNewTitle(e.target.value)}
                          placeholder="子章节标题"
                          aria-label="新子章节标题"
                          className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm"
                        />
                        <Button onClick={() => addChild(section.id)}>添加</Button>
                        <Button variant="ghost" onClick={() => setAddingUnder(null)}>
                          取消
                        </Button>
                      </div>
                    )}
                  </div>

                  <div className="flex shrink-0 flex-col gap-1">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setEditing(section.id)
                        setEditTitle(section.title)
                      }}
                    >
                      重命名
                    </Button>
                    <Button variant="ghost" onClick={() => setAddingUnder(section.id)}>
                      加子节
                    </Button>
                    <Button variant="danger" onClick={() => remove(section)}>
                      删除
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
