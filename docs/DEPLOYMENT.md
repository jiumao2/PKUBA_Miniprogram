# 生产部署准备

当前生产目标是阿里云北京 Ubuntu 24.04 单机部署：Caddy、Django/Gunicorn、PostgreSQL 17 和调赛过期任务使用 Docker Compose 运行，管理站与 API 分别使用 `admin.pkuba.cn` 和 `api.pkuba.cn`。

## 上线边界

- `pkuba.cn` 备案通过前只做配置和内网验收，不开放正式域名，也不向微信后台提交正式 request 域名。
- `.env.production` 只保存在服务器，权限设为 `600`；不得提交 AppSecret、数据库密码、Django 密钥或 GHCR 读取令牌。
- 邮件通知当前暂缓，默认不启动 `outbox` profile。
- 图片当前保存在服务器私有 Docker volume，不通过静态目录公开；启用真实业务数据前必须确定该 volume 的备份方式。
- 旧小程序仓库仍为只读。2026 初始数据只从获准的公开骨架导入，不迁移 OpenID、人员、申请、照片或旧秘密。

## GitHub 与镜像

版本标签 `vX.Y.Z` 触发 `release-images.yml`，使用仓库自带的 `GITHUB_TOKEN` 发布两个私有 GHCR 镜像：

- `ghcr.io/jiumao2/pkuba-api:<tag>`
- `ghcr.io/jiumao2/pkuba-web:<tag>`

服务器只需要一个具有 `read:packages` 权限的独立令牌来执行 `docker login ghcr.io`，不需要 GitHub 写权限。

## 服务器文件

建议部署目录为 `/opt/pkuba/app`。检出固定版本后：

```bash
cd /opt/pkuba/app
cp .env.production.example .env.production
chmod 600 .env.production
```

只在服务器编辑 `.env.production`。配置完成后先验证 Compose，不启动服务：

```bash
docker compose \
  --project-directory /opt/pkuba/app \
  --env-file /opt/pkuba/app/.env.production \
  -f /opt/pkuba/app/infra/compose.prod.yml \
  config --quiet
```

本地或 CI 使用脱敏模板检查时，可临时设置 `PKUBA_ENV_FILE=.env.production.example`；服务器不设置该变量，始终读取 `.env.production`。

备案、DNS、秘密、备份和镜像均准备完成后，生产启动顺序为：拉取固定镜像、启动 PostgreSQL、执行迁移、启动其余服务并检查健康状态。首次导入 2026 数据和首次创建超级管理员必须单独执行并审计，不写进通用启动脚本。

## 小程序生产构建

生产小程序必须在构建时写入 HTTPS API 地址：

```powershell
$env:PKUBA_API_BASE_URL = 'https://api.pkuba.cn'
npm --workspace @pkuba/miniapp run build:weapp
```

备案通过且 HTTPS 健康检查成功后，在微信公众平台把 `https://api.pkuba.cn` 配置为合法 request、uploadFile 和 downloadFile 域名，再进行真机微信登录、图片上传下载和调赛流程验收。
