# 本地诊断网络与验收边界

## 已观察到的问题

提交 `d43f3b591a6ca96127cbc9e38a0513f90e77ef30` 的托管观测栈检查（run `36459869141`）中，Collector 日志已报告 ready，三个容器处于 Up，但 `docker compose ps` 没有显示发布端口，宿主健康检查全部失败。该结果不是 OTLP 回读通过。

此前独立观测栈把唯一网络设为 `internal: true`，同时要求从宿主机访问发布端口。这与 Docker internal-only bridge 的端口发布限制冲突。

## 当前配置

`deploy/observability/compose.yaml` 使用专用标准 bridge。所有发布端口仍显式绑定 `127.0.0.1`，且 bridge 默认绑定地址也设为 `127.0.0.1`。Grafana 仍需随机生成的本地密码，匿名登录关闭；只读根文件系统、能力清空、内存限额及 Collector 数据过滤保持不变。没有改动商城业务 Compose 网络或 Agent/MCP 授权。

**这不是容器出站隔离。** 标准 bridge 允许容器通过宿主网络对外连接。本配置只面向本机合成数据诊断，不适合直接用作生产隔离、安全审计结论或公网 Demo。处理敏感数据时须另行配置出站防火墙或具有明确入口代理的隔离网络，不能只靠 loopback 绑定。Docker 28 之前的 loopback 发布还有同网段可达的历史问题，因此应使用受支持的更新版 Docker 并检查宿主防火墙。

## 必须分开的验收项

1. 容器进程启动、健康探测成功。
2. `collector_smoke` 发送 OTLP 后，从 Tempo 回读完整 span 树并验证隐私过滤。
3. 真实商城、MCP/Java/Milvus/RabbitMQ 与浏览器业务端到端。

前一项通过不能替代后一项。修正配置后的 hosted run 才能证明运行效果；本文件不会提前宣称修复已获运行验证。测试/源码哈希保存在每个工作流 artifact 中。

参考：Docker 官方 Port publishing and mapping、Networking in Compose，以及 Moby issue #36174（internal-only bridge 端口发布行为）。
