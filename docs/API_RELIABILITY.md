# API 写入幂等与列表分页

## 写入幂等

下列高风险命令接受可选请求头 `Idempotency-Key`：

- `POST /api/v1/reschedule-requests/`
- `POST /api/v1/scoresheets/{scoresheet_id}/publish`
- `POST /api/v1/admin/schedule-imports/{batch_id}/confirm`
- `PUT /api/v1/admin/seasons/{season_id}/draw-assignments`
- `POST /api/v1/admin/seasons/{season_id}/lifecycle/apply`
- `POST /api/v1/game-media/games/{game_id}`
- `POST /api/v1/admin/game-media/games/{game_id}`

键必须为 1–200 个非空字符。同一账号、业务命令和键在 24 小时内：

- 请求内容相同时返回第一次成功提交的相同 HTTP 状态和业务结果，不重复执行写入。
- 请求内容不同时返回 `409 IDEMPOTENCY_KEY_REUSED`。
- 业务本身失败时不保存结果，调用方修正请求后可重试。

服务端只保存键和规范化请求的 SHA-256 摘要，不保存原始键。第一次业务写入、响应快照和幂等记录位于同一 PostgreSQL 事务，并使用事务级 advisory lock 串行处理并发重试。涉及临时签名 URL 的接口只保存稳定资源 ID，重放时重新生成当前可用的签名地址。

TypeScript 客户端默认为每次新命令生成键，也允许调用方显式传入既有键。网络超时后必须用同一个键重试；用户主动发起另一项业务时应生成新键。

## 列表分页

以下列表统一返回：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 100
}
```

已分页接口：

- `GET /api/v1/public/games`
- `GET /api/v1/reschedule-requests/`
- `GET /api/v1/scoresheets/`
- `GET /api/v1/admin/game-media/`

`page` 最小为 1，`page_size` 限制在 1–100。现有页面为保持交互不变，会由共享客户端按 100 条逐页读取并设置 1000 页安全上限；新增大数据页面应优先直接展示服务端分页，不应重新实现整表接口。

小程序“对阵”的赛程赛果使用专用比赛日游标接口 `GET /api/v1/public/schedule-days`，不自动收集上述通用比赛分页：

- `direction=initial` 按北京时间确定焦点，最多返回焦点日前后 5 个有比赛日期。
- `direction=before|after` 使用首/末已加载比赛日作为日期游标，每次最多追加 5 个比赛日。
- `direction=range` 只重新核对客户端已加载日期范围，范围最多 180 天，不主动扩展窗口。
- 日期游标页以比赛日为单位，因此不会截断同一天，也不会出现页码。

## 变更要求

- 新增会创建不可重复业务结果的命令时，必须评估是否纳入幂等保护。
- 幂等命令不得绕过既有领域服务、版本检查、权限检查、事务或审计。
- 新增可能随赛季增长的集合接口时，必须使用同一分页结构并覆盖过滤条件、页边界和空页测试。
- OpenAPI 变更后运行 `npm run generate:api`，并由全仓检查确认生成客户端没有过期。
