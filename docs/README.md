# 工程文档

ShopAgentStack 的产品能力、运行方式、服务边界和测试方法。

| 主题 | 内容 |
|---|---|
| [产品展示](showcase.md) | 三端功能、实际操作与素材规范 |
| [持久化本地演示](local-demo-m14.md) | 安全初始化、原生 Milvus 选项、启停与数据保留 |
| [安装与首次运行](getting-started.md) | 环境、构建、迁移、导入、账户与模型连接 |
| [系统架构](architecture.md) | 服务职责、权威数据、交易与异步任务 |
| [Agent 与检索](agent.md) | 工具循环、MCP、上下文、证据子图与来源复核 |
| [API 导航](api.md) | 接口分区、授权、错误及重试约定 |
| [运维指南](operations.md) | 配置、启停、排障与恢复 |
| [数据与测试](testing.md) | 数据规模、测试入口与指标口径 |
| [基线核对与环境隔离](baseline-verification.md) | 核心链路验证矩阵、只读快照比较与未验证边界 |
| [M2.1 真实 Agent 效果观察](agent-effect-m21.md) | 真实服务 HTTP 采样、安全预检、付费调用授权和独立评分边界 |
| [M2.3 执行准入和启动恢复](agent-execution-governance-m23.md) | 单实例并发准入、QUEUED 启动失败与回归限制 |
| [M2.4 权威政策快照计算缓存](m24-policy-cache.md) | 版本化 BM25 构建复用、内容与日期失效、安全界限和 CPU 基准 |
| [M2.5 真实 Hybrid 并发](m25-hybrid-concurrency.md) | 1/2/4/8 并发、显式降级及小样本性能边界 |
| [M2.6 商品比较](m26-product-comparison.md) | 实时 Java 商品详情、授权及最终引用复核 |
| [M2.7 候选发布审查](m27-release-review.md) | 十组精确版本 CI 与人工审查边界 |
| [M2.8 应用升级与备份回退](demo-upgrade-m28.md) | 固定新旧版本、真实数据接续、SQLite 兼容及旧时间点恢复 |
| [M2 候选版本验证](integration/m2-candidate-validation.md) | PR #62 三次独立 Hybrid 运行、真实产物与接续范围 |
| [工程实现与归属](engineering-guide.md) | 上游边界、增量模块和源码导航 |
| [代码结构与维护](maintainability.md) | 关键不变量、注释约定及维护约束 |
| [发布规范](public-release.md) | 许可证、素材、凭据和版本交付检查 |


[贡献指南](../CONTRIBUTING.md) · [安全说明](../SECURITY.md) · [许可证](../LICENSE) · [第三方来源](../THIRD_PARTY_NOTICES.md)

- [M1.4c1 停机备份与全新 BM25 环境还原](demo-recovery-m14c.md)：加密、同机同镜像，按提交验收；非生产热备份。

- [质量汇总与稳定版本检查](ci-required.md)：精确前置任务集、源码/重跑绑定与合入边界。
