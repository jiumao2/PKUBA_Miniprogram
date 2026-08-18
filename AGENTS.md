# PKUBA 开发约束

- 开工前读 `Plan.md` 和规格文档 A、6.6、9、11 章；旧项目只读，禁止迁移秘密或身份数据。
- Django/PostgreSQL 是唯一业务权威；写操作使用稳定 ID、版本号、事务、幂等保护和审计。
- `leader_adjustable`、活动申请锁和目标预留必须分离；非终态不得释放锁。
- 标准容量统计正式比赛与有效预留且场地唯一；超级管理员例外不得静默影响无关申请。
- `ScoresheetReader` 尚未完成：未经用户明确恢复待办，不得复制或迁移其代码，不实现结构化记录表功能。
- V1 暂不实现裁判、公众历史、OCR、实时计分、微信订阅消息、Redis/Celery 或微服务。
- 业务规则改变时同步更新 `Plan.md`、规格、模型和测试。
- 常用命令：`./scripts/dev.ps1`、`./scripts/seed.ps1`、`./scripts/create-admin.ps1 -Username <name>`、`./scripts/check.ps1`。
