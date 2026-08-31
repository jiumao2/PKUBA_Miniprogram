# PKUBA 本地开发与验证

本文面向希望在本地运行、修改或验证 PKUBA 的开发者。产品使用方式见
[`USER_GUIDE.md`](USER_GUIDE.md)，生产部署与恢复边界见
[`DEPLOYMENT.md`](DEPLOYMENT.md)。首次接手仓库或切换 Agent 时，还必须先阅读
[`AGENTS.md`](../AGENTS.md) 与 [`MAINTAINER_GUIDE.md`](MAINTAINER_GUIDE.md)。

## 共享工作区边界

- 修改前先核对分支、`HEAD`、`origin/main` 和 `git status --short`，冻结本任务的文件
  allowlist；发现其他任务改动时保留并绕开。
- 禁止使用 `reset --hard`、`clean`、覆盖式 restore 或批量删除来处理共享脏工作区。
- `docs/INDEPENDENT_TEST_PLAN_AND_RESULTS.md` 只由独立测试任务维护，普通开发、文档和
  提交任务不得修改或纳入 staged diff。
- 本地测试数据库、媒体、归档、凭据和构建证据必须与生产隔离；临时文件应放在 Git
  忽略目录或系统临时目录，并在验证完成后精确清理。
- 发布级质量是默认要求；标签、生产连接、付费服务、真实邮件、微信正式上传和数据删除
  仍需当前任务对精确动作的明确授权。

## 环境要求

仓库当前使用以下工具链：

- Node.js 24 与 npm 11；
- Python 3.13；
- PostgreSQL 17；
- Docker Engine、Docker Compose 与 Buildx；
- 构建微信小程序时需要微信开发者工具。

正式的本地验收基线是 Windows 上的 Ubuntu 24.04 WSL2。macOS 可使用
Docker Desktop 运行同一套 Compose 拓扑进行开发预览，但不能替代正式验收。

## Windows + Ubuntu WSL2

首次运行时，在仓库根目录复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

只在本机 `.env` 中填写自己的数据库、Django、微信等配置。微信 AppSecret、
数据库密码、模型密钥和邮件凭据不得写入小程序源码、命令输出、截图、日志或 Git。

在管理员 PowerShell 中启动完整本地栈：

```powershell
./scripts/deploy-wsl.ps1
```

该脚本会在 WSL 中准备 Docker，构建并启动 PostgreSQL、Django/Gunicorn、Caddy、
Mailpit、记录表识别 worker、邮件 outbox worker 和归档 worker，随后执行迁移、
readiness 检查并构建微信小程序。重复运行时可使用 `-SkipInstall` 跳过 Ubuntu
软件包检查。

部署不会导入历史数据、创建赛季或创建管理员。只有全新且为空的本地数据库才可显式
初始化演示数据：

```powershell
./scripts/initialize-wsl.ps1 -Mode Demo -Confirmation INITIALIZE_LOCAL_DATA
./scripts/create-admin-wsl.ps1 -Username local-admin
```

这些初始化命令只用于本地开发，禁止用于生产。

默认入口：

| 服务 | 地址或目录 |
| --- | --- |
| 管理后台 | `http://localhost:8088/` |
| API | `http://localhost:8088/api/v1` |
| OpenAPI | `http://localhost:8088/api/v1/docs` |
| Mailpit | `http://localhost:8089/` |
| Readiness | `http://localhost:8088/api/v1/health/ready` |
| 微信小程序项目 | `apps/miniapp` |

WSL 重启或 IP 变化后，重新运行部署脚本即可刷新 Windows `localhost` 端口转发。

## 微信小程序开发

微信开发者工具读取 `apps/miniapp/dist`，不会直接编译 `src`。开发时可保持 Taro
监听：

```powershell
npm run dev:miniapp
```

也可以重新生成一次微信产物：

```powershell
npm run build:packages
npm --workspace @pkuba/miniapp run build:weapp
```

随后在微信开发者工具中导入 `apps/miniapp` 并点击“编译”。本地关闭合法域名校验的
设置应写在被 Git 忽略的 `apps/miniapp/project.private.config.json`，不要提交开发者
工具自动生成的个人配置。

真机与生产环境必须使用微信后台配置的 HTTPS 域名。

## macOS 开发预览

安装 Node.js 24、npm 11、Docker Desktop for Mac 和微信开发者工具后，先创建并
保护本地配置：

```bash
cp .env.example .env
chmod 600 .env
```

在 `.env` 中补充仅供本机使用的配置。开发邮件默认进入 Mailpit；不需要的模型与
真实邮件凭据应保持为空。

在仓库根目录定义 Compose 命令并启动服务：

```bash
compose() {
  docker compose --project-name pkuba-mac --project-directory . --env-file .env \
    -f infra/compose.wsl.yml "$@"
}

npm ci
compose build
compose up -d db mailpit
compose run --rm --no-deps api python manage.py migrate --noinput
compose up -d
```

只有新建空库需要显式生成演示数据与本地管理员：

```bash
compose exec -T api python manage.py seed_demo --if-empty
compose exec api python manage.py create_local_admin local-admin
```

构建小程序时提供本地地址：

```bash
npm run build:packages
PKUBA_API_BASE_URL=http://localhost:8088 \
PKUBA_ADMIN_WEB_URL=http://localhost:8088 \
PKUBA_ALLOW_INSECURE_MINIAPP_URL=1 \
npm --workspace @pkuba/miniapp run build:weapp
```

查看日志使用 `compose logs -f`，停止环境使用 `compose down`。不要对需要保留的
数据库或媒体执行带 `-v` 的清理命令。

## 共用静态资源

Logo 的唯一源文件是
[`packages/design-tokens/src/assets/pkuba-logo.png`](../packages/design-tokens/src/assets/pkuba-logo.png)；
记录表模板定义的唯一源文件是
[`apps/api/core/assets/scoresheet/template_definition.json`](../apps/api/core/assets/scoresheet/template_definition.json)。
网页与小程序中的打包副本不应单独编辑。

修改源文件后执行：

```powershell
npm run assets:sync
npm run assets:check
```

`assets:check` 只验证副本，不会覆盖差异。

## 检查

常规完整检查：

```powershell
./scripts/check.ps1
```

使用 WSL 作为本地验收环境时：

```powershell
./scripts/check-wsl.ps1
```

后者会使用隔离 PostgreSQL 测试库运行后端检查，再在 Windows 侧生成客户端并执行
前端类型检查、测试与生产构建。构建通过不能替代真实 API、管理后台和微信开发者工具
验收。

提交改动前还应阅读 [`WORKFLOW.md`](../WORKFLOW.md)，按变更范围完成聚焦测试、
相邻流程回归、敏感信息检查与文档同步。
