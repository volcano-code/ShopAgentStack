# 基线核对与环境隔离检查

[文档首页](README.md) · [测试](testing.md) · [本地演示](local-demo-m14.md) · [质量汇总](ci-required.md)

本文提供已有链路的核对入口和只读配置检查，不是新的模型评测平台，也不是生产认证。基线复核应固定 commit 和 Git tree；报告、CI run/attempt 和部署快照分别记录，源码合入不代表运行环境已经升级。当前导航以 `e2a5310ec8a33f3078f796e8b7f2230c82fa2059` 为起点；新改动需验证自己的源码树，不能继承旧版本的通过结论。

## 功能、实现与证据矩阵

以下为实现和验证入口，不是某一次执行的通过清单。每次核对应分别记录“源码可见”“已执行通过”“失败”“未执行”；运行记录放在 Git 忽略的本机目录。

| 链路 | 实现入口（仓库相对路径） | 验证入口 | 不能据此推断的能力 |
|---|---|---|---|
| 商品问答 | `services/agent/shop_agent_stack/runtime.py`、`mcp_server.py`；Java `ProductQueryService` | `services/agent/tests/`、Java `ProductQueryServiceTest`、商品对话题集 | 关键词查询不是向量推荐；引用检查不是语义正确率 |
| 政策问答 | `mcp_server.py`、`policy_gate.py`、`services/retrieval/online.py` | Hybrid retrieval E2E、`scripts/verify-p3c.ps1`、检索对照 | 工程分支测试不等于真实模型判断准确 |
| 售后确认 | `services/agent/shop_agent_stack/app.py`；Java `AgentOperationTest` 对应业务 | Business E2E、Agent 契约与确认回归 | 生成预览不是提交申请；申请不是批准退款 |
| 退款处理 | Java `RefundService.java`、`RefundWorker.java` | Business E2E 独立数据库读回、`scripts/verify-p4.ps1` | 本地模拟账本不是外部支付网关 |
| 库存与权限 | Java `CatalogManagementService.java`；Portal/Admin 业务入口 | Java `CatalogManagementServiceTest`、`OrderOwnershipTest`、`SupportServiceTest` | 不涵盖任意直接 SQL 或所有上游旧入口 |
| 会话与事件 | `store.py`、`history_pages.py`、`app.py`、`apps/web/src/AgentWorkspace.tsx` | Workspace experience、Agent SQLite/API 测试 | 运行数分页不保证单次事件字节有界；读回不应执行写操作 |
| 观测与交付 | `observability.py`、`tools/shop_demo/` | 观测工作流、Local demo smoke、Demo cold recovery | trace 采样不是完整业务审计；冷备不等于跨版本灾备 |

核对时将业务权威、用户归属、显式确认、库存锁顺序、模拟退款幂等、政策版本与来源复核作为必须保持的不变量。不得为通过测试放开旧支付入口、跳过确认或自动修复账本。

## 只读检查多个本地演示快照

新入口直接复用 `tools.shop_demo.state.load` 验证快照归属、文件清单摘要和生成配置，随后比较声明的资源。它不会调用 Docker、创建 Agent Store、打开业务数据库、初始化环境或请求模型。摘要验证会读取已绑定文件（包括私有配置）计算哈希，但不解析或输出账户密码与模型密钥，也不写这些文件。

在仓库根目录，对**已经通过原有 init 入口创建**的两个本地演示快照运行：

```bash
python -m tools.shop_demo.isolation \
  --state .local/demo \
  --state .local/evaluation
```

路径仅为示例。不存在的状态会拒绝，不会自动创建。支持同一 checkout 下 2—8 个 `shop-local-demo-v1` 快照；旧 PowerShell 环境和 disposable E2E 状态不是此格式，不会被当成合法演示快照。

返回仅包含固定字段、拒绝码和 `--state` 的 1 起始位置，不输出本机路径、项目名、配置正文或原始异常。退出码 0 表示选中的配置中未发现受检冲突；退出码 2 表示冲突或无法完成验证。

| 拒绝码 | 含义 |
|---|---|
| `STATE_COUNT_INVALID` | 少于 2 个或多于 8 个状态 |
| `STATE_INVALID` / `STATE_BUSY` | 状态缺失、损坏、归属或格式不符，或存在生命周期操作锁 |
| `STATE_PATH_OVERLAP` / `PROJECT_REUSED` | 状态目录相同或嵌套，或复用项目标识 |
| `VOLUME_REUSED` / `NETWORK_REUSED` | 声明的最终卷名或网络名重复，包括显式资源名 |
| `PUBLISHED_PORT_OVERLAP` | 固定发布端口和协议重复；不同 host IP 也保守报告 |
| `WRITABLE_BIND_OVERLAP` | 两环境的 bind 路径相同或父子重叠，且至少一侧可写 |
| `CROSS_STATE_BIND` | 一个环境挂载了另一个环境的私有状态，即使只读也拒绝 |

共享只读代码或缓存不自动算冲突，但跨私有状态访问仍拒绝。无法解释的存储驱动、挂载和端口表达式不静默忽略。纯比较函数不能替代入口的快照验证。

**`runtime_verified` 始终为 false。** 配置检查没有枚举实际容器、外来卷挂载、实际端口占用、Docker context、进程、数据库或模型账户；不证明凭据互异、不识别所有硬链接或远程存储别名，也不保证检查之后没有并发修改。它不是恶意输入沙箱、一般 Compose 校验器或破坏性操作授权。

命令不获取生命周期写锁，也不更新 manifest。请在 init/up/stop/restore 等命令结束后执行；存在锁时只报告忙，不移除锁。实际运行隔离仍需在操作者本机验证，并避免用 `-p`、`COMPOSE_PROJECT_NAME` 或额外 Compose 文件绕过原启动器的配置。

## 验收与交付边界

```bash
python -m pytest tools/shop_demo/tests/test_isolation.py -q
```

新增测试放在现有 `tools/shop_demo/tests/`，由 Local demo smoke 的原测试步骤收集，不新增工作流或模型调用。单元测试使用合成快照和占位构建文件，验证配置与文件行为，不启动 Java 或证明浏览器、Docker、真实模型成功。

先运行这项检查，再分别使用原有启动、状态及隔离业务验收入口核对本机环境。保留第一次失败和重跑记录；没有 Docker 或模型预算时明确写“未执行”，不把缺失证据写成通过。

恢复范围仍以 [冷备指南](demo-recovery-m14c.md) 为准；本命令不增加 Hybrid/跨机/跨版本备份能力。公开交付只含检查过的摘要，不上传 `.env`、运行数据库、备份或原始模型输出。

实现依据：[Compose 项目名](https://docs.docker.com/compose/how-tos/project-name/) 与 [卷名称及 external 语义](https://docs.docker.com/reference/compose-file/volumes/)。显式卷名不会自动带项目名前缀，项目名本身也不能证明实际资源完全隔离。
