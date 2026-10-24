# M2.8：固定版本升级与备份回退演练

本阶段验证两个明确代码版本之间的数据读取、候选版本写入，以及使用升级前备份恢复旧应用的能力。入口只创建合成演示环境，不接受用户已有业务环境作为升级目标。

本页定义演练入口、支持范围和证据读取方法。具体提交是否通过，必须检查同一版本的 `Demo version upgrade` 实际运行及其 `evidence.json`。工作流定义、静态检查或单元测试通过，不能代替真实 Docker 演练；失败及未完成记录应保留。

## 版本与支持范围

旧版本固定为 [`e2a5310ec8a33f3078f796e8b7f2230c82fa2059`](https://github.com/volcano-code/ShopAgentStack/commit/e2a5310ec8a33f3078f796e8b7f2230c82fa2059)。候选版本使用执行时的干净 checkout，并记录精确 commit、tree 和实际运行文件摘要；候选必须与旧版本不同。

本阶段范围如下：

| 维度 | 范围 |
|---|---|
| 检索与模型 | BM25，模型出站关闭，不调用付费模型，不启用 fixture 代替模型 |
| 运行环境 | 同一 Docker Engine，独立随机项目、网络、五个持久化卷及回环端口 |
| MySQL | 两版初始化及迁移 SQL 相同；演练旧数据由候选应用继续读取和写入 |
| Agent SQLite | 候选的加法结构变化；另行验证旧→新→旧→新的真实 SQLite 兼容 |
| 基础服务 | 保留数据库、队列及其他基础镜像身份，不执行 MySQL、RabbitMQ 等软件版本升级 |
| 应用版本 | 分别构建两版 Java、Web 和 Agent；核对实际 JAR、Web 文件、Agent 镜像及源码摘要 |
| 全栈回退 | 从升级前加密备份恢复到第三个新环境，运行旧应用快照 |

普通 `backup` / `restore` 仍遵守 [M1.4c 恢复合同](demo-recovery-m14c.md)，包括相同恢复布局合同、原镜像、同 Engine 和新目标要求。本阶段不会把普通 `restore` 改为任意版本迁移工具。

## 实际演练过程

### 1. 旧应用写入并建立恢复点

使用旧 checkout 自己的构建和启动入口创建全新的 BM25 环境，通过 Java API 导入合成商品和政策、调整库存、建立客户及购物车，通过 Agent API 建立会话、偏好及假模型配置。模型配置只验证保存和解密，不调用连接测试或生成接口。

随后显式停止全部服务，检查数据库正常退出，创建并认证加密冷备。备份保存五个卷、原运行快照、账号配置、部署加密密钥及 RabbitMQ 节点身份。原始数据、凭据、密钥和归档均保留在私有目录。

### 2. 候选应用读取旧状态，再产生新状态

在独立新目标中载入旧数据和必要配置，应用运行快照切换到候选构建。启动后先验证旧账号登录、购物车、会话、偏好、密文配置、商品和政策数据，再通过真实 API 产生新的库存与偏好写入，并在重启后重新核对。

Java 服务使用基础镜像并只读挂载 JAR，Web 也挂载构建文件，因此“镜像 ID 相同”不能单独证明应用版本没有变化；“提交 SHA 不同”也不能单独证明实际运行了新程序。验收同时检查源码身份、运行文件摘要和容器实际挂载或镜像。

### 3. 恢复旧备份并验证旧时间点

停止候选环境后，通过原冷恢复入口将升级前备份恢复到第三个新项目，启动旧应用，重新验证业务数据及 Agent 状态。库存的关键观察顺序为：

| 观察点 | 商品 10001 的库存 | 含义 |
|---|---:|---|
| 旧版本建立备份前 | 179 | 通过旧版 Java API 完成的写入 |
| 候选版本写入后 | 178 | 新应用可以在旧数据上执行新的授权写入 |
| 从原备份回退后 | 179 | 恢复到升级前时间点 |

回退环境应恢复旧偏好与会话集合，候选阶段新增状态不应出现在该旧备份的恢复结果中。回退环境再次重启后仍须读回正确；原源环境也应保持可用，受检业务和 Agent 状态不变。

**全栈回退会舍弃备份之后的新写入。** 独立 SQLite 测试中的旧→新→旧→新兼容，不代表完整 Java、Agent、队列和数据库系统已经实现无损降级。本阶段也不承诺热升级、零停机、跨主机迁移、任意 MySQL schema 迁移、Hybrid/Milvus 恢复或生产级无损回退。

## 本地复现

需要 Ubuntu、可用的本机 Docker Engine 和 Compose、JDK 17、Maven、Node 24/npm、Python 3.12，以及足够同时保存两个构建、旧镜像和五卷备份的磁盘空间。使用干净的候选工作副本；首次准备时 `.local/upgrade-baseline` 和 `.local/upgrade-smoke` 均应不存在。

在候选仓库根目录执行：

```bash
python -m pip install -r tools/shop_quality/requirements-test.txt
git worktree add --detach .local/upgrade-baseline e2a5310ec8a33f3078f796e8b7f2230c82fa2059
(
  cd .local/upgrade-baseline
  python -m tools.shop_demo build
)
python -m tools.shop_demo build
python -m tools.shop_demo.upgrade_smoke --baseline-checkout .local/upgrade-baseline
```

入口将旧版源保存在 `.local/upgrade-baseline/.local/upgrade-source`，将升级及回退目标分别保存在 `.local/upgrade-smoke/upgrade` 和 `.local/upgrade-smoke/rollback`，并在 `finally` 中按本轮保存的归属进行清理。中断后可再次运行定向清理入口：

```bash
python -m tools.shop_demo.upgrade_smoke --cleanup-only --baseline-checkout .local/upgrade-baseline
```

清理只处理本轮归属的 Docker 资源，保留私有文件供诊断；不要以全局 Docker 清理代替它。已有演练目录、错误版本或不兼容输入应拒绝。保留首次失败证据后，在新的工作副本中重跑，避免覆盖原结果。

## 验收与公开产物

[`Demo version upgrade`](../.github/workflows/demo-upgrade.yml) 工作流依次检出固定基线和当前候选，执行相关测试，分别通过两个版本各自的 CLI 构建，再运行真实全栈演练。额外清理步骤使用 `if: always()`，确保运行失败后仍尝试清理本轮资源。

验收需同时核对：两个实际运行版本不同；升级前备份认证通过；五卷隔离与配置保留；候选读取旧状态并产生新写入；候选重启保持新状态；原备份恢复旧程序及旧时间点；原源的快照文件和受检数据未变；最终清理成功。任何步骤未完成，都不能将整轮写成成功。

工作流只上传以下两项：

- `.local/upgrade-smoke/artifacts/evidence.json`：允许公开的阶段结果、精确代码及运行摘要、统计和清理结论。
- `.local/upgrade-unit/TEST-upgrade.xml`：本轮限定范围的测试结果。

账号、令牌、部署密钥、加密备份、数据库文件和原始服务日志不属于公开产物。单元测试、真实 SQLite 兼容测试和全栈 Docker 演练应分别报告，不能相互替代。

## 证据字段与判断方式

使用工作流 run / job 绑定实际 checkout，然后读取同次产物。两个源码版本不同、各阶段检查完整以及最终清理成功，必须同时成立；不能仅凭某个前置步骤或旧运行的绿色状态宣布当前提交通过。

| 字段 | 核对内容 |
|---|---|
| `source_pair.baseline.sha` / `tree_sha` | 固定旧版提交及 tree；SHA 必须为本页指定基线 |
| `source_pair.candidate.sha` / `tree_sha` | 本次实际受测 checkout 及 tree，不能用后续文档提交替代 |
| `source_pair.mysql_schema_unchanged` / `backup_contract_unchanged` | SQL 及冷备布局合同一致的准入条件 |
| `runtime.baseline` / `candidate` / `rollback` | 三阶段快照、Java JAR、Web、Agent 源码和实际镜像摘要，以及挂载和运行代码核对结果 |
| `database.baseline` / `candidate` / `rollback` | 独立 MySQL 读回；库存分别为 179、178、179，其余已记录数据保持预期 |
| `live_sqlite` | 全栈启动后新增对象、既有结构不变、完整性检查及回退恢复旧结构 |
| `sqlite_probe.passed` / `checks` / `check_count` | 独立真实 SQLite 旧→新→旧→新的 13 项检查；不能替代全栈结果 |
| `archive_sha256` | 升级前加密恢复点的摘要；归档本身不公开 |
| `isolated_project_count` / `isolated_volume_count` | 三个独立项目、十五个独立卷 |
| `checks` / `status` / `upgrade_verified` / `checkpoint_rollback_verified` | 全部阶段检查完整，且整轮结论一致 |
| `cleanup_verified` / `cleanup_errors` | 清理是否完整；清理失败时整轮必须失败 |
| `failure_stage` / `failure_type` / `failure_locations` | 保留首次失败阶段和安全诊断，不替换成成功记录 |
| `full_stack_lossless_downgrade_verified` / `mysql_ddl_migration_verified` / `hybrid_recovery_verified` | 均应为 false，保持本轮已实现范围的边界 |

PR 记录和交付说明应引用具体运行链接及产物 SHA-256，注明真实成功、失败或未完成状态；本操作指南不预先承诺任何尚未执行的版本已通过。
