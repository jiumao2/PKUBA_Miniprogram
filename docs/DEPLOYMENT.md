# PKUBA 生产部署、回滚与恢复

本文是 PKUBA 生产发布的唯一技术规范。每次发布必须先满足 `WORKFLOW.md` 的仓库门禁、
独立验收、候选授权和恢复演练要求；缺少任何前置条件时立即停止，不得用人工跳步、测试
环境成功或 readiness 代替生产验收。

现有 `pkuba-ip-test` 单 project 只作为隔离测试栈，不得直接视为蓝绿生产基线。首次
生产接入必须先完成受审计的基线转换，并核对 GitHub
`PRODUCTION_DEPLOYMENTS_ENABLED`、服务器 `PKUBA_PRODUCTION_AUTOMATION_ARMED`
和版本 capability 合同全部绑定本次批准的发布。

生产拓扑使用稳定 `pkuba-gateway` Caddy、稳定 `pkuba-data` PostgreSQL、独立
`pkuba-blue`/`pkuba-green` 应用 project。PostgreSQL、私有媒体和归档卷以
external volume 复用，不复制到应用栈。管理站和 API 分别使用
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
预检 → 维护模式与写入围栏 → DB/媒体/归档同一配对恢复点 → 迁移 → 验收
        ↓
成功开放；普通应用故障只切回旧应用，确认数据损坏时才成对恢复 DB/媒体/归档
```

## 不可违反的边界

- 生产 `.env` 只保存在 `/opt/pkuba/ip-test/.env`，权限为 `600`。数据库、
  Django、微信、Qwen、SMTP 和 GHCR 凭据都不能进入 GitHub Actions 日志或仓库。
- 镜像部署只接受 `ghcr.io/jiumao2/pkuba-*@sha256:...`，不接受可变 tag。
- 部署账号只接受四参数 `deploy` 命令；禁止 PTY、端口转发、密码登录和任意 shell。
- Caddy gateway、data、blue、green 使用独立 Compose project；现有 `pkuba-ip-test_*` 数据卷只作为 external volume 复用。不得复制、重建或执行 `down -v`。
- 迁移前等待记录表识别、比赛图片持久化暂存、归档、照片清理、编辑租约及到期调赛处理结束，最长
  15 分钟；超时安全退出且不会进入维护模式。
- 预检必须运行只读赛季一致性审计；任何球队、组别、赛程、时段、签位、调赛、记录表、统计、媒体、导入或归档引用跨赛季时，在生成发布备份和进入维护模式前立即失败。
- writer fence 内同时生成 PostgreSQL dump、私有媒体包和归档包，并写同一 manifest、大小和 SHA-256。该一致点用于确认数据损坏后的成对恢复；普通应用回切绝不恢复其中任一数据资源。
- 回滚点中的 `previous-release.env` 与数据库、媒体和归档文件使用同一 `SHA256SUMS` 保护；恢复脚本在停服或接触数据库、媒体和归档卷之前，只按固定键读取并校验 slot、tag、commit、镜像 digest 及标签对应的发布目录，拒绝额外行、路径穿越和非法值，禁止把备份内容作为 shell 代码执行。即使文件和哈希清单被同时重写，语义校验仍会在数据操作前失败。
- 旧应用栈切流后保留 24 小时且 worker 保持停止。发布时把已经验证的回切来源 capability 写入保留栈状态；回切命令必须同时验证 tag、commit、worktree、镜像 revision、release contract、期限和该持久化合同。capability 名不要求永远相等，但必须与本次发布实际批准的回切方向一致。
- 部署和 application-only 回切在接触运行栈前建立 root-only 的持久事务目录，保存原状态、候选状态、SHA-256、阶段和恢复方向，并逐文件 `fsync` 后才发布日志。`PREPARED`、`RUNTIME_SWITCHED` 或 `STATE_COMMITTING` 中断一律恢复原应用；只有已经持久写入 `NEW_COMMITTED` 的事务才继续完成候选状态。维护模式覆盖整个多文件提交、Caddy reload、API/Web 稳定入口 tag/commit 探测和权威状态复核。
- 专用启动恢复服务会在 Docker 就绪后、任何新部署或成对恢复前检查未完成事务。状态写入、旧 upstream reload、稳定入口探测或事务清理任一步失败时保持维护和诊断日志，不把半完成状态宣称为成功。应用恢复审计必须固定写明 `database_restored=0`、`media_restored=0`、`archive_restored=0`。
- nullable schema 不等于业务语义兼容。`bf444ece` 只理解旧 `request_type`，不能处理同周手册通道，也不能在该能力激活后继续接流或作为普通回切点。首次启用应先发布 bridge，在同一 writer fence 内停止旧 API/worker、迁移并审计 0039，再运行 `python manage.py reschedule_route_activation_preflight --wait-seconds=86400 --json` 排空全部非终态申请与旧幂等窗口，最后才开放入口并切流。在受审计的 baseline conversion 自动流程及等价隔离演练完成前，必须保持该能力关闭；独立预检命令不能冒充已接入发布流程。
- 生产数据与私有媒体的灾难恢复仍需独立异地备份；部署一致点不能替代跨磁盘、跨主机备份。
- 邮件 worker 默认不启动。所有邮件只发篮协公邮，启用前需单独完成授权码轮换与
  Mailpit 验收。
- `scripts/deploy-wsl.ps1` 仅用于本机 Ubuntu WSL，严禁在生产服务器运行。

## GitHub 自动发布

部署 job 只有在仓库变量、`production` Environment、服务器武装开关和 capability 合同
全部匹配本次批准发布时才能运行。前置条件未满足时应保持 fail-closed；不得为了消除
skipped job 而提前开启开关。

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

脚本会短暂停止 API 和写入 worker，生成 PostgreSQL custom dump、完整私有媒体与
归档暂存 tar、逐文件清单和 SHA-256，然后恢复原服务。Caddy 保持运行，这一次旧配置可能短暂显示
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

该脚本只检查现有卷、创建受限账号、安装 ForcedCommand、建立只读仓库和写入“尚未转换”的状态，不重启现有容器，也不会把当前单 project 变成蓝绿基线。执行后自动化仍须保持关闭；必须通过单独评审的基线转换脚本和演练把 active slot 建立为 blue 或 green。没有已批准、可回滚且经过隔离验证的服务器转换流程时，生产接入必须停止。

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

只在本机或隔离服务器 Compose project 中演练，不连接生产：候选 readiness 失败不切流、切流后 5xx 只回切应用、worker 卡死、数据库不可达、媒体只读、迁移不兼容，以及数据库/媒体同时损坏时按同一 manifest 成对恢复。每次都要保留日志证明普通故障没有恢复数据。全部通过并取得用户对精确服务器和转换动作的明确批准后，才执行单 project 到蓝绿拓扑的一次性基线转换。

## 日常发布

只有独立验收允许进入候选发布、基线转换与失败演练完成、用户或授权发布负责人明确批准
本次版本，且所有修改已提交并推送到受保护 `main` 后，才可在干净仓库根目录运行：

```powershell
./scripts/release.ps1 -Version v0.3.0
```

脚本会拒绝脏工作区、非 `main`、本地与 `origin/main` 不一致、非法或重复版本。
它只创建并推送带说明标签，不连接服务器。之后在 GitHub Actions 查看发布、备份、
迁移和 HTTPS 验收结果。

服务器每次部署会：

1. 拉取并验证 tag、commit、digest、Compose、磁盘和业务空闲状态。
2. 持久化发布事务，写入维护标记，并停止蓝、绿两套 API 与全部写入 worker。
3. 在同一 writer fence 内生成 PostgreSQL、媒体和归档一致备份；逐文件校验并 `fsync`，最后写入 `SUCCESS`。
4. 使用新镜像迁移，验证全部复合外键既有行，再执行 `audit_season_integrity` 与 `check --deploy`；任一步失败都不切流。
5. 检查内部 API、PostgreSQL、媒体/归档卷、公开赛季和 worker 稳定期。
6. 在维护状态下经稳定 Caddy 入口核对新 API 与管理站的 tag、commit 和 readiness。
7. 原子提交 current、retained、deadline、upstream 和发布审计，再解除维护状态。
8. 保留最新三个经完整验证的配对恢复点，并同时保留每个恢复点引用的来源、目标 worktree；失败记录永不自动删除。

## 健康接口

- `/api/v1/health/live`：仅表示 Gunicorn 进程存活。
- `/api/v1/health/ready`：检查 PostgreSQL、私有媒体目录、归档目录、迁移集合，以及
  `scoresheet-worker`、`archive-worker`、`expiry` 三类 worker 心跳；返回版本 tag、
  commit 与每项结果，任一关键依赖失败即返回 503。
- `/api/v1/health`：兼容入口，语义与 readiness 相同。
- `https://admin.pkuba.cn/_deployment/ready`：只供部署核对管理站镜像版本。

维护期间仅 live、ready 和管理站部署探针可访问；其他 API 与页面返回正式的 503、
`Retry-After: 120` 和 `Cache-Control: no-store`。

## 自动回滚与人工恢复

候选或切流后的普通应用故障只允许切到保留窗内、且持久化 capability contract 明确允许从当前应用回切的旧应用栈，同时确认 `database_restored=0`、`media_restored=0`、`archive_restored=0`；不得自动重建数据库。受控命令是 `sudo /usr/local/sbin/pkuba-rollback-retained-application blue ROLLBACK_APPLICATION_ONLY`，其中 `blue` 替换为权威保留状态中的目标 slot。

命令在 Compose、维护状态和任何数据资源之前验证固定键状态、发布身份、镜像 revision、截止时间与回切合同；随后先验收目标 API、Web 的 tag/commit 及必需 worker 稳定性，再切 Caddy。全部下一状态先生成和验证，最后才进入唯一提交阶段；任一状态文件操作失败会恢复原 current、retained、deadline、upstream 与运行栈。没有兼容保留栈时必须保持维护并发布 bridge/hotfix。

部署和回切共用 `/usr/local/sbin/pkuba-recover-release-transaction`。它根据已经持久化的阶段确定性选择原应用或候选应用，重新核对两端容器、restart count、Caddy 稳定入口和所有状态文件后才解除维护。`SIGKILL`、掉电或主机重启不会依赖 shell `EXIT` trap：systemd 启动恢复与下一条部署命令都会先执行同一恢复协议。管理员不得手工删除 `state/release-transaction`、`state/release-transaction-completed` 或 `maintenance.enabled` 来绕过恢复。

### 持久事务与开机恢复状态

四个高风险入口（部署、应用回切、应用事务恢复、配对数据恢复）共用
`state/deploy.lock`。`state` 必须是 `root:root 0700` 的真实目录，锁文件必须是
`root:root 0600`、单硬链接的普通文件；符号链接、硬链接替换或非规范路径会在任何
Compose、维护或数据操作前失败。

应用部署/回切事务使用以下阶段：

```text
PREPARED → RUNTIME_SWITCHED → STATE_COMMITTING → NEW_COMMITTED
     └────────────── 中断时恢复 OLD ──────────────┘
                                      NEW_COMMITTED 后完成 NEW
```

每个阶段、原状态、候选状态和不可变字段哈希都先逐文件持久化。只有
`NEW_COMMITTED` 可以在重启后继续候选；更早阶段一律恢复原应用。恢复过程中任一状态
写入、Caddy reload、稳定入口探测或完成日志归档失败，都会重新持久化维护状态、停止
蓝绿两套写者，并保留 `RECOVERY_REQUIRED_*` 事务，不会输出成功审计。

配对数据恢复使用独立状态机：

```text
PREPARED → INCIDENT_CAPTURED → DATA_RESTORED → RUNTIME_RESTORED
        → COMMITTED → paired-restore-completed → 审计归档
```

- 首次人工恢复先在同一全局锁内完成规范路径、固定 SHA 清单、所有普通非链接文件、
  `pg_restore --list`、两份 tar 安全解包/逐文件哈希以及来源应用身份检查；全部通过前
  不创建事务、不进入维护、不停服，也不接触数据库或三个数据卷。
- 已存在事务或主机重启时顺序相反：先持久化维护状态，再停止并确认蓝、绿两套 API、
  expiry、记录表、归档和邮件写者均为零，然后才重验同一个已哈希绑定的备份对象并
  继续恢复。恢复再次崩溃时重复同一流程。
- 数据、目标应用和 Caddy 稳定入口均验证后，完成 payload 和审计先写入事务并
  `fsync`；完成目录、审计、维护移除和事务归档按固定顺序提交。清理失败会重新进入
  维护并保留可重放事务，不能出现“已开放但没有权威状态/审计”的窗口。
- systemd 开机先运行事务恢复，再运行权威应用启动；slot 的 API 和所有 worker 使用
  `restart: "no"`。启动命令仍会先停止并确认两套写者为零，再启动 `current.env` 指定
  的唯一 slot，避免 Docker 重启顺序绕过 writer fence。

只有已经确认数据损坏时，才先停写和保全现场，再由核心开发者执行
`sudo /usr/local/sbin/pkuba-restore-paired-data BACKUP_DIR RESTORE_PAIRED_DATA`，
使用同一 manifest 成对恢复数据库、媒体、归档以及与该快照匹配的应用版本。脚本会先
保存损坏现场，并在启动任何业务服务前使用匹配的应用镜像运行赛季一致性审计；任何一步失败都保持维护状态。若旧应用无法理解当前 schema 或业务语义，禁止直接回切，
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
