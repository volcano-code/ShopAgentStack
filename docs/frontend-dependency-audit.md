# 前端依赖审计门禁

[文档首页](README.md) · [质量汇总](ci-required.md)

`Shop quality` 的 web 任务与 `Workspace experience` 都调用同一个标准库工具：

```bash
python -m tools.shop_quality.npm_audit_gate \
  --project apps/web --out .local/frontend-audit
```

输出目录必须不存在，避免新结果覆盖旧失败。工具运行真实 `npm audit`，显式包含生产、开发、
可选和 peer 依赖，按完整 lockfile 检查，不因为 `NODE_ENV=production` 漏掉构建依赖。
`--ignore-scripts` 禁止此审计操作运行生命周期脚本；它不替代其他安装/构建阶段的安全审查。
不自动运行 `npm audit fix`，不修改 manifest、锁文件或业务数据。

## 判断与证据

- 退出 0：报告格式与锁文件清单匹配，high/critical 为零。可能仍有低级别告警。
- 退出 1：有效报告中存在 high/critical；开发依赖同样阻断。
- 退出 2：网络、超时、命令、输入、报告格式或证据保存失败。计数为 unknown/null，不当作零。

工具要求 npm audit v2 的完整计数，交叉核对漏洞条目、严重度、对应 lockfile 节点、
全部锁定依赖数量及 npm 的 high 阈值退出码。未支持的新 npm 报告格式会拒绝通过，
必须检查实际报告并更新合同，不能用默认零值掩盖未知字段或空响应。

输出 `audit.json`、`audit.md`，记录锁文件/manifest/门禁程序的 SHA256、时间、命令退出码、
范围、规范化包名和拒绝原因。JSON 的 `npm_command_executed` 只表示运行了命令，
不表示获得有效报告。Actions 同次保存的 `source.txt` 记录 checkout 与 tree，必须一起核查。
原始 npm stdout/stderr、公告正文、远程错误详情及凭据不进入上传产物。

主质量工作流通过原有 `required -> web` 依赖传递失败；未增加 `continue-on-error`，
未降低 high 阈值或排除问题包。失败时 artifact 步骤仍执行。
这不是完整安全认证、未知漏洞扫描、生产可利用性评估或分支保护配置。

## source-map-js 的定点修复

Issue #23 对应 GHSA-68fv-2mgg-jv7q。将 `node_modules/source-map-js` 的 1.2.1 改为 1.2.2，
只修改该条目的 `version`、`resolved`、`integrity`；保留原 PostCSS `^1.2.1` 约束、
开发依赖分类、许可证、引擎要求和其他锁定包。元数据来自 npm 官方 registry：

```text
https://registry.npmjs.org/source-map-js
https://github.com/advisories/GHSA-68fv-2mgg-jv7q
version: 1.2.2
resolved: https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz
integrity: sha512-KGj/8Y43x35aZVDtt+J4mK1hoLGHULMYfSkODJNQjNDC3oW1PqPoxMwo0pLUsWM/UEGzON/NxeHywEfNXNP3Vw==
```

这些字段来自发布元数据，不是本地下载 tarball 后重算的摘要。只有锁定 `npm ci` 实际安装、
新审计和相关构建/浏览器回归通过后，才能关闭该修复的集成验收项。
原版本历史 audit 用于检查新门禁会正确拒绝，不作为修复后扫描；合成报告单测也不是在线审计。

## 验证入口

```bash
python -m pytest tools/shop_quality/tests/test_npm_audit_gate.py -q
npm --prefix apps/web ci
python -m tools.shop_quality.npm_audit_gate --project apps/web --out .local/frontend-audit-new
npm --prefix apps/web run test:unit
npm --prefix apps/web run build
npm --prefix apps/web run test:workspace
```

项目构建使用原锁定环境，浏览器依赖仍按原工作流安装。审计不可用就保留失败记录，
不得沿用上一提交的审计结果或把已有浏览器测试绿灯视为新版本安全结论。
