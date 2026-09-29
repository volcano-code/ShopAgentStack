# M1.3c：同一隔离环境的真实混合检索与政策生命周期

这是集成验收入口，不是检索准确率 benchmark，也不是持续在线展示部署。
Java 发布权限、政策有效性、最终源核对与模型工具白名单保持不变。

## 范围

在 M1.3a 的独立 Docker 项目中增加 Milvus / etcd / MinIO 与单实例索引 worker。
使用 `evaluation/retrieval-matrix.json` 中已有的固定提交：BGE-small-zh-v1.5
与 BGE-reranker-base，在 CPU 上实际运行 embedding、Milvus dense search、BM25、
RRF 和 cross-encoder rerank。不使用 hash embedding、不模拟向量数据库、不替换排名。
测试有一个原有种子政策和四份自行创建的合成政策版本/草稿，用于验证工程不变量；
这不是公开评测集、独立 holdout 或准确率/召回率实验，不能宣传效果提升数字。

## 分离模型准备和离线业务网络

`model-prefetch` 是独立的准备 profile，只接外部 edge 网络，挂载代码、配置与本次
新建模型卷，不挂载索引密钥、账号、部署 `.env`、Docker socket 或数据库目录。
仅下载配置中固定 revision 的公开 BAAI 权重及 tokenizer/config，保留模型 README；
不下载 Python 远程代码和 pickle 权重，不使用 HF token，不分发模型权重到 Git。
准备过程生成文件 SHA256 manifest。约 1.2 GB 权重下载是一次性的本地 CPU 模型准备，
不是付费 LLM 调用。下载失败直接失败，不偷换成 fixture。

worker 在 business 内部网络中运行，`HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`，
缓存只读。Milvus/etcd/MinIO/worker 不发布宿主端口，不复用展示栈网络、集合或数据卷。
唯一的宿主业务端口仍是随机 loopback Web 端口。prefetch 与 Web 的 edge 网络有出站能力，
不声称整个 Docker 栈完全无外网。镜像版本沿用原项目，验收记录实际镜像 ID。

## 六个必需阶段

1. 发布客户 v1 与员工材料，保留客户草稿。要求真实 hybrid 方法及当前来源 hash；
   员工/草稿不可检索或取源，重复发布不增加 epoch，错误 epoch/digest 与无服务密钥被拒绝。
2. 停止本项目 worker，发布 v2。必须明确降级到当前 MySQL BM25，v1 不得检索或取源。
3. 恢复 worker。要求新索引 generation、v2 hybrid 命中以及索引 Outbox READY。
4. 停止本项目 Milvus。要求显式 BM25 降级，仍只能返回有效来源。
5. Milvus 停止期间撤回 v2 和员工材料。立即搜索和取源均不得泄露撤回版本。
6. 恢复 Milvus，等待重建与 READY。再做 hybrid 查询、独立 MySQL 和 Milvus 回读。

阶段执行有序且只执行一次；轮询只等待索引 READY，不重试失败的正确性断言。
两个故障注入块都在 finally 中恢复对应服务，恢复异常不得覆盖首个失败，结束时清理
自己的资源。外部强制终止仍需显式 cleanup；不能据此声称生产高可用。

最终独立回读比较 Java 当前 epoch、索引 Outbox generation、真实 Milvus 集合行数和
当前可见 MySQL 条款数。专用实验 sentinel 集合在重建前创建，验证 live generation
清理不误删它、搜索不选择它，且不留下旧 live 集合。测试不连接任何既有实验集合。
随后在同一环境运行既有两个 MCP 确认/归属用例和三个 Playwright 用例及退款数据库核查。
对话模型仍为显式 fixture，支付与退款仍是本地模拟，不证明真实 LLM 或真实资金流程。

## 使用

先按 `docs/business-e2e-m13.md` 构建 Java/Web 产物并安装 Chromium。准备干净 Git checkout、
本机 Docker、足够磁盘/内存以及模型下载网络后执行：

```bash
python -m tools.shop_e2e run --hybrid --state .local/hybrid-e2e
# 强制终止后的限定清理；不会删除其他 Compose 项目
python -m tools.shop_e2e cleanup --state .local/hybrid-e2e
```

默认不启用 tracing；`--hybrid --tracing` 可组合，但是否通过以该组合自己的实跑为准，
不能把分开运行的两个成功结果拼成一次全栈 tracing 证明。默认非 hybrid 运行不下载模型。

工作流为 `hybrid-retrieval-e2e.yml`。只上传 `.local/hybrid-e2e/artifacts/` 中有限汇总、
hash 和合成截图；中间 bearer、索引密钥、原始 HTTP 响应和模型文件不得发布。
`hybrid-progress.json` 保留已完成阶段，`evidence.json` 同时要求检索、业务和清理通过。
本文件说明实现契约，不预先宣称任何提交的 CI 已通过。

## 镜像来源修复

首轮托管测试在启动阶段发现原 `minio/minio` Docker Hub 镜像无法拉取。隔离栈改用
MinIO 自己的 `quay.io/minio/minio`，保持 `RELEASE.2024-12-18T13-15-44Z` 标签；
没有使用未知第三方镜像，也没有假装认证即可修复。此历史镜像仅用于无公网端口的
临时测试，不能据此视为当前安全受支持的生产部署选型。来源切换仍需本次实跑验收。
首个失败的 workflow 与 artifact 保留，不与后续成功混淆。
