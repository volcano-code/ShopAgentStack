# M2.3：单实例执行准入与启动失败恢复

该增量只治理现有单实例 Agent 的任务准入和启动交接；不宣称已实现
多实例队列、分布式任务租约、生产并发容量或完整 QPS 限流。

- 在 SQLite 的 `BEGIN IMMEDIATE` 事务中统计所有活动状态
  `QUEUED/RUNNING/CONFIRMING/STOPPING`；最多 8 个全局活动任务、
  每位用户最多 3 个。跨不同会话仍会统计，避免只限制同一会话。
- 完全相同的 `(session_id, request_id)` 重放先返回原运行，
  即使此时活跃任务达到上限也不会创建第二个任务。
- 新请求超出限额会得到 `429`；没有隐式重试或等待队列，前端给出
  明确提示。待用户确认、结果不明的操作不消耗模型槽，但原业务
  状态机、账号权限和历史保护规则继续生效。
- 若记录已持久化为 `QUEUED`，却在解析模型配置或构造任务时失败，
  使用带归属验证的条件更新转为 `FAILED` 并追加状态事件，
  不长期占用会话。失败返回原运行及明确的 `FAILED` 状态（HTTP 200），
  而不是让前端把已知失败误认为传输结果不明并持续重发同一请求 ID。条件更新只接受 `QUEUED`，不会覆盖用户刚刚发起
  的 `STOPPING` 或已有的其它状态。
- 异常内容、密钥和配置错误正文不会写入状态事件或 HTTP 响应。
  CI 使用合成 SQLite、身份与模型偏好替身，不调用任何付费模型。

## 验收边界

```bash
python -m pytest services/agent/tests/test_run_admission_m23.py -q
npm --prefix apps/web run test:unit
```

需结合当前提交的 Shop quality、Business E2E 和浏览器回归判断是否合格。
上述阈值只是保守的初始配置，不代表已量化的服务承载力。
后续仍需治理模型连接测试等其它入口、精确用量预算、队列/多进程
归属问题及故障注入；不能将本阶段称为全部 M2.3 完成。

明确区分：启动失败返回 HTTP 200 / `run.status=FAILED`，代表幂等的运行记录已成功生成并标记失败；这不是模型任务成功。前端收到失败运行后可清除旧请求 ID；新请求需由用户再次显式发送。真正的传输中断或未知响应仍不得自动创建新请求。


## M2.3 第二增量：模型连接测试单独限额

`POST /settings/providers/{provider}/test` 即使只有简短提示，也可能触发第三方模型账单。
该入口不通过对话运行状态机，因此不能仅依赖 `create_run` 的 8/3 个并发槽限制。
当前实现新增独立的 SQLite 准入事务和元数据表 `provider_test_attempts`：

- 同一账号同时最多 **1** 个测试，全实例同时最多 **4** 个测试。
- 同一账号滚动 **300 秒** 内最多 **3** 次已准入测试。失败和超时也消耗窗口名额，
  但 429 拒绝不消耗名额，也绝不自动重试。
- 模型连接调用包裹 **20 秒** 异步超时，活动租约有效期 **45 秒**；
  释放放在 `finally`，包含异常和请求取消。
- 启动时释放上个进程遗留的活动标记（但近五分钟的尝试次数仍有效）；
  创建与计数在 `BEGIN IMMEDIATE` 中进行，避免同进程并发绕过。
- 数据库只保存匿名请求标识、账户 ID、时间和活动状态，
  不写入模型密钥、端点、提示、响应或供应商异常内容。
- 拒绝使用与运行准入同一 429 响应约定；业务确认权限完全不受此功能改变。

**边界：** 这不是硬性计费预算，单次外部模型调用的实际最终费用仍可能未知；
也不代表跨多个 Agent 进程的完整租约/fencing 协议。时间失真、
进程异常及远端供应商对取消的处理仍存在限制。
CI 只使用模拟 provider，不执行真实的收费连接测试。


## M2.3 第三增量：后台任务异常退出的状态收尾

原来的 `tasks[rid]` 完成回调只负责删除内存引用；如果
`observability.execute_run` 在正常 `runtime.execute` 的异常处理器之外失败，
SQLite 可能长时间残留 `QUEUED/RUNNING`，阻挡新请求并继续占用名额。
现在回调会读取任务最终结果，并以带账户归属的原子条件更新：

- 只有仍处于 `QUEUED` 或 `RUNNING` 的运行可标为 `FAILED`，
  未开始就被取消的任务则可标为 `INTERRUPTED`。
- `STOPPING`、`CONFIRMING`、`UNCERTAIN`、`WAITING_CONFIRMATION`、
  `COMPLETED` 等状态一律不覆盖；用户明确停止后的完成路径保持原样。
- 任务异常只在日志中记录异常类型，不记录异常文本、内部模型提示、
  HTTP 原文、密钥或局部变量。恢复失败也不会在回调中继续抛出。
- 已成功执行并完成状态更新的后台任务不会产生重复状态事件。
  意外成功返回但状态仍为 RUNNING 的情况按不完整执行处理。

该增量不做模型任务自动重新执行，也不宣称已实现分布式任务租约。
新测试专门构造框架包装器异常、取消、并发状态改变与归属隔离，
不会调用外部模型或真实业务操作。

## M2.3 模型调用准入与使用量审计

`model_budget.ModelBudget` 现在同时覆盖**正常对话和政策证据核查两条模型请求路径**。
每次运行最多准入 **8 次**模型请求；失败或取消也占一次额度。
`TOKEN_THRESHOLD=16000` 仍只用于决定是否准入后续调用，按供应商**已报告**
用量计数；下一次请求尝试预留 1,024 tokens 输入余量，并相应降低
`max_output_tokens`。这些都是本地的**继续执行约束**，不等于实际发票金额
或总 token 消耗的绝对上限。

每次已经准入的调用记录一条没有敏感字段的 `usage` 事件：
`call_index`、`max_output_tokens`、`usage_known`、`reported_tokens`、
已报告累计和未知用量次数。请求异常退出时记录 `outcome=unknown`，
不把缺失用量当成零成本。具体供应商价格可能随时间、输入输出、
缓存命中及折扣而变化，目前**不生成货币成本估算**。

`GET /runs/{rid}/usage` 是只读、按账户归属授权的审计摘要：
区分 `reported_token_sum`（已知部分）与 `total_tokens`（仅当全部
调用的用量已知时有数值），`estimated_cost` 永远为 `null`；
如果超过 100 条旧用量日志，也不宣称已完整汇总。

**边界**：预留量不是精确的输入 token 测量；一次进行中的模型调用
可能最终超过阈值。某些服务不返回完整 usage 或忽略输出参数，
也可能在客户端取消后计费。对真实账单的硬性费用控制必须结合供应商
配额和实际账单核对，不能仅依赖此本地预算。

## M2.3 第四增量：严格验收和受限性能证据

Agent 的 `Shop quality` 工作流（OpenTelemetry 开/关两种矩阵）
现新增真实运行的 `tools.shop_quality.m23_evidence` 验收步骤：
- 解析当前构建实际执行的 `artifacts/agent/tests.xml`，要求
  M2.3 核心准入、测试连接限流、后台异常收尾、用量审计、回执丢失、
  取消核实与敏感数据脱敏测试全部执行且通过。
  **缺失、跳过、重复或失败的必测用例直接使工作流失败**，
  不能拿旧 CI 或测试名称的字符串存在性代替执行证据。
- 在 GitHub runner 上以 Python 3.12 运行纯 SQLite 可丢弃基准：
  `same` 和 `distinct` 两种账号模式，各执行 **5 次**，
  每次 **40 个请求**、最多 **12 个线程**，
  实测 429 拒绝数、已准入数、数据库活动数与本地 p50/p95。
- 输出包含 `checkout_sha`、`tree_sha`、PR head SHA、
  Python 版本、必测用例身份、每次原始延迟和中位数，
  文件 `artifacts/agent/m23-admission-evidence.json` 与 JUnit
  一起存入精确提交关联的 CI artifact。
- 报告固定标识 **仅限 SQLite 准入锁路径**，
  `production_capacity_verified=false`、
  `real_model_called=false`、`end_to_end_latency_verified=false`。
  不生成真实交易、支付或业务数据；不把这个数字当生产 QPS、
  实际模型账单或多进程可用性。

复现（已运行过对应 Agent 测试、且仓库包含当前提交时）：

```bash
PYTHONPATH=services/agent python -m tools.shop_quality.m23_evidence \
  --junit artifacts/agent/tests.xml \
  --out artifacts/agent/m23-admission-evidence.json \
  --requests 40 --workers 12 --trials 5
```

独立 Java/MCP/MySQL 回执丢失的真实服务测试属于 Business E2E
的另一份证据，不包含在这个 Python SQLite 量测报告中。
