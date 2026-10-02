# M1.4a：可保留数据的本地演示入口

这个入口不是 E2E 运行器。`up` 成功后服务保持运行，`stop` 只停止本项目容器，保留
MySQL、Redis、RabbitMQ、MongoDB、Agent SQLite 等命名卷；下一次 `up` 复用原有凭据和数据。
只面向个人本机演示，不是生产安装器、迁移工具、公开 HTTPS 部署或高可用方案。

## 第一次使用

目标环境为 Linux、Python 3.12+、本机 Docker Engine/Compose、JDK 17/Maven、Node.js 24。
CI 使用 Ubuntu 24.04；其他桌面系统/WSL2 的权限与路径行为仍须单独实测。
需要为依赖、镜像、构建快照和可选公开 BGE 权重准备磁盘及下载网络。
Hybrid 还需承担向量模型/重排模型与 Milvus 的 CPU、内存开销，未做容量基准。

在仓库根目录执行：

```bash
python -m tools.shop_demo build
python -m tools.shop_demo init --retrieval bm25
python -m tools.shop_demo up
python -m tools.shop_demo status
```

浏览器访问 `http://127.0.0.1:18030`。客户使用页面注册；客服和管理员的用户名、随机初始密码
保存在 `.local/demo/accounts.json`，仅在本机查看，不粘贴到 issue、截图或对话中。
这里创建的是基础种子数据，并未自动导入 100 件扩展商品或发布 96 份政策。
不要直接对这个环境运行依赖旧 `.env`/账户文件的导入或故障测试脚本。

默认 **不启用 fixture，也不给 Agent 开放模型出站网络**。交易、客服和管理页面可以使用；
未配置的聊天模型返回明确错误，不会暗中改用脚本答案。
为了演示原有确定性编排，可在一个新的 state 中显式选择 `init --fixture`；界面仍标记非 AI 模型。
为了自行配置真实提供商，可在新的 state 中选择 `init --allow-model-network`，随后在个人模型设置中保存连接。
该选项只开放 Agent 出站网络，不填入任何平台 API Key，CLI 也不会执行连接测试或调用模型。
用户随后主动发起的真实连接测试/对话可能产生模型费用。
两种模式互斥；初始化后不能通过随手编辑 JSON 切换模式。

## 原生 Milvus 选项

```bash
python -m tools.shop_demo init --retrieval hybrid --state .local/demo-hybrid --port 18031
python -m tools.shop_demo up --state .local/demo-hybrid
```

`hybrid` 使用 M1.3c 的真实 Milvus 原生本地持久化、嵌入式 etcd 和 Woodpecker WAL，
无需 MinIO；模型仍为固定 revision 的 BGE-small-zh-v1.5 与 BGE-reranker-base。
第一次 `up` 在不挂业务密钥的准备容器中缓存公开模型，索引 worker 以只读、离线缓存运行。
仅 worker 的 `/health` 存活不足以宣称就绪：启动还等待已认证的 worker 状态与 Java 当前
政策 epoch、Outbox READY 和索引 generation 一致。索引仍是单 worker，不要自行扩容。
这不是 MinIO/S3 迁移；旧 `deploy/compose.retrieval.yml` 不被加载，旧数据卷也不会接管或删除。
本入口不开放 tracing 组合选项；此前独立 hybrid/trace E2E 不等于组合部署已验收。

## 日常启停与更新

```bash
python -m tools.shop_demo stop
python -m tools.shop_demo up
```

`stop` 不执行 `down --volumes`。每次操作检查项目/资源标签、私有输入哈希和已保存的数据卷
创建身份。首次启动先通过 Compose create 创建但不运行业务容器，记录卷身份后才启动服务。
即使首次健康检查失败，后续发现卷丢失或被重新创建也会拒绝启动，不会“修复”为一个空数据库。
在记录身份前被强制中断的部分环境不会被自动接管；保留文件与卷，先人工确认恢复需求。
端口冲突不会停止其他应用；为新的 state 指定空闲 loopback 端口。

`init` 将当前 Java jar、Web 构建及所需 Python/配置源码复制成私有运行快照。
在原 checkout 重新构建、编辑代码不会热替换一个正在演示的 jar 或服务；重复 `up`
也不会重置账号、重新导入种子、刷新迁移或轮换加密密钥。
因此，**拉取新代码不等于现有演示自动升级**。新代码先建立不同 state 和不同端口验证；
跨版本保留业务数据的升级、备份/恢复、迁移回滚属于后续工作，切勿手动改 manifest 绕过检查。
快照哈希用于错误/篡改检测，不是签名，也不能防御已经控制本机文件或 Docker 的攻击者。

`init` 拒绝覆盖已有（包括部分创建失败的）state；失败的 `up` 保留容器与私有日志用于定位，
不在 finally 中删除用户刚创建的数据。`.local/demo/logs` 不应公开上传。
正常命令结束会释放排他锁；进程被强杀后的 `command.lock` 只在确认没有同 state 命令存活后手工处理。
POSIX state 权限要求 0700，账户和环境文件要求 0600；供容器 UID 10001 直接挂载的 key 文件
为 0644，但其宿主父目录保持 0700。Docker 管理员依然有能力读取它们。
所有状态/启动/停止/销毁命令均拒绝远程 Docker endpoint；不读取根 `.env`，也清除继承的部署/Compose/Docker 环境变量。

## 显式销毁

仅当确认不再需要这个演示的数据库、会话与模型缓存时执行：

```bash
python -m tools.shop_demo status
# 把下面参数替换为 status 输出的完整 shop-demo-<随机串>，不是 yes
python -m tools.shop_demo destroy --confirm shop-demo-<完整随机串>
```

这会删除该项目的容器、网络和命名卷，其他项目及镜像不动。操作仍先校验资源归属。
源代码、私有快照/账号/日志目录不删除；state 标记为 destroyed，不能再次启动。
清除宿主私有目录须自行确认备份需求，绝不能把它当作可公开分发的源码包。

## 验收与范围

`python -m pytest tools/shop_demo/tests tools/shop_e2e/tests -q` 为配置和生命周期工具单测。
其中 Docker/HTTP 替身不算真实服务证明。`Local demo smoke` 的 bm25/hybrid 两组托管任务
分别创建全新环境，通过真实 Web 代理登录，创建合成购物车和 Agent 会话，保存仅用于加密
回读的假模型配置（不发起提供商请求），执行 stop/up 和重复 up，再核对登录、购物车、
会话/偏好、加密配置及卷身份保留，最后只销毁其自己的临时演示。
公开 artifact 仅含受限 evidence；原始响应、账户、密钥、Docker inspect 和日志不上传。
具体某个提交是否已通过，以该提交工作流和产物为准，不从本文推断。
