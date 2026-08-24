# PKUBA 生产部署与自动回滚

生产环境为阿里云 Ubuntu 24.04 单机，保留现有 Compose project
`pkuba-ip-test` 及其 `postgres-data`、`private-media`、`archive-staging` 卷。
管理站和 API 分别使用 `https://admin.pkuba.cn` 与
`https://api.pkuba.cn`。日常发布只需在本地推送版本标签，不再登录服务器。

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
成功开放；任一步失败则恢复数据库和上一镜像
```

## 不可违反的边界

- 生产 `.env` 只保存在 `/opt/pkuba/ip-test/.env`，权限为 `600`。数据库、
  Django、微信、Qwen、SMTP 和 GHCR 凭据都不能进入 GitHub Actions 日志或仓库。
- 镜像部署只接受 `ghcr.io/jiumao2/pkuba-*@sha256:...`，不接受可变 tag。
- 部署账号只接受四参数 `deploy` 命令；禁止 PTY、端口转发、密码登录和任意 shell。
- 不更改 Compose project 名，不复制或重建业务卷，不运行 `down -v`。
- 迁移前等待记录表识别、归档、照片清理、编辑租约及到期调赛处理结束，最长
  15 分钟；超时安全退出且不会进入维护模式。
- 最终数据库 dump 在全部写入进程停止后生成，是自动回滚的权威恢复点。媒体卷在
  写入暂停期间保持原样，不为每次发布重复复制。
- 生产数据与私有媒体的完整灾难恢复仍使用“全系统原始备份”；发布回滚备份不能
  替代跨磁盘、跨主机备份。
- 邮件 worker 默认不启动。所有邮件只发篮协公邮，启用前需单独完成授权码轮换与
  Mailpit 验收。
- `scripts/deploy-wsl.ps1` 仅用于本机 Ubuntu WSL，严禁在生产服务器运行。

## GitHub 自动发布

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

该脚本只检查现有卷、创建 `pkuba-deploy`、安装 ForcedCommand、建立只读仓库和
当前版本清单，不重启现有容器。测试服务器尚含合成公开数据时可临时加
`--allow-synthetic-test-data`；正式上线前必须清除合成数据，并把
`/etc/pkuba-deploy.conf` 的 `PKUBA_ENFORCE_DATA_GATE` 恢复为 `1`。

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

先在隔离 Compose project 验证一次成功发布，并分别制造迁移失败、API readiness
失败、worker 启动失败和外部 HTTPS 失败；每次都应看到旧数据库、旧 digest 和旧
业务计数恢复。随后对生产当前版本执行一次无数据变化重部署，再发布补丁标签。
只有这些检查完成后，才禁用 root 密码登录；仍需保留个人密钥用于灾难恢复。

## 日常发布

确认所有修改已提交并推送到 `main`，然后在仓库根目录运行：

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

迁移、API、worker、Caddy 或真实 HTTPS 验收任一步失败时，服务器会保持维护状态、
停止新服务、重建数据库并恢复最终 dump，再启动上一组 digest。回滚成功后重新开放
访问；回滚自身失败时不会暴露半完成系统，维护标记继续保留。

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

`./scripts/deploy-wsl.ps1` 现在只安装/启动容器、迁移、检查 readiness、构建小程序
和刷新端口代理，不导入旧数据、不生成演示赛季，也不创建或重置管理员。

本地空库需要数据时必须单独显式执行：

```powershell
./scripts/initialize-wsl.ps1 -Mode Demo -Confirmation INITIALIZE_LOCAL_DATA
./scripts/initialize-wsl.ps1 -Mode Legacy2026 \
  -Confirmation INITIALIZE_LOCAL_DATA \
  -LegacySource 'C:\Users\jiumao\Desktop\北大篮协小程序\Backup'
```

创建本地超级管理员也使用独立交互命令：

```powershell
./scripts/create-admin-wsl.ps1 -Username local-admin
```

任何这类初始化命令都不得在生产服务器或真实生产数据库运行。

## 小程序发布边界

发布工作流以 `https://api.pkuba.cn` 构建并保存小程序 `dist/`，但不会替代微信平台
上传、审核和正式发布。备案与 HTTPS 就绪后，还需在微信公众平台配置 request、
uploadFile、downloadFile 合法域名，并完成人工开发者工具和真机验收。
