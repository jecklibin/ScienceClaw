---
id: F025
doc_kind: feature
status: active
created: 2026-06-06
updated: 2026-06-07
---

# F025: AIO Session Sandbox Runtime Adapter

## Goal

在本机尽可能多地验证、模拟并开发 AIO 会话级沙箱与 Runtime Adapter 相关功能模块，使代码同步到内网后，内网 Agent 只需要对真实 AIO create/status/delete API、镜像启动方式和少量环境差异做适配。

## Vision Anchor

- 原始请求或来源：用户明确希望本机先模拟创建 AIO 的方案，并提出可以启动一个固定 AIO 沙箱实例，让 `AioRuntimeProvider` 每次返回该沙箱；随后进一步确认目标不是分阶段路线图，而是一个清晰 goal：本机尽可能多地验证、模拟、开发相关功能模块，内网只做适配和少量迭代。
- 用户痛点或工程问题：真实 AIO 服务位于内网，本机无法直接依赖内网 create API；如果本机只写抽象不验证 Host 到执行面的 HTTP contract，内网 Agent 会承担大量不确定适配成本。
- 期望结果：Host Backend 保持可信控制面；AIO sandbox / Runtime Adapter 是不可信执行面；本机通过 `aio_fixed`、local adapter app、fake `aio` lifecycle smoke、runtime proxy、CDP connector、workspace upload/run/download helper 验证最小闭环。
- 非目标或边界：不在本机重写一套 AIO 调度器；不让 adapter 或 Harness 定义 `AcceptedTrace`、expected signals、Skill 真源或下载归因；不把 create 失败写成半真半假的 runtime record。
- Exit Gate 对照来源：[AIO session sandbox runtime adapter design](../rpa/aio-session-sandbox-runtime-adapter-design.md)、[EV-025](../evidence/EV-025-aio-session-sandbox-runtime-adapter.md)、AGENTS.md RPA/Harness 边界规则。

## Current Status

Active。当前本机 runtime contract、adapter stub、workspace helper、provider diagnostics、proxy/CDP 集成和 smoke 验证已经完成并通过 runtime 测试；真实内网 AIO create/status/delete 服务、镜像打包发布、真实浏览器 Playwright 执行面仍需在内网完成适配验证。

## Links

- Design: [AIO session sandbox runtime adapter design](../rpa/aio-session-sandbox-runtime-adapter-design.md)
- Handoff: [AIO Runtime Adapter 内网适配与交接指南](../rpa/aio-runtime-adapter-internal-handoff.md)
- Evidence: [EV-025 AIO Session Sandbox Runtime Adapter](../evidence/EV-025-aio-session-sandbox-runtime-adapter.md)
- Boundary ADR: [ADR-004 RPA Core Owns Recording Facts, Harness Adapts Only](../decisions/ADR-004-rpa-core-owns-recording-facts-harness-adapts-only.md)
- File API ADR: [ADR-005 AIO Runtime Adapter File API Policy](../decisions/ADR-005-aio-runtime-adapter-file-api-policy.md)

## Acceptance Criteria

- [x] `RUNTIME_MODE=aio_fixed` 可以返回本机预启动 adapter 沙箱，并通过 `/health` 验证 adapter contract。
- [x] `RUNTIME_MODE=aio` 可以通过可配置 HTTP create/status/delete contract 映射真实 AIO lifecycle，并支持 token、镜像、namespace、TTL、extra JSON、adapter env。
- [x] `AioApiRuntimeProvider` 支持 `--sample-response` 离线验证真实 AIO create/status response 样例能否映射为脱敏 runtime 摘要。
- [x] Provider diagnostics 和 runtime status/list 输出不泄露 API token、adapter token 或敏感 metadata。
- [x] Adapter app 暴露 `/health`、recording events、snapshot、execute-step、run-skill、files、downloads、browser info 等最小语义 contract。
- [x] Adapter app 支持 `--self-check` 启动前自检，输出与 `/health` 同源的脱敏诊断并用退出码区分 ok/degraded。
- [x] 提供 `RpaClaw/runtime-adapter/Dockerfile` 与 README，明确 adapter 镜像只启动执行面服务、复用 self-check healthcheck，并记录本机 build 与 AIO env contract。
- [x] Host runtime proxy 和 CDP connector 使用 `route_base_url` 与 session runtime token，不把用户侧 `Authorization` 透传给 adapter。
- [x] Workspace helper 可以上传本地 Skill/input 目录、触发 adapter run、枚举并拉回带 `sha256` 的执行产物。
- [x] 本机 smoke 覆盖 `aio_fixed` 与 fake `aio` lifecycle，不依赖真实内网 AIO 服务，并在输出中包含脱敏的 `adapter_self_check`。
- [x] AIO create 阶段失败以脱敏 provider acquisition error 分类，不写入半真 runtime record。
- [x] `SessionRuntimeManager.ensure_runtime()` 会复用并 refresh 同 session 的 `creating` runtime，避免真实 AIO 异步创建期间重复 create 沙箱。
- [x] Runtime proxy 只允许 `status=ready` 的 runtime 进入 adapter 执行面；`creating` 等未就绪状态返回脱敏的 503 `runtime_not_ready`，避免把异步创建中的 sandbox 当成已可用执行面。
- [x] CDP connector 只允许 `status=ready` 的 session runtime 获取 adapter `browser_info` / CDP URL；`creating` 等未就绪状态直接返回脱敏错误，不触达 adapter client。
- [x] `RuntimeAdapterClient` 将 adapter HTTP 4xx/5xx 包装成脱敏的 `RuntimeAdapterClientError`，保留 method/path/status/detail，避免 Host 日志或内网联调信息泄漏 runtime token。
- [x] `SessionRuntimeManager.ensure_runtime()` 使用 `session_id` 作为 runtime record `_id`，并在 EKS 多实例 duplicate insert 竞争下清理本实例刚创建的 runtime、复用已有 record，降低同 session 重复创建 AIO sandbox 的风险。
- [x] Adapter `/health` 的 401/403 鉴权失败会在 provider refresh 中分类为 `adapter_health_unauthorized`，并只写入 method/path/status code 等非敏感诊断字段。
- [x] AIO delete 阶段失败以脱敏 provider lifecycle error 分类为 `aio_delete_unavailable`，避免 destroy/cleanup 日志泄漏 AIO API token 或 adapter token。
- [x] Adapter 本机执行端点对 stdout/stderr 做有界返回，并用 `stdout_truncated` / `stderr_truncated` 标记截断，避免不可信执行日志撑爆 Host 代理和诊断链路。
- [x] `RuntimeAdapterClientError.detail` 会递归脱敏 adapter 错误正文中的敏感 key 和 runtime token 字符串，保留非敏感 detail 供内网联调。
- [x] Host runtime proxy 会过滤 adapter upstream response 中的 `Set-Cookie`、`Authorization`、`WWW-Authenticate` 等认证/会话 header，避免不可信执行面向前端下发会话状态。
- [x] Host runtime proxy 不会把前端/Host 侧 `Cookie`、`Proxy-Authorization` 等会话 header 转发给 adapter，只注入 session runtime token。
- [x] Adapter `/files/write` 对单文件写入做 10MiB 上限检查，并在 oversized 请求时返回 413 且不创建半成品目录或文件。
- [x] Adapter `/files/download` 对单文件下载做 50MiB 上限检查，并在读取文件内容前拒绝 oversized artifact。
- [x] Adapter `/rpa/downloads` 枚举下载产物时会跳过 oversized artifact 的 sha256 计算，保留 `name/path/size` 并返回 `hash_status=skipped_oversized`。
- [x] Workspace helper 识别 `hash_status=skipped_oversized`，不会继续调用 `/files/download` 拉取超大产物，并在结果中标记 `download_status=skipped_oversized`。
- [x] Workspace helper 上传本地 Skill/input 目录时会在读取文件内容前检查 10MiB 单文件上限，避免 Host 侧无界读入后再等待 adapter 拒绝。
- [x] Adapter file API 限制值收敛到 `adapter_file_policy` 共享模块，adapter app 与 Host workspace helper 不再各自维护写入/下载上限常量。
- [x] Adapter `/health.config.file_policy` 暴露非敏感 file policy diagnostics，便于内网 adapter 镜像 smoke 核对 ADR-005 限制值。
- [x] AIO provider refresh 会把 adapter health 中的 `file_policy` 白名单字段写入 runtime metadata，便于 Host status/list 和 smoke 侧诊断镜像策略。
- [x] `RpaClaw/runtime-adapter/README.md` 记录内网接入核对清单，明确 health/file policy/smoke 字段、真实 AIO 仍需验收项和 adapter 不得拥有的产品事实。
- [x] `adapter_smoke --mode aio_real` 使用真实 `AIO_RUNTIME_*` 配置调用外部 AIO lifecycle，避免内网把 fake `--mode aio` smoke 误当作真实 AIO smoke。
- [x] `aio_real` smoke 在主流程失败且 cleanup/delete 也失败时保留主失败，避免内网联调时 AIO delete 问题遮蔽 adapter health/skill/root cause。
- [x] `adapter_smoke --mode aio_real --keep-runtime` 可在内网排障时保留真实 AIO 沙箱，默认仍会 cleanup，避免调试能力和资源释放边界混在一起。
- [x] `aio_real` smoke 输出包含与 `/health` 同源的 `adapter_self_check` 字段，和本机/fake smoke 的交接输出形状保持一致。
- [x] `get_runtime(refresh=True)` 与 `list_runtimes(refresh=True)` 会完整持久化 provider refresh 后的 runtime record，避免 EKS 多实例间只同步 `status` 而丢失 adapter diagnostics/route metadata。
- [x] `SessionRuntimeManager` 在 duplicate-created cleanup、orphan cleanup 和 expired cleanup 的 warning 日志中会脱敏 provider 原始 delete 异常，避免 AIO API token、adapter token、裸 runtime token 或 Authorization 片段进入 Host 控制面日志。
- [x] 设计文档明确外网/本机阶段不完整模拟 AIO 调度平台，只验证 Host/Adapter 架构边界、semantic API、诊断与闭环；真实 AIO 字段、镜像发布、路由、浏览器/CDP 稳定性和资源释放留到内网 `aio_real` smoke 最终验收。
- [x] Runtime proxy WebSocket/CDP 通道会过滤客户端 `Sec-WebSocket-*` 握手头、Host Cookie 和用户 Authorization，只向 adapter 注入 session runtime token，避免客户端握手状态污染不可信执行面连接。
- [x] Runtime Adapter 执行 `execute-step` 和 `run-skill` 子进程时会清洗 token/secret/authorization/password/credential 类环境变量，避免 adapter bearer token、AIO API token 或 Host 侧凭据继续传给不可信脚本。
- [ ] 内网真实 AIO create/status/delete API 与 route 字段完成适配。
- [ ] 自定义 adapter 镜像发布到内网镜像仓库，并在真实 AIO 中启动后通过 `/health` 与 browser/CDP smoke。

## Patch History

| Patch | Date | Commit | Symptom | Root Cause | Protection | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F025.1 | 2026-06-06 | pending | `creating` runtime 已可被 manager 复用，但 runtime proxy 仍会立即转发到 adapter route。 | 创建复用语义和执行面 ready 语义没有在 proxy 层分离。 | `test_runtime_proxy_http_returns_not_ready_for_creating_runtime` 断言 503 脱敏响应且不触达 upstream。 | done |
| F025.2 | 2026-06-06 | pending | session CDP URL 获取会对 `creating` runtime 继续调用 adapter `browser_info`。 | CDP connector 没有继承 runtime proxy 的执行面 ready gate。 | `test_fetch_cdp_url_rejects_non_ready_session_runtime` 断言未就绪 runtime 报错且不构造 adapter client。 | done |
| F025.3 | 2026-06-06 | pending | adapter 4xx/5xx 会以原始 `httpx.HTTPStatusError` 形式冒泡，内网联调日志容易混入 request/Authorization 上下文。 | `RuntimeAdapterClient` 缺少 Host 侧统一、脱敏的 adapter 错误边界。 | `test_runtime_adapter_client_wraps_http_errors_without_leaking_runtime_token` 断言 status/path/detail 保留且 token 不出现在异常字符串。 | done |
| F025.4 | 2026-06-06 | pending | 内网 EKS 多实例 Host Backend 可能同时对同一 session 执行 `ensure_runtime()`，各自创建 AIO sandbox 后再写 runtime record。 | runtime record 没有使用 `session_id` 唯一身份，manager 对 duplicate insert 没有恢复路径。 | `test_ensure_runtime_recovers_from_duplicate_insert_created_by_another_host_instance` 断言 duplicate 时清理本实例创建物并复用已有 record。 | done |
| F025.5 | 2026-06-06 | pending | adapter `/health` token、路由或上游错误都被压成 `adapter_health_unavailable`，内网联调无法快速区分鉴权配置问题和普通不可达。 | provider 捕获了所有 health 异常，没有消费 `RuntimeAdapterClientError` 的脱敏 method/path/status 结构。 | `test_aio_fixed_runtime_provider_refresh_reports_sanitized_adapter_health_auth_error` 断言 403 分类为 `adapter_health_unauthorized`，保留 method/path/status code 且不泄漏 runtime token。 | done |
| F025.6 | 2026-06-06 | pending | destroy/cleanup 触发真实 AIO DELETE 失败时，底层 HTTP/client 异常会直接冒泡，日志可能混入 AIO API token 或 adapter token。 | `AioApiRuntimeProvider.delete_runtime()` 没有像 create 阶段一样建立脱敏 lifecycle error 边界。 | `test_aio_api_runtime_provider_delete_wraps_lifecycle_failure_without_leaking_tokens` 断言 delete 失败包装为 `AioRuntimeProviderError(operation="delete", reason="aio_delete_unavailable")` 且异常字符串不含 token。 | done |
| F025.7 | 2026-06-07 | pending | adapter 执行命令或 Skill 时 stdout/stderr 原样返回，脚本失控或日志暴涨会让 Host proxy、诊断响应和 artifact closeout 承担无界 payload。 | 本机 adapter stub 缺少执行输出上限和截断标记，未把 AIO sandbox 当作不可信执行面处理。 | `test_runtime_adapter_execute_step_bounds_stdout_and_stderr` 断言 stdout/stderr 被限制在 4096 字符以内，并返回 `stdout_truncated` / `stderr_truncated`。 | done |
| F025.8 | 2026-06-07 | pending | adapter 或网关如果把 runtime token 写入 JSON 错误正文，`RuntimeAdapterClientError.detail` 和异常字符串仍可能泄漏 token。 | F025.3 只包装了 httpx 错误并避免 request 上下文泄漏，没有递归清洗 adapter response body。 | `test_runtime_adapter_client_sanitizes_sensitive_adapter_error_detail` 断言敏感 key 与字符串中的 runtime token 都替换为 `<redacted>`，非敏感 detail 仍保留。 | done |
| F025.9 | 2026-06-07 | pending | Host runtime proxy 会把 adapter upstream 的 `Set-Cookie`、`Authorization`、`WWW-Authenticate` 等响应头原样转给前端，不可信执行面可能下发会话状态或认证挑战。 | proxy 只过滤了 hop-by-hop response header，没有建立 adapter response header 的认证/会话边界。 | `test_runtime_proxy_http_filters_sensitive_upstream_response_headers` 断言敏感 response headers 被过滤，普通诊断 header 仍保留。 | done |
| F025.10 | 2026-06-07 | pending | 前端请求经 Host runtime proxy 进入 adapter 时，Host 侧 `Cookie` / `Proxy-Authorization` 等会话 header 会被转发给不可信执行面。 | request header 过滤只移除了 `Authorization` 和少量 hop-by-hop header，没有把 Host 会话 cookie 与 proxy auth 视为控制面秘密。 | `test_runtime_proxy_http_does_not_forward_host_cookie_or_proxy_auth` 断言 cookie/proxy auth 不进 adapter，`X-Trace-Id` 等非敏感诊断 header 保留，adapter auth 只来自 runtime token。 | done |
| F025.11 | 2026-06-07 | pending | Adapter `/files/write` 可以写入任意大小的 content/base64 payload，失控上传会让不可信执行面消耗 Host proxy、adapter 内存和 sandbox 磁盘资源。 | workspace 写入入口只有路径边界，没有单文件资源边界；目录会在大小检查前创建。 | `test_runtime_adapter_write_file_rejects_oversized_content` 断言 10MiB+1 payload 返回 413，错误脱敏且不留下半成品目录。 | done |
| F025.12 | 2026-06-07 | pending | Adapter `/files/download` 会对 workspace 内任意文件直接 `read_bytes()`，脚本或浏览器生成的大 artifact 可能撑爆 Host proxy/adapter 内存。 | download 入口只有 workspace/path/type 边界，没有在读取文件内容前检查 artifact 大小。 | `test_runtime_adapter_download_file_rejects_oversized_file` 断言超过下载上限时返回 413，且 focused/app/runtime 回归保持通过。 | done |
| F025.13 | 2026-06-07 | pending | Adapter `/rpa/downloads` 为了返回 sha256 会对每个下载产物 `read_bytes()`，大 artifact 即使不会被下载也会在列表阶段消耗内存。 | 下载列表 contract 没有把 artifact metadata 枚举和大文件内容读取分离。 | `test_runtime_adapter_lists_oversized_download_without_hashing` 断言大文件仍出现在列表中，但 `sha256=None` 且 `hash_status=skipped_oversized`。 | done |
| F025.14 | 2026-06-07 | pending | Workspace helper 收到 `hash_status=skipped_oversized` 后仍会继续调用 `/files/download`，导致 Host 侧遇到 413 或 fake client KeyError，诊断不稳定。 | Helper 没有消费 adapter 下载列表的新 contract，把 artifact metadata 枚举和实际下载动作重新耦合。 | `test_run_uploaded_skill_skips_oversized_download_without_fetching` 断言 helper 保留 artifact 元数据、标记 `download_status=skipped_oversized`，且不拉取文件。 | done |
| F025.15 | 2026-06-07 | pending | Workspace helper 上传本地 Skill/input 目录时会先 `read_bytes()` 并 base64 编码，再由 adapter `/files/write` 拒绝超大文件。 | Host staging helper 没有继承 adapter 写入大小 contract，导致资源边界只存在于执行面。 | `test_upload_directory_rejects_oversized_file_before_reading` 断言 helper 在本地发现 oversized 文件并且不会产生 adapter write。 | done |
| F025.16 | 2026-06-07 | pending | ADR-005 已定义 file API policy，但 adapter app 与 Host workspace helper 仍各自维护 10MiB/50MiB 常量，后续内网适配容易静默漂移。 | file-boundary policy 只有文档和局部实现，没有共享代码锚点。 | `test_runtime_adapter_file_policy_is_shared_by_adapter_and_host_helper` 断言 adapter app/helper 都引用 `adapter_file_policy` 的限制值。 | done |
| F025.17 | 2026-06-07 | pending | 内网 adapter 镜像 smoke 只能看到 `/health` ok/degraded，看不到镜像实际携带的 file API 限制值。 | ADR-005 的共享 policy 没有进入 health diagnostics，联调时需要读代码或猜镜像版本。 | `test_runtime_adapter_health_reports_local_surface` 断言 `/health.config.file_policy` 暴露写入/下载上限和 oversized hash 状态。 | done |
| F025.18 | 2026-06-07 | pending | `/health.config.file_policy` 已可见，但 Host runtime metadata 仍只保存 health status/version/contract，status/list 无法直接显示 adapter 镜像文件策略。 | Provider health diagnostic 没有白名单消费 adapter file policy。 | `test_aio_fixed_runtime_provider_records_adapter_file_policy_metadata` 断言 file policy 进入 runtime metadata，smoke 回归断言 fake/local AIO 都带出该 metadata。 | done |
| F025.19 | 2026-06-07 | pending | 内网接入者需要从长设计文、ADR 和 smoke 输出中拼出真实 AIO 联调清单，交接面不够集中。 | Adapter image README 只记录 build/env/smoke，缺少 health/file policy 字段、AIO wiring 与内网验收边界清单。 | README 增补 health/file policy diagnostics、bounded inline file policy、inner-network handoff checklist，并通过 AgentMentor strict 与 diff check 验证。 | done |
| F025.20 | 2026-06-07 | pending | README 建议内网用 `adapter_smoke --mode aio` 跑真实 AIO wiring，但该模式实际启动本进程 fake AIO API。 | fake lifecycle smoke 与真实 AIO lifecycle smoke 没有在 CLI 语义上分离。 | `test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake` 断言 `aio_real` 使用传入真实 provider 配置、保留脱敏输出并完成 Skill upload/run/download 闭环。 | done |
| F025.21 | 2026-06-07 | pending | `aio_real` smoke 主流程失败后仍会在 `finally` 执行 AIO delete；若 delete 也失败，最终错误会变成 `aio_delete_unavailable`。 | cleanup 错误没有和 smoke 主失败分层，导致内网联调 root cause 容易被销毁阶段问题遮蔽。 | `test_real_aio_adapter_smoke_preserves_primary_failure_when_cleanup_fails` 断言主 `/health` 鉴权失败优先保留，cleanup delete 失败不覆盖主异常且不泄漏 token。 | done |
| F025.22 | 2026-06-07 | pending | 内网真实 AIO smoke 失败时可能需要保留 sandbox 看日志、CDP 或 route 状态，但 CLI 只能默认 delete。 | `run_real_aio_adapter_smoke(delete_on_finish=False)` 只存在于代码调用面，没有暴露给内网交接命令。 | `test_adapter_smoke_cli_keep_runtime_disables_real_aio_cleanup` 断言 `--keep-runtime` 只对 `aio_real` 传递 `delete_on_finish=False`，默认 cleanup 语义不变。 | done |
| F025.23 | 2026-06-07 | pending | README 要求内网确认 `adapter_self_check.status`，但 `aio_real` smoke 输出只有 `health` 字段。 | 真实 AIO smoke 只能通过 adapter HTTP `/health` 读取同源诊断，没有把该诊断复制到统一的 `adapter_self_check` 输出字段。 | `test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake` 断言 `aio_real` 返回 `adapter_self_check == health` 且 status 为 ok。 | done |
| F025.24 | 2026-06-07 | pending | `get_runtime(refresh=True)` 与 `list_runtimes(refresh=True)` 返回值带有 provider refresh 的 metadata/route 更新，但 repository 只 `$set status`。 | status/list refresh 持久化逻辑和 `ensure_runtime()` 的完整 record 持久化逻辑不一致，EKS 多实例共享 record 会丢失 adapter diagnostics。 | `test_get_runtime_can_refresh_status_and_persist` 与 `test_list_runtimes_refreshes_and_persists_updates` 断言 refreshed `route_base_url` 与 metadata 完整写回 repository。 | done |
| F025.25 | 2026-06-07 | pending | manager 在 duplicate-created cleanup、orphan cleanup 或 expired cleanup 遇到 provider 原始 delete 异常时，会把异常字符串直接写入 warning 日志。 | `AioApiRuntimeProvider.delete_runtime()` 已有脱敏错误边界，但 manager 仍可能被其他 provider、底层 client 或未包装异常绕过，Host 控制面日志缺少最后一道脱敏保护。 | `test_ensure_runtime_sanitizes_duplicate_created_cleanup_failure_log` 与 `test_cleanup_expired_sanitizes_delete_failure_log` 断言原始 token/Authorization 不进入 `backend.runtime.session_runtime_manager` warning 日志。 | done |
| F025.26 | 2026-06-07 | pending | manager warning 日志已能脱敏 key/Bearer 形态的 secret，但底层异常如果只裸露当前 runtime token 字符串，仍可能进入 Host 控制面日志。 | F025.25 的脱敏 helper 只看异常文本形态，没有把当前 `SessionRuntimeRecord.runtime_token` 作为已知敏感值参与替换。 | `test_cleanup_orphans_sanitizes_bare_runtime_token_delete_failure_log` 断言裸 `runtime_token` 不进入 orphan cleanup warning 日志；focused log set、manager 回归和 runtime 包级回归保持通过。 | done |
| F025.27 | 2026-06-07 | pending | 设计文档开头说明了 Host/执行面边界，但内网接手者仍可能把 fake lifecycle 或 fixed sandbox 误读成“外网完整模拟 AIO 平台”。 | 背景缺少外网可验收范围与内网最终验收范围的显式分层，容易让后续 Agent 重建调度器或把 fake smoke 当真实 AIO smoke。 | `docs/rpa/aio-session-sandbox-runtime-adapter-design.md` 在“背景与目标”补充外网只收敛架构/接口/诊断/闭环，内网负责真实 create/status/delete、镜像、路由、CDP 和资源释放验收，并通过 AgentMentor strict 与 diff check 验证。 | done |
| F025.28 | 2026-06-07 | pending | WebSocket/CDP proxy 会把前端 `Sec-WebSocket-*` 握手头作为 `additional_headers` 传给 adapter upstream，可能造成重复握手头或客户端连接状态污染。 | proxy 的通用 upstream header 过滤覆盖了 Cookie/Authorization/hop-by-hop，但没有把 WebSocket 握手头视为由 Host websocket client 重新生成的协议头。 | `test_runtime_proxy_websocket_headers_do_not_forward_client_handshake_headers` 断言 `Sec-WebSocket-*`、Host Cookie 和用户 Authorization 不进入 upstream headers，且 runtime token 仍由 Host 注入；proxy 与 runtime 包级回归通过。 | done |
| F025.29 | 2026-06-07 | pending | Runtime Adapter 本地执行 `execute-step` / `run-skill` 时使用 `os.environ.copy()`，子进程可以读取 `RUNTIME_ADAPTER_TOKEN`、`AIO_RUNTIME_API_TOKEN` 或 Host authorization 类环境变量。 | adapter 进程环境和不可信脚本/Skill 子进程环境没有分层，执行面 bearer token 与 lifecycle token 会被继续下发给任意脚本。 | `test_runtime_adapter_execute_step_scrubs_sensitive_environment` 与 `test_runtime_adapter_run_skill_scrubs_sensitive_environment` 断言敏感环境变量对子进程不可见但 `PYTHONIOENCODING=utf-8` 保留；adapter app 与 runtime 包级回归通过。 | done |

| F025.30 | 2026-06-07 | pending | Docker Desktop 启动后首次补跑 adapter image build 时，构建上下文过大、`COPY backend/backend` 路径不匹配，且镜像安装完整 Host Backend requirements 导致 build 慢且不符合 adapter 执行面边界。 | adapter image contract 只有文件存在和文档说明，尚未在真实 Docker daemon 上验证；Docker context、包路径和依赖边界没有被 build 证据约束。 | 新增 `RpaClaw/.dockerignore`、`runtime-adapter/requirements.txt`，修正 Dockerfile 只复制 `backend` 包并安装 adapter 最小依赖；`docker build -f runtime-adapter/Dockerfile -t rpaclaw-runtime-adapter:dev .`、容器内 `adapter_app --self-check`、带 token self-check 均通过，镜像 tag 生成且诊断不泄露 token。 | done |
| F025.31 | 2026-06-08 | pending | 外网只用内存 fake AIO smoke 或手工 `docker run`，仍无法证明 Host provider 通过 AIO lifecycle 创建真实 adapter 容器后，可以经 adapter 启动浏览器并注入监听脚本。 | fake AIO lifecycle 与真实容器执行面之间缺少一条可复现的本机桥接验证；同时 Windows 本机 `httpx` 默认信任系统代理，访问 `127.0.0.1` adapter route 时会被代理劫持成 502。 | 新增 `local_fake_aio_service` 与 `adapter_smoke --mode aio_container`，fake AIO `create/status/delete` 会真实启动并回收 adapter 容器；`RuntimeAdapterClient` 默认 `trust_env=False` 避免内部 adapter 控制通道走系统代理；真实 smoke 返回 adapter health `ok`、browser `status=success` 且 listener `status=injected`。 | done |
| F025.32 | 2026-06-08 | pending | F025 的内网接手信息分散在长设计文、adapter README、Feature patch history 和 Evidence 中，内网 Agent 容易漏掉真实 AIO 字段映射、镜像发布、CDP route、EKS 多实例和非目标边界。 | 外网验证已收口，但缺少一个按内网适配顺序组织的单一 handoff 入口。 | 新增 `docs/rpa/aio-runtime-adapter-internal-handoff.md`，集中列出阅读顺序、关键代码入口、配置项、真实 AIO smoke、CDP/browser 验收、常见失败归因、EKS 多实例注意事项和完成标准，并从 Feature/EV/README 链接。 | done |

## Patch Churn Review

2026-06-07：当前 15 个补丁都收敛在同一个边界问题：Host 可以复用或调用 session runtime，但进入 adapter 执行面之前必须先确认 lifecycle/contract/ready/error/ownership/output-boundary/header-boundary/file-boundary 语义清晰。它们不是站点经验规则或 Harness 对 Core 事实的侵入，也没有改变 SOP->Skill 主链路；相反，它们把 AIO 异步生命周期、CDP 连接、adapter HTTP 错误、adapter health 鉴权/路由诊断、delete lifecycle 失败、执行输出边界、adapter 错误正文脱敏、proxy request/response header 边界、adapter/Host 文件读写与下载枚举/拉取边界和 EKS 多实例竞争的边界前移到 Host/Adapter contract。

2026-06-07 收口：file-boundary patch 已经抽出 [ADR-005 AIO Runtime Adapter File API Policy](../decisions/ADR-005-aio-runtime-adapter-file-api-policy.md)。后续若继续出现文件上传、下载、hash、artifact 拉取或大文件传输问题，应优先对照 ADR-005 扩展统一 file API policy；只有当问题跨越 ready/error/token/duplicate-create/delete-cleanup/output-boundary/header-boundary 等非文件边界时，才继续评估共享 runtime execution-plane guard/diagnostic helper、数据库唯一索引/lease、adapter 输出协议、错误脱敏协议或 proxy header policy ADR。

2026-06-07 ADR-005 实现收口：file API 限制值已抽到 `backend.runtime.adapter_file_policy`，adapter app 与 Host workspace helper 共同引用该模块，避免 bounded inline file contract 在两侧静默漂移。

2026-06-08 containerized fake AIO 收口：F025.31 仍属于同一 Host/Adapter 边界收敛，而不是模拟完整 AIO 调度平台。它只证明外网能验证的 create/status/delete 字段映射、adapter image 启动、token/env 传递、ready 诊断、浏览器启动和 listener 注入闭环；真实 AIO 的调度、镜像发布、路由/CDP 网络稳定性与资源释放最终验收仍保留给内网。

2026-06-08 handoff 收口：F025.32 不新增 runtime 行为，只把内网适配路径从长设计文中提炼为可执行指南，避免内网 Agent 把 fake AIO、本机固定沙箱或 adapter image README 误当成完整验收流程。

## Evidence

见 [EV-025 AIO Session Sandbox Runtime Adapter](../evidence/EV-025-aio-session-sandbox-runtime-adapter.md)。

## Next Step

进入人工 review，并把当前分支同步到内网后按 [AIO Runtime Adapter 内网适配与交接指南](../rpa/aio-runtime-adapter-internal-handoff.md) 替换真实 AIO 字段/路径、发布 adapter 镜像、运行 `aio_real` smoke。
