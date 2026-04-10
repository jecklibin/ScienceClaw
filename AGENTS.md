# AGENTS.md

## Project Overview

RpaClaw is a privacy-first personal research assistant powered by LangChain DeepAgents. It provides 1,900+ built-in scientific tools, multi-format document generation, a sandboxed code execution environment, and an RPA skill recording system. All data stays local by default.

## Tech Stack

- Backend: FastAPI (Python 3.13), LangGraph + DeepAgents, Pydantic v2, Motor (async MongoDB)
- Frontend: Vue 3 + TypeScript, Vite, Tailwind CSS, Reka UI
- Database: MongoDB
- Cache/Queue: Redis + Celery
- Sandbox: Docker container with Xvfb, x11vnc, Playwright, Python 3.12
- Node RPA runtime: Fastify + Playwright + ws + Vitest
- Search: SearXNG + Crawl4AI via the websearch microservice

## Directory Structure

```text
RpaClaw/
|-- docker-compose.yml
|-- docker-compose-release.yml
|-- Skills/
|-- Tools/
`-- RpaClaw/
    |-- backend/
    |   |-- main.py
    |   |-- config.py
    |   |-- route/
    |   |-- deepagent/
    |   |-- rpa/
    |   |-- builtin_skills/
    |   |-- mongodb/
    |   `-- im/
    |-- frontend/
    |   `-- src/
    |-- rpa-engine/
    |   |-- src/
    |   `-- tests/
    |-- sandbox/
    `-- task-service/
```

## Services And Ports

| Service | Container Port | Host Port | Purpose |
|---|---:|---:|---|
| Frontend | 5173 | 5173 | Vue dev server / web UI |
| Backend | 8000 | 12001 | FastAPI REST API |
| Sandbox | 8080 | 18080 | Code execution and browser sandbox |
| Sandbox VNC | 6080 | 16080 | VNC WebSocket for sandbox RPA |
| MongoDB | 27017 | 27014 | Database |
| Task Service | 8001 | 12002 | Scheduled tasks |
| Websearch | 8068 | 8068 | SearXNG + Crawl4AI |
| RPA Engine | 3310 | 3310 | Node recorder/replay engine in local or split deployment |

## Running Locally

### Docker

```bash
docker compose up -d --build
docker compose -f docker-compose-release.yml up -d
```

### Local Development

```bash
# Backend
cd RpaClaw/backend
cp .env.example .env
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend
cd RpaClaw/frontend
npm install
npm run dev

# Optional: Node RPA engine
cd ../rpa-engine
npm install
npm run dev
```

Default login: `admin` / `admin123`

## Backend API

All product routes are prefixed with `/api/v1`.

- `/auth` - login, register, password management
- `/sessions` - session CRUD, skills listing, file operations
- `/chat` - streaming chat with LLM agents
- `/rpa` - RPA recording, testing, skill export
- `/file` - file upload/download
- `/models` - model configuration
- `/tools`, `/tooluniverse` - tool discovery
- `/task-settings` - scheduled task configuration
- `/im` - Feishu/Lark integrations

Health check: `GET /health`

Readiness check: `GET /ready`

## Frontend Routing

Routes are defined in `RpaClaw/frontend/src/main.ts`.

- `/chat`
- `/chat/:sessionId`
- `/chat/skills`
- `/chat/tools`
- `/chat/tasks`
- `/rpa/recorder`
- `/rpa/configure`
- `/rpa/test`
- `/share/:sessionId`

## RPA System

The RPA stack now supports two runtime families while preserving the existing FastAPI and frontend API surface.

### Runtime Modes

- Legacy mode: `RPA_ENGINE_MODE=legacy`
  The Python backend continues to own Playwright runtime, recording hooks, replay, and script generation.
- Node engine mode: `RPA_ENGINE_MODE=node`
  The backend becomes an orchestration and compatibility layer, while `RpaClaw/rpa-engine` owns browser runtime, recording semantics, frame-aware actions, replay, and code generation.

### Browser Placement

- Docker browser mode: `STORAGE_BACKEND=docker`
  Browser runtime lives in the sandbox container and the UI uses VNC.
- Local browser mode: `STORAGE_BACKEND=local`
  Browser runtime lives on the host and the UI uses CDP screencast.

These axes are independent. `RPA_ENGINE_MODE` decides who owns the recorder/replay engine. `STORAGE_BACKEND` decides where the browser executes.

### Node Engine Architecture

In node mode, the Python backend keeps user sessions, persistence, product APIs, AI orchestration, and skill export. The Node service owns browser runtime and Playwright-native semantics.

Backend orchestration files:

- `RpaClaw/backend/rpa/manager.py`
- `RpaClaw/backend/rpa/engine_client.py`
- `RpaClaw/backend/rpa/engine_supervisor.py`
- `RpaClaw/backend/rpa/session_gateway.py`
- `RpaClaw/backend/route/rpa.py`

Engine runtime files:

- `RpaClaw/rpa-engine/src/app.ts`
- `RpaClaw/rpa-engine/src/routes/sessions.ts`
- `RpaClaw/rpa-engine/src/routes/replay.ts`
- `RpaClaw/rpa-engine/src/playwright/runtime-controller.ts`
- `RpaClaw/rpa-engine/src/playwright/recorder-adapter.ts`

### Recording And Replay Flow

1. The frontend still calls FastAPI `/api/v1/rpa/...` endpoints.
2. In node mode, `RPAManager` delegates session start, navigation, tab activation, replay, and code generation to `RPASessionGateway`.
3. `RPASessionGateway` uses `RPAEngineClient` for HTTP calls and optionally `LocalRPAEngineSupervisor` to ensure a colocated `rpa-engine` process is running.
4. The Node runtime records Playwright-native actions, selectors, frame paths, popup signals, and page aliases.
5. Backend compatibility mappers translate engine actions back into the legacy step shape expected by the existing frontend and skill export pipeline.

### Local And Cloud Deployment

- Local same-machine mode:
  Run `RpaClaw/rpa-engine` as a local process and point the backend to `http://127.0.0.1:3310`.
- Cloud split deployment:
  Deploy `rpa-engine` separately and point the backend at its reachable base URL.

### Key Files

- `RpaClaw/backend/rpa/cdp_connector.py` - CDP connection abstraction
- `RpaClaw/backend/rpa/screencast.py` - screencast streaming and input injection
- `RpaClaw/backend/rpa/generator.py` - legacy-only generator fallback in node mode
- `RpaClaw/backend/rpa/executor.py` - legacy executor fallback plus engine-aware execution path
- `RpaClaw/backend/rpa/skill_exporter.py` - export skill packages from recorded workflows
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue` - recording UI
- `RpaClaw/frontend/src/pages/rpa/TestPage.vue` - test UI

## Sandbox Interaction

- MCP endpoint: `SANDBOX_MCP_URL`
- `sandbox_execute_bash` returns output in `result.structuredContent.output`
- `sandbox_execute_code` returns stdout in `result.structuredContent.stdout`
- Sandbox browser services are managed by supervisord. Use `supervisorctl stop/start`, not `pkill`.

## Skill System

Skills are directories containing `SKILL.md` plus implementation files.

```text
skill_name/
|-- SKILL.md
`-- skill.py
```

- Built-in skills come from `BUILTIN_SKILLS_DIR`
- External skills come from `EXTERNAL_SKILLS_DIR`
- `GET /api/v1/sessions/skills` scans both locations
- `SKILL.md` must include YAML front matter with `name` and `description`

## Environment Variables

Key variables in `.env`:

```bash
# LLM
DS_API_KEY=
DS_URL=
DS_MODEL=deepseek-chat

# MongoDB
MONGODB_HOST=localhost
MONGODB_PORT=27014
MONGODB_USER=scienceone
MONGODB_PASSWORD=

# Sandbox
SANDBOX_MCP_URL=http://localhost:18080/mcp
STORAGE_BACKEND=docker

# Node RPA engine
RPA_ENGINE_MODE=legacy
RPA_ENGINE_BASE_URL=http://127.0.0.1:3310
RPA_ENGINE_AUTH_TOKEN=
RPA_ENGINE_HOST=127.0.0.1
RPA_ENGINE_PORT=3310
RPA_ENGINE_START_CMD=npm run dev

# Skills
EXTERNAL_SKILLS_DIR=C:\path\to\external_skills
BUILTIN_SKILLS_DIR=D:\code\MyScienceClaw\RpaClaw\backend\builtin_skills

# Workspace
WORKSPACE_DIR=C:\path\to\workspace
```

## Coding Conventions

- Python: PEP 8, snake_case, Pydantic v2, use `model_dump()`
- TypeScript/Vue: camelCase for values, PascalCase for components
- API routes: kebab-case
- Frontend API calls: use `apiClient` from `@/api/client`
- i18n strings: update both `RpaClaw/frontend/src/locales/en.ts` and `RpaClaw/frontend/src/locales/zh.ts`
- Commit prefixes: `feat:`, `fix:`, `refactor:`, `chore:`

## Common Pitfalls

- Do not use `.dict()` on Pydantic v2 models.
- Do not kill sandbox browser services with `pkill`; supervisord will restart them.
- In sandbox Playwright scripts, prefer `page.wait_for_timeout()` over `time.sleep()`.
- `apiClient` already prefixes `/api/v1`; do not double-prefix frontend requests.
- Docker RPA UI uses VNC on port `18080`; local mode uses `/rpa/screencast`.
- `sandbox_execute_bash` is not suitable for long-running foreground processes; use `nohup` and sentinel polling when needed.
- Skills require YAML front matter in `SKILL.md`.
- In node engine mode, the authoritative recorder/replay semantics come from the Node service, not Python-generated selectors.
- `docs/superpowers/plans/2026-04-09-rpa-node-engine.md` is a local plan artifact and should stay untracked unless explicitly requested.
