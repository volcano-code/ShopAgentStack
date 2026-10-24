# M2 候选版本验证指南

核验记录日期：2026-10-23（北京时间）。

本页说明如何复核同一候选版本的真实 CI、Hybrid 重复运行和公开验收结果。验证材料用于人工审查；候选是否可发布，还取决于独立的模型效果、数据迁移及交付验收。

## 版本与证据身份

当前核查对象为 [PR #62](https://github.com/volcano-code/ShopAgentStack/pull/62)，目标分支为 `main`，候选分支为 `integration/m2-final-no-key-20261022`。

| 身份 | 精确值 |
|---|---|
| 候选 head | `df09b22710cff024c4ae65f834510a26e7e0ccde` |
| 候选源码 tree | `08d35577e47aeb2ff10194ff3366fd50ca9e81e2` |
| 基线 base | `e2a5310ec8a33f3078f796e8b7f2230c82fa2059` |
| 本轮 CI 合成合并提交 | `4e457f5c2b21c02c01a9ae3beb2b5faa113060a0` |

相同 head 同时关联 PR #61 和 #62，必须核对 PR 编号及 base。CI 的 checkout 可以是 `refs/pull/62/merge`；此时需检查其父提交包含上述 head 和 base。合成合并提交的存在不表示 PR 已合并。后续文档提交也不能自动继承这里记录的代码验收结论。

## 按执行次数检查

1. 核对工作流的 `event`、`head_sha`、`pull_requests` 和 base，再读取具体 `run_attempt`。保留首次结果，可通过 `actions/runs/{run_id}/attempts/{attempt}` 查询历史执行。
2. 针对每次定向重跑，记录实际 job ID、名称、开始／完成时间和结论。仅重跑 Hybrid 时，不把沿用的 BM25 结果记成新的 BM25 执行。
3. 核对 job checkout、真实服务探针及产物；仅有绿色状态不足以替代内容审核。下载 ZIP 后将摘要与 GitHub artifact 元数据比对，再读取允许公开的证据字段。
4. 保留失败和未完成状态。当前有执行运行中或失败时，不能只选择旧成功记录宣布候选通过。代码或基线变化后，重新判断哪些结果仍适用于新候选。

原始 attempt 1 的十组工作流已独立核对为 PR #62 成功记录。完整清单和门禁格式见 [M2.7 审查说明](../m27-release-review.md)。

## Hybrid 重复验证记录

[既有故障记录](../m24-hybrid-intermittent-diagnostics.md)要求同一候选至少三次独立 Admin Hybrid 执行，并结合六阶段检索生命周期验证。

| 执行 | Run／Job | 结果 |
|---|---|---|
| 原始 attempt 1 | [39776916646／119818082668](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916646/job/119818082668) | 成功 |
| 独立 attempt 2 | [39776916646／119847031444](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916646/job/119847031444) | 成功；真实 Hybrid、来源、比较及清理通过 |
| 独立 attempt 3 | [39776916646／119971398917](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916646/job/119971398917) | 成功；真实 Hybrid、来源、比较及清理通过 |

**累计结论：原始执行和本轮追加的两次独立重跑均成功，共 3/3；满足既定的三次观察要求。历史间歇问题在这些运行中未复现，内部根因仍未被完整证明。**

三次 Hybrid 的实际开始／结束时间分别为北京时间 10-22 20:27:35—20:37:45、10-22 21:16:25—21:23:17、10-23 01:14:11—01:21:01。每次均有不同执行时间、日志及上传产物。GitHub 为复用的 BM25 结果也分配了新的 job ID 和 attempt，但其三条记录的时间均为 10-22 20:27:20—20:40:38；因此 BM25 只计一次实际执行，不能仅按 job ID 数量计数。

要求 Hybrid 的探针必须实际返回 Hybrid，且来源版本、摘要及可见性检查通过。故障注入场景允许按合同明确降级，但不能用该规则放宽正常 Hybrid 探针。若复现失败，保留 `failure_stage`、`failure_type`、`failure_code`，据此定位并修复具体层。

PR #54 曾同一 head 首次成功、重跑失败。随后 [PR #55 的诊断执行](https://github.com/volcano-code/ShopAgentStack/actions/runs/39772612048/job/119819232607)记录到 `failure_stage=hybrid-search`、`failure_code=search-method`：严格 Hybrid 方法断言未通过，与内部失败后显式降级的源码路径相符。现有证据无法继续区分查询锁繁忙、worker 超时或其他内部错误。当前三次通过只表明这些独立运行未复现旧问题，不能据此宣称已证明历史根因或永久消除。

## 已读取的真实检索结果

[原始 Hybrid E2E](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916637)的 artifact `12425987485` 已下载并校验 SHA-256：

`0d01ba3815101d1825a748f1833297afe151129bc490c60394eae7eb768200e4`

六阶段生命周期通过，包括离线修订、Milvus 停机、撤回和恢复；各阶段来源检查及清理通过。其独立并发小样本结果如下：

| 并发 | 请求数 | Hybrid／降级／错误 | 观测 p95（毫秒） |
|---:|---:|---|---:|
| 1 | 8 | 8／0／0 | 299.674 |
| 2 | 8 | 8／0／0 | 373.579 |
| 4 | 8 | 8／0／0 | 773.426 |
| 8 | 8 | 8／0／0 | 1466.385 |

这 32 个请求没有调用付费模型。结果来自临时 CI 环境，没有完成同机受控前后对比，不能据此给出生产 QPS、p99 或因果加速结论。

## 审查与保存

检查 job 时，将应用验证、清理和产物上传的结果分别核对。真实浏览器阶段应确认未使用浏览器替身；商品比较阶段应检查当前上架商品、Java 授权、详情与快照一致以及非法 ID 拒绝。原始 BM25 与 Hybrid 两种环境均已完成两个商品的上述核对；这些结果证明工具及后台数据约束，尚未验证 DeepSeek 能否作出正确比较建议。任何后续失败都应保留实际阶段，不能以某个前置步骤通过替代整项执行通过。

十组完整 CI 与三次 Hybrid 重复运行是两类互补证据：前者覆盖当前集成范围，后者针对已有间歇问题补充观察。汇总时分别列出覆盖范围，避免把同一记录重复计数。待审查记录完整后，再判断能否交由人工评审。

将已独立核对的状态输入现有 `tools.shop_quality.m27_release_review`。其积极结论仅为 `ready_for_human_review_only`；离线工具不能认证调用方提供的 GitHub JSON，也不能证明真实模型准确率、迁移兼容或生产发布条件已满足。

原始日志和运行输出保存在忽略的私有目录；本轮提交只保留经过审核的公开结果、链接及操作说明，不包含凭据或原始业务数据。

## 本轮审查结果与接续

在受测 `df09b227` 干净 checkout 上，现有发布审查测试 16 项通过，真实探针相关合同测试 47 项通过。使用 Admin attempt 2 尚在运行时的真实记录调用门禁，按预期退出 2 且未生成通过文件。第三次结束后，重新读取 PR、十组工作流及所有 Admin job 的真实 API 元数据，核对 PR/head/base/path/最新 attempt 和独立执行时间，再运行同一门禁，退出 0，结果为 `ready_for_human_review_only`。这些本地测试与 CI 的真实服务验证分别统计，不把合同通过率当成模型正确率。

下表是北京时间 2026-10-23 01:21:37 重新采集的最终元数据快照；Admin 使用最新 attempt 3，其余使用各自最新 attempt 1。每条均绑定 PR #62 及本页精确 head/base。

| 工作流 | 当前 attempt | 结果 | 实际运行 |
|---|---:|---|---|
| Shop quality | 1 | success | [39776916556](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916556) |
| Workspace experience | 1 | success | [39776916558](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916558) |
| Observability integration | 1 | success | [39776916587](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916587) |
| Business E2E | 1 | success | [39776916629](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916629) |
| Business tracing E2E | 1 | success | [39776916624](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916624) |
| Hybrid retrieval E2E | 1 | success | [39776916637](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916637) |
| Admin review UI | 3 | success | [39776916646](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916646) |
| Local demo smoke | 1 | success | [39776916563](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916563) |
| Demo seed import | 1 | success | [39776916576](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916576) |
| Demo cold recovery | 1 | success | [39776916736](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916736) |

第二、第三次 Admin Hybrid 产物分别为 [12428178356](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916646/artifacts/12428178356) 和 [12444971696](https://github.com/volcano-code/ShopAgentStack/actions/runs/39776916646/artifacts/12444971696)，ZIP 摘要已与 GitHub 元数据核对，并实际读取其中的验收 JSON。两次都绑定本页的合成合并提交和候选 tree，确认 `browser_mocked=false`、`HYBRID_RRF_RERANK`、来源核对、两个商品的实时详情／快照核对，以及 `cleanup_verified=true`。第二次与第三次日志中的上传 ID 各自对应其产物，没有仅凭相同文件名复用旧证据。

| 实际下载的 ZIP | SHA-256 |
|---|---|
| Admin Hybrid attempt 2 | `045c7c38c8fb1aeca293f69faf59655652a2e8016b8816c26335edf95655a3f6` |
| Admin Hybrid attempt 3 | `4f5f372284352a8e3c00fb6405e63fd9b2d8b9b396e9b979b9eef2938d6a963f` |

本轮没有改动运行代码，验收说明与导航属于后续文档提交。受测代码版本仍以本页精确 SHA 为准。当前结果可交由人工继续审查；PR #62 仍为草稿，未合并 `main`，未部署。门禁中真实 DeepSeek 效果、支付、迁移兼容及签名来源验证标志仍为 false。

下一阶段应根据交付用途完成升级／回退演练，并准备真实 DeepSeek 服务采样及独立人工评分。现有冷恢复仅覆盖同机同镜像的 BM25，范围见 [恢复指南](../demo-recovery-m14c.md)；真实模型评测入口见 [M2.1](../agent-effect-m21.md)。
