# PKUBA 生产部署、回滚与恢复

本文是 PKUBA 生产部署技术规范。仓库门禁与授权顺序见 [`WORKFLOW.md`](../WORKFLOW.md)，
数据恢复细节见 [`BACKUP_AND_ARCHIVE.md`](BACKUP_AND_ARCHIVE.md)。发布级实现不等于生产
操作授权；服务器、GitHub 设置、标签、DNS、微信和数据动作仍须对精确目标获得授权。

## 固定生产拓扑

生产只在全新 `/opt/pkuba/production` 命名空间建立，不迁移、复制或接管旧 QA/demo 卷：

| 项目 | 固定名称或路径 |
| --- | --- |
| 根目录 | `/opt/pkuba/production` |
| 部署状态 | `/opt/pkuba/production/deploy` |
| 日常/周备份 | `/opt/pkuba/production/backups/{daily,weekly}` |
| Compose project | `pkuba-gateway`、`pkuba-data`、`pkuba-blue`、`pkuba-green` |
| 网络 | `pkuba-prod-runtime` |
| 数据卷 | `pkuba-prod-postgres`、`pkuba-prod-media`、`pkuba-prod-archives` |
| Caddy 卷 | `pkuba-prod-caddy-data`、`pkuba-prod-caddy-config` |

`admin.pkuba.cn` 承载管理站，`api.pkuba.cn` 只承载 `/api/*`，`pkuba.cn` 永久 301
到管理站。三站均发送 HSTS。API 域名上的非 API 路径返回 404，不提供第二个管理站入口。

PostgreSQL 17 和 Caddy 2.10 的官方多架构 index 先由 release workflow 复制到项目 GHCR，
再按同一个不可变 digest 使用；服务器不会直接拉取可变官方标签。release manifest 同时记录
来源 digest、GHCR 镜像、应用镜像、tag 和 commit。API 生产镜像不包含测试、fixture、demo
或开发依赖。

## 首次启动前门槛

- `main` 受保护并启用 PR-only、至少一名非作者审批、dismiss stale、会话解决、strict
  `backend`/`frontend`/`openapi`、禁止 force-push/delete。
- 有效 CODEOWNERS、`v*` tag ruleset、`production` Environment 审批和最小可审计
  break-glass 已在 GitHub 实际启用。
- CodeQL、dependency review、Dependabot 和 required CI 全绿；候选已完成独立验收。
- 服务器为全新生产命名空间，数据卷不存在；可用空间至少 15 GiB。任何发布过程中低于
  10 GiB 都硬阻断。
- DNS、证书、GHCR 读取、服务器只读 Deploy Key、Actions 专用 SSH key 与 known-hosts
  已从可信渠道核对。
- `/opt/pkuba/production/.env` 为 `root:root 0600`，包含数据库、Django、微信、邮件等
  运行配置；密钥不进入命令、日志、截图或仓库。Qwen 和邮件 worker 未获授权时保持关闭。

## v1.0.0 空库启动

首先由已验证的 `main` 创建 `v1.0.0`，等待 release workflow 生成带 revision 标签的 API/Web
digest，并在可信管理机准备 Actions 公钥和服务器只读仓库私钥。服务器 root 会话预先建立
并核对 GitHub host key，然后运行：

```bash
sudo /root/pkuba-prod-tools/bootstrap-server.sh \
  --deploy-public-key-file /root/pkuba-actions.pub \
  --github-read-key-file /root/pkuba-github-readonly \
  --release-tag v1.0.0 \
  --release-commit 0123456789abcdef0123456789abcdef01234567 \
  --api-image ghcr.io/jiumao2/pkuba-api@sha256:API_DIGEST \
  --web-image ghcr.io/jiumao2/pkuba-web@sha256:WEB_DIGEST
```

脚本在任何生产写入前拒绝既有 state 和数据卷，并核对 tag、commit、镜像 revision、固定
基础镜像和磁盘空间。随后创建 root-owned namespace、卷、网络、受限部署账号和 systemd，
在维护状态启动 PostgreSQL、执行迁移与 `check --deploy`。数据库必须没有赛季或合成数据。

脚本会在终端交互调用 `bootstrap_first_superadmin` 一次。操作人输入确认、用户名和两次密码；
命令使用 Django 密码校验、事务和 PostgreSQL advisory lock，拒绝既有超级管理员、同名账号
和并发创建，并写不可变审计。密码不通过参数或标准输出传递。之后以管理站正式流程配置
赛季和账号。

初始 blue 栈、gateway、readiness、root key-only SSH、部署 forced command 和 UFW
22/80/443 均验证成功后才解除 maintenance。保持
`PKUBA_PRODUCTION_AUTOMATION_ARMED=0`，直到真实蓝绿、回切、掉电恢复和备份恢复演练通过，
且本次生产授权完成。

## GitHub 发布

`.github/workflows/release.yml` 是正常发布入口：

1. `vX.Y.Z` 必须指向 `main` 上的已验证提交。
2. 可复用 CI 运行后，复制审核过的 PostgreSQL/Caddy index，并构建 API/Web 不可变镜像。
3. 以正式 HTTPS URL 构建小程序 artifact，保留 30 天。
4. 仅当仓库变量 `PRODUCTION_DEPLOYMENTS_ENABLED=true` 时进入 `production` Environment。
5. Environment 审批后，经受限 SSH forced command 部署；成功才创建 GitHub Release。

手工 `.github/workflows/deploy.yml` 只可重新部署已存在的不可变 tag，要求输入 `DEPLOY`，
不会重建镜像。GitHub 和服务器均串行化部署。Environment secrets 只包括：

- `PROD_SSH_HOST`、`PROD_SSH_PORT`、`PROD_SSH_USER`；
- `PROD_SSH_PRIVATE_KEY`；
- 经核对的 `PROD_SSH_KNOWN_HOSTS`。

服务器 forced command 只接受 `deploy TAG COMMIT API_DIGEST WEB_DIGEST`，禁止 PTY、转发、
密码登录和任意 shell。

## 发布事务

每次部署按以下顺序执行：

1. 核对 tag、commit、镜像 revision、release contract、Compose、磁盘和业务空闲状态。
2. 写 root-only 持久 transaction journal，持久化 maintenance。
3. 停止并确认 blue/green 两套 API、expiry、scoresheet、archive、outbox 全部 writer 为零。
4. 在同一 writer fence 内生成数据库、媒体、归档配对恢复点；校验语义、哈希和身份，
   `fsync` 每个 payload 与目录，最后写 `SUCCESS`。
5. 对候选执行迁移、复合外键检查、`audit_season_integrity`、`check --deploy` 和 readiness。
6. 直接端口和稳定 Caddy 入口均核对 API/Web tag、commit、worker 心跳和稳定窗口。
7. 原子提交 current、retained、deadline、upstream、release audit；确认完成审计 durable 后才
   移除 maintenance。

事务阶段：

```text
PREPARED → RUNTIME_SWITCHED → STATE_COMMITTING → NEW_COMMITTED
     └───────── 中断时恢复 OLD ─────────┘       └─ 完成 NEW
```

只有持久写入 `NEW_COMMITTED` 后才完成候选；更早阶段一律恢复旧应用。部署、应用回切、恢复
脚本和 systemd 开机服务都先检测未完成 journal，先 durable maintenance，再 fence 全部 writer。
解析、路径、恢复写、Caddy reload、稳定入口或审计任一步失败均保留 maintenance、journal 和
`RECOVERY_REQUIRED`，不得吞掉错误或写成功审计。

## 日常与发布恢复点

`pkuba-backup@daily` 每日 UTC 03:15 生成数据库/媒体/归档一致恢复点，保留最近 5 份；
`pkuba-backup@weekly` 每周日 UTC 04:15 生成并保留最近 4 份。两者都在 maintenance 和
writer fence 内完成，payload、清单和目录 durable 后最后写 `SUCCESS`。

发布流程另保留最近 3 个完整配对恢复点，以及每份 FROM/TO 身份所需的 release worktree。
日常/周备份不会挤占这 3 份发布回滚点；失败或缺少 `SUCCESS` 的目录不计可恢复点。
这些本机恢复点仍不能替代异地主机/对象存储备份。

## 应用回切与数据恢复

普通应用故障只允许回切保留窗内、release contract 明确兼容的旧应用：

```bash
sudo /usr/local/sbin/pkuba-rollback-retained-application blue ROLLBACK_APPLICATION_ONLY
```

目标 slot 必须来自权威 retained state。回切验证 tag、commit、worktree、镜像 revision、期限、
capability 和稳定入口，并明确记录 `database_restored=0`、`media_restored=0`、
`archive_restored=0`。没有兼容回切点时保持维护并发布 bridge/hotfix。

只有确认数据库、媒体或归档损坏时，核心开发者才可执行：

```bash
sudo /usr/local/sbin/pkuba-restore-paired-data \
  /opt/pkuba/production/deploy/backups/BACKUP_DIR RESTORE_PAIRED_DATA
```

首次恢复在全局锁内先完成规范路径、固定 manifest allowlist、所有 SHA、release/image identity、
`pg_restore --list`、两份 tar 安全 listing 和 scratch 解包验证；全部通过前不创建 journal、
不维护、不停服、不接触数据。已有 journal/开机重入则先 maintenance 和 writer fence，再复核对象。

配对恢复阶段：

```text
PREPARED → INCIDENT_CAPTURED → DATA_RESTORED → RUNTIME_RESTORED
         → COMMITTED → completed payload/audit → cleanup
```

数据、目标应用和稳定入口通过后，完成 payload 先在事务内持久化，再原子归档并幂等安装审计；
确认审计 durable 后才能移除 marker/maintenance。恢复中再次掉电可重放同一备份。任何写、
reload、probe 或 cleanup 失败都保持零 writer 和可诊断事务。

## Readiness 与公开探针

- `/api/v1/health/live`：仅进程存活。
- `/api/v1/health/ready` 与 `/api/v1/health`：数据库、迁移、媒体、归档，以及
  scoresheet/archive/expiry worker heartbeat；任一关键项失败为 503，并返回 tag/commit。
- `https://admin.pkuba.cn/_deployment/ready`：管理站镜像身份。
- `production-probe.yml` 每 15 分钟只在仓库变量和 URL 均显式配置时执行公开探针；未配置
  时安全跳过，不构成生产健康证明。

维护期间除健康和部署探针外均返回正式 503、`Retry-After: 120` 和
`Cache-Control: no-store`。每次生产操作后还要核对 HTTPS/HSTS、公开 API、管理站、真实微信
及业务关键写路径。

## 运维查看与禁止事项

```bash
cat /opt/pkuba/production/deploy/state/current.env
ls -lt /opt/pkuba/production/backups/daily
ls -lt /opt/pkuba/production/backups/weekly
ls -lt /opt/pkuba/production/deploy/backups
ls -lt /opt/pkuba/production/deploy/logs
```

- 不得手工删除 transaction、completed、maintenance 或恢复 marker。
- 不得使用 `docker compose down -v`、全局 prune、可变镜像 tag 或未经核对的备份路径。
- 不得只恢复数据库、媒体或归档中的一部分。
- `scripts/deploy-wsl.ps1` 只用于本地，不能复用于服务器。
- GitHub CI 绿、readiness 绿或本地 QA 都不能替代生产授权、真实蓝绿演练和微信发布。
