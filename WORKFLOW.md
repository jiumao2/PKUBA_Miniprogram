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

服务器完成 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) 的一次性接入后：

1. 确认本地 `main` 工作区干净，且所有提交已经推送。
2. 更新版本号和 `Plan.md` 状态。
3. 运行 `./scripts/release.ps1 -Version vX.Y.Z`；脚本会复核本地 `main` 与 `origin/main` 完全一致，并创建带说明标签。
4. 标签自动触发完整 CI。任何后端、OpenAPI、前端、测试或构建失败都会在连接生产服务器前终止。
5. CI 全绿后发布 digest 镜像、小程序 artifact，并通过受限 SSH 自动执行备份、停写、迁移、内部/外部验收和失败回滚。
6. 只有服务器验收成功才创建 GitHub Release；日常流程不需要人工 SSH。
7. 微信小程序仍需在微信平台人工上传、审核和发布，并完成真机登录、上传和调赛冒烟检查。

重新部署已经存在的版本时，只能在 GitHub Actions 手工运行“Redeploy an existing production release”，输入标签和确认词 `DEPLOY`；该流程重新解析原镜像 digest，不重新构建。首次服务器配置、失败诊断、回滚自身失败和灾难恢复仍允许使用个人 SSH 密钥。

第二名稳定开发者加入后再启用短分支、Pull Request、至少一人审核和 `main` 保护。
