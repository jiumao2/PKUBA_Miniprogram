# PKUBA

北大篮协赛事小程序重写项目。Django/PostgreSQL 是唯一权威数据源，微信小程序和内部管理网站共享 `/api/v1`。

## 目录

- `apps/api`：Django + Django Ninja。
- `apps/admin-web`：React 管理网站。
- `apps/miniapp`：Taro 微信小程序。
- `packages/api-client`：OpenAPI 生成的 TypeScript 类型与客户端。
- `packages/scoresheet-domain`：记录表暂停迁移的占位包，不含业务实现。
- `packages/design-tokens`：共享品牌变量。
- `Plan.md`：目标架构、业务决定和里程碑。
- `WORKFLOW.md`：单人 GitHub 与发布流程。

## 本地开发

要求：Windows PowerShell、Node.js 24、npm 11、Docker Desktop、微信开发者工具。

```powershell
./scripts/bootstrap.ps1
./scripts/start-local.ps1 -AdminUsername jiumao
```

首次命令安装依赖、启动 PostgreSQL 并迁移数据库；第二条命令会提示设置管理员密码，然后构建小程序、打开管理网站和微信开发者工具。在开发者工具中导入 `C:\Users\jiumao\Desktop\PKUBA_Miniprogram\apps\miniapp`，不要直接导入 `dist`。本地调试已关闭微信合法域名校验；真机和生产环境仍必须使用已配置的 HTTPS 域名。

如果已在微信开发者工具的“设置 → 安全设置”中手动开启服务端口，可给启动命令增加 `-UseWechatCli` 自动导入项目；默认启动不依赖该安全开关。

`bootstrap.ps1` 会复用已有的 `pkuba-dev-api` 镜像，避免每次访问 Docker Hub。修改 Python 依赖或 Dockerfile 后使用 `./scripts/bootstrap.ps1 -Rebuild`；如果首次构建无法访问 Docker Hub，需要先在 Docker Desktop 中配置可用的 HTTPS 代理或镜像源。

默认地址：

- API：`http://127.0.0.1:8000/api/v1`
- OpenAPI：`http://127.0.0.1:8000/api/v1/docs`
- 管理网站：`http://127.0.0.1:5173`
- Mailpit：`http://127.0.0.1:8025`

如需单独创建或重设本地管理员：

```powershell
./scripts/create-admin.ps1 -Username your-name
```

该命令会交互式读取并校验密码，不会把密码写入命令行、日志或仓库。小程序构建输出位于 `apps/miniapp/dist`，在微信开发者工具中导入该目录。

如果只想启动后端和前端监听而不自动打开应用，可运行 `./scripts/dev.ps1`。管理站地址为 `http://127.0.0.1:5173`；微信开发者工具项目根目录必须选择 `apps/miniapp`，而不是 `dist`。

赛程模板基线位于 `docs/templates/PKUBA_赛程模板_v1.xlsx`，用于确定填写结构和校验规则。登录管理网站后，可在“赛程导入”按当前赛季下载动态签名模板，上传进入暂存校验，逐场确认调赛政策后再原子写入。

管理网站已提供个人账号登录和“管理员账户”页面。只有超级管理员可升级普通管理员、停用或恢复账号；应用内没有降级入口，且服务器保护最后一个有效超级管理员。

`ScoresheetReader` 及依赖它的结构化记录表功能当前明确暂停，详见 `Plan.md`，未经确认不得开始迁移。

## 检查

```powershell
./scripts/check.ps1
```

复制 `.env.example` 为 `.env` 后只在本机填写秘密。旧项目中的密码、SMTP 凭据、OpenID 和云密钥不得迁入本仓库。

许可证：GPL-3.0。
