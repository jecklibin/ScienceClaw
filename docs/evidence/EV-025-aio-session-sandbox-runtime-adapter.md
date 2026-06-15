---
id: EV-025
doc_kind: evidence
scope: project
feature_refs:
  - docs/features/F025-aio-session-sandbox-runtime-adapter.md
created: 2026-06-06
updated: 2026-06-07
evidence_level: standard
---

# EV-025: AIO Session Sandbox Runtime Adapter

## Scope

验证本机已经形成 AIO 会话级沙箱 Runtime Adapter 的最小可交接能力，包括：

- `aio_fixed` 固定沙箱 provider。
- `aio` HTTP lifecycle provider。
- Runtime Adapter local FastAPI app。
- Runtime adapter client、proxy、CDP connector、workspace helper。
- 本机 fake AIO lifecycle smoke。
- AIO create acquisition error 的脱敏分类与 manager 不入库行为。
- AIO delete lifecycle error 的脱敏分类，避免 destroy/cleanup 日志泄漏 AIO API token 或 adapter token。
- AIO create/status response 样例的离线映射诊断。
- `creating/pending` runtime 的 manager 复用语义，避免真实 AIO 异步创建期间重复 create。
- EKS 多实例 Host Backend 竞争下，同 session duplicate insert 的清理与复用语义，降低重复创建 AIO sandbox 的风险。
- Runtime proxy 的执行面 ready gate：`creating` 等未就绪 runtime 返回脱敏 503，不触达 adapter upstream。
- Runtime proxy 的 request header 边界：Host/前端侧 Cookie、Proxy-Authorization 等会话 header 不会进入 adapter。
- Runtime proxy 的 response header 边界：adapter upstream 的认证/会话响应头会被过滤，普通诊断 header 仍可透传。
- Runtime proxy 的 WebSocket/CDP request header 边界：客户端 `Sec-WebSocket-*` 握手头、Host Cookie 和用户 Authorization 不会进入 adapter upstream，WebSocket client 握手由 Host 重新生成，adapter 只接收 session runtime token 和安全诊断头。
- CDP connector 的执行面 ready gate：`creating` 等未就绪 runtime 不调用 adapter `browser_info`。
- Runtime adapter client 的脱敏 HTTP 错误边界：adapter 4xx/5xx 保留 method/path/status/detail，但不泄漏 runtime token。
- Runtime adapter client 对 adapter 错误正文递归脱敏：敏感 key 和字符串中的 runtime token 会替换为 `<redacted>`。
- Provider refresh 对 adapter `/health` 的脱敏结构化诊断：401/403 分类为 `adapter_health_unauthorized`，metadata 只保留 method/path/status code。
- Adapter execute-step/run-skill 输出有界返回，stdout/stderr 过长时保留截断文本和 `*_truncated` 标记。
- Adapter execute-step/run-skill 子进程环境会移除 token、secret、authorization、password、credential 类环境变量，避免 adapter bearer token、AIO API token 或 Host 侧凭据继续暴露给不可信脚本。
- Adapter `/files/write` 单文件写入有界返回，超过 10MiB 时返回 413，且不创建半成品目录或文件。
- Adapter `/files/download` 单文件下载有界返回，超过 50MiB 时在读取文件内容前返回 413。
- Adapter `/rpa/downloads` 枚举 oversized artifact 时跳过 sha256 计算，避免列表阶段无界读取大文件。
- Workspace helper 会消费 `hash_status=skipped_oversized`，保留 artifact metadata 但不继续拉取 oversized artifact。
- Workspace helper 上传本地 Skill/input 目录时会在 `read_bytes()` 前检查 10MiB 单文件上限。
- ADR-005 沉淀 adapter file API policy，约束 bounded inline file contract、oversized artifact metadata、Host helper 前置边界和后续大文件扩展路径。
- `backend.runtime.adapter_file_policy` 作为 ADR-005 的共享代码锚点，adapter app 与 Host workspace helper 共同引用写入/下载限制值。
- Adapter `/health.config.file_policy` 暴露非敏感 file policy diagnostics，供内网镜像 smoke 核对实际限制值。
- AIO provider refresh 会将 adapter health 中的 file policy 白名单字段写入 runtime metadata，供 Host status/list 与 smoke 诊断使用。
- `adapter_smoke --mode aio_real` 使用真实 `AIO_RUNTIME_*` 配置调用外部 AIO lifecycle endpoint，不启动本进程 fake AIO API。
- `aio_real` smoke cleanup/delete 失败不会遮蔽已经发生的主流程失败，避免内网联调 root cause 被销毁阶段问题覆盖。
- `adapter_smoke --mode aio_real --keep-runtime` 可在内网排障时保留真实 AIO sandbox，默认仍会 cleanup。
- `aio_real` smoke 输出包含与 `/health` 同源的 `adapter_self_check` 字段，使真实 AIO smoke 与本机/fake smoke 输出形状一致。
- `get_runtime(refresh=True)` 与 `list_runtimes(refresh=True)` 会把 provider refresh 后的完整 runtime record 写回共享 repository，保证 EKS 多实例 status/list 诊断一致。
- `SessionRuntimeManager` 在 duplicate-created cleanup、orphan cleanup 和 expired cleanup 的 warning 日志中对 provider 原始 delete 异常做最后一层脱敏，避免 token、Authorization、secret 或裸 runtime token 片段进入 Host 控制面日志。
- 设计文档在入口处明确外网/本机阶段只提前验证与真实 AIO 无关的架构、接口、安全和诊断不确定性；真实 AIO create/status/delete、镜像发布、路由、浏览器/CDP 稳定性和资源释放仍由内网 `aio_real` smoke 与真实环境完成最终验收。
- Adapter 进程启动前 `--self-check` 自检与 smoke 输出中的 `adapter_self_check`。
- `RpaClaw/runtime-adapter` 镜像启动资产的本机 contract。
- `RpaClaw/runtime-adapter/README.md` 的内网接入核对清单，覆盖 health/file policy/smoke 字段、真实 AIO wiring 与仍需内网验收的边界。

不覆盖真实内网 AIO 服务、真实镜像发布、真实浏览器 Playwright 操作链路或 SOP->Skill Core 录制事实正确性。

## Commands

Focused create failure regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_create_wraps_lifecycle_failure_without_leaking_tokens RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_create_wraps_invalid_response RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_ensure_runtime_does_not_insert_when_provider_create_fails -q --basetemp .pytest-tmp-aio-create-failure-focused
```

Runtime manager regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-create-failure-manager
```

Response sample mapping RED / GREEN focused tests:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_diagnoses_create_response_sample_without_leaking_token RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_diagnoses_invalid_response_sample RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_diagnostic_cli_reads_sample_response_file -q --basetemp .pytest-tmp-aio-response-sample-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_diagnoses_create_response_sample_without_leaking_token RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_diagnoses_invalid_response_sample RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_diagnostic_cli_reads_sample_response_file -q --basetemp .pytest-tmp-aio-response-sample-green
```

Runtime manager regression after response sample mapping:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-response-sample-manager
```

Creating runtime reuse RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_ensure_runtime_reuses_creating_record_without_duplicate_create -q --basetemp .pytest-tmp-aio-creating-runtime-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_ensure_runtime_reuses_creating_record_without_duplicate_create -q --basetemp .pytest-tmp-aio-creating-runtime-green
```

Runtime manager regression after creating runtime reuse:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-creating-runtime-manager-green
```

Runtime package regression after creating runtime reuse:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-creating-runtime-runtime
```

Runtime proxy ready gate RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py -k creating_runtime -q --basetemp .pytest-tmp-aio-proxy-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py -k creating_runtime -q --basetemp .pytest-tmp-aio-proxy-green
```

Runtime proxy regression after ready gate:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py -q --basetemp .pytest-tmp-aio-proxy-full
```

Runtime package regression after proxy ready gate:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-proxy-runtime
```

CDP connector ready gate RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_cdp_connector.py -k non_ready -q --basetemp .pytest-tmp-aio-cdp-ready-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_cdp_connector.py -k non_ready -q --basetemp .pytest-tmp-aio-cdp-ready-green
```

CDP connector regression after ready gate:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_cdp_connector.py -q --basetemp .pytest-tmp-aio-cdp-ready-full
```

Runtime package regression after CDP connector ready gate:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-cdp-ready-runtime
```

Adapter client sanitized HTTP error RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_client.py -k wraps_http_errors -q --basetemp .pytest-tmp-aio-client-error-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_client.py -k wraps_http_errors -q --basetemp .pytest-tmp-aio-client-error-green
```

Adapter client regression after sanitized HTTP error:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_client.py -q --basetemp .pytest-tmp-aio-client-error-full
```

Runtime package regression after adapter client sanitized HTTP error:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-client-error-runtime
```

EKS multi-instance duplicate runtime insert RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -k "duplicate_insert or creates_when_missing" -q --basetemp .pytest-tmp-aio-multi-instance-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -k "duplicate_insert or creates_when_missing" -q --basetemp .pytest-tmp-aio-multi-instance-green
```

Runtime manager regression after EKS multi-instance duplicate insert handling:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-multi-instance-manager
```

Runtime package regression after EKS multi-instance duplicate insert handling:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-multi-instance-runtime
```

Manager cleanup log sanitization RED / GREEN focused tests:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_ensure_runtime_sanitizes_duplicate_created_cleanup_failure_log RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_cleanup_expired_sanitizes_delete_failure_log -q --basetemp .pytest-tmp-aio-manager-log-sanitize-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_ensure_runtime_sanitizes_duplicate_created_cleanup_failure_log RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_cleanup_expired_sanitizes_delete_failure_log -q --basetemp .pytest-tmp-aio-manager-log-sanitize-green
```

Runtime manager regression after cleanup log sanitization:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-manager-log-sanitize-manager
```

Runtime package regression after cleanup log sanitization:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-manager-log-sanitize-runtime
```

Manager bare runtime token log sanitization RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_cleanup_orphans_sanitizes_bare_runtime_token_delete_failure_log -q --basetemp .pytest-tmp-aio-manager-bare-token-log-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_cleanup_orphans_sanitizes_bare_runtime_token_delete_failure_log -q --basetemp .pytest-tmp-aio-manager-bare-token-log-green
```

Manager cleanup log sanitization focused set after bare token handling:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_ensure_runtime_sanitizes_duplicate_created_cleanup_failure_log RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_cleanup_expired_sanitizes_delete_failure_log RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_cleanup_orphans_sanitizes_bare_runtime_token_delete_failure_log -q --basetemp .pytest-tmp-aio-manager-log-sanitize-focused
```

Runtime manager regression after bare token log sanitization:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-manager-bare-token-log-manager
```

Runtime package regression after bare token log sanitization:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-manager-bare-token-log-runtime
```

Runtime proxy WebSocket handshake header RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py::test_runtime_proxy_websocket_headers_do_not_forward_client_handshake_headers -q --basetemp .pytest-tmp-aio-ws-handshake-header-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py::test_runtime_proxy_websocket_headers_do_not_forward_client_handshake_headers -q --basetemp .pytest-tmp-aio-ws-handshake-header-green
```

Runtime proxy regression after WebSocket handshake header filtering:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py -q --basetemp .pytest-tmp-aio-ws-handshake-header-proxy
```

Runtime package regression after WebSocket handshake header filtering:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-ws-handshake-header-runtime
```

Adapter subprocess environment scrub RED / GREEN focused tests:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_execute_step_scrubs_sensitive_environment RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_run_skill_scrubs_sensitive_environment -q --basetemp .pytest-tmp-aio-adapter-env-scrub-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_execute_step_scrubs_sensitive_environment RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_run_skill_scrubs_sensitive_environment -q --basetemp .pytest-tmp-aio-adapter-env-scrub-green
```

Adapter app regression after subprocess environment scrub:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py -q --basetemp .pytest-tmp-aio-adapter-env-scrub-app
```

Runtime package regression after subprocess environment scrub:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-env-scrub-runtime
```

AIO design handoff boundary documentation check:

```powershell
python C:\Users\HUAWEI\.codex\skills\using-agentmentor\scripts\knowledge_check.py --root E:\Work-Project\OtherWork\ScienceClaw --docs-path docs --strict
```

```powershell
git diff --check -- docs/rpa/aio-session-sandbox-runtime-adapter-design.md docs/features/F025-aio-session-sandbox-runtime-adapter.md docs/evidence/EV-025-aio-session-sandbox-runtime-adapter.md
```

Adapter health auth diagnostic RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_fixed_runtime_provider_refresh_reports_sanitized_adapter_health_auth_error -q --basetemp .pytest-tmp-aio-health-error-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_fixed_runtime_provider_refresh_reports_sanitized_adapter_health_auth_error -q --basetemp .pytest-tmp-aio-health-error-green
```

Runtime manager regression after adapter health auth diagnostic:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-health-error-manager
```

Runtime package regression after adapter health auth diagnostic:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-health-error-runtime
```

AIO delete failure RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_delete_wraps_lifecycle_failure_without_leaking_tokens -q --basetemp .pytest-tmp-aio-delete-failure-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_api_runtime_provider_delete_wraps_lifecycle_failure_without_leaking_tokens -q --basetemp .pytest-tmp-aio-delete-failure-green
```

Runtime manager regression after AIO delete failure handling:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-delete-failure-manager
```

Runtime package regression after AIO delete failure handling:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-delete-failure-runtime
```

Adapter execution output bound RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_execute_step_bounds_stdout_and_stderr -q --basetemp .pytest-tmp-aio-adapter-output-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_execute_step_bounds_stdout_and_stderr -q --basetemp .pytest-tmp-aio-adapter-output-green
```

Adapter app regression after output bound:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py -q --basetemp .pytest-tmp-aio-adapter-output-app
```

Runtime package regression after output bound:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-output-runtime
```

Adapter client sensitive error detail RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_client.py::test_runtime_adapter_client_sanitizes_sensitive_adapter_error_detail -q --basetemp .pytest-tmp-aio-client-detail-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_client.py::test_runtime_adapter_client_sanitizes_sensitive_adapter_error_detail -q --basetemp .pytest-tmp-aio-client-detail-green
```

Adapter client regression after sensitive error detail sanitization:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_client.py -q --basetemp .pytest-tmp-aio-client-detail-full
```

Runtime package regression after sensitive error detail sanitization:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-client-detail-runtime
```

Runtime proxy upstream response header RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py::test_runtime_proxy_http_filters_sensitive_upstream_response_headers -q --basetemp .pytest-tmp-aio-proxy-header-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py::test_runtime_proxy_http_filters_sensitive_upstream_response_headers -q --basetemp .pytest-tmp-aio-proxy-header-green
```

Runtime proxy regression after response header filtering:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py -q --basetemp .pytest-tmp-aio-proxy-header-full
```

Runtime package regression after response header filtering:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-proxy-header-runtime
```

Runtime proxy request header RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py::test_runtime_proxy_http_does_not_forward_host_cookie_or_proxy_auth -q --basetemp .pytest-tmp-aio-proxy-request-header-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py::test_runtime_proxy_http_does_not_forward_host_cookie_or_proxy_auth -q --basetemp .pytest-tmp-aio-proxy-request-header-green
```

Runtime proxy regression after request header filtering:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_proxy.py -q --basetemp .pytest-tmp-aio-proxy-request-header-full
```

Runtime package regression after request header filtering:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-proxy-request-header-runtime
```

Adapter file write size limit RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_write_file_rejects_oversized_content -q --basetemp .pytest-tmp-aio-adapter-file-limit-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_write_file_rejects_oversized_content -q --basetemp .pytest-tmp-aio-adapter-file-limit-green
```

Adapter app regression after file write size limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py -q --basetemp .pytest-tmp-aio-adapter-file-limit-app
```

Runtime package regression after file write size limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-file-limit-runtime
```

Adapter file download size limit RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_download_file_rejects_oversized_file -q --basetemp .pytest-tmp-aio-adapter-download-limit-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_download_file_rejects_oversized_file -q --basetemp .pytest-tmp-aio-adapter-download-limit-green
```

Adapter app regression after file download size limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py -q --basetemp .pytest-tmp-aio-adapter-download-limit-app
```

Runtime package regression after file download size limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-download-limit-runtime
```

Adapter downloads list hash limit RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_lists_oversized_download_without_hashing -q --basetemp .pytest-tmp-aio-adapter-download-list-limit-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_lists_oversized_download_without_hashing -q --basetemp .pytest-tmp-aio-adapter-download-list-limit-green
```

Adapter app regression after downloads list hash limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py -q --basetemp .pytest-tmp-aio-adapter-download-list-limit-app
```

Runtime package regression after downloads list hash limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-download-list-limit-runtime
```

Workspace helper oversized download RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py::test_run_uploaded_skill_skips_oversized_download_without_fetching -q --basetemp .pytest-tmp-aio-workspace-oversized-download-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py::test_run_uploaded_skill_skips_oversized_download_without_fetching -q --basetemp .pytest-tmp-aio-workspace-oversized-download-green
```

Workspace helper regression after oversized download skip:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py -q --basetemp .pytest-tmp-aio-workspace-oversized-download-workspace
```

Runtime package regression after workspace oversized download skip:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-workspace-oversized-download-runtime
```

Workspace helper upload size limit RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py::test_upload_directory_rejects_oversized_file_before_reading -q --basetemp .pytest-tmp-aio-workspace-upload-limit-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py::test_upload_directory_rejects_oversized_file_before_reading -q --basetemp .pytest-tmp-aio-workspace-upload-limit-green
```

Workspace helper regression after upload size limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py -q --basetemp .pytest-tmp-aio-workspace-upload-limit-workspace
```

Runtime package regression after workspace upload size limit:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-workspace-upload-limit-runtime
```

AgentMentor ADR validation after file API policy:

```powershell
python C:\Users\HUAWEI\.codex\skills\using-agentmentor\scripts\knowledge_check.py --root E:\Work-Project\OtherWork\ScienceClaw --docs-path docs --strict
```

Shared adapter file policy RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_file_policy.py -q --basetemp .pytest-tmp-aio-file-policy-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_file_policy.py -q --basetemp .pytest-tmp-aio-file-policy-green
```

Focused adapter app/workspace/file-policy regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py RpaClaw/backend/tests/runtime/test_runtime_adapter_workspace.py RpaClaw/backend/tests/runtime/test_runtime_adapter_file_policy.py -q --basetemp .pytest-tmp-aio-file-policy-focused
```

Runtime package regression after shared file policy:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-file-policy-runtime
```

Adapter health file policy diagnostics RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_health_reports_local_surface -q --basetemp .pytest-tmp-aio-health-file-policy-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_health_reports_local_surface -q --basetemp .pytest-tmp-aio-health-file-policy-green
```

Focused adapter health/smoke/file-policy regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py RpaClaw/backend/tests/runtime/test_runtime_adapter_file_policy.py -q --basetemp .pytest-tmp-aio-health-file-policy-focused
```

Runtime package regression after health file policy diagnostics:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-health-file-policy-runtime
```

AIO provider file policy metadata RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_fixed_runtime_provider_records_adapter_file_policy_metadata -q --basetemp .pytest-tmp-aio-provider-file-policy-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_fixed_runtime_provider_records_adapter_file_policy_metadata -q --basetemp .pytest-tmp-aio-provider-file-policy-green
```

Focused provider/smoke regression after file policy metadata:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_aio_fixed_runtime_provider_records_adapter_file_policy_metadata RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-provider-file-policy-focused-green
```

Runtime package regression after provider file policy metadata:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-provider-file-policy-runtime-green
```

Runtime adapter image handoff README verification:

```powershell
git diff --check -- RpaClaw/runtime-adapter/README.md docs/features/F025-aio-session-sandbox-runtime-adapter.md docs/evidence/EV-025-aio-session-sandbox-runtime-adapter.md
```

Real AIO smoke mode RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake -q --basetemp .pytest-tmp-aio-real-smoke-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake -q --basetemp .pytest-tmp-aio-real-smoke-green
```

Adapter smoke regression after real AIO smoke mode:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-real-smoke-full
```

Runtime package regression after real AIO smoke mode:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-real-smoke-runtime
```

Real AIO smoke cleanup failure RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_real_aio_adapter_smoke_preserves_primary_failure_when_cleanup_fails -q --basetemp .pytest-tmp-aio-real-smoke-cleanup-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_real_aio_adapter_smoke_preserves_primary_failure_when_cleanup_fails -q --basetemp .pytest-tmp-aio-real-smoke-cleanup-green
```

Adapter smoke regression after cleanup failure handling:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-real-smoke-cleanup-full
```

Real AIO smoke keep-runtime CLI RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_adapter_smoke_cli_keep_runtime_disables_real_aio_cleanup -q --basetemp .pytest-tmp-aio-real-smoke-keep-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_adapter_smoke_cli_keep_runtime_disables_real_aio_cleanup -q --basetemp .pytest-tmp-aio-real-smoke-keep-green
```

Adapter smoke regression after keep-runtime CLI:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-real-smoke-keep-full
```

Real AIO smoke self-check output RED / GREEN focused test:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake -q --basetemp .pytest-tmp-aio-real-smoke-self-check-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py::test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake -q --basetemp .pytest-tmp-aio-real-smoke-self-check-green
```

Adapter smoke regression after real AIO self-check output:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-real-smoke-self-check-full
```

Runtime refresh persistence RED / GREEN focused tests:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_get_runtime_can_refresh_status_and_persist RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_list_runtimes_refreshes_and_persists_updates -q --basetemp .pytest-tmp-aio-runtime-refresh-persist-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_get_runtime_can_refresh_status_and_persist RpaClaw/backend/tests/runtime/test_runtime_manager.py::test_list_runtimes_refreshes_and_persists_updates -q --basetemp .pytest-tmp-aio-runtime-refresh-persist-green
```

Runtime manager regression after refresh persistence:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -q --basetemp .pytest-tmp-aio-runtime-refresh-persist-manager
```

Runtime package regression after refresh persistence:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-runtime-refresh-persist-runtime
```

Initial runtime package regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-create-failure-runtime
```

Adapter self-check RED / GREEN focused tests:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_self_check_cli_prints_sanitized_health RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_self_check_cli_returns_nonzero_for_degraded_env -q --basetemp .pytest-tmp-aio-adapter-self-check-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_self_check_cli_prints_sanitized_health RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py::test_runtime_adapter_self_check_cli_returns_nonzero_for_degraded_env -q --basetemp .pytest-tmp-aio-adapter-self-check-green
```

Adapter app / smoke regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_app.py -q --basetemp .pytest-tmp-aio-adapter-self-check-app-green
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-adapter-self-check-smoke-green
```

Final runtime package regression:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-self-check-runtime
```

Adapter image contract RED / GREEN tests:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_image.py -q --basetemp .pytest-tmp-aio-adapter-image-red
```

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_image.py -q --basetemp .pytest-tmp-aio-adapter-image-green
```

Adapter image contract final verification:

```powershell
python -m pytest RpaClaw/backend/tests/runtime/test_runtime_adapter_image.py -q --basetemp .pytest-tmp-aio-adapter-image-verify
```

Runtime package regression after image assets:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-adapter-image-runtime
```

Runtime package regression after response sample mapping:

```powershell
python -m pytest RpaClaw/backend/tests/runtime -q --basetemp .pytest-tmp-aio-response-sample-runtime
```

Docker daemon probe:

```powershell
docker version --format '{{.Server.Version}}'
```

Staged diff whitespace check:

```powershell
git diff --cached --check -- RpaClaw/backend/runtime/aio_runtime_provider.py RpaClaw/backend/tests/runtime/test_runtime_manager.py docs/rpa/aio-session-sandbox-runtime-adapter-design.md
```

## Results

Pass.

- Focused create failure regression: `3 passed in 0.41s`.
- Runtime manager regression: `68 passed in 6.51s`.
- Response sample mapping RED: `3 failed` because `diagnose_response_sample` and `--sample-response` did not exist.
- Response sample mapping GREEN: `3 passed in 0.39s`.
- Runtime manager regression after response sample mapping: `71 passed in 6.48s`.
- Creating runtime reuse RED: `1 failed` because `ensure_runtime()` only queried `status=ready` and created a duplicate runtime for an existing `creating` record.
- Creating runtime reuse GREEN: `1 passed in 0.16s`.
- Runtime manager regression after creating runtime reuse: `72 passed in 6.90s`.
- Runtime package regression after creating runtime reuse: `131 passed, 431 warnings in 8.59s`.
- Runtime proxy ready gate RED: `1 failed` because `creating` runtime was proxied upstream and returned 200.
- Runtime proxy ready gate GREEN: `2 passed, 11 deselected` for the focused `creating_runtime` selection.
- Runtime proxy regression after ready gate: `13 passed, 39 warnings in 0.53s`.
- Runtime package regression after proxy ready gate: `132 passed, 434 warnings in 9.30s`.
- CDP connector ready gate RED: `1 failed` because `_fetch_cdp_url()` did not reject `creating` runtime and still called adapter client.
- CDP connector ready gate GREEN: `1 passed, 2 deselected in 0.23s`.
- CDP connector regression after ready gate: `3 passed in 0.24s`.
- Runtime package regression after CDP connector ready gate: `133 passed, 434 warnings in 48.34s`.
- Adapter client sanitized HTTP error RED: collection failed because `RuntimeAdapterClientError` did not exist.
- Adapter client sanitized HTTP error GREEN: `1 passed, 2 deselected in 0.64s`.
- Adapter client regression after sanitized HTTP error: `3 passed in 0.79s`.
- Runtime package regression after adapter client sanitized HTTP error: `134 passed, 434 warnings in 25.29s`.
- EKS multi-instance duplicate runtime insert RED: `2 failed` because newly inserted runtime records did not use `_id=session_id` and duplicate insert was not recovered.
- EKS multi-instance duplicate runtime insert GREEN: `2 passed, 71 deselected in 0.56s`.
- Runtime manager regression after EKS multi-instance duplicate insert handling: `73 passed in 9.26s`.
- Runtime package regression after EKS multi-instance duplicate insert handling: `135 passed, 434 warnings in 15.83s`.
- Adapter health auth diagnostic RED: `1 failed` because provider refresh collapsed `RuntimeAdapterClientError(status_code=403)` into `adapter_health_unavailable`.
- Adapter health auth diagnostic GREEN: `1 passed in 0.40s`.
- Runtime manager regression after adapter health auth diagnostic: `74 passed in 8.27s`.
- Runtime package regression after adapter health auth diagnostic: `136 passed, 434 warnings in 12.56s`.
- AgentMentor strict after F025.5: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- AIO delete failure RED: `1 failed` because `delete_runtime()` let the raw client `RuntimeError` propagate with token text.
- AIO delete failure GREEN: `1 passed in 0.35s`.
- Runtime manager regression after AIO delete failure handling: `75 passed in 7.60s`.
- Runtime package regression after AIO delete failure handling: `137 passed, 434 warnings in 13.70s`.
- AgentMentor strict after F025.6: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Adapter execution output bound RED: `1 failed` because stdout returned 5001 characters without truncation metadata.
- Adapter execution output bound GREEN: `1 passed, 28 warnings in 1.06s`.
- Adapter app regression after output bound: `26 passed, 350 warnings in 4.04s`.
- Runtime package regression after output bound: `138 passed, 448 warnings in 16.45s`.
- AgentMentor strict after F025.7: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Adapter client sensitive error detail RED: `1 failed` because `RuntimeAdapterClientError.detail` preserved `session-token` from the adapter JSON body.
- Adapter client sensitive error detail GREEN: `1 passed in 0.29s`.
- Adapter client regression after sensitive error detail sanitization: `4 passed in 0.24s`.
- Runtime package regression after sensitive error detail sanitization: `139 passed, 448 warnings in 16.38s`.
- AgentMentor strict after F025.8: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Runtime proxy upstream response header RED: `1 failed` because `Set-Cookie`, `Authorization`, and `WWW-Authenticate` response headers were forwarded to the frontend.
- Runtime proxy upstream response header GREEN: `1 passed, 6 warnings in 0.82s`.
- Runtime proxy regression after response header filtering: `14 passed, 42 warnings in 1.08s`.
- Runtime package regression after response header filtering: `140 passed, 451 warnings in 16.05s`.
- AgentMentor strict after F025.9: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Runtime proxy request header RED: `1 failed` because Host `Cookie` / `Proxy-Authorization` were forwarded to adapter.
- Runtime proxy request header GREEN: `1 passed, 6 warnings in 5.76s`.
- Runtime proxy regression after request header filtering: `15 passed, 45 warnings in 2.19s`.
- Runtime package regression after request header filtering: `141 passed, 454 warnings in 15.47s`.
- AgentMentor strict after F025.10: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Adapter file write size limit RED: `1 failed` because `/files/write` returned 200 and wrote a 10MiB+1 payload.
- Adapter file write size limit GREEN: `1 passed, 28 warnings in 0.96s`.
- Adapter app regression after file write size limit: `27 passed, 364 warnings in 4.10s`.
- Runtime package regression after file write size limit: `142 passed, 468 warnings in 14.06s`.
- AgentMentor strict after F025.11: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Adapter file download size limit RED: `1 failed` because `MAX_FILE_DOWNLOAD_BYTES` did not exist.
- Adapter file download size limit GREEN: `1 passed, 28 warnings in 1.09s`.
- Adapter app regression after file download size limit: `28 passed, 378 warnings in 5.51s`.
- Runtime package regression after file download size limit: `143 passed, 482 warnings in 16.83s`.
- AgentMentor strict after F025.12: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Adapter downloads list hash limit RED: `1 failed` because `/rpa/downloads` still read the oversized file and returned a sha256.
- Adapter downloads list hash limit GREEN: `1 passed, 28 warnings in 1.01s`.
- Adapter app regression after downloads list hash limit: `29 passed, 392 warnings in 4.45s`.
- Runtime package regression after downloads list hash limit: `144 passed, 496 warnings in 15.28s`.
- AgentMentor strict after F025.13: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Workspace helper oversized download RED: `1 failed` because helper still called `/files/download` for an artifact marked `hash_status=skipped_oversized`.
- Workspace helper oversized download GREEN: `1 passed in 0.24s`.
- Workspace helper regression after oversized download skip: `10 passed in 0.73s`.
- Runtime package regression after workspace oversized download skip: `145 passed, 496 warnings in 14.60s`.
- AgentMentor strict after F025.14: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Workspace helper upload size limit RED: `1 failed` because `MAX_UPLOAD_FILE_BYTES` did not exist.
- Workspace helper upload size limit GREEN: `1 passed in 0.23s`.
- Workspace helper regression after upload size limit: `11 passed in 0.45s`.
- Runtime package regression after workspace upload size limit: `146 passed, 496 warnings in 12.90s`.
- AgentMentor strict after F025.15: `Scanned 264 markdown file(s). Checked 56 knowledge artifact(s). Errors: 0. Warnings: 0.`
- ADR-005 created to govern adapter file API policy after F025.11-F025.15 file-boundary patch churn.
- AgentMentor strict after ADR-005: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Shared adapter file policy RED: collection failed because `backend.runtime.adapter_file_policy` did not exist.
- Shared adapter file policy GREEN: `1 passed, 14 warnings in 1.10s`.
- Focused adapter app/workspace/file-policy regression: `41 passed, 392 warnings in 4.20s`.
- Runtime package regression after shared file policy: `147 passed, 496 warnings in 14.34s`.
- AgentMentor strict after F025.16: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Adapter health file policy diagnostics RED: `1 failed` because `/health.config` did not include `file_policy`.
- Adapter health file policy diagnostics GREEN: `1 passed, 28 warnings in 1.05s`.
- Focused adapter health/smoke/file-policy regression: `32 passed, 423 warnings in 5.31s`.
- Runtime package regression after health file policy diagnostics: `147 passed, 496 warnings in 17.11s`.
- AgentMentor strict after F025.17: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- AIO provider file policy metadata RED: `1 failed` because runtime metadata did not include `adapter_file_policy`.
- AIO provider file policy metadata GREEN: `1 passed in 0.31s`.
- Focused provider/smoke regression after file policy metadata: `3 passed, 45 warnings in 2.10s`.
- Runtime package regression after provider file policy metadata: `148 passed, 496 warnings in 16.30s`.
- AgentMentor strict after F025.18: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Runtime adapter image handoff README verification after F025.19: `git diff --check` passed with only LF/CRLF warnings.
- Runtime package regression after F025.19 README handoff update: `148 passed, 496 warnings in 12.55s`.
- AgentMentor strict after F025.19: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Real AIO smoke mode RED: import failed because `run_real_aio_adapter_smoke` did not exist.
- Real AIO smoke mode GREEN: `1 passed, 31 warnings in 0.84s`.
- Adapter smoke regression after real AIO smoke mode: `3 passed, 62 warnings in 2.36s`.
- Runtime package regression after real AIO smoke mode: `149 passed, 513 warnings in 16.54s`.
- AgentMentor strict after F025.20: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Real AIO smoke cleanup failure RED: `1 failed` because `AioRuntimeProviderError(operation="delete", reason="aio_delete_unavailable")` covered the primary `/health` auth failure.
- Real AIO smoke cleanup failure GREEN: `1 passed, 31 warnings in 0.64s`.
- Adapter smoke regression after cleanup failure handling: `4 passed, 79 warnings in 1.24s`.
- Runtime package regression after cleanup failure handling: `150 passed, 530 warnings in 11.85s`.
- AgentMentor strict after F025.21: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Real AIO smoke keep-runtime CLI RED: `1 failed` because argparse rejected unknown `--keep-runtime`.
- Real AIO smoke keep-runtime CLI GREEN: `1 passed, 14 warnings in 1.06s`.
- Adapter smoke regression after keep-runtime CLI: `5 passed, 79 warnings in 2.86s`.
- Runtime package regression after keep-runtime CLI: `151 passed, 530 warnings in 16.25s`.
- AgentMentor strict after F025.22: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Real AIO smoke self-check output RED: `1 failed` with `KeyError: 'adapter_self_check'`.
- Real AIO smoke self-check output GREEN: `1 passed, 31 warnings in 1.65s`.
- Adapter smoke regression after real AIO self-check output: `5 passed, 79 warnings in 3.01s`.
- Runtime package regression after real AIO self-check output: `151 passed, 530 warnings in 13.51s`.
- AgentMentor strict after F025.23: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Runtime refresh persistence RED: `2 failed` because repository updates only persisted `status`.
- Runtime refresh persistence GREEN: `2 passed in 0.39s`.
- Runtime manager regression after refresh persistence: `76 passed in 10.68s`.
- Runtime package regression after refresh persistence: `151 passed, 530 warnings in 20.26s`.
- AgentMentor strict after F025.24: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Manager cleanup log sanitization RED: `2 failed` because duplicate-created cleanup and expired cleanup warning logs included raw `secret-token` / `adapter-secret`.
- Manager cleanup log sanitization GREEN: `2 passed in 0.47s`.
- Runtime manager regression after cleanup log sanitization: `78 passed in 9.42s`.
- Runtime package regression after cleanup log sanitization: `153 passed, 530 warnings in 14.46s`.
- AgentMentor strict after F025.25: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- F025.25 diff check: `git diff --check` passed with only LF/CRLF warnings.
- Manager bare runtime token log sanitization RED: first attempt had an async owner checker fixture error; corrected RED then `1 failed` because `bare-runtime-secret` appeared in orphan cleanup warning logs.
- Manager bare runtime token log sanitization GREEN: `1 passed in 0.61s`.
- Manager cleanup log sanitization focused set after bare token handling: `3 passed in 0.44s`.
- Runtime manager regression after bare token log sanitization: `79 passed in 8.83s`.
- Runtime package regression after bare token log sanitization: `154 passed, 530 warnings in 15.05s`.
- AgentMentor strict after F025.26: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- F025.26 diff check: `git diff --check` passed with only LF/CRLF warnings.
- AIO design handoff boundary update: `docs/rpa/aio-session-sandbox-runtime-adapter-design.md` now separates external/local validation scope from inner-network final validation scope in the opening background.
- AgentMentor strict after F025.27: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- F025.27 diff check: `git diff --check` passed with only LF/CRLF warnings.
- Runtime proxy WebSocket handshake header RED: `1 failed` because `Sec-WebSocket-Key`, `Sec-WebSocket-Version`, `Sec-WebSocket-Extensions`, and `Sec-WebSocket-Protocol` were forwarded to adapter upstream headers.
- Runtime proxy WebSocket handshake header GREEN: `1 passed, 3 warnings in 0.47s`.
- Runtime proxy regression after WebSocket handshake header filtering: `16 passed, 45 warnings in 0.58s`.
- Runtime package regression after WebSocket handshake header filtering: `155 passed, 530 warnings in 10.26s`.
- AgentMentor strict after F025.28: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- F025.28 diff check: `git diff --check` passed with only LF/CRLF warnings.
- Adapter subprocess environment scrub RED: `2 failed` because `execute-step` and `run-skill` child processes could read `RUNTIME_ADAPTER_TOKEN`, `AIO_RUNTIME_API_TOKEN`, and `HOST_AUTHORIZATION`.
- Adapter subprocess environment scrub GREEN: `2 passed, 42 warnings in 0.58s`.
- Adapter app regression after subprocess environment scrub: `31 passed, 420 warnings in 8.43s`.
- Runtime package regression after subprocess environment scrub: `157 passed, 558 warnings in 21.91s`.
- AgentMentor strict after F025.29: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- F025.29 diff check: `git diff --check` passed with only LF/CRLF warnings.
- AgentMentor strict after F025.3 initially failed because F025 had 3 Patch History rows without `## Patch Churn Review`; F025 now records the review and the next strict run is tracked below.
- Initial runtime package regression: `123 passed, 431 warnings in 9.05s`.
- Adapter self-check RED: `2 failed` because `backend.runtime.adapter_app` had no `main`.
- Adapter self-check focused GREEN: `2 passed, 14 warnings in 0.33s`.
- Adapter app regression after self-check: `25 passed, 336 warnings in 2.19s`.
- Adapter smoke RED: `2 failed` because smoke output did not include `adapter_self_check`.
- Adapter smoke GREEN: `2 passed, 45 warnings in 1.16s`.
- Final runtime package regression: `125 passed, 431 warnings in 10.54s`.
- Adapter image contract RED: `2 failed` because `RpaClaw/runtime-adapter/Dockerfile` and README did not exist.
- Adapter image contract GREEN: `2 passed in 0.19s`.
- Adapter image contract final verification: `2 passed in 0.17s`.
- Runtime package regression after image assets: `127 passed, 431 warnings in 8.63s`.
- Runtime package regression after response sample mapping: `130 passed, 431 warnings in 8.39s`.
- Docker Desktop adapter image build verification after F025.30: `docker build --progress=plain -f runtime-adapter/Dockerfile -t rpaclaw-runtime-adapter:dev .` passed from `RpaClaw`; build context was `112.17kB`; image build self-check returned `status=ok`, `contract_version=v1`, `file_policy` limits, and `token_required=false`.
- Runtime adapter image run self-check after F025.30: `docker run --rm rpaclaw-runtime-adapter:dev python -m backend.runtime.adapter_app --self-check` returned `status=ok`.
- Runtime adapter image token diagnostic after F025.30: `docker run --rm -e RUNTIME_ADAPTER_TOKEN=secret-token-for-build-verify rpaclaw-runtime-adapter:dev python -m backend.runtime.adapter_app --self-check` returned `status=ok` and `token_required=true` without printing the token value.
- Runtime adapter image tag after F025.30: `rpaclaw-runtime-adapter:dev bd80784abda7 247MB`.
- Default slim image build with Debian `chromium` after F025.31 hit external apt mirror `502 Bad Gateway` errors while downloading Chromium/system packages; this is recorded as an environment/network limitation, not adapter contract failure.
- Playwright base adapter image build after F025.31 passed with:
  `docker build --progress=plain --build-arg BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.57.0-noble --build-arg INSTALL_CHROMIUM=false -f runtime-adapter/Dockerfile -t rpaclaw-runtime-adapter:dev .`.
- Runtime adapter image tag after Playwright-base F025.31 build: `rpaclaw-runtime-adapter:dev 0c83264d1f9f 3.35GB`.
- Local fake AIO service contract after F025.31: `pytest RpaClaw\backend\tests\runtime\test_local_fake_aio_service.py -q --basetemp .pytest-tmp-fake-aio-env-green` returned `3 passed, 12 warnings in 1.95s`.
- Adapter proxy/root-cause regression after F025.31: `pytest RpaClaw\backend\tests\runtime\test_runtime_adapter_client.py RpaClaw\backend\tests\runtime\test_local_fake_aio_service.py RpaClaw\backend\tests\runtime\test_runtime_adapter_smoke.py -q --basetemp .pytest-tmp-aio-proxy-fix` returned `14 passed, 91 warnings in 2.89s`.
- Root cause for initial container smoke 502: Python `httpx.AsyncClient` with default `trust_env=True` used system proxy settings for `http://127.0.0.1:18081/health`, returning `502`; `trust_env=False` returned `200` against the same adapter container. `RuntimeAdapterClient` now defaults to `trust_env=False` for internal adapter routes.
- Containerized fake AIO smoke after F025.31:
  `PYTHONPATH=RpaClaw python -m backend.runtime.adapter_smoke --mode aio_container --adapter-token adapter-token` returned `status=ok`, `mode=aio_container`, runtime `namespace=local-fake-aio-container`, `route_base_url=http://127.0.0.1:18081`, adapter health `status=ok`, `browser.status=success`, and listener `status=injected` with marker `__rpaclawRuntimeAdapterListener`.
- Fake AIO container cleanup after F025.31: `docker ps -a --filter "label=rpaclaw.local_fake_aio=true"` returned no containers after the smoke.
- Internal handoff doc after F025.32: `docs/rpa/aio-runtime-adapter-internal-handoff.md` added as the single inner-network adapter handoff entry, covering read order, real AIO config, image publication, `aio_real` smoke, browser/CDP validation, failure attribution, EKS multi-instance constraints, and completion criteria.
- Runtime package regression after F025.31: `pytest RpaClaw\backend\tests\runtime -q --basetemp .pytest-tmp-runtime-f02531-green` returned `163 passed, 584 warnings in 41.80s`.
- `git diff --check`: passed with only Windows LF/CRLF warnings.
- AgentMentor strict after F025.31: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`
- Warnings are existing FastAPI/Python 3.14 deprecation warnings from runtime tests, not behavior failures.

## AgentMentor Validation

```powershell
python C:\Users\HUAWEI\.codex\skills\using-agentmentor\scripts\knowledge_check.py --root E:\Work-Project\OtherWork\ScienceClaw --docs-path docs --strict
```

Result: `Scanned 265 markdown file(s). Checked 57 knowledge artifact(s). Errors: 0. Warnings: 0.`

## Artifacts

- Feature: [F025 AIO Session Sandbox Runtime Adapter](../features/F025-aio-session-sandbox-runtime-adapter.md)
- Design: [AIO session sandbox runtime adapter design](../rpa/aio-session-sandbox-runtime-adapter-design.md)
- Handoff: [AIO Runtime Adapter internal handoff guide](../rpa/aio-runtime-adapter-internal-handoff.md)
- Decision: [ADR-005 AIO Runtime Adapter File API Policy](../decisions/ADR-005-aio-runtime-adapter-file-api-policy.md)
- Code: `RpaClaw/backend/runtime/aio_runtime_provider.py`
- Code: `RpaClaw/backend/runtime/adapter_app.py`
- Code: `RpaClaw/backend/runtime/adapter_client.py`
- Code: `RpaClaw/backend/runtime/adapter_file_policy.py`
- Code: `RpaClaw/backend/runtime/adapter_workspace.py`
- Code: `RpaClaw/backend/runtime/adapter_smoke.py`
- Code: `RpaClaw/backend/runtime/local_fake_aio_service.py`
- Image asset: `RpaClaw/runtime-adapter/Dockerfile`
- Image asset: `RpaClaw/runtime-adapter/README.md`
- Image asset: `RpaClaw/.dockerignore`
- Image asset: `RpaClaw/runtime-adapter/requirements.txt`
- Code: `RpaClaw/backend/route/runtime_proxy.py`
- Code: `RpaClaw/backend/rpa/cdp_connector.py`
- Tests: `RpaClaw/backend/tests/runtime`

## Notes

Create 失败发生在可信 runtime record 形成之前，因此当前实现选择抛出脱敏的 `AioRuntimeProviderError(operation="create", reason=...)`，而不是写入 `status=missing` 的 runtime record。已经形成 runtime record 后，refresh/status/adapter health 的缺失原因才进入 `SessionRuntimeRecord.metadata.runtime_status_reason`。
