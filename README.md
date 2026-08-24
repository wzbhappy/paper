# Paper Assistant

覆盖论文撰写全流程的 AI 辅助平台：发现问题 → 检索文献 → 确定方向 → 撰写综述 → 论文大纲 → 正文撰写 → 润色检查。

一个项目内完成从选题到成稿的全部环节，不需要在 Zotero / 知网 / Overleaf / ChatGPT 之间来回切换。所有 AI 输出都建立在你自己的文献库上，并可溯源到具体文献。

设计文档见 [DESIGN.md](./DESIGN.md)。

## 技术决策

- 单用户部署（暂不多租户）
- 图数据库使用 Neo4j
- 导出格式优先级：Markdown > LaTeX > Word

## 快速开始

### 前置

- Docker & Docker Compose

### 启动全部服务

```bash
cp .env.example .env   # 按需填入 LLM API key
docker compose up --build
```

### 服务地址

| 服务 | 地址 | 说明 |
|---|---|---|
| 前端 | http://localhost:5173 | Dashboard |
| 后端 API | http://localhost:8000 | OpenAPI 文档 /docs |
| Neo4j | http://localhost:7474 | 账号 neo4j / password |
| Qdrant | http://localhost:6333 | 向量库 UI |
| Postgres | localhost:5432 | paper / paper |
| Redis | localhost:6379 | |

> **安全提醒**：上表中的密码是仓库内的开发默认值（见 `docker-compose.yml` 与 `.env.example`），
> 仅适用于绑定在 localhost 的本机开发。后端 API **没有任何鉴权**，任何能访问 8000 端口的人
> 都能读写全部项目数据。若要部署到局域网或公网，必须先改掉所有默认密码，并在 API 前加上
> 认证层（反向代理 Basic Auth 或应用内鉴权）。

## 使用流程

打开 http://localhost:5173 创建项目后，项目页顶部有进度条显示整体完成度与建议的下一步。七个标签对应写作流程的各个环节，可按顺序推进也可自由跳转：

1. **检索文献** — 输入研究需求，跨 arXiv / Semantic Scholar / Crossref 检索，勾选后导入文献库
2. **文献库** — 上传 PDF（可选）以获得全文摘要与向量索引，页面自动轮询解析进度
3. **研究热点** — 统计关键词趋势与共现，识别研究空白
4. **研究方向** — 基于文献库生成带支撑文献的方向建议，采纳其中一个
5. **文献综述** — 按主题/时间线/方法学组织，生成带引用的综述草稿
6. **论文大纲** — 选模板生成章节骨架与写作要点，可手工增删调整
7. **正文撰写** — 逐节写作，选中文字调用 AI 加工，质量检查后导出

导入的文献状态为「仅元数据」，可基于摘要参与综述与热点分析；上传 PDF 后升级为「已解析」，才会进入向量索引并支持研究方向生成与正文 RAG。

## 功能说明

### 文献获取与管理

- **多源检索**：arXiv / Semantic Scholar / Crossref 并行查询，LLM 扩展关键词提高召回，各源按其限流规则节流并做指数退避重试
- **跨源去重**：DOI 优先，无 DOI 时按标题相似度 + 年份判定；合并字段时 Crossref 元数据优先，引用数取各源最大值
- **PDF 解析**：pymupdf 按字号分布推断标题层级转 Markdown（装了 marker-pdf 会自动优先用），启发式抽取标题/作者/摘要/DOI/年份并生成 BibTeX
- **结构化摘要**：LLM 提取问题、方法、数据集、指标、结论、局限、未来工作、关键术语
- **向量索引**：按章节切块写入 Qdrant，检索结果带章节路径可溯源

### 分析与规划

- **研究热点**：关键词频次与趋势（近期窗口相对文献库最新年份而非系统时间）、共现网络、缺少交叉研究的孤立主题、文献报告的局限汇总
- **研究空白**：基于上述统计特征推断，标注数据信号类型与攻克难度
- **研究方向**：纯 Python k-means 主题聚类 + RAG，输出创新点、技术路线、可行性与新颖性评分
- **引用图谱**：Neo4j 存储引用关系，标签传播算法发现引用簇

### 写作与产出

- **综述生成**：引用簇 → LLM 命名小节 → 分节生成 → 全局重编号引用
- **大纲模板**：IMRaD / 综述 / 工科实验 / 学位论文四套，章节骨架固定，LLM 只填每节要点
- **AI 写作动作**：生成初稿、扩写、改写、润色、学术化、翻译、降重，共 7 种；相邻章节正文自动作为衔接上下文
- **质量检查**：引用越界、未被引用的参考文献、空章节、缺少引用的相关工作章节、术语不统一、跨节重复句、口语化表达、超长句、标题层级跳跃、图表编号不连续
- **多格式导出**：Markdown / LaTeX（含 `\cite` 与 thebibliography，可直接编译）/ Word / BibTeX，可选附 AI 使用声明

### 防幻觉设计

AI 生成学术内容最大的风险是编造引用。这个项目在每个生成环节都做了约束：

- 研究方向与研究空白必须引用文献库中的具体文献，越界或无引用的建议直接丢弃
- 综述每节只暴露该簇文献并从 1 局部编号，生成后校验所有 `[n]`，越界的从正文剥离并计数上报
- 正文的纯语言加工动作（润色/改写/翻译/降重）不传入文献，prompt 明确禁止添加引用标记；无可引用文献时任何 `[n]` 都判定为编造
- 大纲生成时模型返回的未知章节路径会被丢弃，章节结构始终由模板决定
- 导出时可附 AI 使用声明，正文中 AI 生成的章节有独立标记

## 配置

### LLM

`.env` 中至少配置一个 provider：

- **DeepSeek**（便宜，推荐）：`DEEPSEEK_API_KEY=sk-...`
- **OpenAI**：`OPENAI_API_KEY=sk-...`，并设 `LLM_DEFAULT=gpt-4o-mini`
- **Ollama**（本地零成本）：无需 key，设 `LLM_DEFAULT=qwen2.5:7b` 之类的本地模型名

按任务分配不同模型可省钱（摘要用小模型、方向分析用大模型）：

```
LLM_SUMMARIZE=deepseek-chat
LLM_DIRECTION=gpt-4o
```

### Embedding

默认走 Ollama 的 `bge-m3`，需先 `ollama pull bge-m3`。若用 OpenAI，设 `EMBEDDING_MODEL=text-embedding-3-large` 与 `EMBEDDING_DIM=3072`。

### 检索源（均可选）

- `SEMANTIC_SCHOLAR_API_KEY`：未鉴权限流约 1 req/s，填 key 后放宽
- `CROSSREF_MAILTO`：填联系邮箱进入 polite pool，限流更宽松

不配置也能用，只是并发受限。

## 本地开发（非 Docker）

后端：

```bash
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

后端测试（用 SQLite + 假 LLM，无需起任何容器）：

```bash
cd backend
DATABASE_URL=sqlite+aiosqlite:///./test.db pytest -q
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端类型检查与构建：

```bash
cd frontend
npx tsc --noEmit
npm run build
```

## 项目结构

```
backend/app/
├── api/           REST 路由（projects / progress / papers / search / hotspot
│                  / directions / review / outline / manuscript / graph / jobs）
├── models/        SQLAlchemy 模型
├── schemas/       Pydantic 请求响应模型
├── llm/           LLM 抽象层 + Jinja2 prompt 模板
├── parser/        PDF → Markdown、元数据抽取、逻辑块切分
├── rag/           embedding、向量库（Qdrant / 内存）、检索
├── retriever/     多源检索适配器（arXiv / S2 / Crossref）+ 跨源去重
├── graph/         引用图谱（Neo4j / 内存）+ 社区发现
├── services/      ingest / summarize / cluster / direction / search / importer
│                  / review / templates / outline / write / export / hotspot
│                  / quality / progress / jobs
└── workers/       Celery 任务（预留）

frontend/src/
├── api/           后端 API 客户端
├── components/    通用 UI 组件
└── features/      dashboard / project / progress / search / library / hotspot
                   / direction / review / outline / manuscript
```

## 测试说明

后端 341 个测试全部不依赖外部服务，`pytest -q` 数秒跑完：

- LLM 用可控的 fake provider，验证 JSON 解析、重试、缓存、任务路由
- HTTP 检索源用 httpx MockTransport 拦截，覆盖重试、限流、解析、异常隔离
- 向量库、引用图谱用内存实现，embedding 用确定性 hash 实现
- PDF 测试用 pymupdf 现场生成真实 PDF；Word 导出验证 zip 结构与内容
- 四个端到端测试分别串起 Phase 1（上传→解析→摘要→入库→方向）、Phase 2（检索→导入→图谱→综述）、Phase 3（大纲→写作→质检→导出）、Phase 4（热点→质量→进展）

## 开发路线

第一版（v0.1）四个阶段均已完成：

- **Phase 1**：PDF 解析 → 结构化摘要 → 向量索引 → 研究方向生成
- **Phase 2**：多源检索 + 跨源去重 + 引用图谱 + 综述生成
- **Phase 3**：大纲模板 + AI 写作动作 + 引用管理 + 多格式导出
- **Phase 4**：研究热点分析 + 完整质量检查 + 阶段进度引导

后续可能的方向（详见 [DESIGN.md](./DESIGN.md) 第 13 节）：

- 富文本编辑器（TipTap）替代当前的 Markdown textarea，支持公式渲染与行内引用
- 中文文献源（知网/万方）接入
- 多用户与团队协作
- VS Code 插件形态

## 已知限制

- 后端 API 无鉴权，仅适合本机单用户使用
- Neo4j、Qdrant、真实 LLM API 的调用路径只有接口契约层面的测试覆盖，未做集成测试
- 三个检索源的 HTTP 交互用 mock 覆盖，真实 API 响应格式变化不在测试范围
- 正文编辑器是 Markdown textarea，无所见即所得与公式渲染
- 术语一致性检查基于内置词表，尚不支持自定义术语表
- 查重仅检测稿件内部的跨章节重复，不与外部文献库比对

## 许可

[MIT](./LICENSE)
