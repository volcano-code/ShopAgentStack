# M1.5：管理员浏览器导入审核

源码提供 `/admin/demo-imports` 页面，管理中心的管理员入口可进入；客户和客服没有读取模板或执行导入的授权。
这是演示管理功能，不是 Agent 工具，不调用生成模型，也不接入真实支付。

## 新环境启用

保留旧环境，在新目录和未使用端口初始化。源码更新不会升级旧运行快照。

```bash
python -m tools.shop_demo build
python -m tools.shop_demo init --state .local/demo-review --port 18033 --retrieval bm25 --enable-seed-import --enable-seed-review-ui
python -m tools.shop_demo up --state .local/demo-review
```

访问本机 `http://127.0.0.1:18033/admin/demo-imports`，使用该环境自己的管理员账户登录。
初始化是唯一需要的准备；进入页面后无需 CLI 生成预览或确认。
`--enable-seed-review-ui` 必须同时显式开启 `--enable-seed-import`。旧初始化选项的布局不变。

初始化复用 seed_data 的商品配图、条款和源文件摘要校验，将模板写入 manifest 绑定的私有运行快照。
只读文件只挂到 admin；不进入 Vite public/dist、Agent 容器或用户浏览器存储。
`GET /shop_agent_stack/demo-imports/template` 在两个开关均开启时注册，读取前检查管理员当前角色，响应 no-store。
它只提供合成审核资料，不生成预览、不写数据库。预览/确认仍使用原 Java 接口。

## 审核流程

1. 生成商品与草稿预览，逐页展开商品、政策全文和 CUSTOMER/STAFF 可见范围。
2. 勾选已审核并输入“导入商品和草稿”，确认后显示后端回执。此时政策仍为草稿。
3. 单独生成政策发布预览，重新检查正文、范围和待发布数量；重新勾选并输入“发布已审核政策”。
4. 发布回执只证明数据库提交，不代表异步 Hybrid 索引已就绪。

页面显示完整计划 ID、来源及计划摘要、过期时间。数据库时钟和当前管理员身份才是最终依据。
确认前只读回查同一计划；身份、动作、载荷或摘要变化会阻止前端 POST。
两次点击被本地互斥拦截；跨页面并发仍由 Java 事务、计划行锁与回执幂等保护。

提交超时/断网不会自动重试，也不会静默生成新计划。先查询原预览状态：APPLIED 显示原回执；
PREVIEW 重新展示并清空勾选/确认短语，管理员重新审核后决定是否再次提交。
刷新不自动恢复或提交，可手动输入原计划 ID 查询；不能凭计划 ID 绕过后端归属检查。
输入确认短语只是交互防误触，绝不是授权令牌。正文以纯文本渲染，不执行 HTML。
页面不把审核正文、确认摘要或回执写入 localStorage/sessionStorage；账户登录沿用现有会话机制。

## 验证边界

`unit/demo-import.test.mjs`：真实纯函数合同、截止时间、回执绑定及非法数据拒绝。
`review-tests/mock.spec.ts`：真实 Chromium 页面、明确的 API 替身，覆盖分离确认、重复点击、丢失响应、过期、权限、刷新、旧预览变化。
`tools.shop_demo.review_smoke`：独立新建 BM25 演示环境，真实浏览器/Java/MySQL 导入与二次发布，最后只清理本次自有测试资源。
`Admin review UI` 工作流同时执行上述范围；某个提交是否通过应查看该提交的实际结果。

本轮不新增 Hybrid 浏览器组合验收、旧状态升级、模板在线编辑、备份格式升级或多实例能力。
源码存在不等于所有测试已执行；不把 API 替身测试当成真实 Java 集成。
