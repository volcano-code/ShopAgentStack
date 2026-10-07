# M1.4c1：加密停机备份与全新 BM25 环境还原

此入口用于本机演示数据恢复，不是生产热备份、跨版本迁移或支付系统灾备。
首版明确只支持 **BM25、模型出站网络关闭、同一 Docker Engine、相同镜像 ID**。
Hybrid/Milvus、远程主机和版本升级会拒绝，不能据此删除旧环境或宣称全部数据恢复能力。
普通备份和还原命令不自动停机、不自动启动、不自动删除源或目标。

## 备份内容和安全边界

保存五个命名卷：MySQL、Agent SQLite、RabbitMQ、Redis、MongoDB；同时保存当前演示
manifest 所绑定的 Java/Web/Agent 运行快照、初始化 SQL、账号配置和部署加密密钥。
不导出 Docker 镜像本身，不复制源码之外的日志、CLI 旧审核文件或模型缓存。
Java 数据库中的导入计划和回执随数据卷保留，但旧 CLI 审核文件不作为可执行确认迁入。
唯一忽略的卷内项目是 MySQL 数据目录的临时 `mysql.sock` 链接/套接字，启动时由 MySQL 重建。
其他链接、设备和套接字拒绝备份，不能静默漏掉不支持的数据。

外层使用 cryptography 的 AES-256-GCM 流式认证加密；每次使用随机 nonce。
恢复前必须认证整个密文，认证完成前不解包、不创建目标容器。密钥错误、篡改或截断会拒绝。
文件清单进一步校验路径、大小、摘要和模式；卷还原后再次读取所有文件字节、UID/GID及模式。
密文并非数字签名；只接受自己可信来源的备份。备份包含可执行程序和数据库，拥有密钥并能替换
备份的人仍可能替换应用，不能把它当作不可信程序沙箱。上限为8GiB归档/展开数据、10万条目。

私有临时明文在操作完成或异常后删除，但不保证磁盘安全擦除；强制断电/SIGKILL可能留下
权限0700目录，应按敏感数据保护。推荐使用加密磁盘。不要把密钥、备份包、SQL、原始运行日志
上传 GitHub 或聊天。密钥丢失无法解密；密钥副本与备份应分开保管。

## 执行步骤

在仓库根目录，先安装可选依赖、准备两个私有目录：

```bash
python -m pip install -r tools/shop_demo/requirements-backup.txt
mkdir -p .local/recovery-keys .local/backups
chmod 700 .local/recovery-keys .local/backups
python -m tools.shop_demo backup-keygen --key-file .local/recovery-keys/demo.key
```

对已经启动过、完成写入的 BM25 演示环境，先明确停止：

```bash
python -m tools.shop_demo stop --state .local/demo-seeded
python -m tools.shop_demo backup --state .local/demo-seeded \
  --output .local/backups/demo.enc --key-file .local/recovery-keys/demo.key
python -m tools.shop_demo backup-inspect \
  --archive .local/backups/demo.enc --key-file .local/recovery-keys/demo.key
```

检查返回摘要，选择不存在的新目录以及不同端口，手工填入完整64位 SHA256：

```bash
python -m tools.shop_demo restore --state .local/demo-restored --port 18032 \
  --archive .local/backups/demo.enc --key-file .local/recovery-keys/demo.key \
  --confirm <完整archive_sha256>
python -m tools.shop_demo up --state .local/demo-restored
```

`backup-inspect` 只证明加密包/文件摘要/布局合同可读取，不是业务恢复成功。
`restore` 的成功只证明五个卷已复制并按文件核验；目标仍停止，`application_verified=false`。
启动后仍需检查商品、政策、会话、加密配置和待处理业务。恢复的管理员账号、JWT密钥、个人模型
配置与源一致，不会偷偷重置；恢复实例仅用于隔离本地演练，不能直接用作公网克隆环境。

## 生命周期限制

源必须显式处于 stopped，所有服务均已退出；数据库退出码非零、OOM、卷丢失或有外来容器
挂载都会拒绝。应用锁只协调本 CLI，不能防止 Docker 管理员同时操作卷。源卷只读挂载给
无网络 helper；无 Docker socket。备份会增加私有命令日志，不会写源业务数据库。

还原创建新随机项目、私有路径、数据卷和网络。保留 RabbitMQ 原 hostname（节点名）及全部
服务原始 image ID，禁止拉取或构建替代镜像。`recovery_pending=true` 期间不能 `up`。
中断还原不自动续传或修复，不自动删除部分目标；保留私有诊断后，只能明确销毁该目标再换新
目录重试。没有 manifest 的预检失败目录只包含本机临时诊断，没有创建业务资源。

同一 Docker Engine 上仍须保留源镜像，清理镜像后不能凭标签猜测原版本。恢复工具布局合同
变化也会拒绝旧包；不是长期归档格式兼容承诺。物理冷备包含队列，不保证任意时刻的跨数据库
全局事务原子快照，也不证明带未完成退款/Agent任务的故障恢复语义或 exactly-once 消费。

## 验收

`Demo cold recovery` 工作流使用独立新建的合成 BM25 源和目标：导入100件商品/96政策，调整
库存，创建购物车、会话、偏好和假模型配置；拒绝运行中的源，停机加密备份，验证错误密钥/
错误确认/已有目标拒绝，恢复五个卷，启动新实例并通过真实 HTTP 和独立 MySQL SELECT 核对
数据；再次重启目标并验证原源数据仍在。只上传允许的 evidence JSON 和 JUnit。
新入口的当前提交是否真实通过，必须查看同一提交的该工作流结果，不能沿用上一版本CI绿灯。

技术依据：Docker volumes 文档的停机卷复制；RabbitMQ 3.13 Backup and Restore 的同节点名
要求；cryptography GCM 文档的认证完成前禁止使用明文规则。这里没有外部支付或付费模型调用。
