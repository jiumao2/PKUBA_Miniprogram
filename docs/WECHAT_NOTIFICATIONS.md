# 微信服务通知与任务箱

## 事件和接收人

报名提交、审核通过或退回、名单变更、领队交接，以及执裁资格、时段报名、选派、改派、撤销、
比赛日期时段、场地、参赛方、状态、比分、正式赛果和记录表发布都会产生相应提醒。
球队消息发给当前领队，执裁消息发给相关人员，待处理事务通知有效管理员；同一账号多重身份合并。
同一事务对同一比赛的多次变更合为一条，撤销选派仍包含原执裁人员。既有调赛及记录表任务也可
使用微信通道。未批准调赛目标场地不进入通知。

数据库触发器覆盖普通服务、批量更新和比赛改期引发的自动选派撤销。通知事件、任务箱记录和
微信发送意图与业务写入处于同一 PostgreSQL 事务；业务回滚时通知一起回滚。记录表识别未正式
发布、草稿自动保存、单纯版本或锁变化不会冒充新的赛果发布。没有额外赛前定时提醒。

任务箱保留全部通知，即使用户拒收、订阅额度耗尽、微信未配置或发送失败。业务任务仍在对应
流程结束时关闭；纯变动通知以 `NOTICE_` 区分，用户查看后转入已完成。红点仍表示 OPEN 项目数。
通知内容只给出业务变化摘要，不包含学号、联系资料、其他球队报名表或私有记录表正文。
点击进入小程序后重新判断当前权限；普通报名通知发送前复核当前领队，交接通知可发送给双方。

## 模板和授权

运营人员须在微信公众平台“订阅消息 → 我的模板”确认符合服务类目的模板、ID、类型和关键词。
代码不虚构模板，也不承诺所有类目可用长期订阅。普通订阅方案由用户主动点击按钮调用
`requestSubscribeMessage`，每次只请求一个已配置模板，避免混合类型。选择不影响报名功能。
客户端上报只记录选择提示，不能伪造微信可用额度；发送结果以微信平台为准。

官方合同：
[普通订阅说明](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/subscribe-message.html)、
[用户订阅接口](https://developers.weixin.qq.com/miniprogram/dev/api/open-api/subscribe-message/wx.requestSubscribeMessage.html)、
[发送接口](https://developers.weixin.qq.com/miniprogram/dev/server/API/mp-message-management/subscribe-message/api_sendmessage.html)、
[稳定访问令牌](https://developers.weixin.qq.com/miniprogram/dev/server/API/mp-access-token/api_getstableaccesstoken.html)。
新版业务卡片订阅有独立协议，不能直接混用本实现。

## 服务端配置

配置由部署人员通过私有环境变量提供，密钥不能提交到仓库或粘贴到聊天。现有微信身份配置复用
`WECHAT_APP_ID` 和 `WECHAT_APP_SECRET`；新增：

- `WECHAT_SUBSCRIBE_ENABLED`：默认关闭；正式启用前完成模板、授权和体验版验证。
- `WECHAT_SUBSCRIBE_STATE`：`developer`、`trial` 或 `formal`，分别指开发、体验或正式版。
- `WECHAT_SUBSCRIBE_TEMPLATES`：JSON 对象，键为 `registration`、`handover`、`team`、`game`、`referee`、`task`；
  可配置语义适用于全部相关事件的 `business` 备用模板。交接优先独立模板，再使用报名模板。

每个配置包括 `template_id`、`type`（`ONCE` 或 `LONG`）、`name` 和 `fields`。
`fields` 的键必须是后台真实关键词，支持下表中的数据来源。配置检查支持 `thing数字`、
`time数字`、`date数字`、`phrase数字`；其他字段类型须先实现对应取值，不能直接借用文字字段。

| 数据来源 | 含义 | 可用关键词类型 |
| --- | --- | --- |
| `title` | 本次通知标题 | thing |
| `summary` | 提示业务已更新，进入小程序查看 | thing |
| `status` | 通用“已更新”，不是审核结果或选派状态 | thing、phrase |
| `date` | 本次业务变化发生日期 | date |
| `time` | 本次业务变化发生时间 | date、time |
| `game_title` | 比赛编号和当前对阵 | thing |
| `game_start_time` | 当前比赛日期和开始时间 | date、time |
| `game_location` | 当前比赛场地 | thing |
| `game_status` | 当前比赛状态，例如“未赛”“已完成” | thing、phrase |

`game_` 开头来源仅可用于 `game`，发送时按通知的比赛 ID 与赛季读取当前权威赛程，
不是事件发生时快照；不能映射为“原比赛时间”或“原场地”。比赛不存在、赛季不符或必要数据
为空时不向微信请求，发送记录记为 `FAILED / TEMPLATE_CONTEXT_MISSING`，任务箱保留。
事物字段规范空白并按微信限制最多展示 20 个字符，完整内容进入小程序查看。通知不包含裁判组
其他成员名单或私密报名信息。`game_status` 不能映射为裁判的“选派状态”。

其中 `date` 和 `time` 不能冒充比赛开始时间；审核结果、裁判具体岗位、人员姓名等不在本轮
映射来源内。运营人员必须按真实关键词含义选择映射，当前字段不支持的模板不得启用。
下面仅演示结构，示例 ID 不能发送，实际模板需核实适用用途和字段长度：

```json
{
  "game": {
    "template_id": "REPLACE_WITH_APPROVED_TEMPLATE_ID",
    "type": "ONCE",
    "name": "比赛变动提醒",
    "fields": {"thing1": "title", "time2": "time", "thing3": "summary"}
  }
}
```

小程序“球队报名”“执裁工作台”和“任务箱”提供接收微信提醒按钮；没有配置模板时明确提示。
配置开启前应检查模板字段和小程序跳转页面均与实际发布版本一致。

部署人员可先保存纯模板 JSON，再运行只读检查：

```text
python manage.py check_wechat_notifications --config-file /private/templates.json
```

不指定文件时检查当前 `WECHAT_SUBSCRIBE_TEMPLATES`。命令不连接数据库、不访问微信、
不发送消息、不修改环境配置；输出不包含模板 ID 或密钥。失败时给出模板和关键词的序号及
原因。通过仅说明本地映射格式受支持，不证明该 ID 归属于当前小程序、模板用途匹配、后台
关键词完整、用户有授权或真实送达。启用前仍须逐项核对公众平台并做体验版验证。

## 发送进程、失败和核对

既有 `expire_reschedules --loop` 后台进程每轮同时处理微信队列，空闲时沿用该进程轮询间隔。
独立扩容可运行 `python manage.py process_wechat_notifications --loop --interval 5`，多个进程
通过行锁领取不同记录。业务保存后立即入任务箱和队列；微信实际送达由进程与平台共同决定。
发送不占用原业务事务，也不因微信失败回滚业务。

- 未配置：`NOT_CONFIGURED`；配置完成后可运行同命令加 `--retry-configured` 重新排队。
- 未授权或微信返回 43101：`NEEDS_AUTH`，继续保留任务箱；用户再次订阅后重排相应模板的记录。
- 成功：`SENT`，只代表微信接口接受，不代表已阅读。
- 明确短暂失败：最多五次，间隔按 120、240、480、900 秒递增。获取令牌失败也使用该界限。
- 永久参数错误：`FAILED`，记录错误码，人工核对模板和映射；不保存原始微信错误正文。
- 请求发出后超时、响应无法判断或进程中断：`UNKNOWN`，不得自动换键或无限重发。
  发送中记录超过两分钟按中断转为结果不明；须有平台证据后由维护者决定处理方式。
- 账号停用或相关普通报名访问权已变化：`SKIPPED`；历史任务箱记录保留。

微信服务端没有本应用的幂等键，故不能承诺网络结果不明时的严格一次送达。访问令牌通过稳定
令牌接口取得，不写日志；OpenID 仅在私有身份表和向微信发送的请求中使用。进程遵守项目系统
写入屏障，备份或恢复期间不并行写入；不新增 Redis、Celery 或真实群发测试。

正式启用需要私有配置、批准模板、用户主动订阅、可用后台进程和微信体验版/真机证据；代码
测试使用伪造平台响应，不以此声称真实服务通知已经送达。
