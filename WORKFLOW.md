# 单人 GitHub 与发布工作流

## 日常开发

1. `git pull --ff-only origin main`
2. 阅读 `AGENTS.md`、`Plan.md` 和相关规格章节。
3. 在 `main` 上做一个边界清晰的小改动。
4. Ubuntu WSL 验收运行 `./scripts/check-wsl.ps1`；使用 Docker Desktop 时运行 `./scripts/check.ps1`。
5. 检查 `git diff --check` 和 `git status --short`。
6. 使用清楚、可回滚的提交信息提交并直接推送 `main`。

单人阶段不要求分支、Pull Request、审核或分支保护。GitHub Actions 只报告结果，不阻止推送。出现失败时，下一次提交优先修复失败，不在已知失败上叠加无关功能。

每次修改微信小程序源码，还必须执行：

```powershell
npm --workspace @pkuba/miniapp run typecheck
npm --workspace @pkuba/miniapp run build:weapp
```

随后在微信开发者工具中打开仓库的 `apps/miniapp`（不要单独导入 `dist`），点击“编译”，逐一打开本轮受影响页面，检查文字、间距、滚动、图片、按钮状态和底栏高亮。交付说明必须列出实际检查的页面；若开发者工具不可连接或未能编译，明确标为未完成，不得以 Taro 构建代替真实样式检查。

## 必须提交

- Django migrations。
- `package-lock.json` 和 Python锁文件。
- OpenAPI 生成的 TypeScript 客户端。
- 业务规则对应的测试和文档更新。

## 禁止提交

- `.env`、`.env.wsl.local`、AppSecret、COS/SMTP/数据库凭据。
- OpenID、真实名单、生产数据库、备份和记录表照片。
- 本地媒体、构建目录、测试缓存和日志。

## 生产发布

`main` 不自动发布。

1. 确认本地检查与 GitHub Actions 通过。
2. 更新版本号和 `Plan.md` 状态。
3. 创建带说明的 `vX.Y.Z` 标签。
4. 手动触发发布工作流。
5. 发布程序先备份 PostgreSQL，再拉取标签对应镜像并执行迁移。
6. 健康检查失败时恢复上一镜像；迁移不可逆时按发布说明恢复备份。
7. 在管理网站完成登录、当前赛季、赛程和待办冒烟检查。

第二名稳定开发者加入后再启用短分支、Pull Request、至少一人审核和 `main` 保护。
