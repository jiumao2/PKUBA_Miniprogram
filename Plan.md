# PKUBA 路线图

本文只记录长期架构方向、下一阶段工作和真实未完成事项。当前产品合同见
[`docs/SYSTEM_SPEC.md`](docs/SYSTEM_SPEC.md)，用户操作见
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)，协作与发布门禁见
[`WORKFLOW.md`](WORKFLOW.md)。日期化测试结果不写入本文。

## 产品目标

PKUBA 为北京大学篮球赛事提供统一的小程序、管理站和服务端。Django/PostgreSQL 是
唯一业务权威；客户端只显示服务端状态并提交意图。系统覆盖赛季配置、球队与名单、
赛程、调赛、媒体、记录表、统计和归档，并保留完整审计与可恢复性。

## 架构方向

- 只维护赛程导入 V3.3 和在线赛程草稿，不保留 V1/V2 或特定赛季导入器。
- 稳定 ID、事务、版本检查、数据库约束与服务端重算共同保护关键写入。
- 记录表原图、草稿、修订、publication、PDF 和审计均不可原地改写。
- 生产使用独立 gateway/data/blue/green Compose project，应用回切与配对数据恢复分离。
- 正式镜像和基础镜像均以不可变 digest 发布；数据库、媒体和归档属于同一恢复点。
- 本地合成数据、浏览器夹具和故障注入与生产配置及数据完全隔离。

## v1.0.0 首发步骤

1. 受保护的 `main` 完成 required checks、非作者审批和会话解决。
2. `v*` tag ruleset、`production` Environment、CODEOWNERS 和最小 break-glass 实际启用。
3. 在空服务器按 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) 建立
   `/opt/pkuba/production`，空数据库完成迁移并一次性创建首位超级管理员。
4. 在隔离环境完成真实蓝绿发布、应用回切、掉电恢复、默认 2 小时保留和成对恢复演练；实际保留期限以权威 retained state 的 deadline 为准。
5. 配置正式 HTTPS、微信合法域名和真实微信身份，完成开发者工具及真机逐页验收。
6. 由授权发布负责人批准候选后创建 `v1.0.0` 标签并通过 GitHub Actions 发布。

## 仍需外部或人工完成

- GitHub 分支保护、tag ruleset、Environment 审批与 required checks 的平台实配。
- 生产服务器、DNS、证书、GHCR 读取权限、SSH forced command 和异地备份实配。
- 真实微信登录、领队认领、管理员注册，以及小程序逐页面/控件和真机验证。
- 正式生产首超管、首次空库赛季配置、真实蓝绿切流和恢复演练。
- 备案链接、生产邮件和应急网络路径的最终核验。

## 非目标

- 不恢复旧小程序、旧赛程导入协议、demo seed 或特定历史赛季导入器。
- 不为小规模团队建立第二套业务权威、幂等系统或通用工作流引擎。
- 不以构建成功、测试数据或本地 readiness 代替微信、GitHub 或生产验收。
