<div align="center">

<h1><img src="images/logo.png" alt="RpaClaw" width="36" style="vertical-align: middle;" />&nbsp;RpaClaw</h1>

**[English](README.md)** | **[中文](README_zh.md)**

</div>

RpaClaw 是一款隐私优先的个人助手，具备 RPA（流程自动化）能力，基于 [LangChain DeepAgents](https://github.com/langchain-ai/deepagents) 架构与 [AIO Sandbox](https://github.com/agent-infra/sandbox) 基础设施构建。

<div align="center">

*RPA 录制与回放 · 自适应执行 · 多格式生成 · 完全本地化 · 隐私优先*

[![Tools](https://img.shields.io/badge/Tools-e74c3c.svg)](./Tools) [![Skills](https://img.shields.io/badge/Skills-f39c12.svg)](./Skills) [![Frontend](https://img.shields.io/badge/Frontend-2ecc71.svg)](./RpaClaw/frontend) [![Backend](https://img.shields.io/badge/Backend-3498db.svg)](./RpaClaw/backend) [![Scheduler](https://img.shields.io/badge/Scheduler-9b59b6.svg)](./RpaClaw/task-service) [![Sandbox](https://img.shields.io/badge/Sandbox-1abc9c.svg)](./RpaClaw/sandbox) [![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

[产品优势](#why-rpaclaw) · [快速开始](#quick-start) · [本地配置](#local-setup) · [免费额度](#free-api-credits) · [工具与技能](#tools-skills) · [实用功能](#practical-features) · [项目结构](#project-structure) · [常用命令](#commands) · [致谢](#acknowledgements)

</div>

---

<a id="why-rpaclaw"></a>

## ✨ 产品优势

<table>
<tr>
<td width="37%" valign="top">

### 🤖 RPA 录制与自动化

录制浏览器交互并自动生成 **Playwright 脚本**。RpaClaw 捕获您的操作，生成智能定位器，创建可复用的自动化技能。支持 Docker 沙箱模式和本地模式，灵活部署。

</td>
<td width="32%" valign="top">

### 🔒 安全至上

完全运行在 **Docker 容器**内，沙箱隔离执行。Agent 无法访问您的宿主系统或个人文件。所有数据仅保存在本地 `./workspace` 目录——不会上传到任何外部服务器。可以放心部署使用。

</td>
<td width="31%" valign="top">

### 🚀 开箱即用

无需繁琐配置。使用预构建 Docker 镜像**一条命令**即可启动。无论您是自动化工作流还是构建 AI Agent，都能立即上手。

</td>
</tr>
</table>

---

<a id="quick-start"></a>

## 📦 快速开始

### Docker 部署（推荐）

#### 前置要求

- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/)（Docker Desktop 已包含 Compose）
- 建议系统内存 ≥ 8 GB

#### 安装与启动

**1. 克隆项目**

```bash
cd RpaClaw
```

**2. 拉取预构建镜像并启动**

```bash
docker compose -f docker-compose-release.yml up -d
```

> 直接拉取预构建镜像，无需本地编译，几分钟即可完成。

**3. 打开浏览器访问**

```
http://localhost:5173
```

**4. 登录**

默认管理员用户名：`admin`

> ⚠️ 首次登录时请设置您的密码。

---

### 🛠️ 开发者 —— 从源码构建

```bash
docker compose up -d --build
```

> 从源码构建所有镜像，适合需要修改代码的开发者。

---

<a id="local-setup"></a>

## 🖥️ 本地开发配置

适合希望在不使用 Docker 的情况下本地运行服务的开发者：

### 前置要求

- Python 3.13+
- Node.js 18+
- MongoDB（可选，用于数据库功能）
- Redis（可选，用于任务调度）

### 后端配置

**1. 进入后端目录**

```bash
cd RpaClaw/backend
```

**2. 创建并配置环境**

```bash
cp .env.example .env
```

**3. 编辑 `.env` 文件配置：**

```bash
# 存储模式：'local' 或 'docker'
STORAGE_BACKEND=local

# MongoDB（可选 - 仅用于用户管理和会话）
MONGODB_HOST=localhost
MONGODB_PORT=27017
MONGODB_USER=
MONGODB_PASSWORD=

# 沙箱（用于 Docker 模式 RPA）
SANDBOX_MCP_URL=http://localhost:18080/mcp

# 统一根目录：workspace、external_skills、builtin_skills、data 自动派生为子目录
RPA_CLAW_HOME=
```

**4. 安装依赖并运行**

```bash
# 使用 uv（推荐）
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# 或使用 pip
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 前端配置

**1. 进入前端目录**

```bash
cd RpaClaw/frontend
```

**2. 安装依赖**

```bash
npm install
```

**3. 运行开发服务器**

```bash
npm run dev
```

**4. 打开浏览器**

```
http://localhost:5173
```

### RPA 模式

#### 本地模式（无需 Docker）

在 `.env` 中设置 `STORAGE_BACKEND=local`。RPA 录制使用 CDP（Chrome DevTools Protocol）屏幕投射代替 VNC。Playwright 直接在宿主机上运行。

**优势：**
- 无需 Docker 沙箱
- 性能更快
- 直接访问宿主浏览器

**限制：**
- 隔离性较弱
- 需要在宿主机上安装 Playwright

#### Docker 模式（沙箱隔离）

在 `.env` 中设置 `STORAGE_BACKEND=docker`。需要沙箱容器运行。RPA 使用 VNC 显示。

**优势：**
- 完全隔离
- 环境一致
- 更安全的执行

**要求：**
- 沙箱容器必须运行
- 可访问 `SANDBOX_MCP_URL`

#### Node RPA Engine 妯″紡

褰撻渶瑕佽褰曞埗銆佸洖鏀惧拰浠ｇ爜鐢熸垚閮界敱浠撳簱鍐呯殑 Node `rpa-engine` 鎺ユ墜鏃讹紝璇疯缃?`RPA_ENGINE_MODE=node`锛岃€屼笉鏄户缁娇鐢ㄦ棫鐨?Python 褰曞埗/鎵ц閾捐矾銆?

鏈湴寮€鍙戝缓璁厛鍚姩 `rpa-engine`锛?

```bash
cd RpaClaw/rpa-engine
npm install
npm run dev
```

鐒跺悗鍦ㄥ悗绔幆澧冧腑鎸囧悜瀹冿細

```bash
RPA_ENGINE_MODE=node
RPA_ENGINE_BASE_URL=http://127.0.0.1:3310
```

瀵逛簬 Docker 寮€鍙戞垨 release compose锛屽悗绔粯璁や細浣跨敤 `http://host.docker.internal:3310` 浣滀负 Node-engine 鍩哄湴鍧€锛岃繖鏍峰彲浠ヨ `rpa-engine` 杩愯鍦ㄥ涓绘満涓婏紝鑰屽悗绔繚鎸佸湪瀹瑰櫒鍐呫€?

浜戠妯″紡涓嬶紝璇峰皢 `rpa-engine` 鐙珛閮ㄧ讲锛屽苟鎶?`RPA_ENGINE_BASE_URL` 鎸囧悜鍙揪鐨勬湇鍔″湴鍧€銆傚鏋滈渶瑕佸唴缃綉鍏冲仛璁よ瘉锛屽彲浠ュ悓鏃堕厤缃?`RPA_ENGINE_AUTH_TOKEN`銆?

---

<a id="tools-skills"></a>

## 🔧 工具与技能体系

### 🧪 1,900+ 内置工具

RpaClaw 集成了 **ToolUniverse**，提供 1,900+ 跨多个领域的工具，用于自动化、数据处理和 AI 驱动的工作流。


### 🛠️ 四层工具架构

| 层级 | 说明 | 示例 |
|---|---|---|
| 🔧 **内置工具** | 核心搜索与爬取能力 | `web_search`、`web_crawl` |
| 🧪 **ToolUniverse** | 1,900+ 科研工具，开箱即用 | UniProt、OpenTargets、FAERS、PDB、ADMET 等 |
| 📦 **沙箱工具** | 文件操作与代码执行 | `read_file`、`write_file`、`execute`、`shell` |
| 🛠️ **自定义 @tool** | 用户自定义 Python 函数，放入 `Tools/` 目录自动热加载 | 您自己的工具 |

### 🎨 自定义工具

RpaClaw 支持便捷的工具扩展：

- **自然语言创建** — 在对话中描述您的需求，Agent 会自动创建、测试并保存新工具。
- **手动挂载** — 将包含 `@tool` 装饰器的 Python 文件放入 `Tools/` 目录，系统自动检测并热加载，无需重启。

### 🧠 技能体系

技能是**结构化的指令文档（SKILL.md）**，用于引导 Agent 完成复杂的多步骤工作流。与工具（可执行代码）不同，技能充当 Agent 的"操作手册"——定义策略、规则和最佳实践。

#### 内置技能

| 技能 | 用途 |
|---|---|
| 🤖 **RPA 录制** | 录制浏览器交互并生成 Playwright 自动化脚本 |
| 📄 **pdf** | 读取、创建、合并、拆分、OCR 以及生成 PDF 文档 |
| 📝 **docx** | 创建和编辑 Word 文档，支持格式、表格和图表 |
| 📊 **pptx** | 生成和编辑 PowerPoint 演示文稿 |
| 📈 **xlsx** | 创建和处理 Excel 电子表格，处理 CSV/TSV 数据 |
| 🛠️ **tool-creator** | 创建自定义 @tool 工具（编写 → 测试 → 保存） |
| 📝 **skill-creator** | 创建和优化技能，支持迭代工作流 |
| 🔍 **find-skills** | 搜索和安装社区技能 |
| 🧪 **tooluniverse** | 访问 1,900+ 内置工具 |

#### 多格式文档生成

RpaClaw 可生成 **4 种格式**的专业文档：

| 格式 | 特性 |
|---|---|
| **PDF** | 封面、目录、图表、引用、参考文献 |
| **DOCX** | 封面、目录、表格、图片、Word 原生排版 |
| **PPTX** | 幻灯片标题、要点列表、图片、演讲者备注 |
| **XLSX** | 数据表格、图表、多工作表、CSV/TSV 导出 |

#### 自定义技能

- **自然语言创建** — 在对话中描述您的工作流，Agent 会自动起草、测试并保存新技能。
- **手动安装** — 将包含 `SKILL.md` 文件的文件夹放入 `Skills/` 目录，Agent 会根据用户意图自动匹配并加载相关技能。
- **社区生态** — 通过内置的 `find-skills` 功能，从开源社区发现和安装技能。


---

<a id="practical-features"></a>

## 💡 实用功能

| 功能 | 说明 |
|---|---|
| 🤖 **RPA 录制与回放** | 录制浏览器交互并生成可复用的 Playwright 自动化脚本。支持 Docker 沙箱模式和本地模式。 |
| ⏰ **定时任务** | 支持 cron 风格的定时或一次性任务调度。结果通过飞书或站内通知推送。 |
| 📁 **文件管理** | 内置文件面板，可浏览、预览和下载会话中生成的工作区文件。 |
| 📊 **资源监测** | 实时仪表盘展示大模型资源消耗和服务健康状态。 |

---

<a id="project-structure"></a>

## 📂 项目结构

```
RpaClaw/
├── docker-compose.yml              # 开发环境编排
├── docker-compose-release.yml      # 预构建镜像编排
├── images/                         # 静态资源（logo、截图）
├── Tools/                          # 自定义工具（热加载）
├── Skills/                         # 用户与社区技能包
├── workspace/                      # 🔒 本地工作目录（数据保持本地）
└── RpaClaw/
    ├── backend/                    # FastAPI 后端
    │   ├── deepagent/              # AI Agent 核心引擎（LangGraph）
    │   ├── builtin_skills/         # 内置技能（pdf、docx、pptx、xlsx 等）
    │   ├── rpa/                    # RPA 录制/回放引擎
    │   ├── route/                  # REST API 路由
    │   ├── im/                     # IM 集成（飞书/Lark）
    │   ├── mongodb/                # 数据库访问层
    │   └── user/                   # 用户管理
    ├── frontend/                   # Vue 3 + Tailwind 前端
    ├── sandbox/                    # 隔离代码执行环境
    ├── task-service/               # 定时任务服务
```

---

<a id="commands"></a>

## 🧑‍💻 常用命令

```bash
# 拉取预构建镜像并启动（普通用户推荐）
docker compose -f docker-compose-release.yml up -d

# 从源码构建并启动（开发者）
docker compose up -d --build

# 日常启动 —— 快速拉起，无需重新构建
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志（-f 持续跟踪）
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f sandbox

# 重启服务
docker compose restart backend

# 停止所有服务
docker compose down

# 停止单个服务
docker compose stop backend
```

---

## 🗑️ 卸载

RpaClaw 完全基于 Docker 构建，卸载非常简洁，**不会对宿主机产生任何不良影响**。

```bash
# 停止并移除所有容器
docker compose down

# （可选）删除已下载的镜像以释放磁盘空间
docker compose down --rmi all --volumes
```

然后直接删除项目文件夹即可：

```bash
rm -rf /path/to/RpaClaw
```

完成。无任何残留文件，无注册表项，无系统级改动。

---

## 📄 开源协议

[MIT License](LICENSE)

---

<a id="acknowledgements"></a>

## 🙏 致谢

RpaClaw 基于优秀的开源项目构建。我们在此向以下项目表示衷心的感谢：

- **[LangChain DeepAgents](https://github.com/langchain-ai/deepagents)** — 基于 LangChain 和 LangGraph 构建的一站式 Agent 框架。RpaClaw 的核心 Agent 引擎由 DeepAgents 架构驱动，提供任务规划、文件系统访问、子 Agent 委托和智能上下文管理等开箱即用的能力。

- **[AIO Sandbox](https://github.com/agent-infra/sandbox)** — 集成了浏览器、Shell、文件系统和 MCP 操作的全能 Agent 沙箱环境。RpaClaw 依托 AIO Sandbox 提供安全、隔离的代码执行和统一文件系统。

- **[ToolUniverse](https://github.com/ZitnikLab/ToolUniverse)** — 由哈佛大学 Zitnik Lab 开发的 1,900+ 科研工具统一生态系统。ToolUniverse 为 RpaClaw 提供了跨学科的科研能力，涵盖药物发现、基因组学、天文学、地球科学等多个领域。

- **[SearXNG](https://github.com/searxng/searxng)** — 注重隐私保护的开源元搜索引擎。RpaClaw 的 `web_search` 工具以 SearXNG 为核心，聚合多个搜索引擎的结果，无任何用户追踪。

- **[Crawl4AI](https://github.com/unclecode/crawl4ai)** — 面向 LLM 的开源网页爬取工具。RpaClaw 的 `web_crawl` 工具由 Crawl4AI 驱动，能够从网页中智能提取内容，服务于科研分析。

---
