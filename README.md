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
- `docs/DEPLOYMENT.md`：阿里云生产配置、GHCR 镜像和备案前上线边界。

## Ubuntu WSL 本地验收

正式的本地验收基线是 Windows 上的 `Ubuntu-24.04` WSL。要求：管理员 PowerShell、WSL2、Node.js 24、npm 11 和微信开发者工具；脚本会在 Ubuntu 内安装 Docker Engine、Compose 和 Buildx。

首次启动前复制本地环境文件：

```powershell
Copy-Item .env.example .env
```

只在本机 `.env` 中填写 `WECHAT_APP_ID=wxc9104b1a61511ee3` 和 `WECHAT_APP_SECRET=...`。AppSecret 只传给 WSL 中的 Django 容器用于微信 `code2session`，不得写入小程序、命令行、截图、日志或 Git。

在管理员 PowerShell 中运行：

```powershell
./scripts/deploy-wsl.ps1
```

脚本会提示输入本地超级管理员密码，然后在 Ubuntu 内构建并启动 PostgreSQL 17、Django/Gunicorn、Caddy 和 Mailpit，执行迁移，只读导入相邻旧项目的 2026 数据，创建 `local-admin`，建立固定的 Windows `localhost` 端口转发，并构建微信小程序。重复部署时可加 `-SkipInstall` 跳过 Ubuntu 包检查。

默认入口：

- 管理网站：`http://localhost:8088/`
- API：`http://localhost:8088/api/v1`
- OpenAPI：`http://localhost:8088/api/v1/docs`
- Mailpit：`http://localhost:8089/`
- 微信小程序项目：`apps/miniapp`（开发者工具会读取其中的 `dist/`）

微信开发者工具不会直接编译 `src/`。每次修改小程序源码后，开发时运行 `npm run dev:miniapp` 保持 Taro 监听，或重新执行 `./scripts/deploy-wsl.ps1 -SkipInstall` / `npm --workspace @pkuba/miniapp run build:weapp` 生成 `dist/`；随后在开发者工具点击“编译”。如果界面仍旧，先确认项目目录是 `apps/miniapp`，再检查 `dist/app.json` 的修改时间，不要导入旧仓库或单独导入 `dist/`。

当前小程序使用自定义大字号底栏：首页、对阵、排名、数据、我的。“对阵”页上方可切换完整赛程赛果与淘汰赛晋级图；淘汰赛按男甲、男乙、女甲、女乙展示真实轮次、比分、胜者和保级赛。若开发者工具仍显示系统默认小字号底栏，执行“清缓存 → 清除全部缓存”，重新点击编译，并确认 `dist/app.json` 中 `tabBar.custom` 为 `true`。

网页登录名默认为 `local-admin`，密码是部署时输入的值。小程序管理员首次注册只填写当前赛季邀请码；初始网页登录密码与该邀请码相同，登录管理网站后用右上角“修改密码”设置个人密码。邀请码轮换不会改变已有管理员密码。

部署脚本会启动隐藏的 WSL 保活进程；WSL 重启或 IP 变化后重新运行脚本即可刷新端口转发。本地开发者工具已关闭合法域名校验；真机与生产环境仍必须使用微信后台配置的 HTTPS 域名。

Docker Desktop + Windows 前端监听脚本仍可用于快速开发：`./scripts/bootstrap.ps1`、`./scripts/start-local.ps1` 和 `./scripts/dev.ps1`。它们不是最终本地验收基线。

赛程模板 V2 基线位于 `docs/templates/PKUBA_赛程模板_v2.xlsx`，按真实数据完整填写的人可读示例位于 `docs/examples/PKUBA_2026北大杯_赛程导入示例_v2.xlsx`，格式和接口约定见 `docs/SCHEDULE_IMPORT_V2.md`。登录管理网站后，可在“赛程导入”按准备中的赛季下载无签名动态模板；管理员通常只补比赛清单并把人可读对阵编号放入网格。上传只创建暂存批次，确认时只新增小组、签位和比赛，不更新或删除既有赛程。新比赛默认允许领队调赛，之后统一在“赛程编辑”逐场修改；误导入由受保护的“重置本赛季导入”整批撤销。

管理网站已提供个人账号登录和“管理员账户”页面。只有超级管理员可升级普通管理员、停用或恢复账号；应用内没有降级入口，且服务器保护最后一个有效超级管理员。

超级管理员可从左侧“赛季与组别”进入基础配置工作区：历史公开赛季用于只读核对；新建准备中赛季时可选系统标准配置，也可仅沿用历史赛季的组别、启用场地、时段和容量。准备期可以统一编辑赛季元信息、组别、场地、时段及每周容量，所有内容经版本检查后一次保存并审计；赛季公开后自动锁定。

小程序“我的”页使用真实微信身份：首次使用先设置唯一昵称，随后可先选男甲/男乙/女甲/女乙，再认领该组别中尚未被认领的球队，或使用当前赛季邀请码注册普通管理员。系统不填写或保存姓名、邮箱、手机号；同一昵称也是管理员网站登录名，同一账号可以同时是领队和管理员。管理员初始网页密码等于注册时的邀请码，后台可改；超级管理员可在“管理员账户”页轮换邀请码，数据库只保存摘要。

领队工作台已提供本队比赛、发起普通/跨周调赛、查看申请、对手确认、指定球队投票、撤回和线下特殊调赛说明。管理员工作台已提供调赛审核与取消、赛程查看；超级管理员还可二次确认后直接纠错赛程，并显式处理关联申请和审计记录。旧小程序的裁判功能不在 V1，记录表、照片和依赖 `ScoresheetReader` 的功能仍保持暂停。

登录入口只在小程序“我的”页。首次进入点击“微信登录”并设置昵称；之后使用仍有效的本地会话，令牌过期时会重新通过 `wx.login` 识别已有 OpenID。点击“退出当前账号”会同时撤销服务端会话。开发者工具中的成功登录必须使用刚生成的 code，不能复用旧 code。

“我的”会在没有本地令牌时静默调用 `wx.login`：如果该 OpenID 已注册，会直接恢复账号、领队和管理员角色，不要求再次点击登录；只有从未注册的 OpenID 才显示昵称注册入口。

在首页、对阵、领队赛程或管理员赛程中点击一场比赛，可进入“比赛资料”。本场参赛球队领队和管理员可以分别上传记录表原图、比赛合照和其他照片；任一当季领队和管理员可以查看。记录表接受可正常解码的 JPEG/PNG/WebP，单张不超过 20 MB，不设固定像素门槛，并需要确认已正确结表。三类图片均进入网页后台“比赛资料”审核；管理员可以按类别筛选、查看原图、通过、退回或重新上传。替换后的图片重新待审核，旧文件退出在线列表但保留审计记录。

超级管理员直接修改日期、时段、场地、参赛方、比分、比赛状态或调赛政策时，正式入口是管理网站“赛程编辑”。该页面执行版本、容量、球队时段、场地和活动申请检查，并要求二次确认；小程序只提供赛程/资料查看和调赛业务入口。

## 2026 北大杯数据

旧项目保持只读。只检查三份公开赛事骨架：`Private_2026北大杯.json`、`Team_2026北大杯.json` 和 `Schedule_2026北大杯.json`；不读取或迁移 OpenID、人员、历史申请、照片、密码或技术统计。

```powershell
. ./scripts/lib.ps1
Invoke-PkubaCompose run --rm -v 'C:\Users\jiumao\Desktop\北大篮协小程序\Backup:/legacy:ro' api python manage.py import_legacy_2026 --source /legacy --dry-run
Invoke-PkubaCompose run --rm -v 'C:\Users\jiumao\Desktop\北大篮协小程序\Backup:/legacy:ro' api python manage.py import_legacy_2026 --source /legacy
```

挂载参数中的 `:ro` 强制容器只读旧目录。导入使用原元信息名称“北大杯”，固定校验 4 个组别、57 支球队、146 场比赛、142 场比分和 8 场不可调比赛；两条历史场地冲突会保留比赛并标记为“历史场地待核实”。原始日期不会平移；当前没有未来比赛时，首页展示近期赛果，赛程页展示完整赛程与比分。排名页由后端根据其中 126 场小组赛即时计算 11 个小组、57 支球队的排名和交手矩阵，不直接读取旧排名缓存。

记录表原图、比赛合照和其他照片上传已经独立实现；`ScoresheetReader` 及依赖它的结构化记录表、赛果发布联动仍明确暂停，详见 `Plan.md`。

## 检查

```powershell
./scripts/check.ps1
```

使用 WSL 作为本地验收环境时，完整检查直接运行：

```powershell
./scripts/check-wsl.ps1
```

该命令先在 WSL 中使用现有 PostgreSQL 服务创建隔离测试库，运行 Ruff、迁移漂移检查、OpenAPI 导出和全部 pytest，再在 Windows 生成 TypeScript 客户端并执行前端类型检查、测试及构建；不会以 SQLite 替代并发测试。

复制 `.env.example` 为 `.env` 后只在本机填写秘密。旧项目中的密码、SMTP 凭据、OpenID 和云密钥不得迁入本仓库。

许可证：GPL-3.0。
