---
id: ADR-005
doc_kind: adr
status: accepted
scope: feature
feature_refs:
  - docs/features/F025-aio-session-sandbox-runtime-adapter.md
decision_area: aio-runtime-adapter-file-api
created: 2026-06-07
updated: 2026-06-07
---

# ADR-005: AIO Runtime Adapter File API Policy

## Context

F025 把 AIO Sandbox / Runtime Adapter 定义为不可信执行面，Host Backend 定义为可信控制面。为了在外网提前验证 workspace / skill / artifact 闭环，我们引入了 adapter `/files/write`、`/files/download`、`/rpa/downloads` 以及 Host 侧 `adapter_workspace` helper。

连续补丁 F025.11-F025.15 暴露出同一个问题：如果文件 API 只有路径边界，没有大小、hash、下载和 Host helper 的统一策略，真实内网 AIO 接入时会把大量与真实 AIO create/status/delete 无关的问题推迟到 smoke 阶段。例如：

- `/files/write` 可能接收无界 inline/base64 payload。
- `/files/download` 可能直接把大 artifact `read_bytes()` 进 adapter / Host proxy 内存。
- `/rpa/downloads` 为了计算 sha256 可能在列表阶段读取大文件。
- Host helper 可能先读入本地大文件再等 adapter 拒绝。
- Host helper 可能看到 `hash_status=skipped_oversized` 后仍继续拉取大 artifact。

这些不是业务事实，也不属于 `AcceptedTrace`、Skill 真源、artifact 归因或 Harness expected signals。它们是 Host/Adapter 执行面契约问题，应该在外网阶段收敛。

## Decision

Adapter file API 采用 bounded inline file contract：

1. `/files/write` 只用于有界小文件写入，当前单文件上限为 `10MiB`。超过上限时 adapter 返回 `413`，且不得创建半成品目录或文件。
2. Host `upload_directory()` 必须在读取本地文件内容前应用同等 `10MiB` 单文件上限，避免先在可信控制面无界读入再等待不可信执行面拒绝。
3. `/files/download` 只用于有界小/中型 artifact 拉取，当前单文件上限为 `50MiB`。超过上限时 adapter 必须在 `read_bytes()` 前返回 `413`。
4. `/rpa/downloads` 是 artifact metadata 枚举 API。它必须返回 `name`、`path`、`size`，但只对 bounded 文件计算 `sha256`。
5. 当 artifact 超过下载上限时，`/rpa/downloads` 必须保留 metadata，返回 `sha256: null` 和 `hash_status: "skipped_oversized"`，而不是隐藏 artifact 或尝试计算 hash。
6. Host `run_uploaded_skill()` 必须消费 `hash_status="skipped_oversized"`，保留 metadata 并返回 `download_status="skipped_oversized"`，不得继续调用 `/files/download`。
7. `sha256` 是 bounded download 的完整性校验，不是 artifact 存在性的唯一证明。大文件存在性由 metadata 和 status 表达。
8. Runtime Adapter 不定义 artifact 业务归因、Skill 真源或 Harness expected signals；这些仍由 Host/RPA Core/Harness 各自边界负责。

## Alternatives

- 让 adapter 对所有文件都流式上传/下载并支持任意大小。拒绝。当前目标是外网验证最小可交接闭环，不是实现完整对象存储或大文件传输平台；无界流式能力会扩大镜像、代理、认证和存储复杂度。
- 让 `/rpa/downloads` 隐藏 oversized artifact。拒绝。隐藏会让 Host 误以为产物不存在，削弱诊断和内网 smoke 的可解释性。
- 对 oversized artifact 也强制计算 sha256。拒绝。列表 API 会退化为大文件读取 API，违背 metadata 枚举边界。
- 只在 adapter 拒绝 oversized 文件，Host helper 不做前置检查。拒绝。Host 是可信控制面，也要保护自身内存和诊断稳定性；否则错误会晚到且更难定位。
- 把大文件下载失败当作 Skill 执行失败。拒绝。大 artifact 未自动拉回是文件 API 策略结果，不等同于 Skill replay 失败。

## Consequences

- 外网阶段的 workspace/skill/artifact 闭环保持简单、可测、可交接；真实内网 Agent 不需要同时调试无界文件传输问题。
- 小文件 artifact 可以继续通过 `sha256` 做完整性校验；大文件需要后续单独设计对象存储、分片下载、预签名 URL 或 AIO 原生 artifact 通道。
- Host UI/API 若展示下载产物，应把 `hash_status=skipped_oversized` / `download_status=skipped_oversized` 表示为“产物存在但未自动拉回”，不能当成产物丢失。
- 后续如果必须支持大 artifact，应该新增明确的大文件传输 ADR 或扩展本 ADR，而不是绕过当前 inline file contract。
- 该决策不改变 RPA Core 录制事实、`AcceptedTrace`、`TraceSkillCompiler` 或 Harness expected signals 的归属。

## Evidence

- Feature: `docs/features/F025-aio-session-sandbox-runtime-adapter.md`
- Evidence: `docs/evidence/EV-025-aio-session-sandbox-runtime-adapter.md`
- Design: `docs/rpa/aio-session-sandbox-runtime-adapter-design.md`
- Related decision: `docs/decisions/ADR-004-rpa-core-owns-recording-facts-harness-adapts-only.md`
