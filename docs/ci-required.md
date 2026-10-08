# 质量汇总与稳定版本检查

[文档首页](README.md) · [测试说明](testing.md) · [发布边界](public-release.md)

## 四组前置任务必须全部存在并成功

`Shop quality required` 使用 `tools/shop_quality/ci_gate.py` 处理 GitHub 的 `needs` 上下文。
固定要求 `quality-tooling`、`web`、`commerce`、`agent` 四组，不能只检查“返回的任务是否全成功”。
任意缺组、多出未知组、失败、取消、跳过、未知结果、JSON重复键、格式错误或超过输入上限均拒绝。
矩阵由 GitHub 聚合到任务组；一个组成功不是一次独立业务场景，也不能把重跑或矩阵重复计数。

保留 `if: always()`：前置任务失败后仍运行门禁。门禁和证据上传均不使用 `continue-on-error`。
现有 JUnit 非零测试门禁、Java 八类定向回归、Agent 追踪开关与 Python 双版本矩阵均不变。

## 可回读的判定

汇总任务生成 `required.json` / `required.md` 并写入 Actions Step Summary。
上传产物名是 `quality-required-<run_attempt>`，保留14天；缺少产物或上传失败仍是工作流失败。
记录仓库、run ID、attempt、触发事件、PR head、实际 checkout commit 与 Git tree。
实际 checkout 必须等于事件 SHA；PR 合并预览 SHA 可以不同于开发分支 head SHA。
`push` 和手动运行中，head 必须与事件 SHA 相同。

报告只包含固定字段、任务组名、允许的状态和固定拒绝码，不保存 needs outputs、模型正文、
原始异常、账户、密钥或业务数据。测试只使用合成 JSON，不调用模型和 GitHub 写接口。
本地报告不是平台签名，也不能抵御有权修改工作流的攻击者。

## 如何处理“总失败、子任务成功”

先查看同一 run 的 attempt 与完整 jobs 列表，核对汇总任务是否真的创建/执行；再读该任务日志。
缺少汇总任务时，不能根据其余子任务成功手工算成全绿。可按 GitHub 提供的重跑功能重跑，
仍须读回最新 attempt 的结论与产物；没有证据时保留为未确认，不猜测调度或平台故障原因。
历史失败、后续重跑与新的代码提交是不同记录，不改写旧证据。

原始 e440470 的汇总曾缺失；后续同一提交的重跑已执行汇总成功。本次代码加固解决的是
“缺少组仍可能被判成功”的独立契约缺口，不声称定位或修复了先前的平台调度原因。
具体运行日期与结果保存在 PR/Actions，不将历史结果充当新提交测试结果。

## 合入主分支的检查范围

质量汇总仅涵盖上述四组，不自动证明下列独立工作流完成：Business E2E、Business tracing E2E、
Hybrid retrieval E2E、Local demo smoke、Demo seed import、Demo cold recovery 以及观测传输检查。
审查者应逐一核对当前 head 的相关工作流、关键产物和源码树，而不是只看一个绿色徽章。
存在未解决的失败、冲突或新增提交时不合并。合并使用预期 head SHA，不强推、不忽略保护规则。

主分支完整源码和演示可用性不代表生产认证：真实生成模型质量、全量浏览器覆盖、多实例恢复、
Hybrid/跨机备份、跨版本迁移与公网HTTPS仍有独立验收要求。不要上传备份/密钥作为CI证据。

## 验证入口与依据

```bash
python -m pytest tools/shop_quality/tests/test_ci_gate.py tools/shop_quality/tests/test_workflow.py -q
```

- [GitHub needs 上下文](https://docs.github.com/zh/actions/reference/workflows-and-actions/contexts#needs-上下文)
- [GitHub 重跑工作流和作业](https://docs.github.com/zh/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)

单元测试覆盖真实判定函数和CLI的退出码/产物，包括旧判定对缺组输入误放行的复现。
它们验证门禁行为，不是生成模型或商城端到端的成功率。
