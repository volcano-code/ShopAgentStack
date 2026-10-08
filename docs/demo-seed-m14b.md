# M1.4b：预览、确认与合成数据导入

这是可选的本机演示管理功能，不是 Agent 工具、支付功能或生产数据迁移。
沿用仓库公开合成数据：100 件商品、100 张配图、10 个分类、96 份政策（80 客户、16 员工）。
导入不调用生成模型，不上传权重，不读取旧部署 `.env`，不运行旧的直接写 SQL 脚本。

## 新演示环境

已有 M1.4a 环境的快照和数据不会自动升级。先保留原环境，使用新目录和不同端口：

```bash
python -m tools.shop_demo build
python -m tools.shop_demo init --state .local/demo-seeded --port 18031 --retrieval bm25 --enable-seed-import
python -m tools.shop_demo up --state .local/demo-seeded
python -m tools.shop_demo seed-preview --state .local/demo-seeded
```

`--retrieval hybrid` 可选。默认关闭导入 API，仅明确开启的 admin 服务注册它。
默认模型与 fixture 仍关闭。不要修改已有 owner/manifest/compose 文件绕过开关。

## 两次不同的明确确认

`seed-preview` 将完整商品/政策内容、服务器前置条件、过期时间与确认摘要写入该演示目录
`imports/<preview-id>.json`。它只保存预览审计，不新增商品、政策或条款。
检查该文件，复制返回的两个值执行（不要把尖括号当实际参数）：

```bash
python -m tools.shop_demo seed-apply --state .local/demo-seeded --preview <preview-id> --confirm <confirmation>
```

首次 SEED 确认会在一个 Java 事务内创建可售的合成商品及 **DRAFT 政策**。
不会自动发布政策，草稿不能作为客户检索证据。
明确审核政策内容及 STAFF/CUSTOMER 可见性后，单独申请发布预览，再确认新预览：

```bash
python -m tools.shop_demo seed-preview --state .local/demo-seeded --publish-policies
python -m tools.shop_demo seed-apply --state .local/demo-seeded --preview <new-preview-id> --confirm <new-confirmation>
```

发布仍经过原 PolicyService、条款生成和 index Outbox。员工政策不会进入客户可见索引。
预览十分钟过期，绑定创建管理员、动作、完整载荷及相关数据库状态。
API 每次重新查询管理员权限，客服或其他管理员不能消费该预览。
完整确认摘要不是授权令牌：缺少当前管理员身份仍然拒绝。

## 重试、冲突和失败

同一已提交预览再次确认只返回已保存回执；不会重新导入或发布。
相同 bundle 的新 SEED 预览明确返回 ALREADY_SEEDED，确认是零新增，不重置库存、销量、价格
或人为上下架。它不是修复或恢复：删除商品不会被自动补回。
不同 bundle、固定商品 ID/SN/SKU/品牌分类碰撞、同标题政策碰撞均拒绝；不接管旧 SQL 导入器的数据。
政策修改、修订或撤回后拒绝批量发布，必须通过已有政策管理逐项处理，不自动恢复旧承诺。

业务写入、映射与回执同事务提交。超时或断网不自动重试写操作；先查询同一预览状态，
再使用原预览确认获取回执。导入客户端拒绝 HTTP 重定向且不使用环境代理。
本机互斥锁防止 CLI 生命周期命令并发，数据库行锁处理跨进程的同时确认。
这不是恶意本机/Docker 管理员的安全隔离，也不是备份/还原或跨版本升级工具。

## 验收

`DemoImportTest` 检查 H2 真实事务中的回滚、竞争、过期、归属和幂等；其中政策发布器为
明确的组件替身，真实 MySQL 发布行为由 `Demo seed import` 工作流验证。
该工作流用两种独立的全新 BM25/Hybrid 演示环境，通过真实 Java HTTP API 导入，
另用只读 MySQL 查询检查数量、草稿/发布、条款及 epoch，正常调减库存后确认再次导入不重置，
再 stop/up 验证持久性。只上传脱敏 evidence 与 JUnit；不上传账号、计划正文、SQL dump。

本页描述代码合同；具体提交是否通过，以该提交的 CI 与 evidence 为准。
**备份、还原与生产恢复尚未实现/验收，不能据此删除原演示数据。**
