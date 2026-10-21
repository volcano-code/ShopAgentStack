## M2.3 故障注入和受限并发负载验收

`python -m shop_agent_stack.admission_bench --requests 40 --workers 12
--owners distinct` 会在**临时 SQLite 文件**中创建合成会话，
并使用线程池模拟同时创建运行。用 `--owners same` 测试单账号限额。
脚本内断言成功数、429 拒绝数与数据库实际队列数一致，
输出 p50/p95（最近秩定义）和样本/环境范围。**这只是本地 SQLite
事务锁路径的受控实验，不测真实 HTTP、模型、MySQL、Redis、
服务吞吐或生产峰值容量。**

`tests/test_failure_matrix_m23.py` 覆盖：Java 接受确认但 MCP
结果丢失时进入 UNCERTAIN，用户无法重复提交，重新查询 Java
后仅补一条业务结果；取消接口响应超时进入待核实，不重复取消，
通过权威操作状态恢复。账号越权需要拒绝；原始异常内容不写事件。
这些是 Java/MCP 合成故障，不是独立真实数据库读回；真实
Business E2E 工作流仍需要单独通过。


### 真实 MCP / Java / MySQL 受控回执丢失测试

Business E2E 流水线现在额外执行
`test_real_mcp_lost_ack_replay_has_single_pending_case`：
用**隔离的合成用户和订单**连接真实 MCP/Java/MySQL，
用户显式确认后成功提交申请，但测试故意在调用方丢弃已返回的成功回执。
随后通过**新 MCP 会话**以原 operation ID 重试，
要求仍返回同一 case ID，且当前客户仅有一笔售后记录。
测试写入只含合成 order/operation/case ID 的收据，由 CI runner
独立使用 MySQL 查询核验：仅一笔申请、已消费的原操作、
尚未发生审批、退款流水、退款任务或异步完成状态。
该结果独立于浏览器页面的成功文案。

这是**应用侧成功回执丢失注入**，并非真实网络代理故障；
仍不能证明任意下游数据库故障、外部支付或多进程故障下的安全性。
原有 Business E2E/Tracing/Hybrid 工作流必须基于新提交独立通过。
