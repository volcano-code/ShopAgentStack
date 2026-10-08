# M1.2 — trace continuity and recorded-run adapter

本次是对 M1.1 的增量实现。**不是完整商城、不是生产发布、不是完成的全链路 benchmark。**

## 1. 实际代码接入

`shop_agent_stack.mcp_observed:app` 包装原 `mcp_server.app`，原 MCP 工具定义不变；完整转发 ASGI lifespan，避免丢失 FastMCP 的 session manager。
只在进程 lifespan 内创建 SDK，退出时关闭。新增 `mcp.request` server span；`business.java` 新增 `commerce.call` client span，HTTPX 在发送请求时附加当前 span 的 `traceparent`。
这是 **Python → Java 出站边界**，本次没有给 Java 控制器/数据库/MQ 加 server span，不能声称已经看到 MySQL、RabbitMQ 或 Milvus 子链路。

公网上的 Agent 入口仍忽略外来 trace context。MCP 默认也不信任外来 traceparent，仅内部网络部署的 override 显式开启 `SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL=true`。
仅接收一条合法的 W3C v00 traceparent；重复/畸形/未来版本输入重建 trace，不复制 baggage/tracestate。flags 保留完整字节，兼容 SDK 发出的 `03`（不是仅接受 `00/01`）。
身份仍由原 execution grant 与 Java 后端校验；trace ID 不代表身份、授权、幂等键或操作确认。

不记录 URL/path/query、消息正文、模型完整响应、用户身份、JWT、执行凭据及确认 token；业务异常照常传播，span 只记录低基数错误类别。
不开启 OTel 时不导入 SDK，Java adapter 的业务返回和授权头保持原语义。没有改模型工具白名单、runtime、确认/停止/核实接口或 Store。

`X-Trace-ID` 关联的是该次 HTTP 请求。创建 run 的 POST 与后台 task 共享其继承的上下文；随后重新打开的 SSE 是新 HTTP 请求，**不应假定其 trace_id 等于后台 run 的 trace_id**。本次没有把 trace_id 持久化写进 Store。

## 2. 本地观测栈（配置已实现，当前交付环境未运行 Docker）

在完整仓库应用补丁后，在仓库根目录执行：

```bash
python -m tools.shop_observability.local_env
# 默认生成 deploy/observability/.env，权限 0600；已存在则拒绝覆盖，不打印密码。
docker compose --env-file deploy/observability/.env -f deploy/observability/compose.yaml config --quiet
docker compose --env-file deploy/observability/.env -f deploy/observability/compose.yaml run --rm --no-deps otel-collector validate --config=/etc/otelcol/config.yaml
docker compose --env-file deploy/observability/.env -f deploy/observability/compose.yaml up -d
```

健康端点：Collector `http://127.0.0.1:13133/`，Tempo `http://127.0.0.1:13200/ready`，Grafana `http://127.0.0.1:13000/api/health`。
使用外部 HTTP 探测，不假定 Collector/Tempo 镜像带 shell/curl。`depends_on` 仅控制启动顺序，不是 readiness 验收。

Grafana：`http://127.0.0.1:13000`，用户名 `admin`，密码在私有 `.env` 文件中。已配置 Tempo datasource、Agent/MCP trace 查询面板；需要在实际浏览器中验收面板。
UI、Tempo query、OTLP 接口只绑定本机回环，网络 internal，关闭匿名登录、使用报告及更新检查。容器限内存、只读根文件系统、cap_drop ALL；数据用有界 tmpfs，**重启即丢失，不是持久化服务**。
镜像固定具体版本而非 `latest`，版本标签已核对官方发布；**未拉取镜像，也未验证 digest 或进行供应链漏洞扫描**。

Collector 配置是 trace-only：有限队列、batch、memory limiter、属性白名单、去掉 span event/status message/tracestate。转换失败丢弃，不通过 debug exporter 输出载荷。
这只是当前已知仪表化格式的防御层，不是能识别所有未知厂商载荷/属性值中的 PII 的通用清洗器，也不是安全审计证明。
没有 Prometheus/Loki、SLO、业务成功率或成本 dashboard，面板只用于定位 trace。

### 必须查询回读的 smoke

等待健康端点返回 200，在已存在且受保护的本地输出目录执行：

```bash
mkdir -p .local/observability
python -m tools.shop_observability.collector_smoke --out .local/observability/smoke-001.json
```

脚本真正 POST OTLP/HTTP，随后通过 Tempo trace-ID API 轮询回读，核对 5 个 span 的 ID/名称/父子关系，以及 synthetic privacy sentinel 是否被 Collector 清掉。
仅 POST 200、缺 span、父子关系错误、仍有 sentinel、超时、partial success 都不能通过。输出不覆盖旧文件。
此脚本发送的是**合成 span**，即使 smoke 通过，也只证明传输、回读和配置过滤，不证明真实 Agent/MCP/Java 业务端到端。

停止只针对该诊断栈：
```bash
docker compose --env-file deploy/observability/.env -f deploy/observability/compose.yaml down
```

### 原商城连接

保留原有 Commerce Compose 文件列表，在 `compose.p2.yml` 之后附加 `-f deploy/compose.otel.yml`，再运行原来的 build/up 流程。
该 override 的服务名/入口来自仓库真实 `deploy/compose.p2.yml`，为 agent 与 commerce-mcp 构建启用 OTel 的镜像，并加入 `shop-observability_default` 外部网络。
诊断栈以自定义 project name 启动时，设置 `SHOP_OBS_NETWORK` 为它实际创建的网络名。不要因此把 MCP 8011 暴露公网。
采样默认 0.1，故障实验需要显式临时设置 1。不要把抽样 trace 当完整写入审计或把采样 run 数当请求成功率分母。

## 3. Agent run → evaluator

原 Store.run() 有事件和 created，但没有可靠终止时间；原 retrieval 事件没有完整排序；usage.reported_tokens 不能拆成输入/输出或金额。
因此适配器明确需要**独立 harness/reviewer sidecar**。不会用当前时间减 created、用最终引用代替初始检索排序、用 COMPLETED 自评任务成功、用 trace 代替后端退款审计。

输入：`cases.jsonl`（既有 evaluator v1）、`raw-runs.jsonl`（Store.run() 快照）、`observations.jsonl`（case/run 映射、耗时、人工/确定性判断等）、`experiment.json`、用于该轮运行的提示词文件。
输出：`runs.jsonl`、兼容既有 evaluator 的 `manifest.json`、包含 commit/patch、数据/提示词/输入哈希、检索配置、模型、时间与可用性统计的 `provenance.json`。
所有输入先验证，再新建 0700 输出目录、0600 文件；不覆盖旧实验。原始快照可能有隐私，只保留在受保护的本地位置，不上传 raw/observations 到公网。
`recorded` 代表读取已有记录，不代表真实模型已运行或来源已被工具认证；fixture provider 无法通过参数伪装为 recorded。

```bash
python -m tools.shop_quality.export_agent \
  --cases /private/cases.jsonl --raw-runs /private/raw-runs.jsonl \
  --observations /private/observations.jsonl --experiment /private/experiment.json \
  --prompt-file /private/system-prompt.txt --out /private/export-001
python -m tools.shop_quality evaluate \
  --cases /private/cases.jsonl --runs /private/export-001/runs.jsonl \
  --manifest /private/export-001/manifest.json --split heldout --out /private/report-001
```

有 qrels 的案例必须提供 `retrieved_ids`，并标记 `retrieval_unit: initial_query`：初次检索、gate/补查前的最终排序。当前 recorder 尚未自动捕获这一数据；缺失时导出拒绝，而不是算伪造的 Recall。
`abstained` 来自独立 sidecar 判断；`task_judgment` 只能是 human/deterministic_backend，缺失则 evaluator 按未成功处理。
`write_audit` 只能来自 backend（fixture 单独标明），缺失审计不能证明安全；工具不会认证 sidecar 声称的来源，应由可信 harness 与签审流程保证。
返回 `ok` 仅表示 Agent 回合完成；`WAITING_CONFIRMATION` 不是退款提交完成，最终业务成功必须另建业务级评测。工具统计仅覆盖开始执行的事件，不能宣称覆盖所有被拒绝的模型工具选择。
缺失任务仍留在 dataset 分母；有 raw run 却没有 case 映射则拒绝，防止静默丢掉失败。重复实验分开记录，不能挑选最好的一次。

### 可直接运行的 fixture 示例

```bash
python -m tools.shop_quality.export_agent \
  --cases tools/shop_quality/examples/agent-export/cases.jsonl \
  --raw-runs tools/shop_quality/examples/agent-export/raw-runs.jsonl \
  --observations tools/shop_quality/examples/agent-export/observations.jsonl \
  --experiment tools/shop_quality/examples/agent-export/experiment.json \
  --prompt-file tools/shop_quality/examples/agent-export/prompt.txt \
  --out /tmp/shop-export-example --allow-fixture
python -m tools.shop_quality evaluate \
  --cases tools/shop_quality/examples/agent-export/cases.jsonl \
  --runs /tmp/shop-export-example/runs.jsonl --manifest /tmp/shop-export-example/manifest.json \
  --out /tmp/shop-eval-example --allow-fixture
```

这些示例只验证软件接口，数字不能用于简历、模型准确率或性能宣传。

## 4. CI 与验收

原 `shop-quality.yml` 保留快速 tooling/web/Java/Agent 门禁，新增 tooling 零测试拒绝。Agent enabled image 要求真实 MCP 协议测试，SDK 缺失必须失败；default-off 保持无 OTel 的业务行为测试。
`observability-smoke.yml` 仅 workflow_dispatch：真实 Collector validate、Compose 启动、HTTP readiness、OTLP→Tempo 查询回读、证据收集、仅清理独立 CI project。
本次没有触发 hosted Actions，没有自动发布镜像、部署公网或使用真实模型密钥。

最短后续路径：在可运行 Docker 的完整 checkout 中应用补丁 → 两种目标镜像回归（真实 MCP 必须执行）→ 手动观测 smoke → 原 Compose+override 与浏览器 trace 检查 → 再做 Java server/MQ/retrieval instrumentation 和真实 benchmark capture。

## 5. 设计依据（官方）

- https://opentelemetry.io/docs/collector/configuration/
- https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.161.0
- https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.161.0/processor/transformprocessor/README.md
- https://grafana.com/docs/tempo/latest/set-up-for-tracing/setup-tempo/deploy/locally/linux/
- https://grafana.com/docs/tempo/latest/api_docs/
- https://github.com/grafana/tempo/releases/tag/v3.0.3
- https://github.com/grafana/grafana/releases/tag/v13.2.2

配置/版本核对不等于容器或 Collector OTTL parser 实际通过；以后者运行日志为最终证据。
