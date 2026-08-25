# PKUBA 备份与赛季归档

## 存储边界

生产环境不接入腾讯 COS。PostgreSQL 使用独立数据卷，记录表、比赛合照和其他照片使用私有 `private-media` 卷；Caddy 不直接公开该目录，图片仍通过服务端短期票据读取。临时导出包写入独立 `archive-staging` 卷。

阿里云每日备份承担灾难恢复。上线前必须确认云备同时覆盖 PostgreSQL 数据卷和 `private-media` 卷，并在隔离 Compose project 完成一次恢复。管理站的人工导出用于赛季交接、照片离线保存和核心开发者按需制作原始备份，不替代云平台日备。

## 管理站三类导出

超级管理员从“备份与归档”操作：

1. `PKUBA_Data_<赛季>_<时间>.zip`：`tables.xlsx`、无损 `raw/*.jsonl`、结构化记录表/publication、统计、调赛、媒体清单、相关审计和 SHA-256。递归排除 OpenID、邀请码摘要、密码/令牌摘要和部署密钥，不包含照片原文件。
2. `Photo_<赛季>_<时间>.zip`：单一 `Photo_<赛季>/` 扁平目录，包含记录表、比赛合照、其他照片及仍在线的历史/软删除版本，并附 CSV、manifest 和 `SHA256SUMS.txt`。
3. `PKUBA_FullBackup_<时间>.tar.zst`：PostgreSQL custom-format dump、完整私有媒体树、Git commit、Django 迁移、全部表精确记录数和媒体 SHA-256。它包含 OpenID、密码哈希等敏感业务数据且不加密，只能在 HTTPS 或 localhost 下重新验证当前密码后生成。

大型任务由 `archive-worker` 使用 PostgreSQL 行锁与五分钟租约领取，同一时间最多运行一个导出或清理任务。全系统备份开始和执行前均拒绝活动识别任务与有效记录表编辑租约；捕获数据库 dump、表计数以及当时的媒体文件集合/大小期间持 PostgreSQL 独占写围栏。HTTP 非安全方法和识别、调赛过期、邮件等业务 worker 持共享围栏，因此已有写入会先完成，新写入立即返回 `503 SYSTEM_BACKUP_WRITE_FENCE`，公开读取不受影响。捕获完成后立即释放围栏，再在锁外计算媒体 SHA-256 并压缩；归档/清理任务互斥，应用内媒体对象不可原地修改。若捕获后发现文件消失或大小变化，整个任务安全失败。

包生成后最多保留 24 小时。点击下载会记录下载次数；“已保存并清理”只有至少发起一次下载后可执行，确认后立即删除服务器暂存文件，但保留文件名、大小、SHA-256、清单和审计。下载票据绑定当前账号和浏览器会话、15 分钟失效，支持 HTTP Range，响应禁止缓存。

## 赛季照片永久清理

照片清理必须同时满足：

- 赛季为不可逆 `ARCHIVED`。
- 不存在活动调赛、有效预留、识别任务或编辑租约。
- 归档后按当前赛季版本生成了完整的最终数据包和最终照片包。
- 两个包仍可下载，或已确认保存到服务器外；服务器端保留完整 manifest、大小和 SHA-256。
- 管理员勾选外部保存声明并通过第二次不可逆确认。

提交时所有在线媒体原子进入 `PURGE_PENDING` 并增加版本。Worker 逐个核对路径、字节数和 SHA-256：匹配才物理删除并标为 `PURGED`；缺失或校验异常标为 `MISSING` 并保留警告。数据库媒体行、原文件名、尺寸、哈希、publication 来源、历史修订和审计不删除。任务可幂等重跑，失败后只继续尚未完成的文件。清理完成后服务器上的最终照片 ZIP 立即删除；结构化数据包仍可继续生成，照片包不能重建。

文件接口对 `PURGED`/`MISSING` 返回 `410 MEDIA_PURGED`，客户端显示“照片已归档至线下备份”，不渲染破图，也不提供替换、审核或删除按钮。

## 隔离恢复

全系统包只能恢复到空数据库和空媒体目录。数据库名必须以 `pkuba_restore_` 开头，目标目录不能是当前 `MEDIA_ROOT` 或其父子目录：

```bash
python manage.py restore_system_backup \
  /path/to/PKUBA_FullBackup_YYYYMMDD-HHMMSS.tar.zst \
  --database-url 'postgresql://USER:PASSWORD@HOST:5432/pkuba_restore_drill' \
  --media-root /restore/private-media \
  --confirm-isolated
```

命令在写入前拒绝路径穿越、特殊/重复/清单外条目，并校验 dump 与全部媒体 SHA-256。`pg_restore --exit-on-error --no-owner --no-privileges` 成功后再次核对全部表记录数、Django 迁移集合、十类赛季专属关系一致性及恢复目录的文件集合、大小和哈希；任何跨赛季球队、组别、赛程、签位、调赛、记录表、统计、媒体、导入或归档引用都会使恢复失败。随后把快照中正在生成本包的来源 `ArchiveJob` 标为已停用，避免恢复环境的 worker 误把它当作崩溃任务重跑。最后必须使用恢复库和媒体目录启动一次临时 API，检查迁移、`/api/v1/health`、公开赛程及至少一张私有原图；不得把网页恢复功能指向生产环境。

## 上线检查

- 阿里云备份任务同时覆盖数据库卷与媒体卷，并记录保留策略和最近成功时间。
- 至少一次云备和一次应用全系统包在独立 Compose project 恢复成功。
- 恢复前后全部表计数、迁移、媒体数量/字节/SHA-256 一致，`audit_season_integrity` 为零违规，健康接口成功。
- 磁盘预检遵守：`可用空间 ≥ 预计包大小 × 1.15 + max(10 GiB, 磁盘总量 25%)`。
- `.env`、AppSecret、SMTP 密码、TLS 私钥、源码和 Docker 镜像不进入原始备份；只记录 Git commit。
- 未加密原始包只能经 HTTPS/localhost 下载并立即移交到受控的离线存储。
