# AGENTS.md

## Core Thinking Principles

- **第一性原理**：先追问原始目标和约束，不要机械沿用既有方案、业界惯例或之前补丁。
- **拒绝盲从**：如果用户给出的路径存在过度设计、成本过高或边界模糊，要直接指出并给出更优路线。
- **目标模糊时必须澄清**：发现关键矛盾、缺失上下文或多条路径后果明显不同，不要靠猜测推进。
- **避免机械折中**：折中方案如果增加复杂度、模糊模块边界且收益不明确，应建议舍弃。

## Project Quick Context

RpaClaw is a privacy-first personal research assistant with a local RPA skill recording system. The current RPA direction is **Trace-first Recording + Post-hoc Skill Compilation**.

- **Backend**: FastAPI, Python, Pydantic v2, LangGraph/DeepAgents, MongoDB.
- **Frontend**: Vue 3, TypeScript, Vite, Tailwind CSS.
- **RPA runtime**: Playwright, local CDP screencast mode, Docker/VNC mode.
- **Skill output**: `SKILL.md` plus `skill.py`.

Detailed project reference: [docs/project/reference.md](docs/project/reference.md)

## Local Startup

Backend:

```powershell
$env:PYTHONPATH="RpaClaw"
python -m uvicorn backend.main:app --app-dir .\RpaClaw --host 0.0.0.0 --port 8000 --reload --reload-dir .\RpaClaw\backend
```

Frontend:

```powershell
$env:BACKEND_URL = "http://localhost:8000"
npm run dev
```

Default local/desktop mode opens as the bootstrap admin without login. Set `AUTH_PROVIDER=local` to enable login; the bootstrap admin is `admin` / `admin123` unless overridden.

## RPA/Agent 架构专项军规

- **军规 1：RPA 录制主路径坚持 Trace-first。**
  录制阶段优先真实操作浏览器并记录 trace，不在录制时构建重型 contract 中间层。自然语言步骤可以生成 Python Playwright 代码完成当前操作，但录制目标是“快速、可观察、可追踪地完成当前步骤”，泛化与去冗余主要留到录制完成后的技能编译与回放验证阶段处理。

- **军规 2：禁止做经验规则驱动的 Agent。**
  经验库、失败模式、selector 经验、站点经验只能作为 repair 的轻量提示，不能替代 Planner/LLM 的语义理解职责，不能强制改写执行策略，也不能因为“看起来可能不稳定”而阻止非危险代码先真实执行。

- **军规 3：失败事实优先，经验提示辅助。**
  repair 输入必须优先保留原始错误日志、当前 URL/title、失败代码/计划摘要和执行结果。经验提示只允许作为低优先级 advisory hint；当事实日志与经验提示冲突时，必须以事实日志和当前页面状态为准。

- **军规 4：安全拦截和稳定性建议必须分层。**
  shell、文件系统破坏、无限循环、敏感本地访问等安全风险可以在执行前拦截；selector 脆弱、空提取、导航慢、页面结构变化等稳定性问题不应预拦截，而应执行后基于失败事实进入 repair。

- **军规 5：Fallback 只能救急，不能反客为主。**
  `_infer_*`、关键词匹配、站点模板、经验提示、候选 selector 表都只能辅助局部失败恢复。一旦它们开始主导主路径行为，应回到第一性原理重新审视架构边界，而不是继续补规则。

- **军规 6：方案设计必须面向泛化场景，禁止为单一站点反向塑造架构。**
  GitHub、百度、内部系统等只能作为验证案例或适配样本，不能成为核心抽象本身。新增编译策略、repair 策略或数据流机制时，必须先说明它解决的通用问题（如跨步骤数据依赖、录制现场值去硬编码、可见可编辑元素定位、动态列表提取），再说明站点案例如何落入该通用抽象。

- **军规 7：先比较 raw snapshot 和 compact snapshot，再修 planner。**
  遇到“LLM 选错区域、提取错数据、操作错元素”时，必须先判断目标信息是否已经进入 `compact_snapshot`。如果 `raw_snapshot` 有信息而 `compact_snapshot` 缺失，优先审视 snapshot 压缩策略；此时直接修 prompt、repair 或 selector 只是补症状。

- **军规 8：snapshot 压缩必须区分任务形态。**
  字段提取、表单读取、详情页信息抽取适合 TopK region 展开；候选选择、搜索结果选择、卡片列表选择需要横向保留候选摘要与主操作 locator。不能用同一种 TopK 区域展开机制覆盖所有页面任务。

- **军规 9：不要加“拦住但不解决”的校验。**
  空提取、弱 selector、页面慢加载等稳定性校验如果只能阻止成功或提前报错，却不能提供更接近 root cause 的修复路径，就不应进入录制主路径。此类校验应作为诊断证据或后置分析，不能替代修复 snapshot、planner 或编译阶段的数据流问题。

## RPA Implementation Boundaries

- 录制阶段自然语言步骤由 `RecordingRuntimeAgent` 执行，只处理当前用户指令，不重新规划整套 SOP。
- 录制阶段允许 LLM 生成临时 Python Playwright 代码；最终 Skill 编译阶段应优先使用 `TraceSkillCompiler` 的确定性逻辑。
- 生成脚本阶段基本不调用 LLM；只有真正语义性的 replay 步骤才保留 runtime AI。
- Repair 最多一次。不要为了提升单步成功率引入多轮循环 repair，除非重新评估录制阶段体验和成本。
- 不要把录制现场 URL、项目名、页面文本直接当成最终脚本的泛化逻辑。录制现场值只能作为 evidence，用于推断 suffix、字段结构或验证输出。
- 后一步依赖前一步结果时，应优先通过 `_results` / `output_key` 建立动态引用，而不是写死 observed value。

RPA architecture docs:

- [Trace-first architecture](docs/rpa/trace-first-architecture.md)
- [Failure repair policy](docs/rpa/failure-repair-policy.md)
- [TraceSkillCompiler generalization](docs/rpa/trace-skill-compiler-generalization.md)

## Key RPA Files

- `RpaClaw/backend/rpa/recording_runtime_agent.py`: natural-language recording-time browser operator.
- `RpaClaw/backend/rpa/trace_models.py`: accepted trace, runtime result, diagnostic models.
- `RpaClaw/backend/rpa/trace_recorder.py`: manual/AI trace conversion and dataflow inference.
- `RpaClaw/backend/rpa/trace_skill_compiler.py`: post-hoc trace-to-skill compiler.
- `RpaClaw/backend/route/rpa.py`: RPA REST and streaming endpoints.
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`: recording UI.
- `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`: timeline and generated script configuration UI.
- `RpaClaw/frontend/src/pages/rpa/TestPage.vue`: generated script test UI.

## Coding Conventions

- **Python**: PEP 8, snake_case, Pydantic v2 (`model_dump()`, `Field(default_factory=...)`).
- **TypeScript/Vue**: camelCase for variables/functions, PascalCase for components.
- **API paths**: kebab-case.
- **Frontend API calls**: use `apiClient`; paths are relative to `/api/v1`, so do not prefix `/api/v1` again.
- **i18n**: update both `src/locales/en.ts` and `src/locales/zh.ts` when touching UI strings.
- **Commits**: use prefixes such as `feat:`, `fix:`, `refactor:`, `chore:`.

## Common Pitfalls

- **Pydantic v2**: use `model_dump()`, not `.dict()`.
- **Sandbox processes**: browser services have `autorestart=true`; use `supervisorctl stop/start`, not `pkill`.
- **Playwright event loop**: use `page.wait_for_timeout(N)`, not `time.sleep(N)` inside async scripts.
- **Frontend API double prefix**: `apiClient` already includes `/api/v1`.
- **Local mode RPA**: set `STORAGE_BACKEND=local`; local mode uses CDP screencast, not VNC.
- **Docker VNC mode**: use noVNC via port `18080`, not raw VNC port `16080`.
- **Long-running sandbox scripts**: use `nohup` plus sentinel-file polling; MCP shell calls kill child process trees when the call returns.
- **Skills discovery**: `SKILL.md` must include YAML front matter.
- **Desktop tools in local mode**: host tool library lives under `TOOLS_DIR`; `/app/Tools` is the sandbox-visible mount path.
