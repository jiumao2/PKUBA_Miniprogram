# 安全政策

## 报告安全问题

请不要在公开 Issue、PR、日志或截图中提交令牌、OpenID、真实名单、原始记录表、
数据库、备份或服务器信息。安全问题请通过 GitHub Security Advisory 的私密报告入口
联系仓库维护者；若该入口尚未启用，请先私下联系维护者，再传输最少必要证据。

维护者会先确认收到报告，再根据影响范围协调修复、凭据轮换、审计和披露时间。不要在
未经授权的生产系统上进行扫描、社工、拒绝服务或数据访问测试。

## 仓库与发布门禁

- GitHub secret scanning、push protection、Dependabot、dependency review 与 CodeQL
  必须在首次发布前真实启用；工作流文件存在不等于平台设置已生效。
- `main`、`v*` ruleset、production Environment、required checks 与 CODEOWNERS 审批
  按 `WORKFLOW.md` 配置；break-glass 只允许最小人员、限时并保留审计。
- 生产凭据只存放在 GitHub Environment secrets 或服务器 root-owned `0600` 环境文件。
  `.env`、SSH 私钥、AppSecret、邮件授权码和模型密钥不得进入 Git。
- 发现疑似泄漏时，先撤销/轮换凭据并保留审计，再清理历史；仅删除当前文件不足以
  解除已经暴露的凭据。
