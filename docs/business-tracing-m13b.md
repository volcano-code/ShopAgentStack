# M1.3b：售后业务追踪（可选）

本阶段把 M1.3a 的指定售后场景与真实 Collector / Tempo 回读绑定。没有增加模型权限，
没有接入真实支付，不证明全部商城场景、Milvus 混合检索或生产容量。

## 两条实际请求链，而非伪造同一个 trace

客户发起请求：`http.request -> agent.run -> llm.call / tool.call -> mcp.request -> commerce.call -> commerce.request`。
客服独立审批：`commerce.request -> refund.publish -> refund.consume -> db.refund.transaction`。
审批是新的人类请求，有自己的 trace。浏览器保存两次响应的 `X-Trace-ID`，并用同一售后 case 的
独立数据库回读绑定它们；没有在 telemetry 中记录用户/订单 ID、审核内容或确认凭据。

`db.refund.transaction` 包住 `RefundService` 的 Spring 事务代理调用，调用返回时事务已经提交；
它不是逐条 SQL 的自动埋点。消费者 span 不覆盖 Spring 容器随后发出的 AMQP ACK。
本项目对单条消息采用显式父子关系，不宣称实现所有消息语义约定或批处理关联。

## 启用和隔离运行

先按 `docs/business-e2e-m13.md` 构建 Java/Web 并安装 Chromium。在干净 Git checkout 执行：

```bash
python -m tools.shop_e2e run --tracing
```

默认不带 `--tracing` 的 M1.3a 仍可运行。两种模式使用不同随机测试项目和自有资源标签，
不能在同一个状态目录重复运行；清理方式与 M1.3a 一致，失败也会清理。
新增 Collector/Tempo 仅连接测试内部网络，不发布宿主端口；仅 Web 随机 loopback 端口对宿主可见。
真实调用链使用 fixture 模型、BM25 和模拟退款，没有向付费模型发送请求。

普通部署的 Java 端默认关闭。启用时配置：

```text
SHOP_AGENT_STACK_OTEL_ENABLED=true
SHOP_AGENT_STACK_OTEL_SERVICE=shop-commerce-portal     # admin 改成 shop-commerce-admin
SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT=http://otel-collector:4318/v1/traces
SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO=0.1
SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL=true             # 仅隔离的 portal 内部 Agent 路径
```

与 Python 相同，endpoint 只能由部署者配置；禁止用户/模型动态提供。Java 只接受 HTTP(S)
`/v1/traces` endpoint，拒绝凭据、query、fragment。SDK 版本由既有 Spring Boot parent 管理。
关闭状态没有 exporter 或外部连通要求。Java tracer 不注册全局 SDK、不采集主机属性，批量队列
上限 512，单次导出超时 2 秒，导出不可达不参与业务事务。参数配置错误属于部署错误，会在启动时报错。

## Outbox、事务与降级边界

迁移 `011-refund-trace.sql` 只增加可选 `shop_agent_stack_refund_trace` 诊断表，可重复执行。
启用追踪时，审批在原业务事务内保存当前请求的 bounded W3C v00 traceparent；事务回滚时
诊断记录也回滚。诊断表不是业务意图、授权或支付账本。不存在旧记录、诊断表缺失或其读写失败时，
退款仍按原 Outbox 执行，追踪则退化为新根 trace，并记录固定告警，不掩盖业务数据库本身的失败。
诊断表可随相应售后归档删除；不会自动删除生产数据。人工重试沿用最初审批来源，后续重试操作
不是该 trace 的新授权。每次重投递创建独立发布/消费 span，业务幂等仍由 Java 和数据库保证。

## 信任与隐私

公网 Nginx 转发到三个 API 时删除 traceparent/tracestate/baggage。Java 对公共路由始终新建根；
只有显式开启信任的 `/shop_agent_stack/internal/agent/` 接收一个合法 v00 traceparent，其他输入
被丢弃，不改变原执行 grant 校验。MQ 只传播 traceparent，不传播用户 JWT、grant 或 baggage。
SDK 不捕获 URL、查询、请求体、模型全文、异常正文、SQL 或消息正文；Collector 再次执行白名单。
HTTP span 记录的是 HTTP 状态码，业务 `CommonResult.code` 可能不同，不把 HTTP 200 当业务成功。
本实现覆盖同步 Java servlet 路由，不声称覆盖 servlet async、所有 JDBC 或所有 MQ 业务。

## 验收

`.github/workflows/business-tracing-e2e.yml` 必须执行原八类 Java 回归及 `CommerceTracingTest`，
并要求后者产生非空成功报告。实际 E2E 仍要求全部三个 Playwright 用例、两个 MCP 用例、独立
数据库不变量和清理成功。新增验收要求两条 trace 的服务身份、完整父子关系、根 span、无错误、
无敏感属性，以及诊断表内审批来源 span 与浏览器响应一致。只拿到 OTLP HTTP 200 不算通过。
公开 `evidence.json` 只记录安全 span 名称/ID/父关系/服务名等摘要，不公开原始运行目录。
缺失 exporter/断链/错误 trace 或回读超时会拒绝本轮验收，但不修改或补造业务结果。

文档描述实现与验收合同。具体提交运行是否通过，以该提交的 Actions 与下载核对的 artifact 为准。
