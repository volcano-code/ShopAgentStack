# ShopAgentStack：M1 质量与可观测性增量

日期：2026-09-22。目标：面向 **2027 年秋招**，先把已有业务能力变成可复验的工程证据。

## 交付边界

这是新增工具，不是完整商城源码、已部署服务或已合并 PR。原仓库仅部分公开入口文件可读取，未取得完整 checkout 和基线 commit SHA。因此本批不修改 Java 交易逻辑、Agent 工具权限、MCP 协议、原始依赖锁和现有前端页面。

| 内容 | 已做 | 尚未验证或完成 |
|---|---|---|
| 质量评测器 | 标准库 CLI、严格输入校验、指标、失败门禁、HTML/JSON/Markdown 报告 | 原业务运行日志的导出适配器、真实模型 benchmark |
| 追踪模块 | 真正使用 OpenTelemetry SDK；属性白名单、异步上下文、纯 ASGI/SSE middleware | 原 Agent 镜像接入、Java/MCP/检索跨服务链路、Collector/Grafana |
| GitHub Actions | 前端、Java、Agent、质量工具四类 job，汇总门禁与证据上传 | GitHub 首次执行、原仓库全量回归 |
| 供应链基础 | Action SHA 固定、只读权限、无持久化 checkout 凭据、Dependabot 配置 | 漏洞扫描、SBOM、密钥扫描、独立安全审计 |
| 公网 Demo | 未执行 | 云资源、HTTPS、隔离、备份、安全验证与部署 |

六个 `fixtures` 是**测试评测器的软件夹具**，不是新建的六个真实电商评测任务；其中的时间、模型名称和业务审计也是夹具。不能将夹具成功率写进项目简历。

## 本地运行新增代码

Python 3.12 或 3.13；当前交付环境实际验证 Python 3.13。可新建独立虚拟环境，不要直接升级原 Agent 环境。

```bash
python -m venv .venv-quality
# Windows PowerShell: .\.venv-quality\Scripts\Activate.ps1
# Linux/macOS: source .venv-quality/bin/activate
python -m pip install -r tools/shop_quality/requirements-test.txt -r tools/shop_observability/requirements.txt
python -m pytest tools/shop_quality/tests tools/shop_observability/tests -q
```

这两个 requirements 固定新增工具的直接依赖版本，不声称已对全部传递依赖锁定或完成漏洞审计。离线评测 CLI 只用 Python 标准库；pytest 和 OTel 分别用于测试、可观测性。

运行软件夹具（必须显式允许）：

```bash
python -m tools.shop_quality evaluate --cases tools/shop_quality/fixtures/cases.jsonl --runs tools/shop_quality/fixtures/runs-valid.jsonl --manifest tools/shop_quality/fixtures/manifest.json --gates tools/shop_quality/fixtures/gates.json --out artifacts/fixture-valid --allow-fixture
```

将 `runs-valid.jsonl` 换成 `runs-adversarial.jsonl` 应退出 **1**，不是 0。省略 `--allow-fixture` 应退出 **2**。退出码 0 表示本次计算成功且已配置门禁通过；未提供 `--gates` 时只计算、不代表通过质量门禁。每次使用不同输出目录，避免将旧报告误当成此次输入校验失败后的新结果。

## 接入真实评测的合同

本批没有猜测原业务 API、字段或已有评测脚本的输出格式，也没有新增未实际连接的“模拟后端适配器”。保留原 `evaluation/`、`scripts/evaluate-retrieval.ps1` 等资产。在取得完整源码后，增加一个只负责映射真实结果的导出模块，输出下面三类文件。

### 1. cases.jsonl：冻结金标准

每行必须包含：

```json
{"case_id":"after-sale-001","split":"heldout","qrels":{"policy-id:version:clause":3},"expected_citation_ids":["policy-id:version:clause"],"allowed_tools":["search_policy","get_order"],"expected_tools":["search_policy"],"should_abstain":false,"check_write_safety":true,"tags":["after-sale"]}
```

示例 ID 仅说明格式，需要替换成实际已发布且可对照的政策版本和真实任务 ID。`qrels` 为 0–3 级相关性，0 不相关；`split` 只有 dev/heldout。查询文本和敏感订单信息留在受控的数据集目录，以 `case_id` 与这里关联；输出合同不收集客户隐私。

金标准在模型/提示词实验之前冻结，heldout 不用于提示词调参。同一输入文件字节以 SHA256 绑定；排序或换行改变也改变哈希。

### 2. runs.jsonl：实际观测

每行必需：`case_id`、`status`、`retrieved_ids`、`citation_ids`、`selected_tools`、`abstained`。

`status` 为 ok/error/timeout/skipped。attempted 状态必须记录实际 `latency_ms`。`selected_tools` 是**去重后的已选择工具名称集合**，不是完整调用顺序；本版不做动作序列对齐，不可把 exact-match 指标描述为轨迹级正确率。

可选字段：实际 input/output tokens、成本美元、trace_id、独立任务判定和写操作审计。未取得成本、token、延迟或人工标注时不能填写估计值来制造好结果。

```json
{"task_judgment":{"passed":true,"method":"deterministic_backend","reference":"test-run/ledger-assertion-001"},"write_audit":{"complete":true,"source":"backend","events":[{"operation_id":"op-001","committed":true,"confirmation_validated":true,"authorized":true}]}}
```

以上只是两个可选字段的格式片段，不是一行完整 run。task_judgment 只接受 human 或 deterministic_backend；不接受模型自评作为独立正确性证据。

`committed` 必须取自权威后端状态/账本，而不是 LLM 的“操作成功”文本。用户确认与权限结果也来自后端。`complete:true, events:[]` 只有在后端确实检查了该任务全部副作用且确认没有写入时才有效。超时任务可能已经发生写入，必须在观测窗口结束并对账后再填完整审计。

**局限：**评测器只能校验输入结构和所提供的观测，无法验证输入文件是否由可信后端生成。它不是运行时权限拦截器、审计签名验证器或安全认证。不要由模型填写 `source:backend` 来伪装证据。

### 3. manifest.json：实验身份

```json
{"schema_version":1,"run_id":"heldout-bge-baseline-001","source_kind":"recorded","dataset_sha256":"填写实际 cases 文件的 64 位 SHA256","system":{"git_revision":"填写实际 40 位 Git commit SHA","provider":"实际供应商","model":"实际模型版本","prompt_version":"实际提示词版本","retrieval_version":"实际检索/重排配置版本"}}
```

`source_kind` 为 fixture/recorded/live。后两者要求合法 40 位 commit SHA，但仍不认证该 commit 是否真实执行过；保存原始日志、锁文件、环境和运行命令作为额外证据。不要把 source_kind 改成 live 就宣称进行了在线实验。

使用真实输入时不传 `--allow-fixture`。重复模型运行应使用独立 run_id，分别记录随机性和成本。开发集与 heldout 的金标准都不能回写。

## 指标和失败规则

| 指标 | 定义/边界 |
|---|---|
| Recall@k | 命中的正相关条目数 / 全部正相关条目数；不是除以 k |
| MRR@k | 前 k 内第一个正相关结果的倒数排名 |
| nDCG@k | gain = 2^relevance - 1；按理想排序归一化 |
| citation_id_precision/recall | 仅引用 ID 集合匹配，不证明答案语义蕴含或事实正确 |
| task_success_rate | 状态 ok 且独立判定 passed 的任务数 / 冻结任务总数 |
| 缺失、超时、失败、跳过、无判定 | 保留在任务成功率分母，不因日志缺失删任务 |
| 无相关文档问题 | 检索指标未定义，报告 null；拒答另行计分 |
| p50/p95 | 在已尝试且有耗时的记录上计算线性插值分位数，包含失败/超时耗时 |
| tokens/cost | 只聚合实际提供的数据，报告各自样本数；不把缺失值冒充零成本 |
| 写入安全 | 不完整审计或确认/权限未知均不能视为安全；显式违规不可被宽松阈值掩盖 |

每个 metric 同时返回 value 和 n。门禁中 `min_samples` 防止靠少量可用样本制造通过；缺失指标、未知指标、空评测和阈值反转会失败。使用 `check_write_safety:false` 的只读数据集意味着不评估该任务的写入安全，不能由此推出项目写入安全性；一旦观测有写事件，仍纳入检查。

门禁配置随业务目标另行冻结。随包 gates.json 只针对六个软件夹具，不是已经验收的业务 SLO。

### A/B 比较

```bash
python -m tools.shop_quality compare --baseline artifacts/baseline/report.json --candidate artifacts/candidate/report.json --metric ndcg@5 --samples 2000 --out artifacts/comparison.json
```

比较器要求相同数据哈希、split、工具版本、k、案例集合和 source_kind；逐例配对 bootstrap 生成 95% 区间。该区间仅反映当前案例重采样，不等于独立多次模型运行，也未做多重比较校正。它不是任意 benchmark 百分比的显著性认证。

## OpenTelemetry 接入边界

`tools/shop_observability/telemetry.py` 提供 Telemetry、TraceMiddleware。真实 SDK 冒烟运行：

```bash
python -m tools.shop_observability.examples.trace_demo --out artifacts/trace-smoke.json
```

它生成实际 SDK span 和父子关系，但 span 内操作是 synthetic，不调用 LLM、Java、MCP 或 Milvus。当前并未配置 OTLP exporter/Collector。

宿主接入需在完整源码下完成：先为 Agent 增加经过依赖冲突验证的 SDK 锁定和镜像 COPY/安装，再由宿主创建 TracerProvider、exporter 和生命周期管理。不要在 import 时覆盖全局 provider。

原 Agent Dockerfile 只复制 `shop_agent_stack` 和 `tests`，**新增根目录 tools 不会自动进入原 Agent 镜像**。所以把本补丁放进仓库不等于已经启用追踪。

完成打包后，可在宿主组装处使用以下形式；`app` 和 `telemetry` 必须是项目实际实例，不应直接用此片段覆盖原入口：

```python
from tools.shop_observability.telemetry import TraceMiddleware
# app 为原 ASGI 应用；telemetry 为宿主创建并配置 exporter 的 Telemetry 实例。
traced_app = TraceMiddleware(app, telemetry, trust_inbound=False)
```

业务执行位置用固定的 agent.run、llm.call、tool.call、retrieval.search、source.validate 名称创建 span。trusted tools/models/providers 白名单必须来自服务端配置，不可来自 LLM 输出。HTTPX/MCP 等出站路径需要显式添加 `telemetry.outbound_headers()` 的 traceparent/tracestate；这不替代已有认证头，也不要转发任意 baggage。

纯 ASGI middleware 在整个 SSE 响应完成后结束 span，避免只观测到响应头。公开入口默认忽略客户端 traceparent；只有明确可信的内部链路才启用 trust_inbound。x-trace-id 仅用于诊断关联，不能授权任何操作。

辅助模块不采集 prompt、订单号、密钥、请求 URL、查询参数或异常原文；异常只记录固定错误类别。宿主其他日志/SDK自动插桩仍可能泄露数据，须单独审查。直接调用原始 span.set_attribute 绕过辅助白名单也不在该模块防护范围。

## CI 首次验收

工作流是候选实现，未在 GitHub 实跑，不添加虚假绿色 badge。四类 job：新增工具、Node24 前端、Java17 Commerce、Python3.12 Agent Dockerfile。前端 build 已包含 typecheck；不调用不存在的 lint/test script。

Agent 在 --network none 的独立容器里运行原 tests，并提供 uid10001 可写 /data；若原测试需要额外环境或真实依赖，首轮可能失败。应配置隔离测试服务或准确划分 unit/integration 后补齐 CI，而不是添加 `|| true`、`continue-on-error` 或 `--if-present` 掩盖失败。

Commerce 默认 clean verify + 不跳过测试 + 禁用 Fabric8 Docker 构建；JUnit 门禁检查实际 testcase，不接受零执行测试的绿灯。真实数据库回归需要隔离依赖，未声明当前命令可替代完整系统验收。

`.github/dependabot.yml` 为新增配置；已有同名文件时安装器拒绝覆盖，需人工合并。SHA pin 只固定 Action 代码，不等于所有镜像、平台、Maven/npm/pip 依赖都已不可变或完成安全审计。Action 升级需要审阅自动更新 PR。

CI 不执行公网部署，不拿生产 secrets，不暂停原 MQ/Milvus，不在真实演示数据库中造单。原 verify-p3c/verify-p4 等故障测试必须留在一次性数据库/卷/网络中单独串行运行；本轮没有执行。

## 原始入口核验来源

下列公开文件用于核验入口，并非整个仓库的完整审计；本轮没有获取可信基线 commit，故不声称补丁与某个 upstream SHA 全量兼容。

- 仓库说明：https://github.com/GRIZ200005/shop-agent-stack
- 前端命令：https://github.com/GRIZ200005/shop-agent-stack/blob/main/apps/web/package.json
- Agent 镜像：https://github.com/GRIZ200005/shop-agent-stack/blob/main/services/agent/Dockerfile
- Java 项目：https://github.com/GRIZ200005/shop-agent-stack/blob/main/services/commerce/pom.xml
- 测试范围：https://github.com/GRIZ200005/shop-agent-stack/blob/main/docs/testing.md
- GitHub 安全建议：https://docs.github.com/en/actions/reference/security/secure-use
- OpenTelemetry Python：https://opentelemetry.io/docs/languages/python/instrumentation/

## 接下来唯一的集成验收点

在完整原仓库中导出一次真实固定任务集的 RAG/Agent 运行结果，接入该 CLI；同时把追踪模块打包并在实际 Agent 请求路径中调用。只有 CI、真实数据报告和真实调用链都留有可复现证据，才将 M1 从“增量工具已验证”升级为“项目集成已验证”。不以新增代码行数或 Action 配置存在代替这一验收。
