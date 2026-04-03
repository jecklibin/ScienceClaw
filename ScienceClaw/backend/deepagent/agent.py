"""
agent.py — 组装 DeepAgent：系统提示词 + 模型 + 工具（内置 + 外部扩展）+ Skills + 监控中间件。

架构：
  - HybridSandboxBackend 作为默认后端：
    - 文件操作（read_file/write_file/edit_file/ls/glob/grep）→ 本地 /home/scienceclaw/
    - 命令执行（execute）→ 远程 sandbox 容器
    - 通过 Docker 共享卷同步文件
  - CompositeBackend 路由：
    - /builtin-skills/ → FilesystemBackend（内置 skills，只读，始终加载）
    - /skills/         → MongoSkillBackend（用户外置 skills，MongoDB 多租户隔离）
  - deepagents 内置工具层统一管理所有工具（不再使用 MCP sandbox 工具）

Skills 架构：
  - 内置 skills（/app/builtin_skills/）：find-skills 等核心能力，
    COPY 进 Docker 镜像，不依赖宿主机挂载（避免 macOS 大小写不敏感文件系统的冲突）
  - 外置 skills（/app/Skills/）：用户通过 find-skills 下载或自行安装的 skills，
    支持屏蔽和删除管理

监控中间件：
  - SSEMonitoringMiddleware 通过 wrap_tool_call 拦截工具执行前后
  - 事件存储在 middleware.sse_events，由 runner.py 轮询消费
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, DEFAULT_SUBAGENT_PROMPT
from backend.deepagent.engine import get_llm_model
from backend.deepagent.local_preview_backend import LocalPreviewShellBackend
from backend.deepagent.tools import propose_skill_save, propose_tool_save, eval_skill, grade_eval
from backend.deepagent.full_sandbox_backend import FullSandboxBackend
from backend.deepagent.mongo_skill_backend import MongoSkillBackend
from backend.deepagent.sse_middleware import SSEMonitoringMiddleware
from backend.deepagent.offload_middleware import ToolResultOffloadMiddleware
from backend.deepagent.diagnostic import DIAGNOSTIC_ENABLED, DiagnosticLogger
from backend.config import settings

# ───────────────────────────────────────────────────────────────────
# 外部扩展工具（Tools 目录自动扫描，支持热加载）
# ───────────────────────────────────────────────────────────────────
try:
    from Tools import reload_external_tools
    _initial = reload_external_tools(force=True)
    logger.info(f"[Agent] 已加载 {len(_initial)} 个外部扩展工具: "
                f"{[t.name for t in _initial]}")
    # Register proxy tools in SSE protocol so tool_meta carries sandbox: true
    from backend.deepagent.sse_protocol import get_protocol_manager as _get_proto
    _proto = _get_proto()
    for _t in _initial:
        _proto.register_sandbox_tool(_t.name, _t.description[:80])
    logger.info(f"[Agent] 已注册 {len(_initial)} 个沙箱代理工具到 SSE 协议")
except ImportError:
    reload_external_tools = None  # type: ignore[assignment]
    logger.warning("[Agent] 未找到 Tools 包，跳过外部扩展工具加载")

# ───────────────────────────────────────────────────────────────────
# 路径配置
# ───────────────────────────────────────────────────────────────────

_BUILTIN_SKILLS_DIR = os.environ.get("BUILTIN_SKILLS_DIR", "/app/builtin_skills")
_BUILTIN_SKILLS_ROUTE = "/builtin-skills/"
_EXTERNAL_SKILLS_ROUTE = "/skills/"
_WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/home/scienceclaw")
_SKILLS_SUBDIR = ".skills"  # session-local skill files for sandbox execution
_SANDBOX_WORKSPACE_DIR = os.environ.get("SANDBOX_WORKSPACE_DIR", "/home/scienceclaw")  # 沙箱内 workspace 路径（与后端共享卷）

# ───────────────────────────────────────────────────────────────────
# Backend 构建
# ───────────────────────────────────────────────────────────────────


def _build_backend(session_id: str, sandbox,
                    user_id: str | None = None, blocked_skills: Set[str] | None = None):
    """构建 CompositeBackend（会话级隔离）。"""
    routes = {}

    if os.path.isdir(_BUILTIN_SKILLS_DIR):
        logger.info(f"[Skills] 内置 skills: {_BUILTIN_SKILLS_DIR} → {_BUILTIN_SKILLS_ROUTE}")
        routes[_BUILTIN_SKILLS_ROUTE] = FilesystemBackend(
            root_dir=_BUILTIN_SKILLS_DIR,
            virtual_mode=True,
        )

    if settings.storage_backend == "local":
        _ext_skills_dir = os.environ.get("EXTERNAL_SKILLS_DIR", "./Skills")
        if os.path.isdir(_ext_skills_dir):
            logger.info(f"[Skills] 本地 skills: {_ext_skills_dir} → {_EXTERNAL_SKILLS_ROUTE}")
            routes[_EXTERNAL_SKILLS_ROUTE] = FilesystemBackend(
                root_dir=_ext_skills_dir,
                virtual_mode=True,
            )
    elif user_id:
        logger.info(f"[Skills] MongoDB skills for user={user_id} → {_EXTERNAL_SKILLS_ROUTE}"
                     f" (blocked: {blocked_skills or set()})")
        routes[_EXTERNAL_SKILLS_ROUTE] = MongoSkillBackend(
            user_id=user_id,
            blocked_skills=blocked_skills,
        )

    if routes:
        return lambda rt: CompositeBackend(default=sandbox, routes=routes)
    else:
        return sandbox


async def _inject_skills_to_sandbox(
    sandbox: FullSandboxBackend,
    workspace: str,
    user_id: str,
    blocked_skills: Set[str],
) -> int:
    """将用户的 MongoDB 技能文件写入沙箱 {workspace}/.skills/{name}/ 目录。

    这样 agent 执行 skill.py 时可以在沙箱文件系统中找到文件。
    返回成功注入的技能数量。
    """
    from backend.storage import get_repository
    col_repo = get_repository("skills")
    filt = {"user_id": user_id, "blocked": {"$ne": True}}
    if blocked_skills:
        filt["name"] = {"$nin": list(blocked_skills)}

    skills_dir = f"{workspace}/{_SKILLS_SUBDIR}"
    injected = 0

    docs = await col_repo.find_many(filt, projection={"name": 1, "files": 1})
    for doc in docs:
        skill_name = doc.get("name", "")
        files = doc.get("files", {})
        if not skill_name or not files:
            continue

        for fname, content in files.items():
            if not content:
                continue
            file_path = f"{skills_dir}/{skill_name}/{fname}"
            try:
                result = await sandbox.awrite(file_path, content)
                if hasattr(result, "error") and result.error:
                    logger.warning(f"[Skills] 注入失败 {file_path}: {result.error}")
            except Exception as exc:
                logger.warning(f"[Skills] 注入失败 {file_path}: {exc}")

        injected += 1

    if injected:
        logger.info(f"[Skills] 已注入 {injected} 个技能到沙箱 {skills_dir}/")
    return injected


# ───────────────────────────────────────────────────────────────────
# 系统提示词
# ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """You are ScienceClaw, a proactive personal AI assistant designed to help users solve problems, conduct research, and complete tasks efficiently.

Current date and time: {current_datetime}.

## Language
Always respond in {language_instruction}.

## Core Principles
- Adapt to the conversation. Chat naturally for casual topics, but take concrete actions when the user asks for tasks or problem-solving.
- Prefer execution over explanation. If a task can be solved through code or tools, implement and execute the solution instead of only describing it.
- **Write files, not chat**: When the user asks to write, create, or generate code/scripts/files, ALWAYS use `write_file` to create real files — never just paste code in chat.
- **Write → Execute → Fix loop**: After writing ANY executable script, you MUST immediately run it via `execute` to verify correctness. If it fails, fix and re-run.
- **Skill-first approach**: ALWAYS check available skills (`{builtin_skills_path}` and `{external_skills_path}`) before starting any task. If a skill matches, `read_file` its SKILL.md and follow the workflow. Do NOT reinvent what a skill already provides.
- **SKILL.md files are instruction documents** — use `read_file` to read them, NEVER `execute` them as scripts.
- Solve problems proactively. Only ask questions when the intent or requirements are truly unclear.

## Workspace
Your workspace directory is {workspace_dir}/.
- All files should be created under this directory using absolute paths.
- The workspace is shared between the file system and the execution sandbox.

## Sandbox Boundary
The sandbox is an isolated execution environment. Scripts running in the sandbox CANNOT import or call your tools directly (`from functions import ...` will FAIL with `ModuleNotFoundError`).

**Data flow**: Use YOUR tools to gather data → save results to workspace files via `write_file` → write sandbox scripts that READ those files. NEVER call your tools from within sandbox scripts.

**Large tool results** are automatically saved to `research_data/` files (raw format). To use them in sandbox scripts: `read_file` the data → write a clean JSON file via a Python script with `json.dump()` → sandbox scripts read that clean file.

## Task Completion Strategy

### Step 1: Understand & Plan
- Identify ALL deliverables, requirements, and output format.
- For any task involving 2+ steps, call `write_todos` BEFORE starting.
- Check Memory: **AGENTS.md** and **CONTEXT.md**.
- **Check Available Skills (MANDATORY)** — review the skills catalog. If ANY skill matches the task, `read_file` that SKILL.md and follow its workflow. Do NOT skip this step.

### Step 2: Execute
- If a skill matched → follow the skill's workflow completely.
- Otherwise, use tools directly. Priority: existing skills > built-in tools.
- **Before `propose_tool_save`**: read `{builtin_skills_path}tool-creator/SKILL.md` first.
- **Before `propose_skill_save`**: read `{builtin_skills_path}skill-creator/SKILL.md` first.
- Build incrementally — one component per tool call. Test via `execute` after writing.

### Step 3: Verify & Deliver
- Re-read the user's original request. Check all deliverables are produced.
- If a script fails, fix the specific error — do NOT rewrite from scratch. If it fails 2+ times, simplify.

### Step 4: Reflect & Capture
After completing a non-trivial task:
- **Reusable workflow** → Suggest saving as a **skill** via skill-creator.
- **Reusable function** → Suggest saving as a **tool** via tool-creator.
- **User preference learned** → Update **AGENTS.md** via `edit_file`.
- **Project context learned** → Update **CONTEXT.md** via `edit_file`.
"""


_EVAL_SYSTEM_PROMPT_TEMPLATE = """You are ScienceClaw, a proactive personal AI assistant designed to help users solve problems, conduct research, and complete tasks efficiently.

Current date and time: {current_datetime}

## Core Principles
- Prefer execution over explanation. If a task can be solved through code or tools, implement and execute the solution instead of only describing it.
- Always respond in the same language the user uses.
- When the user asks to write, create, or generate code/scripts/files, ALWAYS use write_file to create real files.
- Use sandbox execution whenever it can produce verifiable results.

## Workspace
Your workspace directory is {workspace_dir}/.
- All files should be created under this directory using absolute paths.
- The workspace is shared between the file system and the execution sandbox.
"""


_LANGUAGE_MAP = {
    "zh": ("Chinese (Simplified)", "你必须使用简体中文回复所有内容。所有生成的报告、文档标题和正文也必须使用简体中文。"),
    "en": ("English", "You must respond in English. All generated reports, document titles and body text must also be in English."),
}


def get_system_prompt(workspace_dir: str, sandbox_env: str | None = None, language: str | None = None,
                      builtin_skills_path: str | None = None, external_skills_path: str | None = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
    lang_code = (language or "").strip().lower()
    if lang_code in _LANGUAGE_MAP:
        lang_name, lang_detail = _LANGUAGE_MAP[lang_code]
        language_instruction = (
            f"- The user has set their preferred language to **{lang_name}** (code: `{lang_code}`).\n"
            f"- {lang_detail}\n"
            f"- This applies to ALL outputs: conversation replies, report content, section titles, chart labels, and file names."
        )
    else:
        language_instruction = "- Always respond in the same language the user uses."

    # 默认使用虚拟路由（云端模式）
    builtin_path = builtin_skills_path or _BUILTIN_SKILLS_ROUTE
    external_path = external_skills_path or _EXTERNAL_SKILLS_ROUTE

    prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime=now,
        workspace_dir=workspace_dir,
        language_instruction=language_instruction,
        builtin_skills_path=builtin_path,
        external_skills_path=external_path,
    )
    if sandbox_env:
        prompt += f"\n\n## Sandbox Environment Information\n{sandbox_env}"
    return prompt


def _get_eval_system_prompt(workspace_dir: str, sandbox_env: str | None = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
    prompt = _EVAL_SYSTEM_PROMPT_TEMPLATE.format(
        current_datetime=now,
        workspace_dir=workspace_dir,
    )
    if sandbox_env:
        prompt += f"\n\n## Sandbox Environment Information\n{sandbox_env}"
    return prompt


# ───────────────────────────────────────────────────────────────────
# 工具列表（内置 + 外部扩展，不再包含 MCP sandbox 工具）
# ───────────────────────────────────────────────────────────────────

_STATIC_TOOLS = [
    propose_skill_save, propose_tool_save,
    eval_skill, grade_eval,
]


def _collect_tools(blocked_tools: Set[str] | None = None) -> List:
    """合并内置工具与外部扩展工具，去重并过滤屏蔽项。

    通过 DirWatcher 检测 Tools/ 目录变更，仅在变更时才重新 import 模块。
    """
    blocked = blocked_tools or set()
    seen_names: set[str] = set()
    all_tools: List = []

    ext_tools: list = []
    if reload_external_tools is not None:
        try:
            ext_tools = reload_external_tools()
        except Exception:
            logger.warning("[Agent] 动态加载外部工具失败", exc_info=True)

    for t in _STATIC_TOOLS + ext_tools:
        if t.name in blocked:
            logger.info(f"[Agent] 工具已屏蔽，跳过: {t.name}")
            continue
        if t.name not in seen_names:
            all_tools.append(t)
            seen_names.add(t.name)
        else:
            logger.warning(f"[Agent] 工具名称重复，跳过: {t.name}")
    logger.info(f"[Agent] 自定义工具列表({len(all_tools)}): {[t.name for t in all_tools]}")
    return all_tools


# ───────────────────────────────────────────────────────────────────
# 屏蔽查询（MongoDB）
# ───────────────────────────────────────────────────────────────────

async def get_blocked_skills(user_id: str) -> Set[str]:
    """从 MongoDB skills 集合查询用户屏蔽的 skills 列表。"""
    try:
        from backend.storage import get_repository
        repo = get_repository("skills")
        docs = await repo.find_many(
            {"user_id": user_id, "blocked": True},
            projection={"name": 1},
        )
        return {doc["name"] for doc in docs if doc.get("name")}
    except Exception as exc:
        logger.warning(f"[Skills] 查询屏蔽列表失败: {exc}")
        return set()


async def get_blocked_tools(user_id: str) -> Set[str]:
    """从 MongoDB 查询用户屏蔽的 tools 列表。"""
    try:
        from backend.storage import get_repository
        repo = get_repository("blocked_tools")
        docs = await repo.find_many(
            {"user_id": user_id},
            projection={"tool_name": 1},
        )
        return {doc["tool_name"] for doc in docs if doc.get("tool_name")}
    except Exception as exc:
        logger.warning(f"[Tools] 查询屏蔽列表失败: {exc}")
        return set()


# ───────────────────────────────────────────────────────────────────
# 创建 Agent
# ───────────────────────────────────────────────────────────────────

async def deep_agent(
    session_id: str,
    model_config: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    task_settings: Optional["TaskSettings"] = None,
    diagnostic_enabled: bool = False,
    language: Optional[str] = None,
) -> Tuple[Any, SSEMonitoringMiddleware, int, Optional[DiagnosticLogger]]:
    """
    创建一个完整的 DeepAgent 实例（会话级隔离），并注入 SSE 监控中间件。

    Returns:
        (agent, sse_middleware, context_window, diagnostic_logger)

    Skills 架构：
      - 内置 skills（/app/builtin_skills/）：COPY 进镜像，始终加载
      - 外置 skills（/app/Skills/）：用户自管理，支持屏蔽过滤
    """
    from backend.task_settings import TaskSettings as _TS
    ts: _TS = task_settings or _TS()
    model = get_llm_model(model_config, max_tokens_override=ts.max_tokens)
    context_window = getattr(model, "profile", {}).get("max_input_tokens", 131_072)

    blocked_skills = set()
    blocked_tools = set()
    if user_id:
        blocked_skills = await get_blocked_skills(user_id)
        blocked_tools = await get_blocked_tools(user_id)

    # ── 检测 Tools 目录变更并按需重新加载 ──

    tools = _collect_tools(blocked_tools=blocked_tools)

    sse_middleware = SSEMonitoringMiddleware(
        agent_name="DeepAgent",
        parent_agent=None,
        verbose=False,
    )

    # 1. 实例化后端：local 模式用 LocalShellBackend，云端用 FullSandboxBackend
    is_local = settings.storage_backend == "local"

    sandbox_info = None
    if is_local:
        local_workspace = os.path.join(_WORKSPACE_DIR, session_id)
        os.makedirs(local_workspace, exist_ok=True)
        sandbox = LocalPreviewShellBackend(
            session_id=session_id,
            root_dir=local_workspace,
            virtual_mode=False,
            timeout=ts.sandbox_exec_timeout,
            inherit_env=True,
        )
        sandbox_workspace = local_workspace
    else:
        sandbox = FullSandboxBackend(
            session_id=session_id,
            user_id=user_id or "default_user",
            base_dir=_WORKSPACE_DIR,
            sandbox_base_dir=_SANDBOX_WORKSPACE_DIR,
            execute_timeout=ts.sandbox_exec_timeout,
            max_output_chars=ts.max_output_chars,
        )
        # sandbox_workspace: 沙箱内路径（如 /home/scienceclaw/{sid}），用于 system prompt、exec_dir
        # local_workspace:   backend 本地路径（如 D:\...\workspace\{sid}），用于本地文件 I/O
        sandbox_workspace = sandbox.workspace
        local_workspace = os.path.join(_WORKSPACE_DIR, session_id)
        ctx = await sandbox.get_context()
        if ctx.get("success"):
            sandbox_info = ctx.get("data")

    # 1.5 将用户技能文件注入沙箱（仅云端模式）
    if not is_local and user_id:
        try:
            await _inject_skills_to_sandbox(
                sandbox, sandbox_workspace, user_id, blocked_skills,
            )
        except Exception as exc:
            logger.warning(f"[Skills] 技能注入沙箱失败: {exc}")

    # 2. 构建后端：本地模式直接用 LocalShellBackend，云端模式用 CompositeBackend
    if is_local:
        backend = sandbox
    else:
        backend = _build_backend(session_id, sandbox, user_id=user_id, blocked_skills=blocked_skills)

    # 工具结果自动落盘中间件：大型工具结果写入文件，Agent 按需 read_file 读取
    offload_middleware = ToolResultOffloadMiddleware(
        workspace_dir=sandbox_workspace,
        backend=sandbox,
    )

    # ── 诊断模式：记录 LLM 每步看到的完整上下文 ──
    diag: Optional[DiagnosticLogger] = None
    if diagnostic_enabled:
        diag = DiagnosticLogger(local_workspace, session_id)
        offload_middleware._diagnostic = diag

    # 中间件执行顺序：offload（修改结果）→ SSE（监控记录）
    # create_deep_agent 还会自动注入 SummarizationMiddleware（基于 model profile）
    agent_kwargs: Dict[str, Any] = {
        "model": model,
        "tools": tools,
        "middleware": [offload_middleware, sse_middleware],
    }

    # 3. Skills 配置：本地模式用实际目录，云端模式用虚拟路由
    skills_sources: List[str] = []
    builtin_path_for_prompt = _BUILTIN_SKILLS_ROUTE
    external_path_for_prompt = _EXTERNAL_SKILLS_ROUTE

    if is_local:
        # 本地模式：直接传实际目录路径
        if os.path.isdir(_BUILTIN_SKILLS_DIR):
            skills_sources.append(_BUILTIN_SKILLS_DIR)
            builtin_path_for_prompt = _BUILTIN_SKILLS_DIR + "/"
        _ext_skills_dir = os.path.abspath(settings.external_skills_dir)
        if os.path.isdir(_ext_skills_dir):
            skills_sources.append(_ext_skills_dir)
            external_path_for_prompt = _ext_skills_dir + "/"
    else:
        # 云端模式：使用虚拟路由（由 CompositeBackend 处理）
        if os.path.isdir(_BUILTIN_SKILLS_DIR):
            skills_sources.append(_BUILTIN_SKILLS_ROUTE)
        if user_id:
            skills_sources.append(_EXTERNAL_SKILLS_ROUTE)

    # 4. 注入系统提示词（传入实际路径）
    system_prompt = get_system_prompt(
        sandbox_workspace, sandbox_info, language=language,
        builtin_skills_path=builtin_path_for_prompt,
        external_skills_path=external_path_for_prompt
    )
    agent_kwargs["system_prompt"] = system_prompt

    if diag:
        diag.save_system_prompt(system_prompt)

    agent_kwargs["backend"] = backend

    if skills_sources:
        agent_kwargs["skills"] = skills_sources
        logger.info(f"[Agent] 已启用 Skills（sources: {skills_sources}, blocked: {blocked_skills}）")

    # 4. 启用跨会话记忆（两层隔离）
    #    - 全局 AGENTS.md：用户偏好 + 通用模式（跨所有会话，体量小）
    #    - 会话级 CONTEXT.md：当前项目/任务上下文（会话删除时自动清理）
    _mem_user = user_id or "default_user"
    _mem_dir = os.path.join(_WORKSPACE_DIR, "_memory", _mem_user)
    os.makedirs(_mem_dir, exist_ok=True)
    os.chmod(_mem_dir, 0o777)
    _global_mem = os.path.join(_mem_dir, "AGENTS.md")
    if not os.path.isfile(_global_mem):
        with open(_global_mem, "w") as f:
            f.write("# Global Memory (persists across all sessions)\n\n"
                    "## User Preferences\n\n"
                    "## General Patterns\n\n"
                    "## Notes\n")
        logger.info(f"[Memory] 初始化全局 Memory: {_global_mem}")

    _session_mem = os.path.join(local_workspace, "CONTEXT.md")
    if not os.path.isfile(_session_mem):
        with open(_session_mem, "w") as f:
            f.write("# Session Context (this session only)\n\n"
                    "## Project Context\n\n"
                    "## Task Notes\n")
        logger.info(f"[Memory] 初始化会话 Context: {_session_mem}")

    _MAX_MEMORY_CHARS = 4000
    _mem_files_to_use = []
    for _mf in [_global_mem, _session_mem]:
        try:
            _mf_size = os.path.getsize(_mf)
            if _mf_size > _MAX_MEMORY_CHARS:
                with open(_mf, "r", encoding="utf-8") as f:
                    _full = f.read()
                _truncated = _full[:_MAX_MEMORY_CHARS].rsplit("\n", 1)[0]
                _tmp_path = _mf + ".truncated"
                with open(_tmp_path, "w", encoding="utf-8") as f:
                    f.write(_truncated + "\n\n(Memory truncated — keep entries concise to stay under limit)\n")
                _mem_files_to_use.append(_tmp_path)
                logger.warning(
                    f"[Memory] {os.path.basename(_mf)} too large ({_mf_size:,} chars), "
                    f"truncated to {_MAX_MEMORY_CHARS:,} for injection"
                )
            else:
                _mem_files_to_use.append(_mf)
        except Exception:
            _mem_files_to_use.append(_mf)

    agent_kwargs["memory"] = _mem_files_to_use
    logger.info(f"[Memory] 已启用记忆: {[os.path.basename(f) for f in _mem_files_to_use]}")

    # 将主 agent 的关键策略注入到 general-purpose 子 agent 的 system_prompt，
    # 使子 agent 在处理 skill/tool 相关任务时遵循相同的工作流。
    _subagent_policy = f"""\n
## Workspace
Your workspace directory is {sandbox_workspace}/.
All files should be created under this directory using absolute paths.
SKILL.md files are instruction documents — use `read_file` to read them, NEVER `execute` them.
When a skill contains executable files (e.g., skill.py), use the sandbox-local path: `{sandbox_workspace}/.skills/<skill_name>/skill.py`.

## Skills CLI (CRITICAL)
NEVER use `npx skills`. Use `skills` directly. When installing: `HOME={sandbox_workspace} skills add <package> -g -y --agent '*'`. ALL flags are mandatory — omitting any will hang on interactive prompts.

## Task Resources
- **Existing skill?** → `read_file` the SKILL.md and follow it. Check `{external_path_for_prompt}` for local installs first.
- **PDF processing?** → `read_file("{builtin_path_for_prompt}pdf/SKILL.md")`. For form filling, also read FORMS.md.
- **Create a tool** → `read_file("{builtin_path_for_prompt}tool-creator/SKILL.md")`. NEVER write to /app/Tools/ directly.
- **Create a skill** → `read_file("{builtin_path_for_prompt}skill-creator/SKILL.md")`. NEVER write to `{external_path_for_prompt}` directly.
- **Find ecosystem skill** → `read_file("{builtin_path_for_prompt}find-skills/SKILL.md")`. After 2-3 failures, create from scratch.
Always use `write_file` to workspace then `propose_skill_save` / `propose_tool_save`.
"""
    GENERAL_PURPOSE_SUBAGENT["system_prompt"] = DEFAULT_SUBAGENT_PROMPT + _subagent_policy

    agent = create_deep_agent(**agent_kwargs)

    GENERAL_PURPOSE_SUBAGENT["system_prompt"] = DEFAULT_SUBAGENT_PROMPT

    logger.info(
        f"[Agent] session={session_id}, workspace={sandbox_workspace}, "
        f"middleware={sse_middleware.agent_name}, context_window={context_window:,}"
        f"{', diagnostic=ON' if diag else ''}"
    )
    return agent, sse_middleware, context_window, diag


# ───────────────────────────────────────────────────────────────────
# Eval 模式 Agent（精简版，用于 skill 测试）
# ───────────────────────────────────────────────────────────────────

async def deep_agent_eval(
    session_id: str,
    model_config: Optional[Dict[str, Any]] = None,
    skill_sources: Optional[List[str]] = None,
) -> Tuple[Any, SSEMonitoringMiddleware]:
    """
    创建用于 eval 测试的精简 Agent — 不含元工具，只加载目标 skill。

    与 deep_agent() 的关键差异：
      - 精简 system prompt（无 skill-creator/tool-creator 指令）
      - 不包含 propose_skill_save / propose_tool_save 等元工具
      - 不加载外部扩展工具（Tools/）
      - 可指定只加载特定 skill sources
    """
    from backend.task_settings import TaskSettings as _TS
    ts = _TS()
    model = get_llm_model(model_config, max_tokens_override=ts.max_tokens)

    eval_tools = []

    middleware = SSEMonitoringMiddleware(
        agent_name="EvalAgent",
        parent_agent=None,
        verbose=False,
    )

    is_local = settings.storage_backend == "local"

    sandbox_info = None
    if is_local:
        local_workspace = os.path.join(_WORKSPACE_DIR, session_id)
        os.makedirs(local_workspace, exist_ok=True)
        sandbox = LocalPreviewShellBackend(
            session_id=session_id,
            root_dir=local_workspace,
            virtual_mode=False,
            timeout=ts.sandbox_exec_timeout,
            inherit_env=True,
        )
        sandbox_workspace = local_workspace
    else:
        sandbox = FullSandboxBackend(
            session_id=session_id,
            user_id="eval_runner",
            base_dir=_WORKSPACE_DIR,
            sandbox_base_dir=_SANDBOX_WORKSPACE_DIR,
            execute_timeout=ts.sandbox_exec_timeout,
            max_output_chars=ts.max_output_chars,
        )
        sandbox_workspace = sandbox.workspace
        ctx = await sandbox.get_context()
        if ctx.get("success"):
            sandbox_info = ctx.get("data")

    system_prompt = _get_eval_system_prompt(sandbox_workspace, sandbox_info)

    agent_kwargs: Dict[str, Any] = {
        "model": model,
        "tools": eval_tools,
        "middleware": [middleware],
        "system_prompt": system_prompt,
    }

    # 构建后端（含 skill 路由）
    routes = {}
    resolved_sources: List[str] = []

    if skill_sources:
        for src in skill_sources:
            if src == _BUILTIN_SKILLS_ROUTE and os.path.isdir(_BUILTIN_SKILLS_DIR):
                routes[_BUILTIN_SKILLS_ROUTE] = FilesystemBackend(
                    root_dir=_BUILTIN_SKILLS_DIR, virtual_mode=True,
                )
                resolved_sources.append(_BUILTIN_SKILLS_ROUTE)
            elif src == _EXTERNAL_SKILLS_ROUTE:
                if is_local:
                    _ext_skills_dir = os.environ.get("EXTERNAL_SKILLS_DIR", "./Skills")
                    if os.path.isdir(_ext_skills_dir):
                        routes[_EXTERNAL_SKILLS_ROUTE] = FilesystemBackend(
                            root_dir=_ext_skills_dir, virtual_mode=True,
                        )
                        resolved_sources.append(_EXTERNAL_SKILLS_ROUTE)
                else:
                    routes[_EXTERNAL_SKILLS_ROUTE] = MongoSkillBackend(
                        user_id="eval_runner",
                        blocked_skills=set(),
                    )
                    resolved_sources.append(_EXTERNAL_SKILLS_ROUTE)
    else:
        if is_local:
            _ext_skills_dir = os.environ.get("EXTERNAL_SKILLS_DIR", "./Skills")
            if os.path.isdir(_ext_skills_dir):
                routes[_EXTERNAL_SKILLS_ROUTE] = FilesystemBackend(
                    root_dir=_ext_skills_dir, virtual_mode=True,
                )
                resolved_sources.append(_EXTERNAL_SKILLS_ROUTE)
        else:
            routes[_EXTERNAL_SKILLS_ROUTE] = MongoSkillBackend(
                user_id="eval_runner",
                blocked_skills=set(),
            )
            resolved_sources.append(_EXTERNAL_SKILLS_ROUTE)

    if routes:
        agent_kwargs["backend"] = lambda rt: CompositeBackend(default=sandbox, routes=routes)
    else:
        agent_kwargs["backend"] = sandbox

    if resolved_sources:
        agent_kwargs["skills"] = resolved_sources

    agent = create_deep_agent(**agent_kwargs)
    logger.info(f"[EvalAgent] session={session_id}, workspace={sandbox_workspace}, skills={resolved_sources}")
    return agent, middleware
