# 部署指南

本项目是 FastAPI + React + 多个数据存储的架构，无法 100% 部署到单一平台。
推荐采用混合部署：**前端上静态托管平台（Cloudflare Pages 或 Vercel），后端上 Fly.io，各数据服务用托管免费层**。

- 方案一（[Cloudflare Pages](#第三步部署前端到-cloudflare-pages)）：前端托管在 Cloudflare
- 方案二（[Vercel](#前端托管改用-vercel)）：前端托管在 Vercel，其余与方案一完全一致

两种方案的后端、数据库、向量库、引用图谱配置相同，可互换。

## 架构

```
                    前端 SPA（Cloudflare Pages 或 Vercel）
                           │
                           │ HTTPS
                           ▼
                    Fly.io (FastAPI 后端)
                    │   │   │   │
            ┌───────┘   │   │   └────────┐
            ▼           ▼   ▼            ▼
         Neon      Neo4j Aura  Qdrant Cloud  (本地卷)
        Postgres              Vector DB    (PDF 文件)
```

| 组件 | 托管 | 免费层 | 用途 |
|---|---|---|---|
| 前端 | Cloudflare Pages **或** Vercel | 无限请求 | 静态托管 + 自动构建 |
| 后端 | Fly.io | 3 个 shared-cpu-1x 机器 | Docker 运行 FastAPI |
| Postgres | Neon | 0.5 GB | 项目、文献、大纲数据 |
| Neo4j | Neo4j Aura Free | 200K 节点 | 引用图谱（综述分簇） |
| Qdrant | Qdrant Cloud | 1 GB | 文献向量索引 |
| PDF 文件 | Fly.io volume | 3 GB | 上传的 PDF 持久存储 |

> **重要**：后端 API 无鉴权（见 README 安全提醒）。生产部署后必须改掉所有默认密码，
> 并在 API 前加认证层（Cloudflare Access / 反向代理 Basic Auth / 应用内鉴权）。

## 前置准备

- 账号：[GitHub](https://github.com)（代码已在此）、[Fly.io](https://fly.io)、
  [Cloudflare](https://cloudflare.com) **或** [Vercel](https://vercel.com)、[Neon](https://neon.tech)、
  [Neo4j Aura](https://neo4j.com/product/auradb/)、[Qdrant Cloud](https://cloud.qdrant.io)
- 命令行：`fly` CLI（[安装](https://fly.io/docs/hands-on/install-flyctl/)）、`git`、
  Vercel CLI（可选，`npm i -g vercel`）
- 把项目 fork 到你自己的 GitHub，因为 Cloudflare Pages / Vercel 要从你的仓库连

---

## 第一步：创建托管数据服务

### 1.1 Neon Postgres

1. 在 [Neon 控制台](https://console.neon.tech) 新建项目，选离 Fly 区域近的 region（如 `Singapore`）
2. 复制连接串，形如 `postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`
3. 记下，待会儿作为 `DATABASE_URL`

### 1.2 Neo4j Aura Free

1. 在 [Neo4j Aura](https://console.neo4j.io) 新建实例，选 Free
2. **首次创建时会显示一次性密码，务必保存**（关闭后无法再查看）
3. 连接串形如 `neo4j+s://neo4j.databases.neo4j.io:7687`，记为 `NEO4J_URL`，密码为 `NEO4J_PASSWORD`

> Aura Free 长时间不用会自动暂停。综述生成需要图谱，暂停时综述会降级为单一小节（不影响其他功能）。

### 1.3 Qdrant Cloud

1. 在 [Qdrant Cloud](https://cloud.qdrant.io) 新建 cluster，选 Free tier
2. 创建 API Key，记下 cluster URL（形如 `https://xxx.aws.cloud.qdrant.io:6333`）和 key
3. 待会儿设为 `QDRANT_URL`

> 如果不想用 Qdrant Cloud，后端没有配置 `QDRANT_URL` 时会……不，当前实现默认连本地 Qdrant。
> 生产环境必须配 `QDRANT_URL` 指向 Cloud 实例，否则后端启动会连不上 localhost。详见文末「降级方案」。

---

## 第二步：部署后端到 Fly.io

### 2.1 安装 fly CLI 并登录

```bash
# macOS:   brew install flyctl
# Windows: pwsh -Command "iwr https://fly.io/install.ps1 -UseBasicParsing | iex"
# Linux:   curl -L https://fly.io/install.sh | sh
fly auth login
```

### 2.2 创建应用与持久卷

在项目根目录执行：

```bash
fly launch --no-deploy --copy-config --name paper-assistant-backend --region hkg
fly volumes create paper_data --region hkg
```

- `--name` 用你想要的子域名（最终后端 URL 是 `https://<你的名字>.fly.dev`）
- `--region` 选离你近的（`hkg` 香港 / `nrt` 东京 / `sin` 新加坡 / `lax` 洛杉矶等）
- `fly.toml` 已在仓库中，`--copy-config` 会直接使用

> 如果 `fly launch` 提示要改 app 名，按提示改 fly.toml 里的 `app = "..."` 保持一致。

### 2.3 注入机密

把所有需要保密的值设为 Fly secrets（不会写进代码仓库）：

```bash
fly secrets set \
  DATABASE_URL="postgresql://...?sslmode=require" \
  NEO4J_URL="neo4j+s://..." \
  NEO4J_USER="neo4j" \
  NEO4J_PASSWORD="你的Aura密码" \
  QDRANT_URL="https://xxx.aws.cloud.qdrant.io:6333" \
  CORS_ORIGINS="https://你的项目名.pages.dev" \
  DEEPSEEK_API_KEY="sk-..." \
  EMBEDDING_MODEL="bge-m3" \
  EMBEDDING_DIM=1024 \
  QDRANT_COLLECTION="paper_chunks"
```

说明：
- `CORS_ORIGINS` 填你 Cloudflare Pages 的域名（下一步创建后填，可稍后回来改）
- LLM 至少配一个：`DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`（详见 README「LLM 配置」）
- Embedding：用 Ollama 的 `bge-m3` 时 Fly 机器连不到你本机，必须改用 OpenAI embedding
  （设 `EMBEDDING_MODEL=text-embedding-3-large` 与 `EMBEDDING_DIM=3072`），或者把 Ollama 也部署成可访问的服务
- 检索源可选：`SEMANTIC_SCHOLAR_API_KEY`、`CROSSREF_MAILTO`

### 2.4 部署

```bash
fly deploy
```

### 2.5 验证

```bash
fly open
# 浏览器打开 https://<你的名字>.fly.dev/docs 看 OpenAPI 文档
curl https://<你的名字>.fly.dev/health
# 应返回 {"status":"ok"}
```

常见问题：
- 启动失败看日志：`fly logs`
- 数据库连不上：检查 `DATABASE_URL` 是否带 `?sslmode=require`（Neon 必需）
- Qdrant 连不上：确认 `QDRANT_URL` 和集群是否在 Cloud 实例的允许列表

---

## 第三步：部署前端到 Cloudflare Pages

### 3.1 创建 Pages 项目（连 GitHub）

1. 进入 [Cloudflare Dashboard](https://dash.cloudflare.com) → Workers & Pages → Create → Pages → Connect to Git
2. 选择你的 GitHub 仓库 `paper`
3. 构建配置：
   - **Framework preset**: `Vite`
   - **Build command**: `cd frontend && npm install && npm run build`
   - **Build output directory**: `frontend/dist`
   - **Root directory**: 留空（项目根）
4. **Environment variables**（关键）：
   - `VITE_API_BASE` = `https://<你的后端名>.fly.dev/api/v1`
5. 点 Save and Deploy

首次部署后，前端会得到一个 `https://<项目名>.pages.dev` 域名。

> 前端用了 `BrowserRouter`，深链接（如 `/projects/:id`）已通过 `frontend/public/_redirects`
> 在构建时自动带进 `dist/`（`/* /index.html 200`），Cloudflare Pages 会据此做 SPA 回退，无需额外配置。

### 3.2 回填 CORS

回到第二步，把后端的 `CORS_ORIGINS` 更新为你的 Pages 域名：

```bash
fly secrets set CORS_ORIGINS="https://<项目名>.pages.dev"
```

> 如果后续给 Pages 绑了自定义域名，要同时把自定义域名加进 `CORS_ORIGINS`
> （多个用逗号分隔，如 `https://a.pages.dev,https://b.com`）。

### 3.3 验证

打开 `https://<项目名>.pages.dev`，创建项目、上传 PDF 应能正常工作。
如果创建项目报 CORS 错误，检查 `CORS_ORIGINS` 是否与浏览器地址栏完全一致（含 https、不含末尾斜杠）。

---

## 前端托管改用 Vercel

后端、数据库、向量库、引用图谱的配置与方案一完全相同（见第一、二步）。
仅把静态前端从 Cloudflare Pages 换成 Vercel，构建配置与 `VITE_API_BASE` 一致。

> Vercel 不能托管 FastAPI 后端：它的函数运行环境是 Node.js，Python 函数虽有，但本项目
> 依赖 pymupdf（C 扩展）、asyncpg、neo4j、qdrant-client 等原生/重型包，体积与运行限制都
> 不适合上 Vercel。所以后端仍然走 Fly.io（方案一第二步），Vercel 只放前端。

### V.1 通过 Dashboard 连接 GitHub

1. 进入 [Vercel Dashboard](https://vercel.com/dashboard) → Add New → Project → 导入你的 GitHub 仓库 `paper`
2. 框架预设选 `Vite`（Vercel 会自动识别，但确认一下）
3. 构建配置：
   - **Build Command**: `cd frontend && npm install && npm run build`
   - **Output Directory**: `frontend/dist`
   - **Root Directory**: 留空（项目根），不要设成 `frontend`
4. **Environment Variables**（关键）：
   - `VITE_API_BASE` = `https://<你的后端名>.fly.dev/api/v1`
5. Deploy

部署后会得到 `https://<项目名>.vercel.app`，每次向仓库 push 还会生成预览环境。

### V.2 通过 Vercel CLI（可选）

```bash
npm i -g vercel
cd 项目根
vercel login
vercel                     # 首次：按提示连仓库、设框架为 Vite
# 设环境变量后重新部署：
vercel env add VITE_API_BASE
vercel --prod
```

`vercel env add` 会交互式让你填值，等价于 Dashboard 里的 Environment Variables。

### V.3 回填 CORS 并验证

与 Cloudflare 方案一样，把后端 `CORS_ORIGINS` 更新为 Vercel 域名：

```bash
fly secrets set CORS_ORIGINS="https://<项目名>.vercel.app"
```

打开 `https://<项目名>.vercel.app`，创建项目、上传 PDF 应正常工作；
CORS 报错时检查 `CORS_ORIGINS` 与浏览器地址栏是否完全一致。

### V.4（实验性）整站上 Vercel

不推荐，仅作可行性说明：理论上可把 FastAPI 用 [Mangum](https://github.com/jordaneremieff/mangum)
包成 ASGI handler，作为 Vercel Python 函数暴露 `/api/*`，从而省掉 Fly.io。但本项目会撞到：

- Vercel Python 函数源码 + 依赖上限约 250 MB，pymupdf + fastapi + sqlalchemy + 各种客户端极易超限
- 部分原生包（asyncpg 等）需预编译 wheel，冷启动慢且不一定能装上
- Neo4j / Qdrant / Postgres 仍需外部托管（Neon / Aura / Qdrant Cloud），省掉的只是 Fly.io 这一层
- 本地卷不可用在 Vercel，上传的 PDF 必须改存到 Vercel Blob / R2 / S3，需改后端存储代码

综上，整站上 Vercel 收益有限而改动很大，建议仍用 Fly.io 跑后端、Vercel 只放前端。

---

## 第四步（可选）：自定义域名

- **Pages**：Cloudflare Pages 项目设置 → Custom domains → 添加域名（需域名在 Cloudflare DNS 或可改 NS）
- **Fly**：`fly certs add your-domain.com`，按提示加 CNAME 记录
- 加完域名后记得更新 `CORS_ORIGINS` 与 `VITE_API_BASE`，重新 `fly deploy` / Pages 重新构建

---

## 降级方案

当某个数据服务不可用时，后端仍可部分工作：

| 服务不可用 | 影响 | 处理 |
|---|---|---|
| Neo4j | 综述退化为单一小节（社区发现跳过） | `/review/generate` 仍可生成，`/graph/stats` 返回 `available=false` |
| Qdrant | 向量检索与研究方向生成失效 | 启动仍正常，但 `/directions/generate` 无法基于全文 RAG |
| Neon | 后端无法启动（核心依赖） | 必须可用 |

Qdrant 不可用时若想保持研究方向功能，可临时不设 `QDRANT_URL` 并修改代码用内存 store（仅适合单机测试，重启即丢）。

---

## 成本估算（免费层）

| 服务 | 免费额度 | 本项目典型占用 |
|---|---|---|
| Cloudflare Pages | 无限带宽 | 静态资源 ~200 KB |
| Fly.io | 3 个机器 × 256MB | 通常 1 个机器够用，空闲自动停 |
| Neon | 0.5 GB | 项目与文献元数据，几十篇文献 < 10 MB |
| Neo4j Aura | 200K 节点 | 引用图谱通常几百节点 |
| Qdrant Cloud | 1 GB | 每篇文献切几十块，几百块 × 1024 维 ≈ 几百 MB |

**个人使用每月 $0**。超出免费层（如文献超 1000 篇、向量超 1 GB）时：
- Qdrant Cloud 升级（$25/月起），或自托管 Qdrant（Fly 上再起一个 Docker 服务）
- Neon 升级 Scale-out plan
- Fly 升级到 non-stop 机器或更大规格

---

## 故障排查速查

| 症状 | 排查 |
|---|---|
| Pages 部署成功但页面空白 | 检查 `VITE_API_BASE` 是否设对；浏览器控制台看 fetch 报错 |
| 创建项目 4xx/5xx | `fly logs` 看后端错误；检查 DB 连接 |
| 前端报 CORS 错误 | `CORS_ORIGINS` 与浏览器地址栏是否完全一致 |
| 上传 PDF 后解析失败 | `fly ssh console` 进容器看 `/data/papers`；`fly logs` 看解析错误 |
| 综述不分节 | Neo4j 是否暂停（Aura Free 长时间不用会睡）；重启 Aura |
| 方向生成报错 | Qdrant 是否可达；Embedding 配置是否与维度一致 |

---

## 相关文件

- `Dockerfile` — 生产后端镜像（不带 --reload）
- `backend/Dockerfile` — 开发镜像（带 --reload，docker-compose 用）
- `.dockerignore` — 后端镜像构建排除项
- `fly.toml` — Fly.io 部署配置
- `frontend/.env.example` — 前端构建环境变量
- `docker-compose.yml` — 本地全栈开发环境（详见 README）
