# M1.3a：隔离的售后业务端到端测试

此入口使用真实前端、Chromium、Agent、MCP、Java、MySQL、Redis、MongoDB 和 RabbitMQ。
模型仅使用显式启用的 `fixture`（非 AI），支付和退款仅使用原有本地模拟器。
本阶段明确使用 BM25；不启动 Milvus 或下载检索模型，不证明混合检索、分布式追踪或真实模型质量。

## 执行

在仓库根目录，准备本机默认 Docker context、JDK 17、Maven、Node 24 和 Python 3.12：

```bash
mvn -B -ntp -f services/commerce/pom.xml -Ddocker.skip=true -DskipTests=false \
  '-Dtest=OrderOwnershipTest,CartPricingTest,AgentOperationTest,RefundServiceTest,ProductQueryServiceTest,SupportServiceTest,CatalogManagementServiceTest,FulfillmentServiceTest' \
  -Dsurefire.failIfNoSpecifiedTests=false -pl mall-portal,mall-admin -am clean verify
npm --prefix apps/web ci
npm --prefix apps/web run build
(cd apps/web && npm exec --no -- playwright install --with-deps chromium)
python -m tools.shop_e2e run
```

入口拒绝覆盖已存在的 `.local/business-e2e`。重跑可使用新的状态目录：
`python -m tools.shop_e2e run --state .local/business-e2e-second`。
未准备应用构建产物时会直接拒绝，不会停止本地展示服务来重新构建。
验收要求 Git 工作区干净，避免把未提交的实现错误归因于 HEAD 提交。

## 隔离与清理

每次随机生成 `shop-e2e-<128-bit nonce>` 项目、新凭据、独立账号文件、密钥与数据卷。
不读取根目录 `.env` 或 `.local/p1-accounts.json`，清除继承的部署和 Compose 环境变量，
显式使用 `docker --context default`，不接受远端 `DOCKER_HOST`。
宿主只发布随机 loopback Web 端口；Java、Agent、MCP 和数据库只连接内部 business 网络。
Nginx 另连 edge 网络以支持 loopback 端口，故不把整个栈称为完全无出站网络。
初始 schema、合成种子及全部迁移按固定顺序复制到新库初始化目录，不修改任何现存数据库。

正常结束和失败均清理自己的项目及卷。清理前校验本地所有权文件、Compose 内容及每个
容器/网络/卷的测试标签，遇到外来资源拒绝删除。外部强制终止后可执行：
`python -m tools.shop_e2e cleanup --state .local/business-e2e`。
清理不删除源码、其他项目、镜像或测试证据。示例配置的版本标签沿用现有项目；证据记录
实际镜像 ID，不声称版本标签不可变或等价于供应链安全审计。

## 实际验收边界

浏览器测试从客户真实登录与 Agent 预览开始，经页面刷新、显式确认、确认重放、SSE 游标回读，
到客服真实登录、领取、人工批准和异步模拟退款，再查询客户售后页面与只读对账。
另测伪造确认、他人会话/确认访问、取消后的迟到确认，以及未配置模型不得回退。
独立执行原有两项真实 MCP 协议/业务集成用例，不运行付费模型或故障停止脚本。

浏览器必须三个指定用例全部执行成功，MCP 必须两个指定用例全部执行成功。
缺失、重复、意外用例、失败、跳过均拒绝；浏览器不自动重试来掩盖首次失败。
页面成功仍不足以通过：再次通过 MySQL 查询核对订单、售后、操作消费、退款 Outbox、
模拟账本及事件数量。正例只能有一笔同金额退款；取消例不得产生售后、退款任务或账本。
这里的独立回读是独立于页面/API 状态的数据库核查，不是独立团队验证或支付审计。

`.local/business-e2e/artifacts/evidence.json` 记录范围、实际 checkout/tree、输入哈希、
镜像 ID、用例摘要、数据库不变量和清理状态。截图只展示合成退款结果。
原始测试报告、运行输出和凭据留在权限受限的本地状态目录，禁止上传原始目录；
公开工作流仅保存 `artifacts/`；失败时保留有限脱敏诊断摘录，删除生成密钥、Bearer 和长随机凭据。源码 snapshot 是来源记录，不是 E2E 通过证据。
此文件描述测试契约；具体提交是否通过，以该提交的工作流和证据为准。
