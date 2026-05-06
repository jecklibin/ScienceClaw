# RPA 黄金评测应用

`rpa-eval-app` 是 RpaClaw 的本地黄金评测系统，用来评估 AI 录制助手在企业内部办公流程中的稳定性和智能化程度。它不通过容器启动，直接在本机拉起后端、前端和评测 runner。

评测系统覆盖采购、供应商、合同、审批、报表下载等典型企业作业场景。评测 runner 会重置固定业务数据，调用 RpaClaw 执行自然语言指令，并输出 JSON 与 Markdown 测评报告。

## 目录结构

```text
rpa-eval-app/
├── backend/        # FastAPI + SQLite 评测业务系统后端
├── frontend/       # Vue 3 + Element Plus 评测业务系统前端
└── evals/          # YAML 黄金用例、RpaClaw 调用客户端、报告生成器
```

## 环境要求

- Python 3.11+，建议与 RpaClaw 本地开发环境隔离。
- Node.js 18+。
- RpaClaw 后端可访问，默认地址为 `http://localhost:12001`。
- RpaClaw 需要能访问本机评测前端 `http://localhost:5175`。

## 1. 安装后端与评测依赖

在一个终端中执行：

```powershell
cd D:\code\MyScienceClaw\rpa-eval-app\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果你已经有可用的 Python 环境，也可以只执行：

```powershell
cd D:\code\MyScienceClaw\rpa-eval-app\backend
python -m pip install -r requirements.txt
```

## 2. 启动评测后端

继续在后端终端中执行：

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8085
```

后端地址：

- 健康检查：`http://localhost:8085/health`
- API 根路径：`http://localhost:8085/api`

后端每次启动都会重建 SQLite 数据库并加载固定 fixtures。运行时数据保存在 `backend/data/` 和 `backend/downloads/`，这些目录已被 `.gitignore` 忽略。

## 3. 安装并启动评测前端

另开一个终端执行：

```powershell
cd D:\code\MyScienceClaw\rpa-eval-app\frontend
npm install
npm run dev
```

前端地址：

```text
http://localhost:5175
```

前端开发服务器会把 `/api` 代理到 `http://localhost:8085`。

## 4. 确认 RpaClaw 已就绪

启动 RpaClaw 自身后端，默认评测 runner 会访问：

```text
http://localhost:12001
```

如果你的 RpaClaw 后端不是默认地址，运行评测时通过 `--rpaclaw-url` 指定。

如果 RpaClaw API 需要认证 token，运行评测时通过 `--rpaclaw-token` 指定。

如果需要指定 RpaClaw 使用的模型，运行评测时通过 `--model` 指定模型名。runner 会调用 RpaClaw 的 `/api/v1/models`，按模型 `model_name`、显示名或 id 匹配到对应 `model_config_id`，再传给 RPA chat 接口。

runner 会对每个用例执行总时限控制。默认 `--case-timeout-s` 为 180 秒；每个 YAML 用例也可以用 `timeout_s` 单独覆盖。超时后 runner 会主动停止对应 RpaClaw RPA session，并在报告中把失败阶段标为 `timeout`，避免简单用例因 repair 循环或 SSE 长连接被拖到十几分钟。

## 5. 测试账号

评测系统内置以下固定账号：

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | 管理员 |
| `buyer` | `buyer123` | 采购员 |
| `approver` | `approver123` | 审批人 |

评测 runner 会在每个用例开始前重置数据，并使用评测后端签发的 token 访问 `eval-auth.html` 写入浏览器登录态，再导航到用例起始页。发送给 RpaClaw 的指令只包含业务任务本身，避免“登录/导航前置步骤”被误判为业务完成。

## 6. 手动重置评测数据

通常不需要手动重置，因为 runner 每个用例都会自动重置。需要手动重置时执行：

```powershell
Invoke-WebRequest `
  -Method POST `
  -Uri http://localhost:8085/api/eval/reset `
  -Headers @{ "X-RPA-Eval-Reset-Token" = "rpa-eval-reset" }
```

重置 token 可通过环境变量覆盖：

```powershell
$env:RPA_EVAL_RESET_TOKEN = "your-reset-token"
```

## 7. 运行测评

从仓库根目录运行 smoke 用例：

```powershell
cd D:\code\MyScienceClaw
python rpa-eval-app\evals\runner.py --tag smoke
```

运行全部用例：

```powershell
python rpa-eval-app\evals\runner.py --all
```

运行历史坑位回归用例：

```powershell
python rpa-eval-app\evals\runner.py --tag regression
```

运行指定用例：

```powershell
python rpa-eval-app\evals\runner.py `
  --case contract_filter_open_001 `
  --case report_async_download_001
```

如果 RpaClaw 地址或 token 不是默认值：

```powershell
python rpa-eval-app\evals\runner.py `
  --all `
  --eval-backend-url http://localhost:8085 `
  --eval-frontend-url http://localhost:5175 `
  --rpaclaw-url http://localhost:12001 `
  --rpaclaw-token your-token `
  --model deepseek-chat `
  --case-timeout-s 180
```

如果你已经知道模型配置 id，也可以直接使用 `--model-config-id`。

runner 执行时会打印当前进度，例如 `[3/12] START ...`、`[3/12] PASS ...`。每个用例完成后会调用 RpaClaw 的 `/api/v1/rpa/session/{session_id}/stop`，清理对应 Playwright 浏览器实例。全部用例结束后，控制台会输出总数、通过数、失败数和每个用例的简表。

当前用例按复杂度设置了 `timeout_s`：简单页面抽取/导出通常为 90 秒，单页面业务写入通常为 120-150 秒，跨页面端到端流程为 240 秒。这个时限是测评质量的一部分：超时代表当前录制助手没有在合理时间内完成业务任务。

## 8. 用例范围

当前黄金用例位于 `evals/cases/`：

- `login_navigation_001`：登录后从工作台导航进入合同管理。
- `contract_filter_open_001`：按合同状态筛选并打开合同详情。
- `contract_extract_001`：从当前合同详情页提取关键字段，字段断言只检查 Agent 输出，不用页面可见文本替代。
- `supplier_complete_001`：从待补联系人供应商中补全联系人、电话和邮箱。
- `purchase_request_create_001`：创建采购申请。
- `purchase_order_generate_001`：从已批准采购申请生成本轮新增采购订单。
- `contract_lookup_then_purchase_request_001`：先读取合同详情中的归口部门和供应商编号，再把读取到的数据带入采购申请。
- `purchase_request_then_order_001`：先创建采购申请，再基于新申请生成采购订单。
- `approval_high_priority_001`：审批高优先级任务。
- `report_contract_export_001`：导出合同报表，并通过业务系统下载审计确认导出接口被真实触发。
- `report_async_download_001`：生成、轮询并下载异步报表，并通过下载审计确认报表下载被真实触发。
- `empty_result_contract_001`：处理空结果检索场景。

新增历史坑位回归用例位于同一目录，统一使用 `regression` 标签：

- `lab_body_click_export_001`：防止行选择后的 `body/html` 弱点击污染导出菜单录制。
- `lab_split_grid_first_file_001`：验证 header/body 分离表格的第一行文件打开。
- `lab_collection_row_action_001`：验证 collection/row scoped locator 不应在编译回放时被改坏。
- `lab_empty_audit_extract_001`：验证显式空结果抽取是合法输出。
- `lab_dataflow_cost_center_001`：验证前一步读取值能驱动后一步表单填写。
- `lab_parameterized_contract_001`：验证回放目标应使用参数化合同号，而不是录制现场默认值。
- `lab_modal_supplier_contact_001`：验证弹窗内表单填写不会落到背景同名字段。
- `lab_popup_report_download_001`：验证 popup/tab 下载信号归属。

这些用例使用固定业务编号，例如 `CT-2026-RPA-001`、`SUP-2026-002`、`PR-2026-RPA-NEW-001`、`PO-2026-RPA-NEW-001`、`RPT-2026-RPA-001`。

其中带 `e2e` 标签的用例用于评估连续业务流，不只验证单个页面操作。例如先读取合同详情中的字段，再把这些字段带入后续采购申请；或先创建采购申请，再基于新申请生成采购订单。

## 9. 判定机制

runner 会按以下顺序判定用例结果：

- 每个用例开始前调用 reset，确保数据库回到固定 fixtures。
- 执行 `pre_api_assertions`，确认本轮应新增的数据在运行前不存在，防止 fixture 或上次残留导致误放行。
- runner 先注入评测登录态并导航到 `start_path`，再调用 RpaClaw 执行浏览器业务任务。
- 校验基础执行指标，例如 accepted trace 数量和 diagnostics 数量。
- 执行 `api_assertions`，直接读取评测后端业务状态，确认供应商、采购申请、采购订单、审批、报表下载审计等业务结果真的发生。
- 校验页面/产物 telemetry，例如最终 URL、可见文本；下载类用例优先通过评测后端的下载审计 API 判定，避免只因模型回答中提到文件名而误判通过。
- 校验信息抽取类任务时，`expected.extracted_fields` 只从 Agent 的结构化输出或 trace output 中匹配，不再从页面可见文本中匹配，避免“页面上存在字段但没有真正抽取回答”被误判通过。
- 校验需要 Agent 明确给出结论的任务时，使用 `expected.output_text` 匹配 Agent 输出；`expected.visible_text` 只用于真正可采集到页面可见文本的场景。

金额和文本断言会做基础归一化，例如 `680000` 可以匹配 `¥680,000.00`。最终 URL 优先使用最后一个 accepted trace 的 `after_page.url`，避免从历史输入或登录页 URL 中误取结果。

## 10. 查看测评报告

每次运行会生成：

```text
evals/reports/latest-report.json
evals/reports/latest-report.md
evals/reports/runs/{run_id}-report.json
evals/reports/runs/{run_id}-report.md
```

报告包含：

- 总用例数、通过数、失败数。
- 通过率、首轮通过率、修复后通过率。
- 平均耗时。
- 每个用例的失败阶段和失败原因。
- 关键期望产物与业务断言结果。

报告文件属于运行产物，已被 `.gitignore` 忽略；目录中只保留 `.gitkeep`。

## 11. 常用排查

- 后端不可访问：确认 `python -m uvicorn main:app --host 127.0.0.1 --port 8085` 仍在运行。
- 前端不可访问：确认 `npm run dev` 仍在运行，且端口为 `5175`。
- RpaClaw 调用失败：确认 `--rpaclaw-url` 指向正在运行的 RpaClaw 后端。
- 浏览器打不开评测页面：确认 RpaClaw 执行环境能访问 `http://localhost:5175`。如果 RpaClaw 在其他机器或隔离环境中运行，需要把 `--eval-frontend-url` 改成它可访问的地址。
- 用例断言失败：先查看 `evals/reports/latest-report.md` 的 `failure_stage` 和 `failure_message`，再对照对应 YAML 用例中的 `expected` 与 `api_assertions`。
- 用例超时：如果 `failure_stage` 为 `timeout`，先看 YAML 中的 `timeout_s` 是否与场景复杂度匹配，再查看 `latest-report.json` 中已收到的 `raw_events`。简单用例不应通过拉长超时掩盖问题；优先优化指令、页面可定位性或 RpaClaw 录制助手的 repair 收敛。
