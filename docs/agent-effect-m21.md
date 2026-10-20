# M2.1 第一增量：真实 Agent 服务效果观察入口

本项建立一个严控的真实服务 **HTTP 观察器**，而非事后自称模型准确率的测试。
既有 scripts/evaluate-agent.py 即使配置真实模型仍采用合成 MCP 工具，
不能代表真实 Java/MySQL 业务链路。tools.shop_quality 已有独立评测器，
但回答正确性仍必须由外部人工/可信业务判定赋值。

## 预检（默认，不发任何网络或模型请求）

在仓库根目录：

    python -m tools.shop_quality.m21_real_service --scenario dev-reference

读取 evaluation/shopping-dialogues-v1.json 中已公开的 **dev** 样本，
校验题集、估算本轮最大模型请求数量、输出数据集 SHA256。明确拒绝
reserved 样本，不能将其称为未见过的独立 holdout。默认无网络调用，
不修改模型配置、会话、订单或用户账户。

## 显式真实模型调用（仅隔离的合成服务，需另行授权）

以下命令是操作文档，**本次开发及 CI 并未执行真实模型调用**。
在独立 Compose 服务环境准备好合成账号和个人模型配置后，用户自行
验证隔离条件，设置敏感 Bearer 到环境变量，并双重明确授权：

    export SHOP_AGENT_STACK_M21_BEARER="<isolated-synthetic-customer-bearer>"
    export SHOP_AGENT_STACK_M21_PAID_OPT_IN="I_AUTHORIZE_PAID_MODEL_REQUESTS"
    python -m tools.shop_quality.m21_real_service \
      --scenario dev-reference \
      --execute-live --provider custom \
      --endpoint http://127.0.0.1:18136/api/agent \
      --consent I_AUTHORIZE_PAID_MODEL_REQUESTS \
      --isolated-ack I_VERIFIED_A_DISPOSABLE_STACK \
      --max-model-attempts 24 \
      --out .local/m21-observations.json

真实调用仅接受 **127.0.0.1 HTTP**，拒绝跳转、代理、远端 URL、敏感
账户字段写入报告；要求双重付费授权及隔离环境确认。最多选择三个
dev 场景、不超过六轮，每轮后端最多准入八次模型请求。整体调用次数
上限是预检中已知的最大可能值，不是用户账单或输入 token 的硬上限。

观察器只调用 POST /sessions、POST /sessions/{sid}/runs 以及只读状态、
事件分页和用量接口，不会确认售后、取消订单、调用退款或重发超时的
业务请求。任何待确认、失败或结果不明的状态都停止本场景的后续轮次。

## 输出与严格结论边界

输出仅保留场景 ID、运行 ID、状态、已观察工具名称、引用 ID、回答
内容的 SHA256、分页结果、单轮耗时和供应商用量摘要。原始提示、
用户账号、模型回答、确认令牌和 API Key **不写入结果**。
输出文件强制为 0600，拒绝覆盖已有报告。报告始终标记：

- answer_accuracy = null
- task_success_rate = null
- business_write_safety_verified = false
- server_git_sha_verified = false
- model_cost_verified = false

原因：真实模型回答是否符合 rubric、引用是否支持结论、以及 Java
写入是否完成，不由模型生成的文本和 SSE 事件本身证明。下一项工作
应接入独立人工评分与 Java/MySQL 读回，并将 dev 与冻结候选样本分开
统计。只有满足这些证据前提才可发表真实回答效果结论。

本轮 CI 使用注入的合成 HTTP 响应，验证默认零网络、独立授权、
分页读取、无重复 POST、未知用量、不保存原始答案及敏感信息、
待确认的保护。CI 通过 **不代表真实模型测试已经运行**。

## 第二增量：独立人工复核结果

执行真实 Agent 服务采样后，首先生成不含实际回答的人工复核模板：

    python -m tools.shop_quality.m21_review \
      --observations .local/m21-observations.json \
      --scenario dev-reference \
      --template .local/m21-review-template.jsonl

复核人员在隔离的客户工作区查看实际最终回答，并对照题集中的 dev
rubric 填写每个 run ID 的独立评分。填写 completed JSONL 时，每行
只保留字段：case_id、turn、run_id、answer_sha256、method=human、
reviewer_reference、answer_correct、citation_support、
clarification_quality，不应保存实际回答或敏感用户数据。
必须去除模板的 rubric / instruction 字段；模板本身不能冒充评分。

    python -m tools.shop_quality.m21_review \
      --observations .local/m21-observations.json \
      --scenario dev-reference \
      --review .local/m21-human-review.jsonl \
      --out .local/m21-reviewed-score.json

该工具将评分与运行 ID、完整数据集 SHA256 和回答哈希一一对应。
重复、缺失、错位、模型自评、非布尔值、匿名非人类评分均不获
得“审核通过”标记。评分不足时整体准确率为 null，
只输出按全部要求轮数计算的保守下界。即使所有人工评分通过，
业务写入安全性也不因此得到证明：还需要可信的 Java/MySQL
独立审计。人工审核来源仅为输入声明，
工具无法对评分者的真实身份做加密认证。

CI 只在合成观测与合成人工标签上验证这些规则；
M2.1 的真实模型准确率和完整业务效用仍未完成。
