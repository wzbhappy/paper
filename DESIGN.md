# Paper Assistant 设计文档

> 一个覆盖论文撰写全流程的 AI 辅助平台：发现问题 → 检索文献 → 确定方向 → 撰写综述 → 论文大纲 → 正文撰写 → 润色检查。

---

## 1. 项目概述

### 1.1 定位

面向研究生、科研工作者的「学术写作工作流平台」。将分散在多工具（Zotero / 知网 / arXiv / Overleaf / ChatGPT）中的写作环节整合到一个工作流里，每个环节用 LLM 增强。

### 1.2 目标用户

- 需要产出综述、开题报告、期刊论文的硕士/博士研究生
- 跨学科、文献量大、难以快速建立知识体系的科研人员

### 1.3 核心价值

1. **流程闭环**：从选题到成稿不切工具，阶段间数据自动流转
2. **基于私有文献库的 RAG**：所有 AI 输出都建立在用户自己上传/检索到的文献上，避免幻觉
3. **可追溯**：每段 AI 生成的文字都能溯源到具体文献，便于学术合规

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────┐
│                     前端 (React)                       │
│  Dashboard · 文献库 · 综述 · 大纲 · 正文 · 质量检查      │
└────────────────────────┬─────────────────────────────┘
                         │ REST / WebSocket
┌────────────────────────▼─────────────────────────────┐
│                   后端 API (FastAPI)                   │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌────┐ │
│  │发现  │ │检索  │ │阅读  │ │方向  │ │综述  │ │写作│ │
│  │问题  │ │文献  │ │管理  │ │确定  │ │生成  │ │辅助│ │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └────┘ │
└──┬──────────┬──────────┬──────────┬──────────┬───────┘
   │          │          │          │          │
┌──▼───┐ ┌───▼──┐ ┌─────▼──┐ ┌────▼─┐ ┌─────▼────┐
│LLM   │ │Retri │ │Parser  │ │Vector│ │Graph DB  │
│抽象层│ │ever  │ │(PDF)   │ │DB    │ │(引用网络)│
└──────┘ └──────┘ └────────┘ └──────┘ └──────────┘
```

### 2.1 关键设计原则

- **LLM 抽象层**：所有模型调用走统一接口，支持 OpenAI / DeepSeek / 本地 Ollama，按任务类型路由不同模型
- **RAG 优先**：生成类任务必须先检索私有文献库，给出引用来源
- **工作流状态机**：每个项目记录阶段状态，支持回溯与分支，非线性的写作过程
- **异步任务化**：PDF 解析、综述生成等长任务走任务队列，前端轮询/WebSocket 推送

---

## 3. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端框架 | FastAPI (Python 3.11+) | 异步、自动 OpenAPI 文档 |
| 任务队列 | Celery + Redis | PDF 解析、综述生成等长任务 |
| 关系数据库 | PostgreSQL | 项目、文献元数据、用户数据 |
| 向量数据库 | Qdrant | 文献片段语义检索、去重 |
| 图数据库 | Neo4j | 引用关系网络 |
| PDF 解析 | pymupdf / marker-pdf | marker-pdf 输出 Markdown 质量更好 |
| 全文检索 | Meilisearch | 关键词检索、补足向量检索 |
| LLM | OpenAI / DeepSeek / Ollama | 抽象层切换 |
| 前端 | React + Vite + TypeScript | |
| UI | Tailwind + shadcn/ui | |
| 富文本 | TipTap | 正文编辑器，支持 LaTeX/引用 |
| 部署 | Docker Compose（单机）/ K8s | |
| 监控 | Loguru + Prometheus | |

---

## 4. 数据模型

### 4.1 实体关系总览

```
User 1───* Project 1───* Manuscript
                │
                ├── * Paper *───* Tag          (文献库)
                ├── * Paper ──── * Note         (笔记/标注)
                ├── 1 Outline 1───* Section
                ├── * ResearchDirection          (方向建议)
                ├── * ReviewDraft                 (综述草稿)
                └── * Job                         (异步任务)
```

### 4.2 核心表结构（PostgreSQL）

#### projects（论文项目）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK | |
| title | TEXT | 论文题目 |
| discipline | TEXT | 学科（决定模板/提示词风格） |
| stage | ENUM | `discovery` / `search` / `reading` / `direction` / `review` / `outline` / `writing` / `review_check` |
| created_at / updated_at | TIMESTAMPTZ | |

#### papers（文献）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| project_id | UUID FK | |
| source | ENUM | `manual` / `arxiv` / `semantic_scholar` / `pubmed` / `crossref` / `cnki` |
| source_id | TEXT | 外部 ID，用于去重 |
| title / authors / abstract / year | | 元数据 |
| doi | TEXT | |
| pdf_path | TEXT | 本地存储路径 |
| parsed_md | TEXT | 解析后的 Markdown 正文 |
| summary | JSONB | AI 生成的结构化摘要（见 5.3） |
| bibtex | TEXT | 自动生成的 BibTeX 条目 |
| quality_score | FLOAT | 相关性/质量评分 |
| status | ENUM | `pending` / `parsing` / `ready` / `failed` |
| created_at | TIMESTAMPTZ | |

唯一约束：`(project_id, doi)` 与 `(project_id, source, source_id)` 防止重复入库。

#### notes（笔记/标注）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| paper_id | UUID FK | |
| anchor | JSONB | 定位（页码+文本偏移或逻辑块） |
| content | TEXT | 用户笔记内容 |
| quote | TEXT | 高亮的原文片段 |
| tags | TEXT[] | |

#### outlines / outline_sections（大纲）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| project_id | UUID FK | |
| section_id | UUID FK | 父章节（支持嵌套） |
| title / type / order | | `type` ∈ `chapter` / `section` / `subsection` |
| key_points | JSONB | 该节要点数组 |
| template | TEXT | 来源模板（IMRaD 等） |

#### manuscripts / manuscript_sections（正文草稿）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| project_id | UUID FK | |
| outline_section_id | UUID FK | 对应大纲章节 |
| content | TEXT | 富文本（Markdown/HTML） |
| word_count | INT | |
| status | ENUM | `draft` / `revising` / `done` |

#### citations（引用关系）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| manuscript_section_id | UUID FK | |
| paper_id | UUID FK | |
| locator | TEXT | 页码/章节定位 |
| citation_key | TEXT | BibTeX key |

#### research_directions（研究方向建议）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| project_id | UUID FK | |
| statement | TEXT | 方向描述 |
| innovation | TEXT | 创新点 |
| feasibility | FLOAT | 可行性评分 0-1 |
| novelty | FLOAT | 新颖性评分 |
| evidence_paper_ids | UUID[] | 支撑该方向的关键文献 |
| selected | BOOL | 用户是否采纳 |

#### jobs（异步任务）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| project_id | UUID FK | |
| type | ENUM | `parse_pdf` / `summarize` / `gen_review` / `gen_outline` ... |
| status | ENUM | `queued` / `running` / `done` / `failed` |
| result | JSONB | |
| progress | FLOAT | 0-1 |
| error | TEXT | |

---

## 5. 功能模块详细设计

### 5.1 模块①：发现问题（研究热点分析）

**输入**：用户输入学科关键词或种子文献。
**输出**：研究热点报告 + 潜在 research gap 列表。

流程：
1. 调用 arXiv / Semantic Scholar 获取近 N 年高频关键词
2. 关键词共现聚类，绘制热点演化时间线
3. LLM 对「冷热交叉区」进行 gap 提示
4. 输出结构化 gap 清单（每个 gap 附「为何是 gap」+ 支撑文献）

输出数据结构 `HotspotReport`：
```json
{
  "seed_keywords": ["graph neural network"],
  "hot_keywords": [{"term": "...", "count": 123, "trend": "rising"}],
  "gaps": [
    {
      "statement": "...",
      "reason": "已有 A、B 研究，但缺少 C 场景验证",
      "supporting_paper_ids": ["uuid", ...]
    }
  ]
}
```

### 5.2 模块②：检索文献

**多源检索适配器**（统一 `Retriever` 接口）：

| 源 | 接口 | 说明 |
|---|---|---|
| arXiv | `http://export.arxiv.org/api/query` | 预印本，免费 |
| Semantic Scholar | Graph API | 元数据 + 引用网络强 |
| PubMed | E-utilities | 生医领域 |
| Crossref | REST API | 按 DOI/标题补元数据 |
| 知网 | （需授权/付费） | 中文文献，可选 |

统一接口：
```python
class Retriever(Protocol):
    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]: ...
    async def fetch_fulltext(self, paper: PaperMeta) -> bytes | None: ...
```

**关键能力**：
- 关键词扩展：LLM 把用户查询扩展为同义/相关词 + 布尔表达式
- 去重：以 DOI 为主键，无 DOI 时用标题相似度（向量）+ 作者+年份联合判定
- 入库即触发异步解析任务

### 5.3 模块③：阅读管理（PDF 解析 + AI 摘要）

**PDF 解析流水线**：
```
上传 PDF → marker-pdf 转 Markdown → 切分逻辑块(节/段) → 入向量库 → AI 摘要
```

**结构化摘要 schema**（存 `papers.summary`）：
```json
{
  "one_line": "一句话总结",
  "problem": "要解决的问题",
  "method": "核心方法",
  "dataset": "使用的数据集",
  "metrics": {"指标名": "数值"},
  "conclusion": "结论",
  "limitations": ["局限1", "局限2"],
  "future_work": ["..."],
  "key_terms": ["术语1", "..."],
  "cited_papers": [{"title": "...", "doi": "..."}]
}
```

**笔记/标注**：富文本编辑器内高亮 → 落 `notes` 表，支持按标签聚类、按文献筛选。

### 5.4 模块④：确定研究方向

**输入**：项目文献库（已解析）+ 用户初步意向。
**输出**：N 个研究方向建议 + 创新点 + 可行性。

流程：
1. 对文献库做主题聚类（向量聚类 + LLM 命名）
2. 构建「方法 × 数据集 × 任务」矩阵，找空白格
3. LLM 基于空白格生成方向候选，每个方向 RAG 检索证据文献
4. 评分：可行性（数据/算力可得性）× 新颖性（与现有工作差异）
5. 用户可选中 / 反馈 / 让 LLM 细化

### 5.5 模块⑤：撰写综述

**核心：引用网络图驱动组织结构。**

1. 用 Neo4j 存 `Paper -cites-> Paper` 关系
2. 检测引用簇（社区发现算法 Louvain）→ 每簇 = 一个综述小节
3. 簇内按时间线/方法演进排序
4. LLM 对每簇生成综述段落，强制引用簇内文献（RAG）
5. 生成段落附引用编号，自动维护 BibTeX

**引用一致性校验**：正文出现的 `[key]` 必须在 references 列表，反之亦然。

### 5.6 模块⑥：论文大纲

**模板库**（按学科）：
- 实证研究：IMRaD（Introduction/Methods/Results/Discussion）
- 综述：按主题/时间/方法学
- 工科实验：含实验设置、消融实验章节

生成流程：
1. 读取选定研究方向 + 综述草稿
2. 加载学科模板
3. LLM 生成每节 `key_points`（要点数组）
4. 用户拖拽调整层级、增删节点
5. 大纲节点 ↔ 正文 section 双向绑定

### 5.7 模块⑦：正文撰写

**编辑器**：TipTap 富文本，支持：
- 选中段落 → 右键菜单 → 扩写/改写/翻译/降重
- `/公式` 唤起自然语言→LaTeX
- `@引用` 唤起文献库检索，插入引用
- 每节显示对应大纲要点作为写作提示

**AI 写作模式**（可切换）：
- 草稿生成：基于大纲要点 + 文献库 RAG 生成初稿
- 润色模式：保留原意，仅改语言
- 学术化模式：口语→书面学术表达

**溯源**：每段 AI 生成文本记录 `source_paper_ids`，前端可点击查看来源文献片段。

### 5.8 模块⑧：润色与检查

**检查项清单**：
- 语言：语法、时态一致性、被动/主动语态建议
- 引用一致性：正文 vs 参考文献表
- 格式：标题层级、图表编号连续性
- 重复：句级相似度（与文献库对比，标记潜在抄袭）
- 术语一致性：同一术语是否前后统一

输出 `QualityReport`：问题列表 + 严重度 + 修改建议 + 定位。

---

## 6. LLM 抽象层设计

### 6.1 统一接口

```python
class LLMProvider(Protocol):
    async def complete(self, req: LLMRequest) -> LLMResponse: ...
    async def stream(self, req: LLMRequest) -> AsyncIterator[str]: ...

class LLMRequest(BaseModel):
    messages: list[Message]
    model: str | None        # None=用任务默认模型
    task: TaskType           # 路由用
    temperature: float = 0.3
    json_mode: bool = False
    max_tokens: int | None = None

class TaskType(str, Enum):
    SUMMARIZE = "summarize"      # 小模型即可
    TRANSLATE = "translate"
    REVIEW_GEN = "review_gen"     # 大模型
    DIRECTION = "direction"       # 大模型 + 推理
    POLISH = "polish"
    CHAT = "chat"
```

### 6.2 模型路由配置（可配）

```json
{
  "llm": {
    "default": "deepseek-chat",
    "routing": {
      "summarize": "deepseek-chat",
      "direction": "gpt-4o",
      "review_gen": "claude-sonnet",
      "polish": "deepseek-chat"
    },
    "providers": {
      "openai": {"api_key_env": "OPENAI_API_KEY"},
      "deepseek": {"base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
      "ollama": {"base_url": "http://localhost:11434"}
    }
  }
}
```

### 6.3 提示词管理

按 `task + discipline` 维度组织 prompt 模板，存放 `backend/app/llm/prompts/`，支持 Jinja2 变量注入，便于不写代码调整。

### 6.4 成本与限流

- 每个 `LLMRequest` 记录 token 用量 → `llm_usage` 表
- 项目级 token 预算上限，超额告警
- 可缓存（hash(messages)）相同请求

---

## 7. RAG 与知识图谱

### 7.1 文献向量库

- 切分粒度：逻辑块（节/段），保留元数据（paper_id, section, page）
- Embedding：`bge-m3`（中英多语言）或 `text-embedding-3-large`
- 检索：混合检索（向量 + BM25/Meilisearch 关键词），RRF 融合
- 去重入库：相似度 > 0.95 视为重复

### 7.2 引用知识图谱

节点：`Paper`；边：`CITES`（带 context）。
用途：
- 综述分簇组织（社区发现）
- 找「被引最多」「引用了 A 但没引用 B」的文献
- 方向分析时定位引用空白

实现：Neo4j。

---

## 8. API 设计（RESTful）

Base: `/api/v1`。所有写操作返回 `job_id`（异步）或直接结果。

### 8.1 项目
```
POST   /projects                        创建项目
GET    /projects                       列出
GET    /projects/{id}                  详情（含 stage）
PATCH  /projects/{id}                  更新（含切换 stage）
DELETE /projects/{id}
```

### 8.2 模块① 热点分析
```
POST   /projects/{id}/hotspot          body: {seed_keywords}
GET    /projects/{id}/hotspot          获取报告
```

### 8.3 模块② 检索
```
POST   /projects/{id}/search           body: {query, sources[], filters}
GET    /projects/{id}/search/{job_id}   检索结果
POST   /projects/{id}/papers           手动添加/上传 PDF
POST   /projects/{id}/papers/import    批量导入(BibTeX/RIS)
```

### 8.4 模块③ 文献库
```
GET    /projects/{id}/papers           ?q=&tag=&sort=
GET    /projects/{id}/papers/{pid}
PATCH  /projects/{id}/papers/{pid}      修改标签/质量分
DELETE /projects/{id}/papers/{pid}
POST   /projects/{id}/papers/{pid}/parse   重新解析
GET    /projects/{id}/papers/{pid}/notes
POST   /projects/{id}/papers/{pid}/notes
```

### 8.5 模块④ 方向
```
POST   /projects/{id}/directions/generate
GET    /projects/{id}/directions
PATCH  /projects/{id}/directions/{did}    采纳/反馈
```

### 8.6 模块⑤ 综述
```
POST   /projects/{id}/review/generate     body: {cluster_strategy}
GET    /projects/{id}/review
PUT    /projects/{id}/review              编辑综述正文
```

### 8.7 模块⑥ 大纲
```
GET    /projects/{id}/outline
POST   /projects/{id}/outline/generate     body: {template}
PATCH  /projects/{id}/outline/{sid}        改节点
POST   /projects/{id}/outline/{sid}        加子节点
DELETE /projects/{id}/outline/{sid}
```

### 8.8 模块⑦ 正文
```
GET    /projects/{id}/manuscript
PUT    /projects/{id}/manuscript/{section_id}    保存正文
POST   /projects/{id}/manuscript/{section_id}/ai body: {action, selection}
       action ∈ expand/rewrite/translate/dedup/gen_draft
```

### 8.9 模块⑧ 质量检查
```
POST   /projects/{id}/quality-check
GET    /projects/{id}/quality-check/{job_id}
```

### 8.10 通用
```
GET    /projects/{id}/jobs/{job_id}        查询异步任务
GET    /projects/{id}/export               导出 (Markdown 优先, LaTeX, Word)
GET    /projects/{id}/references.bib       导出 BibTeX
```

任务状态通过 `GET /jobs/{id}` 轮询，或 WebSocket `/ws/projects/{id}` 推送。

---

## 9. 前端架构

```
src/
├── features/
│   ├── dashboard/        项目列表 + 阶段引导
│   ├── hotspot/          ① 热点图(关键词云/时间线)
│   ├── search/           ② 检索 + 入库
│   ├── library/          ③ 文献库(卡片/列表, PDF阅读器内嵌)
│   ├── direction/        ④ 方向卡片对比
│   ├── review/           ⑤ 综述编辑 + 引用网络图(react-flow/d3)
│   ├── outline/          ⑥ 大纲树(拖拽)
│   └── manuscript/       ⑦ 正文编辑(TipTap) + ⑧ 质量面板
├── components/           通用组件
├── api/                  自动从后端 OpenAPI 生成 client
├── stores/               Zustand 状态
└── hooks/
```

阶段引导：Dashboard 用 stepper 引导用户按阶段推进，但允许跳转/回退。

---

## 10. 分期开发计划

### Phase 1 — 单链路跑通（4-6 周）
- 项目/文献基础 CRUD + PostgreSQL schema
- PDF 上传 → marker 解析 → 结构化摘要 → 入向量库
- 「文献 → 研究方向建议」一条链路
- 基础前端：文献库 + 方向卡片
- **验收**：上传 10 篇 PDF，得到 3 条带证据的方向建议

### Phase 2 — 检索 + 综述闭环（4-6 周）
- 多源 Retriever（先 arXiv + Semantic Scholar + Crossref）
- 去重 + 一键入库
- 引用图谱 + 综述生成
- **验收**：输入主题 → 自动检索入库 → 生成 1500 字综述含引用

### Phase 3 — 写作链路（6-8 周）
- 大纲生成 + 模板库
- TipTap 正文编辑器 + AI 写作动作
- 引用插入与一致性校验
- **验收**：从大纲生成初稿，每段可溯源

### Phase 4 — 全流程 + 润色 + 导出（4 周）
- 热点分析模块（补齐①）
- 质量检查面板
- 导出 Markdown/LaTeX/Word
- 全流程阶段引导串联

### Phase 5 — 打磨与增长
- 多用户/团队协作
- 模板与 prompt 市场
- 本地 Ollama 一键部署版

---

## 11. 非功能性需求

- **成本**：默认路由用低成本模型，token 用量透明展示，支持本地 Ollama 零成本部署
- **隐私**：PDF 与正文为用户私有，不进训练；提供纯本地部署模式
- **性能**：PDF 解析并发上限控制；向量检索 < 500ms；LLM 流式输出避免长时间空白
- **可观测**：每个 job 记录耗时/成本/LLM 调用链；Prometheus 指标
- **学术合规**：所有 AI 生成文本标记 + 溯源；导出时可附「AI 使用声明」

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 幻觉编造引用 | 强制 RAG，引用必须命中向量库，否则标红警告 |
| PDF 解析质量参差 | marker-pdf 为主 + 失败回退 pymupdf + 人工校正入口 |
| 多源 API 限流/失效 | 适配器模式 + 失败降级 + 缓存 |
| 全流程功能过多做不完 | 严格分期，每期有独立验收点与可发布价值 |
| 中文文献（知网）难获取 | 先聚焦英文源，知网做可选/手动导入 |

---

## 13. 待决策项（已确认）

- [x] 单用户 MVP vs 多租户 → **单用户**（暂不多租户）
- [x] 图数据库是否引入 Neo4j → **引入 Neo4j**（Phase 1 起即接入）
- [x] 富文本导出格式优先级 → **Markdown > LaTeX > Word**
- [ ] 是否做 VS Code 插件形态（Phase 5 评估）
- [ ] 商业模式：开源核心 + 托管 SaaS，还是纯开源

---

## 附录 A：阶段状态机

```
discovery ──> search ──> reading ──> direction ──> review
                                                        │
                  ┌─────────────────────────────────────┘
                  ▼
              outline ──> writing ──> review_check ──> done
```

任意阶段允许回退到前序阶段，状态变更记录在 `project_history`。

## 附录 B：目录结构（规划）

```
paper-assistant/
├── backend/
│   ├── app/
│   │   ├── api/            路由
│   │   ├── services/       业务逻辑（按模块）
│   │   ├── models/         ORM 模型
│   │   ├── schemas/        Pydantic
│   │   ├── llm/            LLM 抽象 + prompts
│   │   ├── retriever/      多源检索适配器
│   │   ├── parser/         PDF 解析
│   │   ├── graph/          引用图谱
│   │   ├── rag/            向量检索 + RAG
│   │   └── workers/        Celery 任务
│   ├── tests/
│   ├── alembic/            迁移
│   └── pyproject.toml
├── frontend/
│   └── src/
├── docs/
├── docker-compose.yml
└── DESIGN.md
```
