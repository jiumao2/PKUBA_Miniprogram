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
./scripts/dev.ps1
```

默认地址：

- API：`http://127.0.0.1:8000/api/v1`
- OpenAPI：`http://127.0.0.1:8000/api/v1/docs`
- 管理网站：`http://127.0.0.1:5173`
- Mailpit：`http://127.0.0.1:8025`

首次启动后运行：

```powershell
./scripts/seed.ps1
./scripts/create-admin.ps1 -Username your-name
```

第二条命令会交互式读取并校验密码，不会把密码写入命令行、日志或仓库。小程序构建输出位于 `apps/miniapp/dist`，在微信开发者工具中导入该目录。

赛程模板基线位于 `docs/templates/PKUBA_赛程模板_v1.xlsx`，用于确定填写结构和校验规则。登录管理网站后，可在“赛程导入”按当前赛季下载动态签名模板，上传进入暂存校验，逐场确认调赛政策后再原子写入。

管理网站已提供个人账号登录和“管理员账户”页面。只有超级管理员可升级普通管理员、停用或恢复账号；应用内没有降级入口，且服务器保护最后一个有效超级管理员。

`ScoresheetReader` 及依赖它的结构化记录表功能当前明确暂停，详见 `Plan.md`，未经确认不得开始迁移。

## 检查

```powershell
./scripts/check.ps1
```

复制 `.env.example` 为 `.env` 后只在本机填写秘密。旧项目中的密码、SMTP 凭据、OpenID 和云密钥不得迁入本仓库。

许可证：GPL-3.0。
