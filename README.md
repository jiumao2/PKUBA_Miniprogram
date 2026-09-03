<p align="center">
  <img src="miniprogram_code.jpg" width="220" alt="微信扫码进入北大篮协小程序">
  <br>
  <sub>微信扫码进入小程序</sub>
</p>

<h1 align="center">PKUBA</h1>

<p align="center">
  <strong>北大篮协赛事管理系统</strong><br>
  从赛季配置到赛果发布，一套系统完成赛事组织、协作与公开。
</p>

<p align="center">
  <a href="docs/USER_GUIDE.md">使用说明</a> ·
  <a href="docs/DEVELOPMENT.md">开发与验证</a> ·
  <a href="WORKFLOW.md">参与贡献</a> ·
  <a href="LICENSE">GPL-3.0</a>
</p>

## 一套系统，贯穿整场赛事

PKUBA 是面向高校篮球赛事的开源管理平台。系统以 Django/PostgreSQL 为权威数据源，通过微信小程序连接公众、参赛者与领队，通过网页后台支持赛事管理员，将分散的赛务工作组织成一条清晰、可靠、可追溯的数据链路。

系统以北大篮协赛事流程为核心，面向高校篮球协会、校内联赛及长期运营的业余赛事开放源代码与自部署能力。

<p align="center">
  <strong>赛季与名单 → 赛程与签位 → 调赛与资料 → 记录表复核 → 赛果与统计 → 备份与归档</strong>
</p>

## 核心特性 / Highlights

- **赛事全生命周期**：统一管理赛季、组别、球队名单、场地容量、在线排期、XLSX 导入、签位结果、调赛、比赛资料、赛果和归档。
- **一体化双端体验**：微信小程序提供赛程、淘汰赛、排名、数据、身份与领队工作台；React 管理后台承载完整赛务操作。
- **纸质记录表数字化**：支持图片上传、AI 辅助识别、网页与小程序人工复核、规则校验、不可变 publication 和公开统计。
- **服务端权威**：权限、状态迁移、版本、容量、比分、排名和派生数据均由服务端重新校验与计算，客户端只呈现状态并提交意图。
- **可靠且可审计**：关键命令使用事务、稳定 ID、版本检查和幂等键，失败不留下部分写入，publication、revision 与审计记录持续保留。
- **隐私与赛季隔离**：赛季专属数据在数据库层隔离；身份、记录表与私有媒体按角色授权，公开接口只返回允许展示的内容。
- **共享契约与设计系统**：OpenAPI 生成 TypeScript 客户端，双端复用记录表领域模型、设计变量和品牌资源。
- **可恢复运维**：提供一致性备份、私有媒体管理、赛季归档、发布回滚与隔离恢复流程。

## 系统如何协作

| 使用者 | 入口 | 服务与数据 |
| --- | --- | --- |
| 公众 / 参赛者 / 领队 | 微信小程序 | Django API → PostgreSQL |
| 赛事管理员 | 管理后台 | Django API → PostgreSQL |

微信小程序与管理后台统一调用 Django API；识别、邮件和归档任务由服务端执行，结果写入 PostgreSQL。

小程序使用 Taro、React 与 TypeScript；管理后台使用 React、Vite 与 TypeScript；服务端使用 Django、Django Ninja 与 Python；运行环境由 Docker Compose、Gunicorn、Caddy 和 GitHub Actions 组成。

## 仓库结构

```text
apps/
  api/                Django API、业务服务与异步任务
  admin-web/          React 管理后台
  miniapp/            Taro 微信小程序
packages/
  api-client/         OpenAPI 生成客户端
  design-tokens/      品牌变量与共用 Logo
  scoresheet-domain/  双端共用的记录表领域模型
docs/                 使用、开发、协议、部署与恢复文档
infra/                Compose 与运行环境配置
scripts/              初始化、检查、发布与运维脚本
```

## 快速开始

正式本地开发与验收环境为 Windows + Ubuntu 24.04 WSL2。准备 Node.js 24、npm 11、WSL2 和微信开发者工具后：

```powershell
git clone https://github.com/jiumao2/PKUBA_Miniprogram.git
Set-Location PKUBA_Miniprogram
Copy-Item .env.example .env
./scripts/deploy-wsl.ps1
```

请先在本机 `.env` 中填写自己的配置，任何密钥都不得提交到 Git。Windows、macOS、微信开发者工具、测试与静态资源步骤见[本地开发与验证](docs/DEVELOPMENT.md)。

## 文档导航

- [系统规范](docs/SYSTEM_SPEC.md)
- [小程序与管理后台使用说明](docs/USER_GUIDE.md)
- [本地开发与验证](docs/DEVELOPMENT.md)
- [维护者与 Agent 接手指南](docs/MAINTAINER_GUIDE.md)
- [赛程编排与 XLSX V3.3](docs/SCHEDULE_IMPORT_V3.md)
- [调赛状态机与兼容规范](docs/RESCHEDULING.md)
- [记录表识别、跨端复核与统计发布](docs/SCORESHEETS.md)
- [API 可靠性](docs/API_RELIABILITY.md)
- [备份与赛季归档](docs/BACKUP_AND_ARCHIVE.md)
- [生产部署、回滚与恢复](docs/DEPLOYMENT.md)
- [团队协作与发布工作流](WORKFLOW.md)

## 参与贡献

欢迎通过 Issue 或 Pull Request 提交问题、改进和适配。贡献流程见
[团队协作与发布工作流](WORKFLOW.md)，本地环境与分项检查见
[本地开发与验证](docs/DEVELOPMENT.md)。

提交前运行：

```powershell
./scripts/check.ps1
```

## License / 许可证

PKUBA 以 [GNU General Public License v3.0](LICENSE) 发布，SPDX 标识为 `GPL-3.0-only`。
