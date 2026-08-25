# PKUBA 同机蓝绿部署方案（测试期草案，尚未启用）

项目当前仍处于测试阶段，独立复测结论为 **NO-GO**。现有服务器上的
`pkuba-ip-test` 是单 project 测试栈，不是本方案已完成的生产基线。GitHub
`PRODUCTION_DEPLOYMENTS_ENABLED`、服务器 `PKUBA_PRODUCTION_AUTOMATION_ARMED`
和版本兼容合同必须继续保持关闭；本页目前只用于代码审查和隔离演练，禁止据此连接
服务器执行切换。

最终拓扑为：稳定 `pkuba-gateway` Caddy、稳定 `pkuba-data` PostgreSQL、独立
`pkuba-blue`/`pkuba-green` 应用 project。现有 PostgreSQL、私有媒体和归档卷以
external volume 复用，不复制到应用栈。管理站和 API 最终分别使用
`https://admin.pkuba.cn` 与 `https://api.pkuba.cn`。

```text
main 上的干净提交
        ↓ scripts/release.ps1
vX.Y.Z 标签
        ↓
可复用 CI 全绿
        ↓
API / Web 镜像（tag + commit tag + 不可变 digest）
        ↓
受限 SSH ForcedCommand
        ↓
预检 → 在线备份 → 维护模式 → 最终备份 → 迁移 → 验收
        ↓
成功开放；普通应用故障只切回旧应用，确认数据损坏时才成对恢复 DB/媒体/归档
```

## 不可违反的边界

- 生产 `.env` 只保存在 `/opt/pkuba/ip-test/.env`，权限为 `600`。数据库、
  Django、微信、Qwen、SMTP 和 GHCR 凭据都不能进入 GitHub Actions 日志或仓库。
- 镜像部署只接受 `ghcr.io/jiumao2/pkuba-*@sha256:...`，不接受可变 tag。
- 部署账号只接受四参数 `deploy` 命令；禁止 PTY、端口转发、密码登录和任意 shell。
- Caddy gateway、data、blue、green 使用独立 Compose project；现有 `pkuba-ip-test_*` 数据卷只作为 external volume 复用。不得复制、重建或执行 `down -v`。
- 迁移前等待记录表识别、归档、照片清理、编辑租约及到期调赛处理结束，最长
  15 分钟；超时安全退出且不会进入维护模式。
- writer fence 内同时生成 PostgreSQL dump、私有媒体包和归档包，并写同一 manifest、大小和 SHA-256。该一致点用于确认数据损坏后的成对恢复；普通应用回切绝不恢复其中任一数据资源。
- 旧应用栈切流后保留 24 小时且 worker 保持停止。只有旧应用与新 schema/data contract 经过兼容测试时才能回切；无法兼容时不得伪装成普通蓝绿发布。
- 生产数据与私有媒体的灾难恢复仍需独立异地备份；部署一致点不能替代跨磁盘、跨主机备份。
- 邮件 worker 默认不启动。所有邮件只发篮协公邮，启用前需单独完成授权码轮换与
  Mailpit 验收。
- `scripts/deploy-wsl.ps1` 仅用于本机 Ubuntu WSL，严禁在生产服务器运行。

## GitHub 自动发布

当前仅允许运行 CI、构建镜像和小程序 artifact；部署 job 受仓库变量显式关闭，服务器端即使被误调用也会因武装开关和兼容合同为 0 而失败。不得为消除 skipped job 而提前开启这些开关。

`.github/workflows/release.yml` 是唯一正常发版入口：

1. 验证 `vX.Y.Z` 标签及其提交属于 `main`。
2. 调用 `ci.yml` 完成 PostgreSQL 后端测试、Ruff、迁移漂移、OpenAPI 同步、
   TypeScript、组件测试和生产构建。
3. CI 全绿后构建并发布 API 与管理站镜像，同时保存 tag、commit tag 和 digest。
4. 构建生产地址的小程序 `dist/` 并保存为 30 天 Actions artifact。
5. 通过 `production` Environment 的专用 SSH 密钥部署。
6. 服务器验收成功后才创建 GitHub Release。

`.github/workflows/deploy.yml` 只用于手工重新部署已经存在的标签，不重新构建镜像。
它会重新解析 GHCR digest，并要求输入 `DEPLOY`。GitHub 与服务器都持有串行锁，
两个发布不会并行迁移。

## 一次性服务器接入

以下步骤只做一次。当前服务器目录是 `/opt/pkuba/ip-test`；执行前确认现有服务、
卷和磁盘：

```bash
cd /opt/pkuba/ip-test
docker compose -p pkuba-ip-test -f compose.yml ps
docker volume inspect pkuba-ip-test_postgres-data
docker volume inspect pkuba-ip-test_private-media
df -h / /var/lib/docker
```

### 1. 生成两套独立密钥

在可信电脑生成 GitHub Actions → 生产服务器的部署密钥：

```bash
ssh-keygen -t ed25519 -f pkuba-actions-production -C github-actions-production
```

私钥内容稍后放入 GitHub Secret `PROD_SSH_PRIVATE_KEY`；公钥上传服务器临时目录。
不要复用个人 SSH 密钥。

在服务器 root 会话生成服务器 → GitHub 的只读 Deploy Key：

```bash
ssh-keygen -t ed25519 -f /root/pkuba-github-readonly -C pkuba-production-readonly
cat /root/pkuba-github-readonly.pub
```

把公钥添加到仓库 Settings → Deploy keys，保持只读。服务器需固定 GitHub host
key；先从可信渠道核对 GitHub 公布的 SSH 指纹，再写入：

```bash
install -d -m 700 /root/.ssh
ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts
ssh-keygen -F github.com -f /root/.ssh/known_hosts
```

### 2. 生成切换前一致备份

把当前提交中的 `scripts/prod/` 安全复制到服务器临时目录，然后以 root 执行：

```bash
chmod 700 /root/pkuba-prod-tools/*.sh
/root/pkuba-prod-tools/backup-current-server.sh
```

脚本会短暂停止 API 和写入 worker，生成 PostgreSQL custom dump、完整私有媒体
tar、清单和 SHA-256，然后恢复原服务。Caddy 保持运行，这一次旧配置可能短暂显示
502；以后自动发布使用正式 503 维护页。记录输出的备份目录，逐项确认
`SHA256SUMS` 通过后再继续。

### 3. 锁定当前版本和镜像 digest

先拉取当前镜像，并从 `RepoDigests` 取得不可变引用：

```bash
docker pull ghcr.io/jiumao2/pkuba-api:v0.2.0
docker pull ghcr.io/jiumao2/pkuba-web:v0.2.0
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' \
  ghcr.io/jiumao2/pkuba-api:v0.2.0
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' \
  ghcr.io/jiumao2/pkuba-web:v0.2.0
```

同时在可信仓库中执行 `git rev-list -n 1 v0.2.0`，得到 40 位 commit。不要把 tag
本身当作回滚依据。

### 4. 安装受限部署入口

服务器必须已经完成 `docker login ghcr.io`，所用令牌只授予 `read:packages`。
把 Actions 公钥、GitHub 只读私钥和一致备份路径传入：

```bash
/root/pkuba-prod-tools/bootstrap-server.sh \
  --deploy-public-key-file /root/pkuba-actions-production.pub \
  --github-read-key-file /root/pkuba-github-readonly \
  --backup-dir /opt/pkuba/backups/20260825T000000Z-pre-automation \
  --current-tag v0.2.0 \
  --current-commit 0123456789abcdef0123456789abcdef01234567 \
  --current-api-image ghcr.io/jiumao2/pkuba-api@sha256:API_DIGEST \
  --current-web-image ghcr.io/jiumao2/pkuba-web@sha256:WEB_DIGEST
```

该脚本只检查现有卷、创建受限账号、安装 ForcedCommand、建立只读仓库和写入“尚未转换”的状态，不重启现有容器，也不会把当前单 project 变成蓝绿基线。执行后自动化仍强制关闭；必须先由后续独立的基线转换脚本和演练把 active slot 建立为 blue 或 green，当前版本尚未提供可批准执行的服务器转换流程。

### 5. 配置 GitHub production Environment

在仓库 Settings → Environments → `production` 添加：

| Secret | 内容 |
| --- | --- |
| `PROD_SSH_HOST` | 生产服务器域名或固定 IP |
| `PROD_SSH_PORT` | SSH 端口，通常为 `22` |
| `PROD_SSH_USER` | `pkuba-deploy` |
| `PROD_SSH_PRIVATE_KEY` | Actions 部署私钥完整内容 |
| `PROD_SSH_KNOWN_HOSTS` | 经可信渠道核对的生产服务器 host-key 行 |

known-hosts 使用非 22 端口时，主机部分必须写成 `[host]:port`。工作流强制
`StrictHostKeyChecking=yes`，不会自动接受新 host key。

### 6. 首次演练

只在本机或隔离服务器 Compose project 中演练，不连接现有测试服务器：候选 readiness 失败不切流、切流后 5xx 只回切应用、worker 卡死、数据库不可达、媒体只读、迁移不兼容，以及数据库/媒体同时损坏时按同一 manifest 成对恢复。每次都要保留日志证明普通故障没有恢复数据。全部通过并经用户明确批准进入上线阶段后，才设计并执行现有单 project 的一次性基线转换。

## 日常发布（尚未开放）

测试阶段不得创建用于部署的生产标签。未来只有独立报告改为 GO、基线转换与失败演练完成、用户明确要求上线后，才确认所有修改已提交并推送到 `main`，然后在仓库根目录运行：

```powershell
./scripts/release.ps1 -Version v0.3.0
```

脚本会拒绝脏工作区、非 `main`、本地与 `origin/main` 不一致、非法或重复版本。
它只创建并推送带说明标签，不连接服务器。之后在 GitHub Actions 查看发布、备份、
迁移和 HTTPS 验收结果。

服务器每次部署会：

1. 拉取并验证 tag、commit、digest、Compose、磁盘和业务空闲状态。
2. 生成在线初步 dump。
3. 写入维护标记并停止 API 与所有写入 worker。
4. 生成最终 dump 与 SHA-256。
5. 使用新镜像迁移并执行 `check --deploy`。
6. 检查内部 API、PostgreSQL、媒体/归档卷、公开赛季、worker 稳定期。
7. 在维护状态下经真实 HTTPS 核对新 API 和管理站版本。
8. 关闭维护状态，再核对公开 API 与管理站首页。
9. 保留最近三个成功版本及回滚点；失败记录永不自动删除。

## 健康接口

- `/api/v1/health/live`：仅表示 Gunicorn 进程存活。
- `/api/v1/health/ready`：检查 PostgreSQL、私有媒体目录和归档目录；返回版本 tag、
  commit 与每项结果，任一关键依赖失败即返回 503。
- `/api/v1/health`：兼容入口，语义与 readiness 相同。
- `https://admin.pkuba.cn/_deployment/ready`：只供部署核对管理站镜像版本。

维护期间仅 live、ready 和管理站部署探针可访问；其他 API 与页面返回正式的 503、
`Retry-After: 120` 和 `Cache-Control: no-store`。

## 自动回滚与人工恢复

候选或切流后的普通应用故障只把 Caddy upstream 切回仍保留的旧应用栈，并确认
`database_restored=0`、`media_restored=0`、`archive_restored=0`；不得自动重建数据库。
只有已经确认数据损坏时，才先停写和保全现场，再由独立事故恢复流程使用同一
manifest 成对恢复数据库、媒体和归档。若旧应用无法读取新 schema，禁止直接回切，
必须保持维护状态并执行预先演练的兼容处理。

GitHub 日志会给出服务器部署日志末尾和恢复目录，但不会显示 `.env`。人工灾难恢复
时先登录服务器查看：

```bash
cat /opt/pkuba/deploy/state/current.env
ls -lt /opt/pkuba/deploy/backups
ls -lt /opt/pkuba/deploy/logs
cat /opt/pkuba/deploy/state/maintenance.enabled
```

不要在未核对 dump、当前 digest 和 volume 名称时手工删除维护标记。全系统数据库与
媒体恢复继续使用 [`BACKUP_AND_ARCHIVE.md`](BACKUP_AND_ARCHIVE.md) 的隔离恢复流程。

## 本地 WSL 与生产严格分离

`./scripts/deploy-wsl.ps1` 只用于本机测试：启动容器、迁移、检查 readiness、构建小程序和刷新端口代理。它不得导入旧数据、生成演示赛季或创建/重置管理员，也不得复用于服务器。

当前仓库仍保留若干本地 demo/legacy 初始化入口，独立报告要求首发前彻底删除；在用户确认精确删除清单前，本部署文档不再把它们列为可用流程。空库首启必须在隔离环境通过正常后台能力完成赛季、管理员与赛事配置，不得依赖这些入口。
## 小程序发布边界

发布工作流以 `https://api.pkuba.cn` 构建并保存小程序 `dist/`，但不会替代微信平台
上传、审核和正式发布。备案与 HTTPS 就绪后，还需在微信公众平台配置 request、
uploadFile、downloadFile 合法域名，并完成人工开发者工具和真机验收。
