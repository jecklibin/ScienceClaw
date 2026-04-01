# CLAUDE.md

## Project Overview

ScienceClaw is a privacy-first personal research assistant powered by LangChain DeepAgents. It provides 1,900+ built-in scientific tools, multi-format document generation, a sandboxed code execution environment, and an RPA skill recording system. All data stays local.

## Tech Stack

- **Backend**: FastAPI (Python 3.13), LangGraph + DeepAgents, Pydantic v2, Motor (async MongoDB)
- **Frontend**: Vue 3 + TypeScript, Vite, Tailwind CSS, Reka UI
- **Database**: MongoDB
- **Cache/Queue**: Redis + Celery
- **Sandbox**: Docker container (AIO Sandbox) with Xvfb, x11vnc, Playwright, Python 3.12
- **Search**: SearXNG + Crawl4AI via websearch microservice

## Directory Structure

```
ScienceClaw/
├── docker-compose.yml              # Dev compose (build from source)
├── docker-compose-release.yml      # Production compose (pre-built images)
├── Skills/                         # User skill packages (mounted to /app/Skills in Docker)
├── Tools/                          # Custom user tools (hot-reload)
└── ScienceClaw/
    ├── backend/                    # FastAPI backend
    │   ├── main.py                 # Entry point, registers all routers
    │   ├── config.py               # Pydantic BaseSettings, reads from .env
    │   ├── route/                  # API routes (auth, sessions, chat, file, rpa, etc.)
    │   ├── deepagent/              # Core LangGraph agent engine
    │   ├── rpa/                    # RPA recording/playback (manager, generator, executor)
    │   ├── builtin_skills/         # 9 built-in skills (pdf, docx, pptx, xlsx, etc.)
    │   ├── mongodb/                # Database access layer
    │   └── im/                     # IM integrations (Feishu/Lark)
    ├── frontend/                   # Vue 3 SPA
    │   └── src/
    │       ├── main.ts             # App entry, router config
    │       ├── api/                # API client (apiClient with auth token)
    │       ├── pages/              # Page components (Home, Chat, Skills, Tools, Tasks, rpa/)
    │       ├── components/         # Reusable components (ui/, filePreviews/, settings/)
    │       ├── composables/        # Vue composables
    │       ├── locales/            # i18n (en.ts, zh.ts)
    │       └── utils/              # Utility functions
    ├── sandbox/                    # Isolated execution environment
    └── task-service/               # Celery-based scheduled task service
```

## Services & Ports

| Service | Container Port | Host Port | Purpose |
|---------|---------------|-----------|---------|
| Frontend | 5173 | 5173 | Vue dev server / web UI |
| Backend | 8000 | 12001 | FastAPI REST API |
| Sandbox | 8080 | 18080 | Code execution (MCP protocol) |
| Sandbox VNC | 6080 | 16080 | VNC WebSocket (for RPA) |
| MongoDB | 27017 | 27014 | Database |
| Task Service | 8001 | 12002 | Scheduled tasks |
| Websearch | 8068 | 8068 | SearXNG + Crawl4AI |

## Running Locally

### Docker (recommended)
```bash
docker compose up -d --build          # Dev build
docker compose -f docker-compose-release.yml up -d  # Pre-built images
```

### Local development
```bash
# Backend
cd ScienceClaw/backend
cp .env.example .env  # Fill in API keys
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend
cd ScienceClaw/frontend
npm install
npm run dev
```

Default login: `admin` / `admin123`

## Backend API

All routes are prefixed with `/api/v1`. Key routers registered in `main.py`:

- `/auth` — Login, register, password management
- `/sessions` — Session CRUD, skills listing, file operations
- `/chat` — Streaming chat with LLM agents
- `/rpa` — RPA recording, testing, skill export
- `/file` — File upload/download
- `/models` — LLM model configuration
- `/tools`, `/tooluniverse` — Tool discovery (1,900+ scientific tools)
- `/task-settings` — Scheduled task configuration
- `/im` — Feishu/Lark webhook integration

Health check: `GET /health`, Readiness: `GET /ready`

## Frontend Routing

Routes defined in `src/main.ts`:

- `/chat` — Main layout (requires auth)
  - `/` — Home page (session list)
  - `/:sessionId` — Chat conversation
  - `/skills` — Skills browser
  - `/tools` — Tools browser
  - `/tasks` — Task scheduler
- `/rpa/recorder` — RPA recording (VNC + step capture)
- `/rpa/configure` — Configure recorded steps & parameters
- `/rpa/test` — Test generated Playwright script
- `/share/:sessionId` — Public session sharing (no auth)

## RPA System

The RPA module records user browser actions and generates Playwright scripts. Supports two modes: **Docker sandbox mode** and **local mode**.

### Modes
- **Docker mode** (`STORAGE_BACKEND=docker`): Playwright runs in sandbox container, uses VNC for display
- **Local mode** (`STORAGE_BACKEND=local`): Playwright runs on host machine, uses CDP screencast for display

### Architecture
1. **Recording**: 
   - Docker mode: `manager.py` launches Playwright in sandbox on DISPLAY=:99, injects JS event capture via `page.expose_function`. Events written to `/tmp/rpa_events.jsonl`, polled by backend every 2s.
   - Local mode: `LocalCDPConnector` launches Playwright on host, `ScreencastService` streams CDP frames via WebSocket at `/rpa/screencast`. Frontend displays frames on canvas and sends mouse/keyboard input back via WebSocket.
2. **Locator generation**: Browser-side JS uses Playwright-codegen-style algorithm (score-based: testid → role+name → placeholder → label → alt → text → title → CSS). Elements are retargeted to nearest interactive ancestor. GUID-like IDs are rejected.
3. **Script generation**: `generator.py` converts locator objects to Playwright API calls. Link clicks use `expect_navigation`, non-link clicks add `wait_for_timeout(500)`.
4. **Execution**: 
   - Docker mode: `executor.py` runs scripts via `nohup` in sandbox background, polls `/tmp/rpa_test_done.txt` for completion. Kills existing browsers via `supervisorctl stop browser` before test.
   - Local mode: Scripts run directly on host via `LocalShellBackend`, no VNC connection needed.
5. **Export**: `skill_exporter.py` saves to `EXTERNAL_SKILLS_DIR` with YAML front-matter in `SKILL.md`.

### Key files
- `backend/rpa/manager.py` — Session lifecycle, BROWSER_SCRIPT (runs inside sandbox), event polling
- `backend/rpa/cdp_connector.py` — CDP connection abstraction (`SandboxCDPConnector` for Docker, `LocalCDPConnector` for local)
- `backend/rpa/screencast.py` — `ScreencastService` for CDP frame streaming + input injection (local mode)
- `backend/rpa/generator.py` — Playwright script generation from recorded steps
- `backend/rpa/executor.py` — Script execution (sandbox or local)
- `backend/rpa/skill_exporter.py` — Export skill to SKILL.md + skill.py
- `backend/route/rpa.py` — REST + WebSocket endpoints (`/rpa/screencast` for CDP streaming)
- `frontend/src/pages/rpa/RecorderPage.vue` — Recording UI (VNC for Docker, Canvas+WebSocket for local)
- `frontend/src/pages/rpa/TestPage.vue` — Test UI (VNC for Docker, Canvas+WebSocket for local)
- `frontend/src/utils/sandbox.ts` — `isLocalMode()` helper

### Sandbox interaction
- MCP JSON-RPC 2.0 protocol at `SANDBOX_MCP_URL`
- `sandbox_execute_bash`: param `cmd`, response at `result.structuredContent.output`
- `sandbox_execute_code`: params `code` + `language`, response at `result.structuredContent.stdout`
- Supervisord manages sandbox services (`browser`, `mcp-server-browser` have `autorestart=true` — must use `supervisorctl stop/start`, not `pkill`)

## Skill System

Skills are directories containing `SKILL.md` (with YAML front-matter) + implementation files.

```
skill_name/
├── SKILL.md    # ---\nname: ...\ndescription: ...\n---
└── skill.py    # Implementation
```

- **Builtin skills**: `BUILTIN_SKILLS_DIR` (default `/app/builtin_skills`, baked into Docker image)
- **External skills**: `EXTERNAL_SKILLS_DIR` (default `/app/Skills`, mounted from host `./Skills`)
- Skills API: `GET /api/v1/sessions/skills` scans both directories
- `SKILL.md` must have YAML front-matter (`---\nname: ...\ndescription: ...\n---`) for the API to parse metadata

## Environment Variables

Key variables in `.env` (see `config.py` for full list):

```bash
# LLM
DS_API_KEY=         # DeepSeek API key
DS_URL=             # DeepSeek base URL
DS_MODEL=           # Model name (default: deepseek-chat)

# MongoDB
MONGODB_HOST=localhost
MONGODB_PORT=27014
MONGODB_USER=scienceone
MONGODB_PASSWORD=

# Sandbox
SANDBOX_MCP_URL=http://localhost:18080/mcp
STORAGE_BACKEND=docker  # 'docker' or 'local' (affects RPA mode)

# Search
WEBSEARCH_BASE_URL=http://localhost:8068

# Skills (local dev — Docker uses /app/Skills and /app/builtin_skills)
EXTERNAL_SKILLS_DIR=C:\Users\...\external_skills
BUILTIN_SKILLS_DIR=D:\code\...\backend\builtin_skills

# Workspace
WORKSPACE_DIR=C:\Users\...\workspace
```

## Coding Conventions

- **Python**: PEP 8, snake_case, Pydantic v2 models (use `model_dump()` not `.dict()`, `Field(default_factory=...)` not mutable defaults)
- **TypeScript/Vue**: camelCase for variables/functions, PascalCase for components
- **API routes**: kebab-case paths
- **Frontend API calls**: use `apiClient` from `@/api/client` (handles auth token). Base URL is `/api/v1` — use relative paths like `/rpa/session/...`, not `/api/v1/rpa/...`
- **i18n**: Both Chinese and English supported. UI strings in `src/locales/en.ts` and `zh.ts`
- **Commit messages**: Chinese or English, prefixed with type: `feat:`, `fix:`, `refactor:`, `chore:`

## Common Pitfalls

- **Pydantic v2**: Use `model_dump()` instead of `.dict()`. Use `Field(default_factory=datetime.now)` instead of `datetime.now()` as default.
- **Sandbox process management**: Supervisord has `autorestart=true` on browser services. Use `supervisorctl stop/start`, not `pkill`.
- **Playwright event loop**: In sandbox scripts, use `page.wait_for_timeout(N)` instead of `time.sleep(N)` — sleep blocks the Playwright event loop and prevents `expose_function` callbacks.
- **Frontend double prefix**: `apiClient` base URL is `/api/v1`. Don't prefix paths with `/api/v1/` again.
- **VNC access**: Docker mode only. Use port 18080 (nginx-served noVNC page), not 16080 (raw websocat). URL: `http://{host}:18080/vnc/index.html?autoconnect=true&resize=scale`
- **Sandbox script execution**: `sandbox_execute_bash` kills the process tree when the MCP call returns. For long-running scripts, use `nohup` + sentinel file polling.
- **Skills not appearing**: `SKILL.md` must have YAML front-matter. The `EXTERNAL_SKILLS_DIR` env var must point to the correct directory for your environment (Docker vs local).
- **Local mode RPA**: Set `STORAGE_BACKEND=local` in `.env`. Frontend detects mode via `/client-config` endpoint. Local mode uses CDP screencast (WebSocket at `/rpa/screencast`), not VNC.
- **CDP screencast performance**: Backend uses JPEG quality=40 for lower latency. Frontend throttles mousemove events and syncs canvas size with image naturalSize for accurate coordinate mapping.
