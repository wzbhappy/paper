# Paper Assistant

覆盖论文撰写全流程的 AI 辅助平台：发现问题 → 检索文献 → 确定方向 → 撰写综述 → 论文大纲 → 正文撰写 → 润色检查。

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

## 已实现功能

### Phase 1：从 PDF 到研究方向

1. **上传 PDF** → 后台自动解析（pymupdf 转 Markdown，按字号推断标题层级）
2. **抽取元数据** → 标题、作者、摘要、DOI、arXiv ID、年份，生成 BibTeX
3. **结构化摘要** → LLM 提取问题/方法/数据集/指标/局限/未来工作/关键术语
4. **建立索引** → 按章节切块后写入向量库，支持语义检索与溯源
5. **生成研究方向** → 基于文献库做主题聚类 + RAG，输出带支撑文献的方向建议，含可行性/新颖性评分

### Phase 2：文献检索与综述

6. **多源检索** → arXiv / Semantic Scholar / Crossref 并行查询，LLM 扩展关键词提高召回
7. **跨源去重** → DOI 优先，无 DOI 时按标题相似度 + 年份判定，合并各源字段（Crossref 元数据优先、引用数取最大值）
8. **一键导入** → 检索结果标注「已在库中」，导入时同步写入引用图谱
9. **引用图谱** → Neo4j 存储引用关系，标签传播算法发现引用簇
10. **综述生成** → 引用簇 → LLM 命名小节 → 分节生成正文 → 全局重编号引用 → 导出 Markdown / BibTeX

### Phase 3：大纲与正文撰写

11. **大纲生成** → 4 套模板（IMRaD / 综述 / 工科实验 / 学位论文）固定章节骨架，LLM 只填每节要点，结合已采纳的研究方向定制
12. **章节编辑** → 手工增删章节、重命名（自动同步子章节路径）、调整要点与篇幅预估
13. **AI 写作动作** → 生成初稿、扩写、改写、润色、学术化、翻译、降重，共 7 种；相邻章节正文自动作为衔接上下文
14. **引用管理** → 正文引用按节局部编号，导出时全局重编号；质量检查报告引用越界、未被引用的参考文献、空章节
15. **多格式导出** → Markdown / LaTeX（含 `\cite` 与 thebibliography）/ Word / BibTeX，可选附 AI 使用声明

防幻觉设计贯穿三期：
- 研究方向必须引用文献库中的具体文献，越界或无引用的建议会被丢弃
- 综述每节只暴露该簇文献并局部编号，生成后校验所有 `[n]` 引用，越界的直接从正文剥离并计数上报
- 正文写作中纯语言加工动作（润色/改写/翻译/降重）不传入文献，prompt 明确禁止添加引用标记；无可引用文献时任何 `[n]` 都会被剥离
- 大纲生成时模型返回的未知章节路径会被丢弃，章节结构始终由模板决定

### 使用流程

1. 打开 http://localhost:5173，创建项目
2.「检索文献」标签 → 输入研究需求 → 勾选结果 → 导入文献库
3.「文献库」标签 → 上传 PDF（可选，获得全文摘要与向量索引），页面自动轮询解析进度
4.「研究方向」标签 → 可填写初步意向 → 生成 → 采纳其中一个
5.「文献综述」标签 → 选组织方式（主题/时间线/方法学）→ 生成 → 可在线编辑并下载 Markdown
6.「论文大纲」标签 → 选模板 → 生成 → 按需增删章节、调整要点
7.「正文撰写」标签 → 逐节写作，选中文字可调用 AI 加工 → 质量检查 → 导出

导入的文献状态为「仅元数据」，可基于摘要参与综述；上传 PDF 后升级为「已解析」，才会进入向量索引并支持研究方向生成。

### LLM 配置

`.env` 中至少配置一个 provider。三种方式：

- **DeepSeek**（便宜，推荐）：`DEEPSEEK_API_KEY=sk-...`
- **OpenAI**：`OPENAI_API_KEY=sk-...`，并设 `LLM_DEFAULT=gpt-4o-mini`
- **Ollama**（本地零成本）：无需 key，设 `LLM_DEFAULT=qwen2.5:7b` 之类的本地模型名

Embedding 默认走 Ollama 的 `bge-m3`，需先 `ollama pull bge-m3`。若用 OpenAI，设 `EMBEDDING_MODEL=text-embedding-3-large` 与 `EMBEDDING_DIM=3072`。

按任务分配不同模型可省钱（摘要用小模型、方向分析用大模型）：

```
LLM_SUMMARIZE=deepseek-chat
LLM_DIRECTION=gpt-4o
```

### 检索源配置（均可选）

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
├── api/           REST 路由（projects / papers / search / directions / review
│                  / outline / manuscript / graph / jobs）
├── models/        SQLAlchemy 模型
├── schemas/       Pydantic 请求响应模型
├── llm/           LLM 抽象层 + Jinja2 prompt 模板
├── parser/        PDF → Markdown、元数据抽取、逻辑块切分
├── rag/           embedding、向量库（Qdrant / 内存）、检索
├── retriever/     多源检索适配器（arXiv / S2 / Crossref）+ 跨源去重
├── graph/         引用图谱（Neo4j / 内存）+ 社区发现
├── services/      ingest / summarize / cluster / direction / search / importer
│                  / review / templates / outline / write / export / jobs
└── workers/       Celery 任务（Phase 4）

frontend/src/
├── api/           后端 API 客户端
├── components/    通用 UI 组件
└── features/      dashboard / project / search / library / direction / review
                   / outline / manuscript
```

## 测试说明

后端 270 个测试全部不依赖外部服务：

- LLM 用可控的 fake provider，验证 JSON 解析、重试、缓存、路由
- HTTP 检索源用 httpx MockTransport 拦截，覆盖重试、限流、解析、异常隔离
- 向量库、引用图谱用内存实现，embedding 用确定性 hash 实现
- PDF 测试用 pymupdf 现场生成真实 PDF；Word 导出验证 zip 结构与内容
- 三个端到端测试分别串起 Phase 1（上传→解析→摘要→入库→方向）、Phase 2（检索→导入→图谱→综述）、Phase 3（大纲→写作→质检→导出）

## 开发路线

- **Phase 1**（已完成）：PDF → 摘要 → 研究方向
- **Phase 2**（已完成）：多源检索 + 跨源去重 + 引用图谱 + 综述生成
- **Phase 3**（已完成）：大纲模板 + AI 写作动作 + 引用管理 + 多格式导出
- **Phase 4**：研究热点分析、更完整的质量检查（语法/术语一致性/查重）、富文本编辑器

详见 [DESIGN.md](./DESIGN.md) 第 10 节。
