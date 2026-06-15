---
id: ADR-006
doc_kind: adr
status: accepted
scope: project
feature_refs: []
decision_area: aio-native-api-first-runtime-strategy
created: 2026-06-08
updated: 2026-06-08
---

# ADR-006: AIO 原生 API 优先的会话级沙箱接入策略

## 状态

Accepted

## Context

RPA/Skill 执行链路需要从本机浏览器逐步迁移到“每个用户会话一个 AIO 沙箱”的隔离执行面。此前外网方案重点验证了 Runtime Adapter 路线：在 AIO 沙箱内运行 RpaClaw adapter 服务，由 Host Backend 通过 adapter semantic API 访问浏览器、文件、脚本执行、下载和诊断能力。

后续基于 `agent-infra/sandbox` 的本机验证表明，AIO 原生 API 已经能覆盖当前上线所需的关键能力：

- 启动并访问浏览器。
- 暴露 CDP/Playwright 控制入口。
- 通过 Playwright 注入现有 recorder listener JS，并捕获用户浏览器操作事件。
- 支持自然语言操作浏览器。
- 支持生成脚本执行和 Skill replay 的基本闭环。
- 支持文件、脚本执行、日志诊断和资源清理等执行面能力。

内网 AIO 服务遵从 AIO 沙箱官网 API 形态，并额外提供基于模板 ID 的沙箱生命周期接口：

- `POST /api/livefunction/sandboxes`
- `GET /api/livefunction/sandboxes/{sandboxId}`
- `DELETE /api/livefunction/sandboxes/{sandboxId}`
- `POST /api/livefunction/sandboxes/refresh/{sandboxId}`

因此，短期上线不应为了“架构看起来完整”而强行把 Runtime Adapter 注入沙箱。第一性原理上，真正目标是会话级隔离与执行闭环，而不是引入额外服务层。

## Decision

短期内采用：

```text
AIO 原生 API + Host Backend 控制面适配
```

Runtime Adapter 暂缓，不作为第一阶段上线依赖。

Host Backend 继续作为可信控制面，负责：

- 用户认证、权限、session ownership。
- AIO sandbox create/status/refresh/delete。
- 多实例下的 session runtime record 持久化与幂等 ensure。
- CDP URL、browser view URL、sandbox status 的脱敏诊断。
- recorder listener JS 注入、AcceptedTrace 接受与持久化。
- Skill 编译、Skill 元数据、artifact 归因和持久化。

AIO Sandbox 作为不可信执行面，负责：

- 会话级浏览器和 browser profile。
- CDP/Playwright 执行。
- 页面 listener JS 运行和 raw event 产生。
- 临时文件、下载、截图、脚本执行、Skill replay 运行时。
- 沙箱日志和短期诊断 evidence。

## Alternatives

本 ADR 不要求废弃 Runtime Adapter 代码。Adapter 保留为第二阶段备选方案，用于以下情况：

- AIO 原生 API 无法稳定覆盖某项必要能力。
- 需要在执行面提供统一的 RpaClaw semantic API 以屏蔽 AIO 版本差异。
- 需要更强的沙箱内脱敏诊断、文件策略或执行策略封装。
- 内网 AIO 路由限制导致 Host Backend 不能直接访问所需 CDP/file/shell/browser API。

## Consequences

短期实现应优先收敛到 `aio_native` / native AIO provider 路线，而不是继续扩展 adapter image、adapter smoke 和 fake AIO lifecycle。

内网 Agent 的主要任务变为：

1. 将本地固定 AIO 沙箱 provider 替换为真实 create/status/refresh/delete provider。
2. 将 AIO 返回的 `sandboxId`、`status`、CDP、VNC/browser view、文件和脚本执行入口映射为 Host runtime record。
3. 保证 EKS 多实例部署下同一 `session_id` 只对应一个有效 sandbox。
4. 跑真实 AIO smoke：浏览器启动、listener 注入、事件捕获、自然语言操作、脚本生成执行、Skill replay、清理释放。

## 多实例约束

内网前端、Host Backend 等模块采用 EKS 微服务多实例部署，因此 Host Backend 不得依赖进程内状态作为 runtime 真源。

必须满足：

- `session_id` 是 runtime ensure 的幂等键。
- runtime record 存储在共享持久化介质中。
- 并发 create 时只能保留一个有效 sandbox；失败写入方需要清理自己刚创建但未赢得绑定的 sandbox。
- `refresh_runtime()` 需要把最新 AIO status 和非敏感诊断写回共享 record。
- metadata 不得包含 token、页面内容、AcceptedTrace 原文、expected signals 或 artifact 真源。

## 验收标准

第一阶段完成条件：

- Host Backend 能通过真实 AIO API 创建、查询、刷新和删除 sandbox。
- 同一用户会话能复用同一 sandbox，刷新后不会重复创建。
- CDP/Playwright 能连接 AIO browser。
- 现有 recorder listener JS 能注入，并能捕获用户点击、输入、导航等事件。
- 自然语言操作可在 AIO browser 中执行。
- 录制后的脚本可生成并执行。
- Skill replay 能在 AIO 执行面运行。
- sandbox 删除后资源释放可观测。
- 诊断输出脱敏，不泄露 AIO token、用户认证凭据和页面敏感内容。

## 取舍

选择 native AIO 路线的原因：

- 更短：不需要构建、发布、启动和诊断额外 adapter 镜像。
- 更贴近当前上线目标：会话级沙箱隔离和 RPA 主链路切换，而不是先建设执行面中间层。
- 更少边界：Host 控制面直接适配 AIO 生命周期，减少 Host -> adapter -> AIO 的重复抽象。
- 已经本机验证关键能力可行。

暂缓 adapter 的原因：

- 在 AIO 原生能力已覆盖当前诉求时，adapter 会显著增加镜像发布、路由、鉴权、版本兼容和诊断复杂度。
- adapter 的长期价值是兼容层和语义层，不是第一阶段上线的必要条件。

## Evidence

- 本地 native AIO 验证：浏览器启动、CDP/Playwright 连接、recorder listener JS 注入、用户事件捕获、自然语言操作、脚本生成与执行均已跑通。
- 内网 API 契约输入来自当前接入信息：`POST /api/livefunction/sandboxes`、`GET /api/livefunction/sandboxes/{sandboxId}`、`DELETE /api/livefunction/sandboxes/{sandboxId}`、`POST /api/livefunction/sandboxes/refresh/{sandboxId}`。
- 交接文档：`docs/rpa/aio-native-internal-handoff.md`。
- 本地 provider 文档：`docs/rpa/aio-native-runtime-provider.md`。
- Focused regression: `$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw/backend/tests/runtime/test_runtime_manager.py -k "aio_ or provider_factory_returns" RpaClaw/backend/tests/runtime/test_cdp_connector.py -q`。
