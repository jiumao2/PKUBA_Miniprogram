# PKUBA 本地开发与验证

本文说明本地环境和检查命令。产品规则见 [`SYSTEM_SPEC.md`](SYSTEM_SPEC.md)，生产操作见
[`DEPLOYMENT.md`](DEPLOYMENT.md)。接手仓库还须先读 [`AGENTS.md`](../AGENTS.md)、
[`MAINTAINER_GUIDE.md`](MAINTAINER_GUIDE.md) 与 [`WORKFLOW.md`](../WORKFLOW.md)。

## 工作区边界

- 修改前核对分支、`HEAD`、`origin/main` 和 `git status --short`，冻结文件 allowlist。
- 禁止在共享工作区使用 `reset --hard`、`clean` 或覆盖他人改动。
- `docs/INDEPENDENT_TEST_PLAN_AND_RESULTS.md` 只由独立测试任务维护。
- 测试数据库、媒体、归档、凭据和构建证据必须与生产隔离；密钥不进入 Git、日志或截图。
- 本地操作不自动授权付费模型、真实邮件、微信发布、标签或生产数据操作。

## 工具链

- Node.js 24、npm 11；
- Python 3.13、PostgreSQL 17；
- Docker Engine、Docker Compose、Buildx；
- 微信小程序验证需要微信开发者工具。

正式本地验收基线是 Windows + Ubuntu 24.04 WSL2。macOS 可用 Docker Desktop 进行
开发预览，但不能替代约定的正式验收环境。

## Windows + WSL2

在仓库根目录创建本机配置：

```powershell
Copy-Item .env.example .env
./scripts/deploy-wsl.ps1
```

`deploy-wsl.ps1` 构建并启动 PostgreSQL、API、Caddy、Mailpit、记录表、归档、邮件和
调赛到期 worker，执行迁移、readiness，并构建小程序。重复运行可用 `-SkipInstall`。

部署不会导入历史数据、创建赛季、生成 demo 或建立管理员。新空库首次需要超级管理员时，
在 API 容器内交互执行：

```powershell
$repoWindows = (Resolve-Path .).Path
$repoWsl = (wsl -d Ubuntu-24.04 -- wslpath -a -u $repoWindows.Replace('\', '/')).Trim()
if (-not $repoWsl) { throw "无法把仓库根目录转换为 WSL 路径。" }

wsl -d Ubuntu-24.04 -- docker compose --project-name pkuba-wsl `
  --project-directory $repoWsl `
  --env-file "$repoWsl/.env" `
  -f "$repoWsl/infra/compose.wsl.yml" `
  exec api python manage.py bootstrap_first_superadmin
```

该命令只适用于全新环境：要求明确确认、交互输入两次密码、通过 Django 密码校验，并在
已有超级管理员或同名账号时拒绝执行。随后用刚建立的超级管理员初始化一次全局管理员
注册邀请码：

```powershell
wsl -d Ubuntu-24.04 -- docker compose --project-name pkuba-wsl `
  --project-directory $repoWsl `
  --env-file "$repoWsl/.env" `
  -f "$repoWsl/infra/compose.wsl.yml" `
  exec api python manage.py bootstrap_admin_registration_policy
```

两条命令都从终端读取敏感输入且不回显。注册策略命令只保存摘要并拒绝重复初始化；后续
邀请码轮换在管理站完成。它们不是账号升级、重置密码或生产自动化接口。赛季、球队、名单
和赛程随后都通过正式管理站创建。

默认入口：

| 服务 | 地址或目录 |
| --- | --- |
| 管理后台 | `http://localhost:8088/` |
| API | `http://localhost:8088/api/v1` |
| OpenAPI | `http://localhost:8088/api/v1/docs` |
| Mailpit | `http://localhost:8089/` |
| Readiness | `http://localhost:8088/api/v1/health/ready` |
| 微信小程序项目 | `apps/miniapp` |

WSL 重启或地址变化后，重新运行部署脚本刷新本地端口转发。

## 微信小程序

微信开发者工具读取 `apps/miniapp/dist`：

```powershell
npm run build:packages
$env:PKUBA_API_BASE_URL = "http://localhost:8088"
$env:PKUBA_ADMIN_WEB_URL = "http://localhost:8088"
$env:PKUBA_ALLOW_INSECURE_MINIAPP_URL = "1"
npm --workspace @pkuba/miniapp run build:weapp
```

导入 `apps/miniapp` 并点击“编译”。关闭合法域名校验等个人设置只能写入被忽略的
`project.private.config.json`。HTTP 仅允许本地开发；真机和正式环境必须使用微信后台
登记的 HTTPS 域名。只有当前任务明确授权时才操作用户已经打开的开发者工具窗口；未获
授权时不要干扰该窗口，需要操作时先向用户确认。

## macOS 开发预览

```bash
cp .env.example .env
chmod 600 .env
npm ci
docker compose --project-name pkuba-mac --project-directory . --env-file .env \
  -f infra/compose.wsl.yml build
docker compose --project-name pkuba-mac --project-directory . --env-file .env \
  -f infra/compose.wsl.yml up -d
```

新空库如需首位超级管理员，使用同一 Compose 参数交互运行
`python manage.py bootstrap_first_superadmin`，再运行
`python manage.py bootstrap_admin_registration_policy`。仓库不提供 demo seed、旧赛季导入器
或命令行密码/邀请码参数。

## 检查

常规完整检查：

```powershell
./scripts/check.ps1
```

WSL 隔离 PostgreSQL 验收：

```powershell
./scripts/check-wsl.ps1
```

核心门槛包括 PostgreSQL 测试、Ruff、迁移漂移、OpenAPI/生成客户端一致性、前端类型、
单测和两端生产构建。涉及页面时还要按范围完成真实浏览器或微信开发者工具检查；构建成功
不能替代交互验收。

单独构建前端时：

```powershell
npm run build:packages
npm run typecheck
npm test
npm run build
```

OpenAPI 或客户端变化必须运行仓库生成脚本并提交两者；默认检查模式不得覆盖漂移，只有显式
同步命令可以写生成文件。
