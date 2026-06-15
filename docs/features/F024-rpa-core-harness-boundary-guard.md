---
id: F024
doc_kind: feature
status: review
created: 2026-06-02
updated: 2026-06-15
---

# F024: RPA Core / Harness Boundary Guard

## Goal

修复并约束 Harness 功能影响 RPA 主链路的问题。RPA 主链路是 `SOP / 自然语言录制 -> accepted trace -> TraceSkillCompiler -> SKILL.md / skill.py`；Harness 只能观察、复制、验证这些事实，不能反向定义或改写主链路事实。

## Vision Anchor

- 原始请求或来源：用户发现“点击列表中第一行的文件名称”会真实触发下载，但录制左侧步骤不再捕获下载事件，并明确要求 Harness 功能不能影响原有 SOP 转义 SKILL 核心链路。
- 用户痛点或工程问题：Harness controlled download / full-live 验证能力与产品录制 Core 共享 `RecordingRuntimeAgent`，边界不清时会让 Harness 验证诉求反向污染主链路。
- 期望结果：下载事件由 Core 录制边界捕获为 `trace.signals.download`；Harness 只验证该事实。Harness 变更若触碰 Core 文件，必须跑 Core SOP->SKILL 回归。
- 非目标或边界：不 fork 两套 RecordingRuntimeAgent / TraceSkillCompiler；不把 Harness expected signals 注入产品录制 trace；不新增站点特例或 legacy step fallback。
- Exit Gate 对照来源：本 Feature、ADR-004、EV-024、AGENTS.md 的 Core/Harness 边界规则。

## Current Status

实现完成，进入人工 review。已修复 simple click plan 缺少 download signal 捕获、Full SOP Harness capture 延迟下载归并、实时与轮询 timeline 下载展示、重复敏感 fill、以及 navigation trace 依赖 Harness 开关等边界问题。已建立 timeline 投影测试、Core/Harness 边界 ADR、Lesson 和项目级规则。Focused Core/Harness 回归与 Harness knowledge check 已通过。

## Links

- ADR: [ADR-004 RPA Core Owns Recording Facts, Harness Adapts Only](../decisions/ADR-004-rpa-core-owns-recording-facts-harness-adapts-only.md)
- Evidence: [EV-024 RPA Core Harness Boundary Guard Evidence](../evidence/EV-024-rpa-core-harness-boundary-guard.md)
- Lesson: [LL-002 Harness Must Not Define RPA Core Facts](../lessons/LL-002-harness-must-not-define-rpa-core-facts.md)
- Related Feature: [F019 RPA Harness Controlled Download Side Effects](F019-rpa-harness-controlled-download-side-effects.md)
- Existing ADR: [ADR-001 RPA Trace Is The Single Accepted Timeline](../decisions/ADR-001-rpa-trace-is-single-accepted-timeline.md)
- Existing ADR: [ADR-002 Trace Evidence Drives Compiler Strategy](../decisions/ADR-002-trace-evidence-driven-compiler-strategy.md)

## Acceptance Criteria

- [x] `RecordingRuntimeAgent` 的 simple `click` plan 能捕获真实 Playwright download event，并写入当前 accepted trace 的 `signals.download`。
- [x] `run_python` 与 simple `click` 使用同一种 Core download capture 结果结构。
- [x] timeline 投影只读取 trace 上已有 `signals.download`，不读取 Harness expected signals 或 controlled fixture。
- [x] 编译器仍只消费 accepted trace；Harness controlled download 仍是 replay/asset 验证能力，不定义产品录制事实。
- [x] AGENTS.md 增加可验证规则：Harness/RPA 变更触碰 Core 文件时必须跑 Core SOP->SKILL 回归。
- [x] 完整 focused Core/Harness 回归命令完成并记录在 EV-024。
- [x] Full SOP Harness capture 开启时，paused 期间晚到的 download event 在 append trace 前由 Core finalization 归并到当前 AI trace，并写入 Harness checkpoint 的 `trace_events.json`。

## Patch History

| Patch | Date | Commit | Symptom | Root Cause | Protection | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F024.1 | 2026-06-02 | `99a52749` | 开启 Full SOP Harness capture 后，“点击列表中第一行的文件名称”真实触发下载，但生成 Skill 第 10 步没有 `expect_download()`；不开 Harness 时偶尔能靠 standalone download fallback 成功。 | route 在 `agent.run()` 后立即 `append_trace()`，而 manager 的 paused pending download 可能在 Harness after checkpoint 期间才到达，错过当前 trace 的 pending merge 点。 | `test_apply_recording_agent_result_waits_for_paused_download_before_append`；`test_full_sop_capture_preserves_delayed_download_signal_in_core_trace`；EV-024 focused Core/Harness 回归。 | done |
| F024.2 | 2026-06-02 | `3a0effe1` | 内网验证发现生成脚本已有下载处理，但录制页左侧实时步骤仍只显示点击文件名，不显示下载副作用。 | 实时录制页 SSE 路径使用 `mapServerTraces()` 直接把 raw trace 映射为展示步骤，只读取 `description/user_instruction/action`，没有投影 trace 上已有的 `signals.download`。 | 新增 RecorderPage RED/GREEN 回归，要求 `trace_added` 中的 `signals.download.filename` 在左侧步骤中可见；实现只改展示字段，不新增 trace、不读取 Harness artifact、不触碰 compiler。 | done |
| F024.3 | 2026-06-02 | `1d4ed8f5` | 内网验证发现开启 Full SOP Harness capture 后，配置页录制步骤和生成 Skill 都出现两次密码输入；不开启 Harness capture 时正常。 | Harness capture 额外采集当前编辑框状态后，密码输入可能同时产生浏览器 input fill 和 current editable fill；中间的 focus click 被 Core/Harness 折叠后，两条 fill 指向同一密码框但 sequence 不相邻，旧去重只接受相邻 sequence，导致 accepted trace 多出一次密码 fill。 | Core recorder 的 fill 去重扩展为同一 target/frame/tab/source、相同 value、短时间窗口内可跨 sequence 空洞合并；Harness trace persistence 只对 trace 文案字段同步替换输入占位符，避免 raw input 从 `description` 泄漏；新增重复敏感 fill 回归并跑完整 manager 回归。 | done |
| F024.4 | 2026-06-02 | `1c2850c2` | 全面审视 Harness/Core 边界时发现，`navigate_active_tab()` 只有在 Full SOP Harness capture 开启时才同步 append navigation trace；不开 Harness 时依赖浏览器 `framenavigated` 异步回流，导致同一导航入口的 accepted trace 来源和时机受 Harness 开关影响。 | 导航 endpoint 为了写 Harness entry-navigation checkpoint，把 Core navigation trace 创建放进了 Harness 条件分支，同时只在 Harness 分支 suppress 底层 navigation event。 | Core navigation trace 创建上移为无条件主链路行为，底层 `framenavigated` suppress 也无条件执行以避免重复；Harness 开启时只额外写 before/after HTML checkpoint。新增不开 Harness 的 navigation trace 回归，并保留 Full SOP checkpoint 回归。 | done |
| F024.5 | 2026-06-03 | `dde739a1` | 内网验证发现 Core trace 已有 `signals.download.filename`，但录制页左侧步骤仍只显示点击文件名，轮询刷新后下载副作用不可见。 | F024.2 只修复 live `trace_added -> mapServerTraces()` 展示路径；录制页 3 秒轮询拿到 `session.timeline` 后走 `mapRpaTimelineProjection()`，该路径优先显示 `title`，丢弃后端 summary/raw trace 中的 download 展示信号。 | 只修前端 timeline projection 展示：当 projected item 的 `raw_trace.signals.download` 存在时，优先保留 summary 中的下载文件名或追加“并下载/并触发下载”。新增 RecorderPage RED/GREEN 回归覆盖轮询 projected timeline，不改 trace 捕获、Harness capture、compiler 或回放。 | done |
| F024.6 | 2026-06-03 | pending | 开启 Full SOP Harness capture 后，生成 Skill 最前面多出 `click body`，导致后续业务步骤编号/时序与未开启 Harness 的脚本不一致；弱 `textbox nth=0` 输入在回放中表现为查询条件未生效。 | 录制入口把启动阶段的无副作用 `body/html` focus click 当成业务 click 写入 Core accepted timeline；既有去噪只覆盖紧贴 fill 的 focus click，无法处理导航前的初始噪声点击。 | Core event 入口仅在 session 尚无 step/trace/recorded action 且点击目标为 `body/html`、无 download/navigation/popup/tab 等副作用证据时丢弃该点击；新增 `test_full_sop_harness_ignores_pre_navigation_body_click_noise`，并跑完整 manager、trace compiler、route trace 回归。 | done |

| F024.7 | 2026-06-03 | pending | Explicit `navigate_active_tab()` with a redirect could record two accepted navigation facts: one from `framenavigated` and one from the explicit navigation trace. | The event suppressor matched only the requested URL. Redirected final URLs escaped suppression during the explicit navigation window. | Core now suppresses all main-frame navigation events for the active tab while explicit `page.goto()` is in flight; the explicit trace records the final `after_page.url`. Added redirect regression plus manager/compiler/route focused checks. | done |
| F024.8 | 2026-06-15 | pending | Explicit URL navigation that redirects to SSO can compile replay code that starts from the observed login URL instead of the original business entry URL; random login nonce/state then breaks replay. | `TraceSkillCompiler._render_navigation_trace()` treated `trace.after_page.url` as the `page.goto()` target. That preserved trace-first observed facts, but mixed replay intent with post-navigation browser state. | Keep `after_page.url` as recorded fact, add explicit `signals.navigation.target_url/observed_url/redirected` evidence for `navigate_active_tab()`, and make standalone navigation replay prefer `signals.navigation.target_url` / `trace.value` before falling back to `after_page.url`. Focused compiler, manager, and SOP->Skill e2e regressions passed. | done |
| F024.9 | 2026-06-15 | pending | RPA page address-bar navigation through an SSO chain could still record late login redirect URLs as standalone navigation traces with empty `value`, so generated Skill replay still started from random login URLs. | `navigate_active_tab()` suppressed synchronous `framenavigated` events only while `page.goto()` was in flight. SSO chains can continue after `domcontentloaded`, so the late main-frame redirect escaped the explicit navigation window and entered the manual event recorder without the original target intent. | Core now keeps a pending explicit navigation trace per session/tab and folds later same-tab main-frame redirects into that trace until the next user action. The trace preserves `value/signals.navigation.target_url` as the replay target and updates `after_page.url/signals.navigation.observed_url` as observed browser fact. Focused manager, compiler, and SOP->Skill e2e regressions passed. | done |

## Patch Churn Review

F024 已出现 7 个补丁，但这些补丁不是同一站点规则的堆叠，而是同一边界原则在不同执行层的补齐：

- F024.1 处理 AI 执行边界的 download 归并时机，确保 Core accepted trace 拥有真实下载事实。
- F024.2 处理前端实时 timeline 投影，确保 UI 只展示 Core trace 已有事实，不从 Harness artifact 合成事实。
- F024.3 处理 manual recorder fill 归并与 Harness artifact 参数化，确保 Full SOP Harness capture 不能让同一输入动作在 Core accepted timeline 中变成两条事实。
- F024.4 处理 URL 导航入口，确保 Core navigation trace 是否产生不再依赖 Harness capture 开关。
- F024.5 补齐 projected session timeline 轮询路径的下载展示，确保 UI 刷新不会覆盖 live trace 中已展示的 Core download fact。
- F024.6 处理启动阶段无语义 `body/html` 点击噪声，确保 Full SOP Harness capture 不能让非业务 focus click 进入 Core accepted timeline 并改变 SOP->Skill 步骤顺序。
- F024.7 处理显式 URL 导航重定向窗口，确保一次 `navigate_active_tab()` 只拥有一条 accepted navigation fact，最终 URL 写入显式 trace，而不是由底层 `framenavigated` 再生成第二条 event trace。

零基审视结论：不需要 fork Harness/Core 双链路，也不需要让 Harness capture 过滤浏览器事件；这两条都会制造第二事实源。继续坚持 ADR-004：Core recorder 负责定义唯一录制事实，Harness 只观察、复制、验证。后续若再次出现 Harness 开启/关闭导致 accepted trace 语义不同，应优先补 Core 事实归并/副作用捕获的通用规则，并同时添加 Harness enabled/disabled 主链路回归。

## Evidence

见 [EV-024 RPA Core Harness Boundary Guard Evidence](../evidence/EV-024-rpa-core-harness-boundary-guard.md)。

## Next Step

进入人工 review。后续任何 Harness/RPA 变更若触碰 Core 文件，必须按 ADR-004 和 AGENTS.md 规则同时运行 Core SOP->SKILL focused regression。
